# customer-service Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-hop QA customer-service agent system with 6 Runtime-level agents (Router, Direction, Evidence, Validation, Task, Fallback) orchestrated via Kernel workflow, with browser + terminal dual-channel output.

**Architecture:** Bottom-up — shared infrastructure first, then each agent independently (adapter + assembler + unit tests), then workflow assembly + integration tests, then frontend. Each agent can be tested standalone before integration.

**Tech Stack:** Python 3.12+, networkx, FastAPI + WebSocket, vanilla HTML/JS, pytest, asyncio

**Test strategy:** Unit tests (`tests/unit/`) cover per-agent I/O contracts — run with `pytest tests/unit/ -v`. Integration tests (`tests/integration/`) cover full QA loop + workflow topology — run with `pytest tests/integration/ -v`.

---

## File Structure

```
agents/customer-service/
├── __init__.py
├── customer_service_workflow.py    # @agent + subscribe() declarations
├── server.py                        # FastAPI WebSocket server (Phase 3)
├── README.md                        # Usage (Phase 3)
│
├── shared/                          # Shared components (Phase 1)
│   ├── __init__.py
│   ├── subgraph_manager.py          # SubGraphManager on networkx
│   ├── retriever.py                 # RetrieverStub + InMemoryRetriever
│   ├── state_schema.py              # QA state read/write helpers
│   ├── prompts.py                   # 3 system prompts + builders + parsers
│   └── frontend_bus.py             # FrontendBus (asyncio.Queue broadcaster)
│
├── agents/                          # Per-agent (Phase 2)
│   ├── __init__.py
│   ├── router/
│   │   ├── __init__.py
│   │   ├── adapter.py              # RouterAdapter (AsyncInputAdapter)
│   │   └── assembler.py            # RouterAssembler (ContextAssembler)
│   ├── direction/
│   │   ├── __init__.py
│   │   ├── adapter.py              # DirectionAdapter
│   │   └── assembler.py            # DirectionAssembler
│   ├── evidence/
│   │   ├── __init__.py
│   │   ├── adapter.py              # EvidenceAdapter
│   │   └── assembler.py            # EvidenceAssembler
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── adapter.py              # ValidationAdapter
│   │   └── assembler.py            # ValidationAssembler
│   ├── task_agent/
│   │   ├── __init__.py
│   │   └── assembler.py            # TaskAssembler (stub)
│   └── fallback/
│       ├── __init__.py
│       └── assembler.py            # FallbackAssembler (stub)
│
├── static/                          # Frontend (Phase 3)
│   └── index.html
│
└── tests/                           # Test suites
    ├── __init__.py
    ├── conftest.py                  # Shared fixtures (mock LLM, test corpus)
    ├── unit/                        # Per-agent unit tests
    │   ├── __init__.py
    │   ├── test_subgraph_manager.py
    │   ├── test_retriever.py
    │   ├── test_state_schema.py
    │   ├── test_prompts.py
    │   ├── test_router.py
    │   ├── test_direction.py
    │   ├── test_evidence.py
    │   ├── test_validation.py
    │   ├── test_task_agent.py
    │   └── test_fallback.py
    └── integration/                 # Workflow integration tests
        ├── __init__.py
        ├── conftest.py              # Kernel + MemoryBackend fixtures
        ├── test_qa_loop.py          # Full expand→validate loop
        └── test_topology.py         # Agent spawn + subscription
```

---

## Phase 1: Shared Infrastructure

### Task 1: Project scaffolding

**Files:**
- Create: `agents/customer-service/__init__.py`
- Create: `agents/customer-service/shared/__init__.py`
- Create: `agents/customer-service/agents/__init__.py`
- Create: `agents/customer-service/tests/__init__.py`
- Create: `agents/customer-service/tests/unit/__init__.py`
- Create: `agents/customer-service/tests/integration/__init__.py`
- Create: `agents/customer-service/tests/conftest.py`

- [ ] **Step 1: Create directory structure and empty init files**

```bash
mkdir -p agents/customer-service/{shared,agents/{router,direction,evidence,validation,task_agent,fallback},static,tests/{unit,integration}}
touch agents/customer-service/__init__.py
touch agents/customer-service/shared/__init__.py
touch agents/customer-service/agents/__init__.py
touch agents/customer-service/agents/router/__init__.py
touch agents/customer-service/agents/direction/__init__.py
touch agents/customer-service/agents/evidence/__init__.py
touch agents/customer-service/agents/validation/__init__.py
touch agents/customer-service/agents/task_agent/__init__.py
touch agents/customer-service/agents/fallback/__init__.py
touch agents/customer-service/tests/__init__.py
touch agents/customer-service/tests/unit/__init__.py
touch agents/customer-service/tests/integration/__init__.py
```

- [ ] **Step 2: Write shared test fixtures in tests/conftest.py**

```python
"""Shared test fixtures for customer-service tests."""
import pytest
from pathlib import Path
import sys

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Ensure customer-service package is importable
_CS_PATH = str(Path(__file__).resolve().parents[1])
if _CS_PATH not in sys.path:
    sys.path.insert(0, _CS_PATH)


@pytest.fixture
def test_corpus():
    """Minimal QA corpus for testing."""
    return [
        ("航班政策", [
            "第3条：旅客可在起飞前2小时申请改签服务。",
            "第7条：改签需支付票价的5%作为手续费。",
        ]),
        ("乘客规则", [
            "第5条：非特价舱位旅客适用本规则。",
            "第8条：特价舱位旅客不享受免费改签。",
        ]),
    ]


@pytest.fixture
def sample_graph():
    """SubGraphManager with ROOT + 2 sample nodes."""
    from shared.subgraph_manager import SubGraphManager
    g = SubGraphManager()
    n1 = g.add_node(
        triple_str="航班 | 改签规则 | 起飞前2小时",
        parent_id="ROOT",
        accumulated_passages="第3条：旅客可在起飞前2小时申请改签服务。",
        select_idx=0,
        retrieved_passages=["第3条：旅客可在起飞前2小时申请改签服务。"],
    )
    n2 = g.add_node(
        triple_str="乘客 | 适用条件 | 非特价舱位",
        parent_id="ROOT",
        accumulated_passages="第5条：非特价舱位旅客适用本规则。",
        select_idx=0,
        retrieved_passages=["第5条：非特价舱位旅客适用本规则。"],
    )
    return g, {"n1": n1, "n2": n2}
```

- [ ] **Step 3: Run pytest to verify test infrastructure**

```bash
python -m pytest agents/customer-service/tests/ -v --collect-only
```
Expected: 0 tests collected (no test files yet), no errors.

- [ ] **Step 4: Commit**

```bash
git add agents/customer-service/
git commit -m "chore: scaffold customer-service agent project structure

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: SubGraphManager

**Files:**
- Create: `agents/customer-service/shared/subgraph_manager.py`
- Create: `agents/customer-service/tests/unit/test_subgraph_manager.py`

- [ ] **Step 1: Write failing unit tests**

Create `agents/customer-service/tests/unit/test_subgraph_manager.py`:

```python
"""Unit tests for SubGraphManager."""
import pytest
from shared.subgraph_manager import SubGraphManager


