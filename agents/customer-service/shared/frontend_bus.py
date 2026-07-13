"""FrontendBus — structured event broadcaster for WebSocket frontend."""
import asyncio
import time


class FrontendBus:
    """Agent → frontend structured event broadcaster.

    Each adapter calls bus.emit(event) in its send() method.
    WebSocket server subscribes via subscribe() to receive events.
    """

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """Register a consumer queue. Called by WebSocket server."""
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def emit(self, event: dict) -> None:
        """Broadcast event to all subscribers. Called by adapters.

        Thread-safe: all adapters run on the same event loop,
        put_nowait is non-blocking.
        """
        event["_timestamp"] = time.time()
        for q in self._queues:
            q.put_nowait(event)
