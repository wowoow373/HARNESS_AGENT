# Batch-02-1: 接口测试 + 类型迁移 — 任务列表

> **执行方式**：按阶段顺序逐条完成，完成后在 `[ ]` 中标记 `[x]`。
>
> **每阶段结束时运行 pytest** 确认无回归。
>
> **重要**：本批次只修改源码和测试文件，不新建接口文件（接口已由 batch-02 完成）。

---

## 阶段 1：`messaging/builder.py` — 转换层建设

**文件**：`harness/messaging/builder.py`

### 1.1 新增 `Message ↔ dict` 双向转换

- [ ] 1.1.1 实现 `message_to_dict(msg: Message) → Dict[str, Any]`
  - 映射 `role`、`content` 字段
  - `tool_call_id` 非 None 时才写入（tool role 消息需要此字段）
- [ ] 1.1.2 实现 `messages_to_dicts(messages: List[Message]) → List[Dict[str, Any]]`
  - 批量转换便捷函数
- [ ] 1.1.3 实现 `dict_to_message(d: Dict[str, Any]) → Message`
  - 从 dict 提取 `role`、`content`、`tool_call_id`
  - `content` 为 None 或缺失时默认 `""`
  - 忽略 `tool_calls` 等 `Message` 不包含的字段

### 1.2 新增 `ToolDefinition → OpenAI tool format`

- [ ] 1.2.1 实现 `tool_definition_to_openai(td: ToolDefinition) → Dict[str, Any]`
  - 输出格式：`{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}`
- [ ] 1.2.2 实现 `tool_definitions_to_openai(tools: List[ToolDefinition]) → List[Dict[str, Any]]`
  - 批量转换便捷函数

### 1.3 现有函数签名升级

- [ ] 1.3.1 `build_assistant_message` 参数类型 `_MinimalResponse` → `Response`
- [ ] 1.3.2 `build_tool_result_message` 参数类型 `_MinimalToolCall` → `ToolCall`
- [ ] 1.3.3 更新 import：删除 `_Minimal*` 引用，新增 `Message`、`ToolDefinition` import

### 1.4 更新 `__init__.py`

**文件**：`harness/messaging/__init__.py`

- [ ] 1.4.1 导出新增函数：`message_to_dict`、`messages_to_dicts`、`dict_to_message`、`tool_definition_to_openai`、`tool_definitions_to_openai`

### 阶段 1 验证

- [ ] V1.1 `python -c "from harness.messaging import message_to_dict, dict_to_message, tool_definitions_to_openai"` 无报错
- [ ] V1.2 `python -c "from harness.interfaces.types import Message; from harness.messaging import message_to_dict; m = Message(role='user', content='hi'); print(message_to_dict(m))"` 输出 `{'role': 'user', 'content': 'hi'}`
- [ ] V1.3 运行 `python -m pytest tests/ -x -q` — 确认阶段 1 未引入回归（现有 builder 调用方仍可用）
- [ ] V1.4 `grep -r 'from harness.core\|from harness.adapters' harness/messaging/builder.py` 结果为空 — builder 不 import 实现模块

---

## 阶段 2：`adapters/llm_adapter.py` — 类型迁移

**文件**：`harness/adapters/llm_adapter.py`

- [ ] 2.1 替换 import：`from ..core.orchestrator import _MinimalResponse, _MinimalToolCall, _MinimalToolCallFunction` → `from ..interfaces.types import Response, ToolCall, ToolCallFunction`
- [ ] 2.2 `__call__` 返回类型标注 `_MinimalResponse` → `Response`
- [ ] 2.3 `_parse_response` 中：
  - `tool_uses: List[_MinimalToolCall]` → `List[ToolCall]`
  - `_MinimalToolCall(id=..., type=..., function=_MinimalToolCallFunction(...))` → `ToolCall(id=..., type=..., function=ToolCallFunction(...))`
  - `_MinimalResponse(text=..., thinking=..., tool_uses=..., stop_reason=...)` → `Response(...)`
- [ ] 2.4 清理 unused import（`from ..core.orchestrator import ...` 不再需要）

