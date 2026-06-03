"""inline_tool — 声明式装饰器，将普通函数包装为 Tool。

使用 @inline_tool 装饰器将任何 Python 函数快速转换为 Tool 实例，
无需手写完整的 BaseTool 子类。

示例::

    @inline_tool(
        name="calculator",
        description="Evaluate a math expression",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression"}
            },
            "required": ["expression"],
        },
    )
    def calculator(args: Dict[str, Any]) -> ToolResult:
        result = eval(args["expression"])
        return ToolResult(success=True, content=str(result))
"""

from typing import Any, Callable, Dict

from ...interfaces.types import ToolDefinition, ToolResult


class InlineTool:
    """由 @inline_tool 装饰器生成的 Tool 实例。

    内部持有装饰器参数和原始函数引用，
    通过 get_definition() 返回元信息，execute() 委托给原函数。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        fn: Callable[[Dict[str, Any]], Any],
    ):
        """初始化 InlineTool。

        Args:
            name: 工具名称。
            description: 工具描述。
            parameters: JSON Schema 格式的参数定义。
            fn: 原始执行函数。
        """
        self._name = name
        self._description = description
        self._parameters = parameters
        self._fn = fn

    def get_definition(self) -> ToolDefinition:
        """返回工具的元信息定义。"""
        return ToolDefinition(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """执行工具，调用原始函数并包装返回值。

        函数可直接返回 ToolResult，或返回任意值（自动包装为 ToolResult）。
        """
        try:
            result = self._fn(args)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, content=result)
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))


def inline_tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
) -> Callable:
    """声明式装饰器：将函数包装为 InlineTool。

    Args:
        name: 工具名称（暴露给 LLM 的名称）。
        description: 工具描述（LLM 可见）。
        parameters: JSON Schema 格式的参数定义。

    Returns:
        装饰器，被装饰函数将变为 InlineTool 实例。

    Example::

        @inline_tool(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"],
            },
        )
        def get_weather(args):
            return f"Weather in {args['city']}: sunny, 22°C"
    """

    def decorator(fn: Callable[[Dict[str, Any]], Any]) -> InlineTool:
        return InlineTool(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
        )

    return decorator
