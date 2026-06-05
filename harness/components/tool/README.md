# Tool & SystemToolProvider

> **Interfaces**: [`Tool`](../../interfaces/tool.py) | [`SystemToolProvider`](../../interfaces/system_tool_provider.py) | **Required?**: No | **Lifecycle Phase**: Init (discovery) + Loop (execution)

## Interface Contracts

### Tool

单个工具的抽象——提供元信息描述和执行逻辑。

```python
class Tool(Protocol):
    def get_definition(self) -> ToolDefinition: ...
    def execute(self, args: Dict[str, Any]) -> ToolResult: ...
```

### SystemToolProvider

管理本地 Tool 集合的 Provider。与 MCPAdapter 平级，通过 ToolRouter 合并后暴露。

```python
class SystemToolProvider(Protocol):
    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...
```

### Key Types

```python
@dataclass
class ToolDefinition:
    name: str                    # Unique tool name
    description: str             # Human-readable description
    parameters: Dict[str, Any]   # JSON Schema for parameters

@dataclass
class ToolResult:
    success: bool                # Execution success
    content: Any                 # Result data (on success)
    error: Optional[str]         # Error message (on failure)
```

### Lifecycle

```
Session Init:
  ToolRouter queries SystemToolProvider.get_tools() → List[ToolDefinition]
  ToolRouter queries MCPAdapter.get_tools() → List[ToolDefinition]
  → Merged into unified tool list, cached for session

Loop (inner, per tool call):
  1. adapter.send(ToolCallEvent)
  2. [Hook: before_tool_execute]
  3. ToolRouter.execute(name, args) → dispatch to SystemToolProvider or MCPAdapter
  4. [Hook: after_tool_execute]
  5. adapter.send(ToolResultEvent)
```

> **ToolRouter** is framework-internal (NOT in DI). It merges SystemToolProvider + MCPAdapter into a `{name → provider}` routing table.

---

## Default Implementations

### 1. BaseTool (Abstract Base Class)

简化 Tool 创建的抽象基类。

```python
from harness.components.tool.base import BaseTool
from harness.interfaces.types import ToolDefinition, ToolResult

class MyTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="my_tool",
            description="Does something useful",
            parameters={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "The input"}
                },
                "required": ["input"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, content=f"Got: {args['input']}")
```

### 2. @inline_tool Decorator

从普通函数创建 Tool 实例。

```python
from harness.components.tool.inline_tool import inline_tool

@inline_tool(
    name="greet",
    description="Greet a user by name",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)
def greet(args: dict) -> dict:
    return {"greeting": f"Hello, {args['name']}!"}
```

### 3. Built-in System Tools

| Tool | Description | Key Parameter |
|------|-------------|---------------|
| `read_file` | Read file contents | `file_path` |
| `write_file` | Write content to file | `file_path`, `content` |
| `shell` | Execute shell command | `command` |

### 4. DefaultSystemToolProvider

管理内置 + 自定义 Tool 集合。

```python
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider

# Default tools only (read_file, write_file, shell)
provider = DefaultSystemToolProvider()

# Add custom tools
provider = DefaultSystemToolProvider(extra_tools=[MyCustomTool()])

# Fully custom tool set (replace defaults)
provider = DefaultSystemToolProvider(
    tools=[MyTool1(), MyTool2()],
    use_builtins=False,
)
```

#### Constructor

```python
DefaultSystemToolProvider(
    tools: Optional[List[BaseTool]] = None,
    extra_tools: Optional[List[BaseTool]] = None,
    use_builtins: bool = True,
)
```

| Param | Default | Description |
|-------|---------|-------------|
| `tools` | `None` | Complete tool list (when `use_builtins=False`) |
| `extra_tools` | `None` | Additional tools appended after builtins |
| `use_builtins` | `True` | Whether to include `read_file`, `write_file`, `shell` |

---

## Implement Your Own

### Custom SystemToolProvider

```python
class RestrictedToolProvider:
    """A tool provider that only allows read-only operations."""

    def get_tools(self) -> list:
        return [
            ToolDefinition(
                name="read_file",
                description="Read a file",
                parameters={"type": "object", "properties": {
                    "file_path": {"type": "string"}
                }},
            ),
            ToolDefinition(
                name="search_code",
                description="Search codebase with grep",
                parameters={"type": "object", "properties": {
                    "pattern": {"type": "string"}
                }},
            ),
        ]

    def execute(self, name: str, args: dict) -> ToolResult:
        if name == "read_file":
            content = Path(args["file_path"]).read_text()
            return ToolResult(success=True, content=content)
        elif name == "search_code":
            result = subprocess.run(["grep", "-r", args["pattern"], "."], capture_output=True, text=True)
            return ToolResult(success=True, content=result.stdout)
        return ToolResult(success=False, error=f"Unknown tool: {name}")
```

### Single Custom Tool

```python
class WebSearchTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        try:
            results = some_search_api(args["query"])
            return ToolResult(success=True, content=results)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

### Registration

```python
# Default tools
container.register(SystemToolProvider, DefaultSystemToolProvider())

# Default + custom
container.register(SystemToolProvider, DefaultSystemToolProvider(
    extra_tools=[WebSearchTool()]
))

# Fully custom
container.register(SystemToolProvider, RestrictedToolProvider())
```

> **SystemToolProvider is optional.** If not registered, ToolRouter has no system tools (MCP tools may still be available).

---

## Deep Harness Usage

复杂 Tool 内部可以用 `DIContainer` 装配一个完整的子 Harness——给它独立的 tools、guides、sensor——来实现多步骤智能操作：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, ContextAssembler, SystemToolProvider, Sensor

class CodeReviewTool(BaseTool):
    """A tool that launches a full sub-harness for multi-step code review."""

    def __init__(self, sub_llm):
        self._sub_llm = sub_llm

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="code_review",
            description="Multi-step code review: reads file, analyzes, writes report",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        file_path = args["file_path"]
        code = Path(file_path).read_text()

        # 装配子 Harness：有独立 tools（读文件、跑测试）和 sensor（写 review 报告）
        sub_container = DIContainer()
        sub_container.register(InputAdapter, OneShotAdapter(
            f"Review this code and write a report:\n\n```python\n{code}\n```"
        ))
        sub_container.register(ContextAssembler, ReviewAssembler(file_path))
        sub_container.register(SystemToolProvider, DefaultSystemToolProvider())
        sub_container.register(Sensor, ReviewReportSensor(memory=self._report_memory))

        sub_harness = Harness.from_container(sub_container, call_llm=self._sub_llm)
        sub_harness.run()

        return ToolResult(success=True, content=sub_harness._final_output)
```

这让 Tool 能力的上限从"单个 API 调用"提升到"任意复杂的多步 Agent 流程"。
