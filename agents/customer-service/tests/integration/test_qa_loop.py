"""Integration tests for full QA loop (requires LLM, marked slow)."""
import pytest


@pytest.mark.slow
class TestQALoopIntegration:

    def test_full_qa_loop_smoke(self):
        pytest.skip("Full integration test requires LLM mock setup — implement after unit tests pass")
