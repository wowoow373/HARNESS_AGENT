# Batch-02: interfaces — 设计文档

> **目标**：将 `harness/interfaces/` 从空占位类升级为正式的 Protocol/ABC 定义 + 完整的大包对象数据类。
>
> **唯一权威来源**：[sdd/02-interfaces.md](../../02-interfaces.md)。所有字段名、类型、方法签名必须与此文件严格一致。

---

## 一、范围与边界

### 在范围内

1. 创建 `harness/interfaces/types.py` — 全部 17 个大包对象数据类
2. 创建 10 个组件接口文件 — 9 个 Protocol 定义 + 1 个 Hook 函数类型别名
3. 更新 `harness/interfaces/__init__.py` — 从空占位类替换为正式定义的 re-export

### 严格不在范围内

- ❌ 不修改 `harness/core/`、`harness/adapters/`、`harness/messaging/` 中的任何文件
- ❌ 不修改 `harness/core/types.py`（`_Minimal*` 类型的迁移由 batch-02-1 完成）
- ❌ 不写测试文件（测试由 batch-02-1 完成）
- ❌ 不写任何实现代码（接口是纯抽象定义）

---

## 二、核心设计决策

### 2.1 Protocol vs ABC

**选择 `typing.Protocol`（结构性子类型）**。

理由：
- 与当前 duck typing 模式一致 — 用户不需要显式继承，有对应方法就能通过 `isinstance` 检查
- 不强制继承关系，保持"预构造实例注册"的灵活性
- `Protocol` 配合 `@runtime_checkable` 装饰器，可在 DI 容器注册时做运行时校验

**Protocol 所需导入**：

```python
from typing import Protocol, runtime_checkable
```

> `runtime_checkable` 来自 `typing` 模块（Python 3.8+），不是独立包。所有接口文件使用 `Protocol` 和 `@runtime_checkable` 时都需要此导入。

例外：大包对象（`types.py`）使用 `@dataclass`，因为它们是纯数据结构，不是行为接口。

### 2.2 接口文件布局

对照 [sdd/03-project-structure.md](../../03-project-structure.md) §二：

```
harness/interfaces/
├── __init__.py               # 导出所有公开接口和类型
├── types.py                  # 17 个大包对象（UserRequest, SystemState, Attachment,
│                             #   EnvState, GuidesBundle, Example, AssemblyContext,
│                             #   Trajectory, Message, Response, ToolCall,
│                             #   ToolCallFunction, ToolDefinition, ToolCallRecord,
│                             #   ToolResult, ToolTransform, MemoryItem）
├── input_adapter.py          # InputAdapter Protocol
├── guide_provider.py         # GuideProvider Protocol + GuideContext dataclass
├── context_assembler.py      # ContextAssembler Protocol
├── memory_backend.py         # MemoryBackend Protocol
├── sensor.py                 # Sensor Protocol
├── tool.py                   # Tool Protocol
├── system_tool_provider.py   # SystemToolProvider Protocol
├── mcp_adapter.py            # MCPAdapter Protocol
├── mcp_handler.py            # MCPHandler Protocol
└── hook.py                   # Hook 函数类型 + HookContext dataclass
```

> **注意**：`tool_registry.py`（ToolRegistry Protocol）和 `mcp_manager.py`（MCPManager Protocol）已在 batch-06 重设计中被删除。ToolRegistry 的职责由框架内部组件 `ToolRouter`（`harness/core/tool_router.py`，非 DI）替代，MCPManager 的职责由 `MCPAdapter` Protocol 替代。

### 2.3 与 `_Minimal*` 类型的关系

**batch-02 仅定义正式类型，不做迁移。batch-02-1 完成迁移。**

- `harness/core/types.py` 中的 `_Minimal*` 类型在 batch-02 期间**不动**
- `harness/core/orchestrator.py` 的 import 在 batch-02 期间**不动**
- 新增的 `harness/interfaces/types.py` 是**独立的正式类型定义**
- batch-02-1 将 orchestator / LLM adapter / messaging builder 全部迁移为正式类型，删除 `_normalize_*` 桥接方法和 `_Minimal*` 类型

```
batch-02（当前）:
  _Minimal* (core/types.py)    ← orchestrator 使用，不动
  正式类型 (interfaces/types.py) ← 新增，定义完成后待迁移

batch-02-1（接口测试 + 迁移）:
  _Minimal* 全部替换为正式类型
  _normalize_* 桥接方法删除
  orchestrator / adapter / builder 统一使用正式类型
  core/types.py 标记为废弃

batch-03~09:
  所有组件实现直接使用正式类型，无需 normalize 桥接
```

