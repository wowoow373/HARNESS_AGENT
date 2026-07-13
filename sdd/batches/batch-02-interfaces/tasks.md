# Batch-02: interfaces — 任务列表

> **重要提醒**：本批次只创建 `harness/interfaces/` 下的文件，**不修改** `harness/core/`、`harness/adapters/`、`harness/messaging/`、`tests/` 中的任何文件。
>
> 执行方式：按顺序逐条完成，完成后在 `[ ]` 中标记 `[x]`。

---

## 阶段 1：types.py — 17 个大包对象

**文件**：`harness/interfaces/types.py`

### 1.1 基础类型（无前向引用依赖）

- [ ] 1.1.1 创建 `SystemState` dataclass
  - 字段：`phase: str`, `session_id: str`, `run_mode: str`, `metadata: Dict[str, Any]`
  - 默认值：`phase = "init"`, `session_id = ""`, `run_mode = "normal"`, `metadata = field(default_factory=dict)`

- [ ] 1.1.2 创建 `Attachment` dataclass
  - 字段：`type: str`, `content: Any`, `meta: Dict[str, Any]`
  - 默认值：`type = "file"`, `content = None`, `meta = field(default_factory=dict)`

- [ ] 1.1.3 创建 `EnvState` dataclass
  - 字段：`work_dir: str`, `git_status: Dict[str, Any]`, `timestamp: float`, `platform: str`
  - 默认值：`work_dir = ""`, `git_status = field(default_factory=dict)`, `timestamp = 0.0`, `platform = ""`

- [ ] 1.1.4 创建 `Example` dataclass
  - 字段：`input: str`, `output: str`
  - 默认值：`input = ""`, `output = ""`

- [ ] 1.1.5 创建 `ToolCallFunction` dataclass
  - 字段：`name: str`, `arguments: str`
  - 默认值：`name = ""`, `arguments = "{}"`

- [ ] 1.1.6 创建 `ToolDefinition` dataclass
  - 字段：`name: str`, `description: str`, `parameters: Dict[str, Any]`
  - 默认值：`name = ""`, `description = ""`, `parameters = field(default_factory=dict)`

- [ ] 1.1.7 创建 `ToolResult` dataclass
  - 字段：`success: bool`, `content: Any`, `error: Optional[str]`
  - 默认值：`success = True`, `content = None`, `error = None`

- [ ] 1.1.8 创建 `MemoryItem` dataclass
  - 字段：`key: str`, `value: Any`, `namespace: str`, `timestamp: float`, `metadata: Dict[str, Any]`
  - 默认值：`key = ""`, `value = None`, `namespace = ""`, `timestamp = 0.0`, `metadata = field(default_factory=dict)`

- [ ] 1.1.9 创建 `Message` dataclass
  - 字段：`role: str`, `content: str`, `tool_call_id: Optional[str]`
  - 默认值：`role = "user"`, `content = ""`, `tool_call_id = None`

### 1.2 组合类型（依赖上面已创建的类型）

- [ ] 1.2.1 创建 `UserRequest` dataclass
  - 字段：`text: str`, `attachments: List[Attachment]`, `context: Dict[str, Any]`, `system_state: SystemState`, `session_id: str`, `metadata: Dict[str, Any]`
  - 默认值：`text = ""`, `attachments = field(default_factory=list)`, `context = field(default_factory=dict)`, `system_state = field(default_factory=SystemState)`, `session_id = ""`, `metadata = field(default_factory=dict)`

- [ ] 1.2.2 创建 `ToolCall` dataclass
  - 字段：`id: str`, `type: str`, `function: ToolCallFunction`
  - 默认值：`id = ""`, `type = "function"`, `function = field(default_factory=ToolCallFunction)`

- [ ] 1.2.3 创建 `ToolCallRecord` dataclass
  - 字段：`tool_name: str`, `arguments: Dict[str, Any]`, `result: Any`, `started_at: float`, `finished_at: float`, `error: Optional[str]`
  - 默认值：`tool_name = ""`, `arguments = field(default_factory=dict)`, `result = None`, `started_at = 0.0`, `finished_at = 0.0`, `error = None`

- [ ] 1.2.4 创建 `GuidesBundle` dataclass
  - 字段：`identity: str`, `capabilities: List[str]`, `rules: List[str]`, `constraints: List[str]`, `examples: List[Example]`
  - 默认值：`identity = ""`, `capabilities = field(default_factory=list)`, `rules = field(default_factory=list)`, `constraints = field(default_factory=list)`, `examples = field(default_factory=list)`

