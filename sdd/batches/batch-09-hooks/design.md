# batch-09: Hooks — 架构设计

> 版本: 1.0
> 依赖: batch-02-1（Hook 接口、HookContext、Hook 类型别名）、batch-07（Sensor，after_sensor Hook 点）、batch-08（InputAdapter，编排器完整控制流已就绪）

---

## 1. 设计目标

实现框架的 Hook 系统，在 11 个生命周期关键节点拦截并修改数据。

1. **Hook 事件常量化**：将 `harness/interfaces/hook.py` docstring 中的 11 个事件名提取为正式常量
2. **HookManager 实现**：提供注册、注销、触发的统一机制
3. **Orchestrator 集成**：在 11 个生命周期点按正确顺序触发 Hook
4. **异常隔离**：单个 Hook 异常不阻塞其他 Hook 和框架流程

---

## 2. 架构位置

```
┌─────────────────────────────────────────┐
│      LifecycleOrchestrator              │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │        HookManager              │    │
│  │  event → [hook1, hook2, ...]    │    │
│  │                                 │    │
│  │  register(event, hook)          │    │
│  │  unregister(event, hook)        │    │
│  │  trigger(event, data, state)    │    │
│  └─────────────────────────────────┘    │
│                    │                    │
│  ┌─────────────────┼─────────────────┐  │
│  ▼                 ▼                 ▼  │
│  Phase 1        Phase 2           Phase 3│
│  before/after  before/after      on_session_end
│  guide         assemble          after_sensor
│                llm_call          on_error
│                tool_execute
└─────────────────────────────────────────┘
```

Hook 在整个框架中的角色：
- **控制流不修改**：框架三阶段固定顺序不变
- **数据流可修改**：Hook 通过修改 `HookContext.data` 拦截并变更数据
- **只读观察**：`after_sensor` 明确标记为只读观察点

---

## 3. 接口回顾

### 3.1 Hook 接口（已在 `harness/interfaces/hook.py` 定义）

```python
@dataclass
class HookContext:
    event: str = ""
    data: Any = None
    system_state: SystemState = field(default_factory=SystemState)

# Hook 是函数类型别名
Hook = Callable[[HookContext], None]
```

### 3.2 11 个 Hook 点（事件名 → data 类型映射）

| 事件名 | data 类型 | 可修改 | 触发阶段 |
|--------|----------|--------|---------|
| `before_guide_generation` | `AssemblyContext` | ✅ | Phase 1，GuideProvider.get_guides() 之前 |
| `after_guide_generation` | `GuidesBundle` | ✅ | Phase 1，GuideProvider.get_guides() 之后 |
| `before_assemble` | `AssemblyContext` | ✅ | Phase 2 外层循环，ContextAssembler.assemble() 之前 |
| `after_assemble` | `List[Message]` | ✅ | Phase 2 外层循环，ContextAssembler.assemble() 之后 |
| `before_llm_call` | `List[Message]` | ✅ | Phase 2 内层循环，call_llm() 之前 |
| `after_llm_call` | `Response` | ✅ | Phase 2 内层循环，call_llm() 之后 |
| `before_tool_execute` | `ToolCall` | ✅ | Phase 2 内层循环，每个 tool 执行之前 |
| `after_tool_execute` | `ToolResult` | ✅ | Phase 2 内层循环，每个 tool 执行之后 |
| `after_sensor` | `Trajectory` | ⚠️ 只读建议 | Phase 3，Sensor.sense() 之后 |
| `on_session_end` | `Trajectory` | ✅ | Phase 3 开始，Sensor.sense() 之前 |
| `on_error` | `Exception` | ✅ | run() except 块中 |

> **注意**：`before_guide_generation` 的 data 类型当前为 `AssemblyContext`（与 orchestrator 中实际传给 GuideProvider 的参数一致），而非 `GuideContext`。这是因为 orchestrator `_phase_init()` 中实际构造的是 `AssemblyContext(user_request=user_request)` 并传给 `guide_provider.get_guides()`。Hook 接收的是同一个对象，因此类型与运行时一致。

---

## 4. HookManager 设计

### 4.1 类结构

