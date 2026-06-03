"""Test DefaultMCPAdapter — MCP 适配层的单元测试。

覆盖：
- 构造与配置
- ToolTransform 声明式转换（expose_as、hidden、description_override、arg_defaults）
- MCPHandler 程序化转换
- get_tools() / execute() / shutdown()
- 边界情况（空 servers、错误处理）
"""

import pytest

from harness.components.mcp_manager import DefaultMCPAdapter
from harness.components.mcp_manager.mcp_client import MCPServerConfig
from harness.interfaces.types import ToolTransform


# ---------------------------------------------------------------------------
# Test: 构造
# ---------------------------------------------------------------------------


class TestDefaultMCPAdapterInit:
    """DefaultMCPAdapter 初始化测试。"""

    def test_init_empty(self):
        """无 servers 时正常初始化。"""
        adapter = DefaultMCPAdapter()
        assert adapter.server_count == 0
        assert adapter.tool_count == 0

    def test_init_with_servers(self):
        """带 servers 配置正常初始化。"""
        adapter = DefaultMCPAdapter(servers=[
            MCPServerConfig(name="test", command="echo", args=["hello"]),
        ])
        assert adapter.server_count == 0  # 尚未启动
        tools = adapter.get_tools()  # 启动会失败（echo 不是 MCP server）
        # echo 不是合法的 MCP server，所以工具列表为空
        assert isinstance(tools, list)

    def test_init_with_transforms(self):
        """带 transforms 正常初始化。"""
        adapter = DefaultMCPAdapter(transforms={
            "secret_tool": ToolTransform(hidden=True),
            "old_name": ToolTransform(expose_as="new_name"),
        })
        assert adapter.tool_count == 0


# ---------------------------------------------------------------------------
# Test: 工具转换
# ---------------------------------------------------------------------------


class TestToolTransform:
    """ToolTransform 声明式转换测试。"""

    def test_hidden(self):
        """hidden=True 的工具不暴露。"""
        t = ToolTransform(hidden=True)
        assert t.hidden is True
        assert t.expose_as is None

    def test_expose_as(self):
        """expose_as 重命名工具。"""
        t = ToolTransform(expose_as="new_tool_name")
        assert t.expose_as == "new_tool_name"
        assert t.hidden is False

    def test_description_override(self):
        """description_override 覆盖描述。"""
        t = ToolTransform(description_override="Better description")
        assert t.description_override == "Better description"

    def test_arg_defaults(self):
        """arg_defaults 注入默认参数。"""
        t = ToolTransform(arg_defaults={"cwd": "/home", "verbose": False})
        assert t.arg_defaults == {"cwd": "/home", "verbose": False}

    def test_combined(self):
        """组合使用多个转换字段。"""
        t = ToolTransform(
            expose_as="safe_delete",
            hidden=False,
            description_override="Safely delete a file",
            arg_defaults={"confirm": True},
        )
        assert t.expose_as == "safe_delete"
        assert t.hidden is False
        assert t.description_override == "Safely delete a file"
        assert t.arg_defaults == {"confirm": True}

    def test_default_values(self):
        """默认值正确。"""
        t = ToolTransform()
        assert t.expose_as is None
        assert t.description_override is None
        assert t.hidden is False
        assert t.arg_defaults == {}
        assert t.arg_transform is None
        assert t.result_transform is None


# ---------------------------------------------------------------------------
# Test: MCPHandler
# ---------------------------------------------------------------------------


class TestMCPHandler:
    """MCPHandler 程序化转换测试。"""

    def test_handler_receives_transforms(self):
        """Handler 的方法签名匹配 MCPHandler Protocol。"""

        class MyHandler:
            def __init__(self):
                self.schema_calls = []
                self.args_calls = []
                self.result_calls = []

            def transform_schema(self, name, schema):
                self.schema_calls.append((name, schema))
                return schema

            def transform_args(self, name, args):
                self.args_calls.append((name, args))
                args["_injected"] = "handler_val"
                return args

            def transform_result(self, name, result):
                self.result_calls.append((name, result))
                return f"[SAFE] {result}"

        handler = MyHandler()

        # 验证 handler 有正确的方法签名
        result = handler.transform_args("test_tool", {"x": 1})
        assert result == {"x": 1, "_injected": "handler_val"}
        assert len(handler.args_calls) == 1

        result = handler.transform_result("test_tool", "secret data")
        assert result == "[SAFE] secret data"

    def test_adapter_accepts_handler(self):
        """DefaultMCPAdapter 接受 handler 参数。"""
        handler = object()  # duck-typing 检查在调用时进行
        adapter = DefaultMCPAdapter(
            servers=[],
            transforms={},
            handler=handler,
        )
        assert adapter._handler is handler


# ---------------------------------------------------------------------------
# Test: 边界情况
# ---------------------------------------------------------------------------


class TestMCPAdapterEdgeCases:
    """边界情况测试。"""

    def test_get_tools_no_servers(self):
        """无 servers 时 get_tools() 返回空列表。"""
        adapter = DefaultMCPAdapter()
        tools = adapter.get_tools()
        assert tools == []

    def test_shutdown_before_start(self):
        """未启动时 shutdown 不崩溃。"""
        adapter = DefaultMCPAdapter()
        adapter.shutdown()
        # 不应抛异常

    def test_execute_unknown_tool(self):
        """执行未知工具返回错误。"""
        adapter = DefaultMCPAdapter()
        result = adapter.execute("unknown", {})
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_server_count_property(self):
        """server_count 正确反映已连接的 server 数量。"""
        adapter = DefaultMCPAdapter()
        assert adapter.server_count == 0

    def test_tool_count_property(self):
        """tool_count 正确反映暴露的工具数量。"""
        adapter = DefaultMCPAdapter()
        assert adapter.tool_count == 0
        assert adapter.has_tool("anything") is False

    def test_has_tool(self):
        """has_tool() 正确查询。"""
        adapter = DefaultMCPAdapter()
        assert adapter.has_tool("nonexistent") is False


# ---------------------------------------------------------------------------
# Test: MCPServerConfig
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    """MCPServerConfig dataclass 测试。"""

    def test_default_values(self):
        """默认值正确。"""
        config = MCPServerConfig()
        assert config.name == ""
        assert config.command == ""
        assert config.args == []
        assert config.env == {}
        assert config.timeout == 30.0

    def test_full_config(self):
        """完整配置正常。"""
        config = MCPServerConfig(
            name="my-server",
            command="python",
            args=["-m", "my_mcp_server"],
            env={"DEBUG": "1"},
            timeout=60.0,
        )
        assert config.name == "my-server"
        assert config.command == "python"
        assert config.args == ["-m", "my_mcp_server"]
        assert config.env == {"DEBUG": "1"}
        assert config.timeout == 60.0
