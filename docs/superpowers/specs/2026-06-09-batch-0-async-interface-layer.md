# Batch 0: Async 接口层 + AsyncLifecycleOrchestrator

> 版本: 0.1 | 日期: 2026-06-09 | 状态: 设计评审
> 依赖: 无新增前置依赖。所有依赖的组件（DIContainer、ToolRouter、ContextAssembler、HookManager 等）均已存在

---

## 一、目标

建立 async 接口层，将现有同步 `LifecycleOrchestrator` 的三阶段编排逻辑迁移为 async 版本。**不改动任何现有同步代码**。

本 batch 结束时：
- `AsyncInputAdapter` Protocol 定义完成，明确 async I/O 契约
- `AsyncLifecycleOrchestrator` 实现完成，可独立运行单轮对话（不含外层多轮循环）
- `KernelBridgeAdapter` 初版可用，实现内联降级路由（TextEvent → stdout, StopEvent → 丢弃）
- `InternalMessage` 和 `__EXIT_SENTINEL__` 共享类型定义完成
- `harness/runtime/` 子包创建
- 全部现有测试继续通过

---

## 二、前置条件

以下组件/接口已存在且**不做任何修改**：

| 组件 | 位置 | 用途 |
|------|------|------|
| `DIContainer` | `harness/core/container.py` | AsyncLifecycleOrchestrator 通过它解析可选组件 |
| `LifecycleOrchestrator` | `harness/core/orchestrator.py` | Async 版本的参照蓝本 |
| `InputAdapter` (Protocol) | `harness/interfaces/input_adapter.py` | 同步版 I/O 接口，不动 |
| `HookManager` | `harness/hooks/hook_manager.py` | Hook 注册/触发（同步调用，不变） |
| `ToolRouter` | `harness/core/tool_router.py` | Tool 执行分发（同步调用，不变） |
| `ContextAssembler` (Protocol) | `harness/interfaces/context_assembler.py` | 上下文组装（同步调用，不变） |
| 所有 `harness.interfaces.types` 中的类型 | `harness/interfaces/types.py` | DTO，全部复用 |

---

## 三、新增/修改的组件

| 组件 | 类型 | 文件 | 需展开内部设计 |
|------|------|------|--------------|
| `AsyncInputAdapter` Protocol | 新增 | `harness/interfaces/async_input_adapter.py` | 是 |
| `AsyncCallLLM` Protocol | 新增 | `harness/interfaces/async_call_llm.py` | 是 |
| `AsyncLifecycleOrchestrator` | 新增 | `harness/core/async_orchestrator.py` | 是 |
| `InternalMessage` | 新增 | `harness/runtime/types.py` | 是 |
| `__EXIT_SENTINEL__` | 新增 | `harness/runtime/types.py` | 是 |
| `KernelBridgeAdapter` | 新增 | `harness/runtime/bridge_adapter.py` | 是 |
| `harness/runtime/` 子包 | 新增 | `harness/runtime/__init__.py` | 否 |
| `harness/interfaces/__init__.py` | 修改 | `harness/interfaces/__init__.py` | 否（仅加 re-export） |
| `harness/core/__init__.py` | 修改 | `harness/core/__init__.py` | 否（仅加 re-export） |

### 3.1 跨 Batch 接口契约

Batch 0 完成后，暴露给 Batch 1 的稳定 import：

```python
from harness.interfaces.async_input_adapter import AsyncInputAdapter
from harness.interfaces.async_call_llm import AsyncCallLLM
from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.runtime.types import InternalMessage, __EXIT_SENTINEL__
```

---

## 四、关键组件设计

### 4.1 核心架构决策：Outer-loop 归属（方案 A）

**这是 Batch 0 最重要的架构决策，影响 AsyncLifecycleOrchestrator 的方法签名和行为。**

现有同步 `LifecycleOrchestrator._phase_loop()` 同时包含外循环（等待下一轮用户输入 → 组装上下文）和内循环（LLM + tool-calling 循环）。在 Runtime 架构中，外循环由 `AgentRuntime.run()` 拥有，因为它需要控制 `max_rounds`、`should_exit`、`oneshot` 模式判定，以及 `_idle_for_quiescence()` 的"等待输入"判断。

**方案 A（采用）**：`_phase_loop()` 仅执行一轮（内循环），`AgentRuntime.run()` 拥有外循环。

```
AgentRuntime.run()   ← 外循环: while not should_exit and rounds < max_rounds:
  │                     - 调用 _phase_loop(ctx)
  │                     - 检查 oneshot / should_exit
  │                     - await adapter.receive() → 下一轮 UserRequest
  │                     - 更新 ctx
  │
  └── AsyncLifecycleOrchestrator._phase_loop(ctx)  ← 内循环: while True:
       │                                                - await call_llm()
       │                                                - 推送事件序列
       │                                                - tool_use? → 执行 → 回到 call_llm
       │                                                - text → break
       │                                             返回（不调用 adapter.receive()）
```

**对 AsyncLifecycleOrchestrator 的影响**：

