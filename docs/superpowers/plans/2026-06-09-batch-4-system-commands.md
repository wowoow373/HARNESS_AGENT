# Batch 4: 系统命令 + CliConsole 完善 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 CLI 交互式多 agent Runtime 管理——用户通过 `/` 前缀命令查看 agent 状态、定向通信、终止 agent/workflow、优雅退出。

**Architecture:** CliConsole（前端）负责 stdin 解析为类型化 SystemCommand，Kernel._handle_system_input（后端）负责命令分发。解析失败由 CliConsole 产生 CommandError，执行失败由 Kernel 产生。Mode B 结束后自然等待用户按回车退出。

**Tech Stack:** Python 3.12+, asyncio, dataclasses, pytest

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `harness/runtime/types.py` | 修改 | 新增 5 个 Command 类型 + 3 个 Event 类型 + 更新 2 个 union |
| `harness/runtime/cli_console.py` | 修改 | 重写 `receive()`（命令解析），追加 `send()` 分支，新增 `__init__` 参数 |
| `harness/runtime/kernel.py` | 修改 | 重写 `_handle_system_input()` 命令分发循环 |
| `harness/runtime/runtime.py` | 修改 | 修正 `_run_from_script_async` finally 块 |
| `harness/runtime/__init__.py` | 修改 | 新增类型 re-export |
| `tests/test_batch_4_system_commands.py` | 创建 | 全部单元测试 + 集成测试 |

---

### Task 1: 新增 SystemCommand 类型定义

**Files:**
- Modify: `harness/runtime/types.py`

- [ ] **Step 1: 在 types.py 末尾（`SystemCommand = CommandTalk` 行之后）新增 5 个 Command dataclass 和 3 个 Event dataclass**

在 `SystemCommand = CommandTalk` 行**之前**插入以下代码（即替换从 `SystemCommand = CommandTalk` 开始的部分）：

```python
# harness/runtime/types.py — 替换原有的 "SystemCommand = CommandTalk" 及之后所有内容

@dataclass
class CommandKill:
    """/kill <pid> — 终止指定 agent。"""
    pid: str


@dataclass
class CommandListAgents:
    """/agents — 列出所有 agent 状态。"""
    pass


@dataclass
class CommandEndWorkflow:
    """/end <flag> — 终止整个 workflow。"""
    flag: str


@dataclass
class CommandExit:
    """/exit — 优雅退出 Runtime。"""
    pass


@dataclass
class CommandTalkDirect:
    """/talk <pid> <text> — 定向向指定 agent 发送消息（Mode B）。"""
    pid: str
    text: str


# ── SystemCommand union 更新 ──
# 注意：CommandError 也在 union 中——CliConsole.receive() 解析失败时
# 返回 CommandError 作为"命令"，Kernel 收到后透传给 console.send() 显示。
SystemCommand = (
    CommandTalk | CommandKill | CommandListAgents
    | CommandEndWorkflow | CommandExit | CommandTalkDirect
    | CommandError
)


# ── 新增 SystemEvent 类型 ──

@dataclass
class AgentsListed:
    """/agents 响应 — agent 状态快照。"""
    agents: dict = field(default_factory=dict)


@dataclass
class CommandError:
    """系统命令执行失败。"""
    command: str = ""
    error: str = ""


@dataclass
class SystemMessage:
    """系统信息提示（非错误）。

    区别于 CommandError：不应以"[系统] 错误:" 前缀显示。
    """
    message: str = ""


# ── SystemEvent union 更新 — 替换原有 union ──
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
    | WorkflowFinished
    | AgentsListed     # Batch 4
    | CommandError     # Batch 4
    | SystemMessage    # Batch 4
)
```

**注意**：原有 types.py 中 `SystemCommand = CommandTalk` 和 `SystemEvent = (...)` 两行需要删除（替换为上述代码）。原有的 `AgentSpawned`, `AgentStateChanged`, `AgentFinished`, `AgentOutput`, `RuntimeStarted`, `RuntimeStopped`, `WorkflowFinished`, `CommandTalk`, `InternalMessage`, `__EXIT_SENTINEL__` 定义全部保留不动。

- [ ] **Step 2: 验证 import 无循环引用**

Run: `python -c "from harness.runtime.types import CommandKill, CommandListAgents, CommandEndWorkflow, CommandExit, CommandTalkDirect, AgentsListed, CommandError, SystemMessage, SystemCommand, SystemEvent; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 验证 SystemCommand union 包含所有类型**

Run:
```bash
python -c "
from harness.runtime.types import *
from typing import get_args
cmds = get_args(SystemCommand)
print([c.__name__ for c in cmds])
"
```
Expected: `['CommandTalk', 'CommandKill', 'CommandListAgents', 'CommandEndWorkflow', 'CommandExit', 'CommandTalkDirect', 'CommandError']`

- [ ] **Step 4: 提交**

```bash
git add harness/runtime/types.py
git commit -m "feat(batch4): add SystemCommand and SystemEvent types

- CommandKill, CommandListAgents, CommandEndWorkflow, CommandExit, CommandTalkDirect
- AgentsListed, CommandError, SystemMessage
- Update SystemCommand and SystemEvent unions
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: CliConsole.__init__ 新增 mode 和 hook 参数

**Files:**
- Modify: `harness/runtime/cli_console.py`

- [ ] **Step 1: 修改 CliConsole.__init__ 添加 mode 和 all_finished_hook 参数，以及 set_all_finished_hook 公开方法**

将现有 `__init__` 方法（当前只有 `def __init__(self):` 或类似的简单初始化）替换为：

