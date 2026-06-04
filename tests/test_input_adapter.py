"""Unit tests for CliAdapter.

Coverage:
    - receive() returns UserRequest with correct text
    - receive() populates session_id
    - receive() handles empty / EOF input
    - send(TextEvent) outputs content to stdout
    - send(TextEvent) with empty content produces no output
    - send(ThinkingEvent) prints to stderr only when debug=True
    - send(ToolCallEvent) prints to stderr with tool name + args summary
    - send(ToolResultEvent) prints to stderr with result + duration
    - send(StopEvent) is a no-op
    - _summarize_args() and _summarize_result() static methods
    - debug property (default False, settable)
    - session_id constructor parameter
    - protocol conformance (isinstance check, signature)
    - custom prompt display
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.interfaces.input_adapter import InputAdapter
from harness.interfaces.types import (
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

def _make_text_event(content: str) -> TextEvent:
    """Shorthand for building a TextEvent."""
    return TextEvent(content=content)


def _make_thinking_event(content: str) -> ThinkingEvent:
    """Shorthand for building a ThinkingEvent."""
    return ThinkingEvent(content=content)


def _make_tool_call_event(
    tool_name: str = "bash",
    arguments: dict | None = None,
    call_id: str = "call_1",
) -> ToolCallEvent:
    """Shorthand for building a ToolCallEvent."""
    return ToolCallEvent(
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments or {},
    )


def _make_tool_result_event(
    tool_name: str = "bash",
    result: object = "output",
    call_id: str = "call_1",
    success: bool = True,
    error: str | None = None,
    duration_ms: float = 123.4,
) -> ToolResultEvent:
    """Shorthand for building a ToolResultEvent."""
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
    """Tests for CliAdapter.receive()."""

    def test_receive_returns_user_request(self):
        """receive() should return a UserRequest with the stripped input text."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("hello\n")):
            request = adapter.receive()

        assert isinstance(request, UserRequest)
        assert request.text == "hello"

    def test_receive_has_session_id(self):
        """receive() should populate session_id on the returned UserRequest."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("hello\n")):
            request = adapter.receive()

        assert request.session_id == adapter.session_id
        assert request.session_id.startswith("cli-")

    def test_receive_empty_input(self):
        """receive() should return text='' for empty/whitespace-only input."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("\n")):
            request = adapter.receive()

        assert request.text == ""

    def test_receive_eof_returns_empty(self):
        """receive() should return text='' when stdin reaches EOF."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("")):
            request = adapter.receive()

        assert request.text == ""

    def test_receive_strips_whitespace(self):
        """receive() should strip leading/trailing whitespace."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("   hello world   \n")):
            request = adapter.receive()

        assert request.text == "hello world"


# ---------------------------------------------------------------------------
# Tests — send(TextEvent)
# ---------------------------------------------------------------------------


class TestSendTextEvent:
    """Tests for CliAdapter.send() with TextEvent."""

    def test_text_event_outputs_to_stdout(self):
        """TextEvent should print content to stdout."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_text_event("hello world"))
            output = mock_stdout.getvalue()

        assert output == "hello world\n"

    def test_text_event_empty_content_no_output(self):
        """TextEvent with empty content should produce no stdout output."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_text_event(""))
            output = mock_stdout.getvalue()

        assert output == ""

    def test_text_event_none_content_handled(self):
        """TextEvent with None-ish content should not crash."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(TextEvent(content=None))
            output = mock_stdout.getvalue()

        # None is falsy so nothing should be printed
        assert output == ""

    def test_text_event_multi_line(self):
        """TextEvent should output multi-line content correctly."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_text_event("line1\nline2\nline3"))
            output = mock_stdout.getvalue()

        assert "line1" in output
        assert "line2" in output
        assert "line3" in output


# ---------------------------------------------------------------------------
# Tests — send(ThinkingEvent)
# ---------------------------------------------------------------------------


