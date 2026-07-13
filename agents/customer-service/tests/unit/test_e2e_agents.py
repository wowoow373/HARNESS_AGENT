"""E2E tests for each agent: real LLM validates prompt→LLM→parser pipeline.

Each test verifies the agent's core behavior WITHOUT the Runtime infrastructure:
  1. Assemble prompt with specific input
  2. Call real LLM
  3. Parse output with the agent's parser
  4. Verify parsed structure and content

Marked @pytest.mark.slow — requires LLM API access (.env configured).
Run: pytest agents/customer-service/tests/unit/test_e2e_agents.py -v -m slow
"""
import pytest
from harness.interfaces.types import AssemblyContext, UserRequest, Message
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.messaging.builder import messages_to_dicts

from agents.router.assembler import RouterAssembler
from agents.router.adapter import RouterAdapter
from agents.direction.assembler import DirectionAssembler
from agents.direction.adapter import DirectionAdapter
from agents.evidence.assembler import EvidenceAssembler
from agents.evidence.adapter import EvidenceAdapter
from agents.validation.assembler import ValidationAssembler
from agents.validation.adapter import ValidationAdapter
from shared.prompts import (
    parse_draft_v3_output,
    parse_final,
    parse_validator_decisions,
    parse_validator_answer,
)
from shared.retriever import InMemoryRetriever
from shared.subgraph_manager import SubGraphManager


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def llm():
    """Real LLM adapter, reads credentials from .env."""
    return MinimalLLMAdapter()


def _call_llm(llm, messages: list) -> str:
    """Call LLM and return text content."""
    response = llm(messages_to_dicts(messages), [])
    return response.text or ""


