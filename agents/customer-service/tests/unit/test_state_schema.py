"""Unit tests for QA loop state schema."""
import pytest
from shared.state_schema import (
    create_initial_state,
    validate_state,
    StateValidationError,
)


class TestCreateInitialState:

    def test_has_required_fields(self):
        state = create_initial_state(question="测试问题", max_hops=4, K=2, top_k=5)
        required = ["question", "round", "max_hops", "phase", "expandable",
                    "graph", "pending", "K", "top_k_retrieve",
                    "tried_candidates", "answer", "sources"]
        for key in required:
            assert key in state, f"Missing key: {key}"

    def test_initial_round_is_one(self):
        state = create_initial_state(question="测试")
        assert state["round"] == 1

    def test_initial_phase_is_direction(self):
        state = create_initial_state(question="测试")
        assert state["phase"] == "direction"

    def test_initial_expandable_is_root(self):
        state = create_initial_state(question="测试")
        assert state["expandable"] == ["ROOT"]

    def test_graph_has_root_node(self):
        state = create_initial_state(question="测试")
        nodes = {n["id"] for n in state["graph"]["nodes"]}
        assert "ROOT" in nodes

    def test_pending_is_zeroed(self):
        state = create_initial_state(question="测试")
        assert state["pending"] == {"total": 0, "received": 0, "results": []}

    def test_validate_accepts_valid_state(self):
        state = create_initial_state(question="测试")
        validate_state(state)


class TestValidateState:

    def test_rejects_missing_question(self):
        state = create_initial_state(question="测试")
        del state["question"]
        with pytest.raises(StateValidationError, match="question"):
            validate_state(state)

    def test_rejects_negative_round(self):
        state = create_initial_state(question="测试")
        state["round"] = 0
        with pytest.raises(StateValidationError, match="round"):
            validate_state(state)

    def test_rejects_invalid_phase(self):
        state = create_initial_state(question="测试")
        state["phase"] = "invalid_phase"
        with pytest.raises(StateValidationError, match="phase"):
            validate_state(state)