| 方法 | 同步版行为 | 异步版行为 |
|------|-----------|-----------|
| `_phase_init()` | `adapter.receive()` → 得到首条 UserRequest → 初始化 → 返回 AssemblyContext | 同，但 `await adapter.receive()` |
| `_phase_loop(ctx)` | **外+内双循环**：外层 while 等 adapter.receive()；内层 while 做 tool-calling | **仅内循环**：接收 AssemblyContext，运行一轮 tool-calling 后返回。**不调用** `adapter.receive()` |
| `_phase_end(traj)` | Sensor.sense() → 清理状态 | 同，但 `adapter.send()` 改为 `await`（如有）。Sensor 保持同步 |

**为什么 Batch 0 就要做这个决策**：`AsyncLifecycleOrchestrator._phase_loop()` 的方法签名决定了 Batch 1 `AgentRuntime.run()` 如何调用它。如果 Batch 0 把外循环留在 orchestrator 内，Batch 1 就需要重构它。

---

### 4.2 `AsyncInputAdapter` Protocol

```python
# harness/interfaces/async_input_adapter.py

from typing import Protocol, runtime_checkable
from .types import UserRequest, AdapterEvent


@runtime_checkable
class AsyncInputAdapter(Protocol):
    """异步输入输出适配器接口。

    与同步 InputAdapter 的区别：
    - receive() / send() 均为 async def，兼容 asyncio 队列和 MessageBus
    - send() 新增 target 参数：None 走订阅路由，指定 pid 走定向投递
    """

    async def receive(self) -> UserRequest:
        """异步接收用户输入并返回标准化请求。

        Returns:
            UserRequest: 标准化用户请求对象。
        """
        ...

    async def send(self, event: AdapterEvent, target: str | None = None) -> None:
        """异步推送事件。

        Args:
            event: 前端事件（ThinkingEvent | ToolCallEvent |
                   ToolResultEvent | TextEvent | StopEvent）。
            target: 定向投递目标 pid。None 表示走 pub-sub 路由。
        """
        ...
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| `send()` 的 `target` 默认值 | `None`，语义为"走 pub-sub 路由" | 大多数调用不需要定向投递；显式 `target=pid` 时走定向路径 |
| `receive()` 返回值 | 同同步版：`UserRequest` | 保持一致性；`__EXIT_SENTINEL__` 的转换在 KBA 内部完成，对外透明 |
| Protocol 放 `harness/interfaces/` | 是 | 符合现有约定——所有 Protocol 都在 interfaces 包 |

---

### 4.3 `AsyncCallLLM` Protocol

```python
# harness/interfaces/async_call_llm.py

from typing import Any, Dict, List, Protocol, runtime_checkable

from .types import Response


@runtime_checkable
class AsyncCallLLM(Protocol):
    """异步 LLM 调用接口。

    编排器在 _phase_loop 内层循环中调用此函数，每次传入当前消息
    和可用工具列表。返回值 Response 可包含 text / thinking / tool_uses。
    """

    async def __call__(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
    ) -> Response:
        """异步调用 LLM。

        Args:
            messages: OpenAI 格式的消息列表。
            tools: OpenAI 格式的可用工具列表。None 表示无可用工具。
                   编排器总是传递两个参数，但实现方可选择忽略 tools
                   （如测试 mock 只需返回固定文本）。

        Returns:
            Response: LLM 响应（text / thinking / tool_uses）。
        """
        ...
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| Protocol 还是 Callable 类型别名？ | 独立 Protocol 类 | 顶层设计明确要求定义 `AsyncCallLLM` Protocol；独立类可被 import、文档化、做 `isinstance` 运行时检查 |
| `tools` 参数必需还是可选？ | 可选，默认值 `None` | 遵循与同步版 `LifecycleOrchestrator` 一致的约定（`Callable[..., Any]`）。编排器总是传递 `(messages, tools)`，但实现方可选择忽略 `tools`——例如测试 mock 只需返回固定 `Response(text=...)` |
| `@runtime_checkable`？ | 是 | 与其他 Protocol（`AsyncInputAdapter`、`InputAdapter`）保持一致 |
| Protocol 放 `harness/interfaces/`？ | 是 | 符合现有约定——所有 Protocol 都在 interfaces 包 |

---

### 4.4 `InternalMessage` 与 `__EXIT_SENTINEL__`