# ═══════════════════════════════════════════════════════════════════════════
# Router Agent
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestRouterAgentE2E:

    def test_classify_qa_question(self, llm):
        """输入政策咨询问题 → LLM 应分类为 qa，置信度 > 0.5"""
        assembler = RouterAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="改签规则是什么？"),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)
        parsed = RouterAdapter._parse_intent(raw)

        assert parsed["intent"] == "qa", (
            f"Expected intent='qa' for policy question, got '{parsed['intent']}'. "
            f"LLM output:\n{raw}"
        )
        assert parsed["confidence"] >= 0.5, (
            f"Expected confidence >= 0.5, got {parsed['confidence']}. "
            f"LLM output:\n{raw}"
        )

    def test_classify_task_request(self, llm):
        """输入业务办理请求 → LLM 应分类为 task"""
        assembler = RouterAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="我要改签机票"),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)
        parsed = RouterAdapter._parse_intent(raw)

        assert parsed["intent"] == "task", (
            f"Expected intent='task' for business request, got '{parsed['intent']}'. "
            f"LLM output:\n{raw}"
        )

    def test_classify_fallback_for_nonsense(self, llm):
        """输入无意义内容 → LLM 应分类为 fallback"""
        assembler = RouterAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="asdfghjkl"),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)
        parsed = RouterAdapter._parse_intent(raw)

        assert parsed["intent"] == "fallback", (
            f"Expected intent='fallback' for nonsense input, got '{parsed['intent']}'. "
            f"LLM output:\n{raw}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Direction Agent
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestDirectionAgentE2E:

    def test_generate_directions_from_root(self, llm):
        """ROOT 节点，无已确认事实 → LLM 应生成至少 1 个 (subj, rel) 候选方向"""
        assembler = DirectionAssembler(K=2)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "generate_directions",
                "question": "改签规则是什么？",
                "node_id": "ROOT",
                "confirmed_triples": [],
                "evidence_passages": [],
                "K": 2,
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)
        remaining_q, candidates = parse_draft_v3_output(raw)

        assert len(candidates) >= 1, (
            f"Expected at least 1 candidate direction, got {len(candidates)}. "
            f"LLM output:\n{raw}"
        )
        for subj, rel in candidates:
            assert subj and rel, (
                f"Each candidate must have non-empty subject and relation. "
                f"Got ({subj!r}, {rel!r}). LLM output:\n{raw}"
            )
        if remaining_q:
            assert len(remaining_q) > 0

    def test_generate_directions_with_context(self, llm):
        """已有 confirmed_triples → LLM 生成的候选方向应与上下文相关"""
        assembler = DirectionAssembler(K=2)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "generate_directions",
                "question": "改签需要满足什么条件？",
                "node_id": "N1",
                "confirmed_triples": ["航班 | 改签规则 | 起飞前2小时"],
                "evidence_passages": ["第3条：旅客可在起飞前2小时申请改签服务。"],
                "K": 2,
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)
        remaining_q, candidates = parse_draft_v3_output(raw)

        assert len(candidates) >= 1, (
            f"Expected at least 1 candidate direction with context. "
            f"LLM output:\n{raw}"
        )

    def test_generate_directions_output_format(self, llm):
        """验证 LLM 输出符合 <remaining_question> + <next_facts> 格式"""
        assembler = DirectionAssembler(K=1)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "generate_directions",
                "question": "金卡会员有什么权益？",
                "node_id": "ROOT",
                "confirmed_triples": [],
                "evidence_passages": [],
                "K": 1,
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)

        # 验证 XML 标签存在
        assert "<remaining_question>" in raw, (
            f"Missing <remaining_question> tag. LLM output:\n{raw}"
        )
        assert "<next_facts>" in raw, (
            f"Missing <next_facts> tag. LLM output:\n{raw}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Evidence Agent
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestEvidenceAgentE2E:

    @pytest.fixture
    def retriever(self, test_corpus):
        return InMemoryRetriever(test_corpus)

    def test_confirm_triple_with_passages(self, llm, retriever):
        """有相关 passages → LLM 应确认 triple 或返回 INVALID"""
        from unittest.mock import MagicMock
        memory = MagicMock()

        assembler = EvidenceAssembler(retriever=retriever, memory=memory, top_k=5)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "confirm_triple",
                "question": "改签规则是什么？",
                "direction": ("旅客", "改签规则"),
                "confirmed_triples": [],
                "corpus": [],
                "node_id": "ROOT",
            }),
        )
        messages = assembler.assemble(ctx)

        # 如果无 passages 则短路（此场景 corpus 有关键词重叠，应有结果）
        if ctx.user_request.metadata.get("_no_passages"):
            pytest.skip("Retriever found no passages for this direction")

        raw = _call_llm(llm, messages)
        parsed = parse_final(raw)

        # 可能返回有效 triple 或 INVALID，都是合法输出
        if parsed == "INVALID":
            assert True  # INVALID 是合法结果
        elif parsed is None:
            pytest.fail(f"Failed to parse Evidence output:\n{raw}")
        else:
            subj, rel, obj, select_idx = parsed
            assert subj and rel and obj, (
                f"Triple must have non-empty subj/rel/obj. Got {parsed}. "
                f"LLM output:\n{raw}"
            )
            assert isinstance(select_idx, int) and select_idx >= 0, (
                f"select_idx must be non-negative int. Got {select_idx}"
            )

    def test_confirm_triple_output_format(self, llm, retriever):
        """验证 LLM 输出符合 'subj | rel | obj | SELECT: idx' 或 'INVALID' 格式"""
        from unittest.mock import MagicMock
        memory = MagicMock()

        assembler = EvidenceAssembler(retriever=retriever, memory=memory, top_k=5)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "confirm_triple",
                "question": "金卡会员有什么权益？",
                "direction": ("金卡会员", "改签权益"),
                "confirmed_triples": [],
                "corpus": [],
                "node_id": "ROOT",
            }),
        )
        messages = assembler.assemble(ctx)

        if ctx.user_request.metadata.get("_no_passages"):
            # 无 passages 时直接返回 INVALID，验证短路逻辑
            assert messages[1].content == "INVALID"
            return

        raw = _call_llm(llm, messages)
        parsed = parse_final(raw)

        # 必须可解析：要么有 triple+SELECT，要么是 INVALID
        assert parsed is not None, (
            f"Could not parse Evidence LLM output (expected 'subj|rel|obj|SELECT:N' "
            f"or 'INVALID'):\n{raw}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Validation Agent
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestValidationAgentE2E:

    @pytest.fixture
    def graph_with_nodes(self):
        """构建一个有 1 个可靠节点的 graph 用于校验测试"""
        g = SubGraphManager()
        n1 = g.add_node(
            triple_str="航班 | 改签规则 | 起飞前2小时",
            parent_id="ROOT",
            accumulated_passages="第3条：旅客可在起飞前2小时申请改签服务。",
            select_idx=0,
            retrieved_passages=["第3条：旅客可在起飞前2小时申请改签服务。"],
        )
        return g, n1

    def test_validate_single_node_graph(self, llm, graph_with_nodes):
        """单节点 graph → LLM 应输出 KEEP/DISCARD 决策"""
        from unittest.mock import MagicMock
        graph, n1 = graph_with_nodes

        memory = MagicMock()
        state = {
            "question": "改签规则是什么？",
            "graph": graph.to_dict(),
        }
        memory.read.return_value = state

        assembler = ValidationAssembler(memory=memory)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "validate_graph",
                "trigger": "evidence_complete",
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)

        # 验证输出包含评分标签
        assert any(tag in raw for tag in ["<structure>", "<semantic>", "Node N0:"]), (
            f"Validator output should contain scoring sections. LLM output:\n{raw}"
        )

        # 验证可以解析 KEEP/DISCARD
        id_map = graph.get_id_map()
        decisions = parse_validator_decisions(raw, id_map)
        assert len(decisions) >= 1, (
            f"Expected at least 1 decision, got {len(decisions)}. "
            f"LLM output:\n{raw}"
        )
        for nid, score in decisions.items():
            assert score in (0, 1), (
                f"Decision must be 0 (DISCARD) or 1 (KEEP), got {score}"
            )

    def test_validate_extracts_answer(self, llm, graph_with_nodes):
        """包含可靠节点的 graph → LLM 应能提取 ANSWER 或返回 NONE"""
        from unittest.mock import MagicMock
        graph, n1 = graph_with_nodes
        graph.update_scores({n1: 1})  # Mark as KEEP

        memory = MagicMock()
        state = {
            "question": "改签规则是什么？",
            "graph": graph.to_dict(),
        }
        memory.read.return_value = state

        assembler = ValidationAssembler(memory=memory)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "validate_graph",
                "trigger": "evidence_complete",
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)

        # ANSWER: NONE 是合法输出（证据不足），ANSWER: <text> 也是合法输出（能回答）
        assert "ANSWER:" in raw, (
            f"Validator output should contain ANSWER: line. LLM output:\n{raw}"
        )

        answer = parse_validator_answer(raw)
        # answer 可以是 None (ANSWER: NONE) 或 str (具体答案)
        # 两者都是合法输出
        if answer is not None:
            assert len(answer) > 0, f"Answer should not be empty string"

    def test_validate_output_has_required_sections(self, llm, graph_with_nodes):
        """验证 LLM 输出包含 structure/semantic/comprehensive/rethink 分析段"""
        from unittest.mock import MagicMock
        graph, n1 = graph_with_nodes

        memory = MagicMock()
        state = {
            "question": "金卡会员有什么权益？",
            "graph": graph.to_dict(),
        }
        memory.read.return_value = state

        assembler = ValidationAssembler(memory=memory)
        ctx = AssemblyContext(
            user_request=UserRequest(text="", metadata={
                "task": "validate_graph",
                "trigger": "evidence_complete",
            }),
        )
        messages = assembler.assemble(ctx)
        raw = _call_llm(llm, messages)

        # topic_code validator 要求四个分析维度
        for tag in ["<structure>", "<semantic>", "<comprehensive>", "<rethink>"]:
            assert tag in raw, (
                f"Missing required validator section {tag}. LLM output:\n{raw}"
            )
