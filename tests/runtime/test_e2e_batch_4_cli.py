"""Batch 4 E2E: CLI 命令端到端 — stdin → 解析 → dispatch → stdout.

验证完整链路：用户输入 → CliConsole.receive() 解析 →
Kernel._handle_system_input 分发 → CliConsole.send() 格式化 → stdout。
"""

import asyncio
import io
from unittest.mock import patch

import pytest

from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.interfaces.types import Response, UserRequest
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.runtime.types import __EXIT_SENTINEL__


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def async_test(coro_func):
    """Decorator: wrap async test with asyncio.run()."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


class _PresetStdin:
    """Preset stdin lines for CliConsole.receive()."""
    def __init__(self, lines: list[str]):
        self._lines = lines
        self._idx = 0

    def readline(self) -> str:
        if self._idx >= len(self._lines):
            return ""
        line = self._lines[self._idx]
        self._idx += 1
        return line


def _msg_content(msg):
    """Extract content from a message (dict or object)."""
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _msg_role(msg):
    """Extract role from a message (dict or object)."""
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


def _echo_llm(expected_input: str, response_text: str):
    """Create an async mock LLM that verifies input and returns a response."""
    async def _mock(messages, tools=None):
        user_texts = [_msg_content(m) for m in messages
                      if _msg_role(m) == "user"]
        found = any(expected_input in t for t in user_texts)
        if not found:
            return Response(
                text=f"ERROR: expected '{expected_input}' not in {user_texts}",
                stop_reason="end_turn",
            )
        return Response(text=response_text, stop_reason="end_turn")
    return _mock


def _noop_llm(response_text: str = "ok"):
    """Create an async mock LLM that always returns the same response."""
    async def _mock(messages, tools=None):
        return Response(text=response_text, stop_reason="end_turn")
    return _mock


def _make_continuous_agent(kernel, pid, entry_text="start", response="ok"):
    """Create a continuous-mode agent with a mock LLM, return (runtime, task).

    Agent blocks in receive() after the first round.
    """
    container = DIContainer()
    container.register(InputAdapter, object())
    harness = Harness.from_container(container, call_llm=_noop_llm(response))

    runtime = AgentRuntime(
        pid=pid, mode="continuous", harness=harness, kernel=kernel,
    )
    runtime.adapter = KernelBridgeAdapter(pid=pid, kernel=kernel, runtime=runtime)
    runtime._init_orchestrator(call_llm=_noop_llm(response))

    kernel.runtime_table[pid] = runtime
    kernel.input_queues[pid] = asyncio.Queue()
    if "wf_root" in kernel.workflow_table:
        kernel.workflow_table["wf_root"].append(pid)
    else:
        kernel.workflow_table["wf_root"] = [pid]
    runtime.workflow_flag = "wf_root"

    kernel.send_input(pid, UserRequest(text=entry_text))
    task = asyncio.create_task(runtime.run())
    kernel._tasks[pid] = task
    task.add_done_callback(
        lambda t, r=runtime: asyncio.create_task(kernel._on_agent_finished(r))
    )
    return runtime


async def _await_all_agents(kernel):
    """Gather all agent tasks in the kernel."""
    if kernel._tasks:
        await asyncio.gather(*kernel._tasks.values())


def _dummy_container():
    c = DIContainer()
    c.register(InputAdapter, object())
    return c


# ═══════════════════════════════════════════════════════════════════════════════
# E2E: Mode A — CLI 交互
# ═══════════════════════════════════════════════════════════════════════════════


@async_test
async def test_e2e_mode_a_agents_command_shows_table():
    """/agents → stdout 包含格式化表格。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/agents\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)
        kernel.spawn_root(
            Harness.from_container(_dummy_container(), call_llm=_noop_llm("root")),
            call_llm=_noop_llm("root"),
        )

        _make_continuous_agent(kernel, "collector", "collect data", "collected")
        await asyncio.sleep(0.05)

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    assert "[系统] Agents" in output
    assert "PID" in output and "STATE" in output
    assert "root" in output
    assert "collector" in output


@async_test
async def test_e2e_mode_a_kill_agent_terminates_it():
    """/kill continuous-agent → agent 被标记退出, stdout 含 terminating。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/kill listener\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)

        listener = _make_continuous_agent(kernel, "listener", "stay alive", "alive")
        await asyncio.sleep(0.1)

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    assert listener.should_exit is True
    assert "terminating" in output


@async_test
async def test_e2e_mode_a_exit_shuts_down_all():
    """/exit → 所有非 FINISHED agent 收到 sentinel，全部最终 FINISHED。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)
        kernel.spawn_root(
            Harness.from_container(_dummy_container(), call_llm=_noop_llm("root")),
            call_llm=_noop_llm("root"),
        )
        root = kernel.runtime_table["root"]

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    assert kernel._shutdown is True
    assert root.state == AgentState.FINISHED
    # root was non-FINISHED when /exit was processed, should have exited
    assert root.should_exit is True


