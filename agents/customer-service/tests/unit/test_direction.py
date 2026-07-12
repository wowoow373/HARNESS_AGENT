"""Unit tests for Direction Agent."""
import pytest
from unittest.mock import MagicMock
from harness.interfaces.types import AssemblyContext, UserRequest
from agents.direction.assembler import DirectionAssembler


class TestDirectionAssembler:

    def test_assemble_draft_prompt(self):
        assembler = DirectionAssembler(K=2)
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={
                    "task": "generate_directions",
                    "question": "改签规则是什么？",
                    "node_id": "ROOT",
                    "confirmed_triples": [],
                    "evidence_passages": [],
                    "K": 2,
                },
            ),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert "改签规则是什么？" in messages[1].content

    def test_assemble_with_confirmed_triples(self):
        assembler = DirectionAssembler(K=2)
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={
                    "task": "generate_directions",
                    "question": "改签规则是什么？",
                    "node_id": "N1",
                    "confirmed_triples": ["航班 | 改签规则 | 起飞前2小时"],
                    "evidence_passages": ["第3条：旅客可在起飞前2小时申请改签。"],
                    "K": 2,
                },
            ),
        )
        messages = assembler.assemble(ctx)
        assert "航班 | 改签规则 | 起飞前2小时" in messages[1].content
        assert "第3条" in messages[1].content