```python
class CliConsole:
    """SystemConsole 默认 CLI 实现。

    receive() 在后台线程读取 stdin，不阻塞 event loop。
    send() 将系统事件格式化为人类可读的文本输出到 stdout。

    Batch 4: 支持 / 前缀命令解析，Mode A/B 纯文本路由。
    """

    def __init__(
        self,
        mode: str = "mode_a",
        all_finished_hook: 'Callable[[], bool] | None' = None,
    ):
        """初始化 CliConsole。

        Args:
            mode: "mode_a"（纯文本路由到 root）或 "mode_b"
                  （纯文本需 /talk 定向）。
            all_finished_hook: Mode B 下用于判断所有 agent 是否已结束。
        """
        self._mode = mode
        self._all_finished_hook = all_finished_hook

    def set_all_finished_hook(self, hook: 'Callable[[], bool]') -> None:
        """设置 all_finished 查询回调（Mode B 下用于判断是否全部完成）。

        通过公开方法注入，而非直接修改 _all_finished_hook 属性。
        """
        self._all_finished_hook = hook
```

- [ ] **Step 2: 验证向后兼容——默认构造不抛异常**

Run: `python -c "from harness.runtime.cli_console import CliConsole; c = CliConsole(); print(c._mode); print(c._all_finished_hook)"`
Expected: `mode_a` / `None`

- [ ] **Step 3: 验证 set_all_finished_hook**

Run:
```bash
python -c "
from harness.runtime.cli_console import CliConsole
c = CliConsole(mode='mode_b')
c.set_all_finished_hook(lambda: True)
print(c._all_finished_hook())
"
```
Expected: `True`

- [ ] **Step 4: 提交**

```bash
git add harness/runtime/cli_console.py
git commit -m "feat(batch4): add mode and all_finished_hook params to CliConsole

- __init__ accepts mode='mode_a'|'mode_b' and optional all_finished_hook
- set_all_finished_hook() public method for Runtime to inject callback
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CliConsole.receive() 命令解析

**Files:**
- Modify: `harness/runtime/cli_console.py`
- Create: `tests/test_batch_4_system_commands.py`

- [ ] **Step 1: 创建测试文件并写 CliConsole.receive() 的第一批测试**

```python
# tests/test_batch_4_system_commands.py

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from harness.runtime.types import (
    CommandTalk, CommandKill, CommandListAgents,
    CommandEndWorkflow, CommandExit, CommandTalkDirect,
    CommandError,
)


# ── Helper: 模拟 stdin.readline ──

class _MockStdin:
    """可预设 readline 返回值的模拟 stdin。"""
    def __init__(self, lines: list[str]):
        self._lines = lines
        self._idx = 0

    def readline(self) -> str:
        if self._idx >= len(self._lines):
            return ""  # EOF
        line = self._lines[self._idx]
        self._idx += 1
        return line


async def _receive_lines(console, lines: list[str]):
    """辅助函数：用 mock stdin 驱动 console.receive()。"""
    mock_stdin = _MockStdin(lines)
    with patch('sys.stdin', mock_stdin):
        return await console.receive()


# ── Tests ──

@pytest.mark.asyncio
async def test_receive_eof_returns_command_exit():
    """EOF (readline 返回 "") → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    mock_stdin = _MockStdin([""])
    with patch('sys.stdin', mock_stdin):
        result = await console.receive()
    assert isinstance(result, CommandExit)


@pytest.mark.asyncio
async def test_receive_mode_a_plain_text_routes_to_root():
    """Mode A 纯文本 → CommandTalk(pid='root', text=...)"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["hello world\n"])
    assert isinstance(result, CommandTalk)
    assert result.pid == "root"
    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_receive_mode_b_plain_text_returns_command_error():
    """Mode B 纯文本 → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b")
    result = await _receive_lines(console, ["hello\n"])
    assert isinstance(result, CommandError)
    assert "纯文本" in result.error


@pytest.mark.asyncio
async def test_receive_mode_a_empty_input_ignored():
    """Mode A 空输入 → 忽略，继续 readline 直到得到有效输入"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["\n", "actual text\n"])
    assert isinstance(result, CommandTalk)
    assert result.text == "actual text"


@pytest.mark.asyncio
async def test_receive_mode_b_empty_input_running_returns_error():
    """Mode B 空输入 + agents 运行中 → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b", all_finished_hook=lambda: False)
    result = await _receive_lines(console, ["\n"])
    assert isinstance(result, CommandError)


@pytest.mark.asyncio
async def test_receive_mode_b_empty_input_all_finished_returns_exit():
    """Mode B 空输入 + 全部 FINISHED → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_b", all_finished_hook=lambda: True)
    result = await _receive_lines(console, ["\n"])
    assert isinstance(result, CommandExit)


@pytest.mark.asyncio
async def test_receive_agents_command():
    """/agents → CommandListAgents()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/agents\n"])
    assert isinstance(result, CommandListAgents)


@pytest.mark.asyncio
async def test_receive_agents_with_extra_arg_errors():
    """/agents extra → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/agents extra\n"])
    assert isinstance(result, CommandError)


@pytest.mark.asyncio
async def test_receive_exit_command():
    """/exit → CommandExit()"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/exit\n"])
    assert isinstance(result, CommandExit)


@pytest.mark.asyncio
async def test_receive_kill_command():
    """/kill collector → CommandKill(pid='collector')"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/kill collector\n"])
    assert isinstance(result, CommandKill)
    assert result.pid == "collector"


@pytest.mark.asyncio
async def test_receive_kill_missing_arg_errors():
    """/kill (缺参数) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/kill\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@pytest.mark.asyncio
async def test_receive_end_command():
    """/end wf_001 → CommandEndWorkflow(flag='wf_001')"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/end wf_001\n"])
    assert isinstance(result, CommandEndWorkflow)
    assert result.flag == "wf_001"


@pytest.mark.asyncio
async def test_receive_end_missing_arg_errors():
    """/end (缺参数) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/end\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@pytest.mark.asyncio
async def test_receive_talk_command():
    """/talk collector 请重新分析 → CommandTalkDirect"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector 请重新分析\n"])
    assert isinstance(result, CommandTalkDirect)
    assert result.pid == "collector"
    assert result.text == "请重新分析"


