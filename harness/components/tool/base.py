"""BaseTool — Tool 接口的抽象基类。

提供 Tool 的默认实现骨架，用户继承此类并实现 get_definition() 和 execute() 即可。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from ...interfaces.types import ToolDefinition, ToolResult


class BaseTool(ABC):
    """Tool 接口的抽象基类。

    继承此类只需实现两个抽象方法：
    - get_definition() → ToolDefinition
    - execute(args) → ToolResult

    示例::

        class MyTool(BaseTool):
            def get_definition(self) -> ToolDefinition:
                return ToolDefinition(
                    name="my_tool",
                    description="Does something useful",
                    parameters={
                        "type": "object",
                        "properties": {
                            "input": {"type": "string", "description": "The input"}
                        },
                        "required": ["input"],
                    },
                )

            def execute(self, args: Dict[str, Any]) -> ToolResult:
                return ToolResult(success=True, content=f"Got: {args['input']}")
    """

    @abstractmethod
    def get_definition(self) -> ToolDefinition:
        """返回工具的元信息定义。

        Returns:
            ToolDefinition: 工具名称、描述、参数 schema。
        """
        ...

    @abstractmethod
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """执行工具并返回结果。

        Args:
            args: 工具调用参数（由 LLM 输出解析而来）。

        Returns:
            ToolResult: 执行结果（success/content/error）。
        """
        ...
