"""ToolRouter — 框架内部工具路由器。

合并多个 ToolProvider 的工具并统一分发。框架内部组件，非 DI 注册，
用户不可替换。编排器直接创建和管理 ToolRouter 实例。

ToolProvider 指满足以下任一 Protocol 的对象：
- SystemToolProvider（本地工具）
- MCPAdapter（MCP 工具）

两者都有 get_tools() 和 execute() 方法，MCPAdapter 额外有 shutdown()。
"""

import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from ..interfaces.types import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部类型：ToolProvider 的 duck-typing 检查
# ---------------------------------------------------------------------------


@runtime_checkable
class _ToolProviderLike(Protocol):
    """ToolProvider 的最小接口约束（duck-typing）。

    同时兼容 SystemToolProvider 和 MCPAdapter。
    """

    def get_tools(self) -> List[ToolDefinition]: ...
    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult: ...


# ---------------------------------------------------------------------------
# ToolRouter
# ---------------------------------------------------------------------------


class ToolRouter:
    """合并多个 ToolProvider 的工具并统一分发。

    框架内部组件，非 DI 注册。编排器直接创建实例，
    通过 register_provider() 注入已 resolve 的 Provider。

    职责：
    - 收集各 Provider 的工具定义，按名称构建路由表
    - 执行时根据路由表分发到对应 Provider
    - 统一调用各 Provider 的 shutdown()

    用法::

        router = ToolRouter()
        router.register_provider(system_tool_provider)
        router.register_provider(mcp_adapter)
        tools = router.list_tools()
        result = router.execute("read_file", {"path": "/tmp/x"})
        router.shutdown()
    """

    def __init__(self):
        """初始化空路由器。"""
        self._routes: Dict[str, _ToolProviderLike] = {}
        self._providers: List[_ToolProviderLike] = []

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register_provider(self, provider: _ToolProviderLike) -> None:
        """注册一个 ToolProvider，将其工具加入路由表。

        遍历 provider.get_tools() 返回的所有工具定义，
        将每个 (name → provider) 映射写入路由表。
        同名工具后者覆盖前者，并记录 WARNING 日志。

        Args:
            provider: 满足 _ToolProviderLike 的对象
                      (SystemToolProvider 或 MCPAdapter)。

        Raises:
            TypeError: provider 缺少 get_tools 或 execute 方法。
        """
        if not isinstance(provider, _ToolProviderLike):
            raise TypeError(
                f"Provider must implement get_tools() and execute(), "
                f"got {type(provider).__name__}"
            )

        self._providers.append(provider)

        try:
            tools = provider.get_tools()
        except Exception as e:
            logger.warning(
                f"Provider '{type(provider).__name__}'.get_tools() failed: {e}"
            )
            return

        for td in tools:
            if td.name in self._routes:
                logger.warning(
                    f"Tool name conflict: '{td.name}' from "
                    f"'{type(provider).__name__}' overrides existing "
                    f"from '{type(self._routes[td.name]).__name__}'"
                )
            self._routes[td.name] = provider

        logger.debug(
            f"Registered provider '{type(provider).__name__}' "
            f"with {len(tools)} tool(s)"
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_tools(self) -> List[ToolDefinition]:
        """列出所有已注册 Provider 的工具定义（合并列表）。

        遍历所有 Provider，收集其 get_tools() 返回值，
        合并为单个列表返回。用于传给 ContextAssembler。

        Returns:
            List[ToolDefinition]: 合并后的工具定义列表。
        """
        result: List[ToolDefinition] = []
        seen: set = set()

        for provider in self._providers:
            try:
                tools = provider.get_tools()
            except Exception as e:
                logger.warning(
                    f"Provider '{type(provider).__name__}'.get_tools() failed: {e}"
                )
                continue

            for td in tools:
                if td.name not in seen:
                    seen.add(td.name)
                    result.append(td)

        return result

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """按名称执行工具。

        查找路由表确定负责该工具的 Provider，
        调用其 execute() 方法返回结果。

        Args:
            name: 工具名称。
            args: 工具调用参数。

        Returns:
            ToolResult: 工具执行结果。

        Raises:
            KeyError: 工具名称未在路由表中找到。
        """
        if name not in self._routes:
            available = sorted(self._routes.keys())
            raise KeyError(
                f"Tool '{name}' not found in router. "
                f"Available tools: {available}"
            )

        provider = self._routes[name]
        return provider.execute(name, args)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """关闭所有 Provider 的资源。

        对每个 Provider 检查是否有 shutdown() 方法，有则调用。
        MCPAdapter 有 shutdown()（关闭子进程），
        SystemToolProvider 通常没有。
        单个 Provider 的 shutdown 异常不影响其他 Provider。
        """
        for provider in self._providers:
            if hasattr(provider, "shutdown"):
                try:
                    provider.shutdown()
                except Exception as e:
                    logger.warning(
                        f"Provider '{type(provider).__name__}'.shutdown() failed: {e}"
                    )

        self._routes.clear()
        self._providers.clear()

    # ------------------------------------------------------------------
    # 查询（辅助）
    # ------------------------------------------------------------------

    @property
    def tool_count(self) -> int:
        """返回路由表中的工具总数。"""
        return len(self._routes)

    @property
    def provider_count(self) -> int:
        """返回已注册的 Provider 数量。"""
        return len(self._providers)

    def has_tool(self, name: str) -> bool:
        """检查路由表中是否存在指定工具。

        Args:
            name: 工具名称。

        Returns:
            True 如果存在，False 否则。
        """
        return name in self._routes