```python
# harness/runtime/types.py

from dataclasses import dataclass, field
import time


@dataclass
class InternalMessage:
    """MessageBus 内部消息格式。

    与 UserRequest 的区别：
    - UserRequest 是 LLM 看到的"用户输入"，经 ContextAssembler 组装进 messages
    - InternalMessage 是 MessageBus 内部的投递单元，
      经 KernelBridgeAdapter.receive() 转换为 UserRequest
    """
    from_pid: str
    content: str               # TextEvent.content 或 "" (for StopEvent)
    metadata: dict = field(default_factory=dict)
    # metadata 示例：
    #   {"stop": True}             — 原始 event 为 StopEvent
    #   {}                         — 原始 event 为 TextEvent
    created_at: float = field(default_factory=time.time)


# 模块级 sentinel 对象，用于 asyncio.Queue 哨兵模式
# 实现为简单的 object() 实例，靠身份比较 (is) 识别
__EXIT_SENTINEL__ = object()
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| `__EXIT_SENTINEL__` 类型 | `object()` 实例 | asyncio.Queue 标准哨兵模式。身份比较（`is`）可靠，不会被字符串内容误匹配 |
| `InternalMessage` 放 `harness/runtime/types.py` | 是 | 这是 Runtime 层内部类型。MessageBus（Batch 3）和 KBA 共用，放 runtime 包而非 interfaces |
| `InternalMessage.created_at` | `time.time()` | 方便后续 debug 和监控。非核心逻辑字段，默认值使构造简单 |

---

### 4.5 `AsyncLifecycleOrchestrator`

#### 4.5.1 构造函数

```python
class AsyncLifecycleOrchestrator:
    """异步生命周期编排器 — 三阶段固定顺序驱动组件调用。

    与 LifecycleOrchestrator 的区别：
    - _phase_loop() 仅执行一轮（内循环），外层多轮循环由 AgentRuntime.run() 控制
    - call_llm / adapter.receive() / adapter.send() 均为 async
    - adapter 在构造时显式传入，不走 DI 容器解析
    - call_llm 在构造时即为 async callable
    """

    def __init__(
        self,
        container: DIContainer,
        *,
        adapter: AsyncInputAdapter,
        # call_llm 允许为 None 以兼容以下场景：
        # - 单元测试：验证编排流程（三阶段顺序、事件推送、tool 分发）而不调 LLM
        # - Hook 开发：在 call_llm 前通过 Hook 拦截并注入自定义行为
        # 与同步版 LifecycleOrchestrator(container, call_llm=None) 的约定一致
        call_llm: AsyncCallLLM | None = None,
    ):
        """初始化异步编排器。

        Args:
            container: DI 容器，用于解析可选组件。
            adapter: 异步 I/O 适配器（显式传入，不走 DI）。
            call_llm: async LLM 调用函数，满足 AsyncCallLLM Protocol。
                      为 None 时 tool_use 循环不可用——用于测试
                      和 Hook 开发场景，允许验证编排流程而不调用真实 LLM。
        """
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| adapter 获取方式 | 构造函数显式传入 | 不走 `container.resolve(AsyncInputAdapter)` 的原因：(a) 避免修改 DIContainer 注册机制；(b) 每个 AgentRuntime 有自己独立的 KBA 实例，DI 容器不应管理这个一对一绑定；(c) 和现有 `LifecycleOrchestrator` 的 adapter 解析路径完全解耦 |
| `call_llm` 类型 | 仅接收 `async callable` | 不做 `iscoroutinefunction` 运行时检测。sync→async 桥接在 Runtime 入口层做（`asyncio.to_thread`），不侵入 orchestrator |
| 其他参数 | 无 | 不暴露 `_MAX_TOOL_ITERATIONS` 等内部常量作为参数——和现有版本保持一致 |

#### 4.4.2 `_phase_init()` — 会话初始化

```
async def _phase_init():
    self._system_state.phase = "init"
    self._start_time = time.time()

    # 1. 等待首条 UserRequest
    user_request = await self._adapter.receive()
    self._session_id = user_request.session_id

    if self._should_exit(user_request):
        self._should_exit_flag = True
        return AssemblyContext(user_request=user_request)

    # 2-4. GuideProvider / MemoryBackend / ToolRouter
    # （与同步版完全一致，不改动）
    ...

    # 5. 构建 AssemblyContext，缓存 guides/tools/tool_router
    self._cached_guides = guides
    self._cached_tools = available_tools
    self._cached_tool_router = tool_router

    return ctx
```

**与同步版的差异**：仅 `adapter.receive()` 加了 `await`。其余逻辑逐行对应。

#### 4.4.3 `_phase_loop(ctx)` — 单轮对话（仅内循环）

**这是与同步版差异最大的方法**。同步版有外层 while（等待 adapter.receive()）+ 内层 while（tool-calling）；异步版**仅保留内层 while**。

