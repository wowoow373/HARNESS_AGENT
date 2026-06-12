"""Group Chat Web Server — 多人实时群聊前端 + WebSocket 桥接。

架构:
    Browser (WebSocket) ←→ FastAPI Server ←→ Kernel.MessageBus
                                                 ↕
                                           Agent1, Agent2, ...

用户消息流:
    用户输入 → WebSocket → server.py → kernel.message_bus.publish("user", ...)
    → MessageBus → 各 Agent 的 input_queue → FlexibleGroupChatInputAdapter

Agent 回复流:
    Agent → AtomicOutputAdapter → KBA → MessageBus.publish(agent_pid, TextEvent)
    → active_subscribers + SystemConsole (终端显示)
    → server.py 监听 SystemConsole AgentOutput 事件 → WebSocket 推送给前端

启动方式:
    python agents/group-chat/server.py
    # 打开浏览器 http://localhost:8000

依赖:
    pip install fastapi uvicorn
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from harness.runtime.kernel import Kernel
from harness.runtime.types import (
    AgentFinished,
    AgentOutput,
    AgentSpawned,
    SystemMessage,
)
from harness.interfaces.types import TextEvent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("group-chat-server")

# ---------------------------------------------------------------------------
# Web event types (sent to frontend)
# ---------------------------------------------------------------------------


def _make_event(event_type: str, **kwargs) -> dict:
    """Build a JSON-serializable event dict for the frontend."""
    return {"type": event_type, **kwargs}


# ---------------------------------------------------------------------------
# WebConsole — SystemConsole that never reads stdin
# ---------------------------------------------------------------------------


class _WebConsole:
    """SystemConsole for web-only mode.

    Never reads stdin — all user input comes via WebSocket → MessageBus.
    AgentOutput is intercepted by _run_group_chat to broadcast to web clients.
    """

    async def send(self, event) -> None:
        pass  # Handled by console_send_with_web wrapper

    async def receive(self):
        # Never return — user input comes via WebSocket
        import asyncio
        while True:
            await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# Group Chat Web Server
# ---------------------------------------------------------------------------


class GroupChatWebServer:
    """Manages the web frontend + WebSocket connections for group chat.

    Integrates with Kernel.MessageBus to bridge user messages in
    and agent messages out.

    Usage::

        server = GroupChatWebServer(kernel, port=8000)
        await server.start()
        # ... agents run ...
        await server.stop()
    """

    def __init__(self, kernel: Kernel, port: int = 8000):
        """Initialize the web server.

        Args:
            kernel: The Kernel instance with spawned agents.
            port: HTTP port to listen on.
        """
        self._kernel = kernel
        self._port = port

        # Connected WebSocket clients
        self._clients: Set[WebSocket] = set()

        # FastAPI app
        self._app = FastAPI(
            title="Harness Group Chat",
            description="Multi-agent real-time group chat",
            version="0.1.0",
        )
        self._setup_routes()
        self._setup_static()

        # uvicorn server
        self._uvicorn_server = None

    # ------------------------------------------------------------------
    # Route setup
    # ------------------------------------------------------------------

    def _setup_routes(self):
        """Register FastAPI routes."""

        @self._app.get("/")
        async def root():
            """Serve the group chat frontend."""
            index_path = (
                Path(__file__).parent / "static" / "index.html"
            )
            if index_path.exists():
                return HTMLResponse(
                    content=index_path.read_text(encoding="utf-8"),
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    },
                )
            return HTMLResponse(
                content="<h1>Group Chat</h1><p>Frontend not found.</p>"
            )

        @self._app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """Handle a single user's WebSocket connection.

            This is the user's bidirectional channel:
            - Incoming: user messages → publish to MessageBus as "user"
            - Outgoing: agent messages → push to browser
            """
            await websocket.accept()
            self._clients.add(websocket)
            logger.info(
                f"WebSocket client connected (total: {len(self._clients)})"
            )

            try:
                # Send current agent list to the new client
                agent_info = self._kernel.list_agents()
                await websocket.send_json(
                    _make_event(
                        "agents_list",
                        agents=[
                            {
                                "pid": pid,
                                "display_name": pid,
                                "state": info["state"],
                            }
                            for pid, info in agent_info.items()
                        ],
                    )
                )

                # Main receive loop
                while True:
                    try:
                        data = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=0.5,
                        )
                    except asyncio.TimeoutError:
                        continue

                    # Handle different message types from frontend
                    msg_type = data.get("type", "message")

                    if msg_type == "message":
                        user_text = data.get("text", "").strip()
                        if not user_text:
                            continue

                        # Publish user message to MessageBus.
                        # The console_send_with_web hook will broadcast it
                        # to all web clients via AgentOutput → broadcast_agent_output.
                        await self._kernel.message_bus.publish(
                            from_pid="user",
                            event=TextEvent(content=user_text),
                        )

                    elif msg_type == "command":
                        command = data.get("command", "").strip()
                        if command == "/end":
                            await self._broadcast_to_web(
                                _make_event(
                                    "system",
                                    message="结束群聊...",
                                )
                            )
                            self._kernel.end_workflow(
                                self._get_active_workflow_flag()
                            )
                            break
                        elif command == "/agents":
                            agent_info = self._kernel.list_agents()
                            await websocket.send_json(
                                _make_event(
                                    "agents_list",
                                    agents=[
                                        {
                                            "pid": pid,
                                            "display_name": pid,
                                            "state": info["state"],
                                        }
                                        for pid, info in agent_info.items()
                                    ],
                                )
                            )

            except WebSocketDisconnect:
                logger.info("WebSocket client disconnected")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                self._clients.discard(websocket)

    def _setup_static(self):
        """Mount static file serving."""
        static_path = Path(__file__).parent / "static"
        if static_path.exists():
            self._app.mount(
                "/static",
                StaticFiles(directory=str(static_path)),
                name="static",
            )

    # ------------------------------------------------------------------
    # Message broadcasting
    # ------------------------------------------------------------------

    async def _broadcast_to_web(self, event: dict) -> None:
        """Send an event to all connected web clients."""
        disconnected = set()
        for ws in self._clients:
            try:
                await ws.send_json(event)
            except Exception:
                disconnected.add(ws)

        for ws in disconnected:
            self._clients.discard(ws)

    async def broadcast_agent_output(
        self, pid: str, content: str, timestamp: Optional[float] = None
    ) -> None:
        """Broadcast an agent's TextEvent to all web clients.

        Called by the agent output listener.
        """
        if timestamp is None:
            timestamp = time.time()

        # Map virtual publishers to human-readable names
        if pid == "user":
            display_name = "我"
        else:
            display_name = pid

        await self._broadcast_to_web(
            _make_event(
                "text",
                from_pid=pid,
                from_name=display_name,
                content=content,
                timestamp=timestamp,
            )
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the web server (non-blocking).

        Integrates uvicorn into the current asyncio event loop.
        """
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host="0.0.0.0",
            port=self._port,
            log_level="info",
        )
        self._uvicorn_server = uvicorn.Server(config)

        # Start uvicorn in the background
        asyncio.create_task(self._uvicorn_server.serve())

        logger.info(
            f"Group Chat Web Server starting on http://localhost:{self._port}"
        )

    async def stop(self) -> None:
        """Stop the web server."""
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
            logger.info("Group Chat Web Server stopping")

    def _get_active_workflow_flag(self) -> Optional[str]:
        """Get the first active workflow flag."""
        for flag in self._kernel.workflow_table:
            return flag
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════


