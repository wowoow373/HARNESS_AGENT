"""EvidenceAdapter — parse LLM triple output + sync barrier check."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_final
from shared.subgraph_manager import SubGraphManager
from shared.state_schema import read_state, write_state


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
        meta = request.metadata or {}
        if meta.get("direction"):
            self._current_direction = tuple(meta["direction"])
        return request

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            # NOTE: MemoryBackend API is read(key, namespace), write(key, value, namespace)
            state = read_state(self._memory)
            if not isinstance(state, dict):
                await self._kba.send(event, target)
                return
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
                write_state(self._memory, state)

                if state["pending"]["received"] >= state["pending"]["total"]:
                    self._kernel.send_input("validation", UserRequest(
                        text="[TASK]",
                        metadata={
                            "task": "validate_graph",
                            "question": state["question"],
                            "trigger": "evidence_complete",
                        },
                    ))

        await self._kba.send(event, target)