### 阶段 2 验证

- [ ] V2.1 `python -c "from harness.adapters.llm_adapter import MinimalLLMAdapter; print('OK')"` 无报错
- [ ] V2.2 `python -c "from harness.adapters.llm_adapter import MinimalLLMAdapter; from harness.interfaces.types import Response; import inspect; hints = inspect.get_annotations(MinimalLLMAdapter.__call__); assert hints.get('return') is Response"` — 返回类型标注已升级
- [ ] V2.3 ⚠️ `tests/test_llm_adapter.py` 暂不运行 — 其中 2 处 `isinstance(result, _MinimalResponse)` 断言需等到阶段 5 迁移后才会通过

---

## 阶段 3：`core/orchestrator.py` — 类型迁移 + 桥接删除

**文件**：`harness/core/orchestrator.py`

### 3.1 Import 替换

- [ ] 3.1.1 删除 `from .types import _Minimal*` 全部 7 个 import
- [ ] 3.1.2 新增 `from ..interfaces.types import (AssemblyContext, GuidesBundle, Message, Response, ToolCall, ToolCallRecord, ToolDefinition, Trajectory, UserRequest)`
- [ ] 3.1.3 新增 `from ..messaging import messages_to_dicts, tool_definitions_to_openai`
- [ ] 3.1.4 新增 `import json`（已有，确认存在 — 用于替代 `parse_arguments()`）

### 3.2 内部状态类型升级

- [ ] 3.2.1 `self._history: List[Dict[str, Any]]` → `List[Message]`
- [ ] 3.2.2 `self._tool_call_records: List[Dict[str, Any]]` → `List[ToolCallRecord]`
- [ ] 3.2.3 `self._cached_guides: Optional[_MinimalGuidesBundle]` → `Optional[GuidesBundle]`
- [ ] 3.2.4 `self._cached_tools: List[Dict[str, Any]]` → `List[ToolDefinition]`
- [ ] 3.2.5 `self._cached_tool_registry: Optional[Any]` — 保持不变（类型标注已足够模糊）

### 3.3 方法签名升级

- [ ] 3.3.1 `_phase_init() -> _MinimalAssemblyContext` → `-> AssemblyContext`
- [ ] 3.3.2 `_phase_loop(initial_ctx: _MinimalAssemblyContext)` → `initial_ctx: AssemblyContext`
- [ ] 3.3.3 `_phase_end(trajectory: _MinimalTrajectory)` → `trajectory: Trajectory`
- [ ] 3.3.4 `_should_exit(user_request: _MinimalUserRequest)` → `user_request: UserRequest`
- [ ] 3.3.5 `_fallback_assemble(ctx: _MinimalAssemblyContext) -> List[Dict]` → `-> List[Message]`
- [ ] 3.3.6 `_build_trajectory() -> _MinimalTrajectory` → `-> Trajectory`

### 3.4 `_normalize_*` 删除 + 调用点适配

- [ ] 3.4.1 删除 `_normalize_user_request()` 方法（L501-L514）
- [ ] 3.4.2 删除 `_normalize_guides()` 方法（L516-L532）
- [ ] 3.4.3 删除 `_normalize_response()` 方法（L534-L569）
- [ ] 3.4.4 `_phase_init` 中 `user_request = self._normalize_user_request(raw_request)` → `user_request = raw_request`
- [ ] 3.4.5 `_phase_init` 中 `guides = self._normalize_guides(raw_guides)` → `guides = raw_guides`
- [ ] 3.4.6 `_phase_loop` 中 `response = self._normalize_response(response)` → 直接使用 `response`
- [ ] 3.4.7 `_phase_loop` 中 `new_request = self._normalize_user_request(raw_request)` → `new_request = raw_request`

### 3.5 `parse_arguments()` → 内联 `json.loads`

- [ ] 3.5.1 所有 `tc.parse_arguments()` 调用替换为 `json.loads(tc.function.arguments)`
  - 位置：`_phase_loop` 内层循环 L290

### 3.6 `_should_exit` 逻辑适配

