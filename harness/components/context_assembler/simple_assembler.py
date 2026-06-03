"""SimpleAssembler — ContextAssembler baseline implementation.

Sliding-window history truncation + direct guide/memory/tool concatenation.

Usage::

    assembler = SimpleAssembler(max_history=50)
    assembler = SimpleAssembler(max_history=50, memory=memory_instance)
    messages = assembler.assemble(assembly_context)
    # messages[0].role == "system"  # guides + memories + tools
    # messages[-1].role == "user"   # current user request
"""

from __future__ import annotations

import logging
from typing import List, Optional, TYPE_CHECKING

from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    MemoryItem,
    Message,
    ToolDefinition,
)

if TYPE_CHECKING:
    from harness.interfaces.memory_backend import MemoryBackend

logger = logging.getLogger(__name__)


class SimpleAssembler:
    """ContextAssembler baseline implementation.

    Assembles GuidesBundle, memories, tools, and history into a List[Message]
    for LLM consumption. Uses sliding-window truncation by message count,
    preserving all system messages.

    Usage::

        assembler = SimpleAssembler(max_history=50)
        assembler = SimpleAssembler(max_history=50, memory=memory_instance)
        messages = assembler.assemble(assembly_context)
        # messages[0].role == "system"  # guides + memories + tools
        # messages[-1].role == "user"   # current user request
    """

    def __init__(
        self,
        max_history: int = 50,
        memory: "Optional[MemoryBackend]" = None,
        include_tools: bool = True,
        include_memories: bool = True,
    ):
        """Initialize SimpleAssembler.

        Args:
            max_history: Sliding window size (keep last N non-system messages).
                         Default 50. 0 means keep only system messages.
                         Negative means unlimited (keep all).
            memory: Optional MemoryBackend instance for enhanced retrieval
                    beyond framework baseline. When None, only consumes
                    AssemblyContext.memories (framework baseline results).
            include_tools: Whether to include available_tools descriptions
                           in the system prompt. Default True.
            include_memories: Whether to include memories in the system
                              prompt. Default True.
        """
        self._max_history = max_history
        self._memory = memory
        self._include_tools = include_tools
        self._include_memories = include_memories

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def assemble(self, inputs: AssemblyContext) -> List[Message]:
        """Assemble AssemblyContext into List[Message] for LLM consumption.

        Pipeline:
            1. Optional: enhanced memory retrieval via self._memory
               (with error handling)
            2. Build system message from guides + tools + memories
            3. Apply sliding window to conversation history
            4. Append current user request as final message
            5. Return complete message list

        Assembly order: system(guides) -> history(sliding window) -> user

        Args:
            inputs: AssemblyContext containing all context information.

        Returns:
            List[Message]: Assembled messages ready for LLM consumption.
                           Minimum 1 system + 1 user message.
        """
        messages: List[Message] = []

        # 1. Enhanced memory retrieval (optional)
        memories = list(inputs.memories) if inputs.memories else []
        if self._memory is not None and self._include_memories and inputs.user_request:
            try:
                extra = self._memory.search(
                    inputs.user_request.text, "semantic", limit=5
                )
                if extra:
                    memories.extend(extra)
            except Exception as e:
                logger.warning(
                    "Enhanced memory retrieval failed: %s, falling back to baseline",
                    e,
                )

        # 2. Build system message
        memories_text = (
            self._format_memories(memories) if self._include_memories else ""
        )
        tools_list = inputs.available_tools if self._include_tools else []
        system_content = self._format_system_prompt(
            inputs.guides, tools_list, memories_text
        )
        messages.append(Message(role="system", content=system_content))

        # 3. Apply sliding window to history
        if inputs.history:
            windowed = self._apply_sliding_window(
                inputs.history, self._max_history
            )
            messages.extend(windowed)

        # 4. Append user request
        user_text = inputs.user_request.text if inputs.user_request else ""
        messages.append(Message(role="user", content=user_text))

        return messages

    # ------------------------------------------------------------------
    # Internal methods — system prompt formatting
    # ------------------------------------------------------------------

    def _format_system_prompt(
        self,
        guides: Optional[GuidesBundle],
        tools: List[ToolDefinition],
        memories_text: str,
    ) -> str:
        """Build the system prompt from guides, tools, and memories.

        Template::

            {identity}

            ## Capabilities
            {capabilities or "General purpose assistance"}

            ## Rules
            {rules or "No specific rules"}

            ## Constraints
            {constraints or "No specific constraints"}

            ## Available Tools
            {tools or "(none)"}

            ## Relevant Memories
            {memories_text}
            (section skipped if memories_text is empty)

        Args:
            guides: GuidesBundle or None.
            tools: List of ToolDefinition.
            memories_text: Pre-formatted memories text or empty string.

        Returns:
            str: Formatted system prompt.
        """
        sections: List[str] = []

        if guides is not None:
            # Identity — raw text without heading
            if guides.identity:
                sections.append(guides.identity)

            # Capabilities
            if guides.capabilities:
                caps = "\n".join(f"- {c}" for c in guides.capabilities)
                sections.append(f"## Capabilities\n\n{caps}")
            else:
                sections.append("## Capabilities\n\nGeneral purpose assistance")

            # Rules
            if guides.rules:
                rules = "\n".join(f"- {r}" for r in guides.rules)
                sections.append(f"## Rules\n\n{rules}")
            else:
                sections.append("## Rules\n\nNo specific rules")

            # Constraints
            if guides.constraints:
                constraints = "\n".join(f"- {c}" for c in guides.constraints)
                sections.append(f"## Constraints\n\n{constraints}")
            else:
                sections.append("## Constraints\n\nNo specific constraints")

            # Examples (skip section entirely if empty)
            if guides.examples:
                example_parts: List[str] = []
                for i, ex in enumerate(guides.examples, 1):
                    title = f"Example {i}"
                    lines: List[str] = [f"### {title}"]
                    if ex.input:
                        lines.append(f"\nInput:\n{ex.input}")
                    if ex.output:
                        lines.append(f"\nOutput:\n{ex.output}")
                    example_parts.append("".join(lines))
                if example_parts:
                    sections.append(
                        "## Examples\n\n" + "\n\n".join(example_parts)
                    )
        else:
            # guides is None — include default sections
            sections.append("## Capabilities\n\nGeneral purpose assistance")
            sections.append("## Rules\n\nNo specific rules")
            sections.append("## Constraints\n\nNo specific constraints")

        # Tools
        tools_text = self._format_tools(tools)
        sections.append(f"## Available Tools\n\n{tools_text}")

        # Memories (skip section entirely if empty)
        if memories_text:
            sections.append(f"## Relevant Memories\n\n{memories_text}")

        return "\n\n".join(sections).strip()

    def _format_tools(self, tools: List[ToolDefinition]) -> str:
        """Format tool definitions as markdown list.

        Each tool is formatted as:
        - **name**(param: type, ...) — description

        Args:
            tools: List of ToolDefinition.

        Returns:
            str: Formatted tools text, or "(none)" if empty.
        """
        if not tools:
            return "(none)"

        lines: List[str] = []
        for tool in tools:
            params = tool.parameters or {}
            if params:
                param_parts: List[str] = []
                for name, info in params.items():
                    if isinstance(info, dict):
                        ptype = info.get("type", "string")
                    else:
                        ptype = str(info)
                    param_parts.append(f"{name}: {ptype}")
                sig = ", ".join(param_parts)
                lines.append(
                    f"- **{tool.name}**({sig}) — {tool.description}"
                )
            else:
                lines.append(
                    f"- **{tool.name}**() — {tool.description}"
                )
        return "\n".join(lines)

    def _format_memories(self, memories: List[MemoryItem]) -> str:
        """Format memory items as markdown list.

        Each memory is formatted as:
        - [namespace/key] value (truncated to 200 chars)

        Args:
            memories: List of MemoryItem.

        Returns:
            str: Formatted memories text, or empty string if empty list.
        """
        if not memories:
            return ""

        lines: List[str] = []
        for m in memories:
            ns = m.namespace or ""
            key = m.key or ""
            value = str(m.value) if m.value else ""
            # Truncate value to 200 characters
            if len(value) > 200:
                value = value[:200] + "..."
            lines.append(f"- [{ns}/{key}] {value}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal methods — sliding window
    # ------------------------------------------------------------------

    def _apply_sliding_window(
        self, history: List[Message], max_history: int
    ) -> List[Message]:
        """Apply sliding window truncation to history.

        Rules:
            1. All system messages (role == "system") are always preserved
               and do not count toward the window quota.
            2. Non-system messages: keep the last max_history entries.
            3. max_history == 0: discard all non-system messages
               (keep only system messages).
            4. max_history < 0: no truncation (keep all).
            5. Return order: system messages (original relative order) +
               truncated non-system messages (original relative order).

        Args:
            history: Original history message list.
            max_history: Sliding window size (number of non-system messages
                         to retain).

        Returns:
            List[Message]: Truncated message list.
        """
        if max_history < 0:
            return list(history)

        system_msgs = [m for m in history if m.role == "system"]
        non_system_msgs = [m for m in history if m.role != "system"]

        if max_history == 0:
            return system_msgs

        # Keep the last max_history non-system messages
        truncated = non_system_msgs[-max_history:]

        return system_msgs + truncated
