"""Harness Agent Template — Runtime 层。

提供多 Agent Runtime 的生命周期管理、消息通信与 Workflow 编排能力。

Batch 1: Runtime + Kernel + AgentRuntime 骨架（单 agent Mode A）。
Batch 2: Workflow 脚本加载（@agent / subscribe / spawn_from_script）。
Batch 3: MessageBus + 订阅 + 并发 + 终止（完整多 agent）。
Batch 4: 系统命令解析 + 信号处理完善。
"""

from .agent_runtime import AgentRuntime, AgentState
from .bridge_adapter import KernelBridgeAdapter
from .cli_console import CliConsole
from .kernel import Kernel
from .runtime import Runtime
from .signals import create_sigint_handler
from .types import (
    AgentFinished,
    AgentOutput,
    AgentSpawned,
    AgentStateChanged,
    CommandTalk,
    InternalMessage,
    RuntimeStarted,
    RuntimeStopped,
    SystemCommand,
    SystemEvent,
    __EXIT_SENTINEL__,
)

__all__ = [
    # Runtime 入口
    "Runtime",
    "Kernel",
    "AgentRuntime",
    "AgentState",
    # I/O
    "KernelBridgeAdapter",
    "CliConsole",
    # 信号
    "create_sigint_handler",
    # 类型
    "InternalMessage",
    "__EXIT_SENTINEL__",
    "AgentOutput",
    # SystemCommand
    "CommandTalk",
    "SystemCommand",
    # SystemEvent
    "AgentSpawned",
    "AgentStateChanged",
    "AgentFinished",
    "RuntimeStarted",
    "RuntimeStopped",
    "SystemEvent",
]
