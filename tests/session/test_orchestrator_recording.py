"""orchestrator 插桩测试：R0–R6 记录点、轮次 flush、共享列表。"""

import json

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.container import DIContainer
from harness.core.session.store import SessionStore
from harness.interfaces.types import (
    Response, ToolCall, ToolCallFunction, ToolDefinition, ToolResult, UserRequest,
)
from tests.session._fakes import run_async


def _read_events(store, pid):
    path = store.agent_log_path(pid)
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines()]


class ScriptedAdapter:
    """异步 InputAdapter 替身：按脚本返回输入，收集发送事件。"""

    def __init__(self, inputs):
        self._inputs = list(inputs)
        self.sent = []

    async def receive(self) -> UserRequest:
        return self._inputs.pop(0) if self._inputs else UserRequest(
            text="", metadata={"exit": True})

    async def send(self, event, target=None):
        self.sent.append(event)


class EchoProvider:
    """最小 SystemToolProvider：一个 echo 工具。"""

    def get_tools(self):
        return [ToolDefinition(name="echo", description="echo",
                               parameters={"type": "object", "properties": {}})]

    def execute(self, name, args):
        return ToolResult(success=True, content=f"echo:{json.dumps(args)}")


def _llm_scripted(responses):
    queue = list(responses)

    async def call_llm(messages, tools):
        return queue.pop(0)

    return call_llm


def _make(tmp_path, inputs, llm, with_provider=False):
    store = SessionStore(str(tmp_path))
    store.begin_session(None)
    container = DIContainer()
    if with_provider:
        from harness.interfaces.system_tool_provider import SystemToolProvider
        container.register(SystemToolProvider, EchoProvider())
    log = store.create_log("root")
    adapter = ScriptedAdapter(inputs)
    orch = AsyncLifecycleOrchestrator(
        container, adapter=adapter, call_llm=llm, session_log=log)
    return store, log, orch


class TestRecording:
    @run_async
    async def test_text_round_records_r0_r1_r4(self, tmp_path):
        """纯文本轮：header(R0) → user(R1) → assistant(R4a) → stop(R4b)。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="你好！")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)

        evts = _read_events(store, "root")
        assert [e["type"] for e in evts] == ["header", "user", "assistant", "stop"]
        assert evts[1]["message"]["content"] == "你好"
        assert evts[2]["message"]["content"] == "你好！"
        assert evts[3]["stop_reason"] == "end_turn"
        await store.close()

    @run_async
    async def test_history_is_shared_object(self, tmp_path):
        store, log, orch = _make(
            tmp_path, [UserRequest(text="hi")],
            _llm_scripted([Response(text="ok")]))
        assert orch._history is log.history           # 同一对象引用
        assert orch._tool_call_records is log.tool_call_records
        await store.close()

    @run_async
    async def test_tool_round_records_r2_r3(self, tmp_path):
        """工具轮：R2(assistant+tool_calls) → R3a(tool_call) → R3b(tool_result)。"""
        llm = _llm_scripted([
            Response(tool_uses=[ToolCall(
                id="call_1", type="function",
                function=ToolCallFunction(name="echo", arguments='{"x":1}'))]),
            Response(text="done"),
        ])
        store, log, orch = _make(
            tmp_path, [UserRequest(text="查一下")], llm, with_provider=True)
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)

        evts = _read_events(store, "root")
        types = [e["type"] for e in evts]
        assert types == ["header", "user", "assistant",
                         "tool_call", "tool_result", "assistant", "stop"]
        rec = evts[3]["record"]
        assert rec["tool_name"] == "echo" and rec["error"] is None
        assert len(log.tool_call_records) == 1       # R3a 镜像 records
        await store.close()

    @run_async
    async def test_flush_at_round_boundary_persists(self, tmp_path):
        """轮次边界 flush：_phase_loop 返回时本轮已在 page cache（文件可读）。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="ok")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        lines = store.agent_log_path("root").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4                        # 未到 finalize 已落盘
        assert log._pending == []
        await store.close()

    @run_async
    async def test_phase_end_finalize_before_clear(self, tmp_path):
        """R6：session_end 在 history 清理前写入；clear 不影响盘上事实。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="最终回复")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        traj = orch._build_trajectory()
        await orch._phase_end(traj)

        evts = [json.loads(l) for l in
                store.agent_log_path("root").read_text(encoding="utf-8").splitlines()]
        assert evts[-1]["type"] == "session_end"
        assert evts[-1]["final_output"] == "最终回复"
        assert len(orch._history) == 0                # clear 依旧发生
        await store.close()

    @run_async
    async def test_user_meta_recorded(self, tmp_path):
        """R1 meta：from/msg_id/type 从 UserRequest.metadata 提取落盘。"""
        req = UserRequest(text="在吗", metadata={
            "from": "b", "type": "talk_to", "msg_id": "M-9", "irrelevant": 1})
        store, log, orch = _make(tmp_path, [req],
                                 _llm_scripted([Response(text="在")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        user_evt = _read_events(store, "root")[1]
        assert user_evt["meta"] == {"from": "b", "type": "talk_to", "msg_id": "M-9"}
        await store.close()

    @run_async
    async def test_no_session_log_behavior_unchanged(self, tmp_path):
        """无 session_log：与现状一致（不建文件、不报错、history 自持）。"""
        container = DIContainer()
        orch = AsyncLifecycleOrchestrator(
            container, adapter=ScriptedAdapter([UserRequest(text="hi")]),
            call_llm=_llm_scripted([Response(text="ok")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        assert len(orch._history) == 2
        assert list(tmp_path.iterdir()) == []
