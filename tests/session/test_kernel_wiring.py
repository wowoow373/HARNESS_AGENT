"""Kernel/Runtime 持久化接线测试。"""

import asyncio
import json

from harness.core.session.config import SessionConfig
from harness.core.session.store import SessionStore
from harness.interfaces.types import UserRequest
from harness.runtime.kernel import Kernel
from harness.runtime.runtime import Runtime
from harness.runtime.types import CommandExit
from tests.session._fakes import MockConsole, MockHarness, run_async


class ExitImmediatelyAdapter:
    """首次 receive 即返回退出（走 _phase_init 退出路径，最小化运行）。"""

    async def receive(self) -> UserRequest:
        return UserRequest(text="/exit")

    async def send(self, event, target=None):
        pass


class ExitCommandConsole(MockConsole):
    """receive 返回 CommandExit：驱动 _handle_system_input 退出（task_sys 不悬挂）。"""

    async def receive(self):
        await asyncio.sleep(0.05)   # 让 root 先跑起来
        return CommandExit()


def _harness_with_exit_adapter():
    from harness.interfaces.async_input_adapter import AsyncInputAdapter
    harness = MockHarness()
    harness.container.register(AsyncInputAdapter, ExitImmediatelyAdapter())
    return harness


class TestKernelWiring:
    @run_async
    async def test_spawn_root_creates_session_log(self, tmp_path):
        store = SessionStore(str(tmp_path))
        store.begin_session(None)
        kernel = Kernel(MockConsole(), store=store)
        kernel.spawn_root(_harness_with_exit_adapter())
        await kernel._tasks["root"]
        await store.close()

        path = store.agent_log_path("root")
        types = [json.loads(l)["type"] for l in
                 path.read_text(encoding="utf-8").splitlines()]
        assert types == ["header", "session_end"]   # 立即退出：仅首尾
        index = store.read_index(store.conv_id)
        assert index["agents"]["root"]["status"] == "paused"
        assert index["agents"]["root"]["last_seq"] == 1

    @run_async
    async def test_kernel_without_store_still_works(self, tmp_path):
        """无 store（向后兼容路径）：spawn/退出正常，零落盘。"""
        kernel = Kernel(MockConsole())
        kernel.spawn_root(_harness_with_exit_adapter())
        await kernel._tasks["root"]
        assert kernel.runtime_table["root"].state.name == "FINISHED"
        assert list(tmp_path.iterdir()) == []

    @run_async
    async def test_spawn_from_script_agents_get_logs(self, tmp_path):
        """workflow 脚本 agent 同样接线（写最小 fixture 脚本）。"""
        script = tmp_path / "wf.py"
        script.write_text(
            "from harness.runtime.decorators import agent\n"
            "from tests.session._fakes import MockHarness\n"
            "from harness.interfaces.async_input_adapter import AsyncInputAdapter\n"
            "from tests.session.test_kernel_wiring import ExitImmediatelyAdapter\n"
            "@agent(name='worker', entry_prompt='go')\n"
            "def make():\n"
            "    h = MockHarness()\n"
            "    h.container.register(AsyncInputAdapter, ExitImmediatelyAdapter())\n"
            "    return h\n",
            encoding="utf-8")
        store = SessionStore(str(tmp_path / "sessions"))
        store.begin_session(None)
        kernel = Kernel(MockConsole(), store=store)
        kernel.spawn_from_script(str(script), parent=None)
        await kernel._tasks["worker"]
        await store.close()
        assert store.agent_log_path("worker").exists()


class TestRuntimeWiring:
    """真实入口 Runtime.run 的端到端接线（每个真实 CLI run 走的路径）。"""

    def test_run_persists_session_and_writes_terminal_state(self, tmp_path):
        """run() 返回后：conv 目录落盘、root.jsonl 首尾完整、index 终态写生效。"""
        cfg = SessionConfig(root=str(tmp_path / "sessions"))
        Runtime(ExitCommandConsole(), session_config=cfg).run(
            _harness_with_exit_adapter())

        conv_dirs = [d for d in (tmp_path / "sessions").iterdir() if d.is_dir()]
        assert len(conv_dirs) == 1
        lines = (conv_dirs[0] / "agents" / "root.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert [json.loads(l)["type"] for l in lines] == ["header", "session_end"]
        index = json.loads(
            (conv_dirs[0] / "index.json").read_text(encoding="utf-8"))
        assert index["status"] == "paused"      # _close_store 终态写
        assert index["owner"] is None
        assert index["agents"]["root"]["status"] == "paused"