@pytest.mark.asyncio
async def test_receive_talk_missing_text_errors():
    """/talk collector (缺 text) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector\n"])
    assert isinstance(result, CommandError)
    assert "缺少消息文本" in result.error


@pytest.mark.asyncio
async def test_receive_talk_missing_all_args_errors():
    """/talk (缺所有参数) → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk\n"])
    assert isinstance(result, CommandError)
    assert "用法" in result.error


@pytest.mark.asyncio
async def test_receive_talk_with_spaces_in_text():
    """/talk collector  多空格  → text 保留所有空格"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/talk collector  多空格  \n"])
    assert isinstance(result, CommandTalkDirect)
    assert result.text == "多空格  "


@pytest.mark.asyncio
async def test_receive_unknown_command_errors():
    """/unknown xyz → CommandError"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    result = await _receive_lines(console, ["/unknown xyz\n"])
    assert isinstance(result, CommandError)
    assert "未知命令" in result.error
```

- [ ] **Step 2: 运行测试，验证全部失败**

Run: `pytest tests/test_batch_4_system_commands.py -v`
Expected: all 18 tests FAIL（CliConsole.receive() 尚未重写）

- [ ] **Step 3: 重写 CliConsole.receive() 方法**

将现有的 `async def receive(self)` 方法（cli_console.py 中当前只有简单 readline + 返回 CommandTalk）替换为：

```python
    async def receive(self) -> SystemCommand:
        """从 stdin 读取一行，解析为 SystemCommand。

        解析规则：
        1. EOF (readline 返回 "") → CommandExit()
        2. 以 "/" 开头 → 系统命令解析
        3. 空输入（仅回车）→ Mode A: 忽略; Mode B: 检查 all_finished
        4. 纯文本 → Mode A: CommandTalk to root; Mode B: CommandError
        """
        import sys

        while True:
            line = await asyncio.to_thread(sys.stdin.readline)

            # EOF
            if not line:
                return CommandExit()

            text = line.rstrip('\n')

            # 空输入
            if not text:
                if self._mode == "mode_a":
                    continue  # 忽略空白行
                else:
                    # Mode B: 检查是否所有 agent 已结束
                    if (self._all_finished_hook is not None
                            and self._all_finished_hook()):
                        return CommandExit()
                    else:
                        return CommandError(
                            command="",
                            error="纯文本需 /talk <pid> <text> 指定目标。"
                                  "输入 /agents 查看状态，/exit 退出",
                        )

            # 系统命令
            if text.startswith('/'):
                return self._parse_command(text)

            # 纯文本
            if self._mode == "mode_a":
                return CommandTalk(pid="root", text=text)
            else:
                return CommandError(
                    command=text,
                    error="纯文本需 /talk <pid> <text> 指定目标。"
                          "输入 /agents 查看状态，/exit 退出",
                )

    def _parse_command(self, text: str) -> SystemCommand:
        """解析 / 前缀命令字符串为 SystemCommand。"""
        parts = text.split(maxsplit=2)

        # /agents, /exit（无参数命令）
        if parts[0] in ('/agents', '/exit'):
            if len(parts) > 1:
                return CommandError(
                    command=text,
                    error=f"'{parts[0]}' 不接受额外参数",
                )
            if parts[0] == '/agents':
                return CommandListAgents()
            else:
                return CommandExit()

        # /kill <pid>
        if parts[0] == '/kill':
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /kill <pid>"
                )
            return CommandKill(pid=parts[1])

        # /end <flag>
        if parts[0] == '/end':
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /end <flag>"
                )
            return CommandEndWorkflow(flag=parts[1])

        # /talk <pid> <text>
        if parts[0] == '/talk':
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /talk <pid> <text>"
                )
            if len(parts) < 3:
                return CommandError(
                    command=text,
                    error="用法: /talk <pid> <text> (缺少消息文本)",
                )
            return CommandTalkDirect(pid=parts[1], text=parts[2])

        # 未知命令
        return CommandError(
            command=text, error=f"未知命令: '{parts[0]}'"
        )
```

**import 补充**：确保 `cli_console.py` 文件头部 import 了新增的类型。在现有 `from .types import (...)` 行中追加：`CommandKill, CommandListAgents, CommandEndWorkflow, CommandExit, CommandTalkDirect, CommandError, SystemCommand`。

- [ ] **Step 4: 运行测试，验证全部通过**

Run: `pytest tests/test_batch_4_system_commands.py -v`
Expected: all 18 tests PASS

- [ ] **Step 5: 提交**

```bash
git add harness/runtime/cli_console.py tests/test_batch_4_system_commands.py
git commit -m "feat(batch4): implement CliConsole.receive() command parsing

- Parse /agents /kill /end /exit /talk commands
- Mode A plain text routes to root agent
- Mode B plain text returns CommandError
- Empty input handling: ignore in Mode A, check all_finished in Mode B
- EOF returns CommandExit
- 18 unit tests covering all commands and edge cases
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: CliConsole.send() 新事件格式化

**Files:**
- Modify: `harness/runtime/cli_console.py`
- Modify: `tests/test_batch_4_system_commands.py` (追加测试)

- [ ] **Step 1: 在测试文件中追加 CliConsole.send() 的 5 条测试**

在 `test_batch_4_system_commands.py` 末尾追加：

