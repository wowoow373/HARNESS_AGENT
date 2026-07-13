# batch-06: Tool & MCP Manager — 验收标准

> 每个验收条目都是可独立验证的、可测试的断言。
> E2E 条目覆盖从 DI 注册 → ToolRouter 合并 → Context 拼装 → LLM tool_use → 路由执行 → shutdown 的完整链路。

---

## AC-TOOL-01: SystemToolProvider 注册与工具发现

**Given** DI 容器中注册了 `DefaultSystemToolProvider`
**When** 编排器 `_phase_init()` 执行
**Then**
- `ToolRouter` 被创建，`SystemToolProvider` 通过 `register_provider()` 注册
- `tool_router.list_tools()` 返回包含 `read_file`、`write_file`、`shell` 三个工具定义的列表
- 每个 ToolDefinition 含非空的 `name`、`description`、`parameters`
- `_cached_tools` 缓存了这些工具定义

**验证方式**: 单元测试 — 注册 MockSystemToolProvider，检查 `list_tools()` 输出

---

## AC-TOOL-02: 工具拼接到 AssemblyContext

**Given** `ToolRouter` 已有注册的 Provider
**When** 编排器构建 `AssemblyContext`
**Then**
- `AssemblyContext.available_tools` 包含 ToolRouter 合并且去重后的 ToolDefinition 列表
- `ContextAssembler.assemble()` 收到的 `ctx.available_tools` 非空
- 工具定义被正确序列化为 OpenAI tool format 传给 LLM

**验证方式**: 集成测试 — 注册 SystemToolProvider，运行 _phase_init，检查 ctx.available_tools

---

## AC-TOOL-03: ToolRouter 按工具名正确路由执行

**Given** `ToolRouter` 注册了 SystemToolProvider（含 `read_file`）+ MCPAdapter（含 `fs_search`）
**When** 编排器调用 `tool_router.execute("read_file", {"path": "/tmp/x"})` 和 `tool_router.execute("fs_search", {"pattern": "*.py"})`
**Then**
- `read_file` 调用被路由到 SystemToolProvider.execute()
- `fs_search` 调用被路由到 MCPAdapter.execute()
- 两个 Provider 各自只收到属于自己的调用

**验证方式**: 单元测试 — ToolRouter 注册两个 mock Provider，检查 execute 路由

---

## AC-TOOL-04: ToolResult → messages 闭环

**Given** LLM 返回了 `tool_uses=[ToolCall(name="read_file", arguments={"path": "/tmp/x"})]`
**When** 编排器内层循环执行该工具并构建 tool_result_message
**Then**
- `ToolResult` 被正确提取（success/content/error）
- `tool_result_message` 以 `role="tool"` + `tool_call_id` 追加到 messages 列表
- 内层循环继续调用 LLM，LLM 收到的 messages 包含工具执行结果
- `ToolCallRecord` 被正确记录（tool_name, arguments, result, started_at, finished_at, error）

**验证方式**: 集成测试 — mock LLM 两轮调用（tool_use + text），检查 messages 链路

---

## AC-TOOL-05: MCPAdapter 注册与运行时裁切

**Given** 用户可以在 DI 容器中选择注册或不注册 `MCPAdapter`
**When** `_phase_init()` 执行 `resolve_optional(MCPAdapter)`
**Then**
- 已注册 → `tool_router.register_provider(mcp_adapter)` 被调用，MCP 工具加入路由表
- 未注册 → `resolve_optional()` 返回 None，WARNING 日志记录，编排器继续正常运行
- 未注册时不调用任何 MCPAdapter 方法

**验证方式**: 单元测试 — 两个场景分别验证

---

## AC-TOOL-06: ToolTransform 声明式转换

**Given** `DefaultMCPAdapter` 配置了 transforms:
```
{"dangerous_tool": ToolTransform(hidden=True),
 "old_name": ToolTransform(expose_as="new_name"),
 "verbose_tool": ToolTransform(description_override="Simpler description"),
 "remote_read": ToolTransform(arg_defaults={"encoding": "utf-8"})}
```
**When** `adapter.get_tools()` 被调用
**Then**
- `dangerous_tool` 不在返回的工具列表中（hidden）
- `old_name` 以 `new_name` 名称出现在列表中
- `verbose_tool` 的 description 为 "Simpler description"
- `remote_read` 执行时 LLM 参数自动合并 `encoding: utf-8`（LLM 传入值优先）

