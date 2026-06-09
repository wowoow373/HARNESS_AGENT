"""MessageBus — pub-sub 路由表 + 消息投递。

做机制不做策略。
- 维护 publisher → subscribers 映射
- publish() 时查表路由到 input_queues
- direct() 时跳过订阅表直接投递
- 提供 get_subscribers_of() 供 Kernel 级联终止查询
"""

from __future__ import annotations

import logging
from typing import Any

from ..interfaces.types import StopEvent, TextEvent

logger = logging.getLogger(__name__)


class MessageBus:
    """pub-sub 路由表 + 消息投递。

    职责边界：
    - 维护 publisher → subscribers 映射
    - publish() 时查表路由到 input_queues
    - direct() 时跳过订阅表直接投递
    - 提供 get_subscribers_of() 供 Kernel 级联终止查询
    - 不负责退出保护（在 KernelBridgeAdapter 层）
    - 不负责 sentinel 推送（在 Kernel 层）
    """

    def __init__(self, input_queues: dict[str, Any], console: Any = None):
        """初始化 MessageBus。

        Args:
            input_queues: Kernel 维护的 per-agent 输入队列
                          dict[str, asyncio.Queue]。
                          MessageBus 持有引用以投递消息。
            console: SystemConsole 引用，用于 publish() 内部 fallback 降级。
                     可选；为 None 时降级回调由调用方通过
                     on_no_subscriber 参数注入。
        """
        self._input_queues = input_queues
        self._console = console

        # publisher_pid → {subscriber_pid, ...}
        self._subscriptions: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def subscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """建立订阅：subscriber 接收 publisher 的每轮 TextEvent/StopEvent。

        纯内存操作（dict/set），同步方法。幂等——重复订阅同一对无副作用。

        Raises:
            ValueError: subscriber_pid == publisher_pid（不允许自订阅）
        """
        if subscriber_pid == publisher_pid:
            raise ValueError(
                f"Self-subscription not allowed: "
                f"'{subscriber_pid}' cannot subscribe to itself."
            )

        if publisher_pid not in self._subscriptions:
            self._subscriptions[publisher_pid] = set()

        self._subscriptions[publisher_pid].add(subscriber_pid)
        logger.debug(
            f"subscribe: '{subscriber_pid}' → '{publisher_pid}'"
        )

    async def publish(
        self,
        from_pid: str,
        event: Any,
        on_no_subscriber: Any = None,
    ) -> None:
        """向 from_pid 的所有订阅者广播 event。

        async 因为有降级路径需要 await on_no_subscriber。

        可路由事件类型：仅 TextEvent 和 StopEvent。调用方
        （KernelBridgeAdapter.send）在调用 publish 前过滤中间事件。

        无订阅者时：
        - event is TextEvent → 调用 on_no_subscriber；若为 None，
          尝试 self._console.send（内部 fallback）；都不可用则静默丢弃
        - event is StopEvent → 静默丢弃（不调用 on_no_subscriber）

        订阅者已 FINISHED（其 input_queue 已从 input_queues 中移除）→ 跳过
        """
        from .types import AgentOutput, InternalMessage

        subscribers = self._subscriptions.get(from_pid, set())

        # 过滤：只投递给 input_queues 中仍存在的订阅者
        active_subscribers = {
            pid for pid in subscribers
            if pid in self._input_queues
        }

        # TextEvent 始终输出到终端（无论有无订阅者），
        # 用户可以实时看到 agent 间的通信流。
        if isinstance(event, TextEvent):
            if self._console is not None:
                await self._console.send(
                    AgentOutput(pid=from_pid, content=event.content)
                )
            elif on_no_subscriber is not None:
                await on_no_subscriber(
                    AgentOutput(pid=from_pid, content=event.content)
                )

        if not active_subscribers:
            # StopEvent + 无订阅者 → 静默丢弃
            return

        # 构造内部消息
        msg = InternalMessage(
            from_pid=from_pid,
            content=event.content if isinstance(event, TextEvent) else "",
            metadata={"stop": True} if isinstance(event, StopEvent) else {},
        )

        # 广播到所有活跃订阅者
        for sub_pid in active_subscribers:
            self._input_queues[sub_pid].put_nowait(msg)
            logger.debug(f"publish: '{from_pid}' → '{sub_pid}'")

    def direct(self, target_pid: str, message: Any) -> None:
        """定向投递：跳过订阅表，直接投递到 target_pid 的队列。

        message 是由调用方（KernelBridgeAdapter）构造的 InternalMessage
        实例。direct() 直接入队，不做重新包装。

        纯 dict 查找 + asyncio.Queue.put_nowait，同步方法。

        Raises:
            KeyError: target_pid 不在 input_queues 中
        """
        if target_pid not in self._input_queues:
            raise KeyError(
                f"target_pid '{target_pid}' not found in input_queues"
            )

        self._input_queues[target_pid].put_nowait(message)
        logger.debug(f"direct: → '{target_pid}'")

    def get_subscribers_of(self, publisher_pid: str) -> list[str]:
        """返回订阅了 publisher_pid 的所有 pid 列表。

        无订阅者返回空列表。纯查询，无副作用。
        """
        return list(self._subscriptions.get(publisher_pid, set()))

    def unsubscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """取消单个订阅关系。

        与 remove_publisher() 的区别：
        - unsubscribe("A", "B")：仅移除 A→B 这一条订阅关系
        - remove_publisher("B")：移除所有指向 B 的订阅关系

        如果订阅关系不存在，静默返回（幂等）。
        """
        if publisher_pid in self._subscriptions:
            self._subscriptions[publisher_pid].discard(subscriber_pid)
            if not self._subscriptions[publisher_pid]:
                del self._subscriptions[publisher_pid]
            logger.debug(
                f"unsubscribe: '{subscriber_pid}' × '{publisher_pid}'"
            )

    def remove_publisher(self, publisher_pid: str) -> None:
        """移除 publisher 的所有订阅关系。

        publisher FINISHED 时由 Kernel._on_agent_finished 调用。
        """
        removed = self._subscriptions.pop(publisher_pid, None)
        if removed:
            logger.debug(
                f"remove_publisher: '{publisher_pid}' "
                f"(removed {len(removed)} subscriber(s))"
            )
