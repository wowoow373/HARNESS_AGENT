"""Customer Service Agent — unified entry point.

Starts Kernel + 6-agent workflow + FastAPI server in one process.

Usage:
    python agents/customer-service/run.py
    curl -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"text":"改签规则是什么？"}'
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

# ── Path setup ──
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
    """Forwards to CliConsole + captures AgentOutput into thread-safe queues."""

    def __init__(self):
        self._cli = CliConsole(mode='mode_b')
        self.output_queues: dict[str, asyncio.Queue] = {}
        self._pending = {}  # pid → list of content strings (thread-safe)

    def make_queue(self, pid: str):
        self._pending[pid] = []

    def drain(self, pid: str) -> list[str]:
        msgs = list(self._pending.get(pid, []))
        self._pending[pid] = []
        return msgs

    async def send(self, event):
        if isinstance(event, AgentOutput):
            if event.pid in self._pending:
                self._pending[event.pid].append(event.content)
        await self._cli.send(event)

    async def receive(self):
        return await self._cli.receive()


# ═══════════════════════════════════════════════════════════════════════════
# Kernel context
# ═══════════════════════════════════════════════════════════════════════════

class KernelContext:
    """Holds kernel + console refs for the /chat endpoint."""

    def __init__(self):
        self.kernel: Kernel | None = None
        self.console: CapturingConsole | None = None
        self.ready = threading.Event()


_ctx = KernelContext()


# ═══════════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Customer Service Agent", version="0.1.0")
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
    if not _ctx.ready.is_set():
        return {"error": "System not initialized yet, wait a moment..."}

    kernel = _ctx.kernel
    console = _ctx.console

    # Register capture for all agents
    for pid in kernel.runtime_table:
        console.make_queue(pid)

    # Send message to router
    # kernel.send_input is synchronous (Queue.put_nowait), safe from any thread
    kernel.send_input("router", UserRequest(text=req.text))

    # Collect outputs — poll with increasing sleep
    all_messages = []
    router_finished = False
    deadline = time.time() + 120

    while time.time() < deadline:
        drained_any = False
        for pid in list(kernel.runtime_table.keys()):
            msgs = console.drain(pid)
            for m in msgs:
                all_messages.append({"pid": pid, "content": m})
                drained_any = True

        if drained_any:
            deadline = time.time() + 15

        # Check router state
        router = kernel.runtime_table.get("router")
        if router and hasattr(router, 'state') and str(router.state) == "AgentState.FINISHED":
            if router_finished:
                time.sleep(0.3)
                for pid in list(kernel.runtime_table.keys()):
                    msgs = console.drain(pid)
                    for m in msgs:
                        all_messages.append({"pid": pid, "content": m})
                break
            router_finished = True
            deadline = time.time() + 3

        time.sleep(0.1)

    # Extract final answer: last router message
    router_msgs = [m["content"] for m in all_messages if m["pid"] == "router"]
    final = router_msgs[-1] if router_msgs else "No response"

    return {"answer": final, "trace": all_messages}


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

async def _run_kernel():
    """Run the Kernel + workflow in asyncio."""
    console = CapturingConsole()
    kernel = Kernel(console)
    _ctx.kernel = kernel
    _ctx.console = console

    result = kernel.spawn_from_script(
        str(Path(__file__).parent / "customer_service_workflow.py")
    )
    print(f"[system] Workflow spawned: {result['workflow_flag']} "
          f"with {len(result['agents'])} agents")

    _ctx.ready.set()

    # Wait for all agent tasks
    tasks = list(kernel._tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _run_event_loop():
    """Run asyncio event loop in background thread."""
    asyncio.run(_run_kernel())


if __name__ == "__main__":
    import uvicorn

    # Start Kernel event loop in background
    loop_thread = threading.Thread(target=_run_event_loop, daemon=True, name="kernel")
    loop_thread.start()

    # Wait for kernel to be ready
    if not _ctx.ready.wait(timeout=30):
        print("ERROR: Kernel failed to start within 30s")
        sys.exit(1)

    print("=" * 50)
    print("  Customer Service Agent")
    print("=" * 50)
    print()
    print("  Frontend:  http://localhost:8000")
    print("  Chat API:  POST http://localhost:8000/chat")
    print()
    print('  Test: curl -X POST http://localhost:8000/chat \\')
    print('        -H "Content-Type: application/json" \\')
    print('        -d \'{"text":"改签规则是什么？"}\'')
    print()
    print("  Press Ctrl+C to stop")
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