- [ ] 3.6.1 `if user_request.text is None:` → `if not user_request.text:`
  - 注意：同时保留下一行的 `if user_request.text.strip() == ""` 以捕获仅空白字符输入

### 3.7 `_fallback_assemble` 返回类型升级

- [ ] 3.7.1 函数体内构造返回值的所有 `Dict` → `Message` 对象
  - `{"role": "system", "content": ctx.guides.identity}` → `Message(role="system", content=ctx.guides.identity)`
  - `{"role": "user", "content": ctx.user_request.text}` → `Message(role="user", content=ctx.user_request.text)`
- [ ] 3.7.2 调用点 `messages = self._fallback_assemble(ctx)` 的后续使用：
  - `messages` 列表传给 `self.call_llm(messages, ...)` 前需包装：`self.call_llm(messages_to_dicts(messages), tools_openai)`

### 3.8 其他 dict 访问 → 属性访问

- [ ] 3.8.1 `_build_trajectory` 中 `last.get("content", "")` → `last.content if last else ""`
- [ ] 3.8.2 `_phase_loop` 中 `self._history.append({"role": "assistant", "content": response.text})` → `self._history.append(Message(role="assistant", content=response.text or ""))`
- [ ] 3.8.3 `_phase_loop` 中 tool_call_records 记录从 dict 构造 → `ToolCallRecord(...)` 构造
- [ ] 3.8.4 `_phase_loop` 中 `messages.append({"role": "assistant", "content": response.text})` → `messages.append(Message(role="assistant", content=response.text or ""))`

### 3.9 LLM 调用点的 tool 格式转换

- [ ] 3.9.1 `self.call_llm(messages, self._cached_tools)` → `self.call_llm(messages_to_dicts(messages), tool_definitions_to_openai(self._cached_tools))`

### 阶段 3 验证

- [ ] V3.1 `python -c "from harness.core.orchestrator import LifecycleOrchestrator"` 无报错
- [ ] V3.2 `grep -r '_Minimal' harness/core/orchestrator.py` 结果为空
- [ ] V3.3 `grep -r '_normalize_' harness/core/orchestrator.py` 结果为空
- [ ] V3.4 `grep -r 'parse_arguments' harness/core/orchestrator.py harness/adapters/llm_adapter.py harness/messaging/` 结果为空
- [ ] V3.5 `python -c "from harness.core.container import DIContainer; from harness.core.orchestrator import LifecycleOrchestrator; c = DIContainer(); o = LifecycleOrchestrator(c); print('OK')"` — 编排器可正常实例化
- [ ] V3.6 ⚠️ `tests/test_orchestrator.py`、`tests/test_black_box.py`、`tests/test_real_llm_trace.py` 暂不运行 — 这些测试仍 import `_Minimal*`，需等到阶段 5 迁移后才会通过

---

## 阶段 4：`core/types.py` — 标记废弃

**文件**：`harness/core/types.py`

- [ ] 4.1 在文件顶部 docstring 后添加 `import warnings` 和 deprecation warning
  - `warnings.warn("harness.core.types is deprecated. Use harness.interfaces.types instead.", DeprecationWarning, stacklevel=2)`
- [ ] 4.2 更新模块 docstring，明确标注 "DEPRECATED — use harness.interfaces.types"
- [ ] 4.3 确认 `grep -r 'from .types import\|from ..core.types import' harness/` 在源码中无残留（测试文件除外）

### 阶段 4 验证

- [ ] V4.1 `python -c "import warnings; warnings.simplefilter('always'); from harness.core.types import _MinimalUserRequest"` 输出 DeprecationWarning
- [ ] V4.2 `grep -r '_Minimal' harness/core/ harness/adapters/ harness/messaging/` 在源码中无残留引用

---

## 阶段 5：测试迁移

### 5.1 `tests/test_orchestrator.py`

