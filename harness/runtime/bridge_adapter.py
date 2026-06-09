"""KernelBridgeAdapter — Runtime 路径下 agent 的 I/O 通道。

实现 AsyncInputAdapter，对接 Kernel 的 input_queue 和 MessageBus。

Batch 0 初版：
- receive() 从 input_queues[pid] 取消息，转换三种入队类型
- send() 内联降级路由（无 MessageBus）：TextEvent → SystemConsole stdout，
  StopEvent → 丢弃，target 非空 → 直接入队
- 退出保护：should_exit 为 True 时静默丢弃所有输出
- 事件类型过滤：仅 TextEvent/StopEvent 参与路由，
  中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole

Batch 3（已实现）：
- target=None → message_bus.publish(from_pid, event, on_no_subscriber=...)
- target=pid → message_bus.direct(target, InternalMessage(...))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..interfaces.async_input_adapter import AsyncInputAdapter
from ..interfaces.types import (
    StopEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserRequest,
)
from .types import AgentOutput, InternalMessage, __EXIT_SENTINEL__

if TYPE_CHECKING:
    from .agent_runtime import AgentRuntime
    from .kernel import Kernel


class KernelBridgeAdapter:
    """实现 AsyncInputAdapter，对接 Kernel 的消息队列。

    每个 AgentRuntime 拥有一个 KBA 实例。
    它是 agent 对外部世界的唯一 I/O 通道。
    """

    def __init__(self, pid: str, kernel: Kernel, runtime: AgentRuntime):
        """初始化 KBA。

        Args:
            pid: 所属 agent 的标识。
            kernel: Kernel 全局单例引用。
            runtime: 所属 AgentRuntime 引用（用于 should_exit 检查）。
        """
        self._pid = pid
        self._kernel = kernel
        self._runtime = runtime

    # ------------------------------------------------------------------
    # AsyncInputAdapter 实现
    # ------------------------------------------------------------------

    async def receive(self) -> UserRequest:
        """从 input_queue 取消息并转换为 UserRequest。

        转换规则：
        - UserRequest → 原样返回
        - InternalMessage → UserRequest(text=msg.content, metadata={from: msg.from_pid, ...})
        - __EXIT_SENTINEL__ → UserRequest(text="", metadata={"exit": True})
        """
        item = await self._kernel.input_queues[self._pid].get()

        if item is __EXIT_SENTINEL__:
            return UserRequest(text="", metadata={"exit": True})
        elif isinstance(item, InternalMessage):
            return UserRequest(
                text=item.content,
                metadata={**item.metadata, "from": item.from_pid},
            )
        elif isinstance(item, UserRequest):
            return item
        else:
            # 防御：未知类型视为纯文本
            return UserRequest(text=str(item))

    async def send(self, event, target=None):
        """推送事件。

        路径分流：
        1. 退出保护：should_exit 为 True → 静默丢弃
        2. 事件类型过滤：非 TextEvent/StopEvent → 降级到 SystemConsole
        3. 定向投递：target 非空 → MessageBus.direct()
        4. 广播：target=None → MessageBus.publish()
        """
        if self._runtime.should_exit:
            return  # 退出保护：丢弃"最后一轮污染"

        # ── 事件类型分流 ──
        # 仅 TextEvent/StopEvent 参与 MessageBus 路由；
        # 中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole
        if not isinstance(event, (TextEvent, StopEvent)):
            if isinstance(event, (ThinkingEvent, ToolCallEvent, ToolResultEvent)):
                await self._kernel._console.send(
                    AgentOutput(
                        pid=self._pid,
                        content=f"[{type(event).__name__}] {event}",
                    )
                )
            return

        if target is not None:
            # 定向投递：走 MessageBus.direct()
            msg = InternalMessage(
                from_pid=self._pid,
                content=event.content if isinstance(event, TextEvent) else "",
                metadata={"stop": True} if isinstance(event, StopEvent) else {},
            )
            self._kernel.message_bus.direct(target, msg)
            # TextEvent 也同步输出到终端
            if isinstance(event, TextEvent):
                await self._kernel._console.send(
                    AgentOutput(pid=self._pid, content=event.content)
                )
        else:
            # pub-sub 路由：TextEvent 在 MessageBus 内部始终输出到终端。
            # on_no_subscriber 仅作为 MessageBus console 为 None 时的兜底。
            await self._kernel.message_bus.publish(
                from_pid=self._pid,
                event=event,
                on_no_subscriber=(
                    self._kernel._console.send
                    if isinstance(event, TextEvent) else None
                ),
            )
