"""AsyncCallLLM Protocol — 异步 LLM 调用接口。

定义 Runtime 路径下 AsyncLifecycleOrchestrator 期望的 LLM 调用契约。
tools 参数为可选（有默认值 None），遵循与同步版 LifecycleOrchestrator
一致的约定：编排器总是传递 messages 和 tools，但 LLM 适配器可以选择
忽略 tools 参数（如测试 mock）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable

from .types import Response


@runtime_checkable
class AsyncCallLLM(Protocol):
    """异步 LLM 调用接口。

    编排器在 _phase_loop 内层循环中调用此函数，每次传入当前消息
    和可用工具列表。返回值 Response 可包含 text / thinking / tool_uses。

    tools 参数有默认值 None——编排器总是传两个参数，但实现方在简单
    场景（测试、纯文本回答）可以仅接收 messages 参数。
    """

    async def __call__(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
    ) -> Response:
        """异步调用 LLM。

        Args:
            messages: OpenAI 格式的消息列表。
            tools: OpenAI 格式的可用工具列表。None 表示无可用的工具信息。

        Returns:
            Response: LLM 响应（text / thinking / tool_uses）。
        """
        ...
