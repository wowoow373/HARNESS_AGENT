"""Customer Service WebSocket Server.

Usage:
    python agents/customer-service/server.py
    # Open browser at http://localhost:8000
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CS_PATH = str(Path(__file__).resolve().parent)
if _CS_PATH not in sys.path:
    sys.path.insert(0, _CS_PATH)

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from shared.frontend_bus import FrontendBus

app = FastAPI(title="Customer Service Agent", version="0.1.0")

static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

frontend_bus = FrontendBus()
connected_clients: list[WebSocket] = []


@app.get("/")
async def root():
    index_path = static_path / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Customer Service Agent</h1>")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    queue = frontend_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:
                break
    except Exception:
        pass
    finally:
        connected_clients.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Customer Service Agent Server")
    print("=" * 50)
    print()
    print("  Frontend: http://localhost:8000")
    print("  WebSocket: ws://localhost:8000/ws")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
