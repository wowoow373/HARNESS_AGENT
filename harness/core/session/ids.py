"""会话相关的 id / token 生成器。"""

from __future__ import annotations

import os
import sys
import time
import uuid


def new_conv_id() -> str:
    """生成会话 id：conv-<时间戳>-<随机后缀>。"""
    return f"conv-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def new_msg_id() -> str:
    """生成跨日志因果边配对键。由内核在调解点盖章，绝不信任 LLM 的 call_id。"""
    return f"M-{uuid.uuid4().hex[:8]}"


def new_owner_token() -> str:
    """生成 owner token：pid-<进程号>-<纳秒时间戳>。"""
    return f"pid-{os.getpid()}-{time.time_ns()}"


def pid_from_token(token: str) -> int | None:
    """从 owner token 解析进程号；解析失败返回 None。"""
    try:
        return int(token.split("-")[1])
    except (IndexError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """进程是否存活（os.kill(pid, 0) 探活）。

    仅限 POSIX：Windows 上 os.kill(pid, 0) 会调用 TerminateProcess
    杀死被探测进程，因此直接抛 NotImplementedError，禁止静默误用。
    """
    if sys.platform == "win32":
        raise NotImplementedError(
            "pid_alive is POSIX-only; Windows owner probing not yet supported"
        )
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    return True
