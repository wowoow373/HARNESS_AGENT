# tests/runtime/test_spawn_from_script.py

"""Tests for Kernel.spawn_from_script() and _inject_runtime_tools()."""

import asyncio
import os
import sys
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.system_tool_provider import SystemToolProvider
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentState
from harness.runtime.tools import CompositeSystemToolProvider


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole — records send calls."""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


def _make_minimal_harness(call_llm=None):
    """Create a minimal Harness instance for @agent factory use."""
    container = DIContainer()
    container.register(InputAdapter, object())  # dummy, won't be called with call_llm=None
    return Harness.from_container(container, call_llm=call_llm)


def _write_workflow_script(agent_count=2, with_subscribe=False):
    """Create a temp workflow script, return its path."""
    agents_block = ""
    if agent_count >= 1:
        agents_block += '''
@agent("collector", entry_prompt="collect data")
def assemble_collector():
    container = DIContainer()
    container.register(InputAdapter, object())  # dummy
    return Harness.from_container(container, call_llm=None)
'''
    if agent_count >= 2:
        agents_block += '''
@agent("analyzer", entry_prompt="analyze data", metadata={"desc": "analyzer"})
def assemble_analyzer():
    container = DIContainer()
    container.register(InputAdapter, object())  # dummy
    return Harness.from_container(container, call_llm=None)
'''

    subscribe_block = ""
    if with_subscribe and agent_count >= 2:
        subscribe_block = '\nsubscribe("analyzer").to("collector")\n'

    content = f'''from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import agent, subscribe
{agents_block}
{subscribe_block}
'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(content)
        path = f.name
    return path


# ── _inject_runtime_tools ──

class TestInjectRuntimeTools:
    """Kernel._inject_runtime_tools tests."""

    def test_injects_composite_provider(self):
        """SystemToolProvider is CompositeSystemToolProvider after injection."""
        kernel = Kernel(_MockConsole())
        container = DIContainer()

        kernel._inject_runtime_tools(container, pid="test")

        provider = container.resolve(SystemToolProvider)
        assert isinstance(provider, CompositeSystemToolProvider)

    def test_preserves_user_tools(self):
        """User's original tools are preserved in the Composite."""
        from harness.components.tool.base import BaseTool
        from harness.interfaces.types import ToolDefinition, ToolResult

        class _UserTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(
                    name="user_tool",
                    description="u",
                    parameters={"type": "object", "properties": {}, "required": []},
                )
            def execute(self, args):
                return ToolResult(success=True, content="user")

        from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
        user_provider = DefaultSystemToolProvider(extra_tools=[_UserTool()])
        container = DIContainer()
        container.register(SystemToolProvider, user_provider)

        kernel = Kernel(_MockConsole())
        kernel._inject_runtime_tools(container, pid="test")

        provider = container.resolve(SystemToolProvider)
        tools = provider.get_tools()
        tool_names = [t.name for t in tools]
        assert "user_tool" in tool_names
        assert "spawn_workflow" in tool_names

    def test_runtime_tools_are_executable(self):
        """Injected runtime tools can be executed."""
        kernel = Kernel(_MockConsole())
        container = DIContainer()

        kernel._inject_runtime_tools(container, pid="test")
        provider = container.resolve(SystemToolProvider)

        result = provider.execute("list_agents", {})
        assert result.success is True

    def test_user_tool_exception_propagates(self):
        """User tool 执行抛异常应穿透（不被吞成 KeyError）。"""
        from harness.components.tool.base import BaseTool
        from harness.interfaces.types import ToolDefinition
        from harness.components.tool.default_system_tool_provider import (
            DefaultSystemToolProvider,
        )

        class _BoomTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(
                    name="boom", description="b",
                    parameters={"type": "object", "properties": {}, "required": []},
                )
            def execute(self, args):
                raise RuntimeError("boom")

        user_provider = DefaultSystemToolProvider(
            tools=[_BoomTool()], use_builtins=False,
        )
        container = DIContainer()
        container.register(SystemToolProvider, user_provider)

        kernel = Kernel(_MockConsole())
        kernel._inject_runtime_tools(container, pid="test")
        provider = container.resolve(SystemToolProvider)

        with pytest.raises(RuntimeError, match="boom"):
            provider.execute("boom", {})


# ── spawn_from_script ──