```
async def _phase_loop(self, ctx: AssemblyContext) -> None:
    self._system_state.phase = "loop"

    assembler = self._resolve_optional(ContextAssembler)
    tool_router = self._cached_tool_router

    # ── 当前轮用户请求写入 history ──
    if ctx.user_request and ctx.user_request.text:
        self._history.append(Message(role="user", content=ctx.user_request.text))

    # ── 组装上下文 ──
    ctx = self._hook_manager.trigger(EVENT_BEFORE_ASSEMBLE, ctx, self._system_state)
    if assembler:
        try:
            messages = assembler.assemble(ctx)
        except Exception as e:
            logger.warning(f"ContextAssembler.assemble() failed: {e}")
            messages = self._fallback_assemble(ctx)
    else:
        messages = self._fallback_assemble(ctx)
    messages = self._hook_manager.trigger(EVENT_AFTER_ASSEMBLE, messages, self._system_state)

    # ── 内层：LLM + Tool call 循环 ──
    tool_iterations = 0
    while True:
        tool_iterations += 1
        if tool_iterations > self._MAX_TOOL_ITERATIONS:
            logger.error(f"Exceeded max tool iterations ({self._MAX_TOOL_ITERATIONS})")
            await self._adapter.send(StopEvent(stop_reason="max_iterations"))
            break

        if not self.call_llm:
            logger.warning("call_llm not set, skipping LLM call")
            await self._adapter.send(StopEvent(stop_reason="no_llm"))
            break

        # --- LLM 调用 ---
        try:
            messages = self._hook_manager.trigger(EVENT_BEFORE_LLM_CALL, messages, self._system_state)
            response = await self.call_llm(              # ← await（同步版无）
                messages_to_dicts(messages),
                tool_definitions_to_openai(self._cached_tools),
            )
            response = self._hook_manager.trigger(EVENT_AFTER_LLM_CALL, response, self._system_state)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

        # --- ① thinking → ThinkingEvent ---
        if response.thinking:
            await self._adapter.send(ThinkingEvent(content=response.thinking))

        # --- ② tool_uses → ToolCallEvent → 执行 → ToolResultEvent ---
        if response.tool_uses:
            # 构造 assistant message → messages
            # 写入 history
            # 串行执行每个 tool，每步推送事件
            for tc in response.tool_uses:
                # JSON 解析 / 推送 ToolCallEvent / 执行 / 推送 ToolResultEvent
                # 与同步版逐行对应，仅 adapter.send 加了 await
                ...
            continue  # 回到内层 LLM 调用

        # --- ③ text → TextEvent + StopEvent → break ---
        if response.text:
            messages.append(Message(role="assistant", content=response.text or ""))
            await self._adapter.send(TextEvent(content=response.text or ""))
            await self._adapter.send(StopEvent(stop_reason=response.stop_reason))
            self._history.append(Message(role="assistant", content=response.text or ""))
            break

        # --- ④ 防御：空响应 ---
        logger.warning("LLM returned empty response (no text, no tool_uses)")
        await self._adapter.send(StopEvent(stop_reason="empty_response"))
        break

    # 返回（不调用 adapter.receive() —— 外层循环由 AgentRuntime.run() 控制）
```

**关键变化（vs 同步版 `_phase_loop`）**：

| 同步版 | 异步版 | 说明 |
|--------|--------|------|
| `adapter.send(event)` | `await self._adapter.send(event)` | 所有 send 加了 await |
| `self.call_llm(...)` | `await self.call_llm(...)` | LLM 调用加了 await |
| 外层 `while not self._should_exit_flag:` | **删除** | 外循环移至 AgentRuntime.run() |
| 末尾 `user_request = adapter.receive()` | **删除** | 下一轮输入由 AgentRuntime 注入 |
| 末尾 `if self._should_exit(user_request): break` | **删除** | 退出检查在 AgentRuntime 层 |
| 末尾 `ctx = AssemblyContext(user_request=...)` | **删除** | AgentRuntime 负责更新 ctx |

#### 4.4.4 `_phase_end(trajectory)` — 会话结束

```
async def _phase_end(self, trajectory: Trajectory) -> None:
    self._system_state.phase = "end"

    # 1. on_session_end Hook
    trajectory = self._hook_manager.trigger(EVENT_ON_SESSION_END, trajectory, self._system_state)

    # 2. Sensor.sense() （同步）
    sensor = self._resolve_optional(Sensor)
    if sensor:
        try:
            sensor.sense(trajectory)
        except Exception as e:
            logger.warning(f"Sensor.sense() failed: {e}")

    # 3. after_sensor Hook
    self._hook_manager.trigger(EVENT_AFTER_SENSOR, trajectory, self._system_state)

    # 4. ToolRouter.shutdown() （同步）
    if self._cached_tool_router:
        try:
            self._cached_tool_router.shutdown()
        except Exception as e:
            logger.warning(f"ToolRouter.shutdown() failed: {e}")

    # 5. 清理内部状态
    self._history.clear()
    self._tool_call_records.clear()
    self._should_exit_flag = False
```

**与同步版的差异**：Sensor、HookManager、ToolRouter 均保持同步调用，不加 `await`。Hook 保持同步——如果后续需要异步 Hook，另开 batch 支持。

#### 4.4.5 `_should_exit()` — 退出检测

与同步版 `LifecycleOrchestrator._should_exit()` **完全一致**，不复制实现——直接从同步版继承逻辑：

- `user_request.text` 为空或仅空白 → 退出
- `user_request.text.strip() == "/exit"` → 退出
- `user_request.metadata.get("exit") is True` → 退出

#### 4.4.6 `_build_trajectory()` 和 `_fallback_assemble()`

与同步版完全一致，逐行复制（无 `await` 调用）。

---

### 4.5 `KernelBridgeAdapter`（初版）

KernelBridgeAdapter 实现 `AsyncInputAdapter`。在 Batch 0-2 期间使用**内联降级路由**（无 MessageBus）；Batch 3 切换到真实 MessageBus。