**验证方式**: 单元测试 — Mock MCPClient，验证转换后的 ToolDefinition 和 execute 行为

---

## AC-TOOL-07: MCPHandler 程序化转换

**Given** `DefaultMCPAdapter` 注入了实现 MCPHandler Protocol 的 handler
**When** 工具发现（get_tools）和执行（execute）流程发生
**Then**
- `handler.transform_schema(name, schema)` 在 get_tools 时对每个工具调用
- `handler.transform_args(name, args)` 在 execute 前调用（arg_defaults 之后）
- `handler.transform_result(name, result)` 在 execute 后调用
- 任一 transform 方法抛异常 → WARNING 日志，不阻断流程

**验证方式**: 单元测试 — SpyHandler 记录调用，验证调用顺序和参数

---

## AC-TOOL-08: shutdown 统一生命周期清理

**Given** ToolRouter 注册了 SystemToolProvider（无 shutdown）+ MCPAdapter（有 shutdown）
**When** 编排器 `_phase_end()` 调用 `tool_router.shutdown()`
**Then**
- `MCPAdapter.shutdown()` 被调用
- SystemToolProvider 不受影响（无 shutdown 方法，跳过）
- 路由表 `_routes` 和 `_providers` 被清空
- 任一 Provider 的 shutdown 异常不影响其他 Provider

**验证方式**: 单元测试 — 注册两个 Provider，检查 shutdown 调用和状态清理

---

## AC-TOOL-09: 同名工具冲突处理

**Given** SystemToolProvider 和 MCPAdapter 各自暴露了名为 `search` 的工具
**When** MCPAdapter 在 SystemToolProvider 之后注册到 ToolRouter
**Then**
- WARNING 日志记录 `Tool name conflict: 'search' from 'MCPAdapter' overrides existing from 'SystemToolProvider'`
- `tool_router.execute("search", ...)` 路由到 MCPAdapter（后者覆盖）
- `tool_router.list_tools()` 中 `search` 只出现一次（去重）

**验证方式**: 单元测试 — 两个 mock Provider 各含同名工具，验证路由和 list_tools

---

## AC-TOOL-10: 未知工具执行错误处理

**Given** ToolRouter 路由表中没有名为 `nonexistent` 的工具
**When** LLM 请求执行 `tool_router.execute("nonexistent", {})`
**Then**
- 抛出 `KeyError`，消息包含工具名和可用工具列表
- 编排器捕获后记录 `ToolCallRecord`，error 字段非空
- 错误被包装为 tool_result_message 返回给 LLM
- 编排器不崩溃，继续内层循环

**验证方式**: 单元测试 — 空 ToolRouter 执行未知工具；集成测试 — Mock LLM 调用未知工具

---

## AC-TOOL-11: SystemToolProvider 自定义（替换/扩展）

**Given** 用户有以下自定义需求：
1. 在默认工具集上加自定义工具
2. 完全替换默认工具集

**When** 分别构造：
```python
# 场景1: 追加
DefaultSystemToolProvider(extra_tools=[MyTool()])
# 场景2: 替换
DefaultSystemToolProvider(tools=[ToolA(), ToolB()], use_builtins=False)
# 场景3: @inline_tool
@inline_tool(name="my_fn", ...)
def my_fn(args): ...
DefaultSystemToolProvider(tools=[my_fn], use_builtins=False)
```

**Then**
- 场景1: tool_count = 4（3 builtins + 1 custom），含 `my_tool`
- 场景2: tool_count = 2，不含 `read_file`/`write_file`/`shell`
- 场景3: tool_count = 1，tool 可正常 execute 并返回结果
- 函数返回 ToolResult 时透传，返回其他值时自动包装为 ToolResult(success=True, content=...)

**验证方式**: 单元测试 — 三种场景分别验证

