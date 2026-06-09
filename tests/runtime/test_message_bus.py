# tests/runtime/test_message_bus.py

"""Tests for MessageBus pub-sub routing.

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import pytest
from harness.runtime.message_bus import MessageBus
from harness.interfaces.types import TextEvent, StopEvent


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole — records send calls with spy."""
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


def _make_bus(console=None):
    """Create a MessageBus with fresh input_queues."""
    return MessageBus(input_queues={}, console=console)


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# ── subscribe ──


def test_subscribe_establishes_mapping():
    bus = _make_bus()
    bus.subscribe("analyzer", "collector")
    assert "analyzer" in bus._subscriptions["collector"]


def test_subscribe_idempotent():
    bus = _make_bus()
    bus.subscribe("analyzer", "collector")
    bus.subscribe("analyzer", "collector")
    assert bus._subscriptions["collector"] == {"analyzer"}


def test_subscribe_self_rejected():
    bus = _make_bus()
    with pytest.raises(ValueError, match="Self-subscription"):
        bus.subscribe("A", "A")


def test_subscribe_multiple_subscribers():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    assert bus._subscriptions["pub"] == {"A", "B"}


# ── publish ──


@async_test
async def test_publish_routes_to_active_subscriber():
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["analyzer"] = q
    bus.subscribe("analyzer", "collector")

    await bus.publish("collector", TextEvent(content="hello"))

    msg = q.get_nowait()
    assert msg.from_pid == "collector"
    assert msg.content == "hello"
    assert msg.metadata == {}


@async_test
async def test_publish_skips_finished_subscriber():
    """Subscriber whose queue was removed (agent FINISHED) is skipped."""
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["analyzer"] = q
    bus.subscribe("analyzer", "collector")

    # Remove queue to simulate agent FINISHED
    del bus._input_queues["analyzer"]

    # Should not crash
    await bus.publish("collector", TextEvent(content="hello"))


@async_test
async def test_publish_no_subscribers_text_event_calls_on_no_subscriber():
    bus = _make_bus()
    called_with = []

    async def _cb(event):
        called_with.append(event)

    await bus.publish("nobody", TextEvent(content="hi"),
                      on_no_subscriber=_cb)

    assert len(called_with) == 1
    assert called_with[0].pid == "nobody"
    assert called_with[0].content == "hi"


@async_test
async def test_publish_no_subscribers_text_event_fallback_console():
    console = _MockConsole()
    bus = _make_bus(console=console)

    await bus.publish("nobody", TextEvent(content="hi"))

    assert len(console.events) == 1
    assert console.events[0].pid == "nobody"
    assert console.events[0].content == "hi"


@async_test
async def test_publish_no_subscribers_text_event_silent_drop():
    """When no on_no_subscriber AND no console, silently drop."""
    bus = _make_bus()  # no console
    await bus.publish("nobody", TextEvent(content="hi"))


@async_test
async def test_publish_no_subscribers_stop_event_ignored():
    """StopEvent with no subscribers is silently dropped."""
    called = []
    async def _cb(event):
        called.append(event)

    bus = _make_bus()
    await bus.publish("nobody", StopEvent(stop_reason="end"),
                      on_no_subscriber=_cb)
    assert len(called) == 0


@async_test
async def test_publish_no_subscribers_stop_event_ignored_with_console():
    console = _MockConsole()
    bus = _make_bus(console=console)
    await bus.publish("nobody", StopEvent(stop_reason="end"))
    assert len(console.events) == 0


@async_test
async def test_publish_on_no_subscriber_priority_over_console():
    """TextEvent with console set → console gets the event, on_no_subscriber unused."""
    console = _MockConsole()
    bus = _make_bus(console=console)
    called_with = []

    async def _cb(event):
        called_with.append(event)

    await bus.publish("nobody", TextEvent(content="hi"),
                      on_no_subscriber=_cb)

    # console 始终接收 TextEvent，on_no_subscriber 仅当 console 为 None 时兜底
    assert len(console.events) == 1
    assert len(called_with) == 0


# ── direct ──


def test_direct_delivers_to_target():
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["target"] = q

    from harness.runtime.types import InternalMessage
    msg = InternalMessage(from_pid="sender", content="ping")
    bus.direct("target", msg)

    received = q.get_nowait()
    assert received.from_pid == "sender"
    assert received.content == "ping"


def test_direct_missing_target_raises_keyerror():
    bus = _make_bus()
    from harness.runtime.types import InternalMessage
    msg = InternalMessage(from_pid="sender", content="ping")

    with pytest.raises(KeyError, match="target_pid"):
        bus.direct("nonexistent", msg)


# ── get_subscribers_of ──


def test_get_subscribers_of_returns_list():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    result = bus.get_subscribers_of("pub")
    assert set(result) == {"A", "B"}


def test_get_subscribers_of_empty_for_unknown():
    bus = _make_bus()
    assert bus.get_subscribers_of("nobody") == []


# ── unsubscribe ──


def test_unsubscribe_removes_single_relationship():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    bus.unsubscribe("A", "pub")
    assert bus._subscriptions["pub"] == {"B"}


def test_unsubscribe_idempotent():
    bus = _make_bus()
    bus.unsubscribe("A", "pub")  # no crash
    bus.subscribe("A", "pub")
    bus.unsubscribe("A", "pub")
    bus.unsubscribe("A", "pub")  # second call, no crash


def test_unsubscribe_cleans_empty_set():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.unsubscribe("A", "pub")
    assert "pub" not in bus._subscriptions


# ── remove_publisher ──


def test_remove_publisher_clears_all():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    bus.remove_publisher("pub")
    assert "pub" not in bus._subscriptions


def test_remove_publisher_idempotent():
    bus = _make_bus()
    bus.remove_publisher("nobody")  # no crash
