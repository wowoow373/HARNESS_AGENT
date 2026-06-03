"""Test harness for SimpleAssembler — batch-05 context assembler implementation.

Covers all acceptance criteria defined in
sdd/batches/batch-05-context-assembler/acceptance.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from harness.components.context_assembler import SimpleAssembler
from harness.components.guide_provider import FileGuideProvider
from harness.components.memory_backend.md_memory import MdMemory
from harness.core.container import DIContainer
from harness.interfaces.context_assembler import ContextAssembler
from harness.interfaces.guide_provider import GuideContext
from harness.interfaces.memory_backend import MemoryBackend
from harness.interfaces.types import (
    AssemblyContext,
    Example,
    GuidesBundle,
    MemoryItem,
    Message,
    SystemState,
    ToolDefinition,
    UserRequest,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_guides() -> GuidesBundle:
    """GuidesBundle with all 5 fields populated."""
    return GuidesBundle(
        identity="You are a coding assistant specialized in Python development.",
        capabilities=[
            "Write and review Python code",
            "Debug and optimize performance",
        ],
        rules=[
            "All code must pass type checking",
            "Prefer standard library over third-party dependencies",
        ],
        constraints=[
            "Never modify .git directory files",
            "Never execute unconfirmed delete operations",
        ],
        examples=[
            Example(
                input="Review this function for bugs.",
                output="I will analyze the function systematically.",
            ),
            Example(
                input="Add a rate limiter.",
                output="I'll propose a plan first.",
            ),
        ],
    )


@pytest.fixture
def empty_guides() -> GuidesBundle:
    """GuidesBundle with all fields at defaults."""
    return GuidesBundle()


@pytest.fixture
def sample_tools() -> List[ToolDefinition]:
    """3 ToolDefinitions (with params, without params)."""
    return [
        ToolDefinition(
            name="read_file",
            description="Read file contents",
            parameters={"path": {"type": "string"}},
        ),
        ToolDefinition(
            name="write_file",
            description="Write file contents",
            parameters={
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        ),
        ToolDefinition(
            name="list_dir",
            description="List directory contents",
            parameters={},
        ),
    ]


@pytest.fixture
def sample_memories() -> List[MemoryItem]:
    """3 MemoryItems in different namespaces."""
    return [
        MemoryItem(
            key="session-001",
            value="User asked about Python async/await patterns",
            namespace="episodic",
            timestamp=1717430400.0,
        ),
        MemoryItem(
            key="user-pref",
            value="User prefers Python, uses black formatter",
            namespace="semantic",
            timestamp=1717430500.0,
        ),
        MemoryItem(
            key="skill-001",
            value="User knows advanced pytest features",
            namespace="procedural",
            timestamp=1717430600.0,
        ),
    ]


@pytest.fixture
def sample_history() -> List[Message]:
    """10 mixed messages (3 system + 7 user/assistant)."""
    return [
        Message(role="system", content="Session started"),
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi! How can I help?"),
        Message(role="system", content="Reminder: be concise"),
        Message(role="user", content="Write a function"),
        Message(role="assistant", content="Here is a function..."),
        Message(role="system", content="System note: use type hints"),
        Message(role="user", content="Add tests"),
        Message(role="assistant", content="Adding tests now..."),
        Message(role="user", content="Thanks"),
    ]


@pytest.fixture
def sample_user_request() -> UserRequest:
    """Standard UserRequest."""
    return UserRequest(
        text="Please write a Python function",
        session_id="test-001",
        system_state=SystemState(
            phase="loop", session_id="test-001", run_mode="normal"
        ),
    )


@pytest.fixture
def sample_context(
    sample_guides: GuidesBundle,
    sample_tools: List[ToolDefinition],
    sample_memories: List[MemoryItem],
    sample_history: List[Message],
    sample_user_request: UserRequest,
) -> AssemblyContext:
    """Complete AssemblyContext with all fields filled."""
    return AssemblyContext(
        user_request=sample_user_request,
        guides=sample_guides,
        available_tools=sample_tools,
        history=sample_history,
        memories=sample_memories,
        system_state=SystemState(
            phase="loop", session_id="test-001", run_mode="normal"
        ),
    )


# ============================================================================
# AC-CA-01: assemble() basic assembly
# ============================================================================


class TestAssembleBasic:
    """AC-CA-01: assemble() basic assembly tests."""

    def test_assemble_basic_with_guides(
        self, sample_guides: GuidesBundle, sample_user_request: UserRequest
    ):
        """AC-CA-01.1/01.2: GuidesBundle appears in system message."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=sample_guides,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        # First message is system
        assert result[0].role == "system"
        # Contains identity text
        assert sample_guides.identity in result[0].content
        # Contains rules
        assert sample_guides.rules[0] in result[0].content

    def test_assemble_with_user_request(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-01.3: UserRequest appears as last message."""
        ctx = AssemblyContext(user_request=sample_user_request)
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        # Last message is user
        assert result[-1].role == "user"
        assert result[-1].content == sample_user_request.text

    def test_assemble_with_history(
        self,
        sample_guides: GuidesBundle,
        sample_user_request: UserRequest,
    ):
        """AC-CA-01.4: History appears between system and user."""
        history_no_system = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi! How can I help?"),
            Message(role="user", content="Write a function"),
            Message(role="assistant", content="Here is a function..."),
        ]
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=sample_guides,
            history=history_no_system,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        # Verify order: system -> history -> user
        assert result[0].role == "system"
        assert result[-1].role == "user"
        # Middle messages should match history in order
        assert len(result) == 1 + len(history_no_system) + 1  # system + history + user
        for i, hmsg in enumerate(history_no_system):
            assert result[1 + i].role == hmsg.role
            assert result[1 + i].content == hmsg.content

    def test_assemble_returns_list_of_message(
        self, sample_context: AssemblyContext
    ):
        """AC-CA-01.5: Return type is List[Message]."""
        assembler = SimpleAssembler()
        result = assembler.assemble(sample_context)

        assert isinstance(result, list)
        for msg in result:
            assert isinstance(msg, Message)


# ============================================================================
# AC-CA-02: System message formatting
# ============================================================================


class TestSystemPromptFormatting:
    """AC-CA-02: System message formatting tests."""

    def test_system_prompt_empty_guides(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-02.1: Empty GuidesBundle still creates system message."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=GuidesBundle(),
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        # System message exists
        assert result[0].role == "system"
        # Should have default sections
        content = result[0].content
        assert "## Capabilities" in content
        assert "General purpose assistance" in content

    def test_system_prompt_all_fields(
        self,
        sample_guides: GuidesBundle,
        sample_tools: List[ToolDefinition],
        sample_memories: List[MemoryItem],
        sample_user_request: UserRequest,
    ):
        """AC-CA-02.2: All 5 guide fields + tools + memories appear."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=sample_guides,
            available_tools=sample_tools,
            memories=sample_memories,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        content = result[0].content
        # Identity
        assert sample_guides.identity in content
        # Capabilities
        assert "## Capabilities" in content
        assert sample_guides.capabilities[0] in content
        # Rules
        assert "## Rules" in content
        assert sample_guides.rules[0] in content
        # Constraints
        assert "## Constraints" in content
        assert sample_guides.constraints[0] in content
        # Examples
        assert "## Examples" in content
        # Tools
        assert "## Available Tools" in content
        # Memories
        assert "## Relevant Memories" in content

    def test_system_prompt_null_guides(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-02.3: guides=None does not crash."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=None,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        assert result[0].role == "system"
        # Should still have default sections
        assert "## Capabilities" in result[0].content
        assert "General purpose assistance" in result[0].content

    def test_system_prompt_emoji_unicode(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-02.4: Emoji and Unicode characters preserved."""
        guides = GuidesBundle(
            identity="You are a friendly coding assistant. 🐍✨",
            rules=["代码必须通过类型检查 🎯", "使用中文注释也是允许的"],
        )
        ctx = AssemblyContext(
            user_request=sample_user_request,
            guides=guides,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        content = result[0].content
        assert "🐍✨" in content
        assert "🎯" in content
        assert "中文" in content


# ============================================================================
# AC-CA-03: Sliding window truncation
# ============================================================================


class TestSlidingWindow:
    """AC-CA-03: Sliding window truncation tests."""

    def test_sliding_window_under_limit(self):
        """AC-CA-03.1: When message count <= max, all messages preserved."""
        history = [
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
        ]
        assembler = SimpleAssembler(max_history=10)
        result = assembler._apply_sliding_window(history, 10)
        assert len(result) == 2

    def test_sliding_window_over_limit(self):
        """AC-CA-03.2: When message count > max, oldest non-system dropped."""
        history = [
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
            Message(role="user", content="msg3"),
            Message(role="assistant", content="msg4"),
            Message(role="user", content="msg5"),
        ]
        assembler = SimpleAssembler(max_history=3)
        result = assembler._apply_sliding_window(history, 3)
        # Only last 3 non-system messages kept
        assert len(result) == 3
        assert result[0].content == "msg3"
        assert result[-1].content == "msg5"

    def test_sliding_window_preserves_system(self):
        """AC-CA-03.3: System messages always preserved, not counted."""
        history = [
            Message(role="system", content="sys1"),
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
            Message(role="user", content="msg3"),
            Message(role="assistant", content="msg4"),
            Message(role="user", content="msg5"),
            Message(role="assistant", content="msg6"),
        ]
        assembler = SimpleAssembler(max_history=3)
        result = assembler._apply_sliding_window(history, 3)
        # 1 system + 3 non-system (last 3: msg4, msg5, msg6)
        assert len(result) == 4
        assert result[0].role == "system"
        assert result[0].content == "sys1"
        assert result[1].content == "msg4"

    def test_sliding_window_multiple_system(self):
        """AC-CA-03.4: Multiple system messages all preserved."""
        history = [
            Message(role="system", content="sys1"),
            Message(role="system", content="sys2"),
            Message(role="system", content="sys3"),
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
            Message(role="user", content="msg3"),
        ]
        assembler = SimpleAssembler(max_history=1)
        result = assembler._apply_sliding_window(history, 1)
        # 3 system + 1 non-system (last 1: msg3)
        assert len(result) == 4
        assert result[0].content == "sys1"
        assert result[1].content == "sys2"
        assert result[2].content == "sys3"
        assert result[3].content == "msg3"

    def test_sliding_window_max_zero(self):
        """AC-CA-03.5: max_history=0 keeps only system messages."""
        history = [
            Message(role="system", content="sys1"),
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
        ]
        assembler = SimpleAssembler(max_history=0)
        result = assembler._apply_sliding_window(history, 0)
        assert len(result) == 1
        assert result[0].role == "system"

    def test_sliding_window_max_one_no_system(self):
        """AC-CA-03.6: max_history=1 with no system, keeps last 1."""
        history = [
            Message(role="user", content="msg1"),
            Message(role="assistant", content="msg2"),
            Message(role="user", content="msg3"),
        ]
        assembler = SimpleAssembler(max_history=1)
        result = assembler._apply_sliding_window(history, 1)
        assert len(result) == 1
        assert result[0].content == "msg3"

    def test_sliding_window_empty_history(self):
        """AC-CA-03.7: Empty history returns empty list."""
        assembler = SimpleAssembler()
        result = assembler._apply_sliding_window([], 10)
        assert result == []


# ============================================================================
# AC-CA-04: available_tools injection
# ============================================================================


class TestToolsInjection:
    """AC-CA-04: available_tools injection tests."""

    def test_tools_injected(
        self,
        sample_tools: List[ToolDefinition],
        sample_user_request: UserRequest,
    ):
        """AC-CA-04.1: Tool names and descriptions appear in system prompt."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            available_tools=sample_tools,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        content = result[0].content
        for tool in sample_tools:
            assert tool.name in content
            assert tool.description in content

    def test_tools_empty(self, sample_user_request: UserRequest):
        """AC-CA-04.2: Empty tools shows "(none)"."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            available_tools=[],
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        assert "(none)" in result[0].content

    def test_tools_disabled(
        self, sample_tools: List[ToolDefinition], sample_user_request: UserRequest
    ):
        """AC-CA-04.3: include_tools=False hides tools."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            available_tools=sample_tools,
        )
        assembler = SimpleAssembler(include_tools=False)
        result = assembler.assemble(ctx)

        # Tool names should not appear in system prompt
        content = result[0].content
        assert sample_tools[0].name not in content


# ============================================================================
# AC-CA-05: Memory injection
# ============================================================================


class TestMemoryInjection:
    """AC-CA-05: Memory injection tests."""

    def test_memories_injected(
        self,
        sample_memories: List[MemoryItem],
        sample_user_request: UserRequest,
    ):
        """AC-CA-05.1: MemoryItem contents appear in system prompt."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=sample_memories,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        content = result[0].content
        assert "## Relevant Memories" in content
        for mem in sample_memories:
            assert mem.key in content

    def test_memories_empty(self, sample_user_request: UserRequest):
        """AC-CA-05.2: Empty memories omits memories section."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=[],
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        assert "## Relevant Memories" not in result[0].content

    def test_memories_disabled(
        self,
        sample_memories: List[MemoryItem],
        sample_user_request: UserRequest,
    ):
        """AC-CA-05.3: include_memories=False hides memories."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=sample_memories,
        )
        assembler = SimpleAssembler(include_memories=False)
        result = assembler.assemble(ctx)

        assert "## Relevant Memories" not in result[0].content

    def test_memories_multiple(
        self,
        sample_memories: List[MemoryItem],
        sample_user_request: UserRequest,
    ):
        """AC-CA-05.4: Multiple MemoryItems all included in order."""
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=sample_memories,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        content = result[0].content
        # Find positions of each memory item in the content
        positions = {}
        for mem in sample_memories:
            pos = content.find(mem.key)
            assert pos != -1, f"Memory key '{mem.key}' not found"
            positions[mem.key] = pos
        # Verify order: episodic first, then semantic, then procedural
        assert positions["session-001"] < positions["user-pref"]
        assert positions["user-pref"] < positions["skill-001"]


# ============================================================================
# AC-CA-06: Optional MemoryBackend injection
# ============================================================================


class TestMemoryBackendInjection:
    """AC-CA-06: Optional MemoryBackend injection tests."""

    def test_no_memory_backend(self, sample_user_request: UserRequest):
        """AC-CA-06.1: No MemoryBackend, uses only AssemblyContext.memories."""
        memories = [
            MemoryItem(
                key="baseline-001",
                value="Baseline memory",
                namespace="episodic",
            )
        ]
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=memories,
        )
        assembler = SimpleAssembler(memory=None)
        result = assembler.assemble(ctx)

        assert "baseline-001" in result[0].content

    def test_memory_backend_enhanced_retrieval(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-06.2: MemoryBackend injected, enhanced retrieval runs."""
        # Create mock MemoryBackend
        mock_memory = MagicMock(spec=MemoryBackend)
        mock_memory.search.return_value = [
            MemoryItem(
                key="enhanced-001",
                value="Enhanced retrieved memory",
                namespace="semantic",
            )
        ]

        memories = [
            MemoryItem(
                key="baseline-001",
                value="Baseline memory",
                namespace="episodic",
            )
        ]
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=memories,
        )
        assembler = SimpleAssembler(memory=mock_memory)
        result = assembler.assemble(ctx)

        # Should contain both baseline and enhanced
        assert "baseline-001" in result[0].content
        assert "enhanced-001" in result[0].content
        # Verify mock was called
        mock_memory.search.assert_called_once_with(
            sample_user_request.text, "semantic", limit=5
        )

    def test_memory_backend_disabled(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-06.3: include_memories=False skips enhanced retrieval."""
        mock_memory = MagicMock(spec=MemoryBackend)
        mock_memory.search.return_value = [
            MemoryItem(
                key="enhanced-001",
                value="Enhanced memory",
                namespace="semantic",
            )
        ]

        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=[],
        )
        assembler = SimpleAssembler(memory=mock_memory, include_memories=False)
        result = assembler.assemble(ctx)

        # Should not have memories section
        assert "## Relevant Memories" not in result[0].content
        # Search should NOT be called
        mock_memory.search.assert_not_called()

    def test_memory_backend_error_fallback(
        self, sample_user_request: UserRequest
    ):
        """AC-CA-06.4: memory.search() error falls back to baseline only."""
        mock_memory = MagicMock(spec=MemoryBackend)
        mock_memory.search.side_effect = RuntimeError("Search failed")

        memories = [
            MemoryItem(
                key="baseline-001",
                value="Baseline memory",
                namespace="episodic",
            )
        ]
        ctx = AssemblyContext(
            user_request=sample_user_request,
            memories=memories,
        )
        assembler = SimpleAssembler(memory=mock_memory)
        result = assembler.assemble(ctx)

        # Should still contain baseline memory
        assert "baseline-001" in result[0].content
        # Should not crash


# ============================================================================
# AC-CA-07: Boundary and error handling
# ============================================================================


class TestBoundaryHandling:
    """AC-CA-07: Boundary and error handling tests."""

    def test_all_none_fields(self):
        """AC-CA-07.1: All optional fields None, returns system + user only."""
        ctx = AssemblyContext(
            user_request=None,
            guides=None,
            available_tools=[],
            history=[],
            memories=[],
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        # Should return [system, user]
        assert len(result) == 2
        assert result[0].role == "system"
        assert result[1].role == "user"
        assert result[1].content == ""

    def test_empty_user_text(self, sample_guides: GuidesBundle):
        """AC-CA-07.2: Empty user text produces user message with empty content."""
        ctx = AssemblyContext(
            user_request=UserRequest(text=""),
            guides=sample_guides,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        assert result[-1].role == "user"
        assert result[-1].content == ""

    def test_long_identity(self):
        """AC-CA-07.3: Very long identity text is preserved in full."""
        long_text = "A" * 5000
        guides = GuidesBundle(identity=long_text)
        ctx = AssemblyContext(
            user_request=UserRequest(text="test"),
            guides=guides,
        )
        assembler = SimpleAssembler()
        result = assembler.assemble(ctx)

        assert long_text in result[0].content

    def test_tool_role_messages(self):
        """AC-CA-07.4: tool role messages correctly preserved after truncation."""
        history = [
            Message(role="user", content="call tool"),
            Message(role="assistant", content="using tool"),
            Message(role="tool", content="tool result", tool_call_id="call_1"),
            Message(role="assistant", content="done"),
        ]
        assembler = SimpleAssembler(max_history=3)
        result = assembler._apply_sliding_window(history, 3)

        # Last 3 non-system messages: assistant, tool, assistant
        assert len(result) == 3
        assert result[0].role == "assistant"
        assert result[1].role == "tool"
        assert result[1].tool_call_id == "call_1"
        assert result[2].role == "assistant"


# ============================================================================
# AC-CA-08: Protocol compliance
# ============================================================================


class TestProtocolCompliance:
    """AC-CA-08: Protocol compliance tests."""

    def test_protocol_compliance(self):
        """AC-CA-08.1: SimpleAssembler passes isinstance check."""
        assembler = SimpleAssembler()
        assert isinstance(assembler, ContextAssembler)

    def test_di_registration(self):
        """AC-CA-08.2: SimpleAssembler can be registered in DI container."""
        container = DIContainer()
        assembler = SimpleAssembler()
        container.register(ContextAssembler, assembler)
        resolved = container.resolve(ContextAssembler)
        assert resolved is assembler

    def test_signature_match(self):
        """AC-CA-08.3: assemble() signature matches Protocol."""
        assembler = SimpleAssembler()
        ctx = AssemblyContext()
        result = assembler.assemble(ctx)
        assert isinstance(result, list)


# ============================================================================
# AC-CA-10: E2E with MdMemory
# ============================================================================


class TestE2EWithMdMemory:
    """AC-CA-10: E2E integration with MdMemory."""

    def test_e2e_with_md_memory(
        self,
        tmp_path: Path,
        sample_guides: GuidesBundle,
        sample_user_request: UserRequest,
    ):
        """AC-CA-10.1: Full pipeline with MdMemory."""
        # Setup MdMemory
        memory_path = tmp_path / "memory"
        md_memory = MdMemory(path=str(memory_path))

        # Write some memories
        md_memory.write(
            "pref-001", "User prefers Python and uses pytest", "semantic"
        )
        md_memory.write(
            "skill-001", "User knows advanced Python async/await patterns", "semantic"
        )

        # Create assembler with memory
        assembler = SimpleAssembler(memory=md_memory)

        # Build context — query "Python" is substring of both stored values
        ctx = AssemblyContext(
            user_request=UserRequest(text="Python"),
            guides=sample_guides,
            memories=[
                MemoryItem(
                    key="ep-001",
                    value="Previous conversation about testing",
                    namespace="episodic",
                )
            ],
        )

        result = assembler.assemble(ctx)

        # System message should contain guides + baseline + enhanced memories
        content = result[0].content
        assert sample_guides.identity in content
        assert "ep-001" in content  # baseline
        assert "pref-001" in content  # enhanced
        assert "skill-001" in content  # enhanced

    def test_e2e_md_memory_no_match(
        self,
        tmp_path: Path,
        sample_guides: GuidesBundle,
    ):
        """AC-CA-10.2: MdMemory with no matches, only guides content."""
        memory_path = tmp_path / "memory"
        md_memory = MdMemory(path=str(memory_path))

        assembler = SimpleAssembler(memory=md_memory)
        ctx = AssemblyContext(
            user_request=UserRequest(text="xyz"),
            guides=sample_guides,
            memories=[],
        )

        result = assembler.assemble(ctx)
        content = result[0].content

        # Should contain guides
        assert sample_guides.identity in content
        # Should not have memories section (no matches)
        assert "## Relevant Memories" not in content


# ============================================================================
# AC-CA-11: E2E with FileGuideProvider
# ============================================================================


class TestE2EWithFileGuideProvider:
    """AC-CA-11: E2E integration with FileGuideProvider."""

    def test_e2e_with_file_guide_provider(
        self, tmp_path: Path
    ):
        """AC-CA-11.1: Full pipeline with FileGuideProvider."""
        # Create AGENTS.md
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            """# You are a test assistant.

You help with Python testing.

## 规则
- Always write tests first
- Keep tests simple

## 约束
- Never skip CI checks
""",
            encoding="utf-8",
        )

        # Parse with FileGuideProvider
        provider = FileGuideProvider(str(agents_md))
        context = GuideContext()
        guides = provider.get_guides(context)

        # Assemble with SimpleAssembler
        assembler = SimpleAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="Write tests"),
            guides=guides,
        )
        result = assembler.assemble(ctx)

        content = result[0].content
        assert "You are a test assistant" in content
        assert "Always write tests first" in content
        assert "Never skip CI checks" in content

    def test_e2e_empty_guide_file(self, tmp_path: Path):
        """AC-CA-11.2: Empty guide file → empty GuidesBundle → no crash."""
        agents_md = tmp_path / "EMPTY.md"
        agents_md.write_text("", encoding="utf-8")

        provider = FileGuideProvider(str(agents_md))
        context = GuideContext()
        guides = provider.get_guides(context)

        assembler = SimpleAssembler()
        ctx = AssemblyContext(
            user_request=UserRequest(text="hello"),
            guides=guides,
        )
        result = assembler.assemble(ctx)

        assert result[0].role == "system"
        assert result[-1].role == "user"
        assert result[-1].content == "hello"


# ============================================================================
# AC-CA-12: Full pipeline E2E
# ============================================================================


class TestE2EFullPipeline:
    """AC-CA-12: Full pipeline E2E tests."""

    def test_e2e_full_pipeline(self, tmp_path: Path):
        """AC-CA-12.1: FileGuideProvider + MdMemory + SimpleAssembler."""
        # 1. Create AGENTS.md
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            """# You are a Python coding assistant.

You help write production-quality Python code.

## 规则
- Always use type hints
- Write docstrings for all public functions
""",
            encoding="utf-8",
        )

        # 2. Parse guides
        provider = FileGuideProvider(str(agents_md))
        context = GuideContext()
        guides = provider.get_guides(context)

        # 3. Create MdMemory and write memories
        #    (stored value must contain query substring for MdMemory.search)
        memory_path = tmp_path / "memory"
        md_memory = MdMemory(path=str(memory_path))
        md_memory.write(
            "pref-001",
            "User prefers Python module architecture patterns",
            "semantic",
        )

        # 4. Build AssemblyContext
        #    (user_request.text "Python module" is a substring of stored value)
        ctx = AssemblyContext(
            user_request=UserRequest(text="Python module"),
            guides=guides,
            available_tools=[
                ToolDefinition(
                    name="run_tests",
                    description="Run the test suite",
                    parameters={},
                ),
            ],
            history=[
                Message(role="user", content="Previous question"),
                Message(role="assistant", content="Previous answer"),
            ],
            memories=[
                MemoryItem(
                    key="ep-001",
                    value="User mentioned pytest earlier",
                    namespace="episodic",
                ),
            ],
        )

        # 5. Assemble
        assembler = SimpleAssembler(memory=md_memory)
        result = assembler.assemble(ctx)

        # 6. Verify structure
        assert len(result) >= 3  # system + history(2) + user
        assert result[0].role == "system"
        assert result[-1].role == "user"

        content = result[0].content

        # Guides content present
        assert "Python coding assistant" in content
        assert "Always use type hints" in content

        # Tools present
        assert "run_tests" in content
        assert "## Available Tools" in content

        # Baseline memory present
        assert "ep-001" in content

        # Enhanced memory present (from MdMemory.search)
        assert "pref-001" in content

        # Memories section present
        assert "## Relevant Memories" in content

        # History present
        history_in_result = [
            m for m in result[1:-1] if m.role in ("user", "assistant")
        ]
        assert len(history_in_result) == 2

        # User request as last message
        assert result[-1].content == "Python module"
