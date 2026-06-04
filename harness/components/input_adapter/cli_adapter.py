"""CliAdapter — InputAdapter command-line implementation.

Reads user input from stdin, writes LLM responses to stdout.

Usage::

    adapter = CliAdapter()
    adapter = CliAdapter(session_id="my-session")
    adapter.prompt = "query> "
    request = adapter.receive()  # blocks on stdin
    adapter.send(response)       # prints text to stdout
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from harness.interfaces.types import Response, UserRequest


class CliAdapter:
    """InputAdapter implementation for command-line interaction.

    Reads single-line user input from stdin and converts it to a
    standardized UserRequest. Formats LLM Response objects and writes
    them to stdout.

    This is the default InputAdapter implementation. It uses only
    stdlib (sys, time) and has no external dependencies.

    Usage::

        adapter = CliAdapter()
        adapter = CliAdapter(session_id="my-session")
        adapter.prompt = "query> "
        request = adapter.receive()  # blocks on stdin
        adapter.send(response)       # prints text to stdout
    """

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(self, session_id: Optional[str] = None) -> None:
        """Initialize CliAdapter.

        Args:
            session_id: Session identifier. When None (default), a unique
                        session ID is auto-generated from the current
                        Unix timestamp.

        """
        self._session_id: str = session_id or self._generate_session_id()
        self._prompt: str = "> "

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

    def send(self, response: Response) -> None:
        """Write the LLM response text to stdout.

        Behaviour:
            - If ``response.text`` is a non-empty string, print it.
            - Otherwise (tool-only responses, empty responses) do
              nothing — tool invocations are internal framework
              concerns that the user does not need to see.

        Args:
            response: The Response object returned by the LLM.

        """
        if response.text:
            print(response.text)

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
