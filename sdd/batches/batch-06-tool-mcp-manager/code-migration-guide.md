# batch-06 代码迁移指南

> 简要列出 batch-06 重设计涉及的代码变更，供实现时逐项勾对。
> 详细设计见 [known-issues.md](known-issues.md)。

---

## 一、删除的文件

- [ ] `harness/interfaces/tool_registry.py` — ToolRegistry Protocol
- [ ] `harness/interfaces/mcp_manager.py` — MCPManager Protocol

## 二、新增的文件

- [ ] `harness/interfaces/system_tool_provider.py` — SystemToolProvider Protocol
- [ ] `harness/interfaces/mcp_adapter.py` — MCPAdapter Protocol
- [ ] `harness/interfaces/mcp_handler.py` — MCPHandler Protocol
- [ ] `harness/core/tool_router.py` — ToolRouter（框架内部，非 DI）
- [ ] `harness/components/tool/default_system_tool_provider.py` — DefaultSystemToolProvider
- [ ] `harness/components/mcp_manager/default_mcp_adapter.py` — DefaultMCPAdapter

## 三、修改的文件

- [ ] `harness/interfaces/__init__.py`
  - 删除 `ToolRegistry`、`MCPManager` 的 export
  - 新增 `SystemToolProvider`、`MCPAdapter`、`MCPHandler` 的 export
- [ ] `harness/interfaces/types.py`
  - 新增 `ToolTransform` dataclass
- [ ] `harness/interfaces/tool.py`
  - 更新 docstring：去掉 `ToolRegistry`/`MCPManager` 引用，改为 `ToolRouter`/`SystemToolProvider`
- [ ] `harness/core/orchestrator.py`
  - `_phase_init()`：删除 `resolve(ToolRegistry)`，改为创建 `ToolRouter` + `resolve_optional(SystemToolProvider)` + `resolve_optional(MCPAdapter)` + `register_provider()`
  - `_phase_loop()`：`tool_registry.execute()` → `self._tool_router.execute()`
  - `_phase_end()`：添加 `self._tool_router.shutdown()`
- [ ] `harness/components/tool/__init__.py` — 导出 `inline_tool`
- [ ] `harness/components/mcp_manager/tool_proxy.py` — 增加 transform 支持

## 四、移动/重组的文件

- [ ] `harness/components/mcp_manager/inline_tool.py` → `harness/components/tool/inline_tool.py`
- [ ] `harness/components/mcp_manager/` 目录重组：
  - 当前：`server_mcp_manager.py` + `inline_mcp_manager.py`
  - 变更为：`default_mcp_adapter.py`（保留 `mcp_client.py` 不变）

## 五、测试文件变更

- [ ] `tests/test_tool_registry.py` → 删除，新增 `tests/test_tool_router.py` + `tests/test_system_tool_provider.py`
- [ ] `tests/test_mcp_manager.py` → 变更为 `tests/test_mcp_adapter.py`

---

## 迁移顺序建议

1. 先新增 interfaces/ 下的 3 个 Protocol 文件 + `ToolTransform`
2. 更新 `interfaces/__init__.py` 和 `interfaces/tool.py`
3. 实现 `ToolRouter`（`core/tool_router.py`）
4. 实现 `DefaultSystemToolProvider` + `DefaultMCPAdapter`
5. 修改 `orchestrator.py` 适配新组件
6. 更新/新增测试
7. 最后删除旧文件（`tool_registry.py`、`mcp_manager.py`）
