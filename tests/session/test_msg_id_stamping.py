"""msg_id 盖章测试：中介点各自产生可配对的发送方事实。

D8：msg_id 由内核在中介点盖章，永不信任 LLM 的 call_id；edge 事件 = 发送方事实。
"""

import asyncio
import json
import re

from harness.core.session.store import SessionStore
from harness.interfaces.async_input_adapter import AsyncInputAdapter
from harness.interfaces.types import StopEvent, UserRequest
from harness.runtime.agent_runtime import AgentRuntime
from harness.runtime.kernel import Kernel
from harness.runtime.message_bus import MessageBus
from harness.runtime.tools import TalkToTool
from tests.session._fakes import MockConsole, MockHarness, run_async

MSG_ID_RE = re.compile(r"^M-[0-9a-f]{8}$")


class _QueueAdapter:
    """AsyncInputAdapter 替身：receive 返回 exit，send 只记录。"""

    def __init__(self):
        self.sent = []

    async def receive(self):
        return UserRequest(text="", metadata={"exit": True})

    async def send(self, event, target=None):
        self.sent.append(event)


def _kernel(tmp_path):
    """创建 store + kernel（begin_session，不创建任何 agent）。"""
    store = SessionStore(str(tmp_path))
    store.begin_session(None)
    return Kernel(MockConsole(), store=store), store


class TestTalkToStamping:
    def test_talk_to_stamps_msg_id_in_metadata_and_result(self, tmp_path):
        kernel, _store = _kernel(tmp_path)
        kernel.input_queues["b"] = asyncio.Queue()
        tool = TalkToTool(kernel, from_pid="root")
        result = tool.execute({"pid": "b", "text": "在吗"})
        payload = json.loads(result.content)
        assert MSG_ID_RE.match(payload["msg_id"])
        req = kernel.input_queues["b"].get_nowait()
        assert req.metadata["msg_id"] == payload["msg_id"]
        assert req.metadata["from"] == "root" and req.metadata["type"] == "talk_to"


class _FakeEvent:
    def __init__(self, text):
        self.text = text


class TestPublishStamping:
    @run_async
    async def test_publish_stamps_per_subscriber_and_returns_edges(self):
        bus = MessageBus(input_queues={"a": asyncio.Queue(), "b": asyncio.Queue()})
        bus.subscribe("a", "root")
        bus.subscribe("b", "root")
        edges = await bus.publish("root", _FakeEvent(text="进度更新"))
        assert len(edges) == 2
        assert edges[0].msg_id != edges[1].msg_id      # 每订阅者独立 msg_id
        assert all(MSG_ID_RE.match(e.msg_id) for e in edges)
        assert {e.to_pid for e in edges} == {"a", "b"}

    @run_async
    async def test_stop_event_unstamped(self):
        bus = MessageBus(input_queues={"a": asyncio.Queue()})
        bus.subscribe("a", "root")
        edges = await bus.publish("root", StopEvent(stop_reason="end_turn"))
        assert edges == []                              # 控制事件不盖章


class TestDirectStamping:
    def test_direct_stamps_msg_id_when_absent(self):
        from harness.runtime.types import InternalMessage
        bus = MessageBus(input_queues={"t": asyncio.Queue()})
        msg = InternalMessage(from_pid="root", content="hi")
        edge = bus.direct("t", msg)
        assert edge is not None and edge.kind == "direct"
        assert MSG_ID_RE.match(edge.msg_id)
        received = bus._input_queues["t"].get_nowait()
        assert received.metadata["msg_id"] == edge.msg_id

    def test_direct_returns_none_when_already_stamped(self):
        from harness.runtime.types import InternalMessage
        bus = MessageBus(input_queues={"t": asyncio.Queue()})
        msg = InternalMessage(from_pid="root", content="hi",
                              metadata={"msg_id": "M-12345678"})
        assert bus.direct("t", msg) is None


class TestChildFinishedAndSpawnEntry:
    @run_async
    async def test_child_finished_metadata_has_msg_id(self, tmp_path):
        kernel, store = _kernel(tmp_path)
        root = kernel._create_root(MockHarness())
        child = AgentRuntime(pid="child-1", mode="oneshot",
                             harness=MockHarness(), kernel=kernel, parent=root)
        child.last_output = "做完了"
        child.workflow_flag = "wf_001"
        kernel.runtime_table["child-1"] = child
        kernel.input_queues["child-1"] = asyncio.Queue()
        await kernel._on_agent_finished(child)
        req = kernel.input_queues["root"].get_nowait()
        assert req.metadata["type"] == "child_finished"
        assert MSG_ID_RE.match(req.metadata["msg_id"])
        assert req.metadata["from"] == "child-1"

    @run_async
    async def test_spawn_entry_stamps_deterministic_msg_id(self, tmp_path):
        from tests.runtime.test_e2e_workflow import _write_workflow_script
        script = _write_workflow_script([{"name": "w1", "entry_prompt": "去干活"}])
        try:
            kernel, _store = _kernel(tmp_path)
            result = kernel.spawn_from_script(script, parent=None,
                                              autostart=False)
            assert result["agents"][0]["pid"] == "w1"
            req = kernel.input_queues["w1"].get_nowait()
            assert req.metadata["msg_id"] == "spawn_entry:w1"
            assert req.metadata["type"] == "spawn_entry"
            assert req.text == "去干活"
        finally:
            import os
            os.unlink(script)
