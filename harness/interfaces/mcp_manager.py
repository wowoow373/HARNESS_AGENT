"""MCPManager 接口 — MCP 配置入口。

将用户的 MCP 配置（外部 Server、内联工具等）转换为框架可识别的 Tool 实例。
"""

from typing import List, Protocol, runtime_checkable

from .tool import Tool


@runtime_checkable
class MCPManager(Protocol):
    """MCP 管理器接口。

    职责：将用户的 MCP 配置转换为框架可识别的 Tool 实例。

    调用时机：框架会话初始化阶段，仅调用一次。
    产出 Tool 列表后注册到 ToolRegistry。

    实现示例：
    - ServerMCPManager — 连接外部 MCP Server（stdio/SSE），转换其暴露的工具
    - InlineMCPManager — 将用户在代码中注册的内联函数包装为框架 Tool
    """

    def load_tools(self) -> List[Tool]:
        """加载并返回所有由 MCP 配置产生的工具实例。

        Returns:
            List[Tool]: 框架可识别的 Tool 实例列表。
        """
        ...
