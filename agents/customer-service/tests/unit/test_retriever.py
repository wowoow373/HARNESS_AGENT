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
