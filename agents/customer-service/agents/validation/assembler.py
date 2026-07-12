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
