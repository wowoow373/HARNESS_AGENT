# Batch-02: interfaces — 验收标准

> 逐一对照检查。全部通过才算 batch-02 完成。
>
> **验收方式**：全部为静态检查（import 验证、类型注解检查、签名比对），**不运行 pytest**。

---

## 一、文件存在性

- [ ] A1.1 `harness/interfaces/types.py` 存在，内容非空
- [ ] A1.2 `harness/interfaces/input_adapter.py` 存在，内容非空
- [ ] A1.3 `harness/interfaces/guide_provider.py` 存在，内容非空
- [ ] A1.4 `harness/interfaces/context_assembler.py` 存在，内容非空
- [ ] A1.5 `harness/interfaces/memory_backend.py` 存在，内容非空
- [ ] A1.6 `harness/interfaces/sensor.py` 存在，内容非空
- [ ] A1.7 `harness/interfaces/tool.py` 存在，内容非空
- [ ] A1.8 `harness/interfaces/system_tool_provider.py` 存在，内容非空
- [ ] A1.9 `harness/interfaces/mcp_adapter.py` 存在，内容非空
- [ ] A1.10 `harness/interfaces/mcp_handler.py` 存在，内容非空
- [ ] A1.11 `harness/interfaces/hook.py` 存在，内容非空
- [ ] A1.12 `harness/interfaces/__init__.py` 存在，内容非空

---

## 二、types.py — 17 个大包对象

### 2.1 字段完整性

以下检查**逐字段比对**[sdd/02-interfaces.md](../../02-interfaces.md)：

- [ ] A2.1.1 `UserRequest` — 6 字段（text, attachments, context, system_state, session_id, metadata）
- [ ] A2.1.2 `SystemState` — 4 字段（phase, session_id, run_mode, metadata）
- [ ] A2.1.3 `Attachment` — 3 字段（type, content, meta）
- [ ] A2.1.4 `EnvState` — 4 字段（work_dir, git_status, timestamp, platform）
- [ ] A2.1.5 `GuidesBundle` — 5 字段（identity, capabilities, rules, constraints, examples）
- [ ] A2.1.6 `Example` — 2 字段（input, output）
- [ ] A2.1.7 `AssemblyContext` — 7 字段（user_request, guides, available_tools, history, memories, system_state, metadata）
- [ ] A2.1.8 `Trajectory` — 7 字段（session_id, history, tool_calls, final_output, execution_time, system_state, metadata）
- [ ] A2.1.9 `Message` — 4 字段（role, content, tool_call_id, tool_calls）
- [ ] A2.1.10 `Response` — 4 字段（text, thinking, tool_uses, stop_reason）
- [ ] A2.1.11 `ToolCall` — 3 字段（id, type, function）
- [ ] A2.1.12 `ToolCallFunction` — 2 字段（name, arguments）
- [ ] A2.1.13 `ToolDefinition` — 3 字段（name, description, parameters）
- [ ] A2.1.14 `ToolCallRecord` — 6 字段（tool_name, arguments, result, started_at, finished_at, error）
- [ ] A2.1.15 `ToolResult` — 3 字段（success, content, error）
- [ ] A2.1.16 `MemoryItem` — 5 字段（key, value, namespace, timestamp, metadata）

### 2.2 字段类型正确性

- [ ] A2.2.1 `UserRequest.text` 类型为 `str`
- [ ] A2.2.2 `UserRequest.attachments` 类型为 `List[Attachment]`
- [ ] A2.2.3 `UserRequest.system_state` 类型为 `SystemState`
- [ ] A2.2.4 `EnvState.git_status` 类型为 `Dict[str, Any]`
- [ ] A2.2.5 `AssemblyContext.memories` 类型为 `List[MemoryItem]`
- [ ] A2.2.6 `AssemblyContext.history` 类型为 `List[Message]`
- [ ] A2.2.7 `Trajectory.tool_calls` 类型为 `List[ToolCallRecord]`
- [ ] A2.2.8 `Message.tool_call_id` 类型为 `Optional[str]`
- [ ] A2.2.9 `ToolCall.function` 类型为 `ToolCallFunction`
- [ ] A2.2.10 `ToolDefinition.parameters` 类型为 `Dict[str, Any]`

### 2.3 默认值合理

- [ ] A2.3.1 所有 `List[X]` 字段使用 `field(default_factory=list)`
- [ ] A2.3.2 所有 `Dict[X, Y]` 字段使用 `field(default_factory=dict)`
- [ ] A2.3.3 所有 `Optional[X]` 字段默认值为 `None`

---

## 三、接口 Protocol — 方法签名正确性

### 3.1 InputAdapter

- [ ] A3.1.1 `receive() → UserRequest` — 无参数，返回 UserRequest
- [ ] A3.1.2 `send(response: Response) → None` — 参数类型为 Response，无返回值

### 3.2 GuideProvider

- [ ] A3.2.1 `GuideContext` dataclass 存在于 `guide_provider.py`
- [ ] A3.2.2 `GuideContext` 包含 `user_request: Optional[UserRequest]`, `system_state: SystemState`, `env_state: Optional[EnvState]`, `metadata: Dict[str, Any]`
- [ ] A3.2.3 `get_guides(context: GuideContext) → GuidesBundle` — 参数和返回值类型正确