### 2.4 `__init__.py` 升级策略

当前 `harness/interfaces/__init__.py` 定义了 8 个空占位类，被 `orchestrator.py` 作为 DI 容器的注册 key 使用。

升级方式：**将空类替换为同名的正式 Protocol 类的 re-export**。

```python
# 当前（占位）
class InputAdapter:
    """输入输出适配器接口。"""
    pass

# 升级后
from .input_adapter import InputAdapter
from .guide_provider import GuideProvider
# ...
```

**向后兼容保证**：`orchestrator.py` 中 `from ..interfaces import InputAdapter` 仍然有效 — import 路径不变，`InputAdapter` 从空类变成同名 Protocol，DI 容器当做 `type` key 使用不受影响。

---

## 三、17 个大包对象设计

全部定义在 `harness/interfaces/types.py`。严格对照 [sdd/02-interfaces.md](../../02-interfaces.md) §通用大包对象。

### 3.1 字段完整性检查清单

对照 SDD 逐一确认每个类型的字段：

| # | 类型 | SDD 定义的字段 | 数量 |
|---|------|---------------|------|
| 1 | `UserRequest` | text: str, attachments: List[Attachment], context: Dict[str, Any], system_state: SystemState, session_id: str, metadata: Dict[str, Any] | 6 |
| 2 | `SystemState` | phase: str, session_id: str, run_mode: str, metadata: Dict[str, Any] | 4 |
| 3 | `Attachment` | type: str, content: Any, meta: Dict[str, Any] | 3 |
| 4 | `EnvState` | work_dir: str, git_status: Dict[str, Any], timestamp: float, platform: str | 4 |
| 5 | `GuidesBundle` | identity: str, capabilities: List[str], rules: List[str], constraints: List[str], examples: List[Example] | 5 |
| 6 | `Example` | input: str, output: str | 2 |
| 7 | `AssemblyContext` | user_request: UserRequest, guides: GuidesBundle, available_tools: List[ToolDefinition], history: List[Message], memories: List[MemoryItem], system_state: SystemState, metadata: Dict[str, Any] | 7 |
| 8 | `Trajectory` | session_id: str, history: List[Message], tool_calls: List[ToolCallRecord], final_output: str, execution_time: float, system_state: SystemState, metadata: Dict[str, Any] | 7 |
| 9 | `Message` | role: str, content: str, tool_call_id: Optional[str], tool_calls: Optional[List[ToolCall]] | 4 |
| 10 | `Response` | text: Optional[str], thinking: Optional[str], tool_uses: List[ToolCall], stop_reason: str | 4 |
| 11 | `ToolCall` | id: str, type: str, function: ToolCallFunction | 3 |
| 12 | `ToolCallFunction` | name: str, arguments: str | 2 |
| 13 | `ToolDefinition` | name: str, description: str, parameters: Dict[str, Any] | 3 |
| 14 | `ToolCallRecord` | tool_call_id: str, tool_name: str, arguments: Dict[str, Any], result: Any, started_at: float, finished_at: float, error: Optional[str] | 7 |
| 15 | `ToolResult` | success: bool, content: Any, error: Optional[str] | 3 |
| 16 | `MemoryItem` | key: str, value: Any, namespace: str, timestamp: float, metadata: Dict[str, Any] | 5 |

### 3.2 类型标注约定

- 所有字段使用 `typing` 模块的泛型：`List`, `Dict`, `Optional`, `Any`
- 使用 `from __future__ import annotations` 延迟求值解决前向引用
- `dataclass` 的 `field(default_factory=...)` 用于可变默认值（List、Dict）
- 可选字段使用 `Optional[X] = None` 默认值

> **关于 Optional 标注的两个视角**：
>
> §3.1 字段表展示的是 **SDD 的"最简声明"形式**（如 `user_request: UserRequest`），去掉了 `Optional` 以保持可读性和与 SDD 规格文本一致。
>
> 实际实现代码（[tasks.md](tasks.md) 阶段 1）中使用了 **"鲁棒 dataclass"形式**（如 `user_request: Optional[UserRequest] = None`），加了 `Optional` 以允许 `None` 默认值。这两种形式影响到的字段包括：
> - `AssemblyContext.user_request`, `AssemblyContext.guides`
> - `GuideContext.user_request`, `GuideContext.env_state`
>
> **实现者应遵循 tasks.md 的鲁棒形式** — 非 Optional 字段在 dataclass 中使用 `None` 默认值会导致类型检查器报错。

