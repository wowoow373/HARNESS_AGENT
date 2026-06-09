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


# ============================================================================
# CliConsole.send() — 5 tests
# ============================================================================


@async_test
async def test_send_agents_listed_multiple():
    """AgentsListed with multiple agents → formatted table"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        await console.send(AgentsListed(agents={
            "root": {"state": "running", "mode": "continuous",
                     "parent": None, "rounds": 5, "error": None},
            "collector": {"state": "finished", "mode": "oneshot",
                          "parent": "root", "rounds": 1, "error": None},
        }))
    output = buf.getvalue()
    assert "root" in output
    assert "running" in output
    assert "continuous" in output
    assert "collector" in output
    assert "finished" in output
    assert "oneshot" in output


@async_test
async def test_send_agents_listed_empty():
    """AgentsListed empty → 'no running agents' message"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        await console.send(AgentsListed(agents={}))
    output = buf.getvalue()
    assert "没有运行中的 agent" in output


@async_test
async def test_send_command_error_with_command():
    """CommandError with command text → shows error + command"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        await console.send(CommandError(
            command="/kill ghost", error="pid 'ghost' 不存在"
        ))
    output = buf.getvalue()
    assert "错误" in output
    assert "/kill ghost" in output
    assert "ghost" in output


@async_test
async def test_send_command_error_no_command():
    """CommandError without command text → shows error only"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        await console.send(CommandError(
            command="", error="按 Enter 退出"
        ))
    output = buf.getvalue()
    assert "错误" in output
    assert "按 Enter 退出" in output


@async_test
async def test_send_system_message():
    """SystemMessage → informational message without 'error' prefix"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        await console.send(SystemMessage(
            message="所有 agent 已完成。按 Enter 退出..."
        ))
    output = buf.getvalue()
    assert "[系统]" in output
    assert "所有 agent 已完成" in output
    assert "错误" not in output  # SystemMessage 不应显示"错误"


# ============================================================================
# Kernel._handle_system_input() — 14 tests
# ============================================================================


class MockConsole:
    """Collects send() calls for verification."""
    def __init__(self, commands=None):
        self.sent_events = []
        self._commands = list(commands) if commands else []
        self._cmd_idx = 0

    async def receive(self):
        if self._cmd_idx >= len(self._commands):
            return CommandExit()
        cmd = self._commands[self._cmd_idx]
        self._cmd_idx += 1
        return cmd

    async def send(self, event):
        self.sent_events.append(event)


class MockAgentRuntime:
    """Minimal mock AgentRuntime for Kernel tests."""
    def __init__(self, pid, state=AgentState.RUNNING, mode="oneshot",
                 parent=None, children=None):
        self.pid = pid
        self.state = state
        self.mode = mode
        self.parent = parent
        self.children = children or []
        self.should_exit = False
        self.workflow_flag = None
        self.round_count = 0
        self.error = None
        self.started_at = time.time()
        self.last_output = ""

    def _idle_for_quiescence(self):
        return self.state == AgentState.RUNNING


class MockQueue:
    """Captures put_nowait calls."""
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


def _make_kernel_with_agents(console, agents):
    """Create a Kernel with mock agents injected.

    Args:
        console: MockConsole instance.
        agents: dict of {pid: MockAgentRuntime}.

    Returns:
        Kernel instance ready for _handle_system_input testing.
    """
    from harness.runtime.kernel import Kernel
    kernel = Kernel.__new__(Kernel)
    kernel.runtime_table = agents
    kernel.input_queues = {pid: MockQueue() for pid in agents}
    kernel._tasks = {}
    kernel.workflow_table = {"wf_root": list(agents.keys())}
    kernel._spawn_counter = 0
    kernel._console = console
    kernel._shutdown = False
    kernel.message_bus = MagicMock()
    kernel.message_bus.get_subscribers_of.return_value = []
    kernel._pending_subscriptions = []

    def _send_input(pid, request):
        if pid in kernel.input_queues:
            kernel.input_queues[pid].put_nowait(request)
    kernel.send_input = _send_input

    def kill(pid):
        agent = agents.get(pid)
        if agent and agent.state != AgentState.FINISHED:
            agent.should_exit = True
            if pid in kernel.input_queues:
                kernel.input_queues[pid].put_nowait(__EXIT_SENTINEL__)

    def end_workflow(flag):
        pids = kernel.workflow_table.get(flag, [])
        for pid in pids:
            kill(pid)
        return list(pids)

    kernel.kill = kill
    kernel.end_workflow = end_workflow
    return kernel


@async_test
async def test_handle_system_input_command_talk_to_existing_pid():
    """CommandTalk to existing pid → send_input called"""
    console = MockConsole(commands=[
        CommandTalk(pid="root", text="hello"),
        CommandExit(),
    ])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"root": root})
    await kernel._handle_system_input()
    queue = kernel.input_queues["root"]
    # First item should be the UserRequest (sentinel from /exit may follow)
    assert len(queue.items) >= 1
    assert queue.items[0].text == "hello"


@async_test
async def test_handle_system_input_command_talk_to_nonexistent_pid():
    """CommandTalk to nonexistent pid → CommandError"""
    console = MockConsole(commands=[
        CommandTalk(pid="ghost", text="hi"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1
    assert "ghost" in error_events[0].error


@async_test
async def test_handle_system_input_command_kill_existing_agent():
    """CommandKill existing agent → should_exit=True, sentinel enqueued"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    await kernel._handle_system_input()
    assert collector.should_exit is True
    assert kernel.input_queues["collector"].items[-1] is __EXIT_SENTINEL__
    state_events = [e for e in console.sent_events
                    if isinstance(e, AgentStateChanged)]
    assert len(state_events) == 1


