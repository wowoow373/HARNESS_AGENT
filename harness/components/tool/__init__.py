"""Harness Agent Template — Tool 组件。

提供 Tool 的创建辅助工具和默认实现：
- BaseTool：抽象基类
- InlineTool / inline_tool：声明式装饰器
- ReadFileTool / WriteFileTool / ShellTool：系统内置工具
- DefaultSystemToolProvider：系统工具提供者的默认实现
"""

from .base import BaseTool
from .default_system_tool_provider import DefaultSystemToolProvider
from .inline_tool import InlineTool, inline_tool
from .system_tools import ReadFileTool, ShellTool, WriteFileTool

__all__ = [
    "BaseTool",
    "DefaultSystemToolProvider",
    "InlineTool",
    "ReadFileTool",
    "ShellTool",
    "WriteFileTool",
    "inline_tool",
]
