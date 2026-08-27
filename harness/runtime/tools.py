# harness/runtime/tools.py

"""Runtime management tool set.

Provides CompositeSystemToolProvider (wraps user's SystemToolProvider) and
5 Runtime management tools: spawn_workflow / end_workflow / finish_agent /
talk_to / list_agents.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..components.tool.base import BaseTool
from ..components.tool.default_system_tool_provider import DefaultSystemToolProvider
from ..core.session.ids import new_msg_id
from ..interfaces.types import ToolDefinition, ToolResult, UserRequest

if TYPE_CHECKING:
    from .kernel import Kernel

logger = logging.getLogger(__name__)


# ── CompositeSystemToolProvider ──────────────────────────────────────────


class CompositeSystemToolProvider:
    """Composite SystemToolProvider.

    Wraps the user's original SystemToolProvider with Runtime management
    tools. Runtime tools take priority over user tools on execute().
    """

    def __init__(
        self,
        user_provider: Optional[object] = None,
        runtime_tools: Optional[List[BaseTool]] = None,
    ):
        if user_provider is None:
            user_provider = DefaultSystemToolProvider()
        self._user = user_provider

        self._runtime_tools: Dict[str, BaseTool] = {}
        if runtime_tools:
            for tool in runtime_tools:
                self._register_runtime_tool(tool)

    # ── SystemToolProvider protocol ──

    def get_tools(self) -> List[ToolDefinition]:
        user_defs = self._user.get_tools() if self._user else []
        runtime_defs = [t.get_definition() for t in self._runtime_tools.values()]
        return user_defs + runtime_defs

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        if name in self._runtime_tools:
            return self._runtime_tools[name].execute(args)

        if self._user:
            # 只有"工具不存在"才 fall through；工具执行异常必须穿透，
            # 交由上层治理层捕获——否则会把工具 bug 误报成 KeyError。
            has_tool = getattr(self._user, "has_tool", None)
            if has_tool is None or has_tool(name):
                return self._user.execute(name, args)

        raise KeyError(
            f"Tool '{name}' not found in CompositeSystemToolProvider"
        )

    # ── Internal ──

    def _register_runtime_tool(self, tool: BaseTool) -> None:
        name = tool.get_definition().name
        if name in self._runtime_tools:
            logger.warning(
                f"Runtime tool '{name}' already registered, overwriting"
            )
        self._runtime_tools[name] = tool

    @property
    def tool_count(self) -> int:
        return len(self._runtime_tools)

    def has_runtime_tool(self, name: str) -> bool:
        return name in self._runtime_tools


# ── Runtime Tools ────────────────────────────────────────────────────────


class SpawnWorkflowTool(BaseTool):
    """Load a workflow script, create child agents, and start them."""

    def __init__(self, kernel: Kernel, parent_pid: str):
        self._kernel = kernel
        self._parent_pid = parent_pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="spawn_workflow",
            description=(
                "Load a workflow from a Python script file, create all agents "
                "declared in it, and start execution. Returns workflow_flag "
                "(for subsequent end_workflow) and agent list (with pid and "
                "metadata). Child agents begin executing entry_prompt "
                "immediately after creation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the workflow script (.py file). "
                            "The script uses @agent decorator to declare agent "
                            "assembly logic."
                        ),
                    },
                },
                "required": ["script_path"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        script_path = args["script_path"]
        try:
            parent = self._kernel.runtime_table.get(self._parent_pid)
            result = self._kernel.spawn_from_script(script_path, parent=parent)
            return ToolResult(
                success=True,
                content=json.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            logger.error(f"spawn_workflow failed: {e}")
            return ToolResult(
                success=False,
                content="",
                error=f"{type(e).__name__}: {e}",
            )


class EndWorkflowTool(BaseTool):
    """Terminate all agents in a workflow."""

    def __init__(self, kernel: Kernel):
        self._kernel = kernel

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="end_workflow",
            description=(
                "Terminate all agents in the specified workflow. "
                "All agents receive an exit signal and go through normal "
                "cleanup (_phase_end) before finishing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "flag": {
                        "type": "string",
                        "description": (
                            "Workflow identifier. The workflow_flag returned "
                            "by spawn_workflow."
                        ),
                    },
                },
                "required": ["flag"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        flag = args["flag"]
        killed = self._kernel.end_workflow(flag)
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True, "killed": killed}),
        )


class FinishAgentTool(BaseTool):
    """Current agent self-termination."""

    def __init__(self, kernel: Kernel, pid: str):
        self._kernel = kernel
        self._pid = pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="finish_agent",
            description=(
                "Mark the current agent's task as complete and trigger "
                "normal exit flow. Only call when the agent is certain "
                "all work is done."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        self._kernel.finish_agent(self._pid)
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True}),
        )


class TalkToTool(BaseTool):
    """Send a directed message to a specific agent."""

    def __init__(self, kernel: Kernel, from_pid: str):
        self._kernel = kernel
        self._from_pid = from_pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="talk_to",
            description=(
                "Send a directed message to a specified agent. The message "
                "is delivered directly to the target agent's input queue, "
                "bypassing subscription routing. The target agent sees this "
                "message in its next conversation round."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": (
                            "Target agent pid. Obtain from the agents list "
                            "returned by spawn_workflow."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message content to send.",
                    },
                },
                "required": ["pid", "text"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        target_pid = args["pid"]
        text = args["text"]
        msg_id = new_msg_id()
        self._kernel.send_input(
            target_pid,
            UserRequest(
                text=text,
                metadata={
                    "from": self._from_pid,
                    "type": "talk_to",
                    "msg_id": msg_id,
                },
            ),
        )
        return ToolResult(
            success=True,
            content=json.dumps(
                {"ok": True, "target": target_pid, "msg_id": msg_id},
                ensure_ascii=False,
            ),
        )


class ListAgentsTool(BaseTool):
    """List all agents' current state in the Kernel."""

    def __init__(self, kernel: Kernel):
        self._kernel = kernel

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_agents",
            description=(
                "List state information for all agents currently in the "
                "Runtime, including pid, state, mode, parent, rounds "
                "executed, and error status."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        agents = self._kernel.list_agents()
        return ToolResult(
            success=True,
            content=json.dumps({"agents": agents}, ensure_ascii=False),
        )


# ── Factory Function ─────────────────────────────────────────────────────


def create_runtime_tools(kernel: Kernel, pid: str) -> list[BaseTool]:
    """Create the list of Runtime management tools for an agent.

    Args:
        kernel: Kernel global singleton reference.
        pid: The current agent's pid.

    Returns:
        List of 5 Runtime tool instances.
    """
    return [
        SpawnWorkflowTool(kernel=kernel, parent_pid=pid),
        EndWorkflowTool(kernel=kernel),
        FinishAgentTool(kernel=kernel, pid=pid),
        TalkToTool(kernel=kernel, from_pid=pid),
        ListAgentsTool(kernel=kernel),
    ]