@async_test
async def test_handle_system_input_command_kill_finished_agent():
    """CommandKill already-FINISHED agent → silent skip, no sentinel"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    kernel.input_queues["collector"].items.clear()
    await kernel._handle_system_input()
    assert collector.should_exit is False
    assert len(kernel.input_queues["collector"].items) == 0


@async_test
async def test_handle_system_input_command_kill_nonexistent_pid():
    """CommandKill nonexistent pid → CommandError"""
    console = MockConsole(commands=[
        CommandKill(pid="ghost"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1
    assert "ghost" in error_events[0].error


@async_test
async def test_handle_system_input_command_list_agents():
    """CommandListAgents → AgentsListed event"""
    console = MockConsole(commands=[
        CommandListAgents(),
        CommandExit(),
    ])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"root": root})
    await kernel._handle_system_input()
    listed_events = [e for e in console.sent_events
                     if isinstance(e, AgentsListed)]
    assert len(listed_events) == 1
    assert "root" in listed_events[0].agents


@async_test
async def test_handle_system_input_command_end_workflow_existing():
    """CommandEndWorkflow existing flag → kills each agent + AgentStateChanged"""
    console = MockConsole(commands=[
        CommandEndWorkflow(flag="wf_root"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.RUNNING)
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(
        console, {"collector": collector, "analyzer": analyzer}
    )
    kernel.workflow_table["wf_root"] = ["collector", "analyzer"]
    await kernel._handle_system_input()
    assert collector.should_exit is True
    assert analyzer.should_exit is True
    state_events = [e for e in console.sent_events
                    if isinstance(e, AgentStateChanged)]
    assert len(state_events) == 2


@async_test
async def test_handle_system_input_command_end_workflow_nonexistent():
    """CommandEndWorkflow nonexistent flag → CommandError"""
    console = MockConsole(commands=[
        CommandEndWorkflow(flag="ghost_wf"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1
    assert "ghost_wf" in error_events[0].error


@async_test
async def test_handle_system_input_command_exit():
    """CommandExit → sentinel to all non-FINISHED, _shutdown=True"""
    console = MockConsole(commands=[CommandExit()])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(
        console, {"root": root, "collector": collector}
    )
    await kernel._handle_system_input()
    assert root.should_exit is True
    assert kernel.input_queues["root"].items[-1] is __EXIT_SENTINEL__
    assert len(kernel.input_queues["collector"].items) == 0
    assert kernel._shutdown is True


@async_test
async def test_handle_system_input_command_talk_direct_existing():
    """CommandTalkDirect to existing active agent → send_input"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="analyzer", text="请重新分析"),
        CommandExit(),
    ])
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"analyzer": analyzer})
    await kernel._handle_system_input()
    queue = kernel.input_queues["analyzer"]
    # First item should be the UserRequest (sentinel from /exit may follow)
    assert len(queue.items) >= 1
    assert queue.items[0].text == "请重新分析"


@async_test
async def test_handle_system_input_command_talk_direct_nonexistent():
    """CommandTalkDirect nonexistent pid → CommandError"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="ghost", text="hi"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1


@async_test
async def test_handle_system_input_command_talk_direct_finished():
    """CommandTalkDirect to FINISHED agent → CommandError"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="collector", text="hi"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1
    assert "已结束" in error_events[0].error


@async_test
async def test_handle_system_input_command_error_passthrough():
    """CommandError (from CliConsole parse failure) → echoed directly"""
    console = MockConsole(commands=[
        CommandError(command="/bad", error="未知命令: '/bad'"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    passthrough = [e for e in console.sent_events
                   if isinstance(e, CommandError)]
    assert len(passthrough) >= 1
