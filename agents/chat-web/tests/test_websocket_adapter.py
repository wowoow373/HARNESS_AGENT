"""Unit tests for WebSocketAdapter.

Coverage:
    - receive() returns UserRequest with correct text
    - receive() populates session_id
    - receive() handles None sentinel (exit signal)
    - send() serializes all 5 event types to JSON-ready dicts
    - _serialize_event() covers all AdapterEvent variants
    - get_event() blocking and non-blocking modes
    - get_event() empty queue behaviour
    - close() signals exit via None sentinel
    - Multi-event drain from outbox
    - session_id auto-generation and custom value
    - InputAdapter protocol conformance
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path
from typing import Dict, Any

import pytest

# Ensure project root and chat-web dir are on path
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CHAT_WEB = Path(__file__).resolve().parents[1]
if str(_CHAT_WEB) not in sys.path:
    sys.path.insert(0, str(_CHAT_WEB))

from adapter.websocket_adapter import WebSocketAdapter
from harness.interfaces.input_adapter import InputAdapter
from harness.interfaces.types import (
    AdapterEvent,
    StopEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_event(content: str = "hello") -> TextEvent:
    return TextEvent(content=content)


def _make_thinking_event(content: str = "reasoning...") -> ThinkingEvent:
    return ThinkingEvent(content=content)


def _make_tool_call_event(
    tool_name: str = "web_search",
    arguments: dict | None = None,
    call_id: str = "call_1",
) -> ToolCallEvent:
    return ToolCallEvent(
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments or {"query": "test"},
    )


def _make_tool_result_event(
    tool_name: str = "web_search",
    result: object = "result",
    call_id: str = "call_1",
    success: bool = True,
    error: str | None = None,
    duration_ms: float = 100.0,
) -> ToolResultEvent:
    return ToolResultEvent(
        call_id=call_id,
        tool_name=tool_name,
        success=success,
        result=result,
        error=error,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Tests — receive()
# ---------------------------------------------------------------------------


class TestReceive:
    """Tests for WebSocketAdapter.receive()."""

    def test_receive_returns_user_request(self):
        """receive() should return a UserRequest with the message text."""
        adapter = WebSocketAdapter()
        adapter.put_message({"text": "hello world"})

        request = adapter.receive()

        assert isinstance(request, UserRequest)
        assert request.text == "hello world"

    def test_receive_has_session_id(self):
        """receive() should populate session_id from adapter."""
        adapter = WebSocketAdapter()
        adapter.put_message({"text": "test"})

        request = adapter.receive()

        assert request.session_id == adapter.session_id
        assert request.session_id.startswith("ws-")

    def test_receive_empty_text(self):
        """receive() should handle empty text field."""
        adapter = WebSocketAdapter()
        adapter.put_message({})

        request = adapter.receive()

        assert request.text == ""

    def test_receive_blocks_until_message(self):
        """receive() should block until a message is available."""
        adapter = WebSocketAdapter()
        # Without putting a message, receive() would block forever.
        # We test the non-blocking side via put_message + immediate receive.
        adapter.put_message({"text": "immediate"})
        request = adapter.receive()
        assert request.text == "immediate"

    def test_receive_exit_signal(self):
        """receive() should return text='' when inbox gets None sentinel."""
        adapter = WebSocketAdapter()
        adapter.close()

        request = adapter.receive()

        assert request.text == ""
        assert request.session_id == adapter.session_id


# ---------------------------------------------------------------------------
# Tests — send() + _serialize_event()
# ---------------------------------------------------------------------------


class TestSend:
    """Tests for WebSocketAdapter.send() event serialization."""

    def test_send_text_event(self):
        """TextEvent should serialize to {'type': 'text', 'content': ...}."""
        adapter = WebSocketAdapter()
        adapter.send(_make_text_event("hello"))

        event = adapter.get_event(block=False)
        assert event == {"type": "text", "content": "hello"}

    def test_send_thinking_event(self):
        """ThinkingEvent should serialize correctly."""
        adapter = WebSocketAdapter()
        adapter.send(_make_thinking_event("reasoning"))

        event = adapter.get_event(block=False)
        assert event == {"type": "thinking", "content": "reasoning"}

    def test_send_tool_call_event(self):
        """ToolCallEvent should serialize with all fields."""
        adapter = WebSocketAdapter()
        adapter.send(_make_tool_call_event(
            tool_name="weather",
            arguments={"city": "beijing"},
            call_id="call_abc",
        ))

        event = adapter.get_event(block=False)
        assert event["type"] == "tool_call"
        assert event["tool_name"] == "weather"
        assert event["arguments"] == {"city": "beijing"}
        assert event["call_id"] == "call_abc"

    def test_send_tool_result_event(self):
        """ToolResultEvent should serialize with all fields."""
        adapter = WebSocketAdapter()
        adapter.send(_make_tool_result_event(
            tool_name="web_search",
            result="found it",
            success=True,
            duration_ms=250.0,
        ))

        event = adapter.get_event(block=False)
        assert event["type"] == "tool_result"
        assert event["tool_name"] == "web_search"
        assert event["success"] is True
        assert event["result"] == "found it"
        assert event["duration_ms"] == 250.0
        assert event["error"] is None

    def test_send_tool_result_error(self):
        """ToolResultEvent with error should serialize error field."""
        adapter = WebSocketAdapter()
        adapter.send(_make_tool_result_event(
            tool_name="weather",
            success=False,
            error="City not found",
            result=None,
        ))

        event = adapter.get_event(block=False)
        assert event["success"] is False
        assert event["error"] == "City not found"
        assert event["result"] is None

    def test_send_stop_event(self):
        """StopEvent should serialize to {'type': 'stop', ...}."""
        adapter = WebSocketAdapter()
        adapter.send(StopEvent(stop_reason="end_turn"))

        event = adapter.get_event(block=False)
        assert event == {"type": "stop", "stop_reason": "end_turn"}

    def test_send_multiple_events_order_preserved(self):
        """Multiple events should come out in FIFO order."""
        adapter = WebSocketAdapter()
        adapter.send(_make_text_event("first"))
        adapter.send(_make_text_event("second"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        assert adapter.get_event(block=False)["content"] == "first"
        assert adapter.get_event(block=False)["content"] == "second"
        assert adapter.get_event(block=False)["type"] == "stop"


# ---------------------------------------------------------------------------
# Tests — get_event()
# ---------------------------------------------------------------------------


class TestGetEvent:
    """Tests for WebSocketAdapter.get_event()."""

    def test_get_event_blocking(self):
        """get_event(block=True) should block until event available."""
        adapter = WebSocketAdapter()
        adapter.send(_make_text_event("blocking"))

        event = adapter.get_event(block=True)
        assert event["content"] == "blocking"

    def test_get_event_non_blocking_empty_raises(self):
        """get_event(block=False) on empty queue should raise queue.Empty."""
        adapter = WebSocketAdapter()

        with pytest.raises(queue.Empty):
            adapter.get_event(block=False)

    def test_get_event_with_timeout(self):
        """get_event(timeout) should raise queue.Empty after timeout."""
        adapter = WebSocketAdapter()

        with pytest.raises(queue.Empty):
            adapter.get_event(block=True, timeout=0.01)


# ---------------------------------------------------------------------------
# Tests — close()
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for WebSocketAdapter.close()."""

    def test_close_puts_sentinel(self):
        """close() should put None on inbox."""
        adapter = WebSocketAdapter()
        adapter.close()

        # The None sentinel should be on inbox
        data = adapter._inbox.get()
        assert data is None

    def close_wake_receive(self):
        """close() should wake a blocked receive()."""
        adapter = WebSocketAdapter()
        adapter.close()

        request = adapter.receive()
        assert request.text == ""


