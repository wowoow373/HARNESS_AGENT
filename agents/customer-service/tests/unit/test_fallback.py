"""Unit tests for Fallback Agent (stub)."""
from harness.interfaces.types import AssemblyContext, UserRequest
from agents.fallback.assembler import FallbackAssembler


class TestFallbackAssembler:

    def test_assemble_fallback_prompt(self):
        assembler = FallbackAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="你能帮我做什么？"),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "无法处理" in messages[0].content or "人工" in messages[0].content