```python
class HookManager:
    """Hook 注册与执行管理器。

    职责：维护事件名到 Hook 函数列表的映射，按注册顺序依次触发。
    不依赖 DI 容器，由 LifecycleOrchestrator 在 __init__ 中实例化。
    """

    def __init__(self):
        self._hooks: Dict[str, List[Hook]] = {}

    def register(self, event: str, hook: Hook) -> None:
        """注册一个 Hook 到指定事件。"""
        ...

    def unregister(self, event: str, hook: Hook) -> None:
        """从指定事件注销一个 Hook。"""
        ...

    def trigger(self, event: str, data: Any, system_state: SystemState) -> Any:
        """触发事件，执行所有注册的 Hook。

        行为：
        - 按注册顺序依次调用 Hook
        - 每个 Hook 可修改 context.data，修改对后续 Hook 可见
        - 单个 Hook 抛异常时：记录 WARNING，跳过该 Hook，继续执行后续 Hook
        - 无 Hook 注册时：直接返回原始 data

        Returns:
            经过所有 Hook 处理后的 data（可能已被修改）。
        """
        ...
```

### 4.2 `register()` 详细行为

1. 校验 `event` 为字符串（非空）
2. 校验 `hook` 为可调用对象
3. 将 hook 追加到对应事件的列表末尾
4. 同一 hook 可被多次注册到同一事件（每次注册独立，unregister 只移除第一次匹配）

### 4.3 `trigger()` 详细行为

1. 构造 `HookContext(event=event, data=data, system_state=system_state)`
2. 查询该事件注册的 Hook 列表
3. 列表为空 → 直接返回 `data`
4. 列表非空 → 依次调用每个 Hook：`hook(context)`
5. 每个 Hook 调用后，`context.data` 可能已被修改
6. 单个 Hook 异常时：
   - 记录 WARNING 日志（含事件名和异常信息）
   - 继续执行后续 Hook
7. 返回最终的 `context.data`

### 4.4 `unregister()` 详细行为

1. 查找事件对应的 Hook 列表
2. 使用 `is` 身份比较移除第一次匹配的 hook
3. 未找到时不抛异常，记录 DEBUG 日志
4. 移除后列表为空时保留空列表（不删除 key）

### 4.5 异常隔离策略

```
Hook1 ──异常──┐
              ├─→ 记录 WARNING，继续
Hook2 ──正常──┤
              ├─→ 正常执行
Hook3 ──异常──┘
              └─→ 记录 WARNING，继续
```

单个 Hook 的异常不影响：
- 同事件的其他 Hook 执行
- 框架主流程继续

---

## 5. Orchestrator 集成

### 5.1 Orchestrator 修改点

在 `LifecycleOrchestrator` 中：

1. **`__init__`** 新增：创建 `self._hook_manager = HookManager()`
2. **新增公开方法**：`register_hook(event: str, hook: Hook) -> None`
3. **`_phase_init()`** 插入 2 个 Hook 点
4. **`_phase_loop()`** 插入 6 个 Hook 点
5. **`_phase_end()`** 插入 2 个 Hook 点
6. **`run()`** 插入 1 个 Hook 点（on_error）

### 5.2 各 Hook 点精确插入位置

#### Phase 1: 会话初始化（_phase_init）

**before_guide_generation**（L~174，guide_provider.get_guides 之前）：
```python
guide_ctx = AssemblyContext(user_request=user_request)
# ← INSERT: before_guide_generation
guide_ctx = self._hook_manager.trigger(
    EVENT_BEFORE_GUIDE_GENERATION, guide_ctx, self._system_state
)
guides = guide_provider.get_guides(guide_ctx)
```

**after_guide_generation**（L~179，get_guides 返回之后）：
```python
guides = guide_provider.get_guides(guide_ctx)
# ← INSERT: after_guide_generation
guides = self._hook_manager.trigger(
    EVENT_AFTER_GUIDE_GENERATION, guides, self._system_state
)
```

#### Phase 2: 多轮对话循环（_phase_loop）

**before_assemble**（L~281，assembler.assemble 之前）：
```python
# === 外层：组装上下文 ===
# ← INSERT: before_assemble
ctx = self._hook_manager.trigger(
    EVENT_BEFORE_ASSEMBLE, ctx, self._system_state
)
if assembler:
    messages = assembler.assemble(ctx)
else:
    messages = self._fallback_assemble(ctx)
# ← INSERT: after_assemble
messages = self._hook_manager.trigger(
    EVENT_AFTER_ASSEMBLE, messages, self._system_state
)
```

