"""Unit tests for topic_code prompts and parsers."""
import pytest
from shared.prompts import (
    CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY,
    CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY,
    CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY,
    build_core_draft_v3_user_content,
    build_core_final_v3_user_content,
    build_core_validator_content_from_merger,
    parse_draft_v3_output,
    parse_draft_list,
    parse_final,
    parse_validator_decisions,
    parse_validator_answer,
)


class TestDraftPrompt:

    def test_system_prompt_is_non_empty(self):
        assert len(CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY) > 100

    def test_user_content_includes_question(self):
        content = build_core_draft_v3_user_content(
            question="测试问题",
            evidence_passages=[],
            confirmed_triples=[],
            K=2,
        )
        assert "测试问题" in content

    def test_user_content_includes_K(self):
        content = build_core_draft_v3_user_content(
            question="Q", evidence_passages=[], confirmed_triples=[], K=3
        )
        assert "3" in content


class TestFinalPrompt:

    def test_system_prompt_is_non_empty(self):
        assert len(CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY) > 100

    def test_user_content_includes_direction(self):
        content = build_core_final_v3_user_content(
            question="Q",
            confirmed_triples=[],
            retrieved_passages=["passage 1"],
            draft_subject="航班",
            draft_relation="改签规则",
        )
        assert "航班" in content
        assert "改签规则" in content
        assert "passage 1" in content


class TestValidatorPrompt:

    def test_system_prompt_is_non_empty(self):
        assert len(CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY) > 100

    def test_content_includes_question(self, sample_graph):
        graph, _ = sample_graph
        content = build_core_validator_content_from_merger(
            question="测试问题",
            merger=graph,
        )
        assert "测试问题" in content


class TestParseDraftOutput:

    def test_parse_valid_output(self):
        raw = """<remaining_question>改签需要什么条件？</remaining_question>
<next_facts>
1. 航班 | 改签规则 | ?
2. 乘客 | 适用条件 | ?
</next_facts>"""
        remaining_q, candidates = parse_draft_v3_output(raw)
        assert "改签需要什么条件" in remaining_q
        assert len(candidates) == 2
        assert candidates[0] == ("航班", "改签规则")

    def test_parse_empty_candidates(self):
        raw = """<remaining_question>无</remaining_question>
<next_facts>
</next_facts>"""
        remaining_q, candidates = parse_draft_v3_output(raw)
        assert candidates == []


class TestParseFinal:

    def test_parse_valid_triple(self):
        result = parse_final("航班 | 改签规则 | 起飞前2小时 | SELECT: 1")
        assert result == ("航班", "改签规则", "起飞前2小时", 1)

    def test_parse_invalid(self):
        assert parse_final("INVALID") == "INVALID"

    def test_parse_malformed_returns_none(self):
        assert parse_final("garbage text without pipes") is None


class TestParseValidator:

    def test_parse_decisions(self):
        raw = """<structure>ok</structure>
<semantic>ok</semantic>
<comprehensive>ok</comprehensive>
<rethink>none</rethink>
Final decision logic: all valid

Node N0: KEEP
Node N1: DISCARD

ANSWER: NONE"""
        id_map = {"abc": "N0", "def": "N1"}
        decisions = parse_validator_decisions(raw, id_map)
        assert decisions == {"abc": 1, "def": 0}

    def test_parse_answer(self):
        raw = "ANSWER: 非特价舱位乘客可在起飞前2小时申请改签"
        assert parse_validator_answer(raw) == "非特价舱位乘客可在起飞前2小时申请改签"

    def test_parse_answer_none(self):
        assert parse_validator_answer("ANSWER: NONE") is None
