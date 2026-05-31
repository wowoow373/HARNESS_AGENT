"""Harness Agent Template — 最小化 LLM 调用适配器。

使用 Python 标准库 urllib 发送 HTTP 请求到 OpenAI 兼容 API。
零外部依赖，开箱即用。支持任何 OpenAI 兼容 endpoint
（OpenAI、Ollama、vLLM、LM Studio 等）。

用法::

    adapter = MinimalLLMAdapter(
        base_url="https://api.openai.com/v1",
        api_key="sk-xxx",
        model="gpt-4o",
    )
    response = adapter(messages, tools)
"""

import json as json_module
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .exceptions import OrchestratorError
from .orchestrator import _MinimalResponse, _MinimalToolCall


class MinimalLLMAdapter:
    """最小化 OpenAI 兼容 LLM 适配器。

    使用标准库 urllib 发送 HTTP 请求到 OpenAI 兼容 API。
    零外部依赖，开箱即用。实现 call_llm 签名约定，
    可直接注入 LifecycleOrchestrator。

    用法::

        adapter = MinimalLLMAdapter(
            base_url="https://api.openai.com/v1",
            api_key="sk-xxx",
            model="gpt-4o",
        )
        harness = Harness.from_container(container, call_llm=adapter)
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 120,
    ):
        """初始化适配器。

        Args:
            base_url: OpenAI 兼容 API 的 base URL。
                      支持替换为 Ollama (http://localhost:11434/v1)、
                      vLLM 等任何兼容端点。
            api_key: API 密钥。空字符串时从环境变量
                     OPENAI_API_KEY 读取。
            model: 模型名称。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度 (0.0 ~ 2.0)。
            timeout: HTTP 请求超时时间（秒）。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._endpoint = f"{self.base_url}/chat/completions"

    def __call__(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> _MinimalResponse:
        """调用 LLM API。

        实现 call_llm 签名约定，可直接注入 LifecycleOrchestrator。

        Args:
            messages: OpenAI 格式的消息列表。
            tools: 工具定义列表（可选）。

        Returns:
            _MinimalResponse: 标准化的 LLM 响应。

        Raises:
            OrchestratorError: API 调用失败时抛出。
        """
        body = self._build_request_body(messages, tools)
        response_json = self._send_request(body)
        return self._parse_response(response_json)

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """构建 OpenAI /v1/chat/completions 请求体。

        Args:
            messages: 消息列表。
            tools: 工具定义列表（可选）。

        Returns:
            HTTP 请求体 dict。
        """
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            body["tools"] = tools
        return body

    def _send_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """发送 HTTP POST 请求，返回解析后的 JSON 响应。

        Args:
            body: 请求体 dict。

        Returns:
            解析后的 JSON 响应 dict。

        Raises:
            OrchestratorError: 网络错误、超时、非 2xx 响应。
        """
        data = json_module.dumps(body).encode("utf-8")
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self._endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response_bytes = resp.read()
                return json_module.loads(response_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = "<unable to read error body>"
            raise OrchestratorError(
                f"LLM API error {e.code}: {error_body[:500]}"
            ) from e
        except urllib.error.URLError as e:
            raise OrchestratorError(
                f"LLM API unreachable: {self._endpoint} — {e.reason}"
            ) from e
        except json_module.JSONDecodeError as e:
            raise OrchestratorError(
                f"LLM API returned invalid JSON: {e}"
            ) from e
        except Exception as e:
            raise OrchestratorError(
                f"LLM API unexpected error: {e}"
            ) from e

    def _parse_response(self, response_json: Dict[str, Any]) -> _MinimalResponse:
        """将 OpenAI chat completion 响应解析为 _MinimalResponse。

        处理三种响应形态：
        - 纯 text: choices[0].message.content 有值, 无 tool_calls
        - 纯 tool_use: choices[0].message.content 为 None, 有 tool_calls
        - text + tool_use 共存: 两者都有

        Args:
            response_json: API 返回的 JSON 响应。

        Returns:
            _MinimalResponse: 标准化的响应对象。

        Raises:
            OrchestratorError: 响应格式不符合预期。
        """
        try:
            choice = response_json["choices"][0]
        except (KeyError, IndexError) as e:
            raise OrchestratorError(
                f"LLM API unexpected response format: "
                f"{str(response_json)[:500]}"
            ) from e

        message = choice.get("message", {})

        # 提取 text
        text: Optional[str] = message.get("content")

        # 提取 tool_uses
        tool_uses: List[_MinimalToolCall] = []
        raw_tool_calls = message.get("tool_calls", [])
        if raw_tool_calls:
            for tc in raw_tool_calls:
                try:
                    tool_uses.append(_MinimalToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    ))
                except (KeyError, TypeError) as e:
                    logger = __import__("logging").getLogger(__name__)
                    logger.warning(
                        f"Skipping malformed tool_call in response: {e}"
                    )

        # 提取 stop_reason（标准化映射）
        finish_reason = choice.get("finish_reason", "stop")
        if finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif finish_reason in ("stop", "length", "content_filter"):
            stop_reason = "end_turn"
        else:
            stop_reason = finish_reason

        return _MinimalResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=stop_reason,
        )
