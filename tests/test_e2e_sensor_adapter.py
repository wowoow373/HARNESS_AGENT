"""E2E integration tests — Sensor + CliAdapter + MdMemory lifecycle.

Validates:
- LoggingSensor writes trajectory to episodic on session end
- CliAdapter full flow with mocked stdin completes without error
- Memory persists across orchestrator sessions (episodic retrieval)
- Exit signals ("/exit", empty input) trigger graceful shutdown with _phase_end

These tests use tempfile.TemporaryDirectory, CliAdapter with mocked stdin,
and mock LLM callables to exercise the LifecycleOrchestrator end-to-end.
"""

import sys
import tempfile
from io import StringIO
from unittest.mock import patch

import pytest

from harness.core.container import DIContainer
from harness.core.orchestrator import LifecycleOrchestrator
from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.interfaces import InputAdapter, MemoryBackend, Sensor
from harness.interfaces.types import Response, Trajectory


# ============================================================================
# Shared helpers
# ============================================================================


def _make_text_llm(text="mock response"):
    """Return a call_llm that always returns a text-only Response."""

    def _llm(messages, tools):
        return Response(text=text, stop_reason="end_turn")

    return _llm


def _register_components(container, adapter, memory=None, sensor=None, **extra):
    """Register standard component instances in a DIContainer."""
    container.register(InputAdapter, adapter)
    if memory is not None:
        container.register(MemoryBackend, memory)
    if sensor is not None:
        container.register(Sensor, sensor)
    for iface, inst in extra.items():
        container.register(iface, inst)
    return container


class SpySensor:
    """Sensor spy — records every sense() invocation."""

    def __init__(self):
        self.calls = []

    def sense(self, trajectory: Trajectory) -> None:
        self.calls.append(trajectory)


# ============================================================================
# Test 1: Sensor writes to episodic on session end
# ============================================================================


class TestSensorWritesOnSessionEnd:
    """Verify LoggingSensor persists trajectory to episodic namespace."""

    def test_sensor_writes_on_session_end(self):
        """Full run() → LoggingSensor.sense() → episodic record exists.

        Steps:
        1. MdMemory in temp dir, LoggingSensor(memory), CliAdapter with mocked stdin
        2. DIContainer with all registered, mock_llm returning text Response
        3. LifecycleOrchestrator.run()
        4. After run, memory.search("session_", "episodic") finds the record
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # --- Setup ---
            memory = MdMemory(path=tmpdir)
            sensor = LoggingSensor(memory=memory)

            # CliAdapter: "hello" then empty line (exit signal)
            fake_stdin = StringIO("hello\n\n")
            fake_stdout = StringIO()

            with patch.object(sys, "stdin", fake_stdin), patch.object(
                sys, "stdout", fake_stdout
            ):
                adapter = CliAdapter()
                container = DIContainer()
                _register_components(container, adapter, memory=memory, sensor=sensor)

                orch = LifecycleOrchestrator(
                    container, call_llm=_make_text_llm("mock reply")
                )
                orch.run()

            # --- Assert ---
            results = memory.search("session_", "episodic")
            assert len(results) >= 1, (
                f"Expected at least 1 episodic record, got {len(results)}"
            )

            item = results[0]
            assert item.key.startswith("session_"), (
                f"Unexpected key: {item.key!r}"
            )
            assert item.namespace == "episodic"

            # The stored value is a stringified dict containing trajectory fields
            val_str = str(item.value)
            assert "user_request" in val_str
            assert "final_output" in val_str
            assert "execution_time" in val_str


# ============================================================================
# Test 2: CliAdapter full flow — orchestrator runs without error
# ============================================================================


class TestCliAdapterFullFlow:
    """Verify CliAdapter integrates with the orchestrator without errors."""

    def test_cli_adapter_full_flow(self):
        """CliAdapter with mocked stdin → orchestrator completes without error.

        The adapter receives "hello", the mock LLM responds with text,
        the adapter sends the response to stdout, and the session ends
        gracefully on the subsequent empty input.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MdMemory(path=tmpdir)
            sensor = LoggingSensor(memory=memory)

            # Two lines: "query" and "" (exit on empty input)
            fake_stdin = StringIO("query\n\n")
            fake_stdout = StringIO()

            with patch.object(sys, "stdin", fake_stdin), patch.object(
                sys, "stdout", fake_stdout
            ):
                adapter = CliAdapter()
                container = DIContainer()
                _register_components(container, adapter, memory=memory, sensor=sensor)

                orch = LifecycleOrchestrator(
                    container, call_llm=_make_text_llm("response text")
                )
                # Must not raise
                orch.run()

            # Verify the response was written to stdout
            stdout_content = fake_stdout.getvalue()
            assert "response text" in stdout_content, (
                f"Expected 'response text' in stdout, got: {stdout_content!r}"
            )

            # Verify state was cleaned up by _phase_end
            assert len(orch._history) == 0
            assert len(orch._tool_call_records) == 0
            assert orch._should_exit_flag is False


# ============================================================================
# Test 3: Memory persists across sessions
# ============================================================================


