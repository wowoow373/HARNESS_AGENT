"""Unit tests for CliAdapter.

Coverage:
    - receive() returns UserRequest with correct text
    - receive() populates session_id
    - receive() handles empty / EOF input
    - send() outputs text to stdout
    - send() with only tool_uses produces no text output
    - session_id constructor parameter
    - protocol conformance (isinstance check)
    - custom prompt display
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.interfaces.input_adapter import InputAdapter
from harness.interfaces.types import Response, UserRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(text=None, tool_uses=None):
    """Shorthand for building a Response object in tests."""
    from harness.interfaces.types import ToolCall

    return Response(
        text=text,
        tool_uses=tool_uses or [],
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
# Tests — send()
# ---------------------------------------------------------------------------


class TestSend:
    """Tests for CliAdapter.send()."""

    def test_send_outputs_text(self):
        """send() should print response.text to stdout."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_response(text="hi"))
            output = mock_stdout.getvalue()

        assert output == "hi\n"

    def test_send_no_text_no_output(self):
        """send() should produce no output when response.text is None or empty."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_response(text=None))
            output = mock_stdout.getvalue()

        assert output == ""

    def test_send_empty_text_no_output(self):
        """send() should produce no output when response.text is an empty string."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_response(text=""))
            output = mock_stdout.getvalue()

        assert output == ""

    def test_send_multi_line_text(self):
        """send() should output multi-line response text correctly."""
        adapter = CliAdapter()

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_response(text="line1\nline2\nline3"))
            output = mock_stdout.getvalue()

        assert "line1" in output
        assert "line2" in output
        assert "line3" in output


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
        """CliAdapter must implement send(response: Response) -> None."""
        assert hasattr(CliAdapter, "send")
        assert callable(CliAdapter.send)


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

        with patch.object(sys, "stdout", StringIO()) as mock_stdout:
            adapter.send(_make_response(text="echo: hello world"))
            output = mock_stdout.getvalue()

        assert "echo: hello world" in output
