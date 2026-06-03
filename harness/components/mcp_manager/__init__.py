"""Harness Agent Template — MCP Manager 组件。

提供 MCP（Model Context Protocol）的框架适配层：
- MCPClient：MCP 协议客户端（stdio/SSE）
- DefaultMCPAdapter：MCPAdapter 的默认实现
"""

from .default_mcp_adapter import DefaultMCPAdapter
from .mcp_client import MCPClient

__all__ = [
    "DefaultMCPAdapter",
    "MCPClient",
]
