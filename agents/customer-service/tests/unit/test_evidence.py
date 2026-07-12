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
