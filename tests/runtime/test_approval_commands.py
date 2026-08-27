"""审批相关 SystemCommand / SystemEvent 类型冒烟测试。"""

import asyncio

from harness.runtime.cli_console import CliConsole
from harness.runtime.kernel import Kernel
from harness.runtime.types import (
    ApprovalRequested, CommandApprove, CommandDeny, CommandError,
)


class _ConsoleSpy:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)

    async def receive(self):
        return None  # 不会被调用到


def test_approval_requested_fields():
    ev = ApprovalRequested(approval_id="a3f9", pid="root",
                           tool_name="delete_file", arguments={"path": "/x"})
    assert ev.approval_id == "a3f9"
    assert ev.pid == "root"
    assert ev.tool_name == "delete_file"
    assert ev.arguments == {"path": "/x"}


def test_command_approve_deny_fields():
    assert CommandApprove(approval_id="a3f9").approval_id == "a3f9"
    assert CommandDeny(approval_id="a3f9").approval_id == "a3f9"


def test_parse_approve():
    cmd = CliConsole()._parse_command("/approve a3f9")
    assert isinstance(cmd, CommandApprove)
    assert cmd.approval_id == "a3f9"


def test_parse_deny():
    cmd = CliConsole()._parse_command("/deny a3f9")
    assert isinstance(cmd, CommandDeny)
    assert cmd.approval_id == "a3f9"


def test_parse_approve_missing_id():
    cmd = CliConsole()._parse_command("/approve")
    assert isinstance(cmd, CommandError)


def test_parse_deny_missing_id():
    cmd = CliConsole()._parse_command("/deny")
    assert isinstance(cmd, CommandError)


def test_kernel_has_broker_and_registry():
    k = Kernel(_ConsoleSpy())
    assert k.approval_broker is not None
    assert k.policy_registry is not None
    assert k.approval_broker.pending_count == 0


def test_kernel_approval_bridge():
    async def _t():
        k = Kernel(_ConsoleSpy())
        aid, fut = k.approval_broker.request("root", "t", {})
        assert k.approval_broker.resolve(aid, True) is True
        assert await fut is True
        return k

    k = asyncio.run(_t())
    assert k.approval_broker.pending_count == 0
