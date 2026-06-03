# batch-06: Tool & MCP Manager — 任务拆解

> 迁移顺序遵循「先接口后实现、先框架后组件、先单元后集成」原则。
> 每步任务可独立验证，减少级联错误。

---

## Phase 1: 接口层（interfaces/）

### T1. 新增 SystemToolProvider Protocol
- 文件: `harness/interfaces/system_tool_provider.py`
- 方法: `get_tools() → List[ToolDefinition]`, `execute(name, args) → ToolResult`
- 验收: `isinstance(obj, SystemToolProvider)` duck-typing 通过

### T2. 新增 MCPAdapter Protocol
- 文件: `harness/interfaces/mcp_adapter.py`
- 方法: `get_tools()`, `execute()`, `shutdown()`
- 验收: 含 `shutdown()` 的完整三元组

### T3. 新增 MCPHandler Protocol
- 文件: `harness/interfaces/mcp_handler.py`
- 方法: `transform_schema()`, `transform_args()`, `transform_result()`
- 验收: 三阶段签名正确

### T4. 新增 ToolTransform dataclass
- 文件: `harness/interfaces/types.py`（修改）
- 字段: `expose_as`, `description_override`, `hidden`, `arg_defaults`, `arg_transform`, `result_transform`
- 验收: 零参数构造 `ToolTransform()` 全默认值

### T5. 更新 interfaces/__init__.py 导出
- 删除: `ToolRegistry`, `MCPManager`
- 新增: `SystemToolProvider`, `MCPAdapter`, `MCPHandler`, `ToolTransform`
- 验收: `from harness.interfaces import SystemToolProvider, MCPAdapter, MCPHandler, ToolTransform` 成功

### T6. 更新 tool.py docstring
- 移除 `ToolRegistry`/`MCPManager` 引用
- 改为 `ToolRouter`/`SystemToolProvider`
- 验收: docstring 不含过时引用

---

## Phase 2: 框架内核（core/）

### T7. 实现 ToolRouter
- 文件: `harness/core/tool_router.py`
- 功能:
  - `register_provider(provider)` — 注册 Provider，构建 name→provider 路由表
  - `list_tools()` — 合并所有 Provider 的工具定义（去重）
  - `execute(name, args)` — 查表分发执行
  - `shutdown()` — 统一清理（仅调用有 shutdown() 的 Provider）
  - 查询: `has_tool()`, `tool_count`, `provider_count`
- 边界: 同名工具后者覆盖 + WARNING；未知工具 KeyError；get_tools() 异常不阻断
- **ToolRouter 不是 DI 组件**，由编排器直接实例化

### T8. 修改编排器 _phase_init()
- 删除 `resolve(ToolRegistry)` 逻辑
- 改为:
  1. 创建 `ToolRouter()` 实例
  2. `resolve_optional(SystemToolProvider)` → `tool_router.register_provider()`
  3. `resolve_optional(MCPAdapter)` → `tool_router.register_provider()`
  4. `tool_router.list_tools()` → 缓存 `_cached_tools`

### T9. 修改编排器 _phase_loop()
- `self._cached_tool_registry.execute()` → `self._cached_tool_router.execute()`
- 增加 `tool_router.has_tool()` 守卫

### T10. 修改编排器 _phase_end()
- 增加 `self._cached_tool_router.shutdown()` 调用
- 更新 docstring: `Sensor.sense() → ToolRouter.shutdown() → 清理内部状态`

---

## Phase 3: 默认实现（components/）

### T11. 创建 BaseTool ABC
- 文件: `harness/components/tool/base.py`
- 抽象方法: `get_definition()`, `execute()`
- 验收: 子类化 + 实现两个方法即满足 Tool Protocol

### T12. 创建 @inline_tool 装饰器 + InlineTool
- 文件: `harness/components/tool/inline_tool.py`
- 功能: 将普通函数包装为 Tool 实例
- 边界: 函数返回 ToolResult 时透传，返回其他值时自动包装