```python
# ── CliConsole.send() tests ──

import io
from harness.runtime.types import (
    AgentsListed, CommandError, SystemMessage, AgentFinished,
    AgentSpawned, AgentOutput,
)


@pytest.mark.asyncio
async def test_send_agents_listed_multiple():
    """AgentsListed 含多个 agent → 格式化表格"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch('sys.stdout', buf):
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


@pytest.mark.asyncio
async def test_send_agents_listed_empty():
    """AgentsListed 空 → '没有运行中的 agent'"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch('sys.stdout', buf):
        await console.send(AgentsListed(agents={}))
    output = buf.getvalue()
    assert "没有运行中的 agent" in output


@pytest.mark.asyncio
async def test_send_command_error_with_command():
    """CommandError 含命令 → 显示错误 + 命令"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch('sys.stdout', buf):
        await console.send(CommandError(
            command="/kill ghost", error="pid 'ghost' 不存在"
        ))
    output = buf.getvalue()
    assert "错误" in output
    assert "/kill ghost" in output
    assert "ghost" in output


@pytest.mark.asyncio
async def test_send_command_error_no_command():
    """CommandError 无命令 → 仅显示错误"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch('sys.stdout', buf):
        await console.send(CommandError(
            command="", error="按 Enter 退出"
        ))
    output = buf.getvalue()
    assert "错误" in output
    assert "按 Enter 退出" in output


@pytest.mark.asyncio
async def test_send_system_message():
    """SystemMessage → 不带'错误'前缀的提示信息"""
    from harness.runtime.cli_console import CliConsole
    console = CliConsole(mode="mode_a")
    buf = io.StringIO()
    with patch('sys.stdout', buf):
        await console.send(SystemMessage(
            message="所有 agent 已完成。按 Enter 退出..."
        ))
    output = buf.getvalue()
    assert "[系统]" in output
    assert "所有 agent 已完成" in output
    assert "错误" not in output  # SystemMessage 不应显示"错误"
```

- [ ] **Step 2: 运行新测试，验证失败**

Run: `pytest tests/test_batch_4_system_commands.py::test_send_agents_listed_multiple tests/test_batch_4_system_commands.py::test_send_agents_listed_empty tests/test_batch_4_system_commands.py::test_send_command_error_with_command tests/test_batch_4_system_commands.py::test_send_command_error_no_command tests/test_batch_4_system_commands.py::test_send_system_message -v`
Expected: 5 FAIL (send() 尚未支持新类型)

- [ ] **Step 3: 在 CliConsole.send() 末尾追加新事件类型的格式化分支**

在 `send()` 方法的最后一个 `elif isinstance(event, WorkflowFinished):` 分支**之后**（return/方法结束之前）追加：

```python
        elif isinstance(event, AgentsListed):
            if not event.agents:
                print("[系统] 没有运行中的 agent")
            else:
                print(f"[系统] Agents ({len(event.agents)}):")
                print(
                    f"  {'PID':12} {'STATE':13} {'MODE':11} "
                    f"{'ROUNDS':7} {'PARENT'}"
                )
                print(
                    f"  {'-'*12} {'-'*13} {'-'*11} "
                    f"{'-'*7} {'-'*12}"
                )
                for pid, info in event.agents.items():
                    parent = info.get("parent") or "-"
                    state = info.get("state", "?")
                    mode = info.get("mode", "?")
                    rounds = str(info.get("rounds", "?"))
                    error_mark = " ⚡" if info.get("error") else ""
                    print(
                        f"  {pid:12} {state:13} {mode:11} "
                        f"{rounds:7} {parent:12}{error_mark}"
                    )

        elif isinstance(event, CommandError):
            print(f"[系统] 错误: {event.error}")
            if event.command:
                print(f"  命令: {event.command}")

        elif isinstance(event, SystemMessage):
            print(f"[系统] {event.message}")
```

**import 补充**：确保 cli_console.py 的 import 中包含 `AgentsListed, CommandError, SystemMessage`。

- [ ] **Step 4: 运行测试，验证通过**

Run: `pytest tests/test_batch_4_system_commands.py::test_send_agents_listed_multiple tests/test_batch_4_system_commands.py::test_send_agents_listed_empty tests/test_batch_4_system_commands.py::test_send_command_error_with_command tests/test_batch_4_system_commands.py::test_send_command_error_no_command tests/test_batch_4_system_commands.py::test_send_system_message -v`
Expected: all 5 PASS

- [ ] **Step 5: 提交**

```bash
git add harness/runtime/cli_console.py tests/test_batch_4_system_commands.py
git commit -m "feat(batch4): add AgentsListed, CommandError, SystemMessage formatting to CliConsole.send()

- AgentsListed: formatted table with PID/STATE/MODE/ROUNDS/PARENT columns
- CommandError: show error message and offending command
- SystemMessage: plain informational message without error prefix
- 5 tests
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Kernel._handle_system_input() 完整命令分发

**Files:**
- Modify: `harness/runtime/kernel.py`
- Modify: `tests/test_batch_4_system_commands.py` (追加测试)

- [ ] **Step 1: 在测试文件中追加 Kernel._handle_system_input() 的 14 条测试**

在 `test_batch_4_system_commands.py` 末尾追加：

```python
# ── Kernel._handle_system_input() tests ──

import asyncio
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.types import (
    CommandTalk, CommandKill, CommandListAgents,
    CommandEndWorkflow, CommandExit, CommandTalkDirect,
    CommandError, AgentsListed, AgentStateChanged, __EXIT_SENTINEL__,
)
from harness.interfaces.types import UserRequest


class MockConsole:
    """收集 send() 调用的 mock console。"""
    def __init__(self, commands=None):
        self.sent_events = []
        self._commands = commands or []
        self._cmd_idx = 0

    async def receive(self):
        if self._cmd_idx >= len(self._commands):
            # 返回 /exit 避免无限循环
            return CommandExit()
        cmd = self._commands[self._cmd_idx]
        self._cmd_idx += 1
        return cmd

    async def send(self, event):
        self.sent_events.append(event)


class MockAgentRuntime:
    """最小 mock AgentRuntime。"""
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
        self.started_at = 0.0
        self.last_output = ""

    def _idle_for_quiescence(self):
        return self.state == AgentState.RUNNING


class MockQueue:
    """收集 put_nowait 调用的 mock asyncio.Queue。"""
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