---

## AC-TOOL-12: 完整 E2E 流程 — 本地工具

**Given** DI 容器注册了：
- `InputAdapter`（提供用户输入 "read /tmp/test.txt"）
- `SystemToolProvider`（DefaultSystemToolProvider，含 read_file）
- Mock LLM 第一轮返回 tool_use `{"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}`，第二轮返回 text "file contents: hello"

**When** `Harness.from_container(container, call_llm=mock_llm).run()`

**Then** 完整链路：
1. `_phase_init`: ToolRouter 创建 → SystemToolProvider 注册 → list_tools() → 工具定义进入 ctx.available_tools
2. ContextAssembler 将 tools 序列化传入 LLM messages
3. 第一轮 LLM 返回 tool_use → ToolRouter.execute("read_file", ...) → SystemToolProvider 执行 → 返回 ToolResult
4. ToolResult → tool_result_message → 追加到 messages
5. 第二轮 LLM 收到含工具结果的 messages → 返回 text → 发给用户
6. `_phase_end`: Sensor.sense(trajectory) → ToolRouter.shutdown()
7. Trajectory.tool_calls 包含 1 条 ToolCallRecord，tool_name="read_file"

**验证方式**: E2E 测试 — mock 所有组件，完整运行 Harness.run()，逐步骤断言

---

## AC-TOOL-13: 完整 E2E 流程 — 多 Provider 并行

**Given** DI 容器注册了：
- `SystemToolProvider`（含 read_file、shell）
- Mock `MCPAdapter`（含 mcp_search）
- Mock LLM 返回 tool_use 分别调用 read_file 和 mcp_search

**When** 运行完整生命周期

**Then**
- `list_tools()` 返回 3 个工具（合并且去重）
- `execute("read_file", ...)` 路由到 SystemToolProvider
- `execute("mcp_search", ...)` 路由到 MCPAdapter
- `shutdown()` 时 MCPAdapter.shutdown() 被调用

**验证方式**: E2E 测试 — 两个 mock Provider，验证合并与分发

---

## AC-TOOL-14: Tool 执行失败完整记录

**Given** SystemToolProvider 的 ShellTool 执行返回 `ToolResult(success=False, error="Command not found")`
**When** LLM 调用 shell 工具
**Then**
- `ToolCallRecord` 的 `result` 为 None（因为 success=False）
- `ToolCallRecord` 的 `error` 为 "Command not found"
- `tool_result_message` 的 content 以 "Error:" 开头
- LLM 下一轮调用收到错误消息并可以据此调整行为

**验证方式**: 集成测试 — Mock SystemToolProvider.execute() 返回失败，检查 ToolCallRecord

---

## AC-TOOL-15: ContextAssembler 内层循环不重复调用

**Given** LLM 在一次外层循环中连续返回 3 次 tool_use
**When** 内层循环反复调用 LLM
**Then**
- `ContextAssembler.assemble()` 在内层循环中调用次数为 0
- 每次 tool_use 循环仅追加 tool_call + tool_result 到 messages，不重新组装上下文

**验证方式**: 集成测试 — Spy ContextAssembler，统计调用次数

---

## AC-TOOL-16: MCP 工具三阶段转换 E2E

**Given** `DefaultMCPAdapter` 配置了：
- `transforms={"raw_search": ToolTransform(expose_as="search", description_override="Search files")}`
- `handler=AuthInjector()` 注入认证 token
- Mock `MCPClient` 返回原始工具 `{"name": "raw_search", "inputSchema": {...}}`

**When** `adapter.get_tools()` 然后 `adapter.execute("search", {"pattern": "*.py"})`

**Then**
1. **Schema 阶段**: `get_tools()` 返回的 ToolDefinition name="search"（非 raw_search），description="Search files"
2. **Args 阶段**: `execute()` 调用 MCPClient.call_tool 时 args 包含注入的认证 token
3. **Result 阶段**: MCPClient 返回的原始结果经 handler.transform_result() 处理后才返回

**验证方式**: 单元测试 — Spy MCPClient + Spy Handler，验证各阶段调用

---