```python
# harness/runtime/bridge_adapter.py

from ..interfaces.async_input_adapter import AsyncInputAdapter
from ..interfaces.types import (
    UserRequest,
    TextEvent,
    StopEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .types import InternalMessage, __EXIT_SENTINEL__


class KernelBridgeAdapter:
    """实现 AsyncInputAdapter，对接 Kernel 的消息队列。

    Batch 0 初版：
    - receive() 从 input_queues[pid] 取消息，转换 __EXIT_SENTINEL__ 和 InternalMessage
    - send() 内联降级路由（无 MessageBus）：TextEvent → SystemConsole stdout，
      StopEvent → 丢弃，target 非空 → 直接入队 input_queue
    - 退出保护：should_exit 为 True 时静默丢弃所有输出
    - 事件类型过滤：仅 TextEvent/StopEvent 参与路由，
      中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole
    """

    def __init__(self, pid: str, kernel: 'Kernel', runtime: 'AgentRuntime'):
        self._pid = pid
        self._kernel = kernel
        self._runtime = runtime

    async def receive(self) -> UserRequest:
        item = await self._kernel.input_queues[self._pid].get()
        if item is __EXIT_SENTINEL__:
            return UserRequest(text="", metadata={"exit": True})
        elif isinstance(item, InternalMessage):
            return UserRequest(
                text=item.content,
                metadata={**item.metadata, "from": item.from_pid}
            )
        elif isinstance(item, UserRequest):
            return item

    async def send(self, event, target=None):
        if self._runtime.should_exit:
            return  # 退出保护：丢弃"最后一轮污染"

        # ── 事件类型分流 ──
        if not isinstance(event, (TextEvent, StopEvent)):
            # 中间事件：直接推给 SystemConsole（不经过 MessageBus）
            if isinstance(event, (ThinkingEvent, ToolCallEvent, ToolResultEvent)):
                await self._kernel._console.send(
                    AgentOutput(
                        pid=self._pid,
                        content=f"[{type(event).__name__}] {event.content}"
                    )
                )
            return

        if target is not None:
            # 定向投递：不经过 MessageBus，直接入队
            self._kernel.input_queues[target].put_nowait(
                InternalMessage(
                    from_pid=self._pid,
                    content=event.content if isinstance(event, TextEvent) else "",
                    metadata={"stop": True} if isinstance(event, StopEvent) else {},
                )
            )
        else:
            # Batch 0-2 降级路由（无 MessageBus）
            if isinstance(event, TextEvent):
                await self._kernel._console.send(
                    AgentOutput(pid=self._pid, content=event.content)
                )
            # StopEvent → 静默丢弃
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 事件类型过滤位置 | KBA.send() 内部 | 将过滤逻辑放在离 agent 最近的地方。MessageBus 只接收 TextEvent/StopEvent，不需要知道其他事件类型 |
| 中间事件降级格式 | `[{EventTypeName}] {content}` | 简单可读。后续 CliConsole 可以选择性展示（如 `--verbose` 模式下显示 ThinkingEvent） |
| 退出保护位置 | KBA.send() 最开头 | 在消息进入任何队列/总线之前拦截。不依赖 MessageBus 或 Kernel 的策略 |
| `target` 非空路由 | 直接 `input_queues[target].put_nowait()` | Batch 0 无 MessageBus，定向投递直接用队列。Batch 3 切换为 `message_bus.direct()` |
| 降级路由目标 | `self._kernel._console.send(AgentOutput(...))` | Kernel 持有 SystemConsole 引用。SystemConsole 在 Batch 1 定义 Protocol，Batch 1 实现 CliConsole。Batch 0 测试时 Kernel 传 mock console |

**KBA 的依赖说明**：

KBA 在构造时需要 `kernel` 和 `runtime` 参数，但这两个类在 Batch 1 才定义。Batch 0 的解决方案：

- **单元测试**：传入 mock/stub 对象，满足属性访问即可
- **集成测试**：在 `AsyncLifecycleOrchestrator` 测试中以最小 stub 验证 KBA.send/receive 的行为
- **KBA 自身不 import Kernel/AgentRuntime**：使用字符串类型注解（`'Kernel'`, `'AgentRuntime'`）或 `typing.TYPE_CHECKING`

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel import Kernel
    from .agent_runtime import AgentRuntime
```

---

### 4.6 `AgentOutput` SystemEvent（临时定义）

在 Batch 0，`SystemConsole` Protocol 尚未定义（Batch 1），但 KBA 需要向 console 推送降级输出。临时方案：

```python
# harness/runtime/types.py（追加）

@dataclass
class AgentOutput:
    """agent 的 TextEvent 无订阅者时的降级通知。
    
    注意：此类型在 Batch 1 会随 SystemConsole Protocol 正式化。
    Batch 0 仅为 KBA 降级路由提供最小载体。
    """
    pid: str = ""
    content: str = ""
```

SystemConsole Protocol 在 Batch 1 定义后，`AgentOutput` 移入正式 SystemEvent 体系。

---

## 五、数据流

### 5.1 AsyncLifecycleOrchestrator 单轮执行

