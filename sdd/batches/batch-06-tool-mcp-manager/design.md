# batch-06: Tool & MCP Manager — 架构设计

> 版本: 2.0（重设计）
> 前身: batch-06 初版已回滚（commit 3ad9120），本文档为重构后的正式设计。
> 问题分析详见 [known-issues.md](known-issues.md)。

---

## 1. 设计目标

将"工具"和"MCP"拆分为两个平级的 DI 插件，由框架内部 `ToolRouter` 合并路由：

1. **来源隔离**：系统工具（`SystemToolProvider`）和 MCP 工具（`MCPAdapter`）各自独立管理
2. **转换能力**：MCP 层具备 schema / args / result 三阶段声明式+程序化两级转换
3. **运行时裁切**：不注册 `MCPAdapter` 即裁切，与框架其他可选组件模式一致
4. **统一生命周期**：`ToolRouter.shutdown()` 统一清理各 Provider 资源

---

## 2. 架构图

```
                         ┌─────────────────────────────────┐
                         │         Core Orchestrator        │
                         │                                  │
                         │  ToolRouter (framework-internal) │
                         │  ┌─────────────────────────────┐ │
                         │  │ name → provider 路由表        │ │
                         │  │ "read_file"  → system        │ │
                         │  │ "fs_search"  → mcp           │ │
                         │  │ "greet"      → mcp           │ │
                         │  └─────────────────────────────┘ │
                         │  list_tools()  → 合并列表         │
                         │  execute()     → 查表分发         │
                         │  shutdown()    → 分发清理         │
                         └──────┬──────────────┬───────────┘
                                │              │
               ┌────────────────▼──┐  ┌────────▼──────────────┐
               │SystemToolProvider │  │ MCPAdapter             │
               │(DI 注册)           │  │ (DI 注册，不注册即裁切)   │
               │                   │  │                        │
               │ get_tools()       │  │ get_tools()            │
               │ execute()         │  │ execute()              │
               │                   │  │ shutdown()             │
               │ [ReadFileTool]    │  │                        │
               │ [WriteFileTool]   │  │ ┌────────────────────┐ │
               │ [ShellTool]       │  │ │ MCPClient          │─┤→ 外部 MCP Server
               │ (内置，自动注入)    │  │ │ (JSON-RPC stdio)   │ │
               └───────────────────┘  │ └────────────────────┘ │
                                      │ ┌────────────────────┐ │
                                      │ │ Transform Pipeline  │ │
                                      │ │ 1. ToolTransform    │ │
                                      │ │    声明式转换        │ │
                                      │ │ 2. MCPHandler       │ │
                                      │ │    程序化转换(可选)   │ │
                                      │ └────────────────────┘ │
                                      └────────────────────────┘
```

---

## 3. 接口设计

### 3.1 SystemToolProvider（DI 插件）

```python
@runtime_checkable
class SystemToolProvider(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
```

- 管理本地实现的 Tool 集合
- 无 `shutdown()`（本地工具无需资源清理）
- 默认实现: `DefaultSystemToolProvider`，内置 `read_file` / `write_file` / `shell`

### 3.2 MCPAdapter（DI 插件，可选）

```python
@runtime_checkable
class MCPAdapter(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

- 消费外部 MCP Server，经转换后暴露工具
- 有 `shutdown()`（需要关闭 MCP Server 子进程）
- 默认实现: `DefaultMCPAdapter`，内置声明式+程序化两级转换

### 3.3 MCPHandler（程序化转换，注入到 DefaultMCPAdapter）

```python
@runtime_checkable
class MCPHandler(Protocol):
    def transform_schema(self, name: str, schema: Dict) -> Dict: ...
    def transform_args(self, name: str, args: Dict) -> Dict: ...
    def transform_result(self, name: str, result: Any) -> Any: ...
```

### 3.4 ToolRouter（框架内部，非 DI）

```python
class ToolRouter:
    def register_provider(self, provider: ToolProvider) -> None: ...
    def list_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

- `_routes: Dict[str, ToolProvider]` — 工具名 → Provider 的映射
- 同名工具后者覆盖前者（WARNING 日志）
- `shutdown()` 仅对有 `shutdown()` 方法的 Provider 调用

### 3.5 ToolTransform（声明式转换声明）

