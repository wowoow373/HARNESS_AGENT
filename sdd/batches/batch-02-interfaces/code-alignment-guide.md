# batch-02-interfaces 代码对齐指南

> 本文档记录 batch-06 重设计后，batch-02 产出的接口文件需要做的变更。
> 重设计详情见 [batch-06 known-issues](../batch-06-tool-mcp-manager/known-issues.md)。

---

## 一、删除的接口文件

| 文件 | 原因 |
|------|------|
| `harness/interfaces/tool_registry.py` | 职责被 `ToolRouter`（框架内部）+ `SystemToolProvider`/`MCPAdapter`（DI 插件）替代 |
| `harness/interfaces/mcp_manager.py` | 演化为 `MCPAdapter` Protocol |

## 二、新增的接口文件

### 2.1 `harness/interfaces/system_tool_provider.py`

```python
from typing import Any, Dict, List, Protocol, runtime_checkable
from .types import ToolDefinition, ToolResult

@runtime_checkable
class SystemToolProvider(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
```

### 2.2 `harness/interfaces/mcp_adapter.py`

```python
from typing import Any, Dict, List, Protocol, runtime_checkable
from .types import ToolDefinition, ToolResult

@runtime_checkable
class MCPAdapter(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

### 2.3 `harness/interfaces/mcp_handler.py`

```python
from typing import Any, Dict, Protocol, runtime_checkable

@runtime_checkable
class MCPHandler(Protocol):
    def transform_schema(self, name: str, schema: Dict) -> Dict: ...
    def transform_args(self, name: str, args: Dict) -> Dict: ...
    def transform_result(self, name: str, result: Any) -> Any: ...
```

## 三、修改的接口文件

### 3.1 `harness/interfaces/types.py`

新增 `ToolTransform` dataclass（放在 `ToolResult` 之后）:

```python
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

@dataclass
class ToolTransform:
    expose_as: Optional[str] = None
    description_override: Optional[str] = None
    hidden: bool = False
    arg_defaults: Dict[str, Any] = field(default_factory=dict)
    arg_transform: Optional[Callable] = None
    result_transform: Optional[Callable] = None
```

### 3.2 `harness/interfaces/tool.py`

docstring 变更：
- 去掉 "被 ToolRegistry 统一调度" → "被 ToolRouter 统一调度"
- 去掉 "用户不直接实现 Tool 接口，通过 MCPManager 间接注入" → "框架用户通过 SystemToolProvider 提供本地 Tool，通过 MCPAdapter 提供 MCP 来源 Tool"

### 3.3 `harness/interfaces/__init__.py`

```python
# 删除这两行
from .tool_registry import ToolRegistry
from .mcp_manager import MCPManager

# 新增这三行
from .system_tool_provider import SystemToolProvider
from .mcp_adapter import MCPAdapter
from .mcp_handler import MCPHandler

# __all__ 中删除 "ToolRegistry", "MCPManager"，新增
# "SystemToolProvider", "MCPAdapter", "MCPHandler"
```

types 导入也要新增 `ToolTransform`。

## 四、测试文件变更

| 文件 | 变更 |
|------|------|
| `tests/test_interfaces_types.py` | 新增 `ToolTransform` 测试 |
| `tests/test_interfaces_conformance.py` | 删除 `ToolRegistry`/`MCPManager` conformance 测试；新增 `SystemToolProvider`/`MCPAdapter`/`MCPHandler` 测试 |
