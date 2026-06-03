"""Test ToolRouter — 框架内部工具路由器的单元测试。

覆盖：
- 初始化与注册 Provider
- list_tools() 合并多来源
- execute() 查表分发
- 名称冲突处理
- shutdown() 统一清理
- has_tool() / tool_count / provider_count 查询
"""

import pytest

from harness.core.tool_router import ToolRouter
from harness.interfaces.types import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Mock Providers
# ---------------------------------------------------------------------------


class _MockSystemProvider:
    """模拟 SystemToolProvider。"""

    def __init__(self, tools=None):
        self._tools = tools or []
        self._executed = []
        self._shutdown_called = False

    def get_tools(self):
        return list(self._tools)

    def execute(self, name, args):
        self._executed.append((name, args))
        return ToolResult(success=True, content=f"system:{name}:{args}")

    def shutdown(self):
        self._shutdown_called = True


class _MockMCPProvider:
    """模拟 MCPAdapter。"""

    def __init__(self, tools=None):
        self._tools = tools or []
        self._executed = []
        self._shutdown_called = False

    def get_tools(self):
        return list(self._tools)

    def execute(self, name, args):
        self._executed.append((name, args))
        return ToolResult(success=True, content=f"mcp:{name}:{args}")

    def shutdown(self):
        self._shutdown_called = True


# ---------------------------------------------------------------------------
# Test: 初始化与注册
# ---------------------------------------------------------------------------


class TestToolRouterInit:
    """ToolRouter 初始化测试。"""

    def test_init_empty(self):
        """初始化后路由表为空。"""
        router = ToolRouter()
        assert router.tool_count == 0
        assert router.provider_count == 0

    def test_register_single_provider(self):
        """注册单个 Provider。"""
        router = ToolRouter()
        provider = _MockSystemProvider()
        router.register_provider(provider)
        assert router.provider_count == 1
        assert router.tool_count == 0  # provider 没有工具

    def test_register_provider_with_tools(self):
        """注册含工具的 Provider。"""
        router = ToolRouter()
        provider = _MockSystemProvider(tools=[
            ToolDefinition(name="read", description="Read a file"),
            ToolDefinition(name="write", description="Write a file"),
        ])
        router.register_provider(provider)
        assert router.provider_count == 1
        assert router.tool_count == 2


class TestToolRouterListTools:
    """list_tools() 测试。"""

    def test_empty_router(self):
        """空路由表返回空列表。"""
        router = ToolRouter()
        assert router.list_tools() == []

    def test_single_provider(self):
        """单个 Provider 返回其完整工具列表。"""
        router = ToolRouter()
        tools = [
            ToolDefinition(name="t1", description="tool 1"),
            ToolDefinition(name="t2", description="tool 2"),
        ]
        router.register_provider(_MockSystemProvider(tools=tools))
        result = router.list_tools()
        assert len(result) == 2
        names = {t.name for t in result}
        assert names == {"t1", "t2"}

    def test_merge_multiple_providers(self):
        """多个 Provider 的工具合并。"""
        router = ToolRouter()
        router.register_provider(_MockSystemProvider(tools=[
            ToolDefinition(name="sys_a", description="System tool A"),
        ]))
        router.register_provider(_MockMCPProvider(tools=[
            ToolDefinition(name="mcp_b", description="MCP tool B"),
        ]))
        result = router.list_tools()
        assert len(result) == 2
        names = {t.name for t in result}
        assert names == {"sys_a", "mcp_b"}

    def test_duplicate_name_dedup(self):
        """同名工具去重（后者覆盖但不重复出现在列表中）。"""
        router = ToolRouter()
        router.register_provider(_MockSystemProvider(tools=[
            ToolDefinition(name="shared", description="from system"),
        ]))
        router.register_provider(_MockMCPProvider(tools=[
            ToolDefinition(name="shared", description="from mcp"),
        ]))
        result = router.list_tools()
        assert len(result) == 1
        assert result[0].name == "shared"
        # 第二个注册的 provider 在路由表中生效
        assert router.has_tool("shared")


