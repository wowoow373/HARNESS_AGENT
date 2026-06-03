"""Test DefaultSystemToolProvider — 系统工具提供者的单元测试。

覆盖：
- 默认内置工具（read_file、write_file、shell）
- execute() 各种场景
- 自定义额外工具（extra_tools）
- 完全自定义工具集（use_builtins=False）
- BaseTool 子类和 @inline_tool 的使用
- 错误处理
"""

import os
import tempfile

import pytest

from harness.components.tool import (
    BaseTool,
    DefaultSystemToolProvider,
    ReadFileTool,
    ShellTool,
    WriteFileTool,
    inline_tool,
)
from harness.interfaces.types import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# Test: 默认内置工具
# ---------------------------------------------------------------------------


class TestDefaultBuiltins:
    """默认内置工具测试。"""

    @pytest.fixture
    def provider(self):
        return DefaultSystemToolProvider()

    def test_has_three_default_tools(self, provider):
        """默认有 read_file、write_file、shell 三个工具。"""
        tools = provider.get_tools()
        names = {t.name for t in tools}
        assert names == {"read_file", "write_file", "shell"}
        assert provider.tool_count == 3

    def test_read_file_tool_definition(self, provider):
        """ReadFileTool 定义正确。"""
        assert provider.has_tool("read_file")
        result = provider.execute("read_file", {"path": "/nonexistent"})
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_read_file_tool_reads_content(self, provider):
        """ReadFileTool 成功读取文件。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            tmp_path = f.name

        try:
            result = provider.execute("read_file", {"path": tmp_path})
            assert result.success is True
            assert "hello world" in str(result.content)
        finally:
            os.unlink(tmp_path)

    def test_write_file_tool(self, provider):
        """WriteFileTool 成功写入文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            result = provider.execute("write_file", {
                "path": path,
                "content": "test content",
            })
            assert result.success is True
            with open(path) as f:
                assert f.read() == "test content"

    def test_write_file_nested_directory(self, provider):
        """WriteFileTool 自动创建上级目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sub", "deep", "file.txt")
            result = provider.execute("write_file", {
                "path": path,
                "content": "nested",
            })
            assert result.success is True
            assert os.path.exists(path)

    def test_shell_tool_success(self, provider):
        """ShellTool 成功执行。"""
        result = provider.execute("shell", {"command": "echo hello"})
        assert result.success is True
        assert "hello" in str(result.content)

    def test_shell_tool_failure(self, provider):
        """ShellTool 执行失败。"""
        result = provider.execute("shell", {"command": "exit 1"})
        assert result.success is False

    def test_unknown_tool_raises(self, provider):
        """未知工具抛出 KeyError。"""
        with pytest.raises(KeyError, match="not found"):
            provider.execute("nonexistent", {})


# ---------------------------------------------------------------------------
# Test: 自定义工具
# ---------------------------------------------------------------------------


class TestCustomTools:
    """自定义工具测试。"""

    def test_extra_tools_append(self):
        """extra_tools 追加到默认工具集后。"""
        class MyCustomTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(name="my_tool", description="Custom tool")

            def execute(self, args):
                return ToolResult(success=True, content="custom result")

        provider = DefaultSystemToolProvider(extra_tools=[MyCustomTool()])
        assert provider.tool_count == 4
        assert provider.has_tool("my_tool")
        # 默认工具仍存在
        assert provider.has_tool("read_file")

        result = provider.execute("my_tool", {})
        assert result.success is True
        assert result.content == "custom result"

    def test_replace_builtins(self):
        """use_builtins=False + tools 完全替换工具集。"""
        class ToolA(BaseTool):
            def get_definition(self):
                return ToolDefinition(name="tool_a", description="A")
            def execute(self, args):
                return ToolResult(success=True, content="a")

        class ToolB(BaseTool):
            def get_definition(self):
                return ToolDefinition(name="tool_b", description="B")
            def execute(self, args):
                return ToolResult(success=True, content="b")

        provider = DefaultSystemToolProvider(
            tools=[ToolA(), ToolB()],
            use_builtins=False,
        )
        assert provider.tool_count == 2
        assert provider.has_tool("tool_a")
        assert provider.has_tool("tool_b")
        assert not provider.has_tool("read_file")

    def test_inline_tool(self):
        """@inline_tool 装饰器创建的 Tool 正常工作。"""

        @inline_tool(
            name="greet",
            description="Greet someone",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name to greet"}
                },
                "required": ["name"],
            },
        )
        def greet(args):
            return f"Hello, {args['name']}!"

        provider = DefaultSystemToolProvider(
            tools=[greet],
            use_builtins=False,
        )
        assert provider.tool_count == 1
        definition = provider.get_tools()[0]
        assert definition.name == "greet"
        assert "Greet" in definition.description

        result = provider.execute("greet", {"name": "World"})
        assert result.success is True
        assert "Hello, World!" in str(result.content)

    def test_tool_returning_tool_result(self):
        """函数直接返回 ToolResult 时正确透传。"""

        @inline_tool(
            name="maybe_fail",
            description="May fail",
            parameters={"type": "object", "properties": {}},
        )
        def maybe_fail(args):
            return ToolResult(success=False, content=None, error="intentional failure")

        provider = DefaultSystemToolProvider(
            tools=[maybe_fail],
            use_builtins=False,
        )
        result = provider.execute("maybe_fail", {})
        assert result.success is False
        assert result.error == "intentional failure"


# ---------------------------------------------------------------------------
# Test: 边界情况
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界情况测试。"""

    def test_duplicate_tool_name_warns(self, caplog):
        """同名工具重复注册触发 warning。"""
        class ToolV1(BaseTool):
            def get_definition(self):
                return ToolDefinition(name="dup", description="v1")
            def execute(self, args):
                return ToolResult(success=True, content="v1")

        class ToolV2(BaseTool):
            def get_definition(self):
                return ToolDefinition(name="dup", description="v2")
            def execute(self, args):
                return ToolResult(success=True, content="v2")

        provider = DefaultSystemToolProvider(
            tools=[ToolV1(), ToolV2()],
            use_builtins=False,
        )
        # 后者覆盖前者
        assert provider.tool_count == 1
        result = provider.execute("dup", {})
        assert result.content == "v2"

    def test_empty_provider(self):
        """空 Provider（无任何工具）正常。"""
        provider = DefaultSystemToolProvider(use_builtins=False)
        assert provider.tool_count == 0
        assert provider.get_tools() == []

    def test_has_tool(self):
        """has_tool() 查询正确。"""
        provider = DefaultSystemToolProvider()
        assert provider.has_tool("read_file") is True
        assert provider.has_tool("nonexistent") is False
