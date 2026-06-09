# tests/runtime/test_kba_send_upgrade.py

"""Tests for KBA.send() MessageBus routing (Batch 3 upgrade).

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import pytest
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.interfaces.types import (
    TextEvent, StopEvent, ThinkingEvent, ToolCallEvent, ToolResultEvent,
)
from harness.runtime.types import InternalMessage, AgentOutput


# ── Stubs ──

class _MockRuntime:
    should_exit = False


class _MockConsole:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


class _MockMessageBus:
    """MessageBus spy — records method calls."""
    def __init__(self):
        self.publish_calls = []
        self.direct_calls = []

    async def publish(self, from_pid, event, on_no_subscriber=None):
        self.publish_calls.append({
            "from_pid": from_pid,
            "event": event,
            "on_no_subscriber": on_no_subscriber,
        })

    def direct(self, target_pid, message):
        self.direct_calls.append({
            "target_pid": target_pid,
            "message": message,
        })


class _MockKernel:
    def __init__(self):
        self._console = _MockConsole()
        self.message_bus = _MockMessageBus()


def _make_kba():
    kernel = _MockKernel()
    runtime = _MockRuntime()
    return KernelBridgeAdapter(pid="test", kernel=kernel, runtime=runtime), kernel


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# ── Tests ──


@async_test
async def test_text_event_target_none_routes_to_publish():
    kba, kernel = _make_kba()
    await kba.send(TextEvent(content="hello"), target=None)

    assert len(kernel.message_bus.publish_calls) == 1
    call = kernel.message_bus.publish_calls[0]
    assert call["from_pid"] == "test"
    assert isinstance(call["event"], TextEvent)
    assert call["event"].content == "hello"
    assert call["on_no_subscriber"] is not None


@async_test
async def test_stop_event_target_none_routes_to_publish():
    kba, kernel = _make_kba()
    await kba.send(StopEvent(stop_reason="end"), target=None)

    assert len(kernel.message_bus.publish_calls) == 1
    call = kernel.message_bus.publish_calls[0]
    assert call["from_pid"] == "test"
    assert isinstance(call["event"], StopEvent)
    assert call["on_no_subscriber"] is None


@async_test
async def test_target_pid_routes_to_direct():
    kba, kernel = _make_kba()
    await kba.send(TextEvent(content="ping"), target="other")

    assert len(kernel.message_bus.direct_calls) == 1
    call = kernel.message_bus.direct_calls[0]
    assert call["target_pid"] == "other"
    assert isinstance(call["message"], InternalMessage)
    assert call["message"].from_pid == "test"
    assert call["message"].content == "ping"
    assert call["message"].metadata == {}


@async_test
async def test_stop_event_target_pid_has_stop_metadata():
    kba, kernel = _make_kba()
    await kba.send(StopEvent(stop_reason="end"), target="other")

    assert len(kernel.message_bus.direct_calls) == 1
    msg = kernel.message_bus.direct_calls[0]["message"]
    assert msg.content == ""
    assert msg.metadata == {"stop": True}


@async_test
async def test_should_exit_drops_all():
    kba, kernel = _make_kba()
    kba._runtime.should_exit = True

    await kba.send(TextEvent(content="should not appear"))
    await kba.send(StopEvent(stop_reason="end"), target="other")

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel.message_bus.direct_calls) == 0


@async_test
async def test_thinking_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ThinkingEvent(content="hmm..."), target=None)

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel.message_bus.direct_calls) == 0
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)


@async_test
async def test_tool_call_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ToolCallEvent(call_id="c1", tool_name="search"), target=None)

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)


@async_test
async def test_tool_result_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ToolResultEvent(call_id="c1", tool_name="search", result="ok"), target=None)

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)
