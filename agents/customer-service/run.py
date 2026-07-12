"""Customer Service Agent — single-process, single-loop entry point.

Usage:
    python agents/customer-service/run.py
    curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"text":"改签规则是什么？"}'
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_CS_PATH = str(Path(__file__).resolve().parent)
if _CS_PATH not in sys.path:
    sys.path.insert(0, _CS_PATH)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness.runtime.kernel import Kernel
from harness.runtime.cli_console import CliConsole
from harness.runtime.types import AgentOutput
from harness.interfaces.types import UserRequest


# ═══════════════════════════════════════════════════════════════════════════
# Capturing console
# ═══════════════════════════════════════════════════════════════════════════

class CapturingConsole:
    """Forwards to CliConsole + captures AgentOutput for /chat endpoint."""

    def __init__(self):
        self._cli = CliConsole(mode='mode_b')
        self._pending = {}  # pid → list of content (accessed from same event loop)

    def drain(self, pid: str) -> list[str]:
        msgs = list(self._pending.get(pid, []))
        self._pending[pid] = []
        return msgs

    def drain_all(self) -> dict[str, list[str]]:
        result = {pid: list(msgs) for pid, msgs in self._pending.items()}
        for pid in self._pending:
            self._pending[pid] = []
        return result

    async def send(self, event):
        if isinstance(event, AgentOutput):
            self._pending.setdefault(event.pid, []).append(event.content)
        await self._cli.send(event)

    async def receive(self):
        return await self._cli.receive()


# ═══════════════════════════════════════════════════════════════════════════
# Global state (same event loop → thread-safe)
# ═══════════════════════════════════════════════════════════════════════════

_kernel: Kernel | None = None
_console: CapturingConsole | None = None


# ═══════════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kernel + workflow on startup, clean up on shutdown."""
    global _kernel, _console

    console = CapturingConsole()
    kernel = Kernel(console)
    _kernel = kernel
    _console = console

    result = kernel.spawn_from_script(
        str(Path(__file__).parent / "customer_service_workflow.py")
    )
    print(f"[system] Workflow spawned: {result['workflow_flag']} "
          f"with {len(result['agents'])} agents")

    # Start agent tasks as background tasks (same event loop)
    for pid, task in kernel._tasks.items():
        asyncio.create_task(_monitor_agent(pid, task))

    yield  # Server is running

    # Shutdown: kill all agents
    for pid in list(kernel.runtime_table.keys()):
        kernel.kill(pid)


async def _monitor_agent(pid: str, task: asyncio.Task):
    """Wait for agent task to complete, log errors."""
    try:
        await task
    except Exception as e:
        print(f"[{pid}] agent error: {e}")


app = FastAPI(title="Customer Service Agent", version="0.1.0", lifespan=lifespan)
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


class ChatRequest(BaseModel):
    text: str


@app.get("/")
async def root():
    index_path = static_path / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Customer Service Agent</h1><p>POST /chat to query</p>")


@app.post("/chat")
async def chat(req: ChatRequest):
    if _kernel is None or _console is None:
        return {"error": "System not initialized"}

    # Clear previous captures
    for pid in _kernel.runtime_table:
        _console.drain(pid)

    # Send message to router (same event loop → thread-safe)
    _kernel.send_input("router", UserRequest(text=req.text))

    # Collect outputs with timeout
    all_messages = []
    deadline = time.time() + 60
    idle_deadline = time.time() + 8
    last_count = 0

    while time.time() < deadline:
        drained = _console.drain_all()
        for pid, msgs in drained.items():
            for m in msgs:
                all_messages.append({"pid": pid, "content": m})

        current_count = len(all_messages)
        if current_count > last_count:
            idle_deadline = time.time() + 8
            last_count = current_count
        elif time.time() > idle_deadline and current_count > 0:
            break

        await asyncio.sleep(0.2)

    # Final drain
    drained = _console.drain_all()
    for pid, msgs in drained.items():
        for m in msgs:
            all_messages.append({"pid": pid, "content": m})

    # Extract final answer
    router_msgs = [m["content"] for m in all_messages
                   if m["pid"] == "router"
                   and not m["content"].startswith("[ThinkingEvent]")
                   and not m["content"].startswith("[ToolCallEvent]")
                   and not m["content"].startswith("[ToolResultEvent]")]
    final = router_msgs[-1] if router_msgs else "No response"

    return {"answer": final, "trace": all_messages}


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Customer Service Agent")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
