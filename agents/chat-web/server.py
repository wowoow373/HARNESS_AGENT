"""Chat-Web Agent — FastAPI WebSocket Server.

Each WebSocket connection spawns an independent Harness Agent instance
with session-isolated memory. Demonstrates InputAdapter replacement:
CliAdapter → WebSocketAdapter.

Usage::

    python agents/chat-web/server.py
    # Open browser at http://localhost:8000

Components assembled per connection:
  - WebSocketAdapter      : WebSocket bidirectional I/O
  - MdMemory              : Session-isolated markdown memory
  - FileGuideProvider     : "agents/chat-web/AGENTS.md" persona
  - SimpleAssembler       : Sliding-window context assembly
  - LoggingSensor         : Trajectory logging to episodic memory
  - DefaultSystemToolProvider + extra : web_search, weather tools
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so harness imports work regardless of
# where this script is launched from.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# Harness framework
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
from harness.hooks.events import EVENT_BEFORE_ASSEMBLE

# Chat-web custom components
# Python package names cannot contain hyphens, so we add the chat-web
# directory to sys.path and import directly.
_chat_web_path = str(Path(__file__).parent)
if _chat_web_path not in sys.path:
    sys.path.insert(0, _chat_web_path)

from adapter.websocket_adapter import WebSocketAdapter
from tools import WebSearchTool, WeatherTool

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("chat-web-server")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Harness Chat-Web Agent",
    description="WebSocket-based chat agent powered by Harness framework",
    version="1.0.0",
)

# Serve static files (frontend)
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def root() -> HTMLResponse:
    """Serve the chat frontend."""
    index_path = static_path / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Harness Chat-Web Agent</h1><p>Frontend not found.</p>")


# ---------------------------------------------------------------------------
# Per-connection Harness lifecycle
# ---------------------------------------------------------------------------

def _run_harness(
    adapter: WebSocketAdapter,
    llm: MinimalLLMAdapter,
    memory_path: str,
) -> None:
    """Run a Harness instance in a background thread.

    This function is synchronous — it blocks until the session ends.
    """
    container = DIContainer()

    # 1. InputAdapter — WebSocket bridge
    container.register(InputAdapter, adapter)

    # 2. MemoryBackend — session-isolated storage
    memory = MdMemory(path=memory_path)
    container.register(MemoryBackend, memory)

    # 3. GuideProvider — chat assistant persona
    guide_path = Path(__file__).parent / "AGENTS.md"
    if guide_path.exists():
        container.register(
            GuideProvider,
            FileGuideProvider(paths=[str(guide_path)]),
        )

    # 4. ContextAssembler — sliding window with memory injection
    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=50, memory=memory),
    )

    # 5. Sensor — trajectory logging
    container.register(Sensor, LoggingSensor(memory=memory))

    # 6. SystemToolProvider — builtins + consumer tools
    container.register(
        SystemToolProvider,
        DefaultSystemToolProvider(
            extra_tools=[WebSearchTool(), WeatherTool()],
        ),
    )

    # 7. Register before_assemble hook to inject strict emoji constraints
    #    directly into the system prompt on every turn.
    def inject_emoji_constraint(ctx):
        assembly_ctx = ctx.data  # ctx.data is AssemblyContext for before_assemble
        if assembly_ctx.guides:
            rules = (
                "\n\n## CRITICAL: EMOJI RULES\n"
                "You have EXACTLY 5 emoji IDs. ONLY use these — NEVER invent new ones:\n"
                "1. :laugh: — funny moments, jokes\n"
                "2. :cool: — impressive, awesome\n"
                "3. :happy: — good news, celebrations\n"
                "4. :cry: — sad, disappointing\n"
                "5. :cute: — comforting, gentle\n"
                "Place ONE emoji at the VERY END of your reply only. "
                "NEVER use Unicode emojis (😊 👍 🎉 etc.)."
            )
            assembly_ctx.guides.identity = (assembly_ctx.guides.identity or "") + rules

    harness = Harness.from_container(container, call_llm=llm)
    harness.register_hook(EVENT_BEFORE_ASSEMBLE, inject_emoji_constraint)
    harness.run()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Handle a single WebSocket connection = one Agent session.

    Architecture:
      1. Accept WebSocket connection.
      2. Create WebSocketAdapter + session-isolated memory.
      3. Start Harness in a background thread.
      4. Main coroutine loop bridges async WebSocket ↔ sync adapter queues.
      5. On disconnect: signal adapter to exit, join thread.
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # --- Create per-session components ---
    adapter = WebSocketAdapter()
    session_id = adapter.session_id
    memory_path = f"./agents/chat-web/memory/{session_id}"
    llm = MinimalLLMAdapter()

    # Ensure memory directory exists
    Path(memory_path).mkdir(parents=True, exist_ok=True)

    # --- Start Harness in background thread ---
    harness_thread = threading.Thread(
        target=_run_harness,
        args=(adapter, llm, memory_path),
        name=f"harness-{session_id}",
        daemon=False,
    )
    harness_thread.start()
    logger.info(f"Harness started in thread for session {session_id}")

    # --- Main bridge loop: WebSocket ↔ adapter queues ---
    try:
        while True:
            # ---- Drain ALL events from adapter outbox → WebSocket ----
            try:
                while True:
                    event = adapter.get_event(block=False)
                    await websocket.send_json(event)
            except queue.Empty:
                pass

            # ---- Receive from WebSocket → adapter inbox ----
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=0.05,
                )
                # Validate incoming message format to prevent exit signal
                if isinstance(data, dict) and "text" in data:
                    adapter.put_message(data)
                else:
                    logger.warning(f"Invalid message format from client: {data}")
            except asyncio.TimeoutError:
                # No message from client — continue loop
                pass
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # --- Cleanup ---
        logger.info(f"Closing session {session_id}")
        adapter.close()

        # Give the harness thread a chance to exit gracefully
        harness_thread.join(timeout=5.0)
        if harness_thread.is_alive():
            logger.warning(f"Harness thread for {session_id} did not exit in time")

        logger.info(f"Session {session_id} closed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("  Harness Chat-Web Agent Server")
    print("=" * 50)
    print()
    print("  Open your browser at: http://localhost:8000")
    print("  WebSocket endpoint:   ws://localhost:8000/ws")
    print()
    print("  Press Ctrl+C to stop")
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
