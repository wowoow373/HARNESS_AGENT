"""Test harness for messaging/builder.py — conversion layer tests."""

import pytest

from harness.interfaces.types import (
    Message,
    Response,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
)
from harness.messaging.builder import (
    build_assistant_message,
    build_tool_result_message,
    dict_to_message,
    message_to_dict,
    messages_to_dicts,
    tool_definition_to_openai,
    tool_definitions_to_openai,
)


# ---------------------------------------------------------------------------
# message_to_dict tests
# ---------------------------------------------------------------------------


class TestMessageToDict:
    """message_to_dict 测试。"""

    def test_user_role(self):
        """message_to_dict — user role 输出正确。"""
        m = Message(role="user", content="hello")
        result = message_to_dict(m)
        assert result == {"role": "user", "content": "hello"}

    def test_system_role(self):
        """message_to_dict — system role 输出正确。"""
        m = Message(role="system", content="You are helpful")
        result = message_to_dict(m)
        assert result == {"role": "system", "content": "You are helpful"}

    def test_assistant_role(self):
        """message_to_dict — assistant role 输出正确。"""
        m = Message(role="assistant", content="I can help")
        result = message_to_dict(m)
        assert result == {"role": "assistant", "content": "I can help"}

    def test_with_tool_call_id(self):
        """message_to_dict — tool_call_id 非 None 时输出含该字段。"""
        m = Message(role="tool", content="result", tool_call_id="call_1")
        result = message_to_dict(m)
        assert result == {
            "role": "tool",
            "content": "result",
            "tool_call_id": "call_1",
        }

    def test_without_tool_call_id(self):
        """message_to_dict — tool_call_id 为 None 时输出不含该字段。"""
        m = Message(role="assistant", content="hi")
        result = message_to_dict(m)
        assert "tool_call_id" not in result
        assert set(result.keys()) == {"role", "content"}


# ---------------------------------------------------------------------------
# dict_to_message tests
# ---------------------------------------------------------------------------


class TestDictToMessage:
    """dict_to_message 测试。"""

    def test_basic_dict(self):
        """dict_to_message — 基本 dict 正确解析。"""
        m = dict_to_message({"role": "user", "content": "hello"})
        assert isinstance(m, Message)
        assert m.role == "user"
        assert m.content == "hello"
        assert m.tool_call_id is None

    def test_with_tool_call_id(self):
        """dict_to_message — 含 tool_call_id 的 tool dict 正确解析。"""
        m = dict_to_message({
            "role": "tool",
            "content": "ok",
            "tool_call_id": "call_1",
        })
        assert m.role == "tool"
        assert m.content == "ok"
        assert m.tool_call_id == "call_1"

    def test_content_none_defaults_to_empty(self):
        """dict_to_message — content 为 None 时默认空字符串。"""
        m = dict_to_message({"role": "assistant"})
        assert m.content == ""

    def test_ignores_extra_fields(self):
        """dict_to_message — 忽略 tool_calls 等非标字段（不抛异常）。"""
        m = dict_to_message({
            "role": "assistant",
            "content": "hi",
            "tool_calls": [{"id": "c1"}],
        })
        assert m.role == "assistant"
        assert m.content == "hi"

    def test_missing_role_defaults_to_user(self):
        """dict_to_message — 缺失 role 时默认 "user"。"""
        m = dict_to_message({"content": "hello"})
        assert m.role == "user"
        assert m.content == "hello"


# ---------------------------------------------------------------------------
# message_to_dict ↔ dict_to_message roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    """往返一致性测试。"""

    @pytest.mark.parametrize("role,content,tool_call_id", [
        ("user", "hello", None),
        ("system", "You are helpful", None),
        ("assistant", "I can help", None),
        ("tool", "result", "call_abc"),
    ])
    def test_roundtrip_consistency(self, role, content, tool_call_id):
        """message_to_dict ↔ dict_to_message 往返一致性。"""
        original = Message(role=role, content=content, tool_call_id=tool_call_id)
        dict_form = message_to_dict(original)
        restored = dict_to_message(dict_form)
        assert restored.role == original.role
        assert restored.content == original.content
        assert restored.tool_call_id == original.tool_call_id


