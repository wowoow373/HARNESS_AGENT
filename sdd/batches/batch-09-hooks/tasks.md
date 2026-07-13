# batch-09: Hooks — 任务清单

> 实现框架 Hook 系统：事件常量、HookManager、Orchestrator 集成、测试。

---

## 任务 1：Hook 事件常量模块

**目标**：将 11 个 Hook 事件名提取为正式常量。

**文件**：`harness/hooks/events.py`（新建）

**详细要求**：
- 定义 11 个 `str` 类型常量，对应 11 个生命周期事件
- 常量命名：`EVENT_<事件名大写下划线>`
- 值保持为小写下划线格式，与 architecture.md 和 hook.py 中的命名一致

**常量列表**：
```python
EVENT_BEFORE_GUIDE_GENERATION = "before_guide_generation"
EVENT_AFTER_GUIDE_GENERATION = "after_guide_generation"
EVENT_BEFORE_ASSEMBLE = "before_assemble"
EVENT_AFTER_ASSEMBLE = "after_assemble"
EVENT_BEFORE_LLM_CALL = "before_llm_call"
EVENT_AFTER_LLM_CALL = "after_llm_call"
EVENT_BEFORE_TOOL_EXECUTE = "before_tool_execute"
EVENT_AFTER_TOOL_EXECUTE = "after_tool_execute"
EVENT_AFTER_SENSOR = "after_sensor"
EVENT_ON_SESSION_END = "on_session_end"
EVENT_ON_ERROR = "on_error"
```

**验收**：常量值与 architecture.md 中定义的事件名完全一致。

---

## 任务 2：HookManager 实现

**目标**：实现 Hook 注册、注销、触发的统一管理器。

**文件**：`harness/hooks/hook_manager.py`（新建）

**详细要求**：

### 2.1 `__init__`
- 初始化 `self._hooks: Dict[str, List[Hook]] = {}`

### 2.2 `register(event: str, hook: Hook) -> None`
- 校验 `event` 为非空字符串
- 校验 `hook` 为 callable
- 将 hook 追加到 `self._hooks[event]` 列表
- 首次注册某事件时自动创建空列表

### 2.3 `unregister(event: str, hook: Hook) -> None`
- 查找 `self._hooks[event]` 列表
- 使用 `is` 身份比较移除第一次匹配
- 事件不存在或 hook 不存在时：记录 DEBUG 日志，不抛异常

### 2.4 `trigger(event: str, data: Any, system_state: SystemState) -> Any`
- 构造 `HookContext(event=event, data=data, system_state=system_state)`
- 获取该事件的 hook 列表
- 列表为空：直接返回 data
- 列表非空：按顺序调用每个 hook，传入同一个 context 对象
- 单个 hook 异常：记录 WARNING 日志（含事件名和异常信息），继续执行后续 hook
- 返回最终的 `context.data`

**验收**：
- register/unregister/trigger 三个方法完整实现
- 异常隔离：单个 hook 异常不影响其他 hook
- 空列表返回原始 data
- 类型标注完整

---

## 任务 3：Hook 模块包初始化

**目标**：创建 `harness/hooks/` 包并导出公开 API。

**文件**：`harness/hooks/__init__.py`（新建）

**详细要求**：
- 从 `events.py` 导入并导出所有 `EVENT_*` 常量
- 从 `hook_manager.py` 导入并导出 `HookManager`
- 定义 `__all__` 列表

**验收**：`from harness.hooks import HookManager, EVENT_BEFORE_LLM_CALL` 可正常工作。

---

## 任务 4：Orchestrator 集成 HookManager

**目标**：在 `LifecycleOrchestrator` 中集成 HookManager 并在 11 个点触发。

**文件**：`harness/core/orchestrator.py`（修改）

**详细要求**：

### 4.1 `__init__` 修改
- 导入 `HookManager` 和事件常量
- 新增 `self._hook_manager = HookManager()`
- 新增 `self._system_state = SystemState()`

### 4.2 新增 `register_hook(event: str, hook: Hook) -> None`
- 代理到 `self._hook_manager.register(event, hook)`
- 公开方法，供用户注册 Hook

### 4.3 `_phase_init` 修改
- 设置 `self._system_state.phase = "init"`
- 设置 `self._system_state.session_id = user_request.session_id`
- **before_guide_generation**：`guide_ctx` 构造后、`get_guides()` 之前
- **after_guide_generation**：`get_guides()` 返回后、缓存之前

### 4.4 `_phase_loop` 修改
- 设置 `self._system_state.phase = "loop"`
- **before_assemble**：`assembler.assemble()` 之前
- **after_assemble**：`assembler.assemble()` 返回后
- **before_llm_call**：`call_llm()` 之前（`messages_to_dicts` 之前）
- **after_llm_call**：`call_llm()` 返回后
- **before_tool_execute**：`json.loads()` 成功后、`tool_router.execute()` 之前
- **after_tool_execute**：ToolResult 字段提取后、构造 `ToolResult` 对象传给 Hook，Hook 修改后的值重新赋回 `success`/`content`/`error` 变量，再用于 `tool_call_records.append()`

### 4.5 `_phase_end` 修改
- 设置 `self._system_state.phase = "end"`
- **on_session_end**：phase_end 开始时、Sensor 之前
- **after_sensor**：`sensor.sense()` 之后、`ToolRouter.shutdown()` 之前

### 4.6 `run` 修改
- **on_error**：`except Exception` 块中、raise 之前

**验收**：11 个 Hook 点全部按设计文档 §5.2 的精确位置插入。

