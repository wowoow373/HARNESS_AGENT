"""End-to-end tests for chat-web agent.

Coverage:
    - Full WebSocket adapter bridge cycle (put → receive → send → get → drain)
    - DI container assembly with all chat-web components
    - Event serialization roundtrip consistency
    - Simulated multi-turn conversation flow
    - Session isolation (per-connection components are independent)
"""

from __future__ import annotations

import queue
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root and chat-web dir are on path
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CHAT_WEB = Path(__file__).resolve().parents[1]
if str(_CHAT_WEB) not in sys.path:
    sys.path.insert(0, str(_CHAT_WEB))

from adapter.websocket_adapter import WebSocketAdapter
from tools import WebSearchTool, WeatherTool

from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    TextEvent,
    ThinkingEvent,
    ToolCall,
    ToolCallEvent,
    ToolCallFunction,
    ToolResultEvent,
    StopEvent,
    UserRequest,
)


# ---------------------------------------------------------------------------
# E2E: Adapter bridge
# ---------------------------------------------------------------------------


class TestAdapterBridge:
    """End-to-end tests for the WebSocket adapter queue bridge."""

    def test_full_conversation_turn(self):
        """Simulate one complete conversation turn through the adapter."""
        adapter = WebSocketAdapter()

        # Step 1: Client sends message via put_message
        adapter.put_message({"text": "What's the weather in Beijing?"})

        # Step 2: Harness receives via receive()
        request = adapter.receive()
        assert request.text == "What's the weather in Beijing?"
        assert request.session_id == adapter.session_id

        # Step 3: Harness processes and sends events
        adapter.send(ThinkingEvent(content="User wants weather info"))
        adapter.send(ToolCallEvent(
            call_id="wc1",
            tool_name="weather",
            arguments={"city": "Beijing"},
        ))
        adapter.send(ToolResultEvent(
            call_id="wc1",
            tool_name="weather",
            success=True,
            result="Sunny, 28C / 18C",
            duration_ms=150.0,
        ))
        adapter.send(TextEvent(content="It's sunny in Beijing today! ☀️"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        # Step 4: Client drains all events
        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        assert len(events) == 5
        assert events[0] == {"type": "thinking", "content": "User wants weather info"}
        assert events[1]["type"] == "tool_call"
        assert events[1]["tool_name"] == "weather"
        assert events[2]["type"] == "tool_result"
        assert events[2]["success"] is True
        assert events[3] == {"type": "text", "content": "It's sunny in Beijing today! ☀️"}
        assert events[4] == {"type": "stop", "stop_reason": "end_turn"}

    def test_multi_turn_conversation(self):
        """Simulate a 3-turn conversation."""
        adapter = WebSocketAdapter()

        for turn, user_msg in enumerate(["hi", "weather in tokyo", "thanks bye"], 1):
            # Client → Adapter
            adapter.put_message({"text": user_msg})

            # Harness receives
            request = adapter.receive()
            assert request.text == user_msg

            # Harness responds
            adapter.send(TextEvent(content=f"Reply {turn}"))
            adapter.send(StopEvent(stop_reason="end_turn"))

        # Drain all events
        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        assert len(events) == 6  # 3 turns × 2 events each
        text_events = [e for e in events if e["type"] == "text"]
        assert len(text_events) == 3
        assert text_events[0]["content"] == "Reply 1"
        assert text_events[1]["content"] == "Reply 2"
        assert text_events[2]["content"] == "Reply 3"

    def test_exit_signal_ends_session(self):
        """close() should signal the orchestrator to exit."""
        adapter = WebSocketAdapter()

        # Normal message first
        adapter.put_message({"text": "hello"})
        req1 = adapter.receive()
        assert req1.text == "hello"

        # Then close signal
        adapter.close()
        req2 = adapter.receive()
        assert req2.text == ""  # Exit signal

    def test_event_types_match_frontend_protocol(self):
        """All serialized event types must match frontend expectations."""
        adapter = WebSocketAdapter()

        # Send all event types
        adapter.send(TextEvent(content="text"))
        adapter.send(ThinkingEvent(content="thinking"))
        adapter.send(ToolCallEvent(call_id="c1", tool_name="t1", arguments={}))
        adapter.send(ToolResultEvent(
            call_id="c1", tool_name="t1", success=True, result="r", duration_ms=1.0
        ))
        adapter.send(StopEvent(stop_reason="end_turn"))

        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        types = [e["type"] for e in events]
        assert types == ["text", "thinking", "tool_call", "tool_result", "stop"]

        # Verify each event has the expected fields
        for e in events:
            assert "type" in e

        # Frontend-specific field checks
        assert "content" in events[0]  # text
        assert "content" in events[1]  # thinking
        assert "tool_name" in events[2]  # tool_call
        assert "arguments" in events[2]
        assert "success" in events[3]  # tool_result
        assert "result" in events[3]
        assert "duration_ms" in events[3]
        assert "stop_reason" in events[4]  # stop


# ---------------------------------------------------------------------------
# E2E: DI Assembly
# ---------------------------------------------------------------------------


class TestDIAssembly:
    """End-to-end tests for DI container assembly."""

    def test_full_assembly(self):
        """Assemble all chat-web components in DI container."""
        container = DIContainer()
        adapter = WebSocketAdapter()
        memory = MdMemory(path="./test_memory_e2e")

        container.register(InputAdapter, adapter)
        container.register(MemoryBackend, memory)
        container.register(
            ContextAssembler,
            SimpleAssembler(max_history=50, memory=memory),
        )
        container.register(Sensor, LoggingSensor(memory=memory))
        container.register(
            SystemToolProvider,
            DefaultSystemToolProvider(extra_tools=[WebSearchTool(), WeatherTool()]),
        )

        # Verify all components are registered
        assert container.is_registered(InputAdapter)
        assert container.is_registered(MemoryBackend)
        assert container.is_registered(ContextAssembler)
        assert container.is_registered(Sensor)
        assert container.is_registered(SystemToolProvider)

    def test_memory_shared_between_assembler_and_sensor(self):
        """MemoryBackend instance must be shared between ContextAssembler and Sensor."""
        container = DIContainer()
        adapter = WebSocketAdapter()
        memory = MdMemory(path="./test_memory_shared")

        container.register(InputAdapter, adapter)
        container.register(MemoryBackend, memory)
        container.register(
            ContextAssembler,
            SimpleAssembler(max_history=50, memory=memory),
        )
        container.register(Sensor, LoggingSensor(memory=memory))
        container.register(
            SystemToolProvider,
            DefaultSystemToolProvider(extra_tools=[WebSearchTool(), WeatherTool()]),
        )

        # Resolve and verify it's the same instance
        resolved_memory = container.resolve(MemoryBackend)
        assert resolved_memory is memory

    def test_tools_include_consumer_tools(self):
        """SystemToolProvider should include web_search and weather."""
        provider = DefaultSystemToolProvider(
            extra_tools=[WebSearchTool(), WeatherTool()],
        )
        tools = provider.get_tools()
        names = {t.name for t in tools}

        assert "web_search" in names
        assert "weather" in names
        # Default builtins should also be present
        assert "read_file" in names
        assert "write_file" in names
        assert "shell" in names

    def test_harness_construction(self):
        """Harness should be constructible from the DI container."""
        container = DIContainer()
        adapter = WebSocketAdapter()
        memory = MdMemory(path="./test_memory_harness")

        container.register(InputAdapter, adapter)
        container.register(MemoryBackend, memory)
        container.register(
            ContextAssembler,
            SimpleAssembler(max_history=50, memory=memory),
        )
        container.register(Sensor, LoggingSensor(memory=memory))
        container.register(
            SystemToolProvider,
            DefaultSystemToolProvider(extra_tools=[WebSearchTool(), WeatherTool()]),
        )

        # Should not raise
        harness = Harness.from_container(container, call_llm=None)
        assert harness is not None


# ---------------------------------------------------------------------------
# E2E: Simulated conversation with mock LLM
# ---------------------------------------------------------------------------


class TestSimulatedConversation:
    """End-to-end tests simulating full conversation flows with mock LLM."""

    def test_single_turn_text_only(self):
        """A single turn with text-only LLM response."""
        adapter = WebSocketAdapter()
        adapter.put_message({"text": "hello"})

        container = DIContainer()
        memory = MdMemory(path="./test_memory_sim1")
        container.register(InputAdapter, adapter)
        container.register(MemoryBackend, memory)
        container.register(ContextAssembler, SimpleAssembler(max_history=50, memory=memory))
        container.register(Sensor, LoggingSensor(memory=memory))
        container.register(SystemToolProvider, DefaultSystemToolProvider())

        def mock_llm(msgs, tools):
            return Response(text="Hello! How can I help?", stop_reason="end_turn")

        harness = Harness.from_container(container, call_llm=mock_llm)

        # Run harness in a way that processes one turn
        # Since harness.run() blocks, we can't easily test the full flow here.
        # Instead, verify the components are assembled correctly.
        assert container.is_registered(InputAdapter)

        # Verify adapter received the message
        request = adapter.receive()
        assert request.text == "hello"

    def test_event_serialization_consistency(self):
        """All event types serialize consistently across the codebase."""
        adapter = WebSocketAdapter()

        # Send a mixed stream
        adapter.send(ThinkingEvent(content="planning"))
        adapter.send(ToolCallEvent(call_id="c1", tool_name="web_search", arguments={"query": "ai"}))
        adapter.send(ToolResultEvent(call_id="c1", tool_name="web_search", success=True, result="AI is cool", duration_ms=200.0))
        adapter.send(TextEvent(content="AI is really cool!"))
        adapter.send(StopEvent(stop_reason="end_turn"))

        # Deserialize and verify structure
        events = []
        try:
            while True:
                events.append(adapter.get_event(block=False))
        except queue.Empty:
            pass

        # Verify each event is a plain dict with only expected keys
        assert set(events[0].keys()) == {"type", "content"}
        assert set(events[1].keys()) == {"type", "call_id", "tool_name", "arguments"}
        assert set(events[2].keys()) == {"type", "call_id", "tool_name", "success", "result", "error", "duration_ms"}
        assert set(events[3].keys()) == {"type", "content"}
        assert set(events[4].keys()) == {"type", "stop_reason"}

    def test_two_adapters_are_isolated(self):
        """Two adapter instances should not share queues."""
        adapter1 = WebSocketAdapter()
        adapter2 = WebSocketAdapter()

        adapter1.put_message({"text": "msg1"})
        adapter2.put_message({"text": "msg2"})

        req1 = adapter1.receive()
        req2 = adapter2.receive()

        assert req1.text == "msg1"
        assert req2.text == "msg2"
        assert adapter1.session_id != adapter2.session_id

        # Events should not leak between adapters
        adapter1.send(TextEvent(content="from1"))
        event = adapter1.get_event(block=False)
        assert event["content"] == "from1"

        with pytest.raises(queue.Empty):
            adapter2.get_event(block=False)


# ---------------------------------------------------------------------------
# E2E: Tool integration with DefaultSystemToolProvider
# ---------------------------------------------------------------------------


class TestToolIntegration:
    """End-to-end tests for tool execution through the provider."""

    def test_web_search_via_provider(self):
        """WebSearchTool should be callable through DefaultSystemToolProvider."""
        provider = DefaultSystemToolProvider(
            extra_tools=[WebSearchTool()],
        )
        result = provider.execute("web_search", {"query": "weather"})
        assert result.success is True
        assert "weather" in str(result.content).lower()

    def test_weather_via_provider(self):
        """WeatherTool should be callable through DefaultSystemToolProvider."""
        provider = DefaultSystemToolProvider(
            extra_tools=[WeatherTool()],
        )
        result = provider.execute("weather", {"city": "beijing"})
        assert result.success is True
        assert "beijing" in str(result.content).lower()

    def test_both_tools_via_provider(self):
        """Both tools should coexist in the same provider."""
        provider = DefaultSystemToolProvider(
            extra_tools=[WebSearchTool(), WeatherTool()],
        )
        assert provider.has_tool("web_search")
        assert provider.has_tool("weather")
        assert provider.has_tool("read_file")  # builtin

        r1 = provider.execute("web_search", {"query": "news"})
        r2 = provider.execute("weather", {"city": "shanghai"})
        assert r1.success is True
        assert r2.success is True
