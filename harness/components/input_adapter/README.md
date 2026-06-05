# InputAdapter

> **Interface**: [`InputAdapter`](../../interfaces/input_adapter.py) | **Required?**: **YES** | **Lifecycle Phase**: Init + Loop

## Interface Contract

`InputAdapter` 是框架与外部世界的**唯一通道**。它接收用户输入并转为标准化请求，将编排器产生的事件流以前后台分离的方式呈现给用户。

```python
class InputAdapter(Protocol):
    def receive(self) -> UserRequest: ...
    def send(self, event: AdapterEvent) -> None: ...
```

### Methods

| Method | When Called | Returns/Receives |
|--------|-------------|------------------|
| `receive()` | Session init (once) + end of each outer loop turn | → `UserRequest` |
| `send(event)` | Inner loop: after each LLM field is produced | ← `AdapterEvent` (5 event types) |

### Exit Signals

The orchestrator treats these as "user wants to exit":
- `UserRequest.text` is `""` or whitespace-only
- `UserRequest.text` matches `"/exit"`
- `UserRequest.metadata` contains `{"exit": True}`

### Event Types (batch-11, event-driven)

`AdapterEvent = Union[ThinkingEvent, ToolCallEvent, ToolResultEvent, TextEvent, StopEvent]`

| Event | LLM Response Field | Frontend Channel | Typical Target |
|-------|-------------------|------------------|----------------|
| `ThinkingEvent` | `response.thinking` | Background (debug) | stderr |
| `ToolCallEvent` | `response.tool_uses[i]` | Background | stderr |
| `ToolResultEvent` | After tool execution | Background | stderr |
| `TextEvent` | `response.text` | **Foreground** | **stdout** |
| `StopEvent` | `response.stop_reason` | Session control | no-op |

> **Design intent**: The orchestrator no longer decides how to render. It only "produces facts" as event objects. The frontend (adapter) decides rendering per event type — **foreground/background separation**.

### Lifecycle

```
Session Init:
  InputAdapter.receive() → UserRequest (first input)

Outer loop (every turn end):
  ...inner loop (LLM produces events → adapter.send())...
  InputAdapter.receive() → UserRequest (next input, or exit)

Inner loop (per LLM response field):
  thinking → adapter.send(ThinkingEvent)
  tool_use → adapter.send(ToolCallEvent) → execute → adapter.send(ToolResultEvent)
  text → adapter.send(TextEvent)
  stop → adapter.send(StopEvent)
```

---

## Default Implementation: CliAdapter

命令行 I/O 适配器：从 **stdin** 读取输入，将事件分发到 **stdout**（前台对话）或 **stderr**（后台状态）。

### Usage

```python
from harness.components.input_adapter.cli_adapter import CliAdapter

adapter = CliAdapter()
adapter.prompt = "query> "
adapter.debug = True                # show thinking events

request = adapter.receive()         # blocks on stdin
adapter.send(event)                 # dispatches to stdout/stderr
```

### Constructor

```python
CliAdapter(session_id: Optional[str] = None, debug: bool = False)
```

| Param | Default | Description |
|-------|---------|-------------|
| `session_id` | Auto-generated (`cli-<timestamp>`) | Session identifier |
| `debug` | `False` | When True, ThinkingEvent printed to stderr |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `prompt` | `str` | Input prompt (default `"> "`) |
| `session_id` | `str` | (read-only) Session identifier |
| `debug` | `bool` | Toggle debug output |

### Event Routing

| Event | Channel | Format |
|-------|---------|--------|
| `TextEvent` | **stdout** | Raw content (foreground reply) |
| `ThinkingEvent` | **stderr** (debug only) | `[thinking] {content}` |
| `ToolCallEvent` | **stderr** | `🔧 {tool_name}({args_summary})` |
| `ToolResultEvent` | **stderr** | `🔧 {tool_name} → OK ({duration}ms)` or `→ ERROR ({duration}ms): {error}` |
| `StopEvent` | — | no-op |

---

## Implement Your Own

### WebSocket adapter

```python
import json
import asyncio

class WebSocketAdapter:
    def __init__(self, websocket):
        self._ws = websocket
        self._session_id = "ws-001"

    async def receive(self) -> UserRequest:
        data = await self._ws.recv()
        payload = json.loads(data)
        return UserRequest(
            text=payload.get("text", ""),
            session_id=self._session_id,
        )

    async def send(self, event: AdapterEvent) -> None:
        if isinstance(event, TextEvent):
            await self._ws.send(json.dumps({"type": "text", "content": event.content}))
        elif isinstance(event, ToolCallEvent):
            await self._ws.send(json.dumps({
                "type": "tool_call",
                "tool_name": event.tool_name,
                "arguments": event.arguments,
            }))
        # ... etc
```

### TUI adapter (using Textual/rich)

```python
class TUIAdapter:
    def __init__(self, app):
        self._app = app
        self._input_queue = queue.Queue()

    def receive(self) -> UserRequest:
        text = self._input_queue.get()  # blocks until user submits
        return UserRequest(text=text)

    def send(self, event: AdapterEvent) -> None:
        if isinstance(event, TextEvent):
            self._app.add_message("assistant", event.content)
        elif isinstance(event, ToolCallEvent):
            self._app.show_spinner(f"Running {event.tool_name}...")
        elif isinstance(event, ToolResultEvent):
            self._app.hide_spinner()
```

### Registration

```python
container.register(InputAdapter, CliAdapter())
# or
container.register(InputAdapter, WebSocketAdapter(ws))
# or
container.register(InputAdapter, TUIAdapter(app))
```

> **InputAdapter is the ONLY required component.** `Harness.from_container()` throws if it's missing.

