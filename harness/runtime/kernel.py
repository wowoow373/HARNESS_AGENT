"""Kernel — 全局单例。

进程表 + 消息路由 + 调度。做机制不做策略——不编排 workflow、
不决定 agent 行为。

Batch 1: 仅支持 spawn_root（单 agent，Mode A）。
Batch 2: spawn_from_script（多 agent workflow 脚本加载）。
Batch 3: MessageBus + 订阅 + 级联 + 静默检测完整实现。✅
Batch 4: 系统命令解析（/agents /kill /end /exit /talk）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..core.session.ids import new_msg_id
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


def _resolve_adapter(container, pid: str, kernel: 'Kernel', runtime: 'AgentRuntime'):
    """Resolve AsyncInputAdapter from DI container, fallback to KBA.

    User can register a custom AsyncInputAdapter in their workflow script's
    @agent assembly function — e.g. a batch-window adapter for slow readers.
    If none registered, the default KernelBridgeAdapter is used.

    Extension point: If the resolved adapter has an ``_inject_kernel_context``
    method (duck-typed), it is called with (pid, kernel, runtime). This allows
    custom adapters (e.g. FlexibleGroupChatInputAdapter) to create their
    internal KernelBridgeAdapter after the kernel context is available.
    """
    from ..interfaces.async_input_adapter import AsyncInputAdapter
    from .bridge_adapter import KernelBridgeAdapter

    try:
        adapter = container.resolve(AsyncInputAdapter)
    except Exception:
        return KernelBridgeAdapter(pid=pid, kernel=kernel, runtime=runtime)

    # Inject kernel context into custom adapters that support it.
    # Duck-typed: any adapter with _inject_kernel_context gets the injection.
    if hasattr(adapter, '_inject_kernel_context'):
        adapter._inject_kernel_context(pid=pid, kernel=kernel, runtime=runtime)

    return adapter


def make_async_llm(sync_call_llm):
    """Wrap a synchronous call_llm as an async callable via asyncio.to_thread."""
    async def _wrapper(msgs, tools, _orig=sync_call_llm):
        return await asyncio.to_thread(_orig, msgs, tools)
    return _wrapper


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

    def __init__(self, console, store=None):
        """初始化 Kernel。

        Args:
            console: SystemConsole 实例，用于推送系统事件。
            store: SessionStore（可选）。None 时持久化关闭，行为与现状一致。
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
        # Batch 3: 创建 MessageBus。
        # input_queues 开始时为空，后续 spawn 中增量添加。
        # MessageBus 持有引用——新 agent queue 自动可见。
        from .message_bus import MessageBus
        self.message_bus = MessageBus(
            input_queues=self.input_queues,
            console=console,
        )
        self._shutdown: bool = False

        # Batch 2 遗留：_pending_subscriptions 不再使用。
        # 订阅关系现在直接注册到 MessageBus（见 spawn_from_script）。
        self._pending_subscriptions: list[tuple[str, str]] = []

        # 持久化：进程级 SessionStore（内核机制，非 DI）
        self._store = store

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def spawn_root(self, harness, call_llm=None) -> str:
        """创建并启动 Mode A 根 agent（= _create_root + 控制台事件 + _start_agent）。

        Args:
            harness: 装配好的 Harness 实例。
            call_llm: async LLM callable。已在 Runtime 入口层做过
                      sync→async 桥接。None 表示无 LLM（测试模式）。

        Returns:
            pid: 固定为 "root"。
        """
        runtime = self._create_root(harness, call_llm)

        # 推送 SystemConsole 事件（仅公开 spawn 路径；boot resume 不重发）
        asyncio.create_task(
            self._console.send(AgentSpawned(pid=runtime.pid, parent=None))
        )

        self._start_agent(runtime.pid)
        logger.info(f"spawn_root: pid='{runtime.pid}' created and started")
        return runtime.pid

    def _create_root(self, harness, call_llm=None) -> 'AgentRuntime':
        """创建根 agent 但不启动（boot 四步序的第 1 步使用）。

        步骤与拆分前 spawn_root 的创建侧完全一致：建 runtime、挂 adapter、
        SessionLog 先行接 orchestrator、注册进程表、记录 workflow。
        不推送 AgentSpawned、不创建运行 task。
        """
        from .agent_runtime import AgentRuntime

        pid = "root"

        # 1. 创建 AgentRuntime
        runtime = AgentRuntime(
            pid=pid,
            mode="continuous",  # Mode A root 强制 continuous
            harness=harness,
            kernel=self,
            parent=None,
        )

        # 2. 挂载 adapter — 优先从 DI 容器 resolve，fallback 到 KBA
        runtime.adapter = _resolve_adapter(
            harness.container, pid=pid, kernel=self, runtime=runtime
        )

        # 2a. Inject Runtime tools
        self._inject_runtime_tools(harness.container, pid=pid)

        # 3. 初始化 orchestrator（先建 SessionLog——orchestrator 与之共享 history）
        session_log = self._make_session_log(pid, harness, runtime, parent=None)
        runtime._init_orchestrator(call_llm=call_llm, session_log=session_log)

        # 4. 注册到进程表
        self.runtime_table[pid] = runtime
        self.input_queues[pid] = asyncio.Queue()

        # 5. 记录 workflow（创建侧事实，boot Mode A 同样适用）
        self.workflow_table["wf_root"] = [pid]
        runtime.workflow_flag = "wf_root"

        return runtime

    def _start_agent(self, pid: str) -> None:
        """启动已注册 agent 的运行 task（boot 四步序的第 4 步使用）。

        即原 spawn_root 的 asyncio.create_task(...) + done_callback。
        需要运行中的事件循环。
        """
        runtime = self.runtime_table[pid]
        task = asyncio.create_task(runtime.run())
        self._tasks[pid] = task
        task.add_done_callback(
            lambda t, r=runtime: asyncio.create_task(
                self._on_agent_finished(r)
            )
        )

    # ------------------------------------------------------------------
    # boot —— fresh/resume 统一入口
    # ------------------------------------------------------------------

    async def boot(self, conv_id=None, *, force: bool = False,
                   harness=None, call_llm=None, script_path=None):
        """统一启动入口：conv_id 为 None → fresh；否则 resume。

        fresh：begin_session(None) + 原 spawn 路径（与现状一致）。
        resume 四步序（设计第五节 Boot-Resume 时序图）：
          1. 创建所有（Mode A 仅 root；Mode B 重跑脚本，不启动不投 entry）
          2. 种子（replay → 物理截断半行 → seed）
          3. 配对修复（T12 的 plan_redelivery 注入点，此处预留调用）
          4. 启动所有（_start_agent）

        接管顺序约束：begin_session + restore_sequencer 必须先于任何
        create_log（Sequencer 按引用捕获，restore 会替换对象）。

        Args:
            conv_id: None → fresh；否则恢复该会话。
            force: 强制接管（owner 冲突/manifest 硬失败/脚本 sha1 不一致
                   降级为告警）。
            harness: Mode A 根 agent 的装配实例。
            call_llm: async LLM callable。
            script_path: Mode B 重跑的 workflow 脚本路径。

        Returns:
            BootReport（conv_id/mode/status_before/replayed/warnings 等）。

        Raises:
            BootError: resume 但 store 缺失（持久化未启用）、会话不存在、
                Mode 组合模糊、廉价校验失败或 manifest 硬校验失败等。
                接管语义：begin_session 之后发生的失败（如 manifest 硬校验）
                会在 index 留下 active + 本进程 owner 的残留——本进程退出后
                该 owner 成为死主，后续 boot 自动放行；同进程重试需 force。
            SessionOwnerConflict: 会话被另一个存活进程持有（未 force）。
        """
        from ..core.session.boot import BootReport
        from ..core.session.exceptions import BootError

        if conv_id is None:
            # fresh 路径：与现状完全一致（store 可缺失），并记录 Mode B 脚本 meta
            script_meta = None
            if script_path is not None:
                import hashlib
                import os as _os
                if (not _os.path.isfile(script_path)
                        or not _os.access(script_path, _os.R_OK)):
                    raise BootError(f"脚本文件不存在或不可读: {script_path}")
                with open(script_path, "rb") as _fh:
                    sha1 = hashlib.sha1(_fh.read()).hexdigest()
                script_meta = {"path": script_path, "sha1": sha1}
            if self._store is not None:
                self._store.begin_session(None, script=script_meta)
            if script_path is not None:
                self.spawn_from_script(script_path, parent=None)
            else:
                self.spawn_root(harness, call_llm)
            return BootReport(
                conv_id=self._store.conv_id if self._store else "",
                mode="fresh")

        if self._store is None:
            raise BootError(
                f"会话持久化未启用（store 缺失），无法 resume '{conv_id}'")

        import os as _os
        if not conv_id or _os.path.basename(conv_id) != conv_id \
                or conv_id in (".", ".."):
            raise BootError(f"非法会话 ID：'{conv_id}'")

        return await self._boot_resume(
            conv_id, force=force, harness=harness,
            call_llm=call_llm, script_path=script_path)

    async def _boot_resume(self, conv_id: str, *, force: bool, harness,
                           call_llm, script_path):
        """resume 主路径：所有权校验 → 重放 → manifest 校验 → 接管 → 四步序。"""
        from ..core.session.boot import BootReport, used_tool_names
        from ..core.session.exceptions import BootError, SessionOwnerConflict
        from ..core.session.ids import pid_alive, pid_from_token
        from ..core.session.manifest import diff_manifest
        from ..core.session.replay import (
            RESUME_MARKER, measure_lsn_gap, plan_redelivery, scan_session,
        )
        from ..interfaces.types import Message

        store = self._store
        index = store.read_index(conv_id)
        conv_dir = store.root_path / conv_id
        if index is None and not conv_dir.is_dir():
            raise BootError(f"会话 '{conv_id}' 不存在于 {store.root_path}")
        index = index or store.rebuild_index(conv_id)

        report = BootReport(conv_id=conv_id, mode="resume",
                            status_before=index.get("status", "unknown"))

        # ── 所有权接管（令牌 + pid 活性 + force）──
        # owner 双形状兼容：begin_session 写 dict，旧投影可能是纯字符串
        owner = index.get("owner")
        token = owner.get("token") if isinstance(owner, dict) else owner
        if not isinstance(token, str):
            token = None
        if token and not force:
            owner_pid = pid_from_token(token)
            if owner_pid is None:
                report.warnings.append("owner token 不可解析，跳过活性检查")
            else:
                try:
                    owner_alive = pid_alive(owner_pid)
                except NotImplementedError:
                    # 平台不支持探活 → 无法确认存活，保守放行
                    owner_alive = False
                    report.warnings.append(
                        "当前平台不支持进程探活，owner 活性未校验")
                if owner_alive:
                    raise SessionOwnerConflict(
                        f"会话 '{conv_id}' 正被进程 {owner_pid} 持有；"
                        f"如确认该进程已退出，使用 --force 强制接管。")

        # ── 重放（读侧先行，不建文件）──
        replays = scan_session(conv_dir)
        if not replays:
            raise BootError(f"会话 '{conv_id}' 没有任何 agent 日志")
        report.lsn_gap = measure_lsn_gap(replays)
        if report.lsn_gap:
            report.warnings.append(
                f"LSN 空洞 {report.lsn_gap}：上次运行有 {report.lsn_gap} 个"
                f"事件已发号未落盘（崩溃损失）")
        for pid, r in replays.items():
            if r.truncated_bytes:
                report.warnings.append(
                    f"agent '{pid}' 日志尾部截断 {r.truncated_bytes} 字节（半行）")

        # ── Mode 判定：有 script 记录 → Mode B；否则 Mode A ──
        script_meta = index.get("script")
        if script_meta is not None and script_path is None:
            raise BootError(
                f"会话 '{conv_id}' 由脚本创建，恢复需提供 script_path")
        mode_b = script_meta is not None
        if script_path is not None and script_meta is None:
            report.warnings.append(
                "提供了 script_path 但该会话无脚本记录，按 Mode A 恢复")

        # ── 接管前廉价校验：begin_session 一旦接管（index 写 active +
        # 本进程 owner），后续失败留下的活主占用会让同进程重试撞自己的
        # SessionOwnerConflict——能在接管前拦的全部拦掉 ──
        if mode_b:
            import os
            if (not os.path.isfile(script_path)
                    or not os.access(script_path, os.R_OK)):
                raise BootError(f"脚本文件不存在或不可读: {script_path}")
            self._verify_script_sha1(script_meta, script_path, force, report)
        else:
            if replays.get("root") is None:
                raise BootError(f"会话 '{conv_id}' 缺少 root 日志，无法恢复")
            if harness is None:
                raise BootError(
                    f"Mode A 恢复会话 '{conv_id}' 需要 harness 实例")

        # ── 接管会话（index owner 更新 + sequencer 恢复）──
        # 必须先于任何 create_log：Sequencer 按引用捕获，restore 会替换对象
        store.begin_session(conv_id)
        store.restore_sequencer(max(r.max_lsn for r in replays.values()) + 1)

        # ── 第 1 步：创建所有（不启动、不投 entry、不重发控制台事件）──
        before = set(self.runtime_table.keys())
        script_entry_prompts = None
        if mode_b:
            created = self._create_agents_from_script(script_path, parent=None)
            main_pid = created["created_pids"][0]   # step 3 校验保证非空
            script_entry_prompts = {
                a["pid"]: a["entry_prompt"]
                for a in created.get("agents", [])
                if a.get("entry_prompt")
            }
        else:
            self._create_root(harness, call_llm)
            main_pid = "root"
        # 只重启本次创建的 agent——复用 kernel 时无关 FINISHED agent 不受影响
        restarted = set(self.runtime_table.keys()) - before

        # ── manifest 分级校验（创建之后、种子之前：_inject_runtime_tools 的
        # composite 已注入容器，探针经 build_tool_router 现算完整工具集）──
        probe: dict = {}
        main_runtime = self.runtime_table.get(main_pid)
        probe_harness = getattr(main_runtime, "_harness", None) or harness
        if probe_harness is not None:
            probe = self._probe_manifest(probe_harness, main_runtime)
        if not probe:
            # 探针失败永远不得变成硬阻断（失败方向向下）
            report.warnings.append("manifest 探针不可用，跳过分级校验")
        else:
            diff = diff_manifest(index.get("manifest"), probe,
                                 used_tool_names=used_tool_names(replays))
            report.warnings.extend(diff.soft)
            if diff.hard and not force:
                raise BootError(
                    "manifest 硬校验失败：\n  " + "\n  ".join(diff.hard)
                    + "\n如确认继续，使用 --force。")
            if diff.hard:
                report.warnings.extend(f"[force 降级] {h}" for h in diff.hard)
        for pid in sorted(restarted):
            r = replays.get(pid)
            if r is None:
                continue                       # 新 agent（Mode B 可能出现）
            session_log = store.log_for(pid)
            if session_log is None:
                continue
            if r.truncated_bytes:
                # 物理截断须在 writer 懒打开（首次 flush）之前——此刻尚无 writer
                log_path = store.agent_log_path(pid)
                with open(log_path, "r+b") as fh:
                    fh.truncate(log_path.stat().st_size - r.truncated_bytes)
            session_log.seed(history=r.history,
                             tool_call_records=r.tool_call_records,
                             last_seq=r.last_seq, last_lsn=r.max_lsn)
            if r.interrupted_at:
                # resume_marker：只在内存合成，永不落盘（幂等）
                session_log.history.append(Message(
                    role="user",
                    content=RESUME_MARKER.format(call_id=r.interrupted_at)))
                report.warnings.append(
                    f"agent '{pid}' 在工具调用 {r.interrupted_at} 处中断，"
                    f"已注入恢复标记")
            report.replayed.append(pid)

        # ── 第 3 步：配对修复 ──
        for plan in plan_redelivery(replays, restarted,
                                    script_entry_prompts=script_entry_prompts):
            store_key = plan.dedup_key
            if (plan.target in restarted and store_key
                    and store_key not in report.redelivered):
                self.send_input(plan.target, plan.request)
                report.redelivered.append(store_key)

        # ── 第 4 步：启动所有 ──
        for pid in sorted(restarted):
            if pid not in self._tasks or self._tasks[pid].done():
                self._start_agent(pid)

        return report

    @staticmethod
    def _verify_script_sha1(script_meta: dict, script_path: str,
                            force: bool, report) -> None:
        """Mode B 前置校验：脚本 sha1 不一致 → 硬失败（--force 降级）。"""
        import hashlib
        from ..core.session.exceptions import BootError

        with open(script_path, "rb") as fh:
            actual = hashlib.sha1(fh.read()).hexdigest()
        expect = script_meta.get("sha1")
        if expect and actual != expect:
            msg = (f"脚本已修改：{script_path}\n  记录: {expect}\n"
                   f"  当前: {actual}")
            if not force:
                raise BootError(msg + "\n如确认继续，使用 --force。")
            report.warnings.append(f"[force 降级] {msg}")

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

    def _signal_all_exit(self) -> None:
        """向所有非 FINISHED/TERMINATING agent 置 should_exit + 推 sentinel。

        CommandExit 清扫与 Runtime finally 的再清扫共用：/exit 落地后在途
        LLM 响应仍可能 spawn 出新 agent（post-sweep spawn 窗口），收尾前
        须再扫一次保证无孤儿。幂等——重复调用只是多推一个 sentinel。
        """
        from .agent_runtime import AgentState

        for pid, agent in self.runtime_table.items():
            if agent.state not in (
                AgentState.FINISHED, AgentState.TERMINATING
            ):
                agent.should_exit = True
                if pid in self.input_queues:
                    self.input_queues[pid].put_nowait(__EXIT_SENTINEL__)

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
        self, script_path: str, parent=None, *,
        autostart: bool = True, deliver_entry: bool = True,
    ) -> dict:
        """Load a workflow script, create multiple agents and start them.

        Args:
            script_path: Absolute path to the workflow script.
            parent: Parent AgentRuntime, None for top-level agents.
            autostart: True（默认）时创建后立即推送 AgentSpawned 并启动 task；
                       False 仅创建（boot 四步序的第 1 步使用，由 boot 统一
                       _start_agent，且 resume 不重发控制台 spawn 事件）。
            deliver_entry: True（默认）时启动后投递 entry_prompt；False 跳过
                       （boot resume 的 entry 重投由配对修复负责，见 T12）。
            默认 True/True 与拆分前行为逐字节等价。

        Returns:
            {"workflow_flag": str, "agents": [{"pid": str, "parent": str|None,
                                               "metadata": dict}]}

        Raises:
            FileNotFoundError: Script file cannot be loaded.
            ValueError: No @agent declarations, or subscribe references
                        unknown agent names.
            RuntimeError: Kernel 正在关闭（_shutdown=True）。/exit 后在途
                        LLM 响应仍可能执行 spawn_workflow——拒绝 spawn，
                        避免新 agent 收不到退出信号成为孤儿。
        """
        if self._shutdown:
            raise RuntimeError("kernel is shutting down, spawn rejected")

        from . import decorators

        created = self._create_agents_from_script(script_path, parent)
        workflow_flag = created["workflow_flag"]
        created_pids = created["created_pids"]

        # ── Step 6/7: 推送 SystemConsole 事件 + 启动 asyncio Tasks ──
        if autostart:
            try:
                _ = asyncio.get_running_loop()
                _has_loop = True
            except RuntimeError:
                _has_loop = False

            if _has_loop:
                for name in created_pids:
                    asyncio.create_task(
                        self._console.send(
                            AgentSpawned(
                                pid=name, parent=parent.pid if parent else None
                            )
                        )
                    )
                for name in created_pids:
                    self._start_agent(name)

        # ── Step 9: Deliver entry_prompts ──
        if deliver_entry:
            for name, blueprint in decorators._agent_registry.items():
                # 确定性 key：同一 spawn 内幂等；同会话重复 spawn 同名
                # workflow 会产生相同 msg_id 的重复 edge（T12 按 msg_id
                # 去重，语义可接受）。
                msg_id = f"spawn_entry:{name}"
                self.send_input(
                    name,
                    UserRequest(
                        text=blueprint["entry_prompt"],
                        metadata={
                            "workflow_flag": workflow_flag,
                            "type": "spawn_entry",
                            "msg_id": msg_id,
                        },
                    ),
                )
                # 发送方事实：entry_prompt 由父 agent（若存在）发起。
                if parent is not None and self._store is not None:
                    parent_log = self._store.log_for(parent.pid)
                    if parent_log is not None:
                        parent_log.record_edge(
                            msg_id=msg_id, to=name, kind="spawn_entry",
                            text=blueprint["entry_prompt"],
                        )

        logger.info(
            f"spawn_from_script: workflow_flag='{workflow_flag}' "
            f"created {len(created_pids)} agent(s): {created_pids}"
        )

        # ── Step 10: Return ──
        return {
            "workflow_flag": workflow_flag,
            "agents": created["agents"],
        }

    def _create_agents_from_script(self, script_path: str, parent=None) -> dict:
        """加载 workflow 脚本并创建全部 agent，但不启动（boot 第 1 步使用）。

        覆盖原 spawn_from_script 的 step 1–5.5（workflow_flag、加载、校验、
        创建 runtime、注册订阅）与 step 8（workflow_table 记录——创建侧事实）。
        不推送 AgentSpawned、不创建运行 task、不投递 entry_prompt。
        """
        import sys
        import importlib.util
        from . import decorators
        from .agent_runtime import AgentRuntime, AgentState

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

        # Known virtual publishers (not declared as @agent, but valid
        # as publish targets — e.g. "user" for group chat human input).
        _VIRTUAL_PUBLISHERS = {"user"}

        for sub in decorators._subscription_registry:
            if sub.subscriber not in decorators._agent_registry:
                raise ValueError(
                    f"subscribe('{sub.subscriber}') references unknown "
                    f"agent. Known: {list(decorators._agent_registry.keys())}"
                )
            if (sub.publisher not in decorators._agent_registry
                    and sub.publisher not in _VIRTUAL_PUBLISHERS):
                raise ValueError(
                    f"subscribe(...).to('{sub.publisher}') references unknown "
                    f"agent. Known: {list(decorators._agent_registry.keys())}"
                )

        # ── Step 4 (暂存): 暂存订阅关系，延后注册到 MessageBus ──
        # 不在此时注册——如果 step 5 的 agent 创建失败，
        # 已注册的订阅关系成为孤立引用。
        _pending_subs = list(decorators._subscription_registry)

        # ── Step 5: Create AgentRuntime for each @agent ──
        created_pids: list[str] = []
        agent_results: list[dict] = []

        for name, blueprint in decorators._agent_registry.items():
            try:
                # 5a. Call factory to get Harness
                harness = blueprint["factory"]()

                # 5b. Determine mode
                # subscribe 声明中出现的 agent 全部 continuous。
                # workflow 不自动退出——用户 /end 或父 agent end_workflow 来终止。
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

                # 5d. Mount adapter — DI resolve or fallback to KBA
                runtime.adapter = _resolve_adapter(
                    harness.container, pid=name, kernel=self, runtime=runtime
                )

                # 5e. Extract and bridge call_llm
                call_llm = getattr(harness, 'call_llm', None)
                if call_llm and not asyncio.iscoroutinefunction(call_llm):
                    call_llm = make_async_llm(call_llm)

                # 5f. Inject Runtime tools
                self._inject_runtime_tools(harness.container, pid=name)

                # 5g. Initialize orchestrator（SessionLog 先行，理由同 spawn_root）
                session_log = self._make_session_log(name, harness, runtime, parent)
                runtime._init_orchestrator(call_llm=call_llm,
                                           session_log=session_log)

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
                runtime.workflow_flag = workflow_flag

                # 5i. Record parent-child relationship
                if parent is not None:
                    parent.children.append(name)

                created_pids.append(name)
                agent_results.append({
                    "pid": name,
                    "parent": parent.pid if parent else None,
                    "metadata": blueprint.get("metadata", {}),
                    "entry_prompt": blueprint.get("entry_prompt"),
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

        # ── Step 5.5: 注册订阅关系到 MessageBus ──
        # 放在 agent 全部创建成功后——如果 step 5 失败并 rollback，
        # 不会留下孤立订阅。
        for sub in _pending_subs:
            self.message_bus.subscribe(sub.subscriber, sub.publisher)
            logger.debug(
                f"spawn_from_script: subscribed "
                f"'{sub.subscriber}' → '{sub.publisher}'"
            )

        # ── Step 8: Record workflow mapping（创建侧事实）──
        self.workflow_table[workflow_flag] = created_pids.copy()

        return {
            "workflow_flag": workflow_flag,
            "created_pids": created_pids,
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

    def _make_session_log(self, pid: str, harness, runtime, parent):
        """为 agent 创建 SessionLog（须在 _init_orchestrator 之前，以共享 history）。

        store 为 None（持久化关闭）时仍创建纯内存 SessionLog——
        "唯一咽喉点"语义不随配置分叉。

        只创建并返回 log；KBA 的发送方事实落盘改为惰性解析
        （经 kernel._store.log_for(pid)），不再此处接线。
        """
        from ..core.session.session_log import SessionLog

        if self._store is None:
            log = SessionLog(conv_id="ephemeral", pid=pid, store=None)
        else:
            log = self._store.create_log(
                pid,
                parent=parent.pid if parent else None,
                manifest_provider=lambda: self._probe_manifest(harness, runtime),
            )

        return log

    def _probe_manifest(self, harness, runtime) -> dict:
        """计算当前装配清单（T9 接入 compute_manifest；失败一律返回 {}）。

        工具集优先取 runtime 的 ``_cached_tools``（phase_init 缓存）；
        boot 探针时 phase_init 尚未运行（缓存为空），改经 build_tool_router
        现算——此时 _inject_runtime_tools 的 composite 已注入容器，
        router 级并集 = 用户工具 ∪ runtime 工具 ∪ MCP 工具。
        """
        try:
            from ..core.session.manifest import compute_manifest
            from ..core.async_orchestrator import build_tool_router
            tools = []
            orch = getattr(runtime, "_orchestrator", None)
            if orch is not None and orch._cached_tools:
                tools = orch._cached_tools
            else:
                tools = build_tool_router(harness.container)[1]
            return compute_manifest(
                harness.container, cached_tools=tools,
                call_llm=getattr(harness, "call_llm", None),
            )
        except Exception as e:
            logger.debug("_probe_manifest failed for '%s': %s",
                         getattr(runtime, "pid", "?"), e)
            return {}

    # ------------------------------------------------------------------
    # 内部方法（Batch 1 stub，后续 batch 升级）
    # ------------------------------------------------------------------

    async def _on_agent_finished(self, runtime: 'AgentRuntime') -> None:
        """agent FINISHED 时的回调（由 Task.done_callback 触发）。

        执行顺序：
        1. 推送 AgentFinished 到 SystemConsole
        1.5. 持久化收尾（幂等）：session_end 兜底 + index 投影更新
        2. 默认订阅：通知父 agent（child_finished），含去重逻辑
        3. 级联终止：通过 MessageBus 查询订阅者，推送 __EXIT_SENTINEL__。
           父 agent 被显式排除（不受级联影响，顶层设计 Section 四.5）。
        4. 清理订阅表：remove_publisher
        """
        from .agent_runtime import AgentState

        duration = time.time() - runtime.started_at

        # ── 1. 推送 SystemConsole ──
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

        # ── 1.5 持久化收尾（幂等）：session_end 兜底 + index 投影更新 ──
        if self._store is not None:
            try:
                await self._store.finalize_agent(
                    runtime.pid,
                    final_output=runtime.last_output,
                    execution_time=duration,
                    status="paused",
                )
            except Exception as e:
                logger.warning(
                    f"finalize_agent('{runtime.pid}') failed: {e}")

        # ── 2. 默认订阅：通知父 agent ──
        # 去重：如果父 agent 显式 subscribe 了本 agent，跳过 child_finished。
        # 父通过 subscribe 流已经收到了子 agent 的输出，无需重复通知。
        if runtime.parent and runtime.parent.state != AgentState.FINISHED:
            parent_subscribed = (
                runtime.parent.pid
                in self.message_bus.get_subscribers_of(runtime.pid)
            )
            if not parent_subscribed:
                msg_id = new_msg_id()
                text = (
                    f"[{runtime.pid}] "
                    f"{'异常退出' if runtime.error else '已完成'}。\n"
                    f"{runtime.last_output}"
                )
                self.send_input(runtime.parent.pid, UserRequest(
                    text=text,
                    metadata={
                        "type": "child_finished",
                        "pid": runtime.pid,
                        "workflow_flag": runtime.workflow_flag,
                        "duration": duration,
                        "error": runtime.error,
                        "from": runtime.pid,
                        "msg_id": msg_id,
                    },
                ))
                logger.debug(
                    f"_on_agent_finished: child_finished sent to "
                    f"parent='{runtime.parent.pid}'"
                )
            else:
                logger.debug(
                    f"_on_agent_finished: parent '{runtime.parent.pid}' "
                    f"already subscribed to '{runtime.pid}', "
                    f"skipping child_finished (dedup)"
                )

        # ── 3. 级联终止：通知显式订阅者 ──
        # 关键：父 agent 不受级联影响。
        # 顶层设计 Section 四.5 明确约定。
        parent_pid = runtime.parent.pid if runtime.parent else None
        subscribers = self.message_bus.get_subscribers_of(runtime.pid)
        for sub_pid in subscribers:
            # 跳过父 agent —— 父不受级联影响
            if sub_pid == parent_pid:
                logger.debug(
                    f"_on_agent_finished: skipping parent '{sub_pid}' "
                    f"in cascade (parent不受级联影响)"
                )
                continue

            sub_runtime = self.runtime_table.get(sub_pid)
            if sub_runtime and sub_runtime.state not in (
                AgentState.FINISHED, AgentState.TERMINATING
            ):
                sub_runtime.should_exit = True
                if sub_pid in self.input_queues:
                    self.input_queues[sub_pid].put_nowait(__EXIT_SENTINEL__)
                    logger.info(
                        f"_on_agent_finished: cascade sentinel sent "
                        f"to '{sub_pid}' (subscribed to '{runtime.pid}')"
                    )

        # ── 4. 清理订阅表 ──
        self.message_bus.remove_publisher(runtime.pid)

    async def _monitor_quiescence(self) -> None:
        """静默检测监控协程。

        每秒检查一次：如果所有非 FINISHED agent 都处于 idle 状态
        （在 RUNNING 状态且在 adapter.receive() 中等待），
        则向全体推送 __EXIT_SENTINEL__ 触发优雅退出。

        Mode B 的核心结束机制：当所有 agent 完成工作进入 WAITING_INPUT，
        无人会再产生输出时，静默检测自动终止所有 agent。
        """
        from .agent_runtime import AgentState

        logger.info("_monitor_quiescence: started")
        while not self._shutdown:
            await asyncio.sleep(1)

            non_finished = [
                r for r in self.runtime_table.values()
                if r.state != AgentState.FINISHED
            ]

            if not non_finished:
                logger.info(
                    "_monitor_quiescence: all agents FINISHED, exiting"
                )
                return

            # 所有非 FINISHED agent 都在等待输入？
            all_idle = all(
                r._idle_for_quiescence() for r in non_finished
            )
            if all_idle:
                logger.info(
                    f"_monitor_quiescence: all {len(non_finished)} "
                    f"non-finished agent(s) idle, pushing sentinel"
                )
                for r in non_finished:
                    r.should_exit = True
                    if r.pid in self.input_queues:
                        self.input_queues[r.pid].put_nowait(
                            __EXIT_SENTINEL__
                        )
                return

    async def _handle_system_input(self) -> None:
        """系统输入处理循环。

        Batch 4 完整实现: 解析并分发全部 7 种 SystemCommand。
        """
        from .agent_runtime import AgentState
        from .types import (
            CommandTalk, CommandKill, CommandListAgents,
            CommandEndWorkflow, CommandExit, CommandTalkDirect,
            CommandError, AgentsListed, AgentStateChanged,
            __EXIT_SENTINEL__,
        )
        from ..interfaces.types import UserRequest

        logger.info("_handle_system_input: started")
        while not self._shutdown:
            command = await self._console.receive()

            # ── CommandTalk: 纯文本路由 ──
            if isinstance(command, CommandTalk):
                if command.pid in self.runtime_table:
                    self.send_input(
                        command.pid,
                        UserRequest(text=command.text),
                    )
                else:
                    await self._console.send(CommandError(
                        command=command.text[:50],
                        error=f"pid '{command.pid}' 不存在",
                    ))

            # ── CommandKill: 终止单个 agent ──
            # 注意：AgentStateChanged 是乐观先行发出的——kill() 只设置
            # should_exit=True + 推送 sentinel，agent 的 state 要到其 run()
            # 进入 finally 块后才变成 TERMINATING。
            elif isinstance(command, CommandKill):
                if command.pid in self.runtime_table:
                    agent = self.runtime_table[command.pid]
                    if agent.state == AgentState.FINISHED:
                        logger.debug(
                            f"_handle_system_input: /kill '{command.pid}' "
                            f"already FINISHED, skipping"
                        )
                    else:
                        self.kill(command.pid)
                        await self._console.send(AgentStateChanged(
                            pid=command.pid,
                            old=agent.state.value,
                            new="terminating",
                        ))
                else:
                    await self._console.send(CommandError(
                        command=f"/kill {command.pid}",
                        error=f"pid '{command.pid}' 不存在",
                    ))

            # ── CommandListAgents: 列出所有 agent ──
            elif isinstance(command, CommandListAgents):
                info = self.list_agents()
                await self._console.send(AgentsListed(agents=info))

            # ── CommandEndWorkflow: 终止整个 workflow ──
            elif isinstance(command, CommandEndWorkflow):
                if command.flag in self.workflow_table:
                    killed = self.end_workflow(command.flag)
                    for pid in killed:
                        agent = self.runtime_table.get(pid)
                        # 仅为非 FINISHED/TERMINATING agent 发事件
                        # （kill() 对已结束 agent 是 no-op）
                        if agent and agent.state not in (
                            AgentState.FINISHED, AgentState.TERMINATING
                        ):
                            await self._console.send(AgentStateChanged(
                                pid=pid,
                                old=agent.state.value,
                                new="terminating",
                            ))
                else:
                    await self._console.send(CommandError(
                        command=f"/end {command.flag}",
                        error=f"workflow flag '{command.flag}' 不存在",
                    ))

            # ── CommandExit: 优雅退出 ──
            elif isinstance(command, CommandExit):
                logger.info("_handle_system_input: /exit received")
                self._signal_all_exit()
                self._shutdown = True
                return  # 退出循环

            # ── CommandTalkDirect: 定向消息（Mode B） ──
            elif isinstance(command, CommandTalkDirect):
                target = self.runtime_table.get(command.pid)
                if target is None:
                    await self._console.send(CommandError(
                        command=f"/talk {command.pid}",
                        error=f"pid '{command.pid}' 不存在",
                    ))
                elif target.state == AgentState.FINISHED:
                    await self._console.send(CommandError(
                        command=f"/talk {command.pid}",
                        error=f"Agent '{command.pid}' 已结束 (FINISHED)，"
                              f"无法接收消息",
                    ))
                else:
                    self.send_input(
                        command.pid,
                        UserRequest(
                            text=command.text,
                            metadata={"from": "user", "type": "talk"},
                        ),
                    )

            # ── CommandError (由 CliConsole 解析失败产生) ──
            elif isinstance(command, CommandError):
                await self._console.send(command)

        logger.info("_handle_system_input: exited loop")
