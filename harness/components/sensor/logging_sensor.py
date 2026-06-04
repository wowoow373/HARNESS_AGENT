"""LoggingSensor — 默认 Sensor 实现。

在会话结束阶段将完整执行轨迹写入 MemoryBackend 的 episodic 命名空间。

Usage::

    from harness.components.sensor import LoggingSensor
    sensor = LoggingSensor(memory=memory_instance)
    sensor.sense(trajectory)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from harness.interfaces.memory_backend import MemoryBackend
from harness.interfaces.sensor import Sensor
from harness.interfaces.types import Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAMESPACE = "episodic"
_KEY_PREFIX = "session_"
_FALLBACK_SESSION_ID = "unknown"
_HISTORY_EXCERPT_MAX_LEN = 500


# ---------------------------------------------------------------------------
# LoggingSensor
# ---------------------------------------------------------------------------


class LoggingSensor:
    """Sensor 的默认实现 — 将轨迹记录到 MemoryBackend。

    MemoryBackend 通过构造函数注入。在 :meth:`sense` 被调用时，
    从 Trajectory 中提取关键信息并写入 episodic 命名空间。

    Usage::

        sensor = LoggingSensor(memory=memory_instance)
        sensor.sense(trajectory)

    Write value structure::

        {
            "session_id": str,
            "timestamp": float,
            "user_request": str,
            "final_output": str,
            "execution_time": float,
            "message_count": int,
            "tool_call_count": int,
            "tool_calls_summary": [
                {"tool_name": str, "success": bool, "error": Optional[str]},
                ...
            ],
            "history_excerpt": str,
        }
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, memory: MemoryBackend) -> None:
        """Initialize LoggingSensor with a MemoryBackend.

        Args:
            memory: MemoryBackend instance used for persisting trajectory data.
        """
        self.memory: MemoryBackend = memory

    # ------------------------------------------------------------------
    # Public interface — Sensor protocol
    # ------------------------------------------------------------------

    def sense(self, trajectory: Trajectory) -> None:
        """Evaluate the full execution trajectory and persist to MemoryBackend.

        Extracts structured data from the Trajectory and writes it to the
        episodic namespace. Writes do NOT raise on failure — errors are
        caught and logged at WARNING level.

        Args:
            trajectory: Complete session execution trajectory.
        """
        session_id = self._extract_session_id(trajectory)
        key = f"{_KEY_PREFIX}{session_id}"
        timestamp = time.time()

        logger.info(
            "sense() called: session_id=%s, execution_time=%.3fs, messages=%d, tool_calls=%d",
            session_id,
            trajectory.execution_time,
            len(trajectory.history),
            len(trajectory.tool_calls),
        )

        value = self._build_value(trajectory, session_id, timestamp)

        try:
            self.memory.write(key, value, namespace=_NAMESPACE)
            logger.debug(
                "Write success: key=%r, namespace=%r", key, _NAMESPACE
            )
        except Exception as exc:
            logger.warning(
                "Write failed: key=%r, namespace=%r, error=%r",
                key,
                _NAMESPACE,
                exc,
            )

    # ------------------------------------------------------------------
    # Internal — data extraction helpers
    # ------------------------------------------------------------------

    def _extract_session_id(self, trajectory: Trajectory) -> str:
        """Extract session_id from trajectory.

        Priority:
            1. ``trajectory.session_id``
            2. Fallback ``"unknown"``

        Args:
            trajectory: Full execution trajectory.

        Returns:
            Non-empty session identifier string.
        """
        if trajectory.session_id:
            return trajectory.session_id
        return _FALLBACK_SESSION_ID

    def _build_value(
        self,
        trajectory: Trajectory,
        session_id: str,
        timestamp: float,
    ) -> Dict[str, Any]:
        """Build the value dict written to MemoryBackend.

        Args:
            trajectory: Full execution trajectory.
            session_id: Extracted session identifier.
            timestamp: Current wall-clock timestamp.

        Returns:
            Structured dictionary with all tracked fields.
        """
        user_request_text = ""
        for msg in trajectory.history:
            if msg.role == "user":
                user_request_text = msg.content
                break

        tool_calls_summary = self._build_tool_calls_summary(trajectory)
        history_excerpt = self._build_history_excerpt(trajectory)

        return {
            "session_id": session_id,
            "timestamp": timestamp,
            "user_request": user_request_text,
            "final_output": trajectory.final_output,
            "execution_time": trajectory.execution_time,
            "message_count": len(trajectory.history),
            "tool_call_count": len(trajectory.tool_calls),
            "tool_calls_summary": tool_calls_summary,
            "history_excerpt": history_excerpt,
        }

    def _build_tool_calls_summary(
        self, trajectory: Trajectory
    ) -> List[Dict[str, Any]]:
        """Build a summary list of tool call records.

        Each entry contains:
            - ``tool_name``
            - ``success``  (bool)
            - ``error``    (str or None)

        Args:
            trajectory: Full execution trajectory.

        Returns:
            List of tool call summary dicts.
        """
        summary: List[Dict[str, Any]] = []
        for tc in trajectory.tool_calls:
            summary.append(
                {
                    "tool_name": tc.tool_name,
                    "success": tc.error is None,
                    "error": tc.error,
                }
            )
        return summary

    def _build_history_excerpt(self, trajectory: Trajectory) -> str:
        """Build a truncated text excerpt from conversation history.

        Concatenates each message as ``role: content`` (newline-separated),
        then truncates to the first ``_HISTORY_EXCERPT_MAX_LEN`` characters.

        Args:
            trajectory: Full execution trajectory.

        Returns:
            Truncated history text (max 500 chars).
        """
        lines: List[str] = []
        for msg in trajectory.history:
            lines.append(f"{msg.role}: {msg.content}")

        full_text = "\n".join(lines)
        if len(full_text) > _HISTORY_EXCERPT_MAX_LEN:
            full_text = full_text[:_HISTORY_EXCERPT_MAX_LEN]
        return full_text
