# tests/runtime/test_tools.py

"""Tests for harness.runtime.tools — CompositeSystemToolProvider + 5 Runtime Tools."""

import json
import pytest
from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider

from harness.runtime.tools import (
    CompositeSystemToolProvider,
    SpawnWorkflowTool,
    EndWorkflowTool,
    FinishAgentTool,
    TalkToTool,
    ListAgentsTool,
    create_runtime_tools,
)


# ── Helpers ──

class _DummyTool(BaseTool):
    """Dummy tool for testing CompositeSystemToolProvider."""
    def get_definition(self):
        return ToolDefinition(
            name="dummy",
            description="A dummy tool",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, args):
        return ToolResult(success=True, content="dummy_result")


class _FakeKernel:
    """Mock Kernel — minimal interface for Tool unit tests."""
    def __init__(self):
        self.runtime_table = {}
        self.workflow_table = {}
        self._spawn_counter = 0
        self._called_spawn_from_script = None
        self._called_end_workflow = None
        self._called_finish_agent = None
        self._called_send_input = None
        self._called_list_agents = None

    def spawn_from_script(self, script_path, parent=None):
        self._called_spawn_from_script = (script_path, parent)
        self._spawn_counter += 1
        return {
            "workflow_flag": f"wf_{self._spawn_counter:03d}",
            "agents": [
                {"pid": "collector", "parent": None, "metadata": {}},
            ],
        }

    def end_workflow(self, flag):
        self._called_end_workflow = flag
        return ["collector"]

    def finish_agent(self, pid):
        self._called_finish_agent = pid

    def send_input(self, pid, request):
        self._called_send_input = (pid, request)

    def list_agents(self):
        self._called_list_agents = True
        return {"root": {"state": "running", "mode": "continuous"}}


# ── CompositeSystemToolProvider ──

class TestCompositeSystemToolProvider:
    """CompositeSystemToolProvider unit tests."""

    def test_get_tools_merges_user_and_runtime(self):
        """get_tools() merges user tools + runtime tools."""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[_DummyTool()],
        )

        tools = composite.get_tools()

        # DefaultSystemToolProvider has read_file/write_file/shell builtins
        assert len(tools) >= 4  # 3 builtins + runtime tool
        # Runtime tool should be at the end
        assert tools[-1].name == "dummy"

    def test_execute_runtime_tool_first(self):
        """execute() checks runtime tools first."""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[_DummyTool()],
        )

        result = composite.execute("dummy", {})

        assert result.success is True
        assert result.content == "dummy_result"

    def test_execute_falls_back_to_user_provider(self):
        """Tool not in runtime falls back to user provider."""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[],
        )

        result = composite.execute("read_file", {"path": "/tmp/__nonexistent_file__98765.txt"})

        # read_file is a DefaultSystemToolProvider builtin
        assert result.success is False  # file not found
        assert "File not found" in result.error

    def test_execute_raises_keyerror_when_not_found(self):
        """Neither runtime nor user provider has tool -> KeyError."""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[],
        )

        with pytest.raises(KeyError, match="nonexistent"):
            composite.execute("nonexistent", {})

    def test_no_user_provider_creates_default(self):
        """No user_provider -> auto-creates DefaultSystemToolProvider."""
        composite = CompositeSystemToolProvider()

        tools = composite.get_tools()

        assert len(tools) >= 3  # DefaultSystemToolProvider builtins

    def test_empty_runtime_tools(self):
        """Empty runtime_tools -> only user tools."""
        provider = DefaultSystemToolProvider(use_builtins=False, tools=[_DummyTool()])
        composite = CompositeSystemToolProvider(
            user_provider=provider,
            runtime_tools=[],
        )

        tools = composite.get_tools()

        assert len(tools) == 1  # only _DummyTool (as user_provider)
        assert tools[0].name == "dummy"

    def test_runtime_tool_priority_over_user_tool(self):
        """Same name -> Runtime tool wins over user tool execute."""
        user_tool = _DummyTool()

        class _OverrideTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(
                    name="dummy",
                    description="overrides",
                    parameters={"type": "object", "properties": {}, "required": []},
                )
            def execute(self, args):
                return ToolResult(success=True, content="runtime_wins")

        composite = CompositeSystemToolProvider(
            user_provider=user_tool,
            runtime_tools=[_OverrideTool()],
        )

        result = composite.execute("dummy", {})
        assert result.content == "runtime_wins"


