"""Sequencer — 会话级 LSN（Log Sequence Number）发号器。

单调递增，不保证连续——崩溃使已发号但未落盘的事件形成合法空洞，
空洞本身就是损失度量的证据（设计 2.4）。不校验连续性。
"""

from __future__ import annotations


class Sequencer:
    """会话级 LSN 发号器。进程内由 SessionStore 持有，跨 boot 经 max(lsn)+1 恢复。"""

    def __init__(self, start: int = 0):
        self._next = start

    def next(self) -> int:
        """取号（单调 +1）。"""
        value = self._next
        self._next += 1
        return value

    @property
    def next_value(self) -> int:
        """下一个将发出的号（不取号）。"""
        return self._next
