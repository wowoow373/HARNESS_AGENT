"""Harness Agent Template — 消息构造工具。

将编排器内部数据结构转换为 OpenAI 兼容的 message dict 格式。
"""

from typing import Any, Dict, List

from ..core.types import _MinimalResponse, _MinimalToolCall


def build_assistant_message(response: _MinimalResponse) -> Dict[str, Any]:
    """将 Response 转为含 tool_calls 的 assistant message dict。

    OpenAI 格式::

        {
            "role": "assistant",
            "content": "<text or None>",
            "tool_calls": [
                {
                    "id": "call_xxx",
                    "type": "function",
                    "function": {"name": "xxx", "arguments": "<json string>"}
                }
            ]
        }

    Args:
        response: LLM 响应。

    Returns:
        OpenAI 兼容的 assistant message dict。
    """
    msg: Dict[str, Any] = {"role": "assistant"}
    if response.text:
        msg["content"] = response.text
    else:
        msg["content"] = None

    if response.tool_uses:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in response.tool_uses
        ]

    return msg


def build_tool_result_message(
    tool_call: _MinimalToolCall,
    result: Any,
    error: str | None = None,
) -> Dict[str, Any]:
    """将 tool 执行结果转换为 tool result message dict。

    OpenAI 格式::

        {
            "role": "tool",
            "tool_call_id": "call_xxx",
            "content": "<result as string>"
        }

    Args:
        tool_call: 原始工具调用。
        result: 执行结果。
        error: 错误信息（如有）。

    Returns:
        OpenAI 兼容的 tool result message dict。
    """
    if error:
        content = f"Error: {error}"
    elif hasattr(result, "content"):
        content = str(result.content)
    else:
        content = str(result)

    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": content,
    }
