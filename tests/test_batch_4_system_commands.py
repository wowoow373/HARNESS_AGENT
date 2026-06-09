"""Tests for Batch 4: System commands + CliConsole completion.

Tests cover:
- CliConsole.receive() command parsing (18 tests)
- CliConsole.send() event formatting (5 tests)
- Kernel._handle_system_input() command dispatch (14 tests)
- Integration tests (8 tests)

Uses asyncio.run() wrapper — consistent with existing test patterns.
"""

import asyncio
import io
import time
from unittest.mock import patch, MagicMock

import pytest

from harness.runtime.types import (
    CommandTalk, CommandKill, CommandListAgents,
    CommandEndWorkflow, CommandExit, CommandTalkDirect,
    CommandError, AgentsListed, AgentStateChanged,
    SystemMessage, AgentFinished, AgentSpawned, AgentOutput,
    __EXIT_SENTINEL__,
)
from harness.runtime.agent_runtime import AgentState
from harness.interfaces.types import UserRequest


# ============================================================================
# Helpers
# ============================================================================


def async_test(coro_func):
    """Decorator: wrap async test function with asyncio.run()."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


class _MockStdin:
    """Preset readline return values for testing CliConsole.receive()."""
    def __init__(self, lines):
        self._lines = lines
        self._idx = 0

    def readline(self):
        if self._idx >= len(self._lines):
            return ""
        line = self._lines[self._idx]
        self._idx += 1
        return line


async def _receive_lines(console, lines):
    """Drive console.receive() with a mock stdin."""
    mock_stdin = _MockStdin(lines)
    with patch("sys.stdin", mock_stdin):
        return await console.receive()


# ============================================================================
# CliConsole.receive() — 18 tests
# ============================================================================


@async_test
async def test_receive_eof_returns_command_exit():
    """EOF (readline returns '') → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    mock_stdin = _MockStdin([""])
    with patch("sys.stdin", mock_stdin):
        result = await console.receive()
    assert isinstance(result, CommandExit)


@async_test
async def test_receive_mode_a_plain_text_routes_to_root():
    """Mode A plain text → CommandTalk(pid='root', text=...)"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["hello world\n"])
    assert isinstance(result, CommandTalk)
    assert result.pid == "root"
    assert result.text == "hello world"


@async_test
async def test_receive_mode_b_plain_text_returns_command_error():
    """Mode B plain text → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b")
    result = await _receive_lines(console, ["hello\n"])
    assert isinstance(result, CommandError)
    assert "纯文本" in result.error


@async_test
async def test_receive_mode_a_empty_input_ignored():
    """Mode A empty input → ignored, continues until valid input"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["\n", "actual text\n"])
    assert isinstance(result, CommandTalk)
    assert result.text == "actual text"


@async_test
async def test_receive_mode_b_empty_input_running_returns_error():
    """Mode B empty input + agents running → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b", all_finished_hook=lambda: False)
    result = await _receive_lines(console, ["\n"])
    assert isinstance(result, CommandError)


@async_test
async def test_receive_mode_b_empty_input_all_finished_returns_exit():
    """Mode B empty input + all FINISHED → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b", all_finished_hook=lambda: True)
    result = await _receive_lines(console, ["\n"])
    assert isinstance(result, CommandExit)


@async_test
async def test_receive_agents_command():
    """/agents → CommandListAgents()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/agents\n"])
    assert isinstance(result, CommandListAgents)


@async_test
async def test_receive_agents_with_extra_arg_errors():
    """/agents extra → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/agents extra\n"])
    assert isinstance(result, CommandError)


@async_test
async def test_receive_exit_command():
    """/exit → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/exit\n"])
    assert isinstance(result, CommandExit)


@async_test
async def test_receive_kill_command():
    """/kill collector → CommandKill(pid='collector')"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/kill collector\n"])
    assert isinstance(result, CommandKill)
    assert result.pid == "collector"


@async_test
async def test_receive_kill_missing_arg_errors():
    """/kill (missing arg) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/kill\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@async_test
async def test_receive_end_command():
    """/end wf_001 → CommandEndWorkflow(flag='wf_001')"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/end wf_001\n"])
    assert isinstance(result, CommandEndWorkflow)
    assert result.flag == "wf_001"


@async_test
async def test_receive_end_missing_arg_errors():
    """/end (missing arg) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/end\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@async_test
async def test_receive_talk_command():
    """/talk collector 请重新分析 → CommandTalkDirect"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector 请重新分析\n"])
    assert isinstance(result, CommandTalkDirect)
    assert result.pid == "collector"
    assert result.text == "请重新分析"


@async_test
async def test_receive_talk_missing_text_errors():
    """/talk collector (missing text) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector\n"])
    assert isinstance(result, CommandError)
    assert "缺少消息文本" in result.error


@async_test
async def test_receive_talk_missing_all_args_errors():
    """/talk (missing all args) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@async_test
async def test_receive_talk_with_spaces_in_text():
    """/talk collector  多空格  → text preserves all spaces"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector  多空格  \n"])
    assert isinstance(result, CommandTalkDirect)
    assert result.text == "多空格  "


@async_test
async def test_receive_unknown_command_errors():
    """/unknown xyz → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/unknown xyz\n"])
    assert isinstance(result, CommandError)
    assert "未知命令" in result.error