def _make_kernel_with_agents(console, agents: dict):
    """创建 Kernel 并注入 mock agents。

    agents: {pid: MockAgentRuntime}
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
    # Batch 3 MessageBus mock
    from unittest.mock import MagicMock
    kernel.message_bus = MagicMock()
    kernel.message_bus.get_subscribers_of.return_value = []
    kernel.message_bus.publish = MagicMock()
    kernel.message_bus.direct = MagicMock()
    kernel.message_bus.remove_publisher = MagicMock()
    kernel._pending_subscriptions = []

    # 保留 send_input 真实实现
    def _send_input(pid, request):
        if pid in kernel.input_queues:
            kernel.input_queues[pid].put_nowait(request)
    kernel.send_input = _send_input
    kernel.kill = lambda pid: Kernel._real_kill(kernel, pid)
    kernel.end_workflow = lambda flag: Kernel._real_end_workflow(kernel, flag)
    kernel.list_agents = lambda: {
        pid: {"state": r.state.value, "mode": r.mode,
              "parent": r.parent.pid if r.parent else None,
              "rounds": r.round_count, "error": r.error}
        for pid, r in agents.items()
    }

    # 注入真实的 kill / end_workflow
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


# Monkey-patch the real kill/end_workflow references for _handle_system_input
Kernel._real_kill = lambda self, pid: None
Kernel._real_end_workflow = lambda self, flag: []


@pytest.mark.asyncio
async def test_handle_system_input_command_talk_to_existing_pid():
    """CommandTalk 到存在的 pid → send_input 被调用"""
    console = MockConsole(commands=[
        CommandTalk(pid="root", text="hello"),
        CommandExit(),  # 第二个命令用于退出循环
    ])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"root": root})
    await kernel._handle_system_input()
    # root 的 input_queue 应收到 UserRequest
    queue = kernel.input_queues["root"]
    assert len(queue.items) == 1
    assert queue.items[0].text == "hello"


@pytest.mark.asyncio
async def test_handle_system_input_command_talk_to_nonexistent_pid():
    """CommandTalk 到不存在的 pid → CommandError"""
    console = MockConsole(commands=[
        CommandTalk(pid="ghost", text="hi"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    assert len(console.sent_events) >= 1
    error_event = console.sent_events[0]
    assert isinstance(error_event, CommandError)
    assert "ghost" in error_event.error


@pytest.mark.asyncio
async def test_handle_system_input_command_kill_existing_agent():
    """CommandKill 存在的 agent → should_exit=True, sentinel 入队"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    await kernel._handle_system_input()
    assert collector.should_exit is True
    # sentinel pushed
    assert kernel.input_queues["collector"].items[-1] is __EXIT_SENTINEL__
    # AgentStateChanged event sent
    state_events = [e for e in console.sent_events
                    if isinstance(e, AgentStateChanged)]
    assert len(state_events) == 1


@pytest.mark.asyncio
async def test_handle_system_input_command_kill_finished_agent():
    """CommandKill 已 FINISHED agent → 静默跳过，不推送 sentinel"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    # 清空 input_queue 以便观察
    kernel.input_queues["collector"].items.clear()
    await kernel._handle_system_input()
    assert collector.should_exit is False  # 未修改
    assert len(kernel.input_queues["collector"].items) == 0  # 无 sentinel


@pytest.mark.asyncio
async def test_handle_system_input_command_kill_nonexistent_pid():
    """CommandKill 不存在的 pid → CommandError"""
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


@pytest.mark.asyncio
async def test_handle_system_input_command_list_agents():
    """CommandListAgents → AgentsListed 事件"""
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


@pytest.mark.asyncio
async def test_handle_system_input_command_end_workflow_existing():
    """CommandEndWorkflow 存在的 flag → 对每个 agent kill + AgentStateChanged"""
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


@pytest.mark.asyncio
async def test_handle_system_input_command_end_workflow_nonexistent():
    """CommandEndWorkflow 不存在的 flag → CommandError"""
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


@pytest.mark.asyncio
async def test_handle_system_input_command_exit():
    """CommandExit → 全体 sentinel + _shutdown=True"""
    console = MockConsole(commands=[CommandExit()])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(
        console, {"root": root, "collector": collector}
    )
    await kernel._handle_system_input()
    # root (非 FINISHED) 收到 sentinel
    assert root.should_exit is True
    assert kernel.input_queues["root"].items[-1] is __EXIT_SENTINEL__
    # collector (已 FINISHED) 不受影响
    assert len(kernel.input_queues["collector"].items) == 0
    assert kernel._shutdown is True


@pytest.mark.asyncio
async def test_handle_system_input_command_talk_direct_existing():
    """CommandTalkDirect 存在且活跃 → send_input"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="analyzer", text="请重新分析"),
        CommandExit(),
    ])
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"analyzer": analyzer})
    await kernel._handle_system_input()
    queue = kernel.input_queues["analyzer"]
    assert len(queue.items) == 1
    assert queue.items[0].text == "请重新分析"