---

## 任务 5：Hook 接口 docstring 更新

**目标**：更新 `harness/interfaces/hook.py` 中的 docstring，引用常量模块。

**文件**：`harness/interfaces/hook.py`（修改）

**详细要求**：
- 将 docstring 中内联的 11 个事件名列表改为引用 `harness.hooks.events` 模块
- 保持 HookContext 和 Hook 类型定义不变

**修改后 docstring 示例**：
```python
"""Hook 函数类型。

签名: (context: HookContext) -> None

Hook 通过修改 HookContext.data 实现拦截效果。
事件常量定义见 harness.hooks.events 模块，共 11 个：

- EVENT_BEFORE_GUIDE_GENERATION → AssemblyContext
- EVENT_AFTER_GUIDE_GENERATION → GuidesBundle
- ...
"""
```

**验收**：docstring 清晰指引用户到常量模块获取事件名。

---

## 任务 6：单元测试 — HookManager

**目标**：测试 HookManager 的核心行为。

**文件**：`tests/test_hooks.py`（新建，第一部分）

**测试用例**：

| # | 测试名 | 验证内容 |
|---|--------|---------|
| 1 | `test_register_and_trigger_single_hook` | 注册一个 hook，触发后 data 被修改 |
| 2 | `test_trigger_returns_original_when_no_hooks` | 无注册 hook 时返回原始 data |
| 3 | `test_multiple_hooks_same_event` | 同一事件多个 hook 按注册顺序执行 |
| 4 | `test_hook_modification_visible_to_next_hook` | 前序 hook 的修改对后续 hook 可见 |
| 5 | `test_hook_exception_does_not_block_others` | 单个 hook 异常不阻塞其他 hook |
| 6 | `test_unregister_removes_hook` | 注销后 hook 不再被触发 |
| 7 | `test_unregister_nonexistent_no_error` | 注销不存在的 hook 不抛异常 |
| 8 | `test_register_invalid_event_raises` | 非空校验（event 为空字符串） |
| 9 | `test_register_invalid_hook_raises` | 非 callable 校验 |
| 10 | `test_system_state_passed_to_hook` | system_state 正确传递给 hook |

**验收**：10 个测试全部通过。

---

## 任务 7：集成测试 — Orchestrator Hook 触发

**目标**：验证 Orchestrator 中 11 个 Hook 点被正确触发。

**文件**：`tests/test_hooks.py`（新建，第二部分，或独立文件 `tests/test_e2e_hooks.py`）

**测试用例**：

| # | 测试名 | 验证内容 |
|---|--------|---------|
| 11 | `test_before_and_after_guide_generation` | Phase 1 中两个 hook 被触发，data 类型正确 |
| 12 | `test_before_and_after_assemble` | Phase 2 外层循环中两个 hook 被触发 |
| 13 | `test_before_and_after_llm_call` | Phase 2 内层循环中两个 hook 被触发 |
| 14 | `test_before_and_after_tool_execute` | tool_use 场景中两个 hook 被触发 |
| 15 | `test_on_session_end` | Phase 3 中 hook 被触发，接收 Trajectory |
| 16 | `test_after_sensor` | Sensor.sense() 之后 hook 被触发 |
| 17 | `test_on_error` | 异常时 on_error hook 被触发 |
| 18 | `test_hook_data_modification_affects_flow` | hook 修改 data 后，后续流程使用修改后的值 |
| 19 | `test_system_state_phase_updated` | system_state.phase 在 init/loop/end 间正确切换 |
| 20 | `test_register_hook_via_orchestrator` | `orchestrator.register_hook()` 方法工作正常 |

**验收**：10 个集成测试全部通过。

---

## 任务 8：端到端测试 — Hook 完整生命周期

**目标**：验证一个 Hook 从注册到触发到修改数据的完整流程。

**文件**：`tests/test_hooks.py`（第三部分）

**测试用例**：

| # | 测试名 | 验证内容 |
|---|--------|---------|
| 21 | `test_e2e_hook_modifies_messages_before_llm_call` | before_llm_call hook 修改 messages，LLM 收到修改后的内容 |
| 22 | `test_e2e_hook_observes_full_session` | 多个 hook 注册到不同事件，完整会话中每个都被触发一次 |
| 23 | `test_e2e_no_hooks_session_still_works` | 无 hook 注册时，完整会话正常执行（无回归） |
| 24 | `test_e2e_hook_exception_does_not_crash_session` | hook 异常时会话继续正常执行 |

**验收**：4 个端到端测试全部通过。

---

## 任务 9：现有测试回归验证

**目标**：确保 Hook 集成不破坏现有测试。

**命令**：`python -m pytest tests/ -v`

**验收**：所有已有测试（`test_orchestrator.py`、`test_sensor.py`、`test_input_adapter.py` 等）仍全部通过。

---

## 任务 10：文档检查

**目标**：确保 design.md、tasks.md、acceptance.md 与代码实现一致。

**检查项**：
- [ ] design.md 中 11 个 Hook 点的插入位置与代码实际一致
- [ ] design.md 中 data 类型与代码实际传递的类型一致
- [ ] acceptance.md 中所有验收项可被测试覆盖
- [ ] tasks.md 中所有任务已完成

---

## 完成标准

全部 10 个任务完成，且：
- `python -m pytest tests/test_hooks.py -v` 全部通过
- `python -m pytest tests/ -v` 全部通过（无回归）
- 代码符合 `sdd/05-conventions.md` 规范
