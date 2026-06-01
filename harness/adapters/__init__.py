"""Harness Agent Template — 外部系统适配器。

包含 LLM API 适配器等外部系统桥接组件。
"""

from .llm_adapter import MinimalLLMAdapter, _read_simple_dotenv

__all__ = ["MinimalLLMAdapter", "_read_simple_dotenv"]
