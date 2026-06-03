"""Tool 接口 — 单个工具的抽象契约。

Tool 定义单个工具的元信息描述和执行逻辑。
ToolProvider（SystemToolProvider / MCPAdapter）管理 Tool 集合，
ToolRouter（框架内部）合并多个 Provider 并统一路由分发。

用户通常不直接实现 Tool 接口，而是：
- 使用 BaseTool 基类或 @inline_tool 装饰器创建 Tool 实例
- 将 Tool 实例注册到 DefaultSystemToolProvider
- 或通过 DefaultMCPAdapter 消费外部 MCP Server

Tool 接口本身是稳定的核心契约，不随架构演进而变化。
"""

from typing import Any, Dict, Protocol, runtime_checkable

from .types import ToolDefinition, ToolResult


@runtime_checkable
class Tool(Protocol):
    """工具执行接口。

    每个 Tool 实例提供自身的元信息描述和具体的执行逻辑。
    ToolRouter 通过 ToolProvider 间接调度 Tool 实例。

    调用时机：
    - get_definition()：会话初始化阶段（ToolProvider 收集工具元信息）
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
