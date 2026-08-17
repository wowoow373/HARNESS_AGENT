"""Kernel/Runtime 持久化接线测试。"""

import asyncio
import json

from harness.core.session.config import SessionConfig
from harness.core.session.store import SessionStore
from harness.interfaces.types import (
    Response, ToolCall, ToolCallFunction, UserRequest,
)
from harness.runtime.kernel import Kernel
from harness.runtime.runtime import Runtime
from harness.runtime.types import CommandExit, CommandTalk
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


class TalkThenExitConsole(MockConsole):
    """先 CommandTalk 触发一轮对话，再 CommandExit（驱动系统输入循环退出）。"""

    def __init__(self):
        super().__init__()
        self._commands = [CommandTalk(pid="root", text="go"), CommandExit()]

    async def receive(self):
        await asyncio.sleep(0.05)
        return self._commands.pop(0)


class DyingConsole(MockConsole):
    """receive 直接抛错：task_sys 异常死亡——永远走不到 CommandExit 清扫。"""

    async def receive(self):
        await asyncio.sleep(0.05)
        raise RuntimeError("console died")


class ScriptedRootAdapter:
    """按脚本返回输入的 root adapter（不经 input_queue）。"""

    def __init__(self, inputs):
        self._inputs = list(inputs)

    async def receive(self) -> UserRequest:
        return self._inputs.pop(0)

    async def send(self, event, target=None):
        pass


def _write_continuous_child_script(tmp_path):
    """连续模式子 agent 脚本（subscribe user → continuous → 阻塞在 receive）。"""
    script = tmp_path / "wf_child.py"
    script.write_text(
        "from harness.runtime.decorators import agent, subscribe\n"
        "from tests.session._fakes import MockHarness\n"
        "subscribe('child').to('user')\n"
        "@agent(name='child', entry_prompt='hi')\n"
        "def make():\n"
        "    return MockHarness()\n",
        encoding="utf-8")
    return script


class TestPostSweepSpawnWindow:
    """post-sweep spawn 窗口回归：/exit 后在途 LLM 响应仍执行 spawn。"""

    def test_post_shutdown_spawn_rejected_and_run_returns(self, tmp_path):
        """_shutdown 翻转后工具循环里的 spawn_workflow 被拒（受控工具错误），
        run() 正常返回、root 收尾完整、无孤儿 agent。"""
        script = _write_continuous_child_script(tmp_path)
        cfg = SessionConfig(root=str(tmp_path / "sessions"))
        rt = Runtime(TalkThenExitConsole(), session_config=cfg)
        state = {"called": False}

        async def fake_llm(msgs, tools):
            if not state["called"]:
                state["called"] = True
                # 模拟在途响应：严格等 _shutdown 翻转后才返回 tool_use
                while not rt._kernel._shutdown:
                    await asyncio.sleep(0.01)
                return Response(tool_uses=[ToolCall(
                    id="call_1", type="function",
                    function=ToolCallFunction(
                        name="spawn_workflow",
                        arguments=json.dumps(
                            {"script_path": str(script)})))])
            return Response(text="done")

        harness = MockHarness(call_llm=fake_llm)   # 无 adapter → KBA
        rt.run(harness)                            # 修复前：永不返回（挂起）

        conv_dirs = [d for d in (tmp_path / "sessions").iterdir() if d.is_dir()]
        assert len(conv_dirs) == 1
        evts = [json.loads(l) for l in
                (conv_dirs[0] / "agents" / "root.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        assert evts[-1]["type"] == "session_end"
        # spawn 被拒 → 受控工具错误落盘，非崩溃
        assert any(
            e["type"] == "tool_call"
            and "shutting down" in (e["record"].get("error") or "")
            for e in evts)
        index = json.loads(
            (conv_dirs[0] / "index.json").read_text(encoding="utf-8"))
        assert list(index["agents"]) == ["root"]  # 无孤儿 child

    def test_unswept_child_reswept_in_finally(self, tmp_path):
        """task_sys 异常死亡（无 CommandExit 清扫）时 spawn 出的连续子 agent
        收不到 sentinel——finally 再清扫兜底：run() 返回且子 agent 有 session_end。"""
        from harness.interfaces.async_input_adapter import AsyncInputAdapter

        script = _write_continuous_child_script(tmp_path)
        cfg = SessionConfig(root=str(tmp_path / "sessions"))
        rt = Runtime(DyingConsole(), session_config=cfg)
        state = {"spawned": False}

        async def fake_llm(msgs, tools):
            if not state["spawned"]:
                state["spawned"] = True
                rt._kernel.spawn_from_script(str(script), parent=None)
            return Response(text="done")

        harness = MockHarness(call_llm=fake_llm)
        harness.container.register(
            AsyncInputAdapter,
            ScriptedRootAdapter([UserRequest(text="go"),
                                 UserRequest(text="", metadata={"exit": True})]))
        rt.run(harness)                            # 修复前：永不返回（挂起）

        conv_dirs = [d for d in (tmp_path / "sessions").iterdir() if d.is_dir()]
        assert len(conv_dirs) == 1
        child_lines = (conv_dirs[0] / "agents" / "child.jsonl").read_text(
            encoding="utf-8").splitlines()
        types = [json.loads(l)["type"] for l in child_lines]
        assert types[0] == "header"
        assert types[-1] == "session_end"         # 再清扫 → 正常收尾
        index = json.loads(
            (conv_dirs[0] / "index.json").read_text(encoding="utf-8"))
        assert index["agents"]["child"]["status"] == "paused"
