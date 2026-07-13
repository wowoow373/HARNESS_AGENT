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


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that require real LLM API calls")


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