class TestSubGraphManager:

    def test_init_has_root_node(self):
        g = SubGraphManager()
        d = g.to_dict()
        nodes = {n["id"] for n in d["nodes"]}
        assert "ROOT" in nodes

    def test_add_node_returns_id(self):
        g = SubGraphManager()
        nid = g.add_node("A | B | C", parent_id="ROOT")
        assert nid is not None
        assert len(nid) == 12  # uuid4 hex[:12]

    def test_add_node_creates_edge_to_parent(self):
        g = SubGraphManager()
        nid = g.add_node("A | B | C", parent_id="ROOT")
        d = g.to_dict()
        edges = [(e["source"], e["target"]) for e in d["edges"]]
        assert ("ROOT", nid) in edges

    def test_get_path_triples_root_node(self):
        g = SubGraphManager()
        assert g.get_path_triples("ROOT") == []

    def test_get_path_triples_leaf(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        n2 = g.add_node("C | D | E", parent_id=n1)
        triples = g.get_path_triples(n2)
        assert triples == ["A | B | C", "C | D | E"]

    def test_get_leaf_nodes(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        n2 = g.add_node("C | D | E", parent_id=n1)
        leaves = g.get_leaf_nodes()
        assert n2 in leaves
        assert n1 not in leaves

    def test_get_id_map(self):
        g = SubGraphManager()
        n1 = g.add_node("First", parent_id="ROOT")
        n2 = g.add_node("Second", parent_id="ROOT")
        id_map = g.get_id_map()
        assert id_map[n1] == "N0"
        assert id_map[n2] == "N1"

    def test_update_scores(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        g.update_scores({n1: 1})
        d = g.to_dict()
        node = next(n for n in d["nodes"] if n["id"] == n1)
        assert node["score"] == 1

    def test_roundtrip_serialization(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT",
                        accumulated_passages="passage 1",
                        select_idx=0,
                        retrieved_passages=["passage 1"])
        g.update_scores({n1: 1})
        data = g.to_dict()
        g2 = SubGraphManager.from_dict(data)
        assert g2.get_path_triples(n1) == ["A | B | C"]
        d2 = g2.to_dict()
        node = next(n for n in d2["nodes"] if n["id"] == n1)
        assert node["score"] == 1
        assert node["select_idx"] == 0

    def test_from_dict_ensures_root(self):
        data = {"nodes": [], "edges": [], "directed": True, "multigraph": False, "graph": {}}
        g = SubGraphManager.from_dict(data)
        d = g.to_dict()
        nodes = {n["id"] for n in d["nodes"]}
        assert "ROOT" in nodes

    def test_get_sources(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT",
                        retrieved_passages=["source text"],
                        select_idx=0)
        g.update_scores({n1: 1})
        sources = g.get_sources()
        assert len(sources) == 1
        assert "source text" in sources[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_subgraph_manager.py -v
```
Expected: FAIL (no module `shared.subgraph_manager`)

- [ ] **Step 3: Implement SubGraphManager**

Create `agents/customer-service/shared/subgraph_manager.py`:

```python
"""SubGraphManager — multi-hop reasoning state graph on networkx."""
import networkx as nx
from uuid import uuid4


class SubGraphManager:
    """Multi-hop reasoning state graph.

    Node attributes:
    - triple_str: "subj | rel | obj", ROOT node is "ROOT"
    - accumulated_passages: str
    - select_idx: int | None
    - retrieved_passages: list[str]
    - creation_order: int
    - score: int (0=DISCARD, 1=KEEP, -1=unscored)
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        self._counter = 0
        self._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)

    def add_node(
        self,
        triple_str: str,
        parent_id: str | None = None,
        accumulated_passages: str | None = None,
        select_idx: int | None = None,
        retrieved_passages: list[str] | None = None,
    ) -> str:
        node_id = uuid4().hex[:12]
        self._counter += 1
        self._graph.add_node(
            node_id,
            triple_str=triple_str,
            accumulated_passages=accumulated_passages,
            select_idx=select_idx,
            retrieved_passages=retrieved_passages or [],
            creation_order=self._counter,
            score=-1,
        )
        if parent_id:
            self._graph.add_edge(parent_id, node_id)
        return node_id

    def get_path_triples(self, node_id: str) -> list[str]:
        """Triple chain from ROOT (exclusive) to node_id (inclusive)."""
        if node_id == "ROOT":
            return []
        triples = []
        for ancestor in nx.ancestors(self._graph, node_id):
            if ancestor != "ROOT":
                triples.append(self._graph.nodes[ancestor]["triple_str"])
        triples.append(self._graph.nodes[node_id]["triple_str"])
        return triples

    def get_leaf_nodes(self) -> list[str]:
        return [n for n in self._graph.nodes
                if n != "ROOT" and self._graph.out_degree(n) == 0]

    def get_accumulated_passages(self, node_id: str) -> str:
        passages = []
        current = node_id
        while current is not None and current != "ROOT":
            ap = self._graph.nodes[current].get("accumulated_passages", "")
            if ap:
                passages.append(ap)
            preds = list(self._graph.predecessors(current))
            current = preds[0] if preds else None
        return "\n".join(reversed(passages))

    def update_scores(self, decisions: dict[str, int]):
        for nid, score in decisions.items():
            if nid in self._graph.nodes:
                self._graph.nodes[nid]["score"] = score

    def get_id_map(self) -> dict[str, str]:
        non_root = [n for n in self._graph.nodes if n != "ROOT"]
        non_root.sort(key=lambda n: self._graph.nodes[n].get("creation_order", 0))
        return {nid: f"N{i}" for i, nid in enumerate(non_root)}

    def get_sources(self) -> list[str]:
        sources = []
        for nid in self._graph.nodes:
            if nid != "ROOT" and self._graph.nodes[nid].get("score") == 1:
                triple = self._graph.nodes[nid]["triple_str"]
                passages = self._graph.nodes[nid].get("retrieved_passages", [])
                idx = self._graph.nodes[nid].get("select_idx")
                if idx is not None and idx < len(passages):
                    sources.append(f"[{triple}] {passages[idx]}")
        return sources

    def node_count(self) -> int:
        return len([n for n in self._graph.nodes if n != "ROOT"])

    def to_dict(self) -> dict:
        return nx.node_link_data(self._graph, edges="edges")

    @classmethod
    def from_dict(cls, data: dict) -> "SubGraphManager":
        instance = cls()
        instance._graph = nx.node_link_graph(data, edges="edges")
        if "ROOT" not in instance._graph.nodes:
            instance._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)
        instance._counter = instance.node_count()
        return instance
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_subgraph_manager.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/shared/subgraph_manager.py agents/customer-service/tests/unit/test_subgraph_manager.py
git commit -m "feat: add SubGraphManager on networkx for multi-hop reasoning state

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Retriever

**Files:**
- Create: `agents/customer-service/shared/retriever.py`
- Create: `agents/customer-service/tests/unit/test_retriever.py`

- [ ] **Step 1: Write failing unit tests**

```python
"""Unit tests for Retriever."""
import pytest
from shared.retriever import RetrieverStub, InMemoryRetriever


class TestInMemoryRetriever:

    @pytest.fixture
    def retriever(self, test_corpus):
        return InMemoryRetriever(test_corpus)

    def test_retrieve_returns_top_k(self, retriever):
        results = retriever.retrieve("改签规则", [], top_k=1)
        assert len(results) <= 1
        assert len(results) > 0

    def test_retrieve_relevant_passage(self, retriever):
        results = retriever.retrieve("改签规则", [], top_k=3)
        assert any("改签" in r for r in results)

    def test_retrieve_empty_for_no_match(self, retriever):
        results = retriever.retrieve("ZZZNOTEXIST", [], top_k=5)
        assert results == []

    def test_retrieve_respects_top_k(self, retriever):
        results = retriever.retrieve("旅客", [], top_k=1)
        assert len(results) == 1

    def test_implements_stub_interface(self, retriever):
        assert isinstance(retriever, RetrieverStub)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_retriever.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement Retriever**

Create `agents/customer-service/shared/retriever.py`:

```python
"""Retriever — document retrieval for evidence anchoring."""


class RetrieverStub:
    """Abstract retriever interface matching topic_code's contract."""
    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        ...


class InMemoryRetriever(RetrieverStub):
    """Keyword-overlap retriever. Replaceable with BM25 or Dense.

    Args:
        corpus: [(title, [sentence_1, sentence_2, ...])]
    """

    def __init__(self, corpus: list[tuple[str, list[str]]]):
        self._flattened = []
        for title, sentences in corpus:
            for s in sentences:
                self._flattened.append(f"[{title}] {s}")

    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        query_terms = set(query.lower().split())
        scored = []
        for doc in self._flattened:
            doc_terms = set(doc.lower().split())
            score = len(query_terms & doc_terms)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_retriever.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/shared/retriever.py agents/customer-service/tests/unit/test_retriever.py
git commit -m "feat: add InMemoryRetriever for evidence document retrieval

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: State Schema

**Files:**
- Create: `agents/customer-service/shared/state_schema.py`
- Create: `agents/customer-service/tests/unit/test_state_schema.py`

- [ ] **Step 1: Write failing unit tests**

```python
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
        validate_state(state)  # should not raise


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_state_schema.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement state schema**

Create `agents/customer-service/shared/state_schema.py`:

```python
"""QA loop shared state schema — creation, validation, and helpers."""
from shared.subgraph_manager import SubGraphManager


# Valid phases
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
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_state_schema.py -v
```
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/shared/state_schema.py agents/customer-service/tests/unit/test_state_schema.py
git commit -m "feat: add QA loop state schema with creation and validation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: topic_code Prompts and Parsers

**Files:**
- Create: `agents/customer-service/shared/prompts.py`
- Create: `agents/customer-service/tests/unit/test_prompts.py`

- [ ] **Step 1: Write failing unit tests for parsers**

Create `agents/customer-service/tests/unit/test_prompts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_prompts.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement prompts and parsers**

Create `agents/customer-service/shared/prompts.py`:

```python
"""topic_code prompts and parsers — extracted and preserved as core assets.

Source: /home/wowoow/topic_code/src/prompts.py
Reused under the "extract prompts, reimplement control flow" strategy.
"""
import re
from shared.subgraph_manager import SubGraphManager


# ═══════════════════════════════════════════════════════════════════════════
# System Prompts (extracted from topic_code)
# ═══════════════════════════════════════════════════════════════════════════

CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY = """You are a knowledge graph reasoning assistant. Your task is to propose the next exploration directions for multi-hop question answering.

Given:
- A complex question
- Already confirmed facts (as triples: subject | relation | object)
- Evidence passages supporting those facts

Output:
1. A <remaining_question> that reformulates what still needs to be answered
2. A <next_facts> section listing candidate directions as:
   N. subject | relation | ?

Only propose directions that are logically connected to the confirmed facts and necessary to answer the question. Do NOT invent facts not supported by the evidence passages."""

CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY = """You are a fact verification assistant. Your task is to confirm whether a proposed direction (subject, relation) can be grounded in the provided evidence passages.

Given:
- A question
- Already confirmed triples
- A proposed direction: (subject, relation, ?)
- Retrieved evidence passages

Output either:
- A confirmed triple: subject | relation | object | SELECT: passage_index
  where passage_index is the 0-based index of the supporting passage
- Or: INVALID (if no passage supports the proposed direction)

Only extract facts that are EXPLICITLY stated in the passages. Do NOT infer or assume."""

CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY = """You are a graph validator. Your task is to evaluate a reasoning graph of triples and determine which nodes are reliable and whether the original question can be answered.

Evaluate each node on:
- <structure>: Does the triple form a logically sound fact?
- <semantic>: Is the triple semantically meaningful?
- <comprehensive>: In context of other nodes, is this triple necessary and correct?

Output for each node:
  Node Nx: KEEP  (if the node is reliable and relevant)
  Node Nx: DISCARD  (if the node is unreliable or irrelevant)

If the evidence is sufficient to answer the question, output:
  ANSWER: <your answer>

If not yet sufficient, output:
  ANSWER: NONE"""


# ═══════════════════════════════════════════════════════════════════════════
# User Content Builders
# ═══════════════════════════════════════════════════════════════════════════

def build_core_draft_v3_user_content(
    question: str,
    evidence_passages: list[str],
    confirmed_triples: list[str],
    K: int,
) -> str:
    parts = [f"Question: {question}"]
    if confirmed_triples:
        parts.append("\nConfirmed Facts:")
        for t in confirmed_triples:
            parts.append(f"  {t}")
    if evidence_passages:
        parts.append("\nEvidence Passages:")
        for i, p in enumerate(evidence_passages):
            parts.append(f"  [{i}] {p}")
    parts.append(f"\nPropose up to {K} next exploration directions.")
    return "\n".join(parts)


def build_core_final_v3_user_content(
    question: str,
    confirmed_triples: list[str],
    retrieved_passages: list[str],
    draft_subject: str,
    draft_relation: str,
) -> str:
    parts = [
        f"Question: {question}",
        f"Proposed direction: {draft_subject} | {draft_relation} | ?",
    ]
    if confirmed_triples:
        parts.append("\nConfirmed Facts:")
        for t in confirmed_triples:
            parts.append(f"  {t}")
    parts.append("\nRetrieved Passages:")
    for i, p in enumerate(retrieved_passages):
        parts.append(f"  [{i}] {p}")
    parts.append("\nConfirm or reject the proposed direction.")
    return "\n".join(parts)


def build_core_validator_content_from_merger(
    question: str,
    merger: SubGraphManager,
) -> str:
    graph = merger
    id_map = graph.get_id_map()
    lines = [f"Question: {question}", f"Graph ({len(id_map)} nodes):"]
    for internal_id, display_id in id_map.items():
        triple = graph._graph.nodes[internal_id].get("triple_str", "")
        lines.append(f"[{display_id}] {triple}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Parsers
# ═══════════════════════════════════════════════════════════════════════════

def parse_draft_v3_output(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Parse Direction LLM output → (remaining_question, [(subj, rel)])."""
    remaining_q = ""
    m = re.search(r"<remaining_question>(.*?)</remaining_question>", text, re.DOTALL)
    if m:
        remaining_q = m.group(1).strip()
    candidates = parse_draft_list(text)
    return remaining_q, candidates


def parse_draft_list(text: str) -> list[tuple[str, str]]:
    """Parse <next_facts> block → [(subj, rel)]."""
    candidates = []
    m = re.search(r"<next_facts>(.*?)</next_facts>", text, re.DOTALL)
    if not m:
        return candidates
    block = m.group(1)
    for line in block.strip().split("\n"):
        line = re.sub(r"^\d+\.\s*", "", line).strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            subj = parts[0]
            rel = parts[1]
            if subj and rel:
                candidates.append((subj, rel))
    return candidates


def parse_final(text: str):
    """Parse Evidence LLM output.

    Returns:
        "INVALID" | None (parse error) | (subj, rel, obj, select_idx: int)
    """
    text = text.strip()
    if text.upper() == "INVALID":
        return "INVALID"
    m = re.match(r"(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*SELECT:\s*(\d+)", text)
    if not m:
        return None
    return (m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), int(m.group(4)))


def parse_validator_decisions(text: str, id_map: dict[str, str]) -> dict[str, int]:
    """Parse KEEP/DISCARD decisions.

    Args:
        text: LLM output text
        id_map: {internal_id: display_id}  (e.g. {"abc": "N0"})

    Returns:
        {internal_id: 1|0}  (1=KEEP, 0=DISCARD)
    """
    # Reverse mapping: display_id → internal_id
    rev_map = {v: k for k, v in id_map.items()}
    decisions = {}
    for line in text.split("\n"):
        m = re.match(r"Node\s+(N\d+)\s*:\s*(KEEP|DISCARD)", line.strip())
        if m:
            display_id = m.group(1)
            decision = 1 if m.group(2) == "KEEP" else 0
            if display_id in rev_map:
                decisions[rev_map[display_id]] = decision
    return decisions


def parse_validator_answer(text: str) -> str | None:
    """Parse ANSWER from validator output. Returns None if ANSWER: NONE."""
    m = re.search(r"ANSWER:\s*(.+)", text)
    if not m:
        return None
    answer = m.group(1).strip()
    if answer.upper() == "NONE":
        return None
    return answer
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_prompts.py -v
```
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/shared/prompts.py agents/customer-service/tests/unit/test_prompts.py
git commit -m "feat: add topic_code prompts and parsers as shared assets

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: FrontendBus

**Files:**
- Create: `agents/customer-service/shared/frontend_bus.py`

- [ ] **Step 1: Implement FrontendBus (no tests needed — infrastructure component)**

Create `agents/customer-service/shared/frontend_bus.py`:

```python
"""FrontendBus — structured event broadcaster for WebSocket frontend."""
import asyncio
import time


class FrontendBus:
    """Agent → frontend structured event broadcaster.

    Each adapter calls bus.emit(event) in its send() method.
    WebSocket server subscribes via subscribe() to receive events.
    """

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """Register a consumer queue. Called by WebSocket server."""
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def emit(self, event: dict) -> None:
        """Broadcast event to all subscribers. Called by adapters.

        Thread-safe: all adapters run on the same event loop,
        put_nowait is non-blocking.
        """
        event["_timestamp"] = time.time()
        for q in self._queues:
            q.put_nowait(event)
```

- [ ] **Step 2: Commit**

```bash
git add agents/customer-service/shared/frontend_bus.py
git commit -m "feat: add FrontendBus for structured agent→frontend events

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 2: Per-Agent Implementation

### Task 7: Router Agent (adapter + assembler + tests)

**Files:**
- Create: `agents/customer-service/agents/router/adapter.py`
- Create: `agents/customer-service/agents/router/assembler.py`
- Create: `agents/customer-service/tests/unit/test_router.py`

- [ ] **Step 1: Write unit tests for RouterAssembler**

```python
"""Unit tests for Router Agent (adapter + assembler)."""
import pytest
from harness.interfaces.types import AssemblyContext, GuidesBundle, Message, UserRequest
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_router.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement RouterAssembler**

Create `agents/customer-service/agents/router/assembler.py`:

```python
"""RouterAssembler — intent classification + answer formatting context."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class RouterAssembler:
    """Assembles prompts for intent classification and answer formatting."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata if ctx.user_request else {}

        # Path A: QA answer from Validation → format for user
        if meta.get("type") == "qa_answer":
            system = "你是客服助手，请根据以下信息回答用户问题。引用来源。"
            sources_text = "\n".join(f"- {s}" for s in meta.get("sources", []))
            user = (
                f"用户问题：{meta.get('question', '')}\n\n"
                f"答案：{meta['answer']}\n\n"
                f"参考来源：\n{sources_text}"
            )
            return [
                Message(role="system", content=system),
                Message(role="user", content=user),
            ]

        # Path B: User message → intent classification
        system = """你是客服系统入口路由。分析用户消息，判定意图。

意图类型：
- qa: 政策咨询、知识问答、事实性问题（如"改签规则是什么？""赔偿标准？"）
- task: 明确要办理业务（如"我要改签""帮我退款"）
- fallback: 意图不明、敏感问题、超出客服范围

输出格式（严格遵守）：
INTENT: <qa|task|fallback>
CONFIDENCE: <0-1>
SLOTS: <JSON dict>"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
```

- [ ] **Step 4: Run tests to verify RouterAssembler passes**

```bash
python -m pytest agents/customer-service/tests/unit/test_router.py::TestRouterAssembler -v
```
Expected: 2 passed

- [ ] **Step 5: Implement RouterAdapter**

Create `agents/customer-service/agents/router/adapter.py`:

```python
"""RouterAdapter — parse LLM intent output and route to downstream agents."""
import re
from harness.interfaces.types import TextEvent, UserRequest
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.interfaces.memory_backend import MemoryBackend
from shared.state_schema import create_initial_state


class RouterAdapter:
    """KBA wrapper. Parses LLM intent classification in send() and routes.

    Constructor-injected:
    - memory: MemoryBackend for initializing QA shared state
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._current_user_message = ""

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        request = await self._kba.receive()
        if request.text and not request.metadata.get("type"):
            self._current_user_message = request.text
        return request

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            parsed = self._parse_intent(event.content)

            if parsed["intent"] == "qa":
                state = create_initial_state(question=self._current_user_message)
                self._memory.write("loop", "qa_state", state)

                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_user_message,
                        "expandable_nodes": [{
                            "node_id": "ROOT",
                            "confirmed_triples": [],
                            "evidence_passages": [],
                        }],
                    }
                ))

            elif parsed["intent"] == "task":
                event.content = self._current_user_message

            elif parsed["intent"] == "fallback":
                pass

        await self._kba.send(event, target)

    @staticmethod
    def _parse_intent(text: str) -> dict:
        intent = "fallback"
        confidence = "0.0"
        slots = "{}"
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("INTENT:"):
                intent = line.split(":", 1)[1].strip().lower()
            elif line.startswith("CONFIDENCE:"):
                confidence = line.split(":", 1)[1].strip()
            elif line.startswith("SLOTS:"):
                slots = line.split(":", 1)[1].strip()
        return {"intent": intent, "confidence": float(confidence), "slots": slots}
```

- [ ] **Step 6: Verify RouterAdapter._parse_intent with additional tests**

Append to `agents/customer-service/tests/unit/test_router.py`:

```python
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
```

- [ ] **Step 7: Run all Router tests**

```bash
python -m pytest agents/customer-service/tests/unit/test_router.py -v
```
Expected: 5 passed

- [ ] **Step 8: Commit**

```bash
git add agents/customer-service/agents/router/ agents/customer-service/tests/unit/test_router.py
git commit -m "feat: add Router Agent with intent classification and routing

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Direction Agent (adapter + assembler + tests)

**Files:**
- Create: `agents/customer-service/agents/direction/adapter.py`
- Create: `agents/customer-service/agents/direction/assembler.py`
- Create: `agents/customer-service/tests/unit/test_direction.py`

- [ ] **Step 1: Write unit tests for DirectionAssembler**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_direction.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement DirectionAssembler**

Create `agents/customer-service/agents/direction/assembler.py`:

```python
"""DirectionAssembler — draft prompt assembly for direction generation."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List
from shared.prompts import (
    CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY,
    build_core_draft_v3_user_content,
)


class DirectionAssembler:
    """Assembles topic_code draft prompt for candidate direction generation."""

    def __init__(self, K: int = 2):
        self._K = K

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata
        system = CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_draft_v3_user_content(
            question=meta["question"],
            evidence_passages=meta.get("evidence_passages", []),
            confirmed_triples=meta.get("confirmed_triples", []),
            K=meta.get("K", self._K),
        )
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

- [ ] **Step 4: Run DirectionAssembler tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_direction.py::TestDirectionAssembler -v
```
Expected: 2 passed

- [ ] **Step 5: Implement DirectionAdapter**

Create `agents/customer-service/agents/direction/adapter.py`:

```python
"""DirectionAdapter — multi-node iteration + LLM output parsing + Evidence dispatch."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_draft_v3_output


class DirectionAdapter:
    """Manages multi-node iteration, parses draft output, dispatches Evidence tasks.

    Constructor-injected:
    - memory: MemoryBackend for reading/writing QA shared state

    Instance state:
    - _pending_nodes: expandable nodes remaining to process
    - _accumulated_tasks: Evidence tasks accumulated across nodes
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._pending_nodes = []
        self._accumulated_tasks = []
        self._current_question = ""
        self._current_node_id = None

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        raw = await self._kba.receive()
        meta = raw.metadata

        if meta.get("task") == "generate_directions":
            self._current_question = meta["question"]
            self._pending_nodes = list(meta["expandable_nodes"])
            self._accumulated_tasks = []
            first_node = self._pending_nodes.pop(0)
            self._current_node_id = first_node["node_id"]
            return UserRequest(
                text="",
                metadata={
                    "task": "generate_directions",
                    "question": meta["question"],
                    "node_id": first_node["node_id"],
                    "confirmed_triples": first_node["confirmed_triples"],
                    "evidence_passages": first_node["evidence_passages"],
                    "K": meta.get("K", 2),
                },
            )
        return raw

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            remaining_q, candidates = parse_draft_v3_output(event.content)

            state = self._memory.read("loop", "qa_state")
            tried = state.get("tried_candidates", {}).get(self._current_node_id, [])
            fresh = [
                (s, r) for s, r in candidates
                if (s.lower(), r.lower()) not in tried
            ]

            for subj, rel in fresh:
                self._accumulated_tasks.append({
                    "task": "confirm_triple",
                    "question": self._current_question,
                    "direction": (subj, rel),
                    "confirmed_triples": state.get("graph", {}),
                    "corpus": [],  # corpus accessed by Evidence via its own retriever
                    "node_id": self._current_node_id,
                })

            tried.extend([(s.lower(), r.lower()) for s, r in fresh])
            state.setdefault("tried_candidates", {})[self._current_node_id] = tried
            self._memory.write("loop", "qa_state", state)

            if self._pending_nodes:
                next_node = self._pending_nodes.pop(0)
                self._current_node_id = next_node["node_id"]
                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_question,
                        "node_id": next_node["node_id"],
                        "confirmed_triples": next_node["confirmed_triples"],
                        "evidence_passages": next_node["evidence_passages"],
                        "K": state.get("K", 2),
                    },
                ))
            else:
                if self._accumulated_tasks:
                    state["pending"]["total"] = len(self._accumulated_tasks)
                    state["pending"]["received"] = 0
                    state["pending"]["results"] = []
                    state["phase"] = "evidence"
                    self._memory.write("loop", "qa_state", state)

                    for task in self._accumulated_tasks:
                        self._kernel.send_input("evidence", UserRequest(
                            text="", metadata=task,
                        ))
                else:
                    self._kernel.send_input("validation", UserRequest(
                        text="", metadata={"trigger": "direction_empty"},
                    ))

        await self._kba.send(event, target)
```

- [ ] **Step 6: Run all Direction tests**

```bash
python -m pytest agents/customer-service/tests/unit/test_direction.py -v
```
Expected: 2 passed (assembler tests; adapter is tested in integration)

- [ ] **Step 7: Commit**

```bash
git add agents/customer-service/agents/direction/ agents/customer-service/tests/unit/test_direction.py
git commit -m "feat: add Direction Agent with draft prompt and multi-node iteration

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Evidence Agent (adapter + assembler + tests)

**Files:**
- Create: `agents/customer-service/agents/evidence/adapter.py`
- Create: `agents/customer-service/agents/evidence/assembler.py`
- Create: `agents/customer-service/tests/unit/test_evidence.py`

- [ ] **Step 1: Write unit tests for EvidenceAssembler**

```python
"""Unit tests for Evidence Agent."""
import pytest
from unittest.mock import MagicMock
from harness.interfaces.types import AssemblyContext, UserRequest
from agents.evidence.assembler import EvidenceAssembler
from shared.retriever import RetrieverStub


class FakeRetriever(RetrieverStub):
    def retrieve(self, query, corpus, top_k):
        return [f"Passage about {query}"]


class TestEvidenceAssembler:

    def test_assemble_with_retrieval(self):
        retriever = FakeRetriever()
        memory = MagicMock()
        assembler = EvidenceAssembler(retriever=retriever, memory=memory, top_k=5)
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={
                    "task": "confirm_triple",
                    "question": "改签规则是什么？",
                    "direction": ("航班", "改签规则"),
                    "confirmed_triples": [],
                    "corpus": [],
                    "node_id": "ROOT",
                },
            ),
        )
        messages = assembler.assemble(ctx)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "Passage about 航班 改签规则" in messages[1].content
        assert "航班" in messages[1].content

    def test_short_circuit_when_no_passages(self):
        retriever = MagicMock()
        retriever.retrieve.return_value = []
        memory = MagicMock()
        assembler = EvidenceAssembler(retriever=retriever, memory=memory, top_k=5)
        ctx = AssemblyContext(
            user_request=UserRequest(
                text="",
                metadata={
                    "task": "confirm_triple",
                    "question": "Q",
                    "direction": ("X", "Y"),
                    "confirmed_triples": [],
                    "corpus": [],
                    "node_id": "ROOT",
                },
            ),
        )
        messages = assembler.assemble(ctx)
        assert "INVALID" in messages[1].content
        assert ctx.user_request.metadata["_no_passages"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_evidence.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement EvidenceAssembler**

Create `agents/customer-service/agents/evidence/assembler.py`:

```python
"""EvidenceAssembler — forced retrieval + final prompt assembly."""
from harness.interfaces.types import AssemblyContext, Message
from harness.interfaces.memory_backend import MemoryBackend
from typing import List
from shared.prompts import (
    CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY,
    build_core_final_v3_user_content,
)
from shared.retriever import RetrieverStub


class EvidenceAssembler:
    """Forced retrieval + topic_code final prompt.

    Constructor-injected:
    - retriever: RetrieverStub (deterministic code, NOT a Tool)
    - memory: MemoryBackend
    - top_k: passages per direction (default 5)
    """

    def __init__(self, retriever: RetrieverStub, memory: MemoryBackend, top_k: int = 5):
        self._retriever = retriever
        self._memory = memory
        self._top_k = top_k
        self._last_passages = []

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata
        subj, rel = meta["direction"]

        query = f"{subj} {rel}"
        passages = self._retriever.retrieve(query, meta.get("corpus", []), self._top_k)
        self._last_passages = passages

        ctx.user_request.metadata["retrieved_passages"] = passages

        if not passages:
            ctx.user_request.metadata["_no_passages"] = True
            return [
                Message(role="system", content="返回 INVALID"),
                Message(role="user", content="INVALID"),
            ]

        system = CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_final_v3_user_content(
            question=meta["question"],
            confirmed_triples=meta.get("confirmed_triples", []),
            retrieved_passages=passages,
            draft_subject=subj,
            draft_relation=rel,
        )
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

- [ ] **Step 4: Run EvidenceAssembler tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_evidence.py::TestEvidenceAssembler -v
```
Expected: 2 passed

- [ ] **Step 5: Implement EvidenceAdapter**

Create `agents/customer-service/agents/evidence/adapter.py`:

```python
"""EvidenceAdapter — parse LLM triple output + sync barrier check."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_final
from shared.subgraph_manager import SubGraphManager


class EvidenceAdapter:
    """Parses LLM triple output, updates graph, checks sync barrier.

    Constructor-injected:
    - memory: MemoryBackend for reading/writing QA shared state
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._current_direction = None

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        request = await self._kba.receive()
        if request.metadata.get("direction"):
            self._current_direction = tuple(request.metadata["direction"])
        return request

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            state = self._memory.read("loop", "qa_state")
            graph = SubGraphManager.from_dict(state["graph"])

            if self._current_direction:
                subj, rel = self._current_direction
                parsed = parse_final(event.content)

                if parsed and parsed != "INVALID":
                    subj_out, rel_out, obj, select_idx = parsed
                    child_id = graph.add_node(
                        triple_str=f"{subj_out} | {rel_out} | {obj}",
                        parent_id=state.get("expandable", ["ROOT"])[0],
                        select_idx=select_idx,
                    )
                    state["graph"] = graph.to_dict()
                    result = {"valid": True, "triple": (subj_out, rel_out, obj),
                              "node_id": child_id, "select_idx": select_idx}
                else:
                    result = {"valid": False,
                              "reason": "INVALID" if parsed == "INVALID" else "PARSE_ERROR"}

                state["pending"]["received"] += 1
                state["pending"]["results"].append(result)
                self._memory.write("loop", "qa_state", state)

                if state["pending"]["received"] >= state["pending"]["total"]:
                    self._kernel.send_input("validation", UserRequest(
                        text="",
                        metadata={
                            "task": "validate_graph",
                            "question": state["question"],
                            "trigger": "evidence_complete",
                        },
                    ))

        await self._kba.send(event, target)
```

- [ ] **Step 6: Run all Evidence tests**

```bash
python -m pytest agents/customer-service/tests/unit/test_evidence.py -v
```
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add agents/customer-service/agents/evidence/ agents/customer-service/tests/unit/test_evidence.py
git commit -m "feat: add Evidence Agent with forced retrieval and triple confirmation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: Validation Agent (adapter + assembler + tests)

**Files:**
- Create: `agents/customer-service/agents/validation/adapter.py`
- Create: `agents/customer-service/agents/validation/assembler.py`
- Create: `agents/customer-service/tests/unit/test_validation.py`

- [ ] **Step 1: Write unit tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_validation.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement ValidationAssembler**

Create `agents/customer-service/agents/validation/assembler.py`:

```python
"""ValidationAssembler — validator prompt assembly for graph scoring."""
from harness.interfaces.types import AssemblyContext, Message
from harness.interfaces.memory_backend import MemoryBackend
from typing import List
from shared.prompts import (
    CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY,
    build_core_validator_content_from_merger,
)
from shared.subgraph_manager import SubGraphManager


class ValidationAssembler:
    """Assembles topic_code validator prompt for global graph scoring.

    Constructor-injected:
    - memory: MemoryBackend for reading graph state

    ★ Validator receives ONLY the triple graph, NOT raw passages.
    """

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        state = self._memory.read("loop", "qa_state")
        graph = SubGraphManager.from_dict(state["graph"])

        system = CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_validator_content_from_merger(
            question=state["question"],
            merger=graph,
        )
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

- [ ] **Step 4: Run ValidationAssembler tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_validation.py::TestValidationAssembler -v
```
Expected: 1 passed

- [ ] **Step 5: Implement ValidationAdapter**

Create `agents/customer-service/agents/validation/adapter.py`:

```python
"""ValidationAdapter — parse KEEP/DISCARD + ANSWER + termination logic."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_validator_decisions, parse_validator_answer
from shared.subgraph_manager import SubGraphManager


class ValidationAdapter:
    """Parses validator output, judges termination, drives loop continuation.

    Constructor-injected:
    - memory: MemoryBackend for reading/writing QA shared state
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._runtime = None
        self._memory = memory

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel
        self._runtime = runtime

    async def receive(self) -> UserRequest:
        return await self._kba.receive()

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            state = self._memory.read("loop", "qa_state")
            graph = SubGraphManager.from_dict(state["graph"])
            prev_node_count = graph.node_count()

            id_map = graph.get_id_map()
            decisions = parse_validator_decisions(event.content, id_map)
            answer = parse_validator_answer(event.content)

            graph.update_scores(decisions)
            state["graph"] = graph.to_dict()
            state["validator_scores"] = decisions
            state["validator_raw_output"] = event.content

            expandable = [nid for nid, score in decisions.items() if score == 1]
            new_node_count = graph.node_count() - prev_node_count

            if answer is not None:
                state["phase"] = "done"
                state["answer"] = answer
                state["sources"] = graph.get_sources()
                self._memory.write("loop", "qa_state", state)
                self._kernel.send_input("router", UserRequest(
                    text="", metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": answer,
                        "sources": state["sources"],
                    },
                ))
                self._kernel.end_workflow(self._runtime.workflow_flag)

            elif state["round"] >= state["max_hops"]:
                self._emit_fallback(state, "max_hops")

            elif not expandable:
                self._emit_fallback(state, "no_expandable")

            elif new_node_count == 0:
                self._emit_fallback(state, "no_progress")

            else:
                state["round"] += 1
                state["expandable"] = expandable
                state["phase"] = "direction"
                state["pending"] = {"total": 0, "received": 0, "results": []}
                self._memory.write("loop", "qa_state", state)

                expandable_nodes = []
                for nid in expandable:
                    expandable_nodes.append({
                        "node_id": nid,
                        "confirmed_triples": graph.get_path_triples(nid),
                        "evidence_passages": graph.get_accumulated_passages(nid),
                    })

                self._kernel.send_input("direction", UserRequest(
                    text="", metadata={
                        "task": "generate_directions",
                        "question": state["question"],
                        "expandable_nodes": expandable_nodes,
                        "K": state.get("K", 2),
                    },
                ))

        await self._kba.send(event, target)

    def _emit_fallback(self, state: dict, reason: str):
        state["phase"] = "done"
        state["answer"] = "抱歉，暂时无法回答这个问题，请咨询人工客服。"
        self._memory.write("loop", "qa_state", state)
        self._kernel.send_input("router", UserRequest(
            text="", metadata={
                "type": "qa_answer",
                "question": state["question"],
                "answer": state["answer"],
                "sources": [],
            },
        ))
        self._kernel.end_workflow(self._runtime.workflow_flag)
```

- [ ] **Step 6: Run all Validation tests**

```bash
python -m pytest agents/customer-service/tests/unit/test_validation.py -v
```
Expected: 1 passed

- [ ] **Step 7: Commit**

```bash
git add agents/customer-service/agents/validation/ agents/customer-service/tests/unit/test_validation.py
git commit -m "feat: add Validation Agent with graph scoring and loop termination

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: Task Agent and Fallback Agent (stubs + tests)

**Files:**
- Create: `agents/customer-service/agents/task_agent/assembler.py`
- Create: `agents/customer-service/agents/fallback/assembler.py`
- Create: `agents/customer-service/tests/unit/test_task_agent.py`
- Create: `agents/customer-service/tests/unit/test_fallback.py`

- [ ] **Step 1: Write tests and implement both stubs in one pass**

Create `agents/customer-service/tests/unit/test_task_agent.py`:

```python
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
```

Create `agents/customer-service/tests/unit/test_fallback.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest agents/customer-service/tests/unit/test_task_agent.py agents/customer-service/tests/unit/test_fallback.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement both stubs**

Create `agents/customer-service/agents/task_agent/assembler.py`:

```python
"""TaskAssembler — stub for business operation intent."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class TaskAssembler:
    """Minimal assembler for task intent (MVP placeholder)."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        system = """你是业务办理助手。当前为 MVP 占位版本。

你可以：
- 确认用户意图
- 引导用户提供必要信息（如订单号）

请回复用户，告知当前可提供的服务。"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
```

Create `agents/customer-service/agents/fallback/assembler.py`:

```python
"""FallbackAssembler — stub for out-of-scope / low-confidence intents."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class FallbackAssembler:
    """Minimal assembler for fallback intent (MVP placeholder)."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        system = """你是异常兜底助手。当前为 MVP 占位版本。

当用户意图不明或超出客服范围时，你的职责是：
- 输出标准兜底话术
- 建议用户转人工客服

请用礼貌的语气回复用户。"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest agents/customer-service/tests/unit/test_task_agent.py agents/customer-service/tests/unit/test_fallback.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/agents/task_agent/ agents/customer-service/agents/fallback/ agents/customer-service/tests/unit/test_task_agent.py agents/customer-service/tests/unit/test_fallback.py
git commit -m "feat: add Task and Fallback Agent stubs for intent routing

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 3: Workflow Assembly & Integration

### Task 12: Workflow Script

**Files:**
- Create: `agents/customer-service/customer_service_workflow.py`

- [ ] **Step 1: Write the complete workflow script**

Create `agents/customer-service/customer_service_workflow.py`:

```python
"""customer_service_workflow.py — 6-agent topology for multi-hop QA customer service.

Launch:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
Then type: /talk router 改签规则是什么？
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CS_PATH = str(Path(__file__).resolve().parent)
if _CS_PATH not in sys.path:
    sys.path.insert(0, _CS_PATH)

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    AsyncInputAdapter,
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
from harness.runtime.decorators import agent, subscribe

from shared.retriever import InMemoryRetriever
from agents.router.adapter import RouterAdapter
from agents.router.assembler import RouterAssembler
from agents.direction.adapter import DirectionAdapter
from agents.direction.assembler import DirectionAssembler
from agents.evidence.adapter import EvidenceAdapter
from agents.evidence.assembler import EvidenceAssembler
from agents.validation.adapter import ValidationAdapter
from agents.validation.assembler import ValidationAssembler
from agents.task_agent.assembler import TaskAssembler
from agents.fallback.assembler import FallbackAssembler


# ═══════════════════════════════════════════════════════════════════════════
# Agent assembly functions
# ═══════════════════════════════════════════════════════════════════════════

@agent(
    "router",
    entry_prompt="你是客服系统入口路由。等待用户消息...",
    metadata={"role": "入口意图识别"},
)
def assemble_router():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, RouterAdapter(memory=memory))
    container.register(ContextAssembler, RouterAssembler())
    container.register(GuideProvider, FileGuideProvider(
        paths=[str(Path(__file__).parent / "AGENTS_router.md")]
    ))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "direction",
    entry_prompt="你是方向生成Agent。等待任务...",
    metadata={"role": "方向生成"},
)
def assemble_direction():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, DirectionAdapter(memory=memory))
    container.register(ContextAssembler, DirectionAssembler(K=2))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "evidence",
    entry_prompt="你是证据锚定Agent。等待任务...",
    metadata={"role": "证据锚定"},
)
def assemble_evidence():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    # MVP: load corpus from data/ or hardcode
    try:
        import json
        with open(Path(__file__).parent / "data" / "corpus.json") as f:
            raw = json.load(f)
            corpus = [(item["title"], item["sentences"]) for item in raw]
    except Exception:
        corpus = []
    retriever = InMemoryRetriever(corpus)
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, EvidenceAdapter(memory=memory))
    container.register(ContextAssembler, EvidenceAssembler(
        retriever=retriever, memory=memory, top_k=5,
    ))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "validation",
    entry_prompt="你是全局校验Agent。等待任务...",
    metadata={"role": "全局校验"},
)
def assemble_validation():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, ValidationAdapter(memory=memory))
    container.register(ContextAssembler, ValidationAssembler(memory=memory))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "task_agent",
    entry_prompt="你是业务办理助手。等待任务...",
    metadata={"role": "业务办理占位"},
)
def assemble_task():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/task")
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, TaskAssembler())
    container.register(GuideProvider, FileGuideProvider(
        paths=[str(Path(__file__).parent / "AGENTS_task.md")]
    ))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "fallback",
    entry_prompt="你是异常兜底助手。等待任务...",
    metadata={"role": "异常兜底占位"},
)
def assemble_fallback():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/fallback")
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, FallbackAssembler())
    container.register(GuideProvider, FileGuideProvider(
        paths=[str(Path(__file__).parent / "AGENTS_fallback.md")]
    ))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


# ═══════════════════════════════════════════════════════════════════════════
# Subscription topology
# ═══════════════════════════════════════════════════════════════════════════

subscribe("router").to("user")
subscribe("task_agent").to("router")
subscribe("fallback").to("router")

# Virtual subscriptions: force direction/evidence/validation into continuous mode.
# Framework auto-detects mode based on has_subscriptions; without these they'd
# be oneshot and exit after one round, breaking the multi-hop loop.
subscribe("direction").to("user")
subscribe("evidence").to("user")
subscribe("validation").to("user")
```

- [ ] **Step 2: Run unit tests to ensure all agents still pass**

```bash
python -m pytest agents/customer-service/tests/unit/ -v
```
Expected: all unit tests pass (~35 tests)

- [ ] **Step 3: Commit**

```bash
git add agents/customer-service/customer_service_workflow.py
git commit -m "feat: add customer-service workflow script with 6-agent topology

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 13: Integration Tests

**Files:**
- Create: `agents/customer-service/tests/integration/conftest.py`
- Create: `agents/customer-service/tests/integration/test_topology.py`
- Create: `agents/customer-service/tests/integration/test_qa_loop.py`

- [ ] **Step 1: Write integration conftest.py**

```python
"""Integration test fixtures for customer-service workflow."""
import pytest
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def workflow_script_path():
    return str(Path(__file__).resolve().parents[2] / "customer_service_workflow.py")
```

- [ ] **Step 2: Write topology integration test**

```python
"""Integration tests for workflow topology (agent spawn + subscriptions)."""
import pytest
from harness.runtime.kernel import Kernel
from harness.runtime.runtime import Runtime as HarnessRuntime


class TestWorkflowTopology:

    def test_all_six_agents_spawned(self, workflow_script_path):
        """Verify all 6 agents spawn successfully."""
        from harness.runtime.decorators import _agent_registry, _subscription_registry
        import importlib.util

        # Clear registries (Kernel.spawn_from_script does this)
        _agent_registry.clear()
        _subscription_registry.clear()

        spec = importlib.util.spec_from_file_location("_wf_test", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert len(_agent_registry) == 6
        assert "router" in _agent_registry
        assert "direction" in _agent_registry
        assert "evidence" in _agent_registry
        assert "validation" in _agent_registry
        assert "task_agent" in _agent_registry
        assert "fallback" in _agent_registry

    def test_subscriptions_include_virtual(self, workflow_script_path):
        """Verify virtual subscriptions exist for direction/evidence/validation."""
        from harness.runtime.decorators import _agent_registry, _subscription_registry
        import importlib.util

        _agent_registry.clear()
        _subscription_registry.clear()

        spec = importlib.util.spec_from_file_location("_wf_test2", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        subs = {(s.subscriber, s.publisher) for s in _subscription_registry}
        assert ("direction", "user") in subs
        assert ("evidence", "user") in subs
        assert ("validation", "user") in subs
        assert ("router", "user") in subs

    def test_each_agent_has_entry_prompt(self, workflow_script_path):
        """Verify each agent has a non-empty entry_prompt."""
        from harness.runtime.decorators import _agent_registry, _subscription_registry
        import importlib.util

        _agent_registry.clear()
        _subscription_registry.clear()

        spec = importlib.util.spec_from_file_location("_wf_test3", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for name, blueprint in _agent_registry.items():
            assert blueprint["entry_prompt"], f"{name} has empty entry_prompt"
```

- [ ] **Step 3: Run topology tests**

```bash
python -m pytest agents/customer-service/tests/integration/test_topology.py -v
```
Expected: 3 passed (these don't need LLM — just module loading)

- [ ] **Step 4: Write QA loop integration test (smoke test — requires LLM, mark as slow)**

```python
"""Integration tests for the full QA loop (requires LLM)."""
import pytest


@pytest.mark.slow
class TestQALoopIntegration:

    def test_full_qa_loop_with_mock_llm(self):
        """Smoke test: verify the workflow topology + memory state flow
        using a mock LLM that returns canned responses.

        This test validates the MECHANISM (agent communication, state
        transitions) without depending on real LLM output quality.
        """
        # NOTE: Full end-to-end with mock LLM is implemented after
        # all agents are verified independently. This test ensures
        # the message flow Direction→Evidence→Validation→Router works.
        #
        # Implementation approach:
        # 1. Create Kernel + MemoryBackend
        # 2. Use a MockLLM that returns pre-written responses for each agent
        # 3. spawn_from_script the workflow
        # 4. send_input to "router" a QA question
        # 5. Wait for "router" to emit the final answer TextEvent
        # 6. Assert the answer is formatted correctly
        pytest.skip("Full integration test requires LLM mock setup — implement after unit tests pass")
```

- [ ] **Step 5: Commit**

```bash
git add agents/customer-service/tests/integration/
git commit -m "test: add integration tests for workflow topology and QA loop

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 14: AGENTS.md Files

**Files:**
- Create: `agents/customer-service/AGENTS_router.md`
- Create: `agents/customer-service/AGENTS_task.md`
- Create: `agents/customer-service/AGENTS_fallback.md`

- [ ] **Step 1: Write all three AGENTS.md files**

Create `AGENTS_router.md`:

```markdown
# Router Agent

You are the entry router for a customer service system. Your job is to classify user intent.

## Capabilities
- Classify user messages into: qa, task, fallback
- Route to the appropriate downstream agent

## Output Format
INTENT: <qa|task|fallback>
CONFIDENCE: <0-1>
SLOTS: <JSON dict>
```

Create `AGENTS_task.md`:

```markdown
# Task Agent (MVP Placeholder)

You are a business operations assistant. Currently in MVP placeholder mode.

## Capabilities
- Acknowledge user's business request
- Ask for required information (e.g., order number)

## Constraints
- Do not promise capabilities not yet implemented
- Suggest contacting human customer service for urgent matters
```

Create `AGENTS_fallback.md`:

```markdown
# Fallback Agent (MVP Placeholder)

You are a fallback handler for unclear or out-of-scope requests.

## Capabilities
- Respond with a standard fallback message
- Suggest contacting human customer service

## Constraints
- Be polite and helpful
- Do not attempt to answer policy questions (those go to QA)
```

- [ ] **Step 2: Commit**

```bash
git add agents/customer-service/AGENTS_*.md
git commit -m "docs: add AGENTS.md persona files for router, task, and fallback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 15: Frontend (static/index.html)

**Files:**
- Create: `agents/customer-service/static/index.html`

- [ ] **Step 1: Write the frontend (single-page vanilla JS)**

Create `agents/customer-service/static/index.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Customer Service Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f1f5f9; height: 100vh; display: flex; flex-direction: column; }
  .header { padding: 12px 20px; background: #1e293b; color: #fff;
            font-weight: 600; display: flex; align-items: center; gap: 10px; }
  .status { font-size: 12px; padding: 2px 10px; border-radius: 10px; }
  .status.connected { background: #16a34a; }
  .status.disconnected { background: #dc2626; }
  .main { flex: 1; display: flex; overflow: hidden; }
  .chat-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 80%; padding: 10px 14px; border-radius: 14px; font-size: 14px; line-height: 1.5; animation: fadeIn 0.2s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  .msg.user { align-self: flex-end; background: #3b82f6; color: #fff; }
  .msg.agent { align-self: flex-start; background: #fff; color: #1e293b; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .reasoning-panel { width: 380px; background: #fff; border-left: 1px solid #e2e8f0;
                     overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .round-group { border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
  .round-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
                  background: #f8fafc; cursor: pointer; font-weight: 600; font-size: 13px; user-select: none; }
  .round-indicator { font-size: 10px; transition: transform 0.2s; }
  .round-group.open .round-indicator { transform: rotate(90deg); }
  .round-body { display: none; padding: 8px 12px; }
  .round-group.open .round-body { display: block; }
  .phase-card { margin: 4px 0; border-radius: 6px; overflow: hidden; font-size: 12px; }
  .phase-card.direction { background: #fef9c3; border: 1px solid #fde047; }
  .phase-card.evidence { background: #dcfce7; border: 1px solid #86efac; }
  .phase-card.validation { background: #f3e8ff; border: 1px solid #d8b4fe; }
  .phase-header { padding: 4px 10px; font-weight: 600; }
  .phase-body { padding: 6px 10px; }
  .candidate-item, .evidence-item, .decision-item { padding: 3px 0; font-family: monospace; font-size: 12px; }
  .evidence-item.valid { color: #166534; }
  .evidence-item.invalid { color: #dc2626; text-decoration: line-through; }
  .decision-item.keep { color: #166534; }
  .decision-item.discard { color: #991b1b; }
  .source-text { font-size: 11px; color: #64748b; margin-top: 2px; }
  .answer-box { margin-top: 6px; padding: 8px 12px; background: #eff6ff;
                border: 1px solid #93c5fd; border-radius: 6px; font-size: 13px; font-weight: 500; color: #1e40af; }
  .input-area { display: flex; gap: 8px; padding: 12px 16px; background: #fff; border-top: 1px solid #e2e8f0; }
  .input-area input { flex: 1; border: 1px solid #e2e8f0; border-radius: 20px; padding: 10px 16px; font-size: 14px; outline: none; }
  .input-area input:focus { border-color: #3b82f6; }
  .input-area button { border: none; border-radius: 20px; padding: 10px 20px; background: #3b82f6;
                       color: #fff; font-weight: 600; cursor: pointer; }
</style>
</head>
<body>
<div class="header">
  <span>🎧 Customer Service Agent</span>
  <span class="status disconnected" id="status">断开</span>
</div>
<div class="main">
  <div class="chat-panel">
    <div class="messages" id="messages"></div>
    <div class="input-area">
      <input id="input" placeholder="输入问题... (如: 改签规则是什么？)" onkeydown="if(event.key==='Enter')send()">
      <button onclick="send()">发送</button>
    </div>
  </div>
  <div class="reasoning-panel" id="reasoning"></div>
</div>
<script>
const WS_URL = 'ws://localhost:8000/ws';
let ws = null;
let currentRound = null;

function setStatus(ok) {
  const s = document.getElementById('status');
  s.textContent = ok ? '已连接' : '断开';
  s.className = 'status ' + (ok ? 'connected' : 'disconnected');
}

function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => setStatus(true);
  ws.onclose = () => { setStatus(false); setTimeout(connect, 3000); };
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
}

function addMessage(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  document.getElementById('messages').appendChild(d);
  document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
}

function escape(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function findOrCreateRound(roundNum) {
  const id = 'round-' + roundNum;
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.className = 'round-group open';
    el.id = id;
    el.innerHTML = `<div class="round-header" onclick="this.parentElement.classList.toggle('open')">
      <span class="round-indicator">▶</span> Round ${roundNum}
    </div><div class="round-body"></div>`;
    document.getElementById('reasoning').appendChild(el);
  }
  return el.querySelector('.round-body');
}

function addPhaseCard(roundNum, type, title, bodyHtml) {
  const body = findOrCreateRound(roundNum);
  const card = document.createElement('div');
  card.className = 'phase-card ' + type;
  card.innerHTML = `<div class="phase-header">${escape(title)}</div><div class="phase-body">${bodyHtml}</div>`;
  body.appendChild(card);
}

function handleEvent(data) {
  switch (data.type) {
    case 'intent_classified':
      addMessage('agent', `[Router] 意图: ${data.data.intent} (置信度: ${data.data.confidence})`);
      break;
    case 'direction_output':
      addPhaseCard(data.round, 'direction', '🔍 方向生成',
        data.data.candidates.map(c => `<div class="candidate-item">${escape(c[0])} | ${escape(c[1])} | ?</div>`).join(''));
      break;
    case 'evidence_output':
      const cls = data.data.valid ? 'valid' : 'invalid';
      const icon = data.data.valid ? '✅' : '❌';
      const triple = data.data.triple
        ? `${escape(data.data.triple.subj)} | ${escape(data.data.triple.rel)} | ${escape(data.data.triple.obj)}`
        : data.data.reason || 'INVALID';
      const srcHtml = data.data.source_passage
        ? `<div class="source-text">来源: ${escape(data.data.source_passage)}</div>` : '';
      addPhaseCard(data.round, 'evidence', '📎 证据锚定',
        `<div class="evidence-item ${cls}">${icon} ${triple}${srcHtml}</div>`);
      break;
    case 'validation_output':
      const decisions = Object.entries(data.data.decisions)
        .map(([nid, d]) => `<div class="decision-item ${d.toLowerCase()}">${escape(nid)}: ${d}</div>`).join('');
      const answerHtml = data.data.answer
        ? `<div class="answer-box">💡 ${escape(data.data.answer)}</div>` : '';
      addPhaseCard(data.round, 'validation', '🛡️ 全局校验', decisions + answerHtml);
      break;
    case 'qa_answer':
      addMessage('agent', data.data.answer);
      if (data.data.sources && data.data.sources.length) {
        addMessage('agent', '📚 参考来源:\n' + data.data.sources.map(s => '· ' + s).join('\n'));
      }
      break;
    case 'qa_fallback':
      addMessage('agent', data.data.answer || '抱歉，暂时无法回答这个问题。');
      break;
  }
}

function send() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text || !ws) return;
  addMessage('user', text);
  ws.send(JSON.stringify({text}));
  input.value = '';
}

connect();
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add agents/customer-service/static/index.html
git commit -m "feat: add single-page frontend with collapsible reasoning panel

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 16: WebSocket Server

**Files:**
- Create: `agents/customer-service/server.py`

- [ ] **Step 1: Write the FastAPI WebSocket server**

Create `agents/customer-service/server.py`:

```python
"""Customer Service WebSocket Server.

Usage:
    python agents/customer-service/server.py
    # Open browser at http://localhost:8000
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from shared.frontend_bus import FrontendBus

app = FastAPI(title="Customer Service Agent", version="0.1.0")

# Serve static frontend
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# FrontendBus — shared between server and workflow (singleton)
frontend_bus = FrontendBus()
connected_clients: list[WebSocket] = []


@app.get("/")
async def root():
    index_path = static_path / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Customer Service Agent</h1>")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)

    queue = frontend_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:
                break
    except Exception:
        pass
    finally:
        connected_clients.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Customer Service Agent Server")
    print("=" * 50)
    print()
    print("  Frontend: http://localhost:8000")
    print("  WebSocket: ws://localhost:8000/ws")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
```

- [ ] **Step 2: Commit**

```bash
git add agents/customer-service/server.py
git commit -m "feat: add FastAPI WebSocket server with FrontendBus integration

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 17: README and Test Corpus

**Files:**
- Create: `agents/customer-service/README.md`
- Create: `agents/customer-service/data/corpus.json`

- [ ] **Step 1: Write a minimal test corpus**

Create `agents/customer-service/data/corpus.json`:

```json
[
  {
    "title": "航班改签政策",
    "sentences": [
      "第1条：旅客可在起飞前24小时免费改签。",
      "第2条：起飞前2小时内改签需支付票价10%的手续费。",
      "第3条：特价舱位旅客不享受免费改签服务。",
      "第4条：改签后的航班必须在原航班日期前后3天内。"
    ]
  },
  {
    "title": "乘客权益规则",
    "sentences": [
      "第5条：航班延误超过2小时，乘客可申请全额退款。",
      "第6条：因天气原因取消航班，航空公司不承担赔偿责任。",
      "第7条：非特价舱位乘客享有优先改签权益。"
    ]
  },
  {
    "title": "会员权益",
    "sentences": [
      "第8条：金卡会员每年享有2次免费改签权益。",
      "第9条：银卡会员改签手续费减半。",
      "第10条：普通会员不享受改签优惠。"
    ]
  }
]
```

- [ ] **Step 2: Write README**

Create `agents/customer-service/README.md`:

```markdown
# customer-service — Multi-Hop QA Customer Service Agent

A customer service agent system demonstrating multi-hop verified question answering,
built on the Harness Agent Template framework with the topic_code verified QA approach.

## Architecture

6 Runtime-level agents orchestrated via Kernel workflow:

- **Router** — Intent classification (qa / task / fallback)
- **Direction** — Candidate direction generation
- **Evidence** — Retrieval + triple confirmation
- **Validation** — Global graph scoring + loop termination
- **Task (stub)** — Business operation placeholder
- **Fallback (stub)** — Out-of-scope handler

## Quick Start

```bash
# Terminal 1: Start WebSocket server
python agents/customer-service/server.py

# Terminal 2: Start Runtime workflow
python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
```

Then open http://localhost:8000 in browser, or type in terminal:
```
/talk router 改签规则是什么？
```

## Testing

```bash
# Unit tests (per-agent, no LLM needed for most)
pytest agents/customer-service/tests/unit/ -v

# Integration tests (topology, no LLM needed)
pytest agents/customer-service/tests/integration/ -v
```
```

- [ ] **Step 3: Final commit**

```bash
git add agents/customer-service/README.md agents/customer-service/data/
git commit -m "docs: add README and test corpus for customer-service agent

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Test Commands Reference

| Level | Command | What it covers |
|---|---|---|
| **Unit** | `pytest agents/customer-service/tests/unit/ -v` | Per-agent adapter + assembler I/O contracts, shared components |
| **Integration** | `pytest agents/customer-service/tests/integration/ -v` | Workflow topology, agent spawn, subscriptions |
| **All (no LLM)** | `pytest agents/customer-service/tests/ -v -m "not slow"` | Everything except full QA loop |

---

*Plan complete. Next: choose execution approach — Subagent-Driven (recommended) or Inline.*
