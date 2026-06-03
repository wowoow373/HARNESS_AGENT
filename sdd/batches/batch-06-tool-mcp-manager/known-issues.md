# batch-06 架构重设计记录

> 本文档记录 batch-06 初版实现中暴露的架构问题，以及经探讨后确定的重设计方案。
> 初版实现已回滚（commit 3ad9120），本文档作为重设计的输入。

---

## 问题 1：ToolRegistry 与 MCP 工具混合

### 原实现

`DefaultMCPManager.load_tools()` 产出的 MCP 工具（`MCPToolProxy` + `InlineTool`），在编排器
`_phase_init()` 中通过 `tool_registry.register(tool)` 注册到了 `ToolRegistry` 中。这导致：

- `ToolRegistry._tools: Dict[str, Tool]` 同时存储了本地工具和 MCP 来源工具，无来源标记
- `_phase_end()` 中 MCP Server 的 shutdown 逻辑依赖 `MCPManager` 实例，与工具实际存储位置分离
- 无法按来源分组管理（如单独卸载 MCP 工具、按来源记录指标）

### 根源

`ToolRegistry` 是执行期的单一真相源，但 `MCPManager` 才是 MCP 生命周期的持有者。两者职责边界在注册那一刻发生了混淆。

---

## 问题 2：MCP 层缺乏转换层

### 原实现

从 MCP Server 发现工具到 LLM 调用到 MCP Server 执行，全部透传：

1. **Schema 透传**：`MCPToolProxy.get_definition()` 将 MCP Server 返回的原始 `name/description/inputSchema`
   直接映射为 `ToolDefinition`，无中间转换
2. **参数透传**：`MCPToolProxy.execute()` 将 LLM 生成的 `args` 原样传给
   `MCPClient.call_tool()`
3. **响应透传**：`MCPClient.call_tool()` 的结果直接包装为 `ToolResult.content`，无处理

这意味着无法做以下事情：
- 重命名工具（如 `filesystem_read` → `read_file`）
- 修改工具描述使其更适配 LLM
- 隐藏某些危险工具不让 LLM 看到
- 在参数中注入系统上下文（如 `cwd`、认证信息）
- 对 MCP 响应做脱敏和格式化

---

## 问题 3：ToolRegistry 职责与注释不一致

### 原实现

接口注释声明 `ToolRegistry` 是"框架内部组件，用户不通过 DI 替换"，但实际：

- 用户需要手动创建 `DefaultToolRegistry()` 实例
- 用户需要手动调用 `registry.register(ReadFileTool())` 注册系统工具
- 用户需要手动将 Registry 注册到 DI 容器
- 编排器通过 DI `resolve(ToolRegistry)` 获取实例

注释声明的"框架内部"与实际使用方式之间存在矛盾。

### 根源

将"工具注册表"和"工具来源管理"两个职责混在了一个组件里。注册表应该是框架内部的（Core 自己管），但工具的提供应该是用户可替换的（和其他所有模块一致的插件模式）。

---

## 重设计方案

### 核心思路

1. **Tool 与 MCP 平级**：系统工具和 MCP 工具是两个独立的 DI 插件，各自实现自己的 Protocol
2. **Core 内部合并**：框架在 Core 层引入 `ToolRouter` 合并两者，维护路由表
3. **MCP 适配层**：MCP Adapter 内部以 MCP 协议形态运作，包含 schema/args/result 三阶段转换
4. **统一裁切模型**：和其他模块一样，不注册就不加载

### 架构图

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
               │ [ShellTool]       │  │ │ MCP Consumer       │ │
               │ (内置，自动注入)    │  │ │ (MCPClient)        │─┤→ 外部 MCP Server
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

### 接口设计

#### 新增 Protocol