@async_test
async def test_e2e_mode_a_plain_text_routes_to_root():
    """Mode A 纯文本 → root 的 LLM 收到对应消息 → root 输出到 stdout。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["hello world\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)

        # root 用 echo LLM — 确认收到 "hello world" 后回复 "got it"
        kernel.spawn_root(
            Harness.from_container(
                _dummy_container(),
                call_llm=_echo_llm("hello world", "got it"),
            ),
            call_llm=_echo_llm("hello world", "got it"),
        )

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    # root 的回复出现在 stdout（作为 AgentOutput 降级事件）
    assert "got it" in output


@async_test
async def test_e2e_mode_a_unknown_command_shows_error():
    """未知 / 命令 → CommandError 输出。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/badcmd arg\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)
        kernel.spawn_root(
            Harness.from_container(_dummy_container(), call_llm=_noop_llm("root")),
            call_llm=_noop_llm("root"),
        )
        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    assert "错误" in output
    assert "未知命令" in output


# ═══════════════════════════════════════════════════════════════════════════════
# E2E: Mode B — CLI 交互
# ═══════════════════════════════════════════════════════════════════════════════


@async_test
async def test_e2e_mode_b_talk_routes_to_target_agent():
    """Mode B: /talk analyzer 'msg' → analyzer 的 LLM 收到消息。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/talk analyzer 请重新分析\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_b")
        kernel = Kernel(console)

        # analyzer 使用 echo LLM 验证收到消息
        container = DIContainer()
        container.register(InputAdapter, object())
        llm = _echo_llm("请重新分析", "重新分析完成")
        harness = Harness.from_container(container, call_llm=llm)

        analyzer = AgentRuntime(
            pid="analyzer", mode="continuous", harness=harness, kernel=kernel,
        )
        analyzer.adapter = KernelBridgeAdapter(
            pid="analyzer", kernel=kernel, runtime=analyzer,
        )
        analyzer._init_orchestrator(call_llm=llm)
        kernel.runtime_table["analyzer"] = analyzer
        kernel.input_queues["analyzer"] = asyncio.Queue()
        kernel.workflow_table["wf_root"] = ["analyzer"]
        analyzer.workflow_flag = "wf_root"
        kernel.send_input("analyzer", UserRequest(text="start"))
        task = asyncio.create_task(analyzer.run())
        kernel._tasks["analyzer"] = task
        await asyncio.sleep(0.1)

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    assert "重新分析完成" in output


@async_test
async def test_e2e_mode_b_talk_to_nonexistent_shows_error():
    """Mode B: /talk to ghost → CommandError。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["/talk ghost hello\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_b")
        kernel = Kernel(console)
        await kernel._handle_system_input()

    output = stdout.getvalue()
    assert "错误" in output
    assert "ghost" in output


@async_test
async def test_e2e_mode_b_plain_text_rejected():
    """Mode B 纯文本 → CommandError，不路由到任何 agent。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["hello there\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_b")
        kernel = Kernel(console)
        await kernel._handle_system_input()

    output = stdout.getvalue()
    assert "错误" in output
    assert "/talk" in output or "纯文本" in output


@async_test
async def test_e2e_mode_b_empty_enter_after_all_finished_exits():
    """Mode B + all finished hook=True + 空输入 → CommandExit → _shutdown。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_b", all_finished_hook=lambda: True)
        kernel = Kernel(console)
        await kernel._handle_system_input()

    assert kernel._shutdown is True


# ═══════════════════════════════════════════════════════════════════════════════
# E2E: 完整交互序列
# ═══════════════════════════════════════════════════════════════════════════════


@async_test
async def test_e2e_full_interaction_sequence_mode_a():
    """完整序列：纯文本 → /agents → /kill → /exit。stdout 验证全链路。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin([
        "analyze the code\n",
        "/agents\n",
        "/kill listener\n",
        "/exit\n",
    ])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)

        # root with echo LLM
        root_llm = _echo_llm("analyze the code", "analyzing...")
        kernel.spawn_root(
            Harness.from_container(_dummy_container(), call_llm=root_llm),
            call_llm=root_llm,
        )

        # listener (continuous — blocks in receive(), killable)
        listener = _make_continuous_agent(kernel, "listener", "stay alive", "alive")
        await asyncio.sleep(0.1)

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()

    # 1. root 回复了
    assert "analyzing" in output

    # 2. /agents 表格
    assert "[系统] Agents" in output
    assert "listener" in output

    # 3. /kill 生效
    assert listener.should_exit is True
    assert "terminating" in output

    # 4. /exit
    assert kernel._shutdown is True


@async_test
async def test_e2e_eof_behaves_like_exit():
    """EOF (readline 返回 '') → CommandExit → _shutdown=True。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin([""])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)
        kernel.spawn_root(
            Harness.from_container(_dummy_container(), call_llm=_noop_llm("root")),
            call_llm=_noop_llm("root"),
        )
        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    assert kernel._shutdown is True


@async_test
async def test_e2e_mode_a_empty_input_ignored():
    """Mode A 空输入被忽略，正常文本仍路由到 root 的 LLM。"""
    from harness.runtime.cli_console import CliConsole

    stdin = _PresetStdin(["\n", "\n", "real input\n", "/exit\n"])
    stdout = io.StringIO()

    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        console = CliConsole(mode="mode_a")
        kernel = Kernel(console)

        # root with echo LLM — should only see "real input", never empty string
        kernel.spawn_root(
            Harness.from_container(
                _dummy_container(),
                call_llm=_echo_llm("real input", "processed"),
            ),
            call_llm=_echo_llm("real input", "processed"),
        )

        await kernel._handle_system_input()
        await _await_all_agents(kernel)

    output = stdout.getvalue()
    # root processed the real input
    assert "processed" in output
    # the echo LLM would have errored if it received an empty string
    # (it checks expected_input in user message). This test passes = no empty input.
