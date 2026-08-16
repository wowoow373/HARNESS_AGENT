"""Session 持久化异常体系。"""

from __future__ import annotations


class SessionError(Exception):
    """会话持久化与恢复的基类异常。"""


class CorruptLogError(SessionError):
    """agent 日志损坏：header 缺失/损坏，或 seq 不连续（缺号=损坏，拒绝恢复）。"""


class SessionOwnerConflict(SessionError):
    """会话被另一个存活进程占用（owner token 校验失败）。"""


class BootError(SessionError):
    """boot 失败：manifest 硬冲突、script sha1 不匹配、会话不存在等。

    启动失败 = 最安全失败：此时无任何 agent 已跑，干净退出即可。
    """
