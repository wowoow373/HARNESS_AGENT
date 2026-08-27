"""ApprovalBroker — 高风险工具审批的 pending 管理与裁决。

挂 Kernel（进程级单例）。管理 pending 审批请求（asyncio.Future 表），
request() 时推送 ApprovalRequested 事件到 SystemConsole，
resolve() 由 Kernel 命令循环（/approve /deny）回调。
"""

from __future__ import annotations

import asyncio
import secrets

from .types import ApprovalRequested


class ApprovalBroker:
    """审批请求的 pending 管理与裁决中枢。

    用法::

        broker = ApprovalBroker(console=console)
        approval_id, future = broker.request(pid, tool_name, args)
        approved = await asyncio.wait_for(future, timeout=300)
        # 命令循环里：broker.resolve(approval_id, approved=True)
    """

    def __init__(self, console):
        self._console = console
        self._pending: dict[str, asyncio.Future] = {}

    def request(self, pid: str, tool_name: str, args: dict) -> tuple[str, asyncio.Future]:
        """创建一个审批请求，推送事件，返回 (approval_id, future)。"""
        approval_id = self._new_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[approval_id] = future
        asyncio.create_task(self._console.send(ApprovalRequested(
            approval_id=approval_id,
            pid=pid,
            tool_name=tool_name,
            arguments=args,
        )))
        return approval_id, future

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """裁决一个 pending 请求。返回 False 表示无此请求或已裁决。"""
        future = self._pending.pop(approval_id, None)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def cancel(self, approval_id: str) -> None:
        """取消一个 pending 请求（agent 被 kill 等场景）。"""
        future = self._pending.pop(approval_id, None)
        if future is not None and not future.done():
            future.cancel()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def _new_id(self) -> str:
        """生成 4 位 hex 短 id，碰撞时重生成。"""
        while True:
            aid = secrets.token_hex(2)
            if aid not in self._pending:
                return aid