### 3.3 前向引用处理

`types.py` 中存在环状引用（`UserRequest` → `SystemState`，`AssemblyContext` → `UserRequest` / `GuidesBundle` / `ToolDefinition` / `Message` / `MemoryItem` / `SystemState`）。解决方案：

- 使用 `from __future__ import annotations` 使所有 annotation 变为字符串延迟求值
- 所有类型按逻辑顺序排列：基础类型在前，组合类型在后

---

## 四、10 个接口文件签名（9 个 Protocol + 1 个 Hook 类型别名）

严格对照 [sdd/02-interfaces.md](../../02-interfaces.md) §组件接口。

### 4.1 InputAdapter

```python
@runtime_checkable
class InputAdapter(Protocol):
    def receive(self) -> UserRequest: ...
    def send(self, response: Response) -> None: ...
```

### 4.2 GuideProvider

```python
@runtime_checkable
class GuideProvider(Protocol):
    def get_guides(self, context: GuideContext) -> GuidesBundle: ...
```

`GuideContext` 定义在 `guide_provider.py`，字段：`user_request: UserRequest`, `system_state: SystemState`, `env_state: EnvState`, `metadata: Dict[str, Any]`。

### 4.3 ContextAssembler

```python
@runtime_checkable
class ContextAssembler(Protocol):
    def assemble(self, inputs: AssemblyContext) -> List[Message]: ...
```

注意：SDD 中参数名是 `inputs`，返回值是 `List[Message]`（不是 `List[Dict]`）。

### 4.4 MemoryBackend

```python
@runtime_checkable
class MemoryBackend(Protocol):
    def read(self, key: str, namespace: str) -> Optional[Any]: ...
    def write(self, key: str, value: Any, namespace: str) -> None: ...
    def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]: ...
    def list_namespaces(self) -> List[str]: ...
```

注意：`search()` 返回值是 `List[MemoryItem]`（不是 `List[Dict]`）。

### 4.5 Sensor

```python
@runtime_checkable
class Sensor(Protocol):
    def sense(self, trajectory: Trajectory) -> None: ...
```

### 4.6 Tool

```python
@runtime_checkable
class Tool(Protocol):
    def get_definition(self) -> ToolDefinition: ...
    def execute(self, args: Dict[str, Any]) -> ToolResult: ...
```

### 4.7 SystemToolProvider

```python
@runtime_checkable
class SystemToolProvider(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
```

系统工具提供者 — 管理本地实现的 Tool 集合。DI 插件，用户可替换。

### 4.8 MCPAdapter

```python
@runtime_checkable
class MCPAdapter(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

MCP 适配层 — 消费外部 MCP Server，经转换后暴露工具。DI 插件，不注册即裁切。`shutdown()` 用于关闭 MCP Server 子进程连接。

### 4.9 MCPHandler

```python
@runtime_checkable
class MCPHandler(Protocol):
    def transform_schema(self, name: str, schema: Dict) -> Dict: ...
    def transform_args(self, name: str, args: Dict) -> Dict: ...
    def transform_result(self, name: str, result: Any) -> Any: ...
```

MCP 程序化转换处理器 — 可选，注入到 `DefaultMCPAdapter` 的 `handler` 参数中。当 `ToolTransform` 声明式配置不够用时提供程序化钩子。

### 4.10 Hook

Hook 是函数类型，不是类 Protocol：

```python
Hook = Callable[[HookContext], None]
```

`HookContext` 定义在 `hook.py`，字段：`event: str`, `data: Any`, `system_state: SystemState`。

---

## 五、模块间依赖关系

### 5.1 类型之间的依赖

```
types.py（无内部依赖，最底层）
    ↑
    ├── input_adapter.py        (import UserRequest, Response)
    ├── guide_provider.py       (import UserRequest, SystemState, EnvState, GuidesBundle)
    ├── context_assembler.py    (import AssemblyContext, Message)
    ├── memory_backend.py       (import MemoryItem)
    ├── sensor.py               (import Trajectory)
    ├── tool.py                 (import ToolDefinition, ToolResult)
    ├── system_tool_provider.py (import ToolDefinition, ToolResult)
    ├── mcp_adapter.py          (import ToolDefinition, ToolResult)
    ├── mcp_handler.py          (import Dict, Any)
    └── hook.py                 (import SystemState)
