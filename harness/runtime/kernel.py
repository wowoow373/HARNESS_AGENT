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

        # 2a. Inject Runtime tools
        self._inject_runtime_tools(harness.container, pid=pid)

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

    def end_workflow(self, flag: str) -> list[str]:
        """Terminate entire workflow, return list of killed pids.

        Args:
            flag: Workflow identifier (e.g. "wf_root" or "wf_001").

        Returns:
            list[str]: Pids of agents that were killed.
        """
        pids = self.workflow_table.get(flag, [])
        logger.info(f"end_workflow: flag='{flag}' killing {len(pids)} agents")
        for pid in pids:
            self.kill(pid)
        return list(pids)

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

    def spawn_from_script(
        self, script_path: str, parent=None
    ) -> dict:
        """Load a workflow script, create multiple agents and start them.

        Args:
            script_path: Absolute path to the workflow script.
            parent: Parent AgentRuntime, None for top-level agents.

        Returns:
            {"workflow_flag": str, "agents": [{"pid": str, "parent": str|None,
                                               "metadata": dict}]}

        Raises:
            FileNotFoundError: Script file cannot be loaded.
            ValueError: No @agent declarations, or subscribe references
                        unknown agent names.
        """
        import sys
        import importlib.util
        from . import decorators
        from .agent_runtime import AgentRuntime, AgentState
        from .bridge_adapter import KernelBridgeAdapter

        # ── Step 1: Generate workflow_flag ──
        self._spawn_counter += 1
        workflow_flag = f"wf_{self._spawn_counter:03d}"

        # ── Step 2: Clear registries + load script ──
        decorators._agent_registry.clear()
        decorators._subscription_registry.clear()

        try:
            spec = importlib.util.spec_from_file_location(
                "_workflow_script", script_path
            )
            if spec is None:
                raise FileNotFoundError(
                    f"Cannot load workflow script: {script_path}"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_workflow_script"] = module
            spec.loader.exec_module(module)
        except Exception:
            decorators._agent_registry.clear()
            decorators._subscription_registry.clear()
            raise

        # ── Step 3: Validate registries ──
        if not decorators._agent_registry:
            raise ValueError(
                f"No @agent declarations found in '{script_path}'"
            )

        for sub in decorators._subscription_registry:
            if sub.subscriber not in decorators._agent_registry:
                raise ValueError(
                    f"subscribe('{sub.subscriber}') references unknown "
                    f"agent. Known: {list(decorators._agent_registry.keys())}"
                )
            if sub.publisher not in decorators._agent_registry:
                raise ValueError(
                    f"subscribe(...).to('{sub.publisher}') references unknown "
                    f"agent. Known: {list(decorators._agent_registry.keys())}"
                )

        # ── Step 4: Stash subscription relationships ──
        for sub in decorators._subscription_registry:
            self._pending_subscriptions.append(
                (sub.subscriber, sub.publisher)
            )

        # ── Step 5: Create AgentRuntime for each @agent ──
        created_pids: list[str] = []
        agent_results: list[dict] = []

        for name, blueprint in decorators._agent_registry.items():
            try:
                # 5a. Call factory to get Harness
                harness = blueprint["factory"]()

                # 5b. Determine mode
                has_subscriptions = any(
                    sub.subscriber == name or sub.publisher == name
                    for sub in decorators._subscription_registry
                )
                mode = "continuous" if has_subscriptions else "oneshot"

                # 5c. Create AgentRuntime
                runtime = AgentRuntime(
                    pid=name,
                    mode=mode,
                    harness=harness,
                    kernel=self,
                    parent=parent,
                )

                # 5d. Mount KernelBridgeAdapter
                runtime.adapter = KernelBridgeAdapter(
                    pid=name, kernel=self, runtime=runtime
                )

                # 5e. Extract and bridge call_llm
                import asyncio as _asyncio
                call_llm = getattr(harness, 'call_llm', None)
                if call_llm and not _asyncio.iscoroutinefunction(call_llm):
                    original = call_llm

                    async def _async_wrapper(msgs, tools,
                                             _orig=original):
                        return await _asyncio.to_thread(_orig, msgs, tools)

                    call_llm = _async_wrapper

                # 5f. Inject Runtime tools
                self._inject_runtime_tools(harness.container, pid=name)

                # 5g. Initialize orchestrator
                runtime._init_orchestrator(call_llm=call_llm)

                # 5h. Register with Kernel (replace if already exists)
                if name in self.runtime_table:
                    existing = self.runtime_table[name]
                    if existing.state != AgentState.FINISHED:
                        logger.warning(
                            f"Agent name '{name}' already exists in "
                            f"runtime_table (state={existing.state.value}), "
                            f"overwriting with new agent."
                        )
                    self.input_queues.pop(name, None)
                    self._tasks.pop(name, None)
                self.runtime_table[name] = runtime
                self.input_queues[name] = asyncio.Queue()

                # 5i. Record parent-child relationship
                if parent is not None:
                    parent.children.append(name)

                created_pids.append(name)
                agent_results.append({
                    "pid": name,
                    "parent": parent.pid if parent else None,
                    "metadata": blueprint.get("metadata", {}),
                })

            except Exception:
                # Rollback: clean up created AgentRuntimes
                for created_pid in created_pids:
                    self.runtime_table.pop(created_pid, None)
                    self.input_queues.pop(created_pid, None)
                if parent is not None:
                    for created_pid in created_pids:
                        if created_pid in parent.children:
                            parent.children.remove(created_pid)
                raise

        # ── Step 6: Push SystemConsole events ──
        try:
            _ = asyncio.get_running_loop()
            _has_loop = True
        except RuntimeError:
            _has_loop = False

        if _has_loop:
            for name in created_pids:
                from .types import AgentSpawned
                asyncio.create_task(
                    self._console.send(
                        AgentSpawned(
                            pid=name, parent=parent.pid if parent else None
                        )
                    )
                )

        # ── Step 7: Start asyncio Tasks ──
        if _has_loop:
            for name in created_pids:
                runtime = self.runtime_table[name]
                task = asyncio.create_task(runtime.run())
                self._tasks[name] = task
                task.add_done_callback(
                    lambda t, r=runtime: asyncio.create_task(
                        self._on_agent_finished(r)
                    )
                )

        # ── Step 8: Record workflow mapping ──
        self.workflow_table[workflow_flag] = created_pids.copy()

        # ── Step 9: Deliver entry_prompts ──
        for name, blueprint in decorators._agent_registry.items():
            self.send_input(
                name,
                UserRequest(
                    text=blueprint["entry_prompt"],
                    metadata={"workflow_flag": workflow_flag},
                ),
            )

        logger.info(
            f"spawn_from_script: workflow_flag='{workflow_flag}' "
            f"created {len(created_pids)} agent(s): {created_pids}"
        )

        # ── Step 10: Return ──
        return {
            "workflow_flag": workflow_flag,
            "agents": agent_results,
        }

    def _inject_runtime_tools(self, container, pid: str) -> None:
        """Inject Runtime management tools into an agent's DI container.

        Wraps as CompositeSystemToolProvider, preserving user's original
        SystemToolProvider.

        Args:
            container: The agent's DIContainer instance.
            pid: The current agent's pid.
        """
        from .tools import create_runtime_tools, CompositeSystemToolProvider
        from ..interfaces.system_tool_provider import SystemToolProvider

        try:
            user_provider = container.resolve(SystemToolProvider)
        except Exception:
            user_provider = None

        runtime_tools = create_runtime_tools(kernel=self, pid=pid)

        composite = CompositeSystemToolProvider(
            user_provider=user_provider,
            runtime_tools=runtime_tools,
        )

        # Replace if already registered (composite wraps original provider,
        # so user tools are not lost)
        if container.is_registered(SystemToolProvider):
            container._registry[SystemToolProvider] = composite
        else:
            container.register(SystemToolProvider, composite)

        logger.debug(
            f"_inject_runtime_tools: pid='{pid}' "
            f"injected {composite.tool_count} runtime tool(s)"
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
