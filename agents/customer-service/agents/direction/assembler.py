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
        meta = ctx.user_request.metadata if ctx.user_request else {}
        # Guard: entry_prompt — wait for actual task
        if not meta.get("task"):
            return [
                Message(role="system", content="等待任务..."),
                Message(role="user", content=ctx.user_request.text if ctx.user_request else "准备就绪"),
            ]
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
