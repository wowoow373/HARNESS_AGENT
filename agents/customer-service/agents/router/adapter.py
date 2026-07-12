"""RouterAdapter — parse LLM intent output and route to downstream agents."""
import re
from harness.interfaces.types import TextEvent, UserRequest
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.interfaces.memory_backend import MemoryBackend
from shared.state_schema import create_initial_state, write_state


class RouterAdapter:
    """KBA wrapper. Parses LLM intent classification in send() and routes.

    Constructor-injected:
    - memory: MemoryBackend for initializing QA shared state
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._current_user_message = ""

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        request = await self._kba.receive()
        meta = request.metadata or {}
        # Save user message text for routing. Skip:
        # - entry_prompt (has workflow_flag)
        # - QA answer back from Validation (type=qa_answer)
        if request.text and meta.get("type") != "qa_answer":
            if "workflow_flag" not in meta:
                self._current_user_message = request.text
        return request

    async def send(self, event, target=None):
        parsed = None
        if isinstance(event, TextEvent):
            parsed = self._parse_intent(event.content)

            # ★ Skip entry_prompt responses — no real user message yet
            if not self._current_user_message:
                await self._kba.send(event, target)
                return

            if parsed["intent"] == "qa":
                state = create_initial_state(question=self._current_user_message)
                write_state(self._memory, state)
                self._kernel.send_input("direction", UserRequest(
                    text="[TASK]",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_user_message,
                        "expandable_nodes": [{
                            "node_id": "ROOT",
                            "confirmed_triples": [],
                            "evidence_passages": [],
                        }],
                    }
                ))

            elif parsed["intent"] == "task":
                self._kernel.send_input("task_agent", UserRequest(
                    text=self._current_user_message,
                ))

            elif parsed["intent"] == "fallback":
                self._kernel.send_input("fallback", UserRequest(
                    text=self._current_user_message,
                ))

        # ★ Only publish to console if not routed via send_input
        if parsed is None or parsed["intent"] == "qa":
            await self._kba.send(event, target)

    @staticmethod
    def _parse_intent(text: str) -> dict:
        intent = "fallback"
        confidence = "0.0"
        slots = "{}"
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("INTENT:"):
                intent = line.split(":", 1)[1].strip().lower()
            elif line.startswith("CONFIDENCE:"):
                confidence = line.split(":", 1)[1].strip()
            elif line.startswith("SLOTS:"):
                slots = line.split(":", 1)[1].strip()
        return {"intent": intent, "confidence": float(confidence), "slots": slots}