class TestMemoryPersistsAcrossSessions:
    """Verify episodic memory survives across orchestrator sessions."""

    def test_memory_persists_across_sessions(self):
        """First orchestrator writes episodic → second retrieves it.

        Session 1 writes to episodic via LoggingSensor.
        Session 2 creates a new MdMemory on the same directory (reindexes
        from disk), then _phase_init searches episodic. The retrieved
        ctx.memories must be non-empty.
        """
        # Use a distinctive token in the mock LLM response so we can
        # search for it later.  The token ends up in the LoggingSensor
        # value dict under "final_output".
        DISTINCTIVE_TOKEN = "MARKER_ZETA_8842"
        SEARCH_QUERY = "ZETA_8842"

        with tempfile.TemporaryDirectory() as tmpdir:
            # ================================================================
            # Session 1 — writes episodic memory
            # ================================================================
            memory1 = MdMemory(path=tmpdir)
            sensor1 = LoggingSensor(memory=memory1)

            fake_stdin1 = StringIO("first session input\n\n")
            with patch.object(sys, "stdin", fake_stdin1), patch.object(
                sys, "stdout", StringIO()
            ):
                adapter1 = CliAdapter()
                container1 = DIContainer()
                _register_components(
                    container1, adapter1, memory=memory1, sensor=sensor1
                )

                orch1 = LifecycleOrchestrator(
                    container1, call_llm=_make_text_llm(DISTINCTIVE_TOKEN)
                )
                orch1.run()

            # Sanity-check: session 1 wrote to episodic
            results1 = memory1.search(SEARCH_QUERY, "episodic")
            assert len(results1) >= 1, (
                "Session 1 did not persist episodic record"
            )

            # ================================================================
            # Session 2 — retrieves episodic memory from disk
            # ================================================================
            memory2 = MdMemory(path=tmpdir)  # re-indexes from disk

            # CliAdapter input must match the distinctive token so that
            # _phase_init searches for it against the episodic namespace.
            fake_stdin2 = StringIO(f"{SEARCH_QUERY}\n\n")
            with patch.object(sys, "stdin", fake_stdin2), patch.object(
                sys, "stdout", StringIO()
            ):
                adapter2 = CliAdapter()
                container2 = DIContainer()
                _register_components(container2, adapter2, memory=memory2)

                orch2 = LifecycleOrchestrator(
                    container2, call_llm=_make_text_llm("second reply")
                )

                # _phase_init searches episodic with the user's input text
                ctx = orch2._phase_init()

            # Assert: memories retrieved from the previous session
            assert len(ctx.memories) >= 1, (
                f"Expected at least 1 memory retrieved from previous session, "
                f"got {len(ctx.memories)}"
            )

            # Verify the retrieved item is from session 1
            found = False
            for item in ctx.memories:
                if DISTINCTIVE_TOKEN in str(item.value):
                    found = True
                    break
            assert found, (
                f"Retrieved memories do not contain the distinctive token "
                f"{DISTINCTIVE_TOKEN!r}: {[str(m.value)[:100] for m in ctx.memories]}"
            )


# ============================================================================
# Test 4: Exit signals trigger graceful _phase_end
# ============================================================================


class TestExitSignals:
    """Verify "/exit" and empty input both trigger graceful _phase_end."""

    @pytest.mark.parametrize(
        "stdin_content,description",
        [
            ("/exit\n", "explicit /exit command"),
            ("\n", "empty input (just newline)"),
            ("   \n", "whitespace-only input"),
        ],
    )
    def test_exit_signal_triggers_phase_end(self, stdin_content, description):
        """Exit signals in first input → _phase_end still called.

        When the very first receive() returns an exit signal the orchestrator
        skips _phase_loop entirely, but _phase_end is always invoked via the
        finally block in run().
        """
        fake_stdin = StringIO(stdin_content)
        fake_stdout = StringIO()

        spy = SpySensor()

        with patch.object(sys, "stdin", fake_stdin), patch.object(
            sys, "stdout", fake_stdout
        ):
            adapter = CliAdapter()
            container = DIContainer()
            _register_components(container, adapter, sensor=spy)

            orch = LifecycleOrchestrator(
                container, call_llm=_make_text_llm("should never be called")
            )
            orch.run()

        # _phase_end must have been called (via finally), which calls sensor.sense()
        assert len(spy.calls) == 1, (
            f"[{description}] Expected exactly 1 sensor.sense() call, "
            f"got {len(spy.calls)}"
        )

        trajectory = spy.calls[0]
        assert trajectory.history == [], (
            f"[{description}] Expected empty history on early exit"
        )
        assert trajectory.tool_calls == [], (
            f"[{description}] Expected empty tool_calls on early exit"
        )

    def test_exit_after_normal_turn(self):
        """Exit signal on second receive also triggers _phase_end.

        First input is a normal message; second input is "/exit".
        The orchestrator completes one LLM turn then exits gracefully.
        """
        fake_stdin = StringIO("one normal message\n/exit\n")
        fake_stdout = StringIO()

        spy = SpySensor()

        with patch.object(sys, "stdin", fake_stdin), patch.object(
            sys, "stdout", fake_stdout
        ):
            adapter = CliAdapter()
            container = DIContainer()
            _register_components(container, adapter, sensor=spy)

            orch = LifecycleOrchestrator(
                container, call_llm=_make_text_llm("normal reply")
            )
            orch.run()

        assert len(spy.calls) == 1, (
            f"Expected exactly 1 sensor.sense() call, got {len(spy.calls)}"
        )

        trajectory = spy.calls[0]
        # History 现在是: user → assistant（按事件流顺序）
        assert len(trajectory.history) >= 1, (
            "Expected at least 1 history entry from the normal turn"
        )
        # Find the assistant message in history
        assistant_msgs = [m for m in trajectory.history if m.role == "assistant"]
        assert len(assistant_msgs) >= 1, "Expected at least one assistant message"
        assert "normal reply" in assistant_msgs[0].content