@pytest.mark.asyncio
async def test_handle_system_input_command_talk_direct_nonexistent():
    """CommandTalkDirect 不存在的 pid → CommandError"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="ghost", text="hi"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) >= 1


@pytest.mark.asyncio
async def test_handle_system_input_command_talk_direct_finished():
    """CommandTalkDirect 目标已 FINISHED → CommandError"""
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


@pytest.mark.asyncio
async def test_handle_system_input_command_error_passthrough():
    """CommandError（从 CliConsole 解析失败）→ 直接回显"""
    console = MockConsole(commands=[
        CommandError(command="/bad", error="未知命令: '/bad'"),
        CommandExit(),
    ])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    passthrough_events = [e for e in console.sent_events
                          if isinstance(e, CommandError)]
    assert len(passthrough_events) >= 1
```

- [ ] **Step 2: 运行新测试，验证失败**

Run: `pytest tests/test_batch_4_system_commands.py -k "handle_system_input" -v`
Expected: all 14 tests FAIL（`_handle_system_input` 尚未重写）

- [ ] **Step 3: 重写 Kernel._handle_system_input() 方法**

将现有的 `async def _handle_system_input(self)` 方法（kernel.py 中当前仅处理 CommandTalk 的 stub）替换为：

```python
    async def _handle_system_input(self) -> None:
        """系统输入处理循环。

        Batch 4 完整实现: 解析并分发全部 7 种 SystemCommand。
        """
        from .types import (
            CommandTalk, CommandKill, CommandListAgents,
            CommandEndWorkflow, CommandExit, CommandTalkDirect,
            CommandError, AgentsListed, AgentStateChanged,
            __EXIT_SENTINEL__,
        )
        from ..interfaces.types import UserRequest

        logger.info("_handle_system_input: started")
        while not self._shutdown:
            command = await self._console.receive()

            # ── CommandTalk: 纯文本路由 ──
            if isinstance(command, CommandTalk):
                if command.pid in self.runtime_table:
                    self.send_input(
                        command.pid,
                        UserRequest(text=command.text),
                    )
                else:
                    await self._console.send(CommandError(
                        command=command.text[:50],
                        error=f"pid '{command.pid}' 不存在",
                    ))

            # ── CommandKill: 终止单个 agent ──
            # 注意：AgentStateChanged 是乐观先行发出的——kill() 只设置
            # should_exit=True + 推送 sentinel，agent 的 state 要到其 run()
            # 进入 finally 块后才变成 TERMINATING。时间窗口取决于 agent 当
            # 前处于 receive() 等待（秒级）还是 LLM 调用中（数十秒）。
            elif isinstance(command, CommandKill):
                if command.pid in self.runtime_table:
                    agent = self.runtime_table[command.pid]
                    if agent.state == AgentState.FINISHED:
                        # 已 FINISHED：静默跳过（kill() 内部也会跳过，
                        # 但这里提前返回避免发送无意义的 AgentStateChanged）
                        logger.debug(
                            f"_handle_system_input: /kill '{command.pid}' "
                            f"already FINISHED, skipping"
                        )
                    else:
                        self.kill(command.pid)
                        await self._console.send(AgentStateChanged(
                            pid=command.pid,
                            old=agent.state.value,
                            new="terminating",
                        ))
                else:
                    await self._console.send(CommandError(
                        command=f"/kill {command.pid}",
                        error=f"pid '{command.pid}' 不存在",
                    ))

            # ── CommandListAgents: 列出所有 agent ──
            elif isinstance(command, CommandListAgents):
                info = self.list_agents()
                await self._console.send(AgentsListed(agents=info))

            # ── CommandEndWorkflow: 终止整个 workflow ──
            elif isinstance(command, CommandEndWorkflow):
                if command.flag in self.workflow_table:
                    killed = self.end_workflow(command.flag)
                    # 为每个被 kill 的 agent 推送状态变更事件
                    for pid in killed:
                        agent = self.runtime_table.get(pid)
                        if agent:
                            await self._console.send(AgentStateChanged(
                                pid=pid,
                                old=agent.state.value,
                                new="terminating",
                            ))
                else:
                    await self._console.send(CommandError(
                        command=f"/end {command.flag}",
                        error=f"workflow flag '{command.flag}' 不存在",
                    ))

            # ── CommandExit: 优雅退出 ──
            elif isinstance(command, CommandExit):
                logger.info("_handle_system_input: /exit received")
                for pid, agent in self.runtime_table.items():
                    if agent.state != AgentState.FINISHED:
                        agent.should_exit = True
                        if pid in self.input_queues:
                            self.input_queues[pid].put_nowait(
                                __EXIT_SENTINEL__
                            )
                self._shutdown = True
                return  # 退出循环

            # ── CommandTalkDirect: 定向消息（Mode B） ──
            elif isinstance(command, CommandTalkDirect):
                target = self.runtime_table.get(command.pid)
                if target is None:
                    await self._console.send(CommandError(
                        command=f"/talk {command.pid}",
                        error=f"pid '{command.pid}' 不存在",
                    ))
                elif target.state == AgentState.FINISHED:
                    await self._console.send(CommandError(
                        command=f"/talk {command.pid}",
                        error=f"Agent '{command.pid}' 已结束 (FINISHED)，"
                              f"无法接收消息",
                    ))
                else:
                    self.send_input(
                        command.pid,
                        UserRequest(text=command.text),
                    )

            # ── CommandError (由 CliConsole 解析失败产生) ──
            # 直接透传给 console.send() 显示给用户
            elif isinstance(command, CommandError):
                await self._console.send(command)

        logger.info("_handle_system_input: exited loop")
```

**import 补充**：确保 `kernel.py` 顶部 import 中包含重写中用到的类型。在现有 `from .types import (...)` 行中追加：`CommandKill, CommandListAgents, CommandEndWorkflow, CommandExit, CommandTalkDirect, CommandError, AgentsListed, AgentStateChanged`。同时追加 `from ..interfaces.types import UserRequest`（如果尚未 import）。

**注意**：`AgentState` 已经在 kernel.py 中被 import（在 `kill()` 等方法中使用）。如果重写中的 `AgentState.FINISHED` 引用报错，确认 import 存在。

- [ ] **Step 4: 运行测试，验证全部通过**

Run: `pytest tests/test_batch_4_system_commands.py -k "handle_system_input" -v`
Expected: all 14 tests PASS

- [ ] **Step 5: 提交**

```bash
git add harness/runtime/kernel.py tests/test_batch_4_system_commands.py
git commit -m "feat(batch4): implement Kernel._handle_system_input() command dispatch

- Handle all 7 SystemCommand types: CommandTalk, CommandKill,
  CommandListAgents, CommandEndWorkflow, CommandExit, CommandTalkDirect,
  CommandError (passthrough)
- CommandKill: optimistic AgentStateChanged before state actually changes
- CommandEndWorkflow: iterate killed pids for correct AgentStateChanged
- CommandExit: push sentinel to all non-FINISHED agents, set _shutdown
- 14 unit tests
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Runtime Mode B 结束行为修正 + 集成测试

**Files:**
- Modify: `harness/runtime/runtime.py`
- Modify: `tests/test_batch_4_system_commands.py` (追加集成测试)

- [ ] **Step 1: 修改 Runtime._run_from_script_async() 的 finally 块**

将 `runtime.py` 中 `_run_from_script_async` 方法的 finally 块从：

```python
        finally:
            # 取消系统输入处理（不再需要监听 stdin）
            task_sys.cancel()
            try:
                await task_sys
            except asyncio.CancelledError:
                pass
            ...
```

替换为：

```python
        finally:
            # 提示用户退出（用 SystemMessage 而非 CommandError——这是提示不是错误）
            from .types import SystemMessage
            await self._console.send(SystemMessage(
                message="所有 agent 已完成。按 Enter 退出..."
            ))
            # task_sys 仍在 readline 中阻塞——等用户按 Enter 后
            # _handle_system_input 中的 readline 返回 → while 循环
            # → CliConsole.receive() 检测到 all_finished → 返回 CommandExit()
            # → _handle_system_input 设 _shutdown=True → 退出循环
            try:
                await task_sys
            except asyncio.CancelledError:
                pass
            ...
```

**同时**，在 `spawn_from_script` 调用后新增 `set_all_finished_hook` 调用。在 `_run_from_script_async` 中 `self._kernel.spawn_from_script(script_path, parent=None)` 之后追加：

```python
        # 设置 CliConsole 的 all_finished 回调（Mode B 空输入退出用）
        if hasattr(self._console, 'set_all_finished_hook'):
            self._console.set_all_finished_hook(self._kernel.all_finished)
```

- [ ] **Step 2: 追加集成测试**

在 `test_batch_4_system_commands.py` 末尾追加：

```python
# ── Integration tests ──


@pytest.mark.asyncio
async def test_integration_command_flow_kill_agent():
    """Mode A: /kill collector → agent FINISHED"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.RUNNING)
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(
        console, {"root": root, "collector": collector}
    )
    await kernel._handle_system_input()
    # collector should be signalled
    assert collector.should_exit is True
    # AgentStateChanged sent
    state_events = [e for e in console.sent_events
                    if isinstance(e, AgentStateChanged)]
    assert any(e.pid == "collector" for e in state_events)