### T13. 创建系统内置工具
- 文件: `harness/components/tool/system_tools.py`
- `ReadFileTool` — 读取文件（FileNotFound/PermissionDenied → ToolResult.error）
- `WriteFileTool` — 写入文件（自动创建上级目录；覆盖写）
- `ShellTool` — 执行 shell 命令（120s timeout；stdout+stderr 合并返回）

### T14. 实现 DefaultSystemToolProvider
- 文件: `harness/components/tool/default_system_tool_provider.py`
- 默认内置 read_file/write_file/shell 三个工具
- 支持 `extra_tools` 追加 + `use_builtins=False` 完全替换
- 工具名冲突时 latter wins + WARNING

### T15. 创建 MCPClient
- 文件: `harness/components/mcp_manager/mcp_client.py`
- 功能: JSON-RPC over stdio 通信（initialize 握手、tools/list、tools/call）
- 生命周期: `start()` / `stop()` / `is_running()`
- 额外: `MCPServerConfig` dataclass

### T16. 实现 DefaultMCPAdapter
- 文件: `harness/components/mcp_manager/default_mcp_adapter.py`
- 转换流水线:
  1. **Schema**: MCP 原始 schema → ToolTransform(expose_as/description_override/hidden) → MCPHandler.transform_schema()
  2. **Args**: LLM args → arg_defaults → arg_transform → MCPHandler.transform_args()
  3. **Result**: MCP result → result_transform → MCPHandler.transform_result()
- `shutdown()` 清理所有 MCPClient 子进程

### T17. 创建包 __init__.py 文件
- `harness/components/tool/__init__.py` — 导出 BaseTool, InlineTool, inline_tool, ReadFileTool, WriteFileTool, ShellTool, DefaultSystemToolProvider
- `harness/components/mcp_manager/__init__.py` — 导出 MCPClient, MCPServerConfig, DefaultMCPAdapter

### T18. 删除旧接口文件
- 删除 `harness/interfaces/tool_registry.py`
- 删除 `harness/interfaces/mcp_manager.py`

---

## Phase 4: 测试

### T19. ToolRouter 单元测试
- 文件: `tests/test_tool_router.py`
- 覆盖: init/register/list_tools(合并+去重)/execute(路由分发+错误)/shutdown/has_tool/tool_count/provider_count

### T20. DefaultSystemToolProvider 单元测试
- 文件: `tests/test_system_tool_provider.py`
- 覆盖: 默认三工具/ReadFileTool(成功+文件不存在)/WriteFileTool(成功+嵌套目录)/ShellTool(成功+失败)/extra_tools/use_builtins=False/@inline_tool/边界

### T21. DefaultMCPAdapter + ToolTransform 单元测试
- 文件: `tests/test_mcp_adapter.py`
- 覆盖: 空构造/ToolTransform 各字段/MCPHandler duck-typing/空 servers/未启动 shutdown/未知工具 execute/MCPServerConfig

### T22. 编排器集成测试（更新）
- 文件: `tests/test_orchestrator.py`
- 变更: `ToolRegistry` → `SystemToolProvider`；Mock 适配新协议；验证 ToolRouter 全链路

### T23. 黑盒测试（更新）
- 文件: `tests/test_black_box.py`
- 变更: `TestToolRegistryDuckTyping` → `TestSystemToolProviderDuckTyping`

### T24. E2E 全流程测试
- 文件: `tests/test_e2e_tool_flow.py`（新增）
- 覆盖:
  1. 注册 SystemToolProvider → ToolRouter → Context → LLM 可见工具
  2. LLM tool_use → ToolRouter.execute() → 正确路由到对应 Provider
  3. ToolResult → 追加到 messages → LLM 再次调用（含工具结果上下文）
  4. _phase_end → ToolRouter.shutdown() → Provider 资源清理

---

## 任务依赖图

```
Phase 1 (T1-T6)    ──── 接口定义，无依赖
    ↓
Phase 2 (T7-T10)   ──── 依赖 Phase 1
    ↓
Phase 3 (T11-T18)  ──── 依赖 Phase 1，T11-T14 并行于 T15-T16
    ↓
Phase 4 (T19-T24)  ──── 依赖 Phase 2 + Phase 3
```
