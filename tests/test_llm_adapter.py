"""Test harness for MinimalLLMAdapter."""

import io
import json
import os
import urllib.error
from unittest import mock

import pytest

from harness.core.exceptions import OrchestratorError
from harness.core.llm_adapter import (
    MinimalLLMAdapter,
    _read_simple_dotenv,
)
from harness.interfaces.types import Response


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestMinimalLLMAdapterInit:
    """MinimalLLMAdapter 构造函数测试。"""

    def test_default_values(self):
        """不传参数时使用硬编码默认值（mock .env 为空）。"""
        with mock.patch("harness.adapters.llm_adapter._read_simple_dotenv",
                        return_value={}):
            adapter = MinimalLLMAdapter()
        assert adapter.base_url == "https://api.openai.com/v1"
        assert adapter.api_key == ""
        assert adapter.model == "gpt-4o"
        assert adapter.max_tokens == 4096
        assert adapter.temperature == 0.7
        assert adapter.timeout == 120
        assert adapter._endpoint == "https://api.openai.com/v1/chat/completions"

    def test_dotenv_values_used_when_no_args(self):
        """.env 文件中的 base_url、model、api_key 被自动读取。"""
        with mock.patch("harness.adapters.llm_adapter._read_simple_dotenv",
                        return_value={
                            "base_url": "https://api.deepseek.com",
                            "api-key": "sk-dotenv",
                            "model": "deepseek-v4-flash",
                        }):
            adapter = MinimalLLMAdapter()
        assert adapter.base_url == "https://api.deepseek.com"
        assert adapter.api_key == "sk-dotenv"
        assert adapter.model == "deepseek-v4-flash"
        assert adapter._endpoint == "https://api.deepseek.com/chat/completions"

    def test_explicit_args_override_dotenv(self):
        """显式参数优先于 .env 和环境变量。"""
        os.environ["OPENAI_API_KEY"] = "sk-env"
        os.environ["LLM_MODEL"] = "env-model"
        try:
            with mock.patch("harness.adapters.llm_adapter._read_simple_dotenv",
                            return_value={
                                "base_url": "https://dotenv.example.com",
                                "api-key": "sk-dotenv",
                                "model": "dotenv-model",
                            }):
                adapter = MinimalLLMAdapter(
                    base_url="https://explicit.example.com",
                    api_key="sk-explicit",
                    model="explicit-model",
                )
            assert adapter.base_url == "https://explicit.example.com"
            assert adapter.api_key == "sk-explicit"
            assert adapter.model == "explicit-model"
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["LLM_MODEL"]

    def test_env_vars_override_dotenv(self):
        """环境变量优先于 .env 文件。"""
        os.environ["LLM_BASE_URL"] = "https://env.example.com"
        os.environ["OPENAI_API_KEY"] = "sk-env"
        os.environ["LLM_MODEL"] = "env-model"
        try:
            with mock.patch("harness.adapters.llm_adapter._read_simple_dotenv",
                            return_value={
                                "base_url": "https://dotenv.example.com",
                                "api-key": "sk-dotenv",
                                "model": "dotenv-model",
                            }):
                adapter = MinimalLLMAdapter()
            assert adapter.base_url == "https://env.example.com"
            assert adapter.api_key == "sk-env"
            assert adapter.model == "env-model"
        finally:
            del os.environ["LLM_BASE_URL"]
            del os.environ["OPENAI_API_KEY"]
            del os.environ["LLM_MODEL"]

    def test_explicit_api_key(self):
        """显式传入 api_key 被使用。"""
        adapter = MinimalLLMAdapter(api_key="sk-test123")
        assert adapter.api_key == "sk-test123"

    def test_api_key_from_env(self):
        """从环境变量读取 api_key（无 .env 时）。"""
        os.environ["OPENAI_API_KEY"] = "sk-from-env"
        try:
            with mock.patch("harness.adapters.llm_adapter._read_simple_dotenv",
                            return_value={}):
                adapter = MinimalLLMAdapter()
            assert adapter.api_key == "sk-from-env"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_api_key_explicit_priority(self):
        """构造参数优先于环境变量。"""
        os.environ["OPENAI_API_KEY"] = "sk-from-env"
        try:
            adapter = MinimalLLMAdapter(api_key="sk-explicit")
            assert adapter.api_key == "sk-explicit"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_base_url_trailing_slash_removed(self):
        """base_url 尾部斜杠被去除。"""
        adapter = MinimalLLMAdapter(base_url="http://localhost:11434/v1/")
        assert adapter.base_url == "http://localhost:11434/v1"
        assert (
            adapter._endpoint
            == "http://localhost:11434/v1/chat/completions"
        )

    def test_custom_model_and_params(self):
        """自定义 model 和参数。"""
        adapter = MinimalLLMAdapter(
            model="gpt-4o-mini", max_tokens=1024, temperature=0.3, timeout=60
        )
        assert adapter.model == "gpt-4o-mini"
        assert adapter.max_tokens == 1024
        assert adapter.temperature == 0.3
        assert adapter.timeout == 60

    def test_empty_api_key_allowed(self):
        """空 api_key 不立即报错。"""
        os.environ.pop("OPENAI_API_KEY", None)
        adapter = MinimalLLMAdapter(api_key="")
        assert adapter.api_key == ""