```

### 5.2 接口之间的依赖

```
Tool ← SystemToolProvider, MCPAdapter（通过 ToolDefinition / ToolResult 间接依赖，不直接引用 Tool Protocol）
```

其余接口之间无互相依赖。

### 5.3 文件创建顺序

基于依赖关系的推荐创建顺序：

1. `types.py` — 无内部依赖，最先创建（含 17 个 dataclass，包括 `ToolTransform`）
2. `tool.py` — 仅依赖 types
3. `system_tool_provider.py`、`mcp_adapter.py`、`mcp_handler.py` — 依赖 types，可并行创建
4. 其余 6 个接口文件 — 依赖 types，可并行创建
5. `__init__.py` — 所有子模块就绪后更新

---

## 六、不与现有代码冲突的保证

> **batch-02 期间的保证**：以下保证仅对 batch-02 有效。batch-02-1 会主动修改 `orchestrator.py`、`llm_adapter.py`、`builder.py`、`core/types.py`，将 `_Minimal*` 全部替换为正式类型。

| 现有依赖方 | 当前用法 | batch-02 后是否受影响 | batch-02-1 后 |
|-----------|---------|---------------------|---------------|
| `orchestrator.py` | `from ..interfaces import InputAdapter, ...` 用作 DI key | ✅ 不受影响，Protocol 类同样是合法 type | 内部 `_Minimal*` → 正式类型 |
| `orchestrator.py` | `from .types import _Minimal*` | ✅ 不受影响，`_Minimal*` 不动 | 替换为 `from ..interfaces.types import ...` |
| `adapters/llm_adapter.py` | `from ..core.orchestrator import _Minimal*` | ✅ 不受影响 | `_MinimalResponse` → `Response` |
| `messaging/builder.py` | `from ..core.types import _Minimal*` | ✅ 不受影响 | `_Minimal*` → 正式类型 |
| `di.py` | `from .core.orchestrator import InputAdapter` | ✅ 不受影响（import 路径不变） | 不受影响 |
| 所有测试 | 使用 `_Minimal*` 类型和占位类作 DI key | ✅ 不受影响 | 更新为使用正式类型 |

---

## 七、与后续 Batch 的接口约定

此节帮助后续 batch 明确"我的实现应该返回什么类型"。

| 后续 Batch | 组件方法 | 参数类型（来自 batch-02） | 返回类型（来自 batch-02） |
|-----------|---------|--------------------------|--------------------------|
| batch-03 | `MemoryBackend.search()` | `query: str, namespace: str, limit: int` | `List[MemoryItem]` |
| batch-03 | `MemoryBackend.read()` | `key: str, namespace: str` | `Optional[Any]` |
| batch-04 | `GuideProvider.get_guides()` | `context: GuideContext` | `GuidesBundle` |
| batch-05 | `ContextAssembler.assemble()` | `inputs: AssemblyContext` | `List[Message]` |
| batch-06 | `Tool.get_definition()` | (无) | `ToolDefinition` |
| batch-06 | `Tool.execute()` | `args: Dict[str, Any]` | `ToolResult` |
| batch-06 | `SystemToolProvider.get_tools()` | (无) | `List[ToolDefinition]` |
| batch-06 | `SystemToolProvider.execute()` | `name: str, args: Dict[str, Any]` | `ToolResult` |
| batch-06 | `MCPAdapter.get_tools()` | (无) | `List[ToolDefinition]` |
| batch-06 | `MCPAdapter.execute()` | `name: str, args: Dict[str, Any]` | `ToolResult` |
| batch-06 | `MCPAdapter.shutdown()` | (无) | `None` |
| batch-06 | `MCPHandler.transform_schema()` | `name: str, schema: Dict` | `Dict` |
| batch-06 | `MCPHandler.transform_args()` | `name: str, args: Dict` | `Dict` |
| batch-06 | `MCPHandler.transform_result()` | `name: str, result: Any` | `Any` |
| batch-07 | `Sensor.sense()` | `trajectory: Trajectory` | `None` |
| batch-08 | `InputAdapter.receive()` | (无) | `UserRequest` |
| batch-08 | `InputAdapter.send()` | `response: Response` | `None` |
| batch-09 | `Hook` 函数 | `context: HookContext` | `None` |
