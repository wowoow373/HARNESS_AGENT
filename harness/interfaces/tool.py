"""Tool 接口 — 工具的实际执行层。

Tool 被 ToolRegistry 统一调度。用户不直接实现 Tool 接口，
通过 MCPManager 间接注入。
"""

from typing import Any, Dict, Protocol, runtime_checkable

from .types import ToolDefinition, ToolResult


@runtime_checkable
class Tool(Protocol):
    """工具执行接口。

    每个 Tool 实例提供自身的元信息描述和具体的执行逻辑。
    ToolRegistry 通过此接口统一管理所有工具的发现与调度。

    调用时机：
    - get_definition()：会话初始化阶段（ToolRegistry 收集工具元信息）
    - execute()：运行时（LLM 请求执行工具时）
    """

    def get_definition(self) -> ToolDefinition:
        """返回工具的元信息定义。

        Returns:
            ToolDefinition: 工具名称、描述、参数 schema。
        """
        ...

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """执行工具并返回结果。

        Args:
            args: 工具调用参数（由 LLM 输出解析而来）。

        Returns:
            ToolResult: 执行结果（success/content/error）。
        """
        ...
