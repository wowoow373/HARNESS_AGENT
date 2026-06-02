"""Harness Agent Template — 消息构造模块。

提供编排器与 LLM 之间的消息格式转换工具。
"""

from .builder import (
    build_assistant_message,
    build_tool_result_message,
    dict_to_message,
    message_to_dict,
    messages_to_dicts,
    tool_definition_to_openai,
    tool_definitions_to_openai,
)

__all__ = [
    "build_assistant_message",
    "build_tool_result_message",
    "dict_to_message",
    "message_to_dict",
    "messages_to_dicts",
    "tool_definition_to_openai",
    "tool_definitions_to_openai",
]
