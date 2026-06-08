"""Runtime 层共享类型。

定义 InternalMessage（MessageBus 内部消息格式）、__EXIT_SENTINEL__
（asyncio.Queue 哨兵）和 AgentOutput（临时降级通知类型）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# InternalMessage — MessageBus 内部投递单元
# ---------------------------------------------------------------------------


@dataclass
class InternalMessage:
    """MessageBus 内部消息格式。

    与 UserRequest 的区别：
    - UserRequest 是 LLM 看到的"用户输入"，经 ContextAssembler 组装进 messages
    - InternalMessage 是 MessageBus 内部的投递单元，
      经 KernelBridgeAdapter.receive() 转换为 UserRequest

    Attributes:
        from_pid: 消息来源 agent 的 pid。
        content: 消息文本内容（TextEvent.content 或 "" 表示 StopEvent）。
        metadata: 扩展元数据。StopEvent 时含 ``{"stop": True}``。
        created_at: 消息创建时间戳。
    """

    from_pid: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# __EXIT_SENTINEL__ — asyncio.Queue 退出哨兵
# ---------------------------------------------------------------------------

# 模块级 sentinel 对象，使用身份比较（is）识别。
# 放入 input_queue 后告知 agent 应退出。
__EXIT_SENTINEL__ = object()


# ---------------------------------------------------------------------------
# ── SystemCommand（Batch 1 最小版本）───────────────────────


@dataclass
class CommandTalk:
    """纯文本输入，路由到指定 agent。

    Attributes:
        pid: 目标 agent 的标识（Mode A 下固定为 "root"）。
        text: 用户输入的原始文本。
    """
    pid: str
    text: str


# Batch 1 仅 CommandTalk 一个命令类型。
# Batch 4 追加: CommandKill, CommandListAgents, CommandEndWorkflow,
# CommandExit, CommandTalkDirect
SystemCommand = CommandTalk


# ── SystemEvent（Batch 1 最小版本）─────────────────────────


@dataclass
class AgentSpawned:
    """agent spawn 完成通知。

    Attributes:
        pid: 新创建 agent 的标识。
        parent: 父 agent 的 pid，None 表示顶层 agent。
    """
    pid: str
    parent: str | None = None


@dataclass
class AgentStateChanged:
    """agent 状态变更通知。

    Attributes:
        pid: agent 标识。
        old: 变更前状态。
        new: 变更后状态。
    """
    pid: str
    old: str
    new: str


@dataclass
class AgentFinished:
    """agent 进入 FINISHED 通知。

    Attributes:
        pid: agent 标识。
        result: last_output 最终输出文本。
        duration: 执行耗时（秒）。
        error: 异常信息，None 表示正常退出。
    """
    pid: str
    result: str = ""
    duration: float = 0.0
    error: str | None = None


@dataclass
class RuntimeStarted:
    """Runtime 启动完成。"""
    pass


@dataclass
class RuntimeStopped:
    """Runtime 所有 agent 已结束。"""
    pass


@dataclass
class AgentOutput:
    """agent 的 TextEvent 无订阅者时的降级通知。

    Batch 1 正式化：从临时类型升级为正式 SystemEvent 子类型。

    Attributes:
        pid: 发送该输出的 agent pid。
        content: 输出文本内容。
    """

    pid: str = ""
    content: str = ""


# Union 类型别名，用于 SystemConsole.send() 签名
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
)
