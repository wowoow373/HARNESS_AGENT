"""DirectionAdapter — multi-node iteration + LLM output parsing + Evidence dispatch."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_draft_v3_output


class DirectionAdapter:
    """Manages multi-node iteration, parses draft output, dispatches Evidence tasks.

    Constructor-injected:
    - memory: MemoryBackend for reading/writing QA shared state

    Instance state:
    - _pending_nodes: expandable nodes remaining to process
    - _accumulated_tasks: Evidence tasks accumulated across nodes
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._pending_nodes = []
        self._accumulated_tasks = []
        self._current_question = ""
        self._current_node_id = None

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        raw = await self._kba.receive()
        meta = raw.metadata or {}

        if meta.get("task") == "generate_directions":
            self._current_question = meta["question"]
            self._pending_nodes = list(meta["expandable_nodes"])
            self._accumulated_tasks = []
            first_node = self._pending_nodes.pop(0)
            self._current_node_id = first_node["node_id"]
            return UserRequest(
                text="",
                metadata={
                    "task": "generate_directions",
                    "question": meta["question"],
                    "node_id": first_node["node_id"],
                    "confirmed_triples": first_node["confirmed_triples"],
                    "evidence_passages": first_node["evidence_passages"],
                    "K": meta.get("K", 2),
                },
            )
        return raw

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            remaining_q, candidates = parse_draft_v3_output(event.content)

            state = self._memory.read("loop", "qa_state")
            tried = state.get("tried_candidates", {}).get(self._current_node_id, [])
            fresh = [
                (s, r) for s, r in candidates
                if (s.lower(), r.lower()) not in tried
            ]

            for subj, rel in fresh:
                self._accumulated_tasks.append({
                    "task": "confirm_triple",
                    "question": self._current_question,
                    "direction": (subj, rel),
                    "confirmed_triples": state.get("graph", {}),
                    "node_id": self._current_node_id,
                })

            tried.extend([(s.lower(), r.lower()) for s, r in fresh])
            state.setdefault("tried_candidates", {})[self._current_node_id] = tried
            self._memory.write("loop", "qa_state", state)

            if self._pending_nodes:
                next_node = self._pending_nodes.pop(0)
                self._current_node_id = next_node["node_id"]
                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_question,
                        "node_id": next_node["node_id"],
                        "confirmed_triples": next_node["confirmed_triples"],
                        "evidence_passages": next_node["evidence_passages"],
                        "K": state.get("K", 2),
                    },
                ))
            else:
                if self._accumulated_tasks:
                    state["pending"]["total"] = len(self._accumulated_tasks)
                    state["pending"]["received"] = 0
                    state["pending"]["results"] = []
                    state["phase"] = "evidence"
                    self._memory.write("loop", "qa_state", state)

                    for task in self._accumulated_tasks:
                        self._kernel.send_input("evidence", UserRequest(
                            text="", metadata=task,
                        ))
                else:
                    self._kernel.send_input("validation", UserRequest(
                        text="", metadata={"trigger": "direction_empty"},
                    ))

        await self._kba.send(event, target)
