"""Unit tests for Task Agent (stub)."""
from harness.interfaces.types import AssemblyContext, UserRequest
from agents.task_agent.assembler import TaskAssembler


class TestTaskAssembler:

    def test_assemble_task_prompt(self):
        assembler = TaskAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="我要改签"),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "业务办理" in messages[0].content
        assert "我要改签" in messages[1].content
