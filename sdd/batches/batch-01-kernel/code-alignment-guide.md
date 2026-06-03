# batch-01-kernel 代码对齐指南

> 本文档记录 batch-06 重设计后，batch-01 产出的代码需要做的变更。
> 重设计详情见 [batch-06 known-issues](../batch-06-tool-mcp-manager/known-issues.md)。

---

## 核心变更：编排器中 ToolRegistry → ToolRouter

### 1. `harness/core/orchestrator.py`

**import 变更**:
```python
# 删除
from ..interfaces import ToolRegistry

# 新增
from ..interfaces import SystemToolProvider, MCPAdapter
from .tool_router import ToolRouter
```

**`_phase_init()` — 阶段一，步骤 4**:

旧：
```python
tool_registry = self._resolve_optional(ToolRegistry)
self._cached_tool_registry = tool_registry
if tool_registry:
    available_tools = tool_registry.list_tools()
```

新：
```python
self._tool_router = ToolRouter()
sys_provider = self._resolve_optional(SystemToolProvider)
if sys_provider:
    self._tool_router.register_provider(sys_provider)
mcp_adapter = self._resolve_optional(MCPAdapter)
if mcp_adapter:
    self._tool_router.register_provider(mcp_adapter)
available_tools = self._tool_router.list_tools()
```

**`_phase_loop()` — 阶段二**:

- `self._cached_tool_registry` → `self._tool_router`
- `tool_registry.execute(name, args)` → `self._tool_router.execute(name, args)`
- 错误消息 `"ToolRegistry not registered"` → `"No tool provider registered"`

**`_phase_end()` — 阶段三**:

新增（在 Sensor.sense() 之前或之后）:
```python
if hasattr(self, '_tool_router'):
    self._tool_router.shutdown()
```

**`__init__()`**:
- 删除 `self._cached_tool_registry` 属性
- 新增 `self._tool_router` 属性（初始化为 None）

### 2. 测试文件

| 文件 | 变更 |
|------|------|
| `tests/test_di.py` | `MockToolRegistry` → `MockSystemToolProvider`；list_tools() → get_tools() |
| `tests/test_integration.py` | DI 注册 `ToolRegistry` → `SystemToolProvider`（+ 可选 `MCPAdapter`） |

### 3. 不需要变更的文件

- `harness/core/container.py` — DI 容器逻辑不变
- `harness/core/types.py` — 已废弃，不动
- `harness/messaging/builder.py` — 格式转换逻辑不变
