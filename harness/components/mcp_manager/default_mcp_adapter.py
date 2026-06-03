"""DefaultMCPAdapter — MCPAdapter 的默认实现。

消费外部 MCP Server，经 ToolTransform 声明式 + MCPHandler 程序化两级转换后暴露工具。

转换流水线：
1. Schema: MCP 原始 schema → ToolTransform → MCPHandler.transform_schema()
2. Args:   LLM args → arg_defaults → arg_transform → MCPHandler.transform_args()
3. Result: MCP result → result_transform → MCPHandler.transform_result()
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from ...interfaces.types import ToolDefinition, ToolResult, ToolTransform
from .mcp_client import MCPClient, MCPServerConfig

logger = logging.getLogger(__name__)

# 尝试导入 MCPHandler（可选）
try:
    from ...interfaces.mcp_handler import MCPHandler as MCPHandlerProtocol
except ImportError:
    MCPHandlerProtocol = None  # type: ignore[assignment]


class DefaultMCPAdapter:
    """MCPAdapter 的默认实现。

    管理一组 MCP Server 连接，提供 schema/args/result 三阶段转换。

    用法::

        adapter = DefaultMCPAdapter(
            servers=[
                MCPServerConfig(
                    name="fs",
                    command="npx",
                    args=["-y", "@anthropic/mcp-filesystem", "/tmp"],
                ),
            ],
            transforms={
                "filesystem_delete": ToolTransform(hidden=True),
                "filesystem_read": ToolTransform(expose_as="read_remote_file"),
            },
        )
        # 注册到 DI
        container.register(MCPAdapter, adapter)
    """

    def __init__(
        self,
        servers: Optional[List[MCPServerConfig]] = None,
        transforms: Optional[Dict[str, ToolTransform]] = None,
        handler: Optional[Any] = None,  # MCPHandler Protocol
    ):
        """初始化 MCP 适配层。

        Args:
            servers: MCP Server 配置列表。
            transforms: 工具名称 → ToolTransform 的声明式转换映射。
                        key 为 MCP 原始名称（转换前）。
            handler: 可选的 MCPHandler 实现（程序化转换）。
        """
        self._servers = servers or []
        self._transforms: Dict[str, ToolTransform] = transforms or {}
        self._handler = handler

        # 内部状态
        self._clients: Dict[str, MCPClient] = {}
        # 名称映射：对外暴露名 → (MCP 原始名, MCPClient)
        self._name_map: Dict[str, tuple] = {}
        # 反向映射：MCP 原始名 → 对外暴露名
        self._reverse_name_map: Dict[str, str] = {}
        self._started = False

    # ------------------------------------------------------------------
    # MCPAdapter 协议方法
    # ------------------------------------------------------------------

    def get_tools(self) -> List[ToolDefinition]:
        """加载并转换所有 MCP Server 的工具定义。

        流程：
        1. 启动所有 MCPClient
        2. 获取每个 Server 的原始工具列表
        3. 应用 ToolTransform 声明式转换
        4. 应用 MCPHandler.transform_schema() 程序化转换
        5. 返回最终工具定义列表

        Returns:
            List[ToolDefinition]: 转换后的工具定义列表。
        """
        self._ensure_started()

        result: List[ToolDefinition] = []

        for server_name, client in self._clients.items():
            try:
                raw_tools = client.list_tools()
            except Exception as e:
                logger.warning(
                    f"Failed to list tools from MCP Server '{server_name}': {e}"
                )
                continue

            for raw in raw_tools:
                raw_name = raw.get("name", "")
                transform = self._transforms.get(raw_name, ToolTransform())

                # 隐藏的工具跳过
                if transform.hidden:
                    logger.debug(f"MCP tool '{raw_name}' is hidden, skipping")
                    continue

                # 确定对外暴露的名称
                expose_name = transform.expose_as or raw_name

                # 构建 schema
                schema = raw.get("inputSchema", {})
                description = transform.description_override or raw.get("description", "")

                # MCPHandler 程序化转换 schema
                if self._handler and hasattr(self._handler, "transform_schema"):
                    try:
                        schema = self._handler.transform_schema(expose_name, schema)
                    except Exception as e:
                        logger.warning(
                            f"MCPHandler.transform_schema() failed for '{expose_name}': {e}"
                        )

                # 注册名称映射
                self._name_map[expose_name] = (raw_name, client)
                self._reverse_name_map[raw_name] = expose_name

                result.append(ToolDefinition(
                    name=expose_name,
                    description=description,
                    parameters=schema,
                ))

                logger.debug(
                    f"MCP tool '{raw_name}' exposed as '{expose_name}' "
                    f"from server '{server_name}'"
                )

        logger.info(f"Loaded {len(result)} MCP tool(s) from {len(self._clients)} server(s)")
        return result

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """执行 MCP 工具调用。

        流程：
        1. 查名称映射获取 MCP 原始名 + MCPClient
        2. 应用 arg_defaults + arg_transform + MCPHandler.transform_args()
        3. 调用 MCPClient.call_tool()
        4. 应用 result_transform + MCPHandler.transform_result()
        5. 返回 ToolResult

        Args:
            name: 工具对外暴露名称。
            args: LLM 传入的参数。

        Returns:
            ToolResult: 转换后的执行结果。
        """
        if name not in self._name_map:
            available = sorted(self._name_map.keys())
            return ToolResult(
                success=False,
                content=None,
                error=f"MCP tool '{name}' not found. Available: {available}",
            )

        raw_name, client = self._name_map[name]

        try:
            # 获取转换配置
            transform = self._transforms.get(raw_name, ToolTransform())

            # --- 参数转换阶段 ---

            # 1. 注入默认参数（LLM 传入值优先）
            for k, v in transform.arg_defaults.items():
                if k not in args:
                    args[k] = v

            # 2. 高级程序化参数转换
            if transform.arg_transform and callable(transform.arg_transform):
                try:
                    args = transform.arg_transform(args)
                except Exception as e:
                    logger.warning(f"arg_transform failed for '{name}': {e}")

            # 3. MCPHandler.transform_args()
            if self._handler and hasattr(self._handler, "transform_args"):
                try:
                    args = self._handler.transform_args(name, args)
                except Exception as e:
                    logger.warning(f"MCPHandler.transform_args() failed for '{name}': {e}")

            # --- 执行阶段 ---
            raw_result = client.call_tool(raw_name, args)

            # --- 结果转换阶段 ---

            # 1. 高级程序化结果转换
            if transform.result_transform and callable(transform.result_transform):
                try:
                    raw_result = transform.result_transform(raw_result)
                except Exception as e:
                    logger.warning(f"result_transform failed for '{name}': {e}")

            # 2. MCPHandler.transform_result()
            if self._handler and hasattr(self._handler, "transform_result"):
                try:
                    raw_result = self._handler.transform_result(name, raw_result)
                except Exception as e:
                    logger.warning(f"MCPHandler.transform_result() failed for '{name}': {e}")

            return ToolResult(success=True, content=raw_result)

        except Exception as e:
            logger.error(f"MCP tool '{name}' execution failed: {e}")
            return ToolResult(success=False, content=None, error=str(e))

    def shutdown(self) -> None:
        """关闭所有 MCP Server 子进程连接。"""
        for server_name, client in self._clients.items():
            try:
                client.stop()
                logger.debug(f"MCP Server '{server_name}' stopped")
            except Exception as e:
                logger.warning(f"Error stopping MCP Server '{server_name}': {e}")

        self._clients.clear()
        self._name_map.clear()
        self._reverse_name_map.clear()
        self._started = False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """确保所有 MCP Server 已启动。"""
        if self._started:
            return

        for config in self._servers:
            try:
                client = MCPClient(config)
                client.start()
                self._clients[config.name] = client
            except Exception as e:
                logger.warning(
                    f"Failed to start MCP Server '{config.name}': {e}"
                )

        self._started = True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def server_count(self) -> int:
        """返回已连接的 MCP Server 数量。"""
        return len(self._clients)

    @property
    def tool_count(self) -> int:
        """返回暴露的工具数量。"""
        return len(self._name_map)

    def has_tool(self, name: str) -> bool:
        """检查指定名称的 MCP 工具是否存在。

        Args:
            name: 工具对外暴露名称。

        Returns:
            True 如果存在。
        """
        return name in self._name_map
