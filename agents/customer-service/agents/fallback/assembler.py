"""FallbackAssembler — stub for out-of-scope / low-confidence intents."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class FallbackAssembler:
    """Minimal assembler for fallback intent (MVP placeholder)."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        system = """你是异常兜底助手。当前为 MVP 占位版本。

当用户意图不明或超出客服范围时，你的职责是：
- 输出标准兜底话术
- 建议用户转人工客服

请用礼貌的语气回复用户。"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
