# tests/runtime/test_on_agent_finished.py

"""Tests for Kernel._on_agent_finished full implementation.

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import time
import pytest
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.types import (
    AgentFinished, __EXIT_SENTINEL__,
)
from harness.interfaces.types import UserRequest


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole with spy."""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


class _MockHarness:
    """Minimal harness stub."""
    def __init__(self):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = None


def _make_runtime(kernel, pid, mode="continuous", parent=None):
    """Create a minimal AgentRuntime for testing."""
    harness = _MockHarness()
    rt = AgentRuntime(
        pid=pid, mode=mode, harness=harness,
        kernel=kernel, parent=parent,
    )
    rt.last_output = f"output from {pid}"
    rt.error = None
    rt.started_at = time.time() - 5.0
    rt.workflow_flag = "wf_test"
    return rt


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    from functools import wraps
    @wraps(coro_func)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


@pytest.fixture
def kernel():
    """Fixture: Kernel with mock console and empty agent tables."""
    console = _MockConsole()
    k = Kernel(console)
    return k


# ── Tests ──


@async_test
async def test_on_agent_finished_pushes_agent_finished_event(kernel):
    runtime = _make_runtime(kernel, "worker")
    kernel.runtime_table["worker"] = runtime
    kernel.input_queues["worker"] = asyncio.Queue()

    await kernel._on_agent_finished(runtime)

    finished_events = [
        e for e in kernel._console.events
        if isinstance(e, AgentFinished)
    ]
    assert len(finished_events) == 1
    assert finished_events[0].pid == "worker"
    assert finished_events[0].result == "output from worker"


@async_test
async def test_child_finished_sent_to_parent(kernel):
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    await kernel._on_agent_finished(child)

    # Parent should receive child_finished UserRequest
    msg = kernel.input_queues["parent"].get_nowait()
    assert isinstance(msg, UserRequest)
    assert msg.metadata["type"] == "child_finished"
    assert msg.metadata["pid"] == "child"
    assert msg.metadata["workflow_flag"] == "wf_test"


@async_test
async def test_child_finished_skipped_when_parent_subscribes_child(kernel):
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    # Parent explicitly subscribes to child
    kernel.message_bus.subscribe("parent", "child")

    await kernel._on_agent_finished(child)

    # Parent should NOT receive child_finished (dedup)
    assert kernel.input_queues["parent"].empty()


@async_test
async def test_child_finished_skipped_when_parent_finished(kernel):
    parent = _make_runtime(kernel, "parent")
    parent.state = AgentState.FINISHED
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    await kernel._on_agent_finished(child)

    # Parent is already FINISHED → no child_finished
    assert kernel.input_queues["parent"].empty()


@async_test
async def test_cascade_sends_sentinel_to_subscribers(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber should receive __EXIT_SENTINEL__
    item = kernel.input_queues["subscriber"].get_nowait()
    assert item is __EXIT_SENTINEL__
    assert sub.should_exit is True


@async_test
async def test_parent_excluded_from_cascade(kernel):
    """Top-level design Section 四.5: parent不受级联影响."""
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    # Parent also explicitly subscribes to child
    kernel.message_bus.subscribe("parent", "child")

    await kernel._on_agent_finished(child)

    # Parent should NOT receive sentinel (excluded from cascade)
    # AND should NOT receive child_finished (dedup — already subscribed)
    assert kernel.input_queues["parent"].empty()
    assert parent.should_exit is False  # Parent unaffected


@async_test
async def test_cascade_skips_terminating_subscriber(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    sub.state = AgentState.TERMINATING
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber already TERMINATING → no sentinel sent
    assert kernel.input_queues["subscriber"].empty()


@async_test
async def test_cascade_skips_finished_subscriber(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    sub.state = AgentState.FINISHED
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber already FINISHED → no sentinel sent
    assert kernel.input_queues["subscriber"].empty()


@async_test
async def test_remove_publisher_called(kernel):
    pub = _make_runtime(kernel, "publisher")
    kernel.runtime_table["publisher"] = pub
    kernel.input_queues["publisher"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Publisher's subscriptions should be cleaned up
    assert kernel.message_bus.get_subscribers_of("publisher") == []