- [ ] 5.1.1 替换所有 `_Minimal*` import 为正式类型 import（~79 处引用）
- [ ] 5.1.2 更新 `_Minimal*` 构造调用为正式类型构造
- [ ] 5.1.3 `_history` 相关：`List[Dict]` → `List[Message]`，断言适配
- [ ] 5.1.4 运行 `python -m pytest tests/test_orchestrator.py -x -q` — 全部通过

### 5.2 `tests/test_black_box.py`

- [ ] 5.2.1 替换所有 `_Minimal*` 引用（~76 处）
- [ ] 5.2.2 运行 `python -m pytest tests/test_black_box.py -x -q` — 全部通过

### 5.3 `tests/test_real_llm_trace.py`

- [ ] 5.3.1 替换所有 `_Minimal*` 引用（~14 处，涉及 6 种类型）
- [ ] 5.3.2 运行 `python -m pytest tests/test_real_llm_trace.py -x -q` — 全部通过（如有真实 LLM 密钥）

### 5.4 `tests/test_llm_adapter.py`

- [ ] 5.4.1 替换 `_MinimalResponse` 引用（~2 处使用 + 2 处 dead import 删除）
- [ ] 5.4.2 删除 `_MinimalToolCall` 和 `_MinimalToolCallFunction` 的 dead import（第 16-17 行）
- [ ] 5.4.3 运行 `python -m pytest tests/test_llm_adapter.py -x -q` — 全部通过

### 5.5 不受影响的测试确认

- [ ] 5.5.1 `tests/test_config.py` — 确认无需修改
- [ ] 5.5.2 `tests/test_container.py` — 确认无需修改
- [ ] 5.5.3 `tests/test_exceptions.py` — 确认无需修改

### 阶段 5 验证

- [ ] V5.1 `grep -r '_Minimal' tests/` 结果为空
- [ ] V5.2 运行 `python -m pytest tests/ -x -q` — 全部通过

---

## 阶段 6：测试 gap 补齐

### 6.1 AC-ORCH-14: 内层循环不调用 ContextAssembler

**文件**：`tests/test_orchestrator.py`

- [ ] 6.1.1 新增测试：Mock ContextAssembler，发起多轮 tool_use 交互，spy 计数 `assemble()` 调用次数
- [ ] 6.1.2 断言：tool_use loop（内层循环）中 `assemble()` 调用次数为 0
- [ ] 6.1.3 断言：外层循环中 `assemble()` 每轮仅调用一次

### 6.2 AC-EDGE-03: 空文件

**文件**：`tests/test_config.py`

- [ ] 6.2.1 新增测试：创建 0 字节 TOML 文件，调用 `ConfigLoader.load()`
- [ ] 6.2.2 断言抛出 `ConfigParseError`

### 6.3 AC-EDGE-04: 纯注释 TOML

**文件**：`tests/test_config.py`

- [ ] 6.3.1 新增测试：创建仅含 `# comment` 无 `[meta]` 节的 TOML 文件
- [ ] 6.3.2 `load()` 后调用 `validate()` → 断言抛出 `ConfigValidationError`

### 6.4 AC-EDGE-05: 大文件 (10MB+)

**文件**：`tests/test_config.py`

- [ ] 6.4.1 新增测试：生成 10MB+ 的合法 TOML 文件（含大量注释或冗余字段）
- [ ] 6.4.2 `ConfigLoader.load()` 不崩溃、不超时（设置合理 timeout）
- [ ] 6.4.3 返回的 `ProfileConfig` 字段完整

### 6.5 转换层单元测试

**文件**：`tests/test_messaging.py`（新建）

- [ ] 6.5.1 测试 `message_to_dict` 三种 role（system/user/assistant）输出正确
- [ ] 6.5.2 测试 `message_to_dict` — `tool_call_id` 非 None 时输出含该字段
- [ ] 6.5.3 测试 `message_to_dict` — `tool_call_id` 为 None 时输出不含该字段
- [ ] 6.5.4 测试 `dict_to_message` — 含 `tool_call_id` 的 tool dict 正确解析
- [ ] 6.5.5 测试 `dict_to_message` — `content` 为 None 时默认空字符串
- [ ] 6.5.6 测试 `message_to_dict` ↔ `dict_to_message` 往返一致性（多种角色）
- [ ] 6.5.7 测试 `tool_definition_to_openai` — 输出格式与 OpenAI spec 一致
- [ ] 6.5.8 测试 `tool_definitions_to_openai` — 空列表返回 `[]`
- [ ] 6.5.9 测试 `build_assistant_message` — `Response`（正式类型）输入正确
- [ ] 6.5.10 测试 `build_tool_result_message` — `ToolCall`（正式类型）输入正确

