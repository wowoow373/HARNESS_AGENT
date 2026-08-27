"""审批相关 SystemCommand / SystemEvent 类型冒烟测试。"""

from harness.runtime.cli_console import CliConsole
from harness.runtime.types import (
    ApprovalRequested, CommandApprove, CommandDeny, CommandError,
)


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
