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
