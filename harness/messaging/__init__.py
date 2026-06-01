"""Harness Agent Template — 消息构造模块。

提供编排器与 LLM 之间的消息格式转换工具。
"""

from .builder import build_assistant_message, build_tool_result_message

__all__ = ["build_assistant_message", "build_tool_result_message"]
