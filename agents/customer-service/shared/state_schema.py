"""QA loop shared state schema — creation, validation, and helpers."""
from shared.subgraph_manager import SubGraphManager


VALID_PHASES = {"idle", "direction", "evidence", "validation", "done"}


class StateValidationError(ValueError):
    """Raised when QA loop state fails validation."""
    pass


def create_initial_state(
    question: str,
    max_hops: int = 4,
    K: int = 2,
    top_k: int = 5,
) -> dict:
    """Create the initial qa_state dict for a new QA loop."""
    graph = SubGraphManager()
    return {
        "question": question,
        "round": 1,
        "max_hops": max_hops,
        "phase": "direction",
        "expandable": ["ROOT"],
        "graph": graph.to_dict(),
        "pending": {"total": 0, "received": 0, "results": []},
        "K": K,
        "top_k_retrieve": top_k,
        "tried_candidates": {},
        "answer": None,
        "sources": None,
    }


def validate_state(state: dict) -> None:
    """Raise StateValidationError if state is structurally invalid."""
    if not isinstance(state.get("question"), str) or not state["question"]:
        raise StateValidationError("question is required and must be a non-empty string")
    if not isinstance(state.get("round"), int) or state["round"] < 1:
        raise StateValidationError("round must be >= 1")
    if state.get("phase") not in VALID_PHASES:
        raise StateValidationError(f"phase must be one of {VALID_PHASES}, got {state.get('phase')}")
    if not isinstance(state.get("expandable"), list):
        raise StateValidationError("expandable must be a list")
    if not isinstance(state.get("graph"), dict):
        raise StateValidationError("graph must be a dict")
    if not isinstance(state.get("pending"), dict):
        raise StateValidationError("pending must be a dict")
    if not isinstance(state.get("max_hops"), int) or state["max_hops"] < 1:
        raise StateValidationError("max_hops must be >= 1")
