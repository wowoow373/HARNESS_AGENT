"""Unit tests for Router Agent (adapter + assembler)."""
import pytest
from harness.interfaces.types import AssemblyContext, GuidesBundle, Message, UserRequest, TextEvent
from agents.router.assembler import RouterAssembler
from agents.router.adapter import RouterAdapter


class TestRouterAssembler:

    def test_assemble_intent_classification_prompt(self):
        assembler = RouterAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="改签规则是什么？"),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "qa" in messages[0].content
        assert messages[1].role == "user"
        assert "改签规则是什么" in messages[1].content

    def test_assemble_qa_answer_formatting(self):
        assembler = RouterAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={
                    "type": "qa_answer",
                    "question": "改签规则是什么？",
                    "answer": "非特价舱位乘客可在起飞前2小时申请改签",
                    "sources": ["第3条：旅客可在起飞前2小时申请改签服务。"],
                },
            ),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert "非特价舱位乘客" in messages[1].content
        assert "第3条" in messages[1].content


class TestRouterAdapterParseIntent:

    def test_parse_qa_intent(self):
        text = "INTENT: qa\nCONFIDENCE: 0.94\nSLOTS: {}"
        result = RouterAdapter._parse_intent(text)
        assert result["intent"] == "qa"
        assert result["confidence"] == 0.94

    def test_parse_task_intent(self):
        text = "INTENT: task\nCONFIDENCE: 0.85\nSLOTS: {\"action\": \"改签\"}"
        result = RouterAdapter._parse_intent(text)
        assert result["intent"] == "task"

    def test_parse_fallback_default(self):
        text = "garbled nonsense output"
        result = RouterAdapter._parse_intent(text)
        assert result["intent"] == "fallback"
        assert result["confidence"] == 0.0