# ---------------------------------------------------------------------------
# Tests — _serialize_event()
# ---------------------------------------------------------------------------


class TestSerializeEvent:
    """Tests for WebSocketAdapter._serialize_event() static method."""

    def test_text_event(self):
        result = WebSocketAdapter._serialize_event(TextEvent(content="hi"))
        assert result == {"type": "text", "content": "hi"}

    def test_thinking_event(self):
        result = WebSocketAdapter._serialize_event(ThinkingEvent(content="think"))
        assert result == {"type": "thinking", "content": "think"}

    def test_tool_call_event(self):
        result = WebSocketAdapter._serialize_event(
            ToolCallEvent(call_id="c1", tool_name="t1", arguments={"a": 1})
        )
        assert result == {
            "type": "tool_call",
            "call_id": "c1",
            "tool_name": "t1",
            "arguments": {"a": 1},
        }

    def test_tool_result_event(self):
        result = WebSocketAdapter._serialize_event(
            ToolResultEvent(
                call_id="c1",
                tool_name="t1",
                success=True,
                result="ok",
                error=None,
                duration_ms=50.0,
            )
        )
        assert result == {
            "type": "tool_result",
            "call_id": "c1",
            "tool_name": "t1",
            "success": True,
            "result": "ok",
            "error": None,
            "duration_ms": 50.0,
        }

    def test_tool_result_event_with_toolresult_object(self):
        """ToolResult object in result field should be unwrapped to .content."""
        from harness.interfaces.types import ToolResult

        result = WebSocketAdapter._serialize_event(
            ToolResultEvent(
                call_id="c1",
                tool_name="web_search",
                success=True,
                result=ToolResult(success=True, content="found it"),
                error=None,
                duration_ms=100.0,
            )
        )
        assert result["result"] == "found it"  # Unwrapped, not a ToolResult dict

    def test_tool_result_event_with_none_result(self):
        """None result should stay None."""
        result = WebSocketAdapter._serialize_event(
            ToolResultEvent(
                call_id="c1",
                tool_name="weather",
                success=False,
                result=None,
                error="City not found",
                duration_ms=50.0,
            )
        )
        assert result["result"] is None
        assert result["error"] == "City not found"

    def test_stop_event(self):
        result = WebSocketAdapter._serialize_event(StopEvent(stop_reason="max_tokens"))
        assert result == {"type": "stop", "stop_reason": "max_tokens"}


