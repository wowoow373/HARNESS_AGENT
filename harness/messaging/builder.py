"""Harness Agent Template — 消息构造工具。

将编排器内部数据结构转换为 OpenAI 兼容的 message dict 格式。
提供 Message ↔ dict 双向转换、ToolDefinition → OpenAI tool format 转换。
"""

from typing import Any, Dict, List

from ..interfaces.types import Message, Response, ToolCall, ToolCallFunction, ToolDefinition


# ---------------------------------------------------------------------------
# Message ↔ dict 双向转换
# ---------------------------------------------------------------------------


def message_to_dict(msg: Message) -> Dict[str, Any]:
    """将 Message 对象转为 OpenAI 兼容 dict。

    Args:
        msg: Message 对象。

    Returns:
        OpenAI 兼容的 message dict。
    """
    result: Dict[str, Any] = {"role": msg.role, "content": msg.content}
    if msg.tool_call_id is not None:
        result["tool_call_id"] = msg.tool_call_id
    if msg.tool_calls is not None:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return result


def messages_to_dicts(messages: List[Any]) -> List[Dict[str, Any]]:
    """批量将 Message 列表转为 OpenAI 兼容 dict 列表。

    对于已经是 dict 的元素直接透传，Message 对象则转换。

    Args:
        messages: Message 对象或 dict 的混合列表。

    Returns:
        OpenAI 兼容的 message dict 列表。
    """
    result: List[Dict[str, Any]] = []
    for m in messages:
        if isinstance(m, Message):
            result.append(message_to_dict(m))
        elif isinstance(m, dict):
            result.append(m)
        else:
            # 兼容其他有 role/content 属性的对象
            result.append(message_to_dict(Message(
                role=getattr(m, "role", "user"),
                content=getattr(m, "content", ""),
                tool_call_id=getattr(m, "tool_call_id", None),
                tool_calls=getattr(m, "tool_calls", None),
            )))
    return result


def dict_to_message(d: Dict[str, Any]) -> Message:
    """将 OpenAI 兼容 dict 转为 Message 对象。

    从 dict 提取 role、content、tool_call_id、tool_calls。
    content 为 None 或缺失时默认 ""。

    Args:
        d: OpenAI 兼容的 message dict。

    Returns:
        Message 对象。
    """
    tool_calls_raw = d.get("tool_calls")
    tool_calls = None
    if tool_calls_raw:
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                type=tc.get("type", "function"),
                function=ToolCallFunction(
                    name=tc.get("function", {}).get("name", ""),
                    arguments=tc.get("function", {}).get("arguments", "{}"),
                ),
            )
            for tc in tool_calls_raw
        ]
    return Message(
        role=d.get("role", "user"),
        content=d.get("content") or "",
        tool_call_id=d.get("tool_call_id"),
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# ToolDefinition → OpenAI tool format
# ---------------------------------------------------------------------------


def tool_definition_to_openai(td: ToolDefinition) -> Dict[str, Any]:
    """将 ToolDefinition 转为 OpenAI tool format。

    Args:
        td: ToolDefinition 对象。

    Returns:
        OpenAI tool format dict。
    """
    return {
        "type": "function",
        "function": {
            "name": td.name,
            "description": td.description,
            "parameters": td.parameters,
        },
    }


def tool_definitions_to_openai(tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
    """批量将 ToolDefinition 列表转为 OpenAI tool format 列表。

    Args:
        tools: ToolDefinition 对象列表。

    Returns:
        OpenAI tool format dict 列表。
    """
    return [tool_definition_to_openai(t) for t in tools]


# ---------------------------------------------------------------------------
# 现有函数（签名升级为正式类型）
# ---------------------------------------------------------------------------


def build_assistant_message(response: Response) -> Dict[str, Any]:
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
    tool_call: ToolCall,
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