# ── Runtime Tools ──

class TestSpawnWorkflowTool:
    """SpawnWorkflowTool tests."""

    def test_get_definition(self):
        kernel = _FakeKernel()
        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")

        d = tool.get_definition()
        assert d.name == "spawn_workflow"
        assert "script_path" in str(d.parameters)

    def test_execute_success(self):
        kernel = _FakeKernel()
        kernel.runtime_table["root"] = object()  # dummy parent
        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")

        result = tool.execute({"script_path": "wf.py"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["workflow_flag"] == "wf_001"
        assert len(data["agents"]) == 1
        assert kernel._called_spawn_from_script is not None

    def test_execute_failure(self):
        kernel = _FakeKernel()
        kernel.runtime_table["root"] = object()

        def failing_spawn(path, parent=None):
            raise FileNotFoundError("no such file")
        kernel.spawn_from_script = failing_spawn

        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")
        result = tool.execute({"script_path": "nonexistent.py"})

        assert result.success is False
        assert "FileNotFoundError" in result.error


class TestEndWorkflowTool:
    """EndWorkflowTool tests."""

    def test_get_definition(self):
        tool = EndWorkflowTool(kernel=_FakeKernel())
        d = tool.get_definition()
        assert d.name == "end_workflow"
        assert "flag" in d.parameters["required"]

    def test_execute_success(self):
        kernel = _FakeKernel()
        tool = EndWorkflowTool(kernel=kernel)

        result = tool.execute({"flag": "wf_001"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["ok"] is True
        assert "collector" in data["killed"]
        assert kernel._called_end_workflow == "wf_001"


class TestFinishAgentTool:
    """FinishAgentTool tests."""

    def test_get_definition(self):
        tool = FinishAgentTool(kernel=_FakeKernel(), pid="worker")
        d = tool.get_definition()
        assert d.name == "finish_agent"

    def test_execute_calls_finish_agent_with_pid(self):
        kernel = _FakeKernel()
        tool = FinishAgentTool(kernel=kernel, pid="worker")

        result = tool.execute({})

        assert result.success is True
        assert kernel._called_finish_agent == "worker"


class TestTalkToTool:
    """TalkToTool tests."""

    def test_get_definition(self):
        tool = TalkToTool(kernel=_FakeKernel(), from_pid="root")
        d = tool.get_definition()
        assert d.name == "talk_to"
        assert "pid" in d.parameters["required"]
        assert "text" in d.parameters["required"]

    def test_execute_sends_input(self):
        kernel = _FakeKernel()
        tool = TalkToTool(kernel=kernel, from_pid="root")

        result = tool.execute({"pid": "collector", "text": "hello"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["target"] == "collector"
        assert kernel._called_send_input is not None
        pid, req = kernel._called_send_input
        assert pid == "collector"
        assert req.text == "hello"
        assert req.metadata["from"] == "root"
        assert req.metadata["type"] == "talk_to"


class TestListAgentsTool:
    """ListAgentsTool tests."""

    def test_get_definition(self):
        tool = ListAgentsTool(kernel=_FakeKernel())
        d = tool.get_definition()
        assert d.name == "list_agents"

    def test_execute_returns_agent_list(self):
        kernel = _FakeKernel()
        tool = ListAgentsTool(kernel=kernel)

        result = tool.execute({})

        assert result.success is True
        data = json.loads(result.content)
        assert "root" in data["agents"]
        assert kernel._called_list_agents is True


class TestCreateRuntimeTools:
    """create_runtime_tools factory function tests."""

    def test_returns_five_tools(self):
        kernel = _FakeKernel()
        tools = create_runtime_tools(kernel=kernel, pid="root")

        assert len(tools) == 5
        names = [t.get_definition().name for t in tools]
        assert names == [
            "spawn_workflow",
            "end_workflow",
            "finish_agent",
            "talk_to",
            "list_agents",
        ]