```python
# harness/interfaces/system_tool_provider.py
@runtime_checkable
class SystemToolProvider(Protocol):
    """系统工具提供者 — 管理本地实现的 Tool 集合。用户可替换。"""
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...

# harness/interfaces/mcp_adapter.py
@runtime_checkable
class MCPAdapter(Protocol):
    """MCP 适配层 — 消费外部 MCP Server，经转换后暴露工具。用户可替换、可裁切。"""
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...  # 关闭 MCP Server 子进程连接

# MCP 转换层：声明式 + 程序化两级
# harness/interfaces/mcp_handler.py
@runtime_checkable
class MCPHandler(Protocol):
    """MCP 程序化转换处理器 — 当 ToolTransform 声明式不够用时使用。"""
    def transform_schema(self, name: str, schema: Dict) -> Dict: ...
    def transform_args(self, name: str, args: Dict) -> Dict: ...
    def transform_result(self, name: str, result: Any) -> Any: ...
```

#### 新增框架内部组件

```python
# harness/core/tool_router.py — 框架内部，非 DI，用户不可替换
class ToolRouter:
    """合并多个 ToolProvider 的工具并统一分发。"""
    _routes: Dict[str, ToolProvider]  # tool_name → provider

    def register_provider(self, provider: ToolProvider) -> None: ...
    def list_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

#### 新增转换类型

```python
# harness/interfaces/types.py
@dataclass
class ToolTransform:
    """单个 MCP 工具的转换声明"""
    expose_as: Optional[str] = None          # 重命名暴露
    description_override: Optional[str] = None
    hidden: bool = False                      # LLM 不可见
    arg_defaults: Dict[str, Any] = field(default_factory=dict)
    arg_transform: Optional[Callable] = None  # 高级：程序化参数转换
    result_transform: Optional[Callable] = None  # 高级：程序化结果转换
```

#### 保留的接口

- `Tool` Protocol — 保留，单个工具的契约（`get_definition()` + `execute()`）
- `BaseTool` ABC — 保留，便利基类

#### 删除的接口

- `ToolRegistry` Protocol — 删除，职责被 `ToolRouter`（框架内部）+ `SystemToolProvider`/`MCPAdapter`（DI 插件）替代
- `MCPManager` Protocol — 删除，演化为 `MCPAdapter`

### 编排器变更

`_phase_init()`:
```python
# 4. ToolRouter (框架内部，非 DI)
self._tool_router = ToolRouter()

# 4a. 系统工具
sys_provider = self._resolve_optional(SystemToolProvider)
if sys_provider:
    self._tool_router.register_provider(sys_provider)

# 4b. MCP 适配层（可选 — 不注册即裁切）
mcp_adapter = self._resolve_optional(MCPAdapter)
if mcp_adapter:
    self._tool_router.register_provider(mcp_adapter)

available_tools = self._tool_router.list_tools()
```

`_phase_end()`:
```python
self._tool_router.shutdown()  # 统一清理，分发到各 Provider
```

`_phase_loop()`:
```python
result = self._tool_router.execute(tool_name, args)
```

### 用户装配代码

```python
container = DIContainer()

# 系统工具 — 内置工具自动注入，用户只需注册
container.register(SystemToolProvider, DefaultSystemToolProvider())

# MCP 适配层（不注册即可裁切） — 声明式转换
container.register(MCPAdapter, DefaultMCPAdapter(
    servers=[
        MCPServerConfig(name="fs", command="npx", args=["-y", "@anthropic/mcp-filesystem", "/tmp"])
    ],
    transforms={
        "filesystem_delete": ToolTransform(hidden=True),
        "filesystem_read": ToolTransform(expose_as="read_remote_file"),
    }
))

# 高级：带程序化转换处理器
# container.register(MCPAdapter, DefaultMCPAdapter(
#     servers=[...],
#     transforms={...},
#     handler=MyAuthInjector(),   # 实现 MCPHandler Protocol
# ))

harness = Harness.from_container(container, call_llm=my_llm)
harness.run()
```

### 用户自定义方式

**自定义系统工具** — 实现 `SystemToolProvider` Protocol，注册到 DI：

```python
class MyToolProvider:
    def get_tools(self) -> list[ToolDefinition]: ...
    def execute(self, name: str, args: dict) -> ToolResult: ...

container.register(SystemToolProvider, MyToolProvider())
```

**自定义 MCP 转换层** — 两条路：

```python
# 轻量：注入 MCPHandler 到 DefaultMCPAdapter
class MyAuthInjector:
    def transform_schema(self, name, schema): ...
    def transform_args(self, name, args): ...
    def transform_result(self, name, result): ...

