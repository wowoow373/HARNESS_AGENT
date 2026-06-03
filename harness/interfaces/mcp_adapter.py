"""MCPAdapter 接口 — MCP 适配层。

消费外部 MCP Server，经转换后暴露工具。用户可替换、可裁切。
与 SystemToolProvider 平级，通过 ToolRouter 合并后暴露给编排器。

调用时机：
- get_tools()：会话初始化阶段（ToolRouter 收集各 Provider 的工具元信息）
- execute()：运行时（LLM 请求执行工具时，ToolRouter 查表分发）
- shutdown()：会话结束时（关闭 MCP Server 子进程连接）

裁切方式：不注册到 DI 容器即可裁切（等价于运行时可选组件）。
"""

from typing import Any, Dict, List, Protocol, runtime_checkable

from .types import ToolDefinition, ToolResult


@runtime_checkable
class MCPAdapter(Protocol):
    """MCP 适配层接口。

    职责：
    1. 消费外部 MCP Server（通过 MCPClient）
    2. 经声明式 (ToolTransform) + 程序化 (MCPHandler) 两级转换暴露工具
    3. 管理 MCP Server 子进程的生命周期

    与旧 MCPManager 的区别：
    - MCPManager 产出 Tool 实例注册到 ToolRegistry
    - MCPAdapter 自身即是 ToolProvider，同时持有 schema/args/result 三阶段转换
    - shutdown() 纳入 ToolRouter 统一生命周期管理

    调用时机：
    - get_tools()：会话初始化阶段
    - execute()：运行时
    - shutdown()：会话结束
    """

    def get_tools(self) -> List[ToolDefinition]:
        """返回经转换后的 MCP 工具元信息列表。

        内部流程：
        1. MCPClient.list_tools() 获取原始工具列表
        2. 经 ToolTransform 声明式转换（重命名、隐藏、注入默认参数）
        3. 经 MCPHandler 程序化转换（如有注册）
        4. 返回最终暴露给 LLM 的工具定义列表

        Returns:
            List[ToolDefinition]: 转换后的工具定义列表。
        """
        ...

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """执行 MCP 工具调用。

        内部流程：
        1. transform_args() 转换 LLM 传入的参数
        2. MCPClient.call_tool() 调用外部 MCP Server
        3. transform_result() 转换 MCP Server 响应

        Args:
            name: 工具名称（对外暴露名，非 MCP 原始名）。
            args: 工具调用参数。

        Returns:
            ToolResult: 转换后的执行结果。
        """
        ...

    def shutdown(self) -> None:
        """关闭所有 MCP Server 子进程连接。

        在会话结束时由 ToolRouter.shutdown() 统一调用。
        实现应确保所有子进程被正确终止，避免僵尸进程。
        """
        ...