## AC-TOOL-17: MCPServerConfig 完整性

**Given** 用户创建 `MCPServerConfig(name="fs", command="npx", args=["-y", "@anthropic/mcp-filesystem", "/tmp"], env={"NODE_ENV": "production"}, timeout=60.0)`
**Then**
- 所有字段正确存储
- 默认构造 `MCPServerConfig()` 时 name=""、command=""、args=[]、env={}、timeout=30.0

**验证方式**: 单元测试 — 字段断言

---

## AC-TOOL-18: DefaultSystemToolProvider 查询方法

**Given** `DefaultSystemToolProvider()` 默认实例
**Then**
- `has_tool("read_file")` → True
- `has_tool("nonexistent")` → False
- `tool_count` → 3
- `execute("nonexistent", {})` → 抛出 KeyError

**验证方式**: 单元测试

---

## AC-TOOL-19: ToolRouter 查询方法

**Given** ToolRouter 注册了一个含 2 个工具的 Provider
**Then**
- `has_tool("t1")` → True
- `has_tool("missing")` → False
- `tool_count` → 2
- `provider_count` → 1
- `shutdown()` 后 `tool_count` → 0, `provider_count` → 0

**验证方式**: 单元测试

---

## AC-TOOL-20: 编排器 ToolRouter shutdown 后状态清理

**Given** 完整运行了 Harness.run()
**When** `_phase_end()` 执行完毕
**Then**
- `_history` 被清空
- `_tool_call_records` 被清空
- `_should_exit_flag` 为 False
- `_cached_tool_router` 的路由表已清空

**验证方式**: 集成测试 — run() 后检查编排器内部状态

---

## 验收矩阵

| AC | 类型 | 测试文件 | 覆盖状态 |
|----|------|----------|----------|
| AC-TOOL-01 | 单元 | test_tool_router.py | ✅ |
| AC-TOOL-02 | 集成 | test_orchestrator.py + test_e2e_tool_flow.py | ✅ |
| AC-TOOL-03 | 单元 | test_tool_router.py + test_e2e_tool_flow.py | ✅ |
| AC-TOOL-04 | 集成 | test_orchestrator.py + test_e2e_tool_flow.py | ✅ |
| AC-TOOL-05 | 单元 | test_e2e_tool_flow.py + test_mcp_adapter.py | ✅ |
| AC-TOOL-06 | 单元 | test_mcp_adapter.py (TestToolTransform) | ✅ |
| AC-TOOL-07 | 单元 | test_mcp_adapter.py (TestMCPHandler) | ✅ |
| AC-TOOL-08 | 单元 | test_tool_router.py (TestToolRouterShutdown) | ✅ |
| AC-TOOL-09 | 单元 | test_tool_router.py (test_duplicate_name_dedup) | ✅ |
| AC-TOOL-10 | 单元+集成 | test_tool_router.py + test_orchestrator.py | ✅ |
| AC-TOOL-11 | 单元 | test_system_tool_provider.py (TestCustomTools) | ✅ |
| AC-TOOL-12 | E2E | test_e2e_tool_flow.py (TestE2ELocalToolFlow) | ✅ |
| AC-TOOL-13 | E2E | test_e2e_tool_flow.py (TestE2EMultiProviderFlow) | ✅ |
| AC-TOOL-14 | 集成 | test_e2e_tool_flow.py (TestToolCallRecordIntegrity) | ✅ |
| AC-TOOL-15 | 集成 | test_e2e_tool_flow.py (TestContextAssemblerCallCountE2E) | ✅ |
| AC-TOOL-16 | 单元 | test_mcp_adapter.py (TestToolTransform + TestMCPHandler) | ✅ |
| AC-TOOL-17 | 单元 | test_mcp_adapter.py (TestMCPServerConfig) | ✅ |
| AC-TOOL-18 | 单元 | test_system_tool_provider.py (TestEdgeCases) | ✅ |
| AC-TOOL-19 | 单元 | test_tool_router.py (TestToolRouterQuery) | ✅ |
| AC-TOOL-20 | 集成 | test_orchestrator.py (TestPhaseEnd) | ✅ |