```python
@dataclass
class ToolTransform:
    expose_as: Optional[str] = None          # 重命名暴露
    description_override: Optional[str] = None
    hidden: bool = False                      # LLM 不可见
    arg_defaults: Dict[str, Any] = field(default_factory=dict)
    arg_transform: Optional[Callable] = None  # 高级：程序化参数转换
    result_transform: Optional[Callable] = None  # 高级：程序化结果转换
```

---

## 4. 编排器集成

### _phase_init()

```
1. resolve(InputAdapter) → receive() → UserRequest
2. resolve_optional(GuideProvider) → get_guides() → GuidesBundle（缓存）
3. resolve_optional(MemoryBackend) → search() → List[MemoryItem]
4. ToolRouter（框架内部，非 DI）
   a. resolve_optional(SystemToolProvider) → tool_router.register_provider()
   b. resolve_optional(MCPAdapter) → tool_router.register_provider()
   c. tool_router.list_tools() → List[ToolDefinition]（缓存）
5. 构建 AssemblyContext
```

### _phase_loop()

```
外层循环（每轮用户输入）:
  ContextAssembler.assemble(ctx) → messages
  内层循环（tool_use 连续生成）:
    call_llm(messages, tools) → Response
    如有 tool_uses:
      for each tool_call:
        tool_router.execute(tool_name, args) → ToolResult
        追加 tool_result_message 到 messages
      如 Response.text 存在 → 发送 + 跳出内层
      否则 → 继续内层
    如纯 text → 发送 + 跳出内层
  adapter.receive() → 更新 ctx
```

### _phase_end()

```
Sensor.sense(trajectory) → tool_router.shutdown() → 清理内部状态
```

---

## 5. 用户装配代码

```python
container = DIContainer()

# 系统工具 — 内置工具自动注入
container.register(SystemToolProvider, DefaultSystemToolProvider())

# MCP 适配层（不注册即裁切）
container.register(MCPAdapter, DefaultMCPAdapter(
    servers=[
        MCPServerConfig(name="fs", command="npx",
                        args=["-y", "@anthropic/mcp-filesystem", "/tmp"])
    ],
    transforms={
        "filesystem_delete": ToolTransform(hidden=True),
        "filesystem_read": ToolTransform(expose_as="read_remote_file"),
    }
))

harness = Harness.from_container(container, call_llm=my_llm)
harness.run()
```

---

## 6. 组件树

```
harness/interfaces/
├── system_tool_provider.py   # SystemToolProvider Protocol (NEW)
├── mcp_adapter.py            # MCPAdapter Protocol (NEW)
├── mcp_handler.py            # MCPHandler Protocol (NEW)
├── tool.py                   # Tool Protocol (UNCHANGED)
├── types.py                  # + ToolTransform (MODIFIED)

harness/core/
├── tool_router.py            # ToolRouter (NEW, framework-internal)
├── orchestrator.py           # 适配 ToolRouter (MODIFIED)

harness/components/tool/
├── __init__.py               # 导出 BaseTool, inline_tool, DefaultSystemToolProvider (NEW)
├── base.py                   # BaseTool ABC (NEW)
├── inline_tool.py            # @inline_tool 装饰器 (MOVED from mcp_manager)
├── system_tools.py           # ReadFileTool, WriteFileTool, ShellTool (NEW)
├── default_system_tool_provider.py  # DefaultSystemToolProvider (NEW)

harness/components/mcp_manager/
├── __init__.py               # 导出 DefaultMCPAdapter, MCPClient (NEW)
├── mcp_client.py             # MCPClient + MCPServerConfig (NEW)
├── default_mcp_adapter.py    # DefaultMCPAdapter (NEW, replaces old default_mcp_manager.py)
```

---

## 7. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | SystemToolProvider 和 MCPAdapter 是两个独立 Protocol | DI 容器一个 type 一个实例；MCPAdapter 多 shutdown() |
| 2 | 声明式 ToolTransform + MCPHandler 拍底 | 改名/隐藏/默认参数覆盖 90%；@inline_tool 已用声明式 |
| 3 | 保留 Tool Protocol | 是单个工具契约，ToolProvider 是集合管理者，两层不冲突 |
| 4 | 运行时裁切 | 遵循现有 _resolve_optional() 模式 |
| 5 | ToolRouter 非 DI，编排器直接创建 | 框架内部行为，用户不应替换 |
