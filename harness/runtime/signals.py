"""SIGINT 两阶段处理器工厂函数。

第一阶段（首次 Ctrl+C）：推送 __EXIT_SENTINEL__ 给所有 agent，优雅退出。
第二阶段（再次 Ctrl+C）：强制 task.cancel() 所有协程，立即终止。
"""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

from .agent_runtime import AgentState
from .types import __EXIT_SENTINEL__

if TYPE_CHECKING:
    from .runtime import Runtime


def create_sigint_handler(runtime: 'Runtime'):
    """创建两阶段 SIGINT 处理器。

    第一阶段：推送 __EXIT_SENTINEL__ 给所有非 FINISHED agent，
              等待它们完成 _phase_end 后自然退出。
    第二阶段：强制 task.cancel() 所有协程。
              AgentRuntime.run() 中 asyncio.shield 被 CancelledError
              穿透，跳过 _phase_end 直接 FINISHED。

    用法:
        handler = create_sigint_handler(runtime)
        loop.add_signal_handler(signal.SIGINT, handler)

    注意: add_signal_handler 仅在主线程有效。非主线程或 Windows 下
         会抛 NotImplementedError，调用方应 catch 后优雅降级。
    """
    def _on_sigint():
        kernel = runtime._kernel
        if kernel is None:
            return

        if runtime._sigint_count == 0:
            # 第一阶段：优雅退出
            runtime._sigint_count = 1
            for pid, agent in kernel.runtime_table.items():
                if agent.state != AgentState.FINISHED:
                    agent.should_exit = True
                    if pid in kernel.input_queues:
                        kernel.input_queues[pid].put_nowait(
                            __EXIT_SENTINEL__
                        )
        else:
            # 第二阶段：强制终止
            for task in kernel._tasks.values():
                if not task.done():
                    task.cancel()

    return _on_sigint