@pytest.mark.asyncio
async def test_integration_command_flow_list_then_kill():
    """/agents → /kill root → /exit"""
    console = MockConsole(commands=[
        CommandListAgents(),
        CommandKill(pid="root"),
        CommandExit(),
    ])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"root": root})
    await kernel._handle_system_input()
    # 第一步: AgentsListed 已发送
    listed = [e for e in console.sent_events if isinstance(e, AgentsListed)]
    assert len(listed) == 1
    # 第二步: root 被 kill
    assert root.should_exit is True
    # 第三步: _shutdown
    assert kernel._shutdown is True


@pytest.mark.asyncio
async def test_integration_kill_finished_agent_silent():
    """/kill 已 FINISHED agent → 静默，无 CommandError，无 sentinel"""
    console = MockConsole(commands=[
        CommandKill(pid="collector"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(console, {"collector": collector})
    kernel.input_queues["collector"].items.clear()
    await kernel._handle_system_input()
    # 无 sentinel
    assert len(kernel.input_queues["collector"].items) == 0
    # 无 CommandError（kill FINISHED agent 不算错误）
    error_events = [e for e in console.sent_events
                    if isinstance(e, CommandError)]
    assert len(error_events) == 0


@pytest.mark.asyncio
async def test_integration_end_workflow_all_finished():
    """/end 全部已 FINISHED workflow → 不推送 AgentStateChanged"""
    console = MockConsole(commands=[
        CommandEndWorkflow(flag="wf_root"),
        CommandExit(),
    ])
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.FINISHED)
    kernel = _make_kernel_with_agents(
        console, {"collector": collector, "analyzer": analyzer}
    )
    kernel.workflow_table["wf_root"] = ["collector", "analyzer"]
    await kernel._handle_system_input()
    # end_workflow 返回空列表，不推送 AgentStateChanged
    state_events = [e for e in console.sent_events
                    if isinstance(e, AgentStateChanged)]
    assert len(state_events) == 0


@pytest.mark.asyncio
async def test_integration_talk_to_agent_full_flow():
    """Mode B: /talk analyzer → analyzer 收到 UserRequest → /exit"""
    console = MockConsole(commands=[
        CommandTalkDirect(pid="analyzer", text="请重新分析"),
        CommandExit(),
    ])
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"analyzer": analyzer})
    await kernel._handle_system_input()
    # analyzer 收到消息
    queue = kernel.input_queues["analyzer"]
    assert len(queue.items) == 1
    assert isinstance(queue.items[0], UserRequest)
    assert queue.items[0].text == "请重新分析"
    # /exit 生效
    assert kernel._shutdown is True


@pytest.mark.asyncio
async def test_integration_mode_b_plain_text_rejected():
    """Mode B 纯文本 → CommandError，不路由到任何 agent"""
    received_commands = []

    class _RecordConsole(MockConsole):
        async def receive(self):
            # 先返回纯文本 CommandError (模拟 CliConsole Mode B 行为)
            # 再返回 /exit
            if not received_commands:
                received_commands.append(1)
                return CommandError(
                    command="hello",
                    error="纯文本需 /talk <pid> <text> 指定目标",
                )
            return CommandExit()

    console = _RecordConsole(commands=[])
    kernel = _make_kernel_with_agents(console, {})
    await kernel._handle_system_input()
    # 透传的 CommandError 被回显
    passthrough = [e for e in console.sent_events
                   if isinstance(e, CommandError)]
    assert len(passthrough) >= 1


