"""Runtime — 顶层入口。

创建 Kernel + 启动事件循环 + 注册信号处理。

用法:
    # Mode A（交互式单 agent）
    console = CliConsole()
    root_harness = Harness.from_container(container, call_llm=my_llm)
    Runtime(console).run(root_harness)

    # Mode B（直接启动 workflow 脚本）
    Runtime(console).run_from_script("workflow.py")
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import TYPE_CHECKING

from .signals import create_sigint_handler
from .types import RuntimeStarted, RuntimeStopped, WorkflowFinished

if TYPE_CHECKING:
    from ..interfaces.system_console import SystemConsole

logger = logging.getLogger(__name__)


class Runtime:
    """Runtime 顶层入口。

    负责创建 Kernel、启动事件循环、注册信号处理。
    Mode A: Runtime(console).run(harness) — 单 agent 交互式对话。
    Mode B: Runtime(console).run_from_script(path) — Batch 3 ✅。
    """

    def __init__(self, console: 'SystemConsole', session_config=None):
        """初始化 Runtime。

        Args:
            console: SystemConsole 实现（如 CliConsole）。
            session_config: SessionConfig（可选）。None 时使用默认配置
                            （root=./sessions, enabled=True）。
        """
        self._console = console
        self._session_config = session_config
        self._store = None
        self._kernel = None
        self._sigint_count: int = 0

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def run(self, harness) -> None:
        """同步入口 — 启动整个 Runtime（Mode A）。

        Args:
            harness: 装配好的 Harness 实例。其 container 属性
                     用于构造 AsyncLifecycleOrchestrator。
        """
        try:
            asyncio.run(self._run_async(harness))
        except KeyboardInterrupt:
            # SIGINT 已在 _on_sigint 中处理
            pass

    def run_from_script(self, script_path: str) -> None:
        """Mode B 入口 — 直接启动 workflow 脚本。

        不创建 root agent。从脚本加载 agent 并启动，等待所有 agent
        FINISHED（通过静默检测或自然结束），汇总结果后返回。

        用法:
            console = CliConsole()
            Runtime(console).run_from_script("workflow.py")
        """
        try:
            asyncio.run(self._run_from_script_async(script_path))
        except KeyboardInterrupt:
            # SIGINT 已在 _on_sigint 中处理
            pass

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _open_store(self):
        """按配置创建 SessionStore 并开始会话（resume 路径在 T13 改为 boot 接管）。"""
        from ..core.session.config import SessionConfig
        from ..core.session.store import SessionStore

        cfg = self._session_config or SessionConfig()
        self._store = SessionStore(cfg.root, enabled=cfg.enabled)
        self._store.begin_session(None)
        return self._store

    async def _close_store(self) -> None:
        """drain + fsync + close；降级的一次性提示（失败方向向下的最后一环）。"""
        if self._store is None:
            return
        try:
            await self._store.close()
        except Exception as e:
            logger.error("SessionStore.close() failed: %s", e)
        for pid in self._store.degraded:
            print(f"[系统] 警告：agent '{pid}' 的会话日志写盘降级，"
                  f"该 agent 日志可能不完整。")

    async def _run_async(self, harness) -> None:
        """异步主流程。"""
        from .kernel import Kernel, make_async_llm

        # 1. sync→async LLM bridge（在 Runtime 入口层，不侵入 orchestrator）
        call_llm = getattr(harness, 'call_llm', None)
        if call_llm and not asyncio.iscoroutinefunction(call_llm):
            call_llm = make_async_llm(call_llm)
            logger.info("Wrapped sync call_llm → async via asyncio.to_thread")

        # 2. 创建 Kernel（注入进程级 SessionStore）
        store = self._open_store()
        self._kernel = Kernel(self._console, store=store)

        # 3. spawn root agent
        self._kernel.spawn_root(harness, call_llm=call_llm)

        # 4. 启动协程
        #    Mode A 不启动 _monitor_quiescence —— root agent 在 receive()
        #    中等待用户输入时处于 idle 状态，静默检测会误判为"工作完成"而
        #    强制终止。Mode A 由用户 /exit 或 SIGINT 控制退出。
        task_root = self._kernel._tasks["root"]
        task_sys = asyncio.create_task(
            self._kernel._handle_system_input()
        )

        # 5. 注册信号处理
        handler = create_sigint_handler(self)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, handler)
            logger.debug("SIGINT handler registered")
        except NotImplementedError:
            logger.debug("SIGINT handler not available on this platform")

        # 6. 推送启动事件
        await self._console.send(RuntimeStarted())

        # 7. 等待完成（root + system input）
        try:
            await asyncio.gather(
                task_root, task_sys,
                return_exceptions=True,
            )
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass
            await self._close_store()

        # 8. 推送停止事件
        await self._console.send(RuntimeStopped())

    async def _run_from_script_async(self, script_path: str) -> None:
        """Mode B 异步主流程。"""
        from .kernel import Kernel

        # 1. 创建 Kernel（注入进程级 SessionStore）
        store = self._open_store()
        self._kernel = Kernel(self._console, store=store)

        # 2. 从脚本 spawn agent（无 parent）
        result = self._kernel.spawn_from_script(script_path, parent=None)

        # 2a. 设置 CliConsole 的 all_finished 回调（Mode B 空输入退出用）
        if hasattr(self._console, 'set_all_finished_hook'):
            self._console.set_all_finished_hook(self._kernel.all_finished)

        logger.info(
            f"run_from_script: workflow_flag='{result['workflow_flag']}' "
            f"with {len(result['agents'])} agent(s)"
        )

        # 3. 启动系统输入处理
        task_sys = asyncio.create_task(
            self._kernel._handle_system_input()
        )

        # 4. 注册信号处理
        handler = create_sigint_handler(self)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, handler)
            logger.debug("SIGINT handler registered (Mode B)")
        except NotImplementedError:
            logger.debug("SIGINT handler not available on this platform")

        # 6. 推送启动事件
        await self._console.send(RuntimeStarted())

        # 6. 等待所有 agent tasks 完成
        #    collector (oneshot) → auto-FINISHED → cascade → analyzer (continuous) → FINISHED
        #    不启用静默检测——agent 执行时间差异大，统一的静默阈值不可靠。
        try:
            agent_tasks = list(self._kernel._tasks.values())
            await asyncio.gather(
                *agent_tasks,
                return_exceptions=True,
            )
        finally:
            # 提示用户退出（用 SystemMessage 而非 CommandError）
            from .types import SystemMessage
            await self._console.send(SystemMessage(
                message="所有 agent 已完成。按 Enter 退出..."
            ))
            # task_sys 仍在 readline 中阻塞——等用户按 Enter 后
            # _handle_system_input 中的 readline 返回 → while 循环
            # → CliConsole.receive() 检测到 all_finished → 返回
            # CommandExit() → _handle_system_input 设 _shutdown=True
            # → 退出循环
            try:
                await task_sys
            except asyncio.CancelledError:
                pass

            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass

            await self._close_store()

        # 8. 收集最终输出
        agents_results = []
        for pid, r in self._kernel.runtime_table.items():
            agents_results.append({
                "pid": pid,
                "output": r.last_output,
                "error": r.error,
                "rounds": r.round_count,
                "duration": (
                    time.time() - r.started_at if r.started_at else 0.0
                ),
            })

        # 9. 推送 WorkflowFinished
        await self._console.send(WorkflowFinished(
            workflow_flag=result["workflow_flag"],
            agents=agents_results,
        ))

        # 10. 推送停止事件
        await self._console.send(RuntimeStopped())
