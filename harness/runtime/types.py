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


# ── Batch 4 新增 SystemCommand 类型 ──


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


@dataclass
class CommandError:
    """系统命令执行失败。

    双重身份：SystemCommand（CliConsole 解析失败时产生）和
    SystemEvent（Kernel 执行失败时产生）。
    """
    command: str = ""
    error: str = ""


# ── SystemCommand union 更新 ──
# 注意：CommandError 也在 union 中——CliConsole.receive() 解析失败时
# 返回 CommandError 作为"命令"，Kernel 收到后透传给 console.send() 显示。
SystemCommand = (
    CommandTalk | CommandKill | CommandListAgents
    | CommandEndWorkflow | CommandExit | CommandTalkDirect
    | CommandError
)


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


@dataclass
class WorkflowFinished:
    """Mode B: 所有 agent FINISHED，workflow 执行完成。

    run_from_script 在返回前推送此事件到 SystemConsole。

    Attributes:
        workflow_flag: workflow 标识（如 "wf_001"）。
        agents: agent 结果列表，每项含 pid/output/error/rounds/duration。
    """
    workflow_flag: str = ""
    agents: list = field(default_factory=list)


# ── Batch 4 新增 SystemEvent 类型 ──


@dataclass
class AgentsListed:
    """/agents 响应 — agent 状态快照。"""
    agents: dict = field(default_factory=dict)


@dataclass
class SystemMessage:
    """系统信息提示（非错误）。

    区别于 CommandError：不应以"[系统] 错误:" 前缀显示。
    """
    message: str = ""


# Union 类型别名，用于 SystemConsole.send() 签名
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
    | WorkflowFinished  # Batch 3 新增
    | AgentsListed     # Batch 4
    | CommandError     # Batch 4
    | SystemMessage    # Batch 4
)