# ---------------------------------------------------------------------------
# messages_to_dicts tests
# ---------------------------------------------------------------------------


class TestMessagesToDicts:
    """messages_to_dicts 批量转换测试。"""

    def test_empty_list(self):
        """空列表返回 []。"""
        assert messages_to_dicts([]) == []

    def test_single_message(self):
        """单个 Message 正确转换。"""
        result = messages_to_dicts([Message(role="user", content="hi")])
        assert result == [{"role": "user", "content": "hi"}]

    def test_multiple_messages(self):
        """多个 Message 正确转换。"""
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="usr"),
        ]
        result = messages_to_dicts(msgs)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "sys"}
        assert result[1] == {"role": "user", "content": "usr"}


# ---------------------------------------------------------------------------
# tool_definition_to_openai tests
# ---------------------------------------------------------------------------


class TestToolDefinitionToOpenAI:
    """tool_definition_to_openai 测试。"""

    def test_format_matches_openai_spec(self):
        """输出格式与 OpenAI spec 一致。"""
        td = ToolDefinition(
            name="read",
            description="Read a file",
            parameters={"type": "object", "properties": {}},
        )
        result = tool_definition_to_openai(td)
        assert result == {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def test_empty_parameters(self):
        """空 parameters 正确输出。"""
        td = ToolDefinition(name="test")
        result = tool_definition_to_openai(td)
        assert result["type"] == "function"
        assert result["function"]["name"] == "test"
        assert result["function"]["parameters"] == {}


# ---------------------------------------------------------------------------
# tool_definitions_to_openai tests
# ---------------------------------------------------------------------------


class TestToolDefinitionsToOpenAI:
    """tool_definitions_to_openai 批量转换测试。"""

    def test_empty_list_returns_empty(self):
        """空列表返回 []。"""
        assert tool_definitions_to_openai([]) == []

    def test_multiple_tools(self):
        """多个 ToolDefinition 输出长度正确，每个格式正确。"""
        tds = [
            ToolDefinition(name="a", description="Tool A"),
            ToolDefinition(name="b", description="Tool B"),
        ]
        result = tool_definitions_to_openai(tds)
        assert len(result) == 2
        for r in result:
            assert "function" in r
            assert r["type"] == "function"


# ---------------------------------------------------------------------------
# build_assistant_message / build_tool_result_message tests
# ---------------------------------------------------------------------------


class TestExistingFunctions:
    """现有函数（签名已升级为正式类型）测试。"""

    def test_build_assistant_message_with_text(self):
        """build_assistant_message — Response 正式类型，纯 text。"""
        r = Response(text="Hello", stop_reason="end_turn")
        msg = build_assistant_message(r)
        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello"

    def test_build_assistant_message_with_tool_calls(self):
        """build_assistant_message — Response 含 tool_uses。"""
        r = Response(
            tool_uses=[
                ToolCall(
                    id="call_1",
                    type="function",
                    function=ToolCallFunction(name="read", arguments='{"path":"/x"}'),
                )
            ],
            stop_reason="tool_use",
        )
        msg = build_assistant_message(r)
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "call_1"
        assert msg["tool_calls"][0]["function"]["name"] == "read"

    def test_build_tool_result_message_success(self):
        """build_tool_result_message — ToolCall 正式类型，成功结果。"""
        tc = ToolCall(id="call_1")
        msg = build_tool_result_message(tc, "file contents")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_1"
        assert msg["content"] == "file contents"

    def test_build_tool_result_message_error(self):
        """build_tool_result_message — ToolCall 正式类型，错误结果。"""
        tc = ToolCall(id="call_2")
        msg = build_tool_result_message(tc, None, error="Permission denied")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_2"
        assert "Error" in msg["content"]
