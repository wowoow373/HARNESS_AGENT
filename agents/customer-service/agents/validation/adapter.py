"""ValidationAdapter — parse KEEP/DISCARD + ANSWER + termination logic."""
from harness.interfaces.types import TextEvent, UserRequest
from harness.interfaces.memory_backend import MemoryBackend
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from shared.prompts import parse_validator_decisions, parse_validator_answer
from shared.subgraph_manager import SubGraphManager


class ValidationAdapter:
    """Parses validator output, judges termination, drives loop continuation.

    Constructor-injected:
    - memory: MemoryBackend for reading/writing QA shared state
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._runtime = None
        self._memory = memory

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel
        self._runtime = runtime

    async def receive(self) -> UserRequest:
        return await self._kba.receive()

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            # NOTE: MemoryBackend API is read(key, namespace), write(key, value, namespace)
            state = self._memory.read("qa_state", "loop")
            if not isinstance(state, dict):
                await self._kba.send(event, target)
                return
            graph = SubGraphManager.from_dict(state["graph"])
            prev_node_count = graph.node_count()

            id_map = graph.get_id_map()
            decisions = parse_validator_decisions(event.content, id_map)
            answer = parse_validator_answer(event.content)

            graph.update_scores(decisions)
            state["graph"] = graph.to_dict()

            expandable = [nid for nid, score in decisions.items() if score == 1]
            new_node_count = graph.node_count() - prev_node_count

            if answer is not None:
                state["phase"] = "done"
                state["answer"] = answer
                state["sources"] = graph.get_sources()
                self._memory.write("qa_state", state, "loop")
                self._kernel.send_input("router", UserRequest(
                    text="[TASK]", metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": answer,
                        "sources": state["sources"],
                    },
                ))
                self._kernel.end_workflow(self._runtime.workflow_flag)

            elif state["round"] >= state["max_hops"]:
                self._emit_fallback(state, "max_hops")

            elif not expandable:
                self._emit_fallback(state, "no_expandable")

            elif new_node_count == 0:
                self._emit_fallback(state, "no_progress")

            else:
                state["round"] += 1
                state["expandable"] = expandable
                state["phase"] = "direction"
                state["pending"] = {"total": 0, "received": 0, "results": []}
                self._memory.write("qa_state", state, "loop")

                expandable_nodes = []
                for nid in expandable:
                    expandable_nodes.append({
                        "node_id": nid,
                        "confirmed_triples": graph.get_path_triples(nid),
                        "evidence_passages": graph.get_accumulated_passages(nid),
                    })

                self._kernel.send_input("direction", UserRequest(
                    text="[TASK]", metadata={
                        "task": "generate_directions",
                        "question": state["question"],
                        "expandable_nodes": expandable_nodes,
                        "K": state.get("K", 2),
                    },
                ))

        await self._kba.send(event, target)

    def _emit_fallback(self, state: dict, reason: str):
        state["phase"] = "done"
        state["answer"] = "抱歉，暂时无法回答这个问题，请咨询人工客服。"
        self._memory.write("qa_state", state, "loop")
        self._kernel.send_input("router", UserRequest(
            text="[TASK]", metadata={
                "type": "qa_answer",
                "question": state["question"],
                "answer": state["answer"],
                "sources": [],
            },
        ))
        self._kernel.end_workflow(self._runtime.workflow_flag)
