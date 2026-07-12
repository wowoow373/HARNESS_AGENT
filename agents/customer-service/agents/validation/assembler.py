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

    * Validator receives ONLY the triple graph, NOT raw passages.
    """

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        # Guard: entry_prompt — no QA state in memory yet
        state = self._memory.read("loop", "qa_state")
        if state is None:
            return [
                Message(role="system", content="等待任务..."),
                Message(role="user", content=ctx.user_request.text if ctx.user_request else "准备就绪"),
            ]
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
