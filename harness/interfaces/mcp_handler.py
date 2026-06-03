"""MCPHandler 接口 — MCP 程序化转换处理器。

当 ToolTransform 声明式转换不够用时，实现此 Protocol 进行程序化转换。
通过注入到 DefaultMCPAdapter 的 handler 参数使用。

三阶段转换：
1. transform_schema() — 在工具发现阶段修改 schema（名称、描述、参数定义）
2. transform_args() — 在工具执行前修改 LLM 传入的参数
3. transform_result() — 在工具执行后修改 MCP Server 返回的结果

调用时机：由 DefaultMCPAdapter 在对应生命周期阶段调用。
"""

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class MCPHandler(Protocol):
    """MCP 程序化转换处理器接口。

    用于高级场景：统一认证注入、动态结果重组、跨工具参数转发等。
    当声明式 ToolTransform 无法覆盖需求时使用。

    示例：
        class MyAuthInjector:
            def transform_schema(self, name, schema):
                # 不修改 schema
                return schema

            def transform_args(self, name, args):
                # 为所有工具注入认证 token
                args["_auth_token"] = os.environ["API_TOKEN"]
                return args

            def transform_result(self, name, result):
                # 脱敏处理
                if "secret" in str(result).lower():
                    return "[REDACTED]"
                return result
    """

    def transform_schema(self, name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """转换工具 schema（在工具发现阶段调用）。

        Args:
            name: 工具对外暴露名称。
            schema: 当前 schema（已应用 ToolTransform 声明式转换）。

        Returns:
            修改后的 schema。
        """
        ...

    def transform_args(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """转换工具调用参数（在工具执行前调用）。

        Args:
            name: 工具对外暴露名称。
            args: LLM 传入的原始参数（已应用 ToolTransform.arg_defaults）。

        Returns:
            修改后的参数。
        """
        ...

    def transform_result(self, name: str, result: Any) -> Any:
        """转换工具执行结果（在工具执行后调用）。

        Args:
            name: 工具对外暴露名称。
            result: MCP Server 返回的原始结果。

        Returns:
            修改后的结果。
        """
        ...
