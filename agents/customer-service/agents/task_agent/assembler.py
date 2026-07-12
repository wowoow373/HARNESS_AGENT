"""TaskAssembler — stub for business operation intent."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class TaskAssembler:
    """Minimal assembler for task intent (MVP placeholder)."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        system = """你是业务办理助手。当前为 MVP 占位版本。

你可以：
- 确认用户意图
- 引导用户提供必要信息（如订单号）

请回复用户，告知当前可提供的服务。"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