### 3.3 ContextAssembler

- [ ] A3.3.1 `assemble(inputs: AssemblyContext) → List[Message]` — 参数名为 inputs，返回 List[Message]

### 3.4 MemoryBackend

- [ ] A3.4.1 `read(key: str, namespace: str) → Optional[Any]`
- [ ] A3.4.2 `write(key: str, value: Any, namespace: str) → None`
- [ ] A3.4.3 `search(query: str, namespace: str, limit: int = 10) → List[MemoryItem]`
- [ ] A3.4.4 `list_namespaces() → List[str]`

### 3.5 Sensor

- [ ] A3.5.1 `sense(trajectory: Trajectory) → None`

### 3.6 Tool

- [ ] A3.6.1 `get_definition() → ToolDefinition`
- [ ] A3.6.2 `execute(args: Dict[str, Any]) → ToolResult`

### 3.7 SystemToolProvider

- [ ] A3.7.1 `get_tools() → List[ToolDefinition]`
- [ ] A3.7.2 `execute(name: str, args: Dict[str, Any]) → ToolResult`

### 3.8 MCPAdapter

- [ ] A3.8.1 `get_tools() → List[ToolDefinition]`
- [ ] A3.8.2 `execute(name: str, args: Dict[str, Any]) → ToolResult`
- [ ] A3.8.3 `shutdown() → None`

### 3.9 MCPHandler

- [ ] A3.9.1 `transform_schema(name: str, schema: Dict) → Dict`
- [ ] A3.9.2 `transform_args(name: str, args: Dict) → Dict`
- [ ] A3.9.3 `transform_result(name: str, result: Any) → Any`

### 3.10 Hook

- [ ] A3.10.1 `HookContext` dataclass 存在于 `hook.py`
- [ ] A3.10.2 `HookContext` 包含 `event: str`, `data: Any`, `system_state: SystemState`
- [ ] A3.10.3 `Hook` 类型别名为 `Callable[[HookContext], None]`

---

## 四、向后兼容性

- [ ] A4.1 `from harness.interfaces import InputAdapter` 不报错
- [ ] A4.2 `from harness.interfaces import GuideProvider` 不报错
- [ ] A4.3 `from harness.interfaces import ContextAssembler` 不报错
- [ ] A4.4 `from harness.interfaces import MemoryBackend` 不报错
- [ ] A4.5 `from harness.interfaces import Sensor` 不报错
- [ ] A4.6 `from harness.interfaces import Tool` 不报错
- [ ] A4.7 `from harness.interfaces import SystemToolProvider` 不报错
- [ ] A4.8 `from harness.interfaces import MCPAdapter` 不报错
- [ ] A4.9 `from harness.interfaces import MCPHandler` 不报错
- [ ] A4.10 上述 10 个名称可作为 `DIContainer.register(InterfaceType, instance)` 的第一个参数（type 检查通过）
- [ ] A4.11 `harness/core/orchestrator.py` 中的 `from ..interfaces import InputAdapter, ...` 不报错（如果已有代码 import 了 hook 等新名称则需确认都能正常 import）
- [ ] A4.12 `harness/core/types.py`（_Minimal* 类型）未被修改（注：batch-02 不修改，迁移由 batch-02-1 完成）
- [ ] A4.13 `harness/core/orchestrator.py` 未被修改

---

## 五、代码质量

### 5.1 文档

- [ ] A5.1.1 `types.py` 中每个 dataclass 有 docstring
- [ ] A5.1.2 每个接口 Protocol 类有 docstring
- [ ] A5.1.3 `harness/interfaces/__init__.py` 有模块 docstring

### 5.2 类型标注

- [ ] A5.2.1 所有接口方法参数有类型标注
- [ ] A5.2.2 所有接口方法返回值有类型标注
- [ ] A5.2.3 所有 dataclass 字段有类型标注（Python dataclass 强制要求）

### 5.3 命名约定

- [ ] A5.3.1 类名 PascalCase
- [ ] A5.3.2 方法名 snake_case
- [ ] A5.3.3 模块文件名 snake_case
- [ ] A5.3.4 不符 [sdd/05-conventions.md](../../05-conventions.md) 的项目为 0

### 5.4 模块边界

- [ ] A5.4.1 `harness/interfaces/types.py` 不 import 项目其他模块（`from harness.xxx` 或 `from ..xxx`）
- [ ] A5.4.2 所有接口文件只 import `types` 和 `typing`（不含任何实现模块）

---

## 六、对照 SDD 的一致性检查

- [ ] A6.1 `types.py` 中的类型数量 = 17（与 sdd/02-interfaces.md 一致，含新增的 `ToolTransform`）
- [ ] A6.2 接口 Protocol 数量 = 9（InputAdapter, GuideProvider, ContextAssembler, MemoryBackend, Sensor, Tool, SystemToolProvider, MCPAdapter, MCPHandler），加 Hook 函数类型别名 = 10 个接口相关名称
- [ ] A6.3 `GuideContext` 和 `HookContext` 作为辅助类型存在
- [ ] A6.4 所有字段名称与 sdd/02-interfaces.md 完全一致（大小写、下划线、拼写）
- [ ] A6.5 文件列表与 sdd/03-project-structure.md 一致
