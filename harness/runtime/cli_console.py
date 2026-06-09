"""CliConsole — SystemConsole 默认 CLI 实现。

receive(): asyncio.to_thread(sys.stdin.readline)
send(): 按事件类型格式化输出到 stdout
"""

from __future__ import annotations

import asyncio
import sys
from typing import Callable

from .types import (
    AgentFinished,
    AgentOutput,
    AgentSpawned,
    AgentStateChanged,
    CommandTalk,
    CommandKill,
    CommandListAgents,
    CommandEndWorkflow,
    CommandExit,
    CommandTalkDirect,
    CommandError,
    RuntimeStarted,
    RuntimeStopped,
    WorkflowFinished,
    SystemCommand,
    SystemEvent,
)


class CliConsole:
    """SystemConsole 默认 CLI 实现。

    receive() 在后台线程读取 stdin，不阻塞 event loop。
    send() 将系统事件格式化为人类可读的文本输出到 stdout。

    Batch 4: 支持 / 前缀命令解析，Mode A/B 纯文本路由。
    """

    def __init__(
        self,
        mode: str = "mode_a",
        all_finished_hook: Callable[[], bool] | None = None,
    ):
        """初始化 CliConsole。

        Args:
            mode: "mode_a"（纯文本路由到 root）或 "mode_b"
                  （纯文本需 /talk 定向）。
            all_finished_hook: Mode B 下用于判断所有 agent 是否已结束。
        """
        self._mode = mode
        self._all_finished_hook = all_finished_hook

    def set_all_finished_hook(self, hook: Callable[[], bool]) -> None:
        """设置 all_finished 查询回调（Mode B 下用于判断是否全部完成）。

        通过公开方法注入，而非直接修改 _all_finished_hook 属性。
        """
        self._all_finished_hook = hook

    async def receive(self) -> SystemCommand:
        """从 stdin 读取一行，解析为 SystemCommand。

        解析规则：
        1. EOF (readline 返回 "") → CommandExit()
        2. 以 "/" 开头 → 系统命令解析
        3. 空输入（仅回车）→ Mode A: 忽略; Mode B: 检查 all_finished
        4. 纯文本 → Mode A: CommandTalk to root; Mode B: CommandError
        """
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)

            # EOF
            if not line:
                return CommandExit()

            text = line.rstrip("\n")

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
            if text.startswith("/"):
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
        if parts[0] in ("/agents", "/exit"):
            if len(parts) > 1:
                return CommandError(
                    command=text,
                    error=f"'{parts[0]}' 不接受额外参数",
                )
            if parts[0] == "/agents":
                return CommandListAgents()
            else:
                return CommandExit()

        # /kill <pid>
        if parts[0] == "/kill":
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /kill <pid>"
                )
            return CommandKill(pid=parts[1])

        # /end <flag>
        if parts[0] == "/end":
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /end <flag>"
                )
            return CommandEndWorkflow(flag=parts[1])

        # /talk <pid> <text>
        if parts[0] == "/talk":
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

    async def send(self, event: SystemEvent) -> None:
        """按事件类型格式化输出到 stdout。

        Args:
            event: 系统事件。
        """
        if isinstance(event, AgentSpawned):
            parent_info = f", parent={event.parent}" if event.parent else ""
            print(f"[系统] Agent spawned: {event.pid}{parent_info}")

        elif isinstance(event, AgentFinished):
            status = "异常退出" if event.error else "正常完成"
            print(
                f"[系统] Agent finished: {event.pid} "
                f"({event.duration:.1f}s, {status})"
            )

        elif isinstance(event, AgentOutput):
            print(f"[{event.pid}] {event.content}")

        elif isinstance(event, AgentStateChanged):
            print(
                f"[系统] Agent {event.pid}: "
                f"{event.old} → {event.new}"
            )

        elif isinstance(event, RuntimeStarted):
            print("[系统] Runtime 启动")

        elif isinstance(event, RuntimeStopped):
            print("[系统] Runtime 停止")

        elif isinstance(event, WorkflowFinished):
            print(f"[系统] Workflow {event.workflow_flag} 完成:")
            for agent in event.agents:
                status = "异常" if agent.get("error") else "正常"
                print(
                    f"  {agent['pid']:12} {status}  "
                    f"{agent['rounds']}轮  {agent['duration']:.1f}s"
                )
                output = agent.get("output", "")
                if output:
                    truncated = output[:200]
                    if len(output) > 200:
                        truncated += "..."
                    print(f"    → {truncated}")
