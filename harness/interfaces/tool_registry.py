"""ToolRegistry 接口 — 工具调度。

管理所有 Tool 的注册、发现与调度执行。
ToolRegistry 是框架内部组件（框架创建和管理），不作为用户可替换接口注册到 DI 容器。
"""

from typing import Any, Dict, List, Protocol, runtime_checkable

from .tool import Tool
from .types import ToolDefinition, ToolResult


@runtime_checkable
class ToolRegistry(Protocol):
    """工具注册表接口。

    职责：管理所有 Tool 的注册、发现与调度执行。

    调用时机：
    - register()：会话初始化阶段（系统 Tool + MCPManager 加载的 Tool）
    - list_tools()：会话初始化阶段（收集元信息给 ContextAssembler）
    - execute()：运行时（LLM 请求执行工具时）

    设计要点：
    - 系统基础 Tool 直接注册到 ToolRegistry
    - MCPManager 加载的 Tool 也通过 register() 注入
    - 执行前后触发 before_tool_execute / after_tool_execute Hook
    """

    def register(self, tool: Tool) -> None:
        """注册一个工具实例。

        Args:
            tool: 实现 Tool 接口的工具实例。
        """
        ...

    def list_tools(self) -> List[ToolDefinition]:
        """列出所有已注册工具的元信息。

        Returns:
            List[ToolDefinition]: 工具定义列表（name, description, parameters）。
        """
        ...

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """按名称执行工具。

        Args:
            name: 工具名称。
            args: 工具调用参数。

        Returns:
            ToolResult: 工具执行结果。
        """
        ...