# ---------------------------------------------------------------------------
# _build_request_body
# ---------------------------------------------------------------------------


class TestBuildRequestBody:
    """_build_request_body 测试。"""

    def test_without_tools(self):
        """tools 为 None 时请求体不包含 tools 字段。"""
        adapter = MinimalLLMAdapter(model="test-model")
        body = adapter._build_request_body(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
        )
        assert body["model"] == "test-model"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert "tools" not in body

    def test_with_tools(self):
        """tools 非空时正确包含。"""
        adapter = MinimalLLMAdapter(model="test-model")
        body = adapter._build_request_body(
            messages=[{"role": "user", "content": "read"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read a file",
                        "parameters": {},
                    },
                }
            ],
        )
        assert "tools" in body
        assert len(body["tools"]) == 1

    def test_messages_passed_through(self):
        """messages 原样传递。"""
        adapter = MinimalLLMAdapter()
        msgs = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
        ]
        body = adapter._build_request_body(msgs, None)
        assert body["messages"] == msgs

    def test_max_tokens_and_temperature_included(self):
        """max_tokens 和 temperature 包含在请求体中。"""
        adapter = MinimalLLMAdapter(max_tokens=2048, temperature=0.5)
        body = adapter._build_request_body(
            [{"role": "user", "content": "hi"}], None
        )
        assert body["max_tokens"] == 2048
        assert body["temperature"] == 0.5


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    """_parse_response 测试。"""

    def test_parse_text_response(self):
        """解析纯 text 响应。"""
        adapter = MinimalLLMAdapter(api_key="test")
        response_json = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help?",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        result = adapter._parse_response(response_json)
        assert isinstance(result, Response)
        assert result.text == "Hello! How can I help?"
        assert result.tool_uses == []
        assert result.stop_reason == "end_turn"

    def test_parse_tool_use_response(self):
        """解析纯 tool_use 响应。"""
        adapter = MinimalLLMAdapter(api_key="test")
        response_json = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path": "/tmp/x"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        result = adapter._parse_response(response_json)
        assert result.text is None
        assert len(result.tool_uses) == 1
        assert result.tool_uses[0].id == "call_abc123"
        assert result.tool_uses[0].function.name == "read"
        assert result.tool_uses[0].function.arguments == '{"path": "/tmp/x"}'
        assert result.stop_reason == "tool_use"

    def test_parse_coexistence_response(self):
        """解析 text + tool_uses 共存响应。"""
        adapter = MinimalLLMAdapter(api_key="test")
        response_json = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Let me check that file for you",
                        "tool_calls": [
                            {
                                "id": "call_def456",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path": "/tmp/x"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        result = adapter._parse_response(response_json)
        assert result.text == "Let me check that file for you"
        assert len(result.tool_uses) == 1

    def test_parse_multiple_tool_calls(self):
        """解析多个 tool_calls。"""
        adapter = MinimalLLMAdapter(api_key="test")
        response_json = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path":"/x"}',
                                },
                            },
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": '{"path":"/y","content":"z"}',
                                },
                            },
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        result = adapter._parse_response(response_json)
        assert len(result.tool_uses) == 2
        assert result.tool_uses[0].function.name == "read"
        assert result.tool_uses[1].function.name == "write"

    def test_parse_invalid_response_raises(self):
        """无效响应（缺少 choices）抛出 OrchestratorError。"""
        adapter = MinimalLLMAdapter(api_key="test")
        with pytest.raises(OrchestratorError):
            adapter._parse_response({"no_choices": []})

    def test_parse_empty_choices_raises(self):
        """空 choices 抛出 OrchestratorError。"""
        adapter = MinimalLLMAdapter(api_key="test")
        with pytest.raises(OrchestratorError):
            adapter._parse_response({"choices": []})

    def test_finish_reason_tool_calls_maps_to_tool_use(self):
        """finish_reason "tool_calls" 映射为 "tool_use"。"""
        adapter = MinimalLLMAdapter(api_key="test")
        result = adapter._parse_response({
            "choices": [
                {
                    "message": {"content": None, "tool_calls": []},
                    "finish_reason": "tool_calls",
                }
            ]
        })
        assert result.stop_reason == "tool_use"

    def test_finish_reason_stop_maps_to_end_turn(self):
        """finish_reason "stop" 映射为 "end_turn"。"""
        adapter = MinimalLLMAdapter(api_key="test")
        result = adapter._parse_response({
            "choices": [
                {"message": {"content": "done"}, "finish_reason": "stop"}
            ]
        })
        assert result.stop_reason == "end_turn"

    def test_finish_reason_length_maps_to_end_turn(self):
        """finish_reason "length" 映射为 "end_turn"。"""
        adapter = MinimalLLMAdapter(api_key="test")
        result = adapter._parse_response({
            "choices": [
                {
                    "message": {"content": "truncated..."},
                    "finish_reason": "length",
                }
            ]
        })
        assert result.stop_reason == "end_turn"

    def test_finish_reason_unknown_passthrough(self):
        """未知 finish_reason 原样保留。"""
        adapter = MinimalLLMAdapter(api_key="test")
        result = adapter._parse_response({
            "choices": [
                {
                    "message": {"content": "ok"},
                    "finish_reason": "custom_reason",
                }
            ]
        })
        assert result.stop_reason == "custom_reason"