class TestSendThinkingEvent:
    """Tests for CliAdapter.send() with ThinkingEvent."""

    def test_thinking_event_debug_true_prints_to_stderr(self):
        """ThinkingEvent should print to stderr when debug=True."""
        adapter = CliAdapter(debug=True)

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_thinking_event("reasoning here"))
            output = mock_stderr.getvalue()

        assert "[thinking] reasoning here" in output

    def test_thinking_event_debug_false_no_output(self):
        """ThinkingEvent should produce no output when debug=False (default)."""
        adapter = CliAdapter()  # debug defaults to False

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_thinking_event("secret reasoning"))
            output = mock_stderr.getvalue()

        assert output == ""

    def test_thinking_event_empty_content_no_output_even_with_debug(self):
        """ThinkingEvent with empty content should not print even when debug=True."""
        adapter = CliAdapter(debug=True)

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_thinking_event(""))
            output = mock_stderr.getvalue()

        assert output == ""

    def test_thinking_event_does_not_go_to_stdout(self):
        """ThinkingEvent should never print to stdout."""
        adapter = CliAdapter(debug=True)

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_thinking_event("reasoning"))
            output = mock_stdout.getvalue()

        assert output == ""


# ---------------------------------------------------------------------------
# Tests — send(ToolCallEvent)
# ---------------------------------------------------------------------------


class TestSendToolCallEvent:
    """Tests for CliAdapter.send() with ToolCallEvent."""

    def test_tool_call_event_prints_to_stderr(self):
        """ToolCallEvent should print tool name and args summary to stderr."""
        adapter = CliAdapter()

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_tool_call_event(
                tool_name="bash",
                arguments={"command": "ls -la"},
            ))
            output = mock_stderr.getvalue()

        assert "bash" in output
        assert "ls -la" in output

    def test_tool_call_event_empty_args(self):
        """ToolCallEvent with empty args should still print tool name."""
        adapter = CliAdapter()

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_tool_call_event(
                tool_name="pwd",
                arguments={},
            ))
            output = mock_stderr.getvalue()

        assert "pwd" in output


# ---------------------------------------------------------------------------
# Tests — send(ToolResultEvent)
# ---------------------------------------------------------------------------


class TestSendToolResultEvent:
    """Tests for CliAdapter.send() with ToolResultEvent."""

    def test_tool_result_success_prints_to_stderr(self):
        """ToolResultEvent (success) should print to stderr with result."""
        adapter = CliAdapter()

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_tool_result_event(
                tool_name="bash",
                result="file1.txt\nfile2.txt",
            ))
            output = mock_stderr.getvalue()

        assert "bash" in output
        assert "OK" in output
        assert "123ms" in output

    def test_tool_result_error_prints_to_stderr(self):
        """ToolResultEvent (error) should print error details to stderr."""
        adapter = CliAdapter()

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_tool_result_event(
                tool_name="bash",
                success=False,
                error="command not found",
                result=None,
            ))
            output = mock_stderr.getvalue()

        assert "bash" in output
        assert "ERROR" in output
        assert "command not found" in output

    def test_tool_result_includes_duration(self):
        """ToolResultEvent should include duration in milliseconds."""
        adapter = CliAdapter()

        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(_make_tool_result_event(duration_ms=456.7))
            output = mock_stderr.getvalue()

        assert "457ms" in output


# ---------------------------------------------------------------------------
# Tests — send(StopEvent)
# ---------------------------------------------------------------------------


