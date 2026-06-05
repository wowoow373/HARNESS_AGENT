# MCPAdapter & MCPHandler

> **Interfaces**: [`MCPAdapter`](../../interfaces/mcp_adapter.py) | [`MCPHandler`](../../interfaces/mcp_handler.py) | **Required?**: No (not registered = MCP functionality cut) | **Lifecycle Phase**: Init (discovery) + Loop (execution) + End (shutdown)

## Interface Contracts

### MCPAdapter

消费外部 MCP Server，经声明式 + 程序化两级转换后暴露工具。与 SystemToolProvider 平级，通过 ToolRouter 合并。

```python
class MCPAdapter(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
    def shutdown(self) -> None: ...
```

### MCPHandler

当声明式转换不够用时，实现此 Protocol 进行程序化转换。

```python
class MCPHandler(Protocol):
    def transform_schema(self, name: str, schema: Dict[str, Any]) -> Dict[str, Any]: ...
    def transform_args(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]: ...
    def transform_result(self, name: str, result: Any) -> Any: ...
```

### Lifecycle

```
Session Init:
  MCPAdapter.get_tools()
    → Start MCPClients (subprocesses)
    → Discover tools from each server
    → Apply ToolTransform (declarative) + MCPHandler.transform_schema()
    → Register in ToolRouter name map

Loop (per tool call):
  MCPAdapter.execute(name, args)
    → Look up (original_name, MCPClient) from name map
    → Apply arg_defaults + arg_transform + MCPHandler.transform_args()
    → MCPClient.call_tool()
    → Apply result_transform + MCPHandler.transform_result()
    → Return ToolResult

Session End:
  MCPAdapter.shutdown()
    → Stop all MCPClient subprocesses
    → Clear name maps
```

---

## Transform Pipeline (2-Level)

```
                     Schema                  Args                  Result
                     ──────                  ────                  ──────
MCP Raw:      [original schema]        [LLM args]            [MCP result]
                   │                        │                      │
Level 1:      ToolTransform            arg_defaults           result_transform
(declarative) expose_as, hidden,       (inject defaults,      (callable)
              description_override     LLM values win)
                   │                        │                      │
Level 2:      MCPHandler               MCPHandler             MCPHandler
(programmatic) .transform_schema()     .transform_args()     .transform_result()
                   │                        │                      │
                   ▼                        ▼                      ▼
Final:         ToolDefinition          MCP args               ToolResult
```

### ToolTransform (Declarative)

覆盖 90% 的转换场景，纯配置无需代码：

```python
@dataclass
class ToolTransform:
    expose_as: Optional[str] = None          # Rename tool
    description_override: Optional[str] = None # Override description
    hidden: bool = False                     # Hide from LLM (still internally callable)
    arg_defaults: Dict[str, Any] = {}        # Inject default args
    arg_transform: Optional[Callable] = None  # Programmatic arg transform
    result_transform: Optional[Callable] = None # Programmatic result transform
```

---

## Default Implementation: DefaultMCPAdapter

管理一组 MCP Server 子进程连接，提供完整的转换管道。

### Usage

```python
from harness.components.mcp_manager.default_mcp_adapter import DefaultMCPAdapter
from harness.components.mcp_manager.mcp_client import MCPServerConfig
from harness.interfaces.types import ToolTransform

adapter = DefaultMCPAdapter(
    servers=[
        MCPServerConfig(
            name="fs",
            command="npx",
            args=["-y", "@anthropic/mcp-filesystem", "/tmp"],
        ),
        MCPServerConfig(
            name="github",
            command="npx",
            args=["-y", "@anthropic/mcp-github"],
        ),
    ],
    transforms={
        # Hide dangerous operations
        "filesystem_delete": ToolTransform(hidden=True),
        # Rename for clarity
        "filesystem_read": ToolTransform(expose_as="read_remote_file"),
        # Inject defaults
        "github_create_issue": ToolTransform(
            arg_defaults={"labels": ["auto-generated"]}
        ),
    },
)
```

### Constructor

```python
DefaultMCPAdapter(
    servers: Optional[List[MCPServerConfig]] = None,
    transforms: Optional[Dict[str, ToolTransform]] = None,
    handler: Optional[MCPHandler] = None,
)
```

| Param | Default | Description |
|-------|---------|-------------|
| `servers` | `[]` | MCP server configurations |
| `transforms` | `{}` | `{original_name → ToolTransform}` declarative transforms |
| `handler` | `None` | Optional MCPHandler for programmatic transforms |

### MCPServerConfig

```python
@dataclass
class MCPServerConfig:
    name: str                  # Server identifier
    command: str               # Executable (npx, python, etc.)
    args: List[str]            # Command arguments
    env: Dict[str, str] = {}   # Environment variables
```

### Register & Cut MCP

```python
# Register → MCP functionality enabled
container.register(MCPAdapter, DefaultMCPAdapter(
    servers=[MCPServerConfig(name="fs", command="npx", args=["-y", "@anthropic/mcp-filesystem", "/tmp"])],
))

# Don't register → MCP functionality completely cut
# (no MCP tools, no MCP subprocesses)
```

---

## Implement Your Own

### Custom MCPHandler (auth injection)

```python
class AuthInjector:
    """Injects authentication tokens into all MCP tool calls."""

    def transform_schema(self, name: str, schema: dict) -> dict:
        return schema  # No schema changes

    def transform_args(self, name: str, args: dict) -> dict:
        args["_auth_token"] = os.environ["API_TOKEN"]
        return args

    def transform_result(self, name: str, result) -> Any:
        # Redact sensitive data
        if isinstance(result, str) and "secret" in result.lower():
            return "[REDACTED]"
        return result
```

### Custom MCPAdapter (HTTP-based instead of stdio)

```python
class HTTPMCPAdapter:
    def __init__(self, server_url: str):
        self._url = server_url
        self._tools: Dict[str, dict] = {}

    def get_tools(self) -> list:
        resp = requests.get(f"{self._url}/tools")
        tools = []
        for t in resp.json():
            tools.append(ToolDefinition(
                name=t["name"],
                description=t["description"],
                parameters=t.get("inputSchema", {}),
            ))
            self._tools[t["name"]] = t
        return tools

    def execute(self, name: str, args: dict) -> ToolResult:
        try:
            resp = requests.post(f"{self._url}/tools/{name}", json=args)
            return ToolResult(success=True, content=resp.json())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def shutdown(self) -> None:
        pass  # HTTP is stateless
```

### Registration

```python
# Default MCP adapter
container.register(MCPAdapter, DefaultMCPAdapter(
    servers=[...],
    transforms={"dangerous_tool": ToolTransform(hidden=True)},
    handler=AuthInjector(),
))

# Custom MCP adapter
container.register(MCPAdapter, HTTPMCPAdapter("http://localhost:9000"))
```