- [ ] 1.2.5 创建 `Response` dataclass
  - 字段：`text: Optional[str]`, `thinking: Optional[str]`, `tool_uses: List[ToolCall]`, `stop_reason: str`
  - 默认值：`text = None`, `thinking = None`, `tool_uses = field(default_factory=list)`, `stop_reason = "end_turn"`

- [ ] 1.2.6 创建 `AssemblyContext` dataclass
  - 字段：`user_request: Optional[UserRequest]`, `guides: Optional[GuidesBundle]`, `available_tools: List[ToolDefinition]`, `history: List[Message]`, `memories: List[MemoryItem]`, `system_state: SystemState`, `metadata: Dict[str, Any]`
  - 默认值：`user_request = None`, `guides = None`, `available_tools = field(default_factory=list)`, `history = field(default_factory=list)`, `memories = field(default_factory=list)`, `system_state = field(default_factory=SystemState)`, `metadata = field(default_factory=dict)`

- [ ] 1.2.7 创建 `Trajectory` dataclass
  - 字段：`session_id: str`, `history: List[Message]`, `tool_calls: List[ToolCallRecord]`, `final_output: str`, `execution_time: float`, `system_state: SystemState`, `metadata: Dict[str, Any]`
  - 默认值：`session_id = ""`, `history = field(default_factory=list)`, `tool_calls = field(default_factory=list)`, `final_output = ""`, `execution_time = 0.0`, `system_state = field(default_factory=SystemState)`, `metadata = field(default_factory=dict)`

### 1.3 文件收尾

- [ ] 1.3.1 在文件顶部添加 `from __future__ import annotations`
- [ ] 1.3.2 添加 `__all__` 导出列表，包含全部 17 个类型
- [ ] 1.3.3 每个 dataclass 添加 docstring
- [ ] 1.3.4 确认 `types.py` 不 import 项目的任何其他模块（它是纯数据类型层）

---

## 阶段 2：10 个接口文件（9 Protocol + 1 Hook 类型别名）

### 2.1 Tool（优先，被 system_tool_provider 和 mcp_adapter 依赖）

**文件**：`harness/interfaces/tool.py`

- [ ] 2.1.1 定义 `Tool` Protocol，`@runtime_checkable`
  - `get_definition(self) -> ToolDefinition`
  - `execute(self, args: Dict[str, Any]) -> ToolResult`
- [ ] 2.1.2 添加模块 docstring

### 2.2 其余接口文件（可并行创建）

**文件**：`harness/interfaces/input_adapter.py`

- [ ] 2.2.1 定义 `InputAdapter` Protocol，`@runtime_checkable`
  - `receive(self) -> UserRequest`
  - `send(self, response: Response) -> None`

**文件**：`harness/interfaces/guide_provider.py`

- [ ] 2.2.2 定义 `GuideContext` dataclass
  - 字段：`user_request: Optional[UserRequest]`, `system_state: SystemState`, `env_state: Optional[EnvState]`, `metadata: Dict[str, Any]`
  - 默认值：`user_request = None`, `system_state = field(default_factory=SystemState)`, `env_state = None`, `metadata = field(default_factory=dict)`
- [ ] 2.2.3 定义 `GuideProvider` Protocol，`@runtime_checkable`
  - `get_guides(self, context: GuideContext) -> GuidesBundle`

**文件**：`harness/interfaces/context_assembler.py`

- [ ] 2.2.4 定义 `ContextAssembler` Protocol，`@runtime_checkable`
  - `assemble(self, inputs: AssemblyContext) -> List[Message]`

**文件**：`harness/interfaces/memory_backend.py`

- [ ] 2.2.5 定义 `MemoryBackend` Protocol，`@runtime_checkable`
  - `read(self, key: str, namespace: str) -> Optional[Any]`
  - `write(self, key: str, value: Any, namespace: str) -> None`
  - `search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]`
  - `list_namespaces(self) -> List[str]`

**文件**：`harness/interfaces/sensor.py`

- [ ] 2.2.6 定义 `Sensor` Protocol，`@runtime_checkable`
  - `sense(self, trajectory: Trajectory) -> None`

**文件**：`harness/interfaces/system_tool_provider.py`

- [ ] 2.2.7 定义 `SystemToolProvider` Protocol，`@runtime_checkable`
  - `get_tools(self) -> List[ToolDefinition]`
  - `execute(self, name: str, args: Dict[str, Any]) -> ToolResult`