**before_llm_call**（L~305，call_llm 之前）：
```python
try:
    # ← INSERT: before_llm_call
    messages = self._hook_manager.trigger(
        EVENT_BEFORE_LLM_CALL, messages, self._system_state
    )
    response = self.call_llm(
        messages_to_dicts(messages),
        tool_definitions_to_openai(self._cached_tools),
    )
    # ← INSERT: after_llm_call
    response = self._hook_manager.trigger(
        EVENT_AFTER_LLM_CALL, response, self._system_state
    )
```

**before_tool_execute**（L~340，tool_router.execute 之前）：
```python
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
    else:
        # ← INSERT: before_tool_execute
        tc = self._hook_manager.trigger(
            EVENT_BEFORE_TOOL_EXECUTE, tc, self._system_state
        )
        try:
            if tool_router and tool_router.has_tool(tc.function.name):
                result = tool_router.execute(tc.function.name, args)
            else:
                error = ...
        except Exception as e:
            error = str(e)
        after_ts = time.time()
```

**after_tool_execute**（L~355，ToolResult 字段提取之后、记录到 tool_call_records 之前）：
```python
        # 提取 ToolResult 字段
        if hasattr(result, "success"):
            success = result.success
            content = result.content if hasattr(result, "content") else str(result)
            if hasattr(result, "error") and result.error:
                error = result.error
        else:
            success = error is None
            content = result

        # ← INSERT: after_tool_execute
        tool_result = ToolResult(success=success, content=content, error=error)
        tool_result = self._hook_manager.trigger(
            EVENT_AFTER_TOOL_EXECUTE, tool_result, self._system_state
        )
        success = tool_result.success
        content = tool_result.content
        error = tool_result.error

        # 记录到 tool_call_records（使用可能已被 Hook 修改的值）
        ...
```

#### Phase 3: 会话结束（_phase_end）

**on_session_end**（L~434，phase_end 开始时、Sensor 之前）：
```python
def _phase_end(self, trajectory: Trajectory) -> None:
    logger.info("Phase 3: Session end starting")

    # ← INSERT: on_session_end
    trajectory = self._hook_manager.trigger(
        EVENT_ON_SESSION_END, trajectory, self._system_state
    )

    # 1. Sensor（可选）
    sensor = self._resolve_optional(Sensor)
    ...
```

**after_sensor**（L~448，Sensor.sense 之后）：
```python
    if sensor:
        try:
            sensor.sense(trajectory)
            logger.debug("Sensor.sense() completed")
        except Exception as e:
            logger.warning(f"Sensor.sense() failed: {e}")

    # ← INSERT: after_sensor
    self._hook_manager.trigger(
        EVENT_AFTER_SENSOR, trajectory, self._system_state
    )
```

#### run() — on_error

**on_error**（L~124，except 块中、raise 之前）：
```python
except Exception as e:
    logger.error(f"Orchestrator error: {e}")
    # ← INSERT: on_error
    self._hook_manager.trigger(
        EVENT_ON_ERROR, e, self._system_state
    )
    if isinstance(e, (ComponentNotRegisteredError, OrchestratorError)):
        raise
    raise OrchestratorError(str(e)) from e
```

### 5.3 system_state 更新

Orchestrator 中新增 `self._system_state: SystemState` 字段，在各阶段更新：

```python
# _phase_init 开始
self._system_state.phase = "init"
self._system_state.session_id = user_request.session_id

# _phase_loop 开始
self._system_state.phase = "loop"

# _phase_end 开始
self._system_state.phase = "end"
```

`system_state` 通过 `HookContext` 传递给所有 Hook，让 Hook 知晓当前所处阶段。

### 5.4 边界情况处理

| 场景 | 影响的事件 | 行为 |
|------|-----------|------|
| **Phase 1 提前退出**（用户第一轮输入 `/exit`） | `before_guide_generation`, `after_guide_generation` | **不触发**。`guide_provider.get_guides()` 未被调用，因此相关 Hook 不触发 |
| **assembler 为 None** | `before_assemble`, `after_assemble` | **仍然触发**。`before_assemble` 在 `if assembler:` 之前触发，`after_assemble` 在 `_fallback_assemble()` 之后触发 |
| **call_llm 为 None** | `before_llm_call`, `after_llm_call` | **不触发**。无 LLM 调用发生时，这两个 Hook 跳过 |
| **tool 参数 JSON 解析失败** | `before_tool_execute`（不触发），`after_tool_execute`（触发） | `before_tool_execute` 不触发（在 `json.loads()` 成功后才进入分支）。`after_tool_execute` 触发（在 `if/else` 块外部），接收含 `error` 的 `ToolResult` |
| **call_llm 抛异常** | `after_llm_call` | **不触发**。`after_llm_call` 只在 LLM 调用成功返回后触发；异常直接进入 `except` 块，可能触发 `on_error` |
| **on_error Hook 抛异常** | `on_error` 自身 | **不影响**。`trigger()` 的异常隔离机制同样适用于 `on_error` 事件。单个 `on_error` Hook 抛异常时：记录 WARNING，继续执行其他 `on_error` Hook，不影响原始异常的上抛 |
| **Phase 3 在 finally 中执行** | `on_session_end` | **始终触发**。即使 `try` 块中发生异常，`finally` 确保 `_phase_end()` 被调用，`on_session_end` 接收的 Trajectory 可能不完整（如 `_history` 只包含部分消息） |