container.register(MCPAdapter, DefaultMCPAdapter(
    servers=[...],
    transforms={...},
    handler=MyAuthInjector(),
))

# 完全替换：实现 MCPAdapter Protocol
class MyMCPAdapter:
    def get_tools(self) -> list[ToolDefinition]: ...
    def execute(self, name, args) -> ToolResult: ...
    def shutdown(self) -> None: ...

container.register(MCPAdapter, MyMCPAdapter())
```

`DefaultSystemToolProvider` 默认内置 `ReadFileTool`、`WriteFileTool`、`ShellTool`，用户无需显式指定。`BaseTool`（继承）和 `@inline_tool`（装饰器）位于 `harness/components/tool/` 下，是给自定义 ToolProvider 实现者使用的辅助工具。

---

## 设计决策记录

### 决策 1：两个独立 Protocol 而非一个

`SystemToolProvider` 和 `MCPAdapter` 是两个独立 Protocol，不共用一个。

**理由**：
- DI 容器一个 type 一个实例，共用会导致无法同时注册两个来源
- `MCPAdapter` 有 `shutdown()` 而 `SystemToolProvider` 没有，语义不同
- 独立 Protocol 符合"平级"的设计意图，各自可独立替换

### 决策 2：声明式 ToolTransform + MCPHandler 拍底

**理由**：
- 改名、隐藏、注入默认参数覆盖 90% 场景，声明式最简洁
- `@inline_tool` 已是声明式装饰器模式，保持一致性
- `MCPHandler` Protocol 给需要程序化转换的高级用户（统一认证、动态结果重组等）

### 决策 3：保留 Tool Protocol

**理由**：
- `Tool` 是单个工具的契约，`ToolProvider` 是工具集合的管理者
- 两层抽象各司其职，与现有设计不冲突
- `BaseTool` 和 `@inline_tool` 依赖 `Tool` Protocol

### 决策 4：运行时裁切

MCP 可裁切遵循项目现有的可选组件模式——不注册 `MCPAdapter` 到 DI 即裁切，
`_resolve_optional()` 返回 None 并记录 WARNING 日志。不引入安装时裁切。

### 决策 5：回滚而非修补

batch-06 初版（commit 3ad9120）已从 master 回滚。原因：
- 改动是架构性的，不是修 bug
- batch-06 是 master HEAD，后面无依赖提交
- 约 50% 代码可复用，回滚后重新组装比重构更干净

---

## 文件变更清单（相对于 batch-05 基线）

### 新增
- `harness/interfaces/system_tool_provider.py`
- `harness/interfaces/mcp_adapter.py`
- `harness/interfaces/mcp_handler.py`
- `harness/core/tool_router.py`
- `harness/components/tool/default_system_tool_provider.py`
- `harness/components/mcp_manager/default_mcp_adapter.py`（替代 default_mcp_manager.py）
- `harness/interfaces/types.py` — 新增 `ToolTransform` dataclass

### 修改
- `harness/interfaces/__init__.py` — 导出新 Protocol，移除 `ToolRegistry`/`MCPManager`
- `harness/core/orchestrator.py` — `_phase_init()` / `_phase_loop()` / `_phase_end()`
- `harness/components/mcp_manager/tool_proxy.py` — 增加 transform 支持
- `harness/components/tool/__init__.py` — 增加 `inline_tool` 导出

### 删除
- `harness/interfaces/tool_registry.py`
- `harness/interfaces/mcp_manager.py`
- `harness/components/tool_registry/`（整个目录）
- `harness/components/mcp_manager/default_mcp_manager.py`
- `sdd/batches/batch-06-tool-mcp-manager/known-issues.md`（本文档，完成后删除）

### 保留不变
- `harness/interfaces/tool.py`
- `harness/components/tool/base.py`
- `harness/components/tool/system_tools.py`
- `harness/components/mcp_manager/mcp_client.py`
- `harness/components/mcp_manager/inline_tool.py`（移至 `components/tool/`）