**文件**：`harness/interfaces/mcp_adapter.py`

- [ ] 2.2.8 定义 `MCPAdapter` Protocol，`@runtime_checkable`
  - `get_tools(self) -> List[ToolDefinition]`
  - `execute(self, name: str, args: Dict[str, Any]) -> ToolResult`
  - `shutdown(self) -> None`

**文件**：`harness/interfaces/mcp_handler.py`

- [ ] 2.2.9 定义 `MCPHandler` Protocol，`@runtime_checkable`
  - `transform_schema(self, name: str, schema: Dict) -> Dict`
  - `transform_args(self, name: str, args: Dict) -> Dict`
  - `transform_result(self, name: str, result: Any) -> Any`

**文件**：`harness/interfaces/hook.py`

- [ ] 2.2.10 定义 `HookContext` dataclass
  - 字段：`event: str`, `data: Any`, `system_state: SystemState`
  - 默认值：`event = ""`, `data = None`, `system_state = field(default_factory=SystemState)`
- [ ] 2.2.10 定义 `Hook` 类型别名
  - `Hook = Callable[[HookContext], None]`

---

## 阶段 3：更新 `__init__.py`

**文件**：`harness/interfaces/__init__.py`

> **注意**：本阶段仅给出了 re-export 的概要说明。如需完整的导入清单（30+ 条精确的 `from .xxx import YYY` 语句），请参考 [sdd/02-interfaces.md](../../02-interfaces.md) §通用大包对象 和 §组件接口，那里列出了每个类型的完整字段定义和每个接口的完整方法签名。也可对照 [design.md](design.md) §三 的 17 类型字段表和 §四 的 10 接口文件代码片段逐一确认。

- [ ] 3.1 删除 8 个空占位类定义
- [ ] 3.2 从各子模块 re-export 正式类型：
  - 从 `types.py`：全部 17 个大包对象
  - 从各接口文件：全部 9 个 Protocol 类 + `GuideContext` + `HookContext` + `Hook`
- [ ] 3.3 更新模块 docstring
- [ ] 3.4 定义 `__all__` 导出列表：
  - 17 个类型 + 9 个 Protocol + 2 个辅助类型 (GuideContext, HookContext) + 1 个类型别名 (Hook)

---

## 阶段 4：验证（不运行测试，纯静态检查）

- [ ] 4.1 验证 `python -c "from harness.interfaces import InputAdapter, GuideProvider, ContextAssembler, MemoryBackend, Sensor, Tool, SystemToolProvider, MCPAdapter, MCPHandler"` 不报错
- [ ] 4.2 验证 `python -c "from harness.interfaces.types import UserRequest, SystemState, Attachment, EnvState, GuidesBundle, Example, AssemblyContext, Trajectory, Message, Response, ToolCall, ToolCallFunction, ToolDefinition, ToolCallRecord, ToolResult, MemoryItem"` 不报错
- [ ] 4.3 验证 `orchestrator.py` 中 `from ..interfaces import InputAdapter, ...` 仍然有效
- [ ] 4.4 验证 `di.py` 中 `from .core.orchestrator import InputAdapter` 仍然有效
- [ ] 4.5 运行 `python -c "from harness.core.container import DIContainer; from harness.interfaces import InputAdapter; c = DIContainer(); c.register(InputAdapter, object())"` 不报错（Protocol 可作 DI key）
- [ ] 4.6 运行 `python -c "from harness.interfaces import InputAdapter; from typing import runtime_checkable; print(runtime_checkable)"` 确认 Python 3.11+ Protocol 支持正常

---

## 不需要做的事（明确排除）

- ❌ 不创建或修改测试文件（测试由 batch-02-1 完成）
- ❌ 不修改 `harness/core/orchestrator.py`（迁移由 batch-02-1 完成）
- ❌ 不修改 `harness/core/types.py`（迁移由 batch-02-1 完成）
- ❌ 不修改 `harness/core/container.py`
- ❌ 不修改 `harness/di.py`
- ❌ 不修改 `harness/adapters/llm_adapter.py`（迁移由 batch-02-1 完成）
- ❌ 不修改 `harness/messaging/builder.py`（迁移由 batch-02-1 完成）
- ❌ 不迁移 `_Minimal*` 类型（迁移由 batch-02-1 完成）
- ❌ 不运行 pytest（仅阶段 4 的 import 检查）