```
┌─ AgentRuntime.run()（Batch 1）或 测试代码 ─────────────────────────┐
│                                                                     │
│  ctx = await orch._phase_init()                                     │
│  │    ├─ await adapter.receive() → UserRequest("hello")             │
│  │    ├─ GuideProvider / MemoryBackend / ToolRouter 初始化           │
│  │    └─ return AssemblyContext(user_request=..., guides=..., ...)  │
│  │                                                                  │
│  await orch._phase_loop(ctx)                                        │
│  │    ├─ history.append(user msg)                                   │
│  │    ├─ ContextAssembler.assemble(ctx) → messages                  │
│  │    ├─ await call_llm(messages, tools) → Response                 │
│  │    ├─ await adapter.send(TextEvent("回答内容"))                    │
│  │    ├─ await adapter.send(StopEvent(stop_reason="end_turn"))       │
│  │    └─ return（不调用 adapter.receive()）                          │
│  │                                                                  │
│  traj = orch._build_trajectory()                                    │
│  await orch._phase_end(traj)                                        │
│       ├─ Sensor.sense(traj)                                         │
│       └─ 清理内部状态                                                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 KernelBridgeAdapter 消息转换路径

```
                 ┌────────────────────────────┐
                 │   Kernel.input_queues[pid]  │
                 └──────────┬─────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        UserRequest   InternalMessage  __EXIT_SENTINEL__
              │             │             │
              ▼             ▼             ▼
    KBA.receive()    KBA.receive()   KBA.receive()
    原样返回         转换为            转换为
                    UserRequest      UserRequest
                    (metadata        (text="",
                     .from=from_pid)  metadata={exit:True})
```

```
KBA.send(event, target=None)
  │
  ├─ should_exit? → True → return（静默丢弃）
  │
  ├─ event 是 TextEvent/StopEvent?
  │   ├─ Yes → 继续
  │   └─ No (Thinking/ToolCall/ToolResult) → console.send(AgentOutput(...)) → return
  │
  ├─ target 非空?
  │   └─ Yes → input_queues[target].put_nowait(InternalMessage(...)) → return
  │
  └─ target 为 None（降级路由）
      ├─ TextEvent → console.send(AgentOutput(pid, content))
      └─ StopEvent → 静默丢弃
