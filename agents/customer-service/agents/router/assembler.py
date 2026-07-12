"""RouterAssembler — intent classification + answer formatting context."""
from harness.interfaces.types import AssemblyContext, Message
from typing import List


class RouterAssembler:
    """Assembles prompts for intent classification and answer formatting."""

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata if ctx.user_request else {}

        # Path A: QA answer from Validation → format for user
        if meta.get("type") == "qa_answer":
            system = "你是客服助手，将以下答案用友好的语气告诉用户："
            user = meta['answer']
            return [
                Message(role="system", content=system),
                Message(role="user", content=user),
            ]

        # Path B: User message → intent classification
        system = """你是客服系统入口路由。分析用户消息，判定意图。

意图类型：
- qa: 政策咨询、知识问答、事实性问题（如"改签规则是什么？""赔偿标准？"）
- task: 明确要办理业务（如"我要改签""帮我退款"）
- fallback: 意图不明、敏感问题、超出客服范围

输出格式（严格遵守）：
INTENT: <qa|task|fallback>
CONFIDENCE: <0-1>
SLOTS: <JSON dict>"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