---

## 6. 组件树

```
harness/interfaces/
├── hook.py              # HookContext + Hook 类型别名 (已存在，仅更新 docstring)

harness/hooks/           # NEW 目录
├── __init__.py          # 导出 EVENT_* 常量和 HookManager
├── events.py            # 11 个事件名常量 (NEW)
└── hook_manager.py      # HookManager 实现 (NEW)

harness/core/
└── orchestrator.py      # 集成 HookManager + 11 个触发点 (MODIFY)

tests/
├── test_hooks.py        # HookManager 单元测试 + Orchestrator Hook 集成测试 (NEW)
```

---

## 7. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | HookManager 不由 DI 容器管理 | HookManager 是框架内部机制，不是用户可替换的组件。用户通过 orchestrator.register_hook() 注册 Hook 函数 |
| 2 | Hook 异常不阻塞其他 Hook | Hook 是用户自定义扩展，单个 Hook 的 bug 不应影响框架主流程和其他 Hook |
| 3 | 同一事件支持多个 Hook | 允许多个独立功能模块各注册自己的 Hook，互不干扰 |
| 4 | unregister 使用 `is` 比较 | 函数对象的唯一标识比较，避免 `==` 可能引发的意外行为 |
| 5 | after_sensor 标记为"只读建议" | 技术上 Hook 仍可修改 data，但语义上设计为观察 Sensor 副作用，修改无意义 |
| 6 | before_guide_generation 使用 AssemblyContext | 与 orchestrator 中实际传给 GuideProvider 的参数类型一致（当前实现传的是 AssemblyContext 而非 GuideContext） |
| 7 | after_tool_execute 接收 ToolResult | 将工具执行结果规范化为 ToolResult 后传给 Hook，提供统一接口 |
| 8 | Hook 通过修改 context.data 实现拦截 | 与 hook.py 中已定义的 Hook 函数签名 `(context: HookContext) -> None` 保持一致 |

---

## 8. 与前后批次的接口约定

### 8.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| Hook 接口定义 | batch-02-1 `interfaces/hook.py` | 直接复用 HookContext、Hook 类型 |
| Orchestrator 控制流 | batch-02-1 `core/orchestrator.py` | 在 11 个点插入 Hook 触发 |
| 所有大包对象类型 | batch-02 `interfaces/types.py` | Hook data 的类型来源 |
| Sensor | batch-07 `components/sensor/` | after_sensor Hook 在 Sensor.sense() 之后触发 |
| InputAdapter | batch-08 `components/input_adapter/` | 编排器完整控制流已就绪 |

### 8.2 严格不在范围内

- ❌ 不修改 `harness/interfaces/hook.py` 中的类型定义（HookContext、Hook）
- ❌ 不修改 DI 容器 `harness/core/container.py`
- ❌ 不实现具体的 Hook 函数（用户自定义）
- ❌ 不修改其他组件的实现（GuideProvider、ContextAssembler、Sensor 等）
- ❌ 不修改 Harness 类（`harness/di.py`）
- ❌ 不添加异步 Hook 支持
- ❌ 不添加 Hook 优先级/排序机制（按注册顺序）
- ❌ 不添加 Hook 中间件链（如 pre/post hook 包装）

### 8.3 为后续批次提供的基础

| 产出 | 被哪些批次使用 | 使用方式 |
|------|--------------|---------|
| Hook 事件常量 | batch-10 | DI 装配文档中引用 |
| HookManager | 框架内核 | 所有后续生命周期扩展的基础 |
| Orchestrator Hook 集成 | batch-10 | 端到端验收时验证 Hook 触发 |