### 阶段 6 验证

- [ ] V6.1 运行 `python -m pytest tests/test_config.py -x -q -k "empty or comment or large"` — 新增 3 个测试通过
- [ ] V6.2 运行 `python -m pytest tests/test_orchestrator.py -x -q -k "assemble"` — AC-ORCH-14 通过
- [ ] V6.3 运行 `python -m pytest tests/test_messaging.py -x -q` — 10 个新增测试通过
- [ ] V6.4 运行 `python -m pytest tests/ -x -q` — 全部测试通过

---

## 阶段 7：文档同步

### 7.1 `CORE_DEVELOPER_GUIDE.md`

- [ ] 7.1.1 替换 §4 代码示例中的 `_Minimal*` → 正式类型
- [ ] 7.1.2 替换 §5 代码示例中的 `_Minimal*` → 正式类型
- [ ] 7.1.3 替换 §7.2 代码示例中的 `_Minimal*` → 正式类型
- [ ] 7.1.4 替换 §11 代码示例中的 `_Minimal*` → 正式类型
- [ ] 7.1.5 删除/更新"已标注过时警告"的提示

### 7.2 `ARCHITECTURE.md`

- [ ] 7.2.1 检查是否有 `_Minimal*` 引用，如有则更新为正式类型
- [ ] 7.2.2 更新类型相关描述为正式类型名称

### 阶段 7 验证

- [ ] V7.1 `grep -r '_Minimal' CORE_DEVELOPER_GUIDE.md` 结果为空
- [ ] V7.2 `grep -r '_Minimal' ARCHITECTURE.md` 结果为空（如之前无引用则跳过）

---

## 阶段 8：最终验证

- [ ] 8.1 `grep -r '_Minimal' harness/` 仅在 `harness/core/types.py`（已废弃）中有定义，源码中无引用
- [ ] 8.2 `grep -r '_normalize_' harness/` 结果为空
- [ ] 8.3 `grep -r 'parse_arguments' harness/` 结果为空
- [ ] 8.4 `python -c "from harness.interfaces import InputAdapter; from harness.core.container import DIContainer; c = DIContainer(); c.register(InputAdapter, object())"` — DI 注册正常
- [ ] 8.5 `python -c "from harness.di import Harness"` — 装配入口正常
- [ ] 8.6 运行 `python -m pytest tests/ -v` — 全部通过，0 失败
- [ ] 8.7 运行 `python -m pytest tests/ --cov=harness --cov-report=term-missing` — 覆盖率达到或超过 batch-01 基线
- [ ] 8.8 验证 re-export 包装路径一致性：`python -c "from harness.core.llm_adapter import MinimalLLMAdapter; from harness.adapters.llm_adapter import MinimalLLMAdapter as M2; assert MinimalLLMAdapter is M2"` — re-export 路径可用
- [ ] 8.9 `python -c "from harness.interfaces.types import __all__; assert len(__all__) == 16, f'Expected 16 types, got {len(__all__)}'"` — 正式类型数量 = 16

---

## 不需要做的事（明确排除）

- ❌ 不创建新的接口文件（batch-02 已完成）
- ❌ 不修改 `harness/core/container.py`
- ❌ 不修改 `harness/core/exceptions.py`
- ❌ 不修改 `harness/config/loader.py` 的逻辑（仅添加测试）
- ❌ 不修改任何 `harness/interfaces/` 下的文件
- ❌ 不实现任何组件（batch-03 ~ 09 的职责）
- ❌ 不修改 `harness/di.py`
- ❌ 不修改编排器三阶段控制流的逻辑结构
