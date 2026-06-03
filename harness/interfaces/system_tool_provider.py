"""SystemToolProvider 接口 — 系统工具提供者。

管理本地实现的 Tool 集合。用户可替换，通过 DI 容器注册。
与 MCPAdapter 平级，各自独立管理自己的工具集。

调用时机：
- get_tools()：会话初始化阶段（ToolRouter 收集各 Provider 的工具元信息）
- execute()：运行时（LLM 请求执行工具时，ToolRouter 查表分发）
"""

from typing import Any, Dict, List, Protocol, runtime_checkable

from .types import ToolDefinition, ToolResult


@runtime_checkable
class SystemToolProvider(Protocol):
    """系统工具提供者接口。

    职责：管理本地实现的 Tool 集合（如 ReadFileTool、WriteFileTool、ShellTool）。
    与 MCPAdapter 平级，通过 ToolRouter 合并后暴露给编排器。

    自定义方式：
    - 实现此 Protocol 并注册到 DI 容器即可完全替换内置工具集
    - DefaultSystemToolProvider 内置了 read_file/write_file/shell 三个基础工具
    - 使用 BaseTool 和 @inline_tool 辅助编写自定义 Tool

    调用时机：
    - get_tools()：会话初始化阶段
    - execute()：运行时（每次工具调用）
    """

    def get_tools(self) -> List[ToolDefinition]:
        """返回所有系统工具的元信息列表。

        Returns:
            List[ToolDefinition]: 工具定义列表（name, description, parameters）。
        """
        ...

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """按名称执行指定的系统工具。

        Args:
            name: 工具名称。
            args: 工具调用参数。

        Returns:
            ToolResult: 工具执行结果（success/content/error）。
        """
        ...
