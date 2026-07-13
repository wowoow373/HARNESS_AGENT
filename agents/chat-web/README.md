# chat-web — ToC Web Chat Assistant

A consumer-facing web chat assistant built with the Harness Agent Template framework. This Agent showcases the **InputAdapter replacement** capability: swapping the default `CliAdapter` (stdin/stdout) for a `WebSocketAdapter` that enables real-time bidirectional communication in the browser.

## Screenshot

![Chat-Web Interface](screenshot.png)

*Web chat interface with colorful message bubbles, tool call spinners, and custom emoji rendering.*

## Quick Start

```bash
# From the harness_agent project root:
python agents/chat-web/server.py
```

Then open your browser and navigate to:

```
http://localhost:8000
```

## What Makes It Different

| Aspect | Default (coding-assistant) | chat-web |
|--------|---------------------------|----------|
| **Interaction** | Terminal stdin/stdout | Browser via WebSocket |
| **Visual Style** | Black & white CLI | Colorful chat bubbles |
| **Target Scene** | Code development | Daily chit-chat & info lookup |
| **Concurrency** | Single user, serial | Multi-user, each WebSocket gets its own Harness instance |

## Architecture

```
FastAPI Server
  ├── WebSocket endpoint (/ws)
  │     └── WebSocketAdapter (one per connection)
  │           ├── receive()  → blocks on ws.receive_text()
  │           └── send()     → pushes events to the frontend
  └── Static files (static/index.html)
```

**Event flow:**

```
User types ──► WebSocketAdapter.receive() ──► Orchestrator
                                                    │
LLM reply ◄── WebSocketAdapter.send(TextEvent) ◄────┘
                          │
                    Frontend JS parses JSON event
                          │
                    ├─ type: "text"        → message bubble
                    ├─ type: "tool_call"   → "Searching..." spinner
                    ├─ type: "tool_result" → result snippet
                    └─ type: "thinking"    → collapsible thought
```

## Component Replacement

The key replacement is the **InputAdapter**:

| Component | Default | chat-web Replacement |
|-----------|---------|----------------------|
| `InputAdapter` | `CliAdapter` | `WebSocketAdapter` |
| `GuideProvider` | Coding assistant guide | Friendly chat persona (`AGENTS.md`) |
| `SystemToolProvider` | read / write / shell | `web_search`, `weather` |
| `Hook` | — | `before_assemble` hook injects strict emoji constraints |

## Emoji System

Chat-web uses **custom emoji images** instead of Unicode emojis. The Agent is constrained to use only 5 predefined emoji IDs (`:laugh:`, `:cool:`, `:happy:`, `:cry:`, `:cute:`), which the frontend renders as expressive images. A `before_assemble` hook dynamically injects the strict emoji rules into every system prompt to prevent the LLM from inventing new emoji IDs or using Unicode emojis.

Each WebSocket connection spins up an independent `Harness` instance, so multiple users can chat simultaneously without interfering with one another.

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | Search the web for information |
| `weather` | Query weather conditions |

## Tests

Run the full test suite from the project root:

```bash
pytest agents/chat-web/tests/ -v
```

| Test Module | Coverage | Lines |
|-------------|----------|-------|
| `test_websocket_adapter.py` | Adapter protocol, serialization, session isolation | 515 |
| `test_tools.py` | Tool execution, edge cases, argument validation | 250 |
| `test_emojis.py` | Emoji constraint enforcement, rendering | 261 |
| `test_e2e.py` | End-to-end WebSocket flow, multi-user isolation | 417 |

## Tech Stack

- **Backend**: FastAPI + WebSocket
- **Frontend**: Vanilla HTML / JS (single file, ~200 lines, no framework)
- **Concurrency**: One `Harness` instance per WebSocket connection
- **Testing**: pytest (4 modules, 1444 lines total)

## Directory Structure

```
agents/chat-web/
├── AGENTS.md                 # Agent persona and behavior rules
├── README.md                 # This file
├── screenshot.png            # Web interface screenshot
├── server.py                 # FastAPI entry point + per-connection Harness lifecycle
├── adapter/
│   ├── __init__.py
│   └── websocket_adapter.py  # WebSocket InputAdapter implementation
├── static/
│   ├── index.html            # Chat frontend (vanilla JS, ~200 lines)
│   └── emojis/               # Custom emoji images (5 expressive emojis)
│       ├── manifest.json
│       ├── laugh.jpg
│       ├── cool.jpg
│       ├── happy.jpg
│       ├── cry.jpg
│       └── cute.gif
├── tools/
│   ├── __init__.py
│   ├── web_search.py         # Simulated web search tool
│   └── weather.py            # Simulated weather query tool
└── tests/                    # Test suite (4 test modules, 1444 lines)
    ├── __init__.py
    ├── test_e2e.py           # End-to-end WebSocket integration tests
    ├── test_emojis.py        # Emoji constraint & rendering tests
    ├── test_tools.py         # Tool execution tests
    └── test_websocket_adapter.py  # Adapter unit tests
```