```

---

## 六、错误处理

### 6.1 异常分类与策略

| 异常来源 | 处理位置 | 策略 |
|---------|---------|------|
| `adapter.receive()` 异常 | `_phase_init` / AgentRuntime.run | 传播到调用方。AgentRuntime（Batch 1）在 finally 中确保 _phase_end 执行 |
| `call_llm()` 异常 | `_phase_loop` 内层 | 同步版行为：raise 传播到 run() → _phase_end 在 finally 中执行。异步版保持一致 |
| `adapter.send()` 异常 | 各调用点 | 不 catch——send 失败意味着 I/O 通道断开，应传播到顶层 |
| `ContextAssembler.assemble()` 异常 | `_phase_loop` | 同步版行为：WARNING 日志 + `_fallback_assemble()` 降级。异步版保持一致 |
| `Sensor.sense()` 异常 | `_phase_end` | WARNING 日志，不阻止清理流程 |
| `ToolRouter.shutdown()` 异常 | `_phase_end` | WARNING 日志，不阻止状态清理 |
| Hook 内部异常 | `HookManager.trigger` | HookManager 已内置 catch+WARNING。无需额外处理 |
| `__EXIT_SENTINEL__` 在 init 阶段 | `_phase_init` | `_should_exit_flag = True`，返回最小 ctx。行为与同步版一致 |

### 6.2 `__EXIT_SENTINEL__` 边界条件

| 场景 | 行为 |
|------|------|
| init 阶段收到 sentinel | `_should_exit_flag = True`，返回 `AssemblyContext(user_request=UserRequest(text="", metadata={exit:True}))` |
| loop 阶段收到 sentinel | `_should_exit()` 检测到 `metadata["exit"]` → loop 中的内层 while break → 返回到 AgentRuntime.run() |
| 重复 sentinel | `KBA.receive()` 每次从队列取一个。第一次取到 sentinel → 返回 `UserRequest(metadata={exit:True})`。第二次取时 `should_exit` 已为 True，外层 while 已 break |

---

## 七、测试策略

### 7.1 测试框架与模式

与现有测试保持一致：pytest + 手动 mock（不使用 mock 库）。

### 7.2 单元测试场景

#### AsyncInputAdapter Protocol

| 测试 | 验证点 |
|------|--------|
| Protocol 可被 `isinstance` 检查 | `@runtime_checkable` 生效 |
| 缺少 `receive()` 的类不满足 Protocol | `isinstance(obj, AsyncInputAdapter)` 为 False |
| 缺少 `send()` 的类不满足 Protocol | 同上 |
| `send()` 缺少 `target` 参数的类仍满足 Protocol | `target` 有默认值，调用方不传也能工作 |

#### InternalMessage & __EXIT_SENTINEL__

| 测试 | 验证点 |
|------|--------|
| `InternalMessage` 默认构造 | 字段默认值正确 |
| `InternalMessage` 自定义构造 | 所有字段可设置 |
| `__EXIT_SENTINEL__` 身份比较 | `item is __EXIT_SENTINEL__` 为 True，`item == __EXIT_SENTINEL__` 也为 True（同一对象） |
| 另一个 `object()` 不是 sentinel | `object() is not __EXIT_SENTINEL__` |

#### AsyncLifecycleOrchestrator（mock async call_llm + mock KBA）

| 测试 | 验证点 |
|------|--------|
| 构造：仅 container + adapter | 初始化成功，状态干净 |
| 构造：带 call_llm | `call_llm` 正确存储 |
| `_phase_init`：最小组件（仅 adapter） | 返回 AssemblyContext，user_request 正确 |
| `_phase_init`：首轮 exit | `_should_exit_flag = True`，返回最小 ctx |
| `_phase_init`：with GuideProvider | guides 正确缓存 |
| `_phase_init`：with MemoryBackend | memories 正确检索 |
| `_phase_loop`：纯文本单轮 | TextEvent + StopEvent 顺序发送；history 正确记录 |
| `_phase_loop`：tool_use → text | 两次 call_llm；tool 正确执行；history 含 tool 记录 |
| `_phase_loop`：text + tool_uses 共存 | 内层继续循环，中间 text 不发用户，最终 text 发送 |
| `_phase_loop`：tool 执行失败 | error 记录到 tool_call_records |
| `_phase_loop`：call_llm 为 None | 不崩溃，发 StopEvent("no_llm") |
| `_phase_loop`：不调用 adapter.receive() | 单轮结束后返回，不等待输入 |
| `_phase_end`：正常流程 | Sensor 被调用，状态清理 |
| `_phase_end`：无 Sensor | 不崩溃 |
| `_phase_end`：Sensor 抛异常 | WARNING 日志，后续清理继续 |
| `_build_trajectory` | final_output / execution_time 正确 |
| 异常时 `_phase_end` 仍被执行 | （同同步版 finally 语义） |

#### KernelBridgeAdapter

| 测试 | 验证点 |
|------|--------|
| `receive()`: UserRequest 原样返回 | 入队 UserRequest → 取出相同对象 |
| `receive()`: InternalMessage → UserRequest | `from_pid` 进入 `metadata["from"]` |
| `receive()`: `__EXIT_SENTINEL__` → UserRequest with exit | `text=""`, `metadata={"exit": True}` |
| `send()`: should_exit=True → 丢弃 | 消息不入队，不抛异常 |
| `send()`: ThinkingEvent → 降级到 console | console.send 被调用 |
| `send()`: ToolCallEvent → 降级到 console | console.send 被调用 |
| `send()`: ToolResultEvent → 降级到 console | console.send 被调用 |
| `send()`: TextEvent, target=None → 降级到 console | console.send(AgentOutput(...)) |
| `send()`: StopEvent, target=None → 丢弃 | console.send 不被调用，不抛异常 |
| `send()`: TextEvent, target=pid → 入队 | target 的 input_queue 收到 InternalMessage |
| `send()`: StopEvent, target=pid → 入队 | InternalMessage 含 `metadata={"stop": True}` |

### 7.3 需要的 Mock/Stub

| Mock 对象 | 最小接口 | 用途 |
|----------|---------|------|
| `MockKernel` | `input_queues: dict[str, asyncio.Queue]`, `_console` (有 `async send()` 方法) | KBA 测试 |
| `MockRuntime` | `should_exit: bool` 属性 | KBA 退出保护测试 |
| `MockAsyncAdapter` | `async receive() → UserRequest`, `async send(event, target=None)` | Orchestrator 单元测试 |
| `MockAsyncLLM` | `async def __call__(msgs, tools) → Response` | Orchestrator 单元测试 |

### 7.4 现有测试回归

Batch 0 **不改动任何现有文件**（除两个 `__init__.py` 追加 re-export），因此现有 22 个测试文件应全部继续通过。运行 `pytest` 验证。

---

## 八、验收标准

### AC-0.1 AsyncInputAdapter & AsyncCallLLM Protocol
- [ ] `AsyncInputAdapter` Protocol 定义在 `harness/interfaces/async_input_adapter.py`
- [ ] `@runtime_checkable`，`isinstance(obj, AsyncInputAdapter)` 可用于运行时检查
- [ ] `send()` 的 `target` 参数有默认值 `None`
- [ ] `AsyncCallLLM` Protocol 定义在 `harness/interfaces/async_call_llm.py`
- [ ] `tools` 参数可选，默认值 `None`（与同步版 `Callable[..., Any]` 约定一致）
- [ ] `harness/interfaces/__init__.py` 中 re-export `AsyncInputAdapter` 和 `AsyncCallLLM`

### AC-0.2 AsyncLifecycleOrchestrator
- [ ] 构造函数显式接收 `adapter` 参数（不走 DI 容器解析）
- [ ] 构造函数接收 `call_llm: AsyncCallLLM | None`，`None` 为测试/Hook 兼容预留
- [ ] `_phase_init()` 使用 `await adapter.receive()` 获取首条输入
- [ ] `_phase_loop(ctx)` **仅执行单轮**（内循环），不调用 `adapter.receive()`，不包含外循环 while
- [ ] `_phase_end(trajectory)` 完成 Sensor + 清理，Sensor/Hook/ToolRouter 保持同步调用
- [ ] `_should_exit()` / `_build_trajectory()` / `_fallback_assemble()` 行为与同步版一致

### AC-0.3 InternalMessage & __EXIT_SENTINEL__
- [ ] `InternalMessage` dataclass 定义在 `harness/runtime/types.py`
- [ ] `__EXIT_SENTINEL__` 模块级 sentinel 对象，使用 `is` 身份比较
- [ ] `AgentOutput` 临时 dataclass 定义在 `harness/runtime/types.py`

### AC-0.4 KernelBridgeAdapter
- [ ] 实现 `AsyncInputAdapter` Protocol
- [ ] `receive()` 正确转换三种入队类型：`UserRequest` / `InternalMessage` / `__EXIT_SENTINEL__`
- [ ] `send()` 含退出保护（`should_exit` 检查）
- [ ] `send()` 事件类型过滤：仅 `TextEvent`/`StopEvent` 参与路由
- [ ] `send()` 内联降级路由：`TextEvent` → console, `StopEvent` → 丢弃
- [ ] `send()` 定向投递：`target=pid` 时直接入队 `input_queues[pid]`
- [ ] 不 import `Kernel`/`AgentRuntime`（使用 TYPE_CHECKING + 字符串注解）

### AC-0.5 包结构
- [ ] `harness/runtime/__init__.py` 创建（空模块或仅 docstring）
- [ ] `harness/interfaces/__init__.py` 新增 `AsyncInputAdapter` re-export
- [ ] `harness/core/__init__.py` 新增 `AsyncLifecycleOrchestrator` re-export

### AC-0.6 现有测试不退化
- [ ] `pytest` 全部现有 22 个测试文件通过
- [ ] 无 import 错误（新增模块不影响已有 import 路径）

### AC-0.7 新测试
- [ ] AsyncInputAdapter Protocol 满足性测试 ≥ 3 条
- [ ] InternalMessage / __EXIT_SENTINEL__ 行为测试 ≥ 3 条
- [ ] AsyncLifecycleOrchestrator 三阶段走通（mock async LLM）≥ 10 条
- [ ] KernelBridgeAdapter 消息转换/过滤/降级测试 ≥ 8 条

---

## 九、与后续 Batch 的接口约定

### 9.1 Batch 0 → Batch 1

```
暴露:
  from harness.interfaces.async_input_adapter import AsyncInputAdapter
  from harness.interfaces.async_call_llm import AsyncCallLLM
  from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
  from harness.runtime.bridge_adapter import KernelBridgeAdapter
  from harness.runtime.types import InternalMessage, __EXIT_SENTINEL__, AgentOutput

