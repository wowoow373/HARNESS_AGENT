"""CliAdapter — InputAdapter command-line implementation.

Reads user input from stdin, dispatches AdapterEvent to appropriate
output channels (stdout for foreground conversation, stderr for
background tool/system status).

Usage::

    adapter = CliAdapter()
    adapter = CliAdapter(session_id="my-session")
    adapter.prompt = "query> "
    adapter.debug = True
    request = adapter.receive()  # blocks on stdin
    adapter.send(event)          # dispatch on event type
"""

from __future__ import annotations

import sys
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


class CliAdapter:
    """InputAdapter implementation for command-line interaction.

    Reads single-line user input from stdin and converts it to a
    standardized UserRequest. Dispatches AdapterEvent objects to
    the appropriate output channel:

    * TextEvent → stdout (foreground conversation)
    * ThinkingEvent → stderr (background, debug mode only)
    * ToolCallEvent → stderr (background tool status)
    * ToolResultEvent → stderr (background tool result)
    * StopEvent → no-op (session control)

    This is the default InputAdapter implementation. It uses only
    stdlib (sys, time) and has no external dependencies.

    Usage::

        adapter = CliAdapter()
        adapter = CliAdapter(session_id="my-session")
        adapter.prompt = "query> "
        adapter.debug = True
        request = adapter.receive()  # blocks on stdin
        adapter.send(event)          # dispatch on event type
    """

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(
        self,
        session_id: Optional[str] = None,
        debug: bool = False,
    ) -> None:
        """Initialize CliAdapter.

        Args:
            session_id: Session identifier. When None (default), a unique
                        session ID is auto-generated from the current
                        Unix timestamp.
            debug: When True, ThinkingEvent is printed to stderr.
                   Default False.

        """
        self._session_id: str = session_id or self._generate_session_id()
        self._prompt: str = "> "
        self._debug: bool = debug

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def prompt(self) -> str:
        """The input prompt string displayed before each receive() call.

        Default is ``"> "``. Set to any string to customise the prompt
        shown to the user.

        """
        return self._prompt

    @prompt.setter
    def prompt(self, value: str) -> None:
        self._prompt = value

    @property
    def session_id(self) -> str:
        """The session identifier used for this adapter instance.

        Either the value passed to the constructor or an auto-generated
        timestamp-based string.

        """
        return self._session_id

    @property
    def debug(self) -> bool:
        """Whether debug output (thinking) is enabled.

        When True, ``ThinkingEvent`` is printed to stderr.
        Default is False.

        """
        return self._debug

    @debug.setter
    def debug(self, value: bool) -> None:
        self._debug = value

    # ------------------------------------------------------------------
    # InputAdapter protocol
    # ------------------------------------------------------------------

    def receive(self) -> UserRequest:
        """Read one line from stdin and return a UserRequest.

        Behaviour:
            1. Write ``self.prompt`` to stdout (flushed immediately).
            2. Block until a full line is available on stdin.
            3. Strip leading and trailing whitespace.
            4. Return a ``UserRequest`` with ``text`` set to the line
               and ``session_id`` set to this adapter's session ID.

        Returns:
            UserRequest: A standardised request object. On EOF (Ctrl+D)
            the ``text`` field will be an empty string, which the
            orchestrator can interpret as an exit signal.

        """
        sys.stdout.write(self._prompt)
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            # Ctrl+C produces an empty request (orchestrator exit
            # signal) without a traceback.
            return UserRequest(text="", session_id=self._session_id)
        if not line:
            # EOF — stdin closed (e.g. Ctrl+D, pipe exhausted).
            return UserRequest(text="", session_id=self._session_id)
        text = line.strip()
        return UserRequest(text=text, session_id=self._session_id)

    def send(self, event: AdapterEvent) -> None:
        """Dispatch an adapter event to the appropriate output channel.

        Event routing:

        * ``TextEvent`` → print ``content`` to **stdout** (foreground).
        * ``ThinkingEvent`` → print to **stderr** only when
          ``debug`` is True (background).
        * ``ToolCallEvent`` → print to **stderr** with tool name
          and arguments summary (background).
        * ``ToolResultEvent`` → print to **stderr** with result
          summary and duration (background).
        * ``StopEvent`` → no-op (session control event).

        Args:
            event: The adapter event to dispatch.

        """
        if isinstance(event, TextEvent):
            if event.content:
                print(event.content)

        elif isinstance(event, ThinkingEvent):
            if self._debug and event.content:
                print(f"[thinking] {event.content}", file=sys.stderr)

        elif isinstance(event, ToolCallEvent):
            summary = self._summarize_args(event.tool_name, event.arguments)
            print(f"🔧 {event.tool_name}({summary})", file=sys.stderr)

        elif isinstance(event, ToolResultEvent):
            if event.error:
                print(
                    f"🔧 {event.tool_name} → ERROR "
                    f"({event.duration_ms:.0f}ms): {event.error}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"🔧 {event.tool_name} → OK "
                    f"({event.duration_ms:.0f}ms)",
                    file=sys.stderr,
                )

        elif isinstance(event, StopEvent):
            # Session control event — nothing to display.
            pass

    # ------------------------------------------------------------------
    # Internal helpers (migrated from LifecycleOrchestrator)
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_args(tool_name: str, args: Dict[str, Any]) -> str:
        """Generate a human-readable summary of tool call arguments.

        Args:
            tool_name: The tool name (used to select display format).
            args: The tool arguments dict.

        Returns:
            A single-line summary, truncated to keep output compact.

        """
        if not args:
            return ""
        # Friendly display for common tools
        if tool_name in ("read_file", "write_file"):
            path = args.get("file_path", args.get("path", ""))
            return str(path)[:80]
        if tool_name == "shell":
            cmd = args.get("command", "")
            return str(cmd)[:100]
        # Default: show all keys, truncate values
        parts = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 60:
                v_str = v_str[:57] + "..."
            parts.append(f"{k}={v_str}")
        return ", ".join(parts)[:120]

    @staticmethod
    def _summarize_result(result: Any) -> str:
        """Generate a human-readable summary of a tool execution result.

        Args:
            result: The result returned by the tool (may be ToolResult
                    or any object).

        Returns:
            A single-line summary, truncated to keep output compact.

        """
        # Extract content from ToolResult-like objects
        if hasattr(result, "content"):
            content = result.content
        else:
            content = result

        if content is None:
            return "null"
        if isinstance(content, str):
            if len(content) > 120:
                return content[:117] + "..."
            return content
        s = str(content)
        if len(s) > 120:
            s = s[:117] + "..."
        return s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a unique session identifier from the current timestamp.

        Returns:
            str: Session ID in the format ``"cli-<unix-timestamp>"``.

        """
        return f"cli-{int(time.time())}"
