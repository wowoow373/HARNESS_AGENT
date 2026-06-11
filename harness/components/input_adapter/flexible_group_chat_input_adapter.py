"""FlexibleGroupChatInputAdapter — 带缓冲窗口和随机触发的输入适配器。

群聊场景的核心"节奏控制器"。每个 Agent 独立拥有一份实例，通过不同的
min_wait / max_wait / jitter 参数实现差异化的响应节奏。

职责：
- 从 Kernel 的 input_queues[pid] 中拉取消息（通过内部 KBA）
- 维护一个短期消息缓冲窗口
- 在合适的时机把缓冲中的消息打包成 UserRequest
- 决定 Agent 何时"思考"和"开口"

触发逻辑：
1. 阻塞等待第一条消息到达
2. 收到第一条消息后，启动计时器：
   min_deadline = now + min_wait + random_jitter
   max_deadline = now + max_wait + random_jitter
3. 持续非阻塞接收新消息，追加到缓冲，不重置计时器
4. 检查：
   - 若缓冲非空且 now >= min_deadline → 触发，返回 UserRequest
   - 若缓冲非空且 now >= max_deadline → 强制触发
   - 若缓冲为空 → 继续等待（即使 max_deadline 到了也不触发）

配置参数（放在 @agent 的 metadata 中）：
- min_wait: 最短等待时间（秒），默认 1.0
- max_wait: 最长等待时间（秒），默认 5.0
- jitter: 随机抖动上限（秒），默认 0.5

数据契约：
- 返回的 UserRequest.text 为兜底摘要
- 真正的结构化数据放在 UserRequest.metadata["buffered"] 中
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ...interfaces.async_input_adapter import AsyncInputAdapter
from ...interfaces.types import TextEvent, UserRequest
from ...runtime.types import InternalMessage, __EXIT_SENTINEL__

if TYPE_CHECKING:
    from ...runtime.agent_runtime import AgentRuntime
    from ...runtime.kernel import Kernel

logger = logging.getLogger(__name__)


class FlexibleGroupChatInputAdapter:
    """带缓冲窗口和随机触发的异步输入适配器。

    实现 AsyncInputAdapter 协议。内部持有 KernelBridgeAdapter，
    在 _inject_kernel_context 被调用后完成初始化。

    Usage in workflow script::

        adapter = FlexibleGroupChatInputAdapter(min_wait=0.8, max_wait=3.0)
        # 注册到 DI 容器
        container.register(AsyncInputAdapter, adapter)
        # Kernel 在 spawn 时调用 adapter._inject_kernel_context(pid, kernel, runtime)
    """

    def __init__(
        self,
        min_wait: float = 1.0,
        max_wait: float = 5.0,
        jitter: float = 0.5,
        user_name: str = "用户",
    ):
        """初始化 FlexibleGroupChatInputAdapter。

        Args:
            min_wait: 最短等待时间（秒）。收到第一条消息后至少等这么久。
            max_wait: 最长等待时间（秒）。超过这个时间强制触发。
            jitter: 随机抖动上限（秒）。在 min/max 上额外添加 random(0, jitter)。
            user_name: 真实用户在群聊中的显示名称。
        """
        self._min_wait = min_wait
        self._max_wait = max_wait
        self._jitter = jitter
        self._user_name = user_name

        # Kernel context — injected via _inject_kernel_context
        self._pid: Optional[str] = None
        self._kernel: Optional['Kernel'] = None
        self._runtime: Optional['AgentRuntime'] = None

        # Internal KernelBridgeAdapter — created after injection
        self._kba: Any = None

        # Buffering state
        self._buffer: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Kernel context injection (called by _resolve_adapter)
    # ------------------------------------------------------------------

    def _inject_kernel_context(
        self, pid: str, kernel: 'Kernel', runtime: 'AgentRuntime'
    ) -> None:
        """Called by _resolve_adapter to inject kernel dependencies.

        Creates the internal KernelBridgeAdapter that handles actual
        queue I/O and MessageBus routing.
        """
        from ...runtime.bridge_adapter import KernelBridgeAdapter

        self._pid = pid
        self._kernel = kernel
        self._runtime = runtime
        self._kba = KernelBridgeAdapter(pid=pid, kernel=kernel, runtime=runtime)
        logger.debug(
            f"FlexibleGroupChatInputAdapter[{pid}]: kernel context injected, "
            f"min_wait={self._min_wait}s, max_wait={self._max_wait}s, "
            f"jitter={self._jitter}s"
        )

    # ------------------------------------------------------------------
    # AsyncInputAdapter implementation
    # ------------------------------------------------------------------

    async def receive(self) -> UserRequest:
        """缓冲式接收消息，在合适时机触发返回。

        1. 阻塞等待第一条消息
        2. 启动计时器
        3. 非阻塞收集后续消息
        4. 在 min_deadline ~ max_deadline 之间触发

        Returns:
            UserRequest with metadata["buffered"] containing the buffered
            messages. If exit signal received, returns UserRequest with
            metadata["exit"] = True.
        """
        if self._kba is None:
            raise RuntimeError(
                "FlexibleGroupChatInputAdapter.receive() called before "
                "_inject_kernel_context. Kernel must spawn the agent first."
            )

        self._buffer.clear()

        # ── Phase 1: Block until first message ──
        logger.debug(f"[{self._pid}] Phase 1: waiting for first message...")
        first_msg = await self._receive_one()
        if first_msg is None:
            # Exit signal
            return UserRequest(text="", metadata={"exit": True})

        self._buffer.append(first_msg)
        now = time.time()
        min_deadline = now + self._min_wait + random.random() * self._jitter
        max_deadline = now + self._max_wait + random.random() * self._jitter
        logger.debug(
            f"[{self._pid}] Phase 2: first message received, "
            f"min_deadline={min_deadline - now:.1f}s, "
            f"max_deadline={max_deadline - now:.1f}s"
        )

        # ── Phase 2: Collect messages until deadline ──
        while True:
            now = time.time()
            remaining = max(min_deadline - now, 0.0)

            if remaining > 0:
                # Wait with timeout for the next message
                msg = await self._receive_one(timeout=remaining)
                if msg is None:
                    # Exit signal during wait
                    break
                if msg:
                    self._buffer.append(msg)
                    logger.debug(
                        f"[{self._pid}] buffered message from "
                        f"'{msg.get('from', '?')}', "
                        f"buffer_size={len(self._buffer)}"
                    )
                # Loop back to check deadlines
                continue

            # Timeout reached — check if we should trigger
            now = time.time()

            # If buffer has content and we're past min_deadline, trigger
            if self._buffer and now >= min_deadline:
                logger.debug(
                    f"[{self._pid}] Triggering at "
                    f"{now - (min_deadline - self._min_wait):.1f}s "
                    f"with {len(self._buffer)} buffered messages"
                )
                break

            # If past max_deadline and buffer non-empty, force trigger
            if self._buffer and now >= max_deadline:
                logger.debug(
                    f"[{self._pid}] Force-triggering at max_deadline "
                    f"with {len(self._buffer)} buffered messages"
                )
                break

            # Buffer empty but past max_deadline — continue waiting
            # (this shouldn't normally happen with reasonable params)
            if not self._buffer and now >= max_deadline:
                logger.debug(
                    f"[{self._pid}] max_deadline reached but buffer empty, "
                    f"continuing to wait"
                )
                # Reset deadlines to wait more
                min_deadline = now + self._min_wait
                max_deadline = now + self._max_wait

        # ── Phase 3: Build UserRequest ──
        # Check for exit
        if not self._buffer:
            return UserRequest(text="", metadata={"exit": True})

        # Build summary text (for logging/debugging)
        summary = f"[群聊摘要] {len(self._buffer)}条消息"

        return UserRequest(
            text=summary,
            metadata={"buffered": list(self._buffer)},
        )

    async def send(self, event, target=None):
        """委托给内部 KernelBridgeAdapter.send()。

        Args:
            event: AdapterEvent 实例。
            target: 可选定向投递目标 pid。
        """
        if self._kba is None:
            logger.warning(
                f"FlexibleGroupChatInputAdapter[{self._pid}].send() "
                f"called before kernel context injected. Dropping event."
            )
            return
        await self._kba.send(event, target=target)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _receive_one(
        self, timeout: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """从 KBA 接收一条消息并转换为 buffered 格式。

        Args:
            timeout: 最大等待秒数。None 表示无限等待。

        Returns:
            dict with keys: from, from_name, content, timestamp.
            None if exit signal received.
            空 dict 如果超时（仅在 timeout 不为 None 时）。
        """
        try:
            if timeout is not None:
                request = await asyncio.wait_for(
                    self._kba.receive(), timeout=timeout
                )
            else:
                request = await self._kba.receive()
        except asyncio.TimeoutError:
            return {}

        # Check exit signal
        if request.metadata.get("exit"):
            return None

        # ── Handle entry_prompt (has workflow_flag, no "from") ──
        # entry_prompt is the initial instruction from the workflow script.
        # It's not a real group chat message but serves as the agent's
        # trigger to introduce themselves. Present it as context from "群聊".
        if "workflow_flag" in request.metadata and "from" not in request.metadata:
            return {
                "from": "system",
                "from_name": "群聊",
                "content": request.text,
                "timestamp": time.time(),
            }

        # ── Build buffered message from UserRequest ──
        from_pid = request.metadata.get("from", "unknown")
        from_name = self._resolve_display_name(from_pid)

        return {
            "from": from_pid,
            "from_name": from_name,
            "content": request.text,
            "timestamp": time.time(),
        }

    def _resolve_display_name(self, pid: str) -> str:
        """将 pid 映射为显示名称。

        对于 "user" 特殊 pid，使用配置的 user_name。
        对于其他 pid，直接使用 pid 作为显示名（后续可从 metadata 增强）。
        """
        if pid == "user":
            return self._user_name

        # For agent pids, use pid as fallback display name.
        # In a full implementation, this could look up metadata from
        # the agent registry or workflow config.
        return pid
