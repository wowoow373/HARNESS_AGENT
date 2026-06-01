"""Harness Agent Template — 生命周期编排器。

按三阶段固定顺序驱动组件调用：
1. 会话初始化（Session Init）
2. 多轮对话循环（Conversation Loop）
3. 会话结束（Session End）

编排器不实现任何业务逻辑。它只做"在正确的时间、以正确的顺序、
调用正确的组件方法"。所有业务行为由注入的组件决定。

LLM 调用通过构造函数注入的 call_llm 可调用对象实现。
"""

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .container import DIContainer
from .exceptions import ComponentNotRegisteredError, OrchestratorError
from .types import (
    _MinimalAssemblyContext,
    _MinimalGuidesBundle,
    _MinimalResponse,
    _MinimalToolCall,
    _MinimalToolCallFunction,
    _MinimalTrajectory,
    _MinimalUserRequest,
)
from ..interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MCPManager,
    MemoryBackend,
    Sensor,
    Tool,
    ToolRegistry,
)
from ..messaging import build_assistant_message, build_tool_result_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LifecycleOrchestrator
# ---------------------------------------------------------------------------


class LifecycleOrchestrator:
    """生命周期编排器 — 按三阶段固定顺序驱动组件调用。

    编排器不实现任何业务逻辑。它只做"在正确的时间、以正确的顺序、
    调用正确的组件方法"。所有业务行为由注入的组件决定。

    LLM 调用通过构造函数注入的 call_llm 可调用对象实现。

    用法::

        orch = LifecycleOrchestrator(container, call_llm=my_llm_adapter)
        orch.run()
    """

    # 退出关键词
    _EXIT_KEYWORD = "/exit"

    # 内层循环最大迭代次数（防止无限 tool-calling 循环）
    _MAX_TOOL_ITERATIONS = 100

    def __init__(
        self,
        container: DIContainer,
        call_llm: Optional[Callable[..., Any]] = None,
    ):
        """初始化编排器。

        Args:
            container: DI 容器，用于解析组件实例。
            call_llm: LLM 调用函数，签名:
                      (messages: List[Dict], tools: List[Dict]) → _MinimalResponse。
                      为 None 时 tool_use 循环不可用，但仍可验证编排流程。
        """
        self.container = container
        self.call_llm = call_llm

        # 会话状态
        self._history: List[Dict[str, Any]] = []
        self._tool_call_records: List[Dict[str, Any]] = []
        self._start_time: float = 0.0
        self._should_exit_flag: bool = False

        # 阶段一缓存的不可变数据
        self._cached_guides: Optional[_MinimalGuidesBundle] = None
        self._cached_tools: List[Dict[str, Any]] = []
        self._cached_tool_registry: Optional[Any] = None  # 避免 phase_init/loop 重复 resolve

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        """启动并运行完整的会话生命周期。

        这是编排器的唯一公开入口。内部依次执行：
        1. _phase_init()     — 会话初始化
        2. _phase_loop()     — 多轮对话循环
        3. _phase_end()      — 会话结束（finally 块中执行）

        Raises:
            ComponentNotRegisteredError: 必需的组件未注册。
            OrchestratorError: 编排流程中的其他错误。
        """
        try:
            ctx = self._phase_init()
            self._phase_loop(ctx)
        except Exception as e:
            logger.error(f"Orchestrator error: {e}")
            if isinstance(e, (ComponentNotRegisteredError, OrchestratorError)):
                raise
            raise OrchestratorError(str(e)) from e
        finally:
            trajectory = self._build_trajectory()
            self._phase_end(trajectory)

    # ------------------------------------------------------------------
    # 阶段一：会话初始化
    # ------------------------------------------------------------------

    def _phase_init(self) -> _MinimalAssemblyContext:
        """阶段一：会话初始化。

        步骤：
        1. resolve InputAdapter → receive() → UserRequest
        2. resolve GuideProvider → get_guides() → GuidesBundle（缓存）
        3. resolve MemoryBackend → search() → List[MemoryItem]
        4. resolve ToolRegistry → list_tools() → List[ToolDefinition]（缓存）
        5. 构建并返回 AssemblyContext

        Returns:
            _MinimalAssemblyContext: 初始化的上下文对象。

        Raises:
            ComponentNotRegisteredError: InputAdapter 未注册。
        """
        self._start_time = time.time()
        logger.info("Phase 1: Session initialization starting")

        # 1. InputAdapter（必需组件）
        adapter = self.container.resolve(InputAdapter)
        raw_request = adapter.receive()
        user_request = self._normalize_user_request(raw_request)
        logger.debug(f"Received user request: {user_request.text}")

        # 检查退出信号 — 用户在第一轮就发出退出指令时直接跳到阶段三
        if self._should_exit(user_request):
            logger.info("Exit signal received in phase init, skipping to phase end")
            self._should_exit_flag = True
            return _MinimalAssemblyContext(user_request=user_request)

        # 2. GuideProvider（可选）
        guides = _MinimalGuidesBundle()
        guide_provider = self._resolve_optional(GuideProvider)
        if guide_provider:
            try:
                # 构建 GuideContext（最小表示）
                guide_ctx = _MinimalAssemblyContext(user_request=user_request)
                raw_guides = guide_provider.get_guides(guide_ctx)
                guides = self._normalize_guides(raw_guides)
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

        # 4. ToolRegistry（可选，缓存引用供 _phase_loop 使用）
        available_tools: List[Dict[str, Any]] = []
        tool_registry = self._resolve_optional(ToolRegistry)
        self._cached_tool_registry = tool_registry
        if tool_registry:
            try:
                available_tools = tool_registry.list_tools()
                logger.debug(f"Available tools: {len(available_tools)}")
            except Exception as e:
                logger.warning(f"ToolRegistry.list_tools() failed: {e}")
        self._cached_tools = available_tools

        # 5. 构建 AssemblyContext
        ctx = _MinimalAssemblyContext(
            user_request=user_request,
            guides=guides,
            available_tools=available_tools,
            history=self._history,
            memories=memories,
        )

        logger.info("Phase 1: Session initialization complete")
        return ctx

    # ------------------------------------------------------------------
    # 阶段二：多轮对话循环
    # ------------------------------------------------------------------

    def _phase_loop(self, initial_ctx: _MinimalAssemblyContext) -> None:
        """阶段二：多轮对话循环。

        外层循环（每轮用户输入触发一次）：
          1. ContextAssembler.assemble(ctx) → messages
          2. 进入内层循环

        内层循环（同一轮内 tool_use 连续生成）：
          3. self.call_llm(messages, tools) → Response
          4. 处理 Response（text 和 tool_uses 可共存）：
             a. 如有 tool_uses → 每个 tool 经 ToolRegistry 串行执行
                → tool_use + tool_result 追加到 messages
             b. 如有 text → InputAdapter.send(response) → 跳出内层循环
             c. 如仅有 tool_uses 无 text → 回到步骤 3
          5. 回到外层循环：InputAdapter.receive() → 更新 ctx → 回到步骤 1

        退出条件：用户发出退出信号或空输入。

        Args:
            initial_ctx: 阶段一产出的 AssemblyContext。
        """
        logger.info("Phase 2: Conversation loop starting")

        ctx = initial_ctx
        assembler = self._resolve_optional(ContextAssembler)
        adapter = self.container.resolve(InputAdapter)
        tool_registry = self._cached_tool_registry

        # 外层循环 — 每轮用户输入触发一次
        while not self._should_exit_flag:
            # === 外层：组装上下文 ===
            if assembler:
                try:
                    messages = assembler.assemble(ctx)
                except Exception as e:
                    logger.warning(f"ContextAssembler.assemble() failed: {e}")
                    messages = self._fallback_assemble(ctx)
            else:
                messages = self._fallback_assemble(ctx)

            # === 内层：LLM + Tool call 循环 ===
            tool_iterations = 0
            while True:
                tool_iterations += 1
                if tool_iterations > self._MAX_TOOL_ITERATIONS:
                    logger.error(
                        f"Exceeded max tool iterations ({self._MAX_TOOL_ITERATIONS}). "
                        "Breaking inner loop to prevent infinite tool-calling."
                    )
                    break
                if not self.call_llm:
                    logger.warning("call_llm not set, skipping LLM call")
                    # 无 LLM 时模拟一次文本响应后跳出
                    break

                try:
                    response = self.call_llm(messages, self._cached_tools)
                except Exception as e:
                    logger.error(f"LLM call failed: {e}")
                    raise

                # 标准化 response 为 _MinimalResponse
                response = self._normalize_response(response)

                # --- 处理 tool_uses ---
                if response.tool_uses:
                    # 构造含 tool_calls 的 assistant message 并追加
                    assistant_msg = build_assistant_message(response)
                    messages.append(assistant_msg)

                    # 串行执行每个 tool
                    for tc in response.tool_uses:
                        before_ts = time.time()
                        args: Dict[str, Any] = {}
                        error: Optional[str] = None
                        result: Any = None

                        try:
                            args = tc.parse_arguments()
                        except json.JSONDecodeError as e:
                            error = f"Failed to parse tool arguments: {e}"
                            after_ts = time.time()
                        else:
                            try:
                                if tool_registry:
                                    result = tool_registry.execute(
                                        tc.function.name, args
                                    )
                                else:
                                    error = (
                                        f"ToolRegistry not registered, "
                                        f"cannot execute tool '{tc.function.name}'"
                                    )
                            except Exception as e:
                                error = str(e)
                            after_ts = time.time()

                        # 提取 ToolResult 字段
                        if hasattr(result, "success"):
                            success = result.success
                            content = result.content if hasattr(result, "content") else str(result)
                            if hasattr(result, "error") and result.error:
                                error = result.error
                        else:
                            success = error is None
                            content = result

                        # 记录到 tool_call_records
                        self._tool_call_records.append({
                            "tool_name": tc.function.name,
                            "arguments": args,
                            "result": content if success else None,
                            "started_at": before_ts,
                            "finished_at": after_ts,
                            "error": error,
                        })

                        # 构造 tool result message 追加到 messages
                        tool_msg = build_tool_result_message(tc, content, error)
                        messages.append(tool_msg)

                    # 如果本次 response 有 text → 发给用户 + 跳出内层循环
                    if response.text:
                        adapter.send(response)
                        self._history.append(
                            {"role": "assistant", "content": response.text}
                        )
                        break

                    # 仅有 tool_uses 无 text → 继续内层循环（回到 LLM）
                    continue

                # --- 处理纯 text 响应（无 tool_uses） ---
                if response.text:
                    messages.append(
                        {"role": "assistant", "content": response.text}
                    )
                    adapter.send(response)
                    self._history.append(
                        {"role": "assistant", "content": response.text}
                    )
                    break  # 跳出内层循环

                # --- 防御：空响应 ---
                logger.warning(
                    "LLM returned empty response (no text, no tool_uses)"
                )
                break

            # === 外层：等待下一轮用户输入 ===
            raw_request = adapter.receive()
            new_request = self._normalize_user_request(raw_request)

            if self._should_exit(new_request):
                self._should_exit_flag = True
                break

            # 更新 ctx 用于下一轮
            ctx = _MinimalAssemblyContext(
                user_request=new_request,
                guides=self._cached_guides,
                available_tools=self._cached_tools,
                history=self._history,
                memories=ctx.memories,
            )

        logger.info("Phase 2: Conversation loop ended")

    # ------------------------------------------------------------------
    # 阶段三：会话结束
    # ------------------------------------------------------------------

    def _phase_end(self, trajectory: _MinimalTrajectory) -> None:
        """阶段三：会话结束。

        Args:
            trajectory: run() 中组装好的完整执行轨迹。

        Sensor.sense(trajectory) → 清理内部状态。
        """
        logger.info("Phase 3: Session end starting")

        # 1. Sensor（可选）
        sensor = self._resolve_optional(Sensor)
        if sensor:
            try:
                sensor.sense(trajectory)
                logger.debug("Sensor.sense() completed")
            except Exception as e:
                logger.warning(f"Sensor.sense() failed: {e}")

        # 2. 清理内部状态
        self._history.clear()
        self._tool_call_records.clear()
        self._should_exit_flag = False

        logger.info("Phase 3: Session end complete")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

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

    def _should_exit(self, user_request: _MinimalUserRequest) -> bool:
        """判断是否应该退出会话。

        退出条件（任一满足即退出）：
        1. user_request.text 为 None 或空字符串或仅空白字符
        2. user_request.text 匹配退出关键词 "/exit"
        3. user_request.metadata 中包含 "exit": True

        Args:
            user_request: 用户请求。

        Returns:
            True 如果应该退出，False 否则。
        """
        if user_request.text is None:
            return True
        if user_request.text.strip() == "":
            return True
        if user_request.text.strip() == self._EXIT_KEYWORD:
            return True
        if user_request.metadata.get("exit") is True:
            return True
        return False

    def _build_trajectory(self) -> _MinimalTrajectory:
        """从会话记录组装完整的 Trajectory 对象。

        Returns:
            _MinimalTrajectory: 完整的执行轨迹。
        """
        execution_time = time.time() - self._start_time
        final_output = ""
        if self._history:
            last = self._history[-1]
            final_output = last.get("content", "")

        return _MinimalTrajectory(
            history=list(self._history),
            tool_calls=list(self._tool_call_records),
            final_output=final_output,
            execution_time=execution_time,
        )

    def _fallback_assemble(
        self, ctx: _MinimalAssemblyContext
    ) -> List[Dict[str, Any]]:
        """无 ContextAssembler 时的降级上下文组装。

        Args:
            ctx: AssemblyContext。

        Returns:
            降级的 message 列表。
        """
        messages: List[Dict[str, Any]] = []
        if ctx.guides and ctx.guides.identity:
            messages.append({
                "role": "system",
                "content": ctx.guides.identity,
            })
        if ctx.user_request and ctx.user_request.text:
            messages.append({
                "role": "user",
                "content": ctx.user_request.text,
            })
        return messages

    # ------------------------------------------------------------------
    # 标准化辅助方法（将外部对象转为内部最小表示）
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_user_request(raw: Any) -> _MinimalUserRequest:
        """将外部 UserRequest 转为 _MinimalUserRequest。

        支持两种输入形式：
        - 已经是 _MinimalUserRequest → 直接返回
        - 有 text 属性的对象 → 提取 text 和 metadata
        """
        if isinstance(raw, _MinimalUserRequest):
            return raw
        text = getattr(raw, "text", None)
        metadata = getattr(raw, "metadata", {})
        return _MinimalUserRequest(text=text, metadata=metadata)

    @staticmethod
    def _normalize_guides(raw: Any) -> _MinimalGuidesBundle:
        """将外部 GuidesBundle 转为 _MinimalGuidesBundle。"""
        if isinstance(raw, _MinimalGuidesBundle):
            return raw
        identity = getattr(raw, "identity", "")
        capabilities = getattr(raw, "capabilities", [])
        rules = getattr(raw, "rules", [])
        constraints = getattr(raw, "constraints", [])
        examples = getattr(raw, "examples", [])
        return _MinimalGuidesBundle(
            identity=identity,
            capabilities=capabilities,
            rules=rules,
            constraints=constraints,
            examples=examples,
        )

    @staticmethod
    def _normalize_response(raw: Any) -> _MinimalResponse:
        """将外部 Response 转为 _MinimalResponse。

        支持两种输入形式：
        - 已经是 _MinimalResponse → 直接返回
        - 其他对象 → 尝试提取 text、tool_uses、stop_reason 属性
        """
        if isinstance(raw, _MinimalResponse):
            return raw

        text = getattr(raw, "text", None)
        thinking = getattr(raw, "thinking", None)
        stop_reason = getattr(raw, "stop_reason", "end_turn")
        raw_tool_uses = getattr(raw, "tool_uses", [])

        tool_uses: List[_MinimalToolCall] = []
        for tc in raw_tool_uses:
            if isinstance(tc, _MinimalToolCall):
                tool_uses.append(tc)
            else:
                tool_uses.append(_MinimalToolCall(
                    id=getattr(tc, "id", ""),
                    type=getattr(tc, "type", "function"),
                    function=_MinimalToolCallFunction(
                        name=getattr(tc, "name", ""),
                        arguments=getattr(tc, "arguments", "{}"),
                    ),
                ))

        return _MinimalResponse(
            text=text,
            thinking=thinking,
            tool_uses=tool_uses,
            stop_reason=stop_reason,
        )