class TestSendStopEvent:
    """Tests for CliAdapter.send() with StopEvent."""

    def test_stop_event_is_noop(self):
        """StopEvent should produce no output on stdout or stderr."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            with patch.object(sys, "stderr", StringIO()) as mock_stderr:
                adapter.send(StopEvent(stop_reason="end_turn"))
                assert mock_stdout.getvalue() == ""
                assert mock_stderr.getvalue() == ""


# ---------------------------------------------------------------------------
# Tests — _summarize_args()
# ---------------------------------------------------------------------------


class TestSummarizeArgs:
    """Tests for CliAdapter._summarize_args() static method."""

    def test_empty_args_returns_empty_string(self):
        """Empty args dict should return empty string."""
        result = CliAdapter._summarize_args("bash", {})
        assert result == ""

    def test_read_file_shows_path(self):
        """For read_file, the file_path should be shown."""
        result = CliAdapter._summarize_args("read_file", {"file_path": "/tmp/test.txt"})
        assert "/tmp/test.txt" in result

    def test_write_file_shows_path(self):
        """For write_file, the file_path should be shown."""
        result = CliAdapter._summarize_args("write_file", {"file_path": "/etc/config"})
        assert "/etc/config" in result

    def test_shell_shows_command(self):
        """For shell tool, the command should be shown."""
        result = CliAdapter._summarize_args("shell", {"command": "echo hello"})
        assert "echo hello" in result

    def test_generic_tool_shows_key_value_pairs(self):
        """Generic (non-read/write/shell) tools should show key=value pairs."""
        result = CliAdapter._summarize_args("search", {"query": "pytest", "limit": 10})
        assert "query=pytest" in result
        assert "limit=10" in result

    def test_long_values_are_truncated(self):
        """Values longer than 60 chars should be truncated with '...'."""
        long_val = "x" * 100
        result = CliAdapter._summarize_args("generic", {"key": long_val})
        assert "..." in result
        assert len(result) <= 120

    def test_overall_summary_truncated_at_120(self):
        """The overall summary string is capped at 120 characters."""
        args = {f"k{i}": f"v{i}" for i in range(20)}
        result = CliAdapter._summarize_args("generic", args)
        assert len(result) <= 120


# ---------------------------------------------------------------------------
# Tests — _summarize_result()
# ---------------------------------------------------------------------------


class TestSummarizeResult:
    """Tests for CliAdapter._summarize_result() static method."""

    def test_none_result_returns_null(self):
        """None result should return 'null'."""
        result = CliAdapter._summarize_result(None)
        assert result == "null"

    def test_string_result_returned_as_is(self):
        """String result should be returned directly (short)."""
        result = CliAdapter._summarize_result("hello")
        assert result == "hello"

    def test_long_string_result_truncated(self):
        """String result longer than 120 chars should be truncated."""
        long_str = "x" * 200
        result = CliAdapter._summarize_result(long_str)
        assert len(result) <= 120
        assert result.endswith("...")

    def test_non_string_result_converted(self):
        """Non-string result should be converted to str and truncated if needed."""
        result = CliAdapter._summarize_result(42)
        assert "42" in result

    def test_object_with_content_attr(self):
        """Object with .content attribute should use that attribute."""

        class FakeToolResult:
            def __init__(self, content):
                self.content = content

        fake = FakeToolResult("result content")
        result = CliAdapter._summarize_result(fake)
        assert result == "result content"


# ---------------------------------------------------------------------------
# Tests — debug property
# ---------------------------------------------------------------------------


class TestDebug:
    """Tests for CliAdapter.debug property."""

    def test_debug_defaults_to_false(self):
        """debug should default to False."""
        adapter = CliAdapter()
        assert adapter.debug is False

    def test_debug_setter(self):
        """debug should be settable to True."""
        adapter = CliAdapter()
        adapter.debug = True
        assert adapter.debug is True

    def test_debug_constructor_parameter(self):
        """debug should be settable via constructor."""
        adapter = CliAdapter(debug=True)
        assert adapter.debug is True

    def test_debug_toggle(self):
        """debug should be toggleable back and forth."""
        adapter = CliAdapter()
        adapter.debug = True
        assert adapter.debug is True
        adapter.debug = False
        assert adapter.debug is False


# ---------------------------------------------------------------------------
# Tests — session_id
# ---------------------------------------------------------------------------


class TestSessionId:
    """Tests for CliAdapter session_id handling."""

    def test_custom_session_id(self):
        """Constructor should accept and preserve a custom session_id."""
        adapter = CliAdapter(session_id="test-session")
        assert adapter.session_id == "test-session"

    def test_auto_generated_session_id(self):
        """Constructor should auto-generate a session_id when None is passed."""
        adapter = CliAdapter()
        assert adapter.session_id.startswith("cli-")
        # Should be a valid integer timestamp after the prefix
        ts_str = adapter.session_id[len("cli-"):]
        assert ts_str.isdigit()


# ---------------------------------------------------------------------------
# Tests — protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    """Tests for InputAdapter protocol conformance."""

    def test_protocol_conformance(self):
        """CliAdapter should be recognised as an InputAdapter via isinstance."""
        adapter = CliAdapter()
        assert isinstance(adapter, InputAdapter)

    def test_protocol_has_receive(self):
        """CliAdapter must implement receive() -> UserRequest."""
        assert hasattr(CliAdapter, "receive")
        assert callable(CliAdapter.receive)

    def test_protocol_has_send(self):
        """CliAdapter must implement send(event: AdapterEvent) -> None."""
        assert hasattr(CliAdapter, "send")
        assert callable(CliAdapter.send)

    def test_send_accepts_text_event(self):
        """send() should accept a TextEvent without error."""
        adapter = CliAdapter()
        # Should not raise
        adapter.send(TextEvent(content="test"))

    def test_send_accepts_stop_event(self):
        """send() should accept a StopEvent without error."""
        adapter = CliAdapter()
        # Should not raise
        adapter.send(StopEvent())


# ---------------------------------------------------------------------------
# Tests — prompt
# ---------------------------------------------------------------------------


class TestPrompt:
    """Tests for the prompt property."""

    def test_default_prompt(self):
        """Default prompt should be '> '."""
        adapter = CliAdapter()
        assert adapter.prompt == "> "

    def test_custom_prompt_displayed(self):
        """Custom prompt should be written to stdout on receive()."""
        adapter = CliAdapter()
        adapter.prompt = "query> "

        with patch.object(sys, "stdin", StringIO("hello\n")):
            with patch.object(sys, "stdout", StringIO()) as mock_stdout:
                adapter.receive()
                output = mock_stdout.getvalue()

        assert output == "query> "

    def test_prompt_setter(self):
        """Setting the prompt property should update the value."""
        adapter = CliAdapter()
        adapter.prompt = "input: "
        assert adapter.prompt == "input: "


# ---------------------------------------------------------------------------
# Integration-style test
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end receive-then-send round-trip."""

    def test_receive_send_roundtrip(self):
        """A complete receive/send cycle should work without errors."""
        adapter = CliAdapter()

        with patch.object(sys, "stdin", StringIO("hello world\n")):
            request = adapter.receive()

        assert request.text == "hello world"

        # Send a TextEvent as the response
        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_text_event("echo: hello world"))
            output = mock_stdout.getvalue()

        assert "echo: hello world" in output

    def test_receive_send_multiple_events(self):
        """A full cycle: thinking, tool call, tool result, text, stop."""
        adapter = CliAdapter(debug=True)

        with patch.object(sys, "stdin", StringIO("run ls\n")):
            request = adapter.receive()

        assert request.text == "run ls"

        # Simulate a full event stream from the orchestrator
        with patch.object(sys, "stderr", StringIO()) as mock_stderr:
            adapter.send(ThinkingEvent(content="Let me list files"))
            adapter.send(ToolCallEvent(
                call_id="c1", tool_name="bash",
                arguments={"command": "ls"},
            ))
            adapter.send(ToolResultEvent(
                call_id="c1", tool_name="bash",
                result="file1.txt\nfile2.txt",
                duration_ms=50.0,
            ))
            stderr_output = mock_stderr.getvalue()

        assert "thinking" in stderr_output.lower()
        assert "bash" in stderr_output
        assert "OK" in stderr_output
        assert "50ms" in stderr_output

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(TextEvent(content="Here are the files"))
            adapter.send(StopEvent(stop_reason="end_turn"))
            stdout_output = mock_stdout.getvalue()

        assert "Here are the files" in stdout_output
