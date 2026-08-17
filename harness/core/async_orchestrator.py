"""AsyncLifecycleOrchestrator — 异步生命周期编排器。

将现有 LifecycleOrchestrator 的三阶段编排逻辑迁移为 async 版本。
与同步版的差异：
- _phase_loop() 仅执行一轮（内循环），外层多轮循环由 AgentRuntime.run() 控制
- call_llm / adapter.receive() / adapter.send() 均为 async
- adapter 在构造时显式传入，不走 DI 容器解析
- call_llm 在构造时即为 async callable

不改动现有 LifecycleOrchestrator。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .container import DIContainer
from .exceptions import ComponentNotRegisteredError, OrchestratorError
from .tool_router import ToolRouter
from ..hooks import (
    EVENT_AFTER_ASSEMBLE,
    EVENT_AFTER_GUIDE_GENERATION,
    EVENT_AFTER_LLM_CALL,
    EVENT_AFTER_SENSOR,
    EVENT_AFTER_TOOL_EXECUTE,
    EVENT_BEFORE_ASSEMBLE,
    EVENT_BEFORE_GUIDE_GENERATION,
    EVENT_BEFORE_LLM_CALL,
    EVENT_BEFORE_TOOL_EXECUTE,
    EVENT_ON_ERROR,
    EVENT_ON_SESSION_END,
    HookManager,
)
from ..interfaces import (
    ContextAssembler,
    GuideProvider,
    MCPAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from ..interfaces.async_input_adapter import AsyncInputAdapter
from ..interfaces.async_call_llm import AsyncCallLLM
from ..interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    StopEvent,
    SystemState,
    TextEvent,
    ThinkingEvent,
    ToolCall,
    ToolCallEvent,
    ToolCallRecord,
    ToolDefinition,
    ToolResult,
    ToolResultEvent,
    Trajectory,
    UserRequest,
)
from ..messaging import (
    build_assistant_message,
    build_tool_result_message,
    messages_to_dicts,
    tool_definitions_to_openai,
)

logger = logging.getLogger(__name__)


def _request_meta(request: UserRequest) -> dict:
    """提取持久化所需的交互元数据（from/msg_id/type/workflow_flag）。

    只白名单提取——metadata 是用户扩展桶，不全量落盘。
    """
    return {
        k: request.metadata[k]
        for k in ("from", "msg_id", "type", "workflow_flag")
        if k in request.metadata
    }


# ---------------------------------------------------------------------------
# AsyncLifecycleOrchestrator
# ---------------------------------------------------------------------------


class AsyncLifecycleOrchestrator:
    """异步生命周期编排器 — 三阶段固定顺序驱动组件调用。

    与 LifecycleOrchestrator 的区别：
    - _phase_loop() 仅执行一轮（内循环），外层多轮循环由 AgentRuntime.run() 控制
    - call_llm / adapter.receive() / adapter.send() 均为 async
    - adapter 在构造时显式传入，不走 DI 容器解析
    - call_llm 在构造时即为 async callable

    用法::

        orch = AsyncLifecycleOrchestrator(
            container, adapter=kba, call_llm=async_llm
        )
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        traj = orch._build_trajectory()
        await orch._phase_end(traj)
    """

    # 退出关键词
    _EXIT_KEYWORD = "/exit"

    # 内层循环最大迭代次数（防止无限 tool-calling 循环）
    _MAX_TOOL_ITERATIONS = 100

    def __init__(
        self,
        container: DIContainer,
        *,
        adapter: AsyncInputAdapter,
        # call_llm 允许为 None 以兼容以下场景：
        # - 单元测试（验证编排流程不调 LLM）
        # - Hook 开发（在 call_llm 前拦截并注入自定义行为）
        # 与同步版 LifecycleOrchestrator 的 Optional 约定一致
        call_llm: Optional[AsyncCallLLM] = None,
        # session_log 为 None 时行为与插桩前完全一致（无持久化路径）
        session_log=None,
    ):
        """初始化异步编排器。

        Args:
            container: DI 容器，用于解析可选组件（GuideProvider、
                       MemoryBackend、ContextAssembler、Sensor 等）。
            adapter: 异步 I/O 适配器（显式传入，不走 DI 容器解析）。
                     每个 AgentRuntime 有独立的 KBA 实例。
            call_llm: async LLM 调用函数。
                      签名: (messages: List[Dict], tools: List[Dict]) → Response。
                      为 None 时 tool_use 循环不可用，但仍可验证编排流程。
        """
        self.container = container
        self.call_llm = call_llm
        self._adapter = adapter

        # 会话状态
        self._session_id: str = ""
        self._history: List[Message] = []
        self._tool_call_records: List[ToolCallRecord] = []
        self._start_time: float = 0.0
        self._should_exit_flag: bool = False
        self._system_state: SystemState = SystemState()

        # Hook 管理器
        self._hook_manager: HookManager = HookManager()

        # 阶段一缓存的不可变数据
        self._cached_guides: Optional[GuidesBundle] = None
        self._cached_tools: List[ToolDefinition] = []
        self._cached_tool_router: Optional[ToolRouter] = None

        # SessionLog —— _history/_tool_call_records 的唯一变异点。
        # 同一对象引用两视图：orchestrator 的字段直接重绑定为 SessionLog 的列表，
        # 读取方（AgentRuntime._extract_last_output、assemble、sensor）零改动。
        self._session_log = session_log
        if session_log is not None:
            self._history = session_log.history
            self._tool_call_records = session_log.tool_call_records

    # ------------------------------------------------------------------
    # Hook 注册
    # ------------------------------------------------------------------

    def register_hook(self, event: str, hook) -> None:
        """注册一个生命周期 Hook。

        代理到内部 HookManager。

        Args:
            event: 生命周期事件名。
            hook: Hook 函数。
        """
        self._hook_manager.register(event, hook)

    # ------------------------------------------------------------------
    # 阶段一：会话初始化
    # ------------------------------------------------------------------

    async def _phase_init(self) -> AssemblyContext:
        """阶段一：会话初始化。

        步骤：
        1. await adapter.receive() → UserRequest
        2. resolve GuideProvider → get_guides() → GuidesBundle（缓存）
        3. resolve MemoryBackend → search() → List[MemoryItem]
        4. create ToolRouter → resolve SystemToolProvider/MCPAdapter
           → list_tools() → List[ToolDefinition]（缓存）
        5. 构建并返回 AssemblyContext

        Returns:
            AssemblyContext: 初始化的上下文对象。

        Raises:
            ComponentNotRegisteredError: 必需的组件未注册。
        """
        self._start_time = time.time()
        self._system_state.phase = "init"
        logger.info("Phase 1: Session initialization starting")

        # 1. 异步等待首条 UserRequest
        user_request = await self._adapter.receive()
        self._session_id = user_request.session_id
        self._system_state.session_id = user_request.session_id
        logger.debug(f"Received user request: {user_request.text}")

        # 检查退出信号 — 用户在第一轮就发出退出指令时直接跳到阶段三
        if self._should_exit(user_request):
            logger.info("Exit signal received in phase init, skipping to phase end")
            self._should_exit_flag = True
            return AssemblyContext(user_request=user_request)

        # 2. GuideProvider（可选）
        guides = GuidesBundle()
        guide_provider = self._resolve_optional(GuideProvider)
        if guide_provider:
            try:
                guide_ctx = AssemblyContext(user_request=user_request)
                guide_ctx = self._hook_manager.trigger(
                    EVENT_BEFORE_GUIDE_GENERATION, guide_ctx, self._system_state
                )
                guides = guide_provider.get_guides(guide_ctx)
                guides = self._hook_manager.trigger(
                    EVENT_AFTER_GUIDE_GENERATION, guides, self._system_state
                )
                logger.debug(f"Guides loaded: identity={guides.identity[:50]}...")
            except Exception as e:
                logger.warning(f"GuideProvider.get_guides() failed: {e}")
        self._cached_guides = guides

        # 3. MemoryBackend（可选）
        memories: List[Any] = []
        memory = self._resolve_optional(MemoryBackend)
        if memory and user_request.text:
            try:
                memories = memory.search(user_request.text, "episodic")
                logger.debug(f"Retrieved {len(memories)} memories")
            except Exception as e:
                logger.warning(f"MemoryBackend.search() failed: {e}")

        # 4. ToolRouter（框架内部，非 DI）— 合并 SystemToolProvider + MCPAdapter
        available_tools: List[ToolDefinition] = []
        tool_router = ToolRouter()

        # 4a. SystemToolProvider（可选）
        sys_provider = self._resolve_optional(SystemToolProvider)
        if sys_provider:
            try:
                tool_router.register_provider(sys_provider)
                logger.debug(
                    f"SystemToolProvider registered: {type(sys_provider).__name__}"
                )
            except Exception as e:
                logger.warning(f"SystemToolProvider registration failed: {e}")

        # 4b. MCPAdapter（可选 — 不注册即裁切）
        mcp_adapter = self._resolve_optional(MCPAdapter)
        if mcp_adapter:
            try:
                tool_router.register_provider(mcp_adapter)
                logger.debug(
                    f"MCPAdapter registered: {type(mcp_adapter).__name__}"
                )
            except Exception as e:
                logger.warning(f"MCPAdapter registration failed: {e}")

        try:
            available_tools = tool_router.list_tools()
            logger.debug(f"Available tools: {len(available_tools)}")
        except Exception as e:
            logger.warning(f"ToolRouter.list_tools() failed: {e}")

        self._cached_tool_router = tool_router
        self._cached_tools = available_tools

        # 5. 构建 AssemblyContext
        ctx = AssemblyContext(
            user_request=user_request,
            guides=guides,
            available_tools=available_tools,
            history=self._history,
            memories=memories,
        )

        logger.info("Phase 1: Session initialization complete")
        return ctx

    # ------------------------------------------------------------------
    # 阶段二：单轮对话（仅内循环）
    # ------------------------------------------------------------------

    async def _phase_loop(self, ctx: AssemblyContext) -> None:
        """阶段二：单轮对话（仅内层 LLM + Tool call 循环）。

        与同步版 _phase_loop 的差异：
        - **不包含外层 while**（外层多轮循环由 AgentRuntime.run() 控制）
        - **不调用 adapter.receive()**（下一轮输入由 AgentRuntime 注入）
        - 仅执行一轮：组装上下文 → LLM+tool 循环 → 发送输出 → 返回

        Args:
            ctx: 当前轮的 AssemblyContext（user_request 为本轮输入）。
        """
        # 如果 _phase_init 已检测到退出信号，立即返回
        if self._should_exit_flag:
            return

        logger.info("Phase 2: Single round starting")
        self._system_state.phase = "loop"

        assembler = self._resolve_optional(ContextAssembler)
        tool_router = self._cached_tool_router

        # ── 当前轮用户请求写入 history（R1: record_message + meta）──
        if ctx.user_request and ctx.user_request.text:
            self._record_message(
                Message(role="user", content=ctx.user_request.text),
                meta=_request_meta(ctx.user_request),
            )

        # ── 组装上下文 ──
        ctx = self._hook_manager.trigger(
            EVENT_BEFORE_ASSEMBLE, ctx, self._system_state
        )
        if assembler:
            try:
                messages = assembler.assemble(ctx)
            except Exception as e:
                logger.warning(f"ContextAssembler.assemble() failed: {e}")
                messages = self._fallback_assemble(ctx)
        else:
            messages = self._fallback_assemble(ctx)
        messages = self._hook_manager.trigger(
            EVENT_AFTER_ASSEMBLE, messages, self._system_state
        )

        # ── 内层：LLM + Tool call 循环 ──
        tool_iterations = 0
        while True:
            tool_iterations += 1
            if tool_iterations > self._MAX_TOOL_ITERATIONS:
                logger.error(
                    f"Exceeded max tool iterations ({self._MAX_TOOL_ITERATIONS}). "
                    "Breaking inner loop to prevent infinite tool-calling."
                )
                await self._adapter.send(StopEvent(stop_reason="max_iterations"))
                self._record_stop("max_iterations")
                break

            if not self.call_llm:
                logger.warning("call_llm not set, skipping LLM call")
                await self._adapter.send(StopEvent(stop_reason="no_llm"))
                self._record_stop("no_llm")
                break

            # --- LLM 调用 ---
            try:
                messages = self._hook_manager.trigger(
                    EVENT_BEFORE_LLM_CALL, messages, self._system_state
                )
                response = await self.call_llm(
                    messages_to_dicts(messages),
                    tool_definitions_to_openai(self._cached_tools),
                )
                response = self._hook_manager.trigger(
                    EVENT_AFTER_LLM_CALL, response, self._system_state
                )
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                raise

            # --- ① thinking → ThinkingEvent ---
            if response.thinking:
                await self._adapter.send(ThinkingEvent(content=response.thinking))

            # --- ② tool_uses → ToolCallEvent → 执行 → ToolResultEvent ---
            if response.tool_uses:
                # 构造含 tool_calls 的 assistant message 并追加到 messages
                assistant_msg = build_assistant_message(response)
                messages.append(assistant_msg)

                # 将 assistant tool_use 消息写入 history（R2，Hook 后终值）
                self._record_message(Message(
                    role="assistant",
                    content=response.text or "",
                    tool_calls=list(response.tool_uses),
                ))

                # 串行执行每个 tool，每步推送事件
                for tc in response.tool_uses:
                    before_ts = time.time()
                    args: Dict[str, Any] = {}
                    error: Optional[str] = None
                    result: Any = None

                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError as e:
                        error = f"Failed to parse tool arguments: {e}"
                        after_ts = time.time()
                        await self._adapter.send(ToolCallEvent(
                            call_id=tc.id,
                            tool_name=tc.function.name,
                            arguments={},
                        ))
                        await self._adapter.send(ToolResultEvent(
                            call_id=tc.id,
                            tool_name=tc.function.name,
                            success=False,
                            error=error,
                            duration_ms=(after_ts - before_ts) * 1000,
                        ))
                    else:
                        # 推送 ToolCallEvent
                        await self._adapter.send(ToolCallEvent(
                            call_id=tc.id,
                            tool_name=tc.function.name,
                            arguments=args,
                        ))

                        tc = self._hook_manager.trigger(
                            EVENT_BEFORE_TOOL_EXECUTE, tc, self._system_state
                        )
                        try:
                            if tool_router and tool_router.has_tool(tc.function.name):
                                result = tool_router.execute(
                                    tc.function.name, args
                                )
                            else:
                                error = (
                                    f"ToolRouter has no tool '{tc.function.name}'. "
                                    f"Available: {sorted(tool_router._routes.keys()) if tool_router else 'none'}"
                                )
                        except Exception as e:
                            error = str(e)
                        after_ts = time.time()
                        duration_ms = (after_ts - before_ts) * 1000

                        # 推送 ToolResultEvent
                        await self._adapter.send(ToolResultEvent(
                            call_id=tc.id,
                            tool_name=tc.function.name,
                            success=(error is None),
                            result=result if error is None else None,
                            error=error,
                            duration_ms=duration_ms,
                        ))

                    # 提取 ToolResult 字段
                    if result is not None and hasattr(result, "success"):
                        success = result.success
                        content = result.content if hasattr(result, "content") else str(result)
                        if hasattr(result, "error") and result.error:
                            error = result.error
                    else:
                        success = error is None
                        content = result

                    # 触发 after_tool_execute
                    tool_result = ToolResult(
                        success=success, content=content, error=error
                    )
                    tool_result = self._hook_manager.trigger(
                        EVENT_AFTER_TOOL_EXECUTE, tool_result, self._system_state
                    )
                    success = tool_result.success
                    content = tool_result.content
                    error = tool_result.error

                    # 记录到 tool_call_records（R3a，Hook 后终值）
                    self._record_tool_call(ToolCallRecord(
                        tool_call_id=tc.id,
                        tool_name=tc.function.name,
                        arguments=args,
                        result=content if success else None,
                        started_at=before_ts,
                        finished_at=after_ts,
                        error=error,
                    ))

                    # 构造 tool result message 追加到 messages
                    tool_msg = build_tool_result_message(tc, content, error)
                    messages.append(tool_msg)

                    # 将 tool 执行结果写入 history（R3b）
                    self._record_message(Message(
                        role="tool",
                        content=str(content) if not error else f"Error: {error}",
                        tool_call_id=tc.id,
                    ))

                # text + tool_uses 共存时：
                # - 中间文本不发送给用户（是模型的"思考过程"）
                # - 继续内层循环，让 LLM 处理 tool 结果后生成最终回复
                continue

            # --- ③ text → TextEvent + StopEvent → break ---
            if response.text:
                messages.append(
                    Message(role="assistant", content=response.text or "")
                )
                await self._adapter.send(TextEvent(content=response.text or ""))
                await self._adapter.send(StopEvent(stop_reason=response.stop_reason))
                # R4a/R4b：assistant 终值 + 轮次停止原因
                self._record_message(
                    Message(role="assistant", content=response.text or "")
                )
                self._record_stop(response.stop_reason)
                break  # 跳出内层循环

            # --- ④ 防御：空响应 ---
            logger.warning(
                "LLM returned empty response (no text, no tool_uses)"
            )
            await self._adapter.send(StopEvent(stop_reason="empty_response"))
            self._record_stop("empty_response")
            break

        # ── 轮次边界：批量 flush（返回即达 page cache；进程崩溃不丢本轮）──
        if self._session_log is not None:
            await self._session_log.flush()

        logger.info("Phase 2: Single round ended")

    # ------------------------------------------------------------------
    # 阶段三：会话结束
    # ------------------------------------------------------------------

    async def _phase_end(self, trajectory: Trajectory) -> None:
        """阶段三：会话结束。

        Sensor.sense(trajectory) → ToolRouter.shutdown() → 清理内部状态。

        Args:
            trajectory: 组装好的完整执行轨迹。
        """
        logger.info("Phase 3: Session end starting")
        self._system_state.phase = "end"

        # 1. on_session_end Hook（Sensor 之前）
        trajectory = self._hook_manager.trigger(
            EVENT_ON_SESSION_END, trajectory, self._system_state
        )

        # 2. Sensor（可选，同步调用）
        sensor = self._resolve_optional(Sensor)
        if sensor:
            try:
                sensor.sense(trajectory)
                logger.debug("Sensor.sense() completed")
            except Exception as e:
                logger.warning(f"Sensor.sense() failed: {e}")

        # 3. after_sensor Hook
        self._hook_manager.trigger(
            EVENT_AFTER_SENSOR, trajectory, self._system_state
        )

        # 4. ToolRouter shutdown（统一清理，分发到各 Provider）
        if self._cached_tool_router:
            try:
                self._cached_tool_router.shutdown()
                logger.debug("ToolRouter.shutdown() completed")
            except Exception as e:
                logger.warning(f"ToolRouter.shutdown() failed: {e}")

        # R6: finalize —— session_end 事件 + fsync。
        # 必须在 history 清理之前（final_output 来自 trajectory，事件此刻编码）；
        # 失败只降级（finalize 内部兜底），不阻断 _phase_end 其余清理。
        if self._session_log is not None:
            try:
                await self._session_log.finalize(
                    status="paused",
                    final_output=trajectory.final_output,
                    execution_time=trajectory.execution_time,
                )
            except Exception as e:
                logger.warning(f"SessionLog.finalize() failed: {e}")

        # 5. 清理内部状态
        self._history.clear()
        self._tool_call_records.clear()
        self._should_exit_flag = False

        logger.info("Phase 3: Session end complete")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # SessionLog 记录点（无 session_log 时退化为原样的内存 append）
    # ------------------------------------------------------------------

    def _record_message(self, message: Message, *, meta: Optional[dict] = None) -> None:
        if self._session_log is not None:
            self._session_log.record_message(message, meta=meta)
        else:
            self._history.append(message)

    def _record_tool_call(self, record: ToolCallRecord) -> None:
        if self._session_log is not None:
            self._session_log.record_tool_call(record)
        else:
            self._tool_call_records.append(record)

    def _record_stop(self, reason: str) -> None:
        if self._session_log is not None:
            self._session_log.record_stop(reason)

    def _resolve_optional(self, interface: type) -> Optional[Any]:
        """尝试解析组件，不存在时返回 None 并记录 WARNING。

        Args:
            interface: 组件的抽象接口类型。

        Returns:
            已注册的实例，或 None（如果未注册）。
        """
        try:
            return self.container.resolve(interface)
        except ComponentNotRegisteredError:
            logger.warning(
                f"Component '{interface.__name__}' not registered, skipping"
            )
            return None

    def _should_exit(self, user_request: UserRequest) -> bool:
        """判断是否应该退出会话。

        退出条件（任一满足即退出）：
        1. user_request.text 为空字符串或仅空白字符
        2. user_request.text 匹配退出关键词 "/exit"
        3. user_request.metadata 中包含 "exit": True

        Args:
            user_request: 用户请求。

        Returns:
            True 如果应该退出，False 否则。
        """
        if not user_request.text:
            return True
        if user_request.text.strip() == "":
            return True
        if user_request.text.strip() == self._EXIT_KEYWORD:
            return True
        if user_request.metadata.get("exit") is True:
            return True
        return False

    def _build_trajectory(self) -> Trajectory:
        """从会话记录组装完整的 Trajectory 对象。

        Returns:
            Trajectory: 完整的执行轨迹。
        """
        execution_time = time.time() - self._start_time
        final_output = ""
        if self._history:
            last = self._history[-1]
            final_output = last.content if last else ""

        return Trajectory(
            session_id=self._session_id,
            history=list(self._history),
            tool_calls=list(self._tool_call_records),
            final_output=final_output,
            execution_time=execution_time,
        )

    def _fallback_assemble(
        self, ctx: AssemblyContext
    ) -> List[Message]:
        """无 ContextAssembler 时的降级上下文组装。

        Args:
            ctx: AssemblyContext。

        Returns:
            降级的 message 列表（Message 对象）。
        """
        messages: List[Message] = []
        if ctx.guides and ctx.guides.identity:
            messages.append(Message(
                role="system",
                content=ctx.guides.identity,
            ))
        if ctx.user_request and ctx.user_request.text:
            messages.append(Message(
                role="user",
                content=ctx.user_request.text,
            ))
        return messages