async def _run_group_chat(script_path: str, port: int = 8000) -> None:
    """Run the complete group chat system.

    1. Load workflow script → spawn agents
    2. Start web server
    3. Listen for agent outputs → broadcast to web clients
    4. Handle system input (for terminal /commands)
    """
    from harness.runtime.cli_console import CliConsole
    from harness.runtime.types import RuntimeStarted, RuntimeStopped

    # ── 1. Create Kernel ──
    # If stdin is a TTY → use CliConsole for /commands support.
    # Otherwise (web-only, piped, etc.) → WebConsole that never reads stdin.
    if sys.stdin.isatty():
        console = CliConsole(mode="mode_b")
    else:
        console = _WebConsole()
    kernel = Kernel(console)

    # ── 2. Spawn agents from workflow script ──
    result = kernel.spawn_from_script(script_path, parent=None)
    logger.info(
        f"Spawned {len(result['agents'])} agent(s) "
        f"for workflow '{result['workflow_flag']}'"
    )

    # ── 3. Start web server ──
    web_server = GroupChatWebServer(kernel, port=port)
    await web_server.start()

    # ── 4. Agent output listener ──
    # Intercept agent TextEvents and broadcast to web clients.
    # We hook into the SystemConsole — when an AgentOutput event arrives,
    # we forward it to the web frontend (in addition to terminal display).

    original_send = console.send

    async def console_send_with_web(event):
        """Intercept SystemConsole.send to also broadcast to web."""
        if isinstance(event, AgentOutput):
            # Skip non-text events (thinking/tool traces) — only TextEvents
            # reach the web frontend. Non-text events are degraded to
            # AgentOutput with "[ThinkingEvent]" / "[ToolCallEvent]" prefix.
            content = event.content or ""
            if not content.startswith("[ThinkingEvent]") and \
               not content.startswith("[ToolCallEvent]") and \
               not content.startswith("[ToolResultEvent]"):
                await web_server.broadcast_agent_output(
                    pid=event.pid, content=event.content
                )
        elif isinstance(event, AgentSpawned):
            await web_server._broadcast_to_web(
                _make_event(
                    "agent_joined",
                    pid=event.pid,
                )
            )
        elif isinstance(event, AgentFinished):
            await web_server._broadcast_to_web(
                _make_event(
                    "agent_left",
                    pid=event.pid,
                )
            )
        await original_send(event)

    console.send = console_send_with_web

    # ── 5. Launch agent tasks ──
    agent_tasks = list(kernel._tasks.values())
    task_sys = asyncio.create_task(kernel._handle_system_input())

    # ── 6. Push startup event ──
    await console.send(RuntimeStarted())

    print()
    print("=" * 50)
    print("  🎉 群聊已启动！")
    print(f"  浏览器打开: http://localhost:{port}")
    print(f"  参与成员: {', '.join(result['agents'][0]['pid'] for _ in [0])}")
    print(f"  Agents: {[a['pid'] for a in result['agents']]}")
    print("  输入 /end 结束群聊")
    print("=" * 50)
    print()

    # ── 7. Wait for agents or system exit ──
    try:
        await asyncio.gather(
            *agent_tasks,
            return_exceptions=True,
        )
    finally:
        # Signal system input handler to exit
        kernel._shutdown = True
        try:
            await task_sys
        except asyncio.CancelledError:
            pass

        await console.send(RuntimeStopped())
        await web_server.stop()


def main():
    """Synchronous entry point for the group chat server."""
    script_path = str(Path(__file__).parent / "group_chat_demo.py")

    print("=" * 50)
    print("  Harness Group Chat Server")
    print("=" * 50)
    print()
    print(f"  Workflow script: {script_path}")
    print()

    try:
        asyncio.run(_run_group_chat(script_path, port=8000))
    except KeyboardInterrupt:
        print("\n群聊已终止。")


if __name__ == "__main__":
    main()
