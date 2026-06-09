"""AgentRuntime — 每个 Agent 的"进程控制块"。

管理 Agent 生命周期状态机、运行模式（continuous/oneshot）、
外层多轮对话循环。内部委托 AsyncLifecycleOrchestrator 执行三阶段编排。
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import TYPE_CHECKING, Optional

from ..core.async_orchestrator import AsyncLifecycleOrchestrator
from ..interfaces.async_call_llm import AsyncCallLLM
from ..interfaces.types import AssemblyContext

if TYPE_CHECKING:
    from .kernel import Kernel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentState
# ---------------------------------------------------------------------------


class AgentState(enum.Enum):
    """Agent 生命周期状态机。

    CREATED ──→ INIT ──→ RUNNING ──→ TERMINATING ──→ FINISHED
      │           │          │              ▲
      │           │          │              │
      └───────────┴──────────┴──────────────┘
      任何非 FINISHED 状态均可 → TERMINATING
    """

    CREATED     = "created"      # spawn 完成，Task 未启动
    INIT        = "init"         # _phase_init() 执行中，等待首条输入
    RUNNING     = "running"      # 对话循环运行中
    TERMINATING = "terminating"  # _phase_end() 执行中
    FINISHED    = "finished"     # 不可逆终态


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------


class AgentRuntime:
    """每个 Agent 的运行时上下文。

    负责：
    - 状态机管理（CREATED → INIT → RUNNING → TERMINATING → FINISHED）
    - 外层多轮对话循环（max_rounds 控制、should_exit 检测、oneshot 判定）
    - _idle_for_quiescence() 供 Kernel 静默检测使用

    内部委托 AsyncLifecycleOrchestrator 执行三阶段编排。
    """

    def __init__(
        self,
        *,
        pid: str,
        mode: str,
        harness,
        kernel: 'Kernel',
        parent: Optional['AgentRuntime'] = None,
        max_rounds: int = 1000,
    ):
        """初始化 AgentRuntime。

        Args:
            pid: 在 Kernel 中的唯一标识。root agent 固定为 "root"。
            mode: 运行模式。"continuous" 持续等待输入，
                  "oneshot" 一轮完成后自动退出。
            harness: 装配好的 Harness 实例。
            kernel: Kernel 全局单例引用。
            parent: 父 AgentRuntime。None 表示顶层 agent。
            max_rounds: 最大对话轮数硬上限（安全网）。
        """
        self.pid = pid
        self.mode = mode
        self._harness = harness
        self._kernel = kernel
        self.parent = parent
        self.max_rounds = max_rounds

        # 状态
        self.state = AgentState.CREATED
        self.should_exit = False
        self.error: Optional[str] = None
        self._idle_since: Optional[float] = None

        # 计数与结果
        self.round_count = 0
        self.started_at: float = 0.0
        self.last_output: str = ""
        self._finished = asyncio.Event()

        # I/O 通道（由 Kernel.spawn_root() 等挂载）
        self.adapter = None

        # 子 agent 追踪（Batch 2+ 使用）
        self.children: list[str] = []

        # workflow 归属（Batch 3+，由 Kernel.spawn 设置）
        self.workflow_flag: Optional[str] = None

        # 编排器（_init_orchestrator() 完成后设置）
        self._orchestrator: Optional[AsyncLifecycleOrchestrator] = None

    # ------------------------------------------------------------------
    # orchestrator 初始化
    # ------------------------------------------------------------------

    def _init_orchestrator(self, call_llm: Optional[AsyncCallLLM] = None):
        """在 Kernel 设置好 adapter 后调用，完成 orchestrator 装配。

        Args:
            call_llm: async LLM callable。已在 Runtime 入口层做过
                      sync→async 桥接。
        """
        self._orchestrator = AsyncLifecycleOrchestrator(
            container=self._harness.container,
            adapter=self.adapter,
            call_llm=call_llm,
        )

    # ------------------------------------------------------------------
    # run() 协程
    # ------------------------------------------------------------------

    async def run(self):
        """Agent 主协程。

        生命周期：
        1. INIT: _phase_init() → 等待首条 UserRequest
        2. RUNNING: 外层 while 循环，每轮调 _phase_loop(ctx)
        3. TERMINATING: _phase_end(traj) → 清理
        4. FINISHED: _finished Event 设置
        """
        self.state = AgentState.INIT
        self.started_at = time.time()
        logger.info(f"[{self.pid}] run() started, mode={self.mode}")

        try:
            # ── 阶段一：会话初始化 ──
            ctx = await self._orchestrator._phase_init()

            # _phase_init 中收到 exit → should_exit_flag 为 True
            if self._orchestrator._should_exit_flag:
                self.should_exit = True
                logger.info(f"[{self.pid}] Exit detected in phase_init")

            self.state = AgentState.RUNNING

            # ── 外层循环：每轮对话 ──
            while not self.should_exit and self.round_count < self.max_rounds:
                # 阶段二：执行一轮
                await self._orchestrator._phase_loop(ctx)
                self.round_count += 1

                # oneshot 模式：一轮后自动退出
                if self.mode == "oneshot":
                    logger.info(f"[{self.pid}] oneshot: auto-exit after round 1")
                    self.should_exit = True
                    break

                # 退出检查（在 receive 之前——避免消费多余消息）
                if self.should_exit or self.round_count >= self.max_rounds:
                    break

                # 等待下一轮输入
                logger.debug(f"[{self.pid}] Waiting for next round input...")
                self._idle_since = time.time()
                request = await self.adapter.receive()
                self._idle_since = None

                if self._orchestrator._should_exit(request):
                    logger.info(f"[{self.pid}] Exit signal in receive, breaking loop")
                    self.should_exit = True
                    break

                # 更新 ctx 用于下一轮
                ctx = AssemblyContext(
                    user_request=request,
                    guides=self._orchestrator._cached_guides,
                    available_tools=self._orchestrator._cached_tools,
                    history=self._orchestrator._history,
                    memories=ctx.memories,
                )

        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            logger.error(f"[{self.pid}] run() error: {self.error}")

        finally:
            self.state = AgentState.TERMINATING
            logger.info(f"[{self.pid}] Entering TERMINATING (error={self.error})")

            # 在 _phase_end 清理 history 之前提取 last_output
            self.last_output = self._extract_last_output()

            try:
                traj = self._orchestrator._build_trajectory()
                await asyncio.shield(self._orchestrator._phase_end(traj))
            except asyncio.CancelledError:
                logger.info(f"[{self.pid}] _phase_end cancelled (force shutdown)")
            except Exception as e:
                if not self.error:
                    self.error = f"_phase_end failed: {e}"
                logger.warning(f"[{self.pid}] _phase_end error: {e}")

            self.state = AgentState.FINISHED
            self._finished.set()
            logger.info(
                f"[{self.pid}] FINISHED — rounds={self.round_count}, "
                f"output_len={len(self.last_output)}"
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _idle_for_quiescence(self) -> bool:
        """判断 agent 是否在等待输入（静默检测用）。

        Returns:
            True 如果 agent 在 RUNNING 状态且正在等待 adapter.receive()。
        """
        return (
            self.state == AgentState.RUNNING
            and self._idle_since is not None
        )

    def _extract_last_output(self) -> str:
        """从 orchestrator._history 中提取最后一条 assistant 输出。

        Returns:
            assistant 最后一条文本内容，无历史时返回空字符串。
        """
        if self._orchestrator and self._orchestrator._history:
            for msg in reversed(self._orchestrator._history):
                if msg.role == "assistant" and msg.content:
                    return msg.content
        return ""