Batch 1 依赖:
  - AsyncCallLLM Protocol：Batch 1 Runtime.run() 入口层做 sync→async LLM bridge
    时，桥接产物的类型目标即 AsyncCallLLM
  - AsyncLifecycleOrchestrator._phase_loop(ctx) 仅跑一轮，Batch 1 AgentRuntime.run()
    在外层 while 中调用它
  - KernelBridgeAdapter 已实现 AsyncInputAdapter，Batch 1 的 AgentRuntime
    在 spawn 时创建 KBA 实例并注入 AsyncLifecycleOrchestrator
  - __EXIT_SENTINEL__ 已定义，Kernel 在 kill/end_workflow 时使用
  - InternalMessage 已定义，MessageBus 和 KBA 共用
```

### 9.2 Batch 3 → KBA 升级

```
Batch 0-2 KBA:
  - 内联降级路由（TextEvent → console, StopEvent → 丢弃）
  - 定向投递走 input_queue 直接 put_nowait

Batch 3 升级:
  - target=None → message_bus.publish(from_pid, event, on_no_subscriber=...)
  - target=pid → message_bus.direct(target, InternalMessage(...))
  - 降级回调由 message_bus.publish 的 on_no_subscriber 参数注入
```

### 9.3 职责边界（本 Batch 不做的内容）

以下内容**不属于 Batch 0**，在后续 batch 中实现：

- ❌ `AgentRuntime` 类及 `AgentState` 枚举（Batch 1）
- ❌ `Kernel` 类及进程表管理（Batch 1）
- ❌ `SystemConsole` Protocol 及 `CliConsole` 实现（Batch 1）
- ❌ `Runtime` 顶层入口及 `run()` 方法（Batch 1）
- ❌ sync→async LLM bridge（`asyncio.to_thread`）（Batch 1，在 Runtime.run() 入口层）
- ❌ `@agent` / `subscribe` 装饰器（Batch 2）
- ❌ `spawn_workflow` 等 Runtime tool（Batch 2）
- ❌ `MessageBus`（Batch 3）
- ❌ 流式订阅、级联终止、静默检测（Batch 3）
- ❌ 系统命令解析（Batch 4）
- ❌ SIGINT 信号处理（Batch 4）