# ---------------------------------------------------------------------------
# Test: execute() 分发
# ---------------------------------------------------------------------------


class TestToolRouterExecute:
    """execute() 测试。"""

    def test_execute_routes_to_correct_provider(self):
        """工具调用路由到正确的 Provider。"""
        router = ToolRouter()
        sys = _MockSystemProvider(tools=[
            ToolDefinition(name="sys_tool", description="System tool"),
        ])
        mcp = _MockMCPProvider(tools=[
            ToolDefinition(name="mcp_tool", description="MCP tool"),
        ])
        router.register_provider(sys)
        router.register_provider(mcp)

        result_sys = router.execute("sys_tool", {"key": "val"})
        assert result_sys.success is True
        assert len(sys._executed) == 1
        assert sys._executed[0] == ("sys_tool", {"key": "val"})
        assert len(mcp._executed) == 0

        result_mcp = router.execute("mcp_tool", {"x": 1})
        assert result_mcp.success is True
        assert len(mcp._executed) == 1
        assert mcp._executed[0] == ("mcp_tool", {"x": 1})

    def test_execute_unknown_tool_raises(self):
        """未知工具抛出 KeyError。"""
        router = ToolRouter()
        with pytest.raises(KeyError, match="not found"):
            router.execute("nonexistent", {})


# ---------------------------------------------------------------------------
# Test: shutdown()
# ---------------------------------------------------------------------------


class TestToolRouterShutdown:
    """shutdown() 测试。"""

    def test_shutdown_calls_provider_shutdown(self):
        """shutdown() 分发到有 shutdown() 方法的 Provider。"""
        router = ToolRouter()
        sys = _MockSystemProvider(tools=[
            ToolDefinition(name="t1", description=""),
        ])
        mcp = _MockMCPProvider(tools=[
            ToolDefinition(name="t2", description=""),
        ])
        router.register_provider(sys)
        router.register_provider(mcp)

        router.shutdown()

        assert sys._shutdown_called is True
        assert mcp._shutdown_called is True
        # shutdown 后状态清理
        assert router.tool_count == 0
        assert router.provider_count == 0

    def test_shutdown_clears_routes(self):
        """shutdown() 清理路由表。"""
        router = ToolRouter()
        router.register_provider(_MockSystemProvider(tools=[
            ToolDefinition(name="t1", description=""),
        ]))
        assert router.tool_count == 1
        router.shutdown()
        assert router.tool_count == 0


# ---------------------------------------------------------------------------
# Test: 查询方法
# ---------------------------------------------------------------------------


class TestToolRouterQuery:
    """查询方法测试。"""

    def test_has_tool(self):
        """has_tool() 正确返回工具存在与否。"""
        router = ToolRouter()
        router.register_provider(_MockSystemProvider(tools=[
            ToolDefinition(name="existing", description=""),
        ]))
        assert router.has_tool("existing") is True
        assert router.has_tool("missing") is False

    def test_tool_count(self):
        """tool_count 正确反映工具数量。"""
        router = ToolRouter()
        assert router.tool_count == 0
        router.register_provider(_MockSystemProvider(tools=[
            ToolDefinition(name="a", description=""),
            ToolDefinition(name="b", description=""),
        ]))
        assert router.tool_count == 2
        router.register_provider(_MockMCPProvider(tools=[
            ToolDefinition(name="c", description=""),
        ]))
        assert router.tool_count == 3

    def test_provider_count(self):
        """provider_count 正确反映 Provider 数量。"""
        router = ToolRouter()
        assert router.provider_count == 0
        router.register_provider(_MockSystemProvider())
        assert router.provider_count == 1
        router.register_provider(_MockMCPProvider())
        assert router.provider_count == 2
