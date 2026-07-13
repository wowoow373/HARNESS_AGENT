"""Unit tests for Validation Agent."""
import pytest
from unittest.mock import MagicMock
from harness.interfaces.types import AssemblyContext, UserRequest
from agents.validation.assembler import ValidationAssembler


class TestValidationAssembler:

    def test_assemble_validator_prompt(self, sample_graph):
        graph, _ = sample_graph
        memory = MagicMock()
        state = {
            "question": "测试问题",
            "graph": graph.to_dict(),
        }
        memory.read.return_value = state

        assembler = ValidationAssembler(memory=memory)
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={"task": "validate_graph", "trigger": "evidence_complete"},
            ),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "测试问题" in messages[1].content
        assert "Graph" in messages[1].content
