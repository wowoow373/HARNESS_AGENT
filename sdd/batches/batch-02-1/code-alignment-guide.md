# batch-02-1 代码对齐指南

> 本文档记录 batch-06 重设计后，batch-02-1 产出的代码需要做的变更。
> 重设计详情见 [batch-06 known-issues](../batch-06-tool-mcp-manager/known-issues.md)。

---

batch-02-1 的核心任务是 `_Minimal*` → 正式类型的迁移，受影响的代码变更量较小。

## 需要修改的文件

### 1. 测试文件中的 import 更新

| 文件 | 变更 |
|------|------|
| `tests/test_di.py` | `from harness.interfaces import ToolRegistry, MCPManager` → `from harness.interfaces import SystemToolProvider, MCPAdapter, MCPHandler` |
| `tests/test_integration.py` | 同上 |
| 其他测试文件 | 搜索 `ToolRegistry` / `MCPManager` 引用并替换 |

### 2. `harness/core/orchestrator.py`

已在 batch-01-kernel 的 code-alignment-guide 中覆盖。batch-02-1 迁移后的正式类型引用（`from ..interfaces import ToolRegistry`）需要同步变更为 `SystemToolProvider, MCPAdapter`。

### 3. 不需要变更的文件

- `harness/messaging/builder.py` — 格式转换不涉及工具管理
- `harness/adapters/llm_adapter.py` — 不引用 ToolRegistry/MCPManager
- `CORE_DEVELOPER_GUIDE.md` — 如有示例代码引用旧接口需更新，建议全局搜索后处理
