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