# ---------------------------------------------------------------------------
# __call__
# ---------------------------------------------------------------------------


class TestCall:
    """__call__ 测试。"""

    def test_call_returns_minimal_response(self):
        """__call__ 返回 Response 类型。"""
        adapter = MinimalLLMAdapter(api_key="test")

        mock_response = {
            "choices": [
                {"message": {"content": "hello"}, "finish_reason": "stop"}
            ]
        }

        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = (
                json.dumps(mock_response).encode("utf-8")
            )
            result = adapter([{"role": "user", "content": "hi"}])
            assert isinstance(result, Response)
            assert result.text == "hello"

    def test_call_signature_matches_call_llm(self):
        """__call__ 签名匹配 call_llm 约定 (messages, tools)。"""
        adapter = MinimalLLMAdapter(api_key="test")

        mock_response = {
            "choices": [
                {"message": {"content": "ok"}, "finish_reason": "stop"}
            ]
        }

        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = (
                json.dumps(mock_response).encode("utf-8")
            )
            # 两个参数形式
            result = adapter(
                [{"role": "user", "content": "hi"}],
                [{"type": "function", "function": {"name": "read", "description": "...", "parameters": {}}}],
            )
            assert result.text == "ok"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """错误处理测试。"""

    def test_http_error_wrapped_in_orchestrator_error(self):
        """HTTP 错误（4xx/5xx）被包装为 OrchestratorError。"""
        adapter = MinimalLLMAdapter(api_key="test")

        with mock.patch("urllib.request.urlopen") as m:
            m.side_effect = urllib.error.HTTPError(
                "http://fake.url",
                401,
                "Unauthorized",
                {},
                io.BytesIO(b'{"error":"invalid_api_key"}'),
            )
            with pytest.raises(OrchestratorError) as exc_info:
                adapter([{"role": "user", "content": "hi"}])
            assert "401" in str(exc_info.value)

    def test_url_error_wrapped_in_orchestrator_error(self):
        """网络不可达错误被包装为 OrchestratorError。"""
        adapter = MinimalLLMAdapter(api_key="test")

        with mock.patch("urllib.request.urlopen") as m:
            m.side_effect = urllib.error.URLError("connection refused")
            with pytest.raises(OrchestratorError) as exc_info:
                adapter([{"role": "user", "content": "hi"}])
            assert "unreachable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Zero dependency check
# ---------------------------------------------------------------------------


class TestZeroDependency:
    """零外部依赖验证。"""

    def test_no_third_party_imports(self):
        """验证 MinimalLLMAdapter 不引入第三方依赖。"""
        import inspect

        # 获取模块源代码
        source = inspect.getsource(MinimalLLMAdapter)
        # 验证不包含常见的第三方库导入
        forbidden = ["import openai", "import requests", "import httpx", "import aiohttp"]
        for f in forbidden:
            assert f not in source, f"Found forbidden import: {f}"
