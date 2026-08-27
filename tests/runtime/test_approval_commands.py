"""审批相关 SystemCommand / SystemEvent 类型冒烟测试。"""

from harness.runtime.types import (
    ApprovalRequested, CommandApprove, CommandDeny,
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
