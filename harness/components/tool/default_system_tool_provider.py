"""DefaultSystemToolProvider — SystemToolProvider 的默认实现。

内置 ReadFileTool、WriteFileTool、ShellTool 三个系统基础工具。
用户可以替换此实现（注册自定义 SystemToolProvider 到 DI）。
"""

import logging
from typing import Any, Dict, List, Optional

from ...interfaces.types import ToolDefinition, ToolResult
from .base import BaseTool
from .system_tools import ReadFileTool, ShellTool, WriteFileTool

logger = logging.getLogger(__name__)


class DefaultSystemToolProvider:
    """SystemToolProvider 的默认实现。

    默认内置三个工具：read_file、write_file、shell。
    通过继承 BaseTool 或使用 @inline_tool 创建自定义 Tool 实例后，
    可通过 constructor 注入额外工具。

    用法::

        # 使用默认工具集
        provider = DefaultSystemToolProvider()

        # 带额外自定义工具
        provider = DefaultSystemToolProvider(extra_tools=[MyCustomTool()])

        # 完全自定义工具集（替换默认）
        provider = DefaultSystemToolProvider(
            tools=[MyTool1(), MyTool2()],
            use_builtins=False,
        )
    """

    def __init__(
        self,
        tools: Optional[List[BaseTool]] = None,
        extra_tools: Optional[List[BaseTool]] = None,
        use_builtins: bool = True,
    ):
        """初始化 Provider。

        Args:
            tools: 完整的工具列表（当 use_builtins=False 时替代默认工具集）。
            extra_tools: 额外的自定义工具（追加到默认工具集后）。
            use_builtins: 是否使用内置默认工具集。
        """
        self._tool_index: Dict[str, BaseTool] = {}

        if use_builtins:
            self._register(ReadFileTool())
            self._register(WriteFileTool())
            self._register(ShellTool())

        if tools:
            for tool in tools:
                self._register(tool)

        if extra_tools:
            for tool in extra_tools:
                self._register(tool)

        logger.debug(
            f"Initialized DefaultSystemToolProvider with {len(self._tool_index)} tool(s)"
        )

    # ------------------------------------------------------------------
    # SystemToolProvider 协议方法
    # ------------------------------------------------------------------

    def get_tools(self) -> List[ToolDefinition]:
        """返回所有系统工具的元信息列表。

        Returns:
            List[ToolDefinition]: 工具定义列表。
        """
        return [tool.get_definition() for tool in self._tool_index.values()]

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """按名称执行指定的系统工具。

        Args:
            name: 工具名称。
            args: 工具调用参数。

        Returns:
            ToolResult: 工具执行结果。

        Raises:
            KeyError: 工具名称未找到。
        """
        if name not in self._tool_index:
            available = sorted(self._tool_index.keys())
            raise KeyError(
                f"Tool '{name}' not found in system tools. "
                f"Available: {available}"
            )

        tool = self._tool_index[name]
        return tool.execute(args)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _register(self, tool: BaseTool) -> None:
        """注册一个 Tool 实例到索引。

        Args:
            tool: BaseTool 实例。
        """
        definition = tool.get_definition()
        name = definition.name
        if name in self._tool_index:
            logger.warning(
                f"System tool '{name}' already registered, overwriting"
            )
        self._tool_index[name] = tool

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def tool_count(self) -> int:
        """返回已注册的工具数量。"""
        return len(self._tool_index)

    def has_tool(self, name: str) -> bool:
        """检查指定名称的工具是否存在。

        Args:
            name: 工具名称。

        Returns:
            True 如果存在。
        """
        return name in self._tool_index