# ---------------------------------------------------------------------------
# Tests — session_id
# ---------------------------------------------------------------------------


class TestSessionId:
    """Tests for session_id handling."""

    def test_auto_generated_session_id(self):
        """Constructor should auto-generate a ws- prefixed nanosecond timestamp."""
        adapter = WebSocketAdapter()
        assert adapter.session_id.startswith("ws-")
        ts_str = adapter.session_id[len("ws-"):]
        assert ts_str.isdigit()
        assert int(ts_str) > 0

    def test_custom_session_id(self):
        """Constructor should accept a custom session_id."""
        adapter = WebSocketAdapter(session_id="my-custom-session")
        assert adapter.session_id == "my-custom-session"

    def test_session_id_unique_per_instance(self):
        """Each instance should have a unique session_id."""
        adapter1 = WebSocketAdapter()
        time.sleep(0.01)  # Ensure different timestamps
        adapter2 = WebSocketAdapter()
        assert adapter1.session_id != adapter2.session_id


# ---------------------------------------------------------------------------
# Tests — protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    """Tests for InputAdapter protocol conformance."""

    def test_isinstance_input_adapter(self):
        """WebSocketAdapter should satisfy InputAdapter protocol."""
        adapter = WebSocketAdapter()
        assert isinstance(adapter, InputAdapter)

    def test_has_receive_method(self):
        assert hasattr(WebSocketAdapter, "receive")
        assert callable(WebSocketAdapter.receive)

    def test_has_send_method(self):
        assert hasattr(WebSocketAdapter, "send")
        assert callable(WebSocketAdapter.send)

    def test_receive_returns_user_request(self):
        adapter = WebSocketAdapter()
        adapter.put_message({"text": "test"})
        result = adapter.receive()
        assert isinstance(result, UserRequest)


# ---------------------------------------------------------------------------
# Integration — full bridge cycle
# ---------------------------------------------------------------------------


class TestBridgeCycle:
    """End-to-end bridge cycle tests."""

    def test_full_roundtrip(self):
        """Simulate a full conversation turn: receive → send events → drain."""
        adapter = WebSocketAdapter()

        # 1. Client sends message
        adapter.put_message({"text": "What's the weather?"})

        # 2. Harness receives it
        request = adapter.receive()
        assert request.text == "What's the weather?"

        # 3. Harness sends events
        adapter.send(ThinkingEvent(content="Let me check..."))
        adapter.send(ToolCallEvent(
            call_id="c1", tool_name="weather", arguments={"city": "beijing"}
        ))
        adapter.send(ToolResultEvent(
            call_id="c1", tool_name="weather", success=True,
            result="Sunny, 28C", duration_ms=120.0,
        ))
        adapter.send(TextEvent(content="It's sunny in Beijing!"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        # 4. Client drains all events
        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        assert len(events) == 5
        assert events[0]["type"] == "thinking"
        assert events[1]["type"] == "tool_call"
        assert events[2]["type"] == "tool_result"
        assert events[3]["type"] == "text"
        assert events[4]["type"] == "stop"

    def test_multiple_receive_cycles(self):
        """Multiple user inputs across conversation turns."""
        adapter = WebSocketAdapter()

        # Turn 1
        adapter.put_message({"text": "hello"})
        req1 = adapter.receive()
        assert req1.text == "hello"
        adapter.send(TextEvent(content="Hi there!"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        # Turn 2
        adapter.put_message({"text": "bye"})
        req2 = adapter.receive()
        assert req2.text == "bye"
        adapter.send(TextEvent(content="Goodbye!"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        # Drain
        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        assert len(events) == 4
        assert events[0]["content"] == "Hi there!"
        assert events[1]["type"] == "stop"
        assert events[2]["content"] == "Goodbye!"
        assert events[3]["type"] == "stop"
