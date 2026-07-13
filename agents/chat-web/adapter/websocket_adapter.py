"""WebSocketAdapter — InputAdapter WebSocket implementation.

Bridges synchronous InputAdapter protocol with asynchronous WebSocket
communication using thread-safe queue.Queue instances.

Usage::

    adapter = WebSocketAdapter()
    adapter = WebSocketAdapter(session_id="my-session")

    # In the WebSocket endpoint (async context):
    #   adapter.put_message({"text": "hello"})   # push user input
    #   event = adapter.get_event(block=True)     # pull events to send

    # In the orchestrator (sync context):
    request = adapter.receive()  # blocks until user input arrives
    adapter.send(event)          # enqueues event for WebSocket
"""

from __future__ import annotations

import queue
import time
from typing import Any, Dict, Optional

from harness.interfaces.types import (
    AdapterEvent,
    StopEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserRequest,
)


class WebSocketAdapter:
    """InputAdapter implementation for WebSocket interaction.

    Uses two thread-safe queues to bridge sync orchestrator and async
    WebSocket endpoint:

    * inbox:  WebSocket endpoint -> receive()   (user input)
    * outbox: send() -> WebSocket endpoint       (agent events)

    This is the default InputAdapter for chat-web agent. It uses only
    stdlib (queue, time) and has no external dependencies.

    Usage::

        adapter = WebSocketAdapter()
        adapter = WebSocketAdapter(session_id="my-session")

        # WebSocket endpoint (async context):
        adapter.put_message({"text": "hello"})
        event = adapter.get_event(block=True)

        # Orchestrator (sync context):
        request = adapter.receive()
        adapter.send(event)
    """

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(
        self,
        session_id: Optional[str] = None,
    ) -> None:
        """Initialize WebSocketAdapter.

        Args:
            session_id: Session identifier. When None (default), a unique
                        session ID is auto-generated from the current
                        Unix timestamp.

        """
        self._session_id: str = session_id or self._generate_session_id()
        self._inbox: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue()
        self._outbox: queue.Queue[Dict[str, Any]] = queue.Queue()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        """The session identifier used for this adapter instance.

        Either the value passed to the constructor or an auto-generated
        timestamp-based string.

        """
        return self._session_id

    # ------------------------------------------------------------------
    # InputAdapter protocol
    # ------------------------------------------------------------------

    def receive(self) -> UserRequest:
        """Block until a user message arrives from the WebSocket endpoint.

        Behaviour:
            1. Block on the inbox queue until a message is available.
            2. If the message is None (sentinel), return a UserRequest
               with empty text as an exit signal.
            3. Otherwise, extract the "text" field and return a
               UserRequest with the text and this adapter's session ID.

        Returns:
            UserRequest: A standardised request object. When the inbox
            receives None, the ``text`` field will be an empty string,
            which the orchestrator can interpret as an exit signal.

        """
        data = self._inbox.get()
        if data is None:
            return UserRequest(text="", session_id=self._session_id)
        text = data.get("text", "")
        return UserRequest(text=text, session_id=self._session_id)

    def send(self, event: AdapterEvent) -> None:
        """Serialize and enqueue an adapter event for the WebSocket endpoint.

        The event is converted to a JSON-serialisable dict via
        _serialize_event() and placed on the outbox queue. The
        WebSocket endpoint can retrieve it via get_event().

        Args:
            event: The adapter event to dispatch.

        """
        payload = self._serialize_event(event)
        self._outbox.put(payload)

    # ------------------------------------------------------------------
    # WebSocket endpoint interface
    # ------------------------------------------------------------------

    def put_message(self, data: Dict[str, Any]) -> None:
        """Push a user message into the inbox queue.

        Called by the WebSocket endpoint when a new message arrives
        from the client.

        Args:
            data: A dict containing the user message. Expected to have
                  a "text" key with the message content.

        """
        self._inbox.put(data)

    def get_event(
        self,
        block: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve an event from the outbox queue.

        Called by the WebSocket endpoint to pull events that should be
        sent to the client.

        Args:
            block: If True (default), block until an event is available.
                   If False, return immediately, raising queue.Empty
                   if no event is present.
            timeout: Maximum time to block, in seconds. Only used when
                     block is True. None means wait indefinitely.

        Returns:
            A dict representing the event, or None if the queue is empty
            and block is False (raises queue.Empty instead).

        Raises:
            queue.Empty: If block is False and no event is available.

        """
        return self._outbox.get(block=block, timeout=timeout)

    def close(self) -> None:
        """Signal the adapter to shut down.

        Places a None sentinel on the inbox queue, which causes
        receive() to wake up and return an exit signal.

        """
        self._inbox.put(None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_event(event: AdapterEvent) -> Dict[str, Any]:
        """Convert an AdapterEvent to a JSON-serialisable dict.

        Args:
            event: The adapter event to serialise.

        Returns:
            A dict with a "type" field and event-specific fields.

        """
        if isinstance(event, TextEvent):
            return {"type": "text", "content": event.content}

        if isinstance(event, ThinkingEvent):
            return {"type": "thinking", "content": event.content}

        if isinstance(event, ToolCallEvent):
            return {
                "type": "tool_call",
                "call_id": event.call_id,
                "tool_name": event.tool_name,
                "arguments": event.arguments,
            }

        if isinstance(event, ToolResultEvent):
            # The orchestrator may pass a ToolResult object as result.
            # Extract its .content for JSON serialization.
            result = event.result
            if result is not None and hasattr(result, "content"):
                result = result.content
            return {
                "type": "tool_result",
                "call_id": event.call_id,
                "tool_name": event.tool_name,
                "success": event.success,
                "result": result,
                "error": event.error,
                "duration_ms": event.duration_ms,
            }

        if isinstance(event, StopEvent):
            return {"type": "stop", "stop_reason": event.stop_reason}

        # Fallback for unknown event types
        return {"type": "unknown"}

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a unique session identifier from the current timestamp.

        Uses nanosecond precision to avoid collisions when multiple
        adapters are created within the same second.

        Returns:
            str: Session ID in the format ``"ws-<nanosecond-timestamp>"``.

        """
        return f"ws-{time.time_ns()}"