class TestSpawnFromScript:
    """Kernel.spawn_from_script integration tests."""

    def test_creates_agents_in_runtime_table(self):
        """spawn_from_script registers agents in runtime_table."""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            assert "collector" in kernel.runtime_table
            assert "analyzer" in kernel.runtime_table
            assert result["workflow_flag"].startswith("wf_")
            assert len(result["agents"]) == 2
        finally:
            os.unlink(path)

    def test_returns_correct_agent_metadata(self):
        """Return value has correct pid/parent/metadata per agent."""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            agent_map = {a["pid"]: a for a in result["agents"]}
            assert agent_map["collector"]["parent"] is None
            assert agent_map["analyzer"]["metadata"]["desc"] == "analyzer"
        finally:
            os.unlink(path)

    def test_oneshot_mode_when_no_subscribe(self):
        """Agent without subscribe -> oneshot mode."""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            rt = kernel.runtime_table["collector"]
            assert rt.mode == "oneshot"
        finally:
            os.unlink(path)

    def test_continuous_mode_when_subscriber(self):
        """Agent with subscribe -> continuous mode."""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert kernel.runtime_table["analyzer"].mode == "continuous"
        finally:
            os.unlink(path)

    def test_continuous_mode_when_publisher(self):
        """Agent that is a publisher -> continuous mode."""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert kernel.runtime_table["collector"].mode == "continuous"
        finally:
            os.unlink(path)

    def test_entry_prompt_delivered(self):
        """entry_prompt is delivered to agent's input_queue."""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            msg = kernel.input_queues["collector"].get_nowait()
            assert msg.text == "collect data"
            assert msg.metadata["workflow_flag"].startswith("wf_")
        finally:
            os.unlink(path)

    def test_workflow_table_recorded(self):
        """workflow_table records workflow_flag -> pid list."""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            flag = result["workflow_flag"]
            assert flag in kernel.workflow_table
            assert set(kernel.workflow_table[flag]) == {"collector", "analyzer"}
        finally:
            os.unlink(path)

    def test_subscriptions_registered_to_message_bus(self):
        """Batch 3: subscribe relationships registered directly to MessageBus."""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            # Subscriptions now go to MessageBus, not _pending_subscriptions
            subscribers = kernel.message_bus.get_subscribers_of("collector")
            assert "analyzer" in subscribers
            assert kernel._pending_subscriptions == []
        finally:
            os.unlink(path)

    def test_input_queues_created(self):
        """Each agent gets an input_queue."""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert "collector" in kernel.input_queues
            assert isinstance(kernel.input_queues["collector"], asyncio.Queue)
        finally:
            os.unlink(path)

    def test_no_agent_declarations_raises(self):
        """No @agent declarations -> ValueError."""
        path = _write_workflow_script(agent_count=0)
        try:
            kernel = Kernel(_MockConsole())
            with pytest.raises(ValueError, match="No @agent declarations"):
                kernel.spawn_from_script(path)
        finally:
            os.unlink(path)

    def test_invalid_subscribe_reference_raises(self):
        """subscribe to unknown agent -> ValueError."""
        content = """from harness.runtime.decorators import agent, subscribe
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter

@agent("only_one", entry_prompt="go")
def make():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

subscribe("unknown").to("only_one")
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False
        ) as f:
            f.write(content)
            path = f.name

        try:
            kernel = Kernel(_MockConsole())
            with pytest.raises(ValueError, match="subscribe.*unknown"):
                kernel.spawn_from_script(path)
        finally:
            os.unlink(path)

    def test_spawn_counter_increments(self):
        """_spawn_counter increments on each spawn."""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            assert kernel._spawn_counter == 0

            kernel.spawn_from_script(path)
            assert kernel._spawn_counter == 1
            assert kernel.workflow_table["wf_001"] == ["collector"]

            kernel.spawn_from_script(path)
            assert kernel._spawn_counter == 2
            assert kernel.workflow_table["wf_002"] == ["collector"]
        finally:
            os.unlink(path)

    def test_parent_children_recorded(self):
        """spawn_from_script with parent -> parent.children has child pids."""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            from harness.runtime.agent_runtime import AgentRuntime
            parent = AgentRuntime(
                pid="parent_agent", mode="continuous",
                harness=_make_minimal_harness(), kernel=kernel,
            )

            kernel.spawn_from_script(path, parent=parent)

            assert "collector" in parent.children
            assert kernel.runtime_table["collector"].parent is parent
        finally:
            os.unlink(path)


# ── end_workflow 返回值 ──

class TestEndWorkflowReturns:
    """Batch 2: end_workflow returns killed pids list."""

    def test_end_workflow_returns_killed_pids(self):
        """end_workflow() returns list of killed pids."""
        kernel = Kernel(_MockConsole())
        kernel.workflow_table["wf_test"] = ["a", "b"]

        from harness.runtime.agent_runtime import AgentRuntime
        for pid in ["a", "b"]:
            rt = AgentRuntime(
                pid=pid, mode="oneshot",
                harness=_make_minimal_harness(), kernel=kernel,
            )
            kernel.runtime_table[pid] = rt
            kernel.input_queues[pid] = asyncio.Queue()

        killed = kernel.end_workflow("wf_test")
        assert set(killed) == {"a", "b"}

    def test_end_workflow_unknown_flag_returns_empty(self):
        """Unknown flag -> empty list."""
        kernel = Kernel(_MockConsole())
        killed = kernel.end_workflow("nonexistent")
        assert killed == []
