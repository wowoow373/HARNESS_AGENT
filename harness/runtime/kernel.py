"""Kernel — 全局单例。

进程表 + 消息路由 + 调度。做机制不做策略——不编排 workflow、
不决定 agent 行为。

Batch 1: 仅支持 spawn_root（单 agent，Mode A）。
Batch 2: spawn_from_script（多 agent workflow 脚本加载）。
Batch 3: MessageBus 集成、默认订阅、级联终止、静默检测完整实现。
Batch 4: 系统命令解析（/agents /kill /end /exit /talk）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

from ..interfaces.types import UserRequest
from .types import (
    AgentFinished,
    AgentSpawned,
    CommandTalk,
    __EXIT_SENTINEL__,
)

if TYPE_CHECKING:
    from .agent_runtime import AgentRuntime

logger = logging.getLogger(__name__)


class Kernel:
    """全局单例。进程表 + 消息路由 + 调度。

    职责：
    - 维护 runtime_table（pid → AgentRuntime）
    - 维护 input_queues（pid → asyncio.Queue）
    - 提供 spawn_root / send_input / kill / end_workflow / finish_agent /
      list_agents / all_finished 公开方法
    - 监控静默（stub in Batch 1, 完整实现 in Batch 3）
    - 处理系统输入（stub in Batch 1, 完整实现 in Batch 4）
    """

    def __init__(self, console):
        """初始化 Kernel。

        Args:
            console: SystemConsole 实例，用于推送系统事件。
        """
        # 进程表
        self.runtime_table: dict[str, 'AgentRuntime'] = {}
        self.input_queues: dict[str, asyncio.Queue] = {}
        self._tasks: dict[str, asyncio.Task] = {}

        # workflow（Batch 1 仅单 agent）
        self.workflow_table: dict[str, list[str]] = {}
        self._spawn_counter: int = 0

        # 基础设施
        self._console = console
        self.message_bus = None  # Batch 3 替换为 MessageBus
        self._shutdown: bool = False

        # Batch 2-3 预留
        self._pending_subscriptions: list[tuple[str, str]] = []

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def spawn_root(self, harness, call_llm=None) -> str:
        """创建 Mode A 根 agent（pid="root", mode="continuous"）。

        Args:
            harness: 装配好的 Harness 实例。
            call_llm: async LLM callable。已在 Runtime 入口层做过
                      sync→async 桥接。None 表示无 LLM（测试模式）。

        Returns:
            pid: 固定为 "root"。
        """
        from .agent_runtime import AgentRuntime
        from .bridge_adapter import KernelBridgeAdapter

        pid = "root"

        # 1. 创建 AgentRuntime
        runtime = AgentRuntime(
            pid=pid,
            mode="continuous",  # Mode A root 强制 continuous
            harness=harness,
            kernel=self,
            parent=None,
        )

        # 2. 挂载 KernelBridgeAdapter
        runtime.adapter = KernelBridgeAdapter(
            pid=pid, kernel=self, runtime=runtime
        )

        # 3. 初始化 orchestrator
        runtime._init_orchestrator(call_llm=call_llm)

        # 4. 注册到进程表
        self.runtime_table[pid] = runtime
        self.input_queues[pid] = asyncio.Queue()

        # 5. 推送 SystemConsole 事件
        asyncio.create_task(
            self._console.send(AgentSpawned(pid=pid, parent=None))
        )

        # 6. 启动 asyncio Task
        task = asyncio.create_task(runtime.run())
        self._tasks[pid] = task
        task.add_done_callback(
            lambda t, r=runtime: asyncio.create_task(
                self._on_agent_finished(r)
            )
        )

        # 7. 记录 workflow
        self.workflow_table["wf_root"] = [pid]

        logger.info(f"spawn_root: pid='{pid}' created and started")
        return pid

    def send_input(self, pid: str, request: UserRequest) -> None:
        """向指定 agent 投递 UserRequest。

        框架内部 API——用于 entry_prompt 注入、child_finished 通知等。

        Args:
            pid: 目标 agent 标识。
            request: 要投递的 UserRequest。
        """
        if pid in self.input_queues:
            self.input_queues[pid].put_nowait(request)
        else:
            logger.warning(f"send_input: pid '{pid}' not in input_queues")

    def kill(self, pid: str) -> None:
        """终止指定 agent。

        设置 agent.should_exit = True，并向其 input_queue 推送
        __EXIT_SENTINEL__。如果 agent 在 receive() 中等待，会被唤醒；
        如果在 call_llm() 中，LLM 返回后下一轮 while 检测到 should_exit。

        Args:
            pid: 目标 agent 标识。
        """
        from .agent_runtime import AgentState

        agent = self.runtime_table.get(pid)
        if agent and agent.state != AgentState.FINISHED:
            agent.should_exit = True
            if pid in self.input_queues:
                self.input_queues[pid].put_nowait(__EXIT_SENTINEL__)
            logger.info(f"kill: pid='{pid}' signalled to exit")
        elif agent:
            logger.debug(f"kill: pid='{pid}' already FINISHED, skipping")

    def end_workflow(self, flag: str) -> None:
        """终止整个 workflow。

        对 workflow_table[flag] 中所有非 FINISHED agent 调用 kill()。

        Args:
            flag: workflow 标识（如 "wf_root" 或 "wf_001"）。
        """
        pids = self.workflow_table.get(flag, [])
        logger.info(f"end_workflow: flag='{flag}' killing {len(pids)} agents")
        for pid in pids:
            self.kill(pid)

    def finish_agent(self, pid: str) -> None:
        """agent 自身完成（语义上等同于 kill）。"""
        logger.info(f"finish_agent: pid='{pid}'")
        self.kill(pid)

    def list_agents(self) -> dict[str, dict]:
        """返回 runtime_table 的只读快照。

        Returns:
            dict[pid, {"state": str, "mode": str, "parent": str|None,
                        "rounds": int, "error": str|None}]
        """
        return {
            pid: {
                "state": r.state.value,
                "mode": r.mode,
                "parent": r.parent.pid if r.parent else None,
                "rounds": r.round_count,
                "error": r.error,
            }
            for pid, r in self.runtime_table.items()
        }

    def all_finished(self) -> bool:
        """所有 runtime_table 中的 agent 是否均为 FINISHED。"""
        from .agent_runtime import AgentState

        return all(
            r.state == AgentState.FINISHED
            for r in self.runtime_table.values()
        )

    # ------------------------------------------------------------------
    # 内部方法（Batch 1 stub，后续 batch 升级）
    # ------------------------------------------------------------------

    async def _on_agent_finished(self, runtime: 'AgentRuntime') -> None:
        """agent FINISHED 时的回调（由 Task.done_callback 触发）。

        Batch 1 stub: 仅推送 AgentFinished 事件到 SystemConsole。
        Batch 3 完整实现: + child_finished 默认订阅 + 级联终止。

        Args:
            runtime: 已进入 FINISHED 的 AgentRuntime 实例。
        """
        duration = time.time() - runtime.started_at

        await self._console.send(AgentFinished(
            pid=runtime.pid,
            result=runtime.last_output,
            duration=duration,
            error=runtime.error,
        ))

        logger.info(
            f"_on_agent_finished: pid='{runtime.pid}' "
            f"duration={duration:.1f}s error={runtime.error}"
        )

    async def _monitor_quiescence(self) -> None:
        """静默检测监控协程。

        Batch 1 stub: 只等待所有 agent FINISHED 后返回。
        Batch 3 完整实现: 检测 idle 后主动推送 sentinel 全体退出。
        """
        logger.info("_monitor_quiescence: started (stub mode)")
        while not self._shutdown:
            if self.all_finished():
                logger.info("_monitor_quiescence: all agents FINISHED, exiting")
                return
            await asyncio.sleep(1)

    async def _handle_system_input(self) -> None:
        """系统输入处理循环。

        Batch 1 stub: 仅纯文本路由到 root（Mode A）。
        Batch 4 完整实现: /agents /kill /end /exit /talk 命令解析。
        """
        logger.info("_handle_system_input: started (stub mode)")
        while not self._shutdown:
            command = await self._console.receive()

            if isinstance(command, CommandTalk):
                target_pid = command.pid
                if target_pid in self.runtime_table:
                    self.send_input(
                        target_pid,
                        UserRequest(text=command.text),
                    )
                else:
                    logger.warning(
                        f"No agent with pid '{target_pid}' "
                        f"for routing text: '{command.text[:50]}...'"
                    )
