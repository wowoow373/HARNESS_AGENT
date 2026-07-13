# batch-09: Hooks — 验收标准

> 实现完成后逐条对照确认。全部通过即 batch-09 完成。
>
> **决策: ACCEPT ✅** — 所有验收项通过，541 个测试全部通过，零回归。

---

## 一、功能验收

### HookManager

- [x] `HookManager` 可正常实例化
- [x] `register(event, hook)` 能将 Hook 注册到指定事件
- [x] `unregister(event, hook)` 能将 Hook 从指定事件移除
- [x] `trigger(event, data, system_state)` 执行所有该事件的 Hook
- [x] 同一事件注册多个 Hook 时，按注册顺序依次执行
- [x] 单个 Hook 抛异常时：记录 WARNING 日志，不阻塞同事件的其他 Hook
- [x] 无 Hook 注册时 `trigger()` 直接返回原始 data
- [x] `register()` 对非空 event 和非 callable hook 有校验

### Orchestrator 集成

- [x] `LifecycleOrchestrator` 在 `__init__` 中创建 `HookManager` 实例
- [x] `LifecycleOrchestrator.register_hook(event, hook)` 方法可用
- [x] Phase 1 中 `before_guide_generation` 和 `after_guide_generation` 被触发
- [x] Phase 2 外层循环中 `before_assemble` 和 `after_assemble` 被触发
- [x] Phase 2 内层循环中 `before_llm_call` 和 `after_llm_call` 被触发
- [x] Phase 2 内层循环中每个 tool 执行前后 `before_tool_execute` 和 `after_tool_execute` 被触发
- [x] Phase 3 中 `on_session_end` 在 Sensor 之前被触发
- [x] Phase 3 中 `after_sensor` 在 Sensor.sense() 之后被触发（语义上为只读观察点）
- [x] `run()` 的 except 块中 `on_error` 被触发
- [x] Hook 修改 `context.data` 后，后续流程使用修改后的值
- [x] `system_state.phase` 在 init → loop → end 间正确切换

### 事件常量

- [x] `harness/hooks/events.py` 中定义 11 个常量
- [x] 常量命名格式为 `EVENT_<大写下划线>`
- [x] 常量值与 architecture.md 中定义的事件名完全一致
- [x] `from harness.hooks import EVENT_*` 可正常导入

---

## 二、集成验收

- [x] 无 Hook 注册时，完整会话生命周期正常执行（无回归）
- [x] 多个 Hook 注册到不同事件时，完整会话中每个事件都被触发
- [x] `before_llm_call` Hook 修改 messages 后，mock LLM 收到修改后的内容
- [x] `after_llm_call` Hook 修改 Response 后，后续流程（send/tool_execute）使用修改后的 Response
- [x] `on_error` Hook 在异常时被触发，异常对象正确传递
- [x] 已有所有 batch（01~08）的测试全部通过，无回归

---

## 三、代码质量

- [x] 所有公开方法有完整类型标注
- [x] 所有公开类有 docstring
- [x] 符合 `05-conventions.md` 命名和结构规范
- [x] `harness/hooks/` 不 import 任何 `harness/components/` 中的具体实现
- [x] `harness/core/orchestrator.py` 对 Hook 的 import 仅限于 `harness/hooks/`（接口层）
- [x] 新增测试文件 `tests/test_hooks.py` 全部通过（38 tests）
- [x] 所有已有测试文件仍全部通过（541 total, 0 failures）
