"""Test harness for LoggingSensor — batch-07 sensor implementation.

Covers all acceptance criteria defined in
sdd/batches/batch-07-sensor/design.md.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from harness.components.sensor import LoggingSensor
from harness.interfaces.sensor import Sensor
from harness.interfaces.types import (
    Message,
    SystemState,
    ToolCallRecord,
    Trajectory,
    UserRequest,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_memory() -> MagicMock:
    """Mock MemoryBackend."""
    return MagicMock()


@pytest.fixture
def populated_trajectory() -> Trajectory:
    """Trajectory with all fields populated."""
    return Trajectory(
        user_request=UserRequest(
            text="Write a hello world Python script",
            session_id="sess-001",
        ),
        history=[
            Message(role="user", content="Write a hello world Python script"),
            Message(role="assistant", content="Here is a hello world script:"),
            Message(
                role="tool",
                content="File created successfully.",
                tool_call_id="call_1",
            ),
        ],
        tool_calls=[
            ToolCallRecord(
                tool_name="write_file",
                arguments={"path": "/tmp/hello.py", "content": "print('hello')"},
                result="File created.",
                started_at=1000.0,
                finished_at=1000.5,
                error=None,
            ),
            ToolCallRecord(
                tool_name="run_command",
                arguments={"command": "python /tmp/hello.py"},
                result="",
                started_at=1001.0,
                finished_at=1001.3,
                error="Command not found: python",
            ),
        ],
        final_output="The script has been created at /tmp/hello.py",
        execution_time=2.5,
        system_state=SystemState(session_id="sess-001", phase="end"),
    )


# ============================================================================
# Tests
# ============================================================================


class TestConstructor:
    """Tests for LoggingSensor construction."""

    def test_constructor_stores_memory(self, mock_memory: MagicMock) -> None:
        """Verify that the memory attribute is set from constructor injection."""
        sensor = LoggingSensor(memory=mock_memory)
        assert sensor.memory is mock_memory


class TestProtocolConformance:
    """Tests for Sensor protocol conformance."""

    def test_protocol_conformance(self, mock_memory: MagicMock) -> None:
        """Verify LoggingSensor satisfies the Sensor Protocol."""
        sensor = LoggingSensor(memory=mock_memory)
        assert isinstance(sensor, Sensor)


class TestSenseBehavior:
    """Tests for sense() method behavior."""

    def test_sense_writes_to_episodic(
        self,
        mock_memory: MagicMock,
        populated_trajectory: Trajectory,
    ) -> None:
        """Write must be called with namespace="episodic"."""
        sensor = LoggingSensor(memory=mock_memory)
        sensor.sense(populated_trajectory)

        mock_memory.write.assert_called_once()

        # The namespace kwarg must be "episodic"
        _, kwargs = mock_memory.write.call_args
        assert kwargs["namespace"] == "episodic"

    def test_sense_value_has_all_fields(
        self,
        mock_memory: MagicMock,
        populated_trajectory: Trajectory,
    ) -> None:
        """The value dict written must contain all required keys."""
        sensor = LoggingSensor(memory=mock_memory)
        sensor.sense(populated_trajectory)

        mock_memory.write.assert_called_once()
        args, kwargs = mock_memory.write.call_args
        # key is args[0], value is args[1], namespace is kwargs["namespace"]
        value = args[1]

        expected_keys = {
            "session_id",
            "timestamp",
            "user_request",
            "final_output",
            "execution_time",
            "message_count",
            "tool_call_count",
            "tool_calls_summary",
            "history_excerpt",
        }
        assert set(value.keys()) == expected_keys

        # Spot-check a few values
        assert value["session_id"] == "sess-001"
        assert value["user_request"] == "Write a hello world Python script"
        assert value["final_output"] == "The script has been created at /tmp/hello.py"
        assert value["execution_time"] == 2.5
        assert value["message_count"] == 3
        assert value["tool_call_count"] == 2

        # tool_calls_summary structure
        assert len(value["tool_calls_summary"]) == 2
        assert value["tool_calls_summary"][0] == {
            "tool_name": "write_file",
            "success": True,
            "error": None,
        }
        assert value["tool_calls_summary"][1] == {
            "tool_name": "run_command",
            "success": False,
            "error": "Command not found: python",
        }

        # history_excerpt
        assert "Write a hello world Python script" in value["history_excerpt"]

    def test_empty_trajectory_no_raise(self, mock_memory: MagicMock) -> None:
        """A default Trajectory() should not cause sense() to raise."""
        sensor = LoggingSensor(memory=mock_memory)
        # Default-constructed Trajectory with no explicit fields
        trajectory = Trajectory()

        # Must not raise
        sensor.sense(trajectory)

        mock_memory.write.assert_called_once()
        args, kwargs = mock_memory.write.call_args
        # key is args[0], value is args[1], namespace is kwargs["namespace"]
        value = args[1]

        # Fallback session_id and zeroed values
        assert value["session_id"] == "unknown"
        assert value["user_request"] == ""
        assert value["final_output"] == ""
        assert value["execution_time"] == 0.0
        assert value["message_count"] == 0
        assert value["tool_call_count"] == 0
        assert value["tool_calls_summary"] == []
        assert value["history_excerpt"] == ""

    def test_write_failure_logs_warning(
        self,
        mock_memory: MagicMock,
        populated_trajectory: Trajectory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When write() raises, catch exception and log WARNING; do not re-raise."""
        mock_memory.write.side_effect = RuntimeError("Disk full")

        sensor = LoggingSensor(memory=mock_memory)

        with caplog.at_level(logging.WARNING, logger="harness.components.sensor.logging_sensor"):
            sensor.sense(populated_trajectory)

        # Check that WARNING was logged
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert len(warning_records) >= 1
        assert "Write failed" in warning_records[0].message
        assert "Disk full" in warning_records[0].message

    def test_multiple_sessions_different_keys(
        self, mock_memory: MagicMock
    ) -> None:
        """Two sense() calls with different session_ids must use different keys."""
        sensor = LoggingSensor(memory=mock_memory)

        traj_a = Trajectory(
            user_request=UserRequest(text="task A", session_id="sess-A"),
            execution_time=1.0,
        )
        traj_b = Trajectory(
            user_request=UserRequest(text="task B", session_id="sess-B"),
            execution_time=2.0,
        )

        sensor.sense(traj_a)
        sensor.sense(traj_b)

        assert mock_memory.write.call_count == 2

        call_args_list = mock_memory.write.call_args_list
        key_a = call_args_list[0][0][0]  # first positional arg
        key_b = call_args_list[1][0][0]  # first positional arg

        assert key_a == "session_sess-A"
        assert key_b == "session_sess-B"
        assert key_a != key_b