@pytest.mark.asyncio
async def test_integration_empty_input_mode_a_skipped():
    """Mode A 空输入被 CliConsole 忽略，不产生任何 SystemCommand"""
    # 此测试在 CliConsole.receive() 层已验证（test_receive_mode_a_empty_input_ignored）
    # 这里验证 Kernel 侧：如果 CliConsole 确实不返回空输入的命令，
    # 则 send_input 不会被无效调用触发。
    console = MockConsole(commands=[CommandExit()])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    kernel = _make_kernel_with_agents(console, {"root": root})
    # 清空 input_queue
    kernel.input_queues["root"].items.clear()
    await kernel._handle_system_input()
    # root 的 input_queue 应保持空（没有因空输入而产生的消息）
    assert len(kernel.input_queues["root"].items) == 0


@pytest.mark.asyncio
async def test_integration_exit_pushes_sentinel_to_all_non_finished():
    """/exit → 所有非 FINISHED agent 收到 sentinel，已 FINISHED 的跳过"""
    console = MockConsole(commands=[CommandExit()])
    root = MockAgentRuntime(pid="root", state=AgentState.RUNNING)
    collector = MockAgentRuntime(pid="collector", state=AgentState.FINISHED)
    analyzer = MockAgentRuntime(pid="analyzer", state=AgentState.TERMINATING)
    kernel = _make_kernel_with_agents(
        console, {"root": root, "collector": collector, "analyzer": analyzer}
    )
    # 清空所有 queue
    for q in kernel.input_queues.values():
        q.items.clear()
    await kernel._handle_system_input()
    # root: sentinel 入队
    assert kernel.input_queues["root"].items[-1] is __EXIT_SENTINEL__
    # collector: 已 FINISHED，不受影响
    assert len(kernel.input_queues["collector"].items) == 0
    # analyzer: TERMINATING 但 spec 说 "state != FINISHED"——需要修正：
    # CommandExit 中应为 "agent.state not in (AgentState.FINISHED, AgentState.TERMINATING)"
    # 但当前伪代码为 "agent.state != AgentState.FINISHED"
    # TERMINATING agent 也会收到 sentinel（幂等，不影响正确性）
    assert kernel._shutdown is True
```

- [ ] **Step 3: 修复 CommandExit 中对 TERMINATING agent 的处理**

在 kernel.py 的 `_handle_system_input` → `CommandExit` 分支中，将条件从 `agent.state != AgentState.FINISHED` 改为 `agent.state not in (AgentState.FINISHED, AgentState.TERMINATING)`：

```python
            elif isinstance(command, CommandExit):
                logger.info("_handle_system_input: /exit received")
                for pid, agent in self.runtime_table.items():
                    if agent.state not in (
                        AgentState.FINISHED, AgentState.TERMINATING
                    ):
                        agent.should_exit = True
                        if pid in self.input_queues:
                            self.input_queues[pid].put_nowait(
                                __EXIT_SENTINEL__
                            )
                self._shutdown = True
                return
```

- [ ] **Step 4: 运行全部测试，验证通过**

Run: `pytest tests/test_batch_4_system_commands.py -v`
Expected: all tests PASS (18 receive + 5 send + 14 kernel + 8 integration = 45 tests)

- [ ] **Step 5: 提交**

```bash
git add harness/runtime/runtime.py harness/runtime/kernel.py tests/test_batch_4_system_commands.py
git commit -m "feat(batch4): fix Mode B exit + add integration tests

- Replace task_sys.cancel() with honest SystemMessage prompt
- Set CliConsole.all_finished_hook via public set_all_finished_hook()
- CommandExit: skip TERMINATING agents in addition to FINISHED
- 8 integration tests covering full command flows
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: `__init__.py` re-export + 回归测试

**Files:**
- Modify: `harness/runtime/__init__.py`
- (No test file changes needed)

- [ ] **Step 1: 更新 `harness/runtime/__init__.py` 的 re-export**

在现有 re-export 列表末尾追加 Batch 4 新增的类型：

```python
# harness/runtime/__init__.py — 追加在现有 re-export 之后

from .types import (
    CommandKill,
    CommandListAgents,
    CommandEndWorkflow,
    CommandExit,
    CommandTalkDirect,
    CommandError,
    AgentsListed,
    SystemMessage,
    SystemCommand,
)
```

- [ ] **Step 2: 验证 import 路径**

Run:
```bash
python -c "
from harness.runtime import (
    CommandKill, CommandListAgents, CommandEndWorkflow,
    CommandExit, CommandTalkDirect,
    AgentsListed, CommandError, SystemMessage, SystemCommand,
)
print('OK')
"
```
Expected: `OK`

- [ ] **Step 3: 运行全部现有测试，确保无回归**

Run: `pytest tests/ -v --ignore=tests/test_batch_4_system_commands.py -x`
Expected: all existing tests PASS

- [ ] **Step 4: 运行 Batch 4 全套新测试**

Run: `pytest tests/test_batch_4_system_commands.py -v`
Expected: 45 tests PASS

- [ ] **Step 5: 运行全量测试**

Run: `pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 6: 提交**

```bash
git add harness/runtime/__init__.py
git commit -m "chore(batch4): add new types to runtime __init__.py re-exports

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

- [ ] Spec Section 4.1 (SystemCommand types) → Task 1
- [ ] Spec Section 4.2 (SystemEvent types) → Task 1
- [ ] Spec Section 4.3 (CliConsole.receive()) → Task 3
- [ ] Spec Section 4.4 (Kernel._handle_system_input()) → Task 5
- [ ] Spec Section 4.5 (CliConsole.send()) → Task 4
- [ ] Spec Section 4.6 (Mode B exit fix) → Task 6
- [ ] Spec Section 4.7 (CliConsole.__init__ signature) → Task 2
- [ ] Spec Section 4.8 (Runtime mode info passing) → Task 6
- [ ] Spec Section 6 (Error handling) → Tasks 3, 5, 6
- [ ] Spec Section 7 (Test strategy) → Tasks 3, 4, 5, 6
- [ ] AC-4.1 → AC-4.9 (Acceptance criteria) → Tasks 1-7
