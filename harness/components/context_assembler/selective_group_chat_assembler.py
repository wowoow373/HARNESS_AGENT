"""SelectiveGroupChatAssembler — 要求 LLM 先选择后回复的上下文组装器。

消费 FlexibleGroupChatInputAdapter 传入的 UserRequest.metadata["buffered"]
结构化消息，将其组装为 LLM 可理解的群聊上下文。

核心职责：
- 消费 UserRequest.metadata["buffered"] 中的结构化消息
- 给缓冲中的消息编号
- 注入群聊规则、性格设定、表情规则
- 过滤/压缩 _history 中的旧记录

历史记录处理策略（MVP 妥协）：
1. 丢弃所有旧的 role="user" 消息（之前回合的缓冲 dump，信息已过时）
2. 保留最近 2 条 role="assistant" 消息（该 Agent 自己之前说过的话）
3. 压缩 assistant 消息：用正则提取 [回复] 内容，丢弃 [选择] 标记，
   包装为 "你之前说过：" 的 system 提示

LLM 看到的最终消息列表：
1. 身份 + 群聊规则 + 表情规则 (system)
2. 自己之前说过的话 (system, 可选)
3. 当前缓冲消息编号 (system)
4. 选择回复指令 (user)
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from ...interfaces.context_assembler import ContextAssembler
from ...interfaces.types import AssemblyContext, Message

logger = logging.getLogger(__name__)

# ── Regex for extracting [回复] content from history ──
_RE_REPLY_EXTRACT = re.compile(r'\[回复\]\s*(.+)', re.DOTALL)
_RE_SELECT_STRIP = re.compile(r'\[选择\]\s*\d+\s*')

# ── Group chat rules (hardcoded per MVP) ──
GROUP_CHAT_RULES = """## 群聊规则
- 当前是一个多人群聊，你在和几个朋友聊天
- 你只能从"最近群聊消息"中选择一条回复
- 如果没人说话值得回，输出 [选择] 0 和 [回复] 无
- 回复要口语化、简短，像真人微信聊天
- 不要重复别人已经说过的内容
- 不要过度热情，偶尔潜水也是正常的"""

# ── Emoji rules (hardcoded, reusing chat-web's 5 IDs) ──
EMOJI_RULES = """## 表情规则
- 可用表情（ONLY these 5）:
  :happy: — 开心、高兴
  :laugh: — 大笑、觉得好笑
  :cool:  — 酷、赞、认可
  :cry:   — 悲伤、无奈
  :cute:  — 可爱、温柔
- 每条回复最多用两个表情，自然插入在对应情绪的句子末尾
- 绝不用 Unicode 表情（如 😊 👍）
- 如果没合适的情绪，不用表情"""

# ── Reply instruction template ──
REPLY_INSTRUCTION = """请从以上消息中选择一条回复。输出格式：
[选择] <编号或0>
[回复] <内容或'无'>"""

# ── Default persona (overridden by metadata) ──
DEFAULT_PERSONA = "你是一个友善的群聊参与者。"


class SelectiveGroupChatAssembler:
    """群聊上下文组装器。

    实现 ContextAssembler Protocol。消费 UserRequest.metadata["buffered"]
    结构化消息，注入群聊规则，过滤历史。

    Usage::

        container.register(ContextAssembler, SelectiveGroupChatAssembler())
    """

    def __init__(
        self,
        display_name: Optional[str] = None,
        persona: Optional[str] = None,
        speaking_style: Optional[str] = None,
        interests: Optional[str] = None,
        max_consecutive_replies: Optional[int] = None,
        initial_injection: Optional[str] = None,
        injection_rounds: int = 0,
    ):
        """初始化 SelectiveGroupChatAssembler。

        Args:
            display_name: Agent 在群聊中的显示名称。
            persona: 性格描述。若为 None，使用默认值。
            speaking_style: 说话风格补充。若为 None 则不追加。
            interests: 兴趣话题描述。匹配时 Agent 会变得更热情话多。
            max_consecutive_replies: 最大连续自回复轮数。
            initial_injection: 前 N 轮注入的系统提示（如推广产品）。
                               持续 injection_rounds 轮后自动移除。
            injection_rounds: initial_injection 持续的轮数。
        """
        self._display_name = display_name
        self._persona = persona
        self._speaking_style = speaking_style
        self._interests = interests
        self._max_consecutive_replies = max_consecutive_replies
        self._initial_injection = initial_injection
        self._injection_rounds = injection_rounds

    # ------------------------------------------------------------------
    # ContextAssembler implementation
    # ------------------------------------------------------------------

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        """将 AssemblyContext 组装为群聊 LLM 上下文。

        Args:
            ctx: 包含 user_request, history, guides 等的上下文大包。

        Returns:
            List[Message]: 按顺序排列的消息列表。
        """
        messages: List[Message] = []

        # ── Determine display name and persona ──
        display_name = self._display_name or "Agent"
        persona = self._persona or DEFAULT_PERSONA
        speaking_style = self._speaking_style or ""

        # ── 1. Identity + rules system message ──
        identity_text = (
            f"你是{display_name}，你的性格是：{persona}。\n"
            f"请始终用{display_name}的身份说话，保持角色一致性。"
        )
        if speaking_style:
            identity_text += f"\n你的说话风格：{speaking_style}"

        # ── Interests system ──
        if self._interests:
            identity_text += (
                f"\n\n## 你的兴趣\n{self._interests}"
                f"\n当话题匹配到你的兴趣时，你会变得明显更热情、回复更长更积极。"
                f"当话题不匹配时，保持你原本的性格和说话习惯。"
            )

        system_content = f"{identity_text}\n\n{GROUP_CHAT_RULES}\n\n{EMOJI_RULES}"

        # ── Temporary prompt injection (前N轮推广产品等) ──
        if self._initial_injection and self._injection_rounds > 0:
            own_count = self._count_own_replies(ctx.history)
            if own_count < self._injection_rounds:
                system_content += f"\n\n{self._initial_injection}"
                logger.debug(
                    f"SelectiveGroupChatAssembler: injection active "
                    f"(round {own_count + 1}/{self._injection_rounds})"
                )

        messages.append(Message(role="system", content=system_content))

        # ── 2. Extract previous replies from history ──
        previous_replies = self._extract_previous_replies(ctx.history)
        if previous_replies:
            history_text = "你之前说过：\n" + "\n".join(
                f'- "{r}"' for r in previous_replies
            )
            messages.append(Message(role="system", content=history_text))
            logger.debug(
                f"SelectiveGroupChatAssembler: included {len(previous_replies)} "
                f"previous replies from history"
            )

        # ── 3. Buffered messages ──
        buffered = self._get_buffered_messages(ctx)
        if buffered:
            buffered_text = "最近群聊消息：\n" + "\n".join(
                f"{i}. [{m.get('from_name', m.get('from', '?'))}] {m.get('content', '')}"
                for i, m in enumerate(buffered, 1)
            )
            messages.append(Message(role="system", content=buffered_text))
            logger.debug(
                f"SelectiveGroupChatAssembler: included {len(buffered)} "
                f"buffered messages"
            )
        else:
            # No buffered messages — this shouldn't normally happen
            messages.append(
                Message(role="system", content="最近群聊消息：\n（暂无新消息）")
            )

        # ── 4. Rate limiting: count total own replies in history ──
        own_reply_count = self._count_own_replies(ctx.history)

        # ── 5. Reply instruction ──
        instruction = REPLY_INSTRUCTION
        if (self._max_consecutive_replies is not None
                and own_reply_count >= self._max_consecutive_replies):
            # Check if anyone else has spoken recently (non-self messages in buffer)
            others_spoken = any(
                m.get("from") != "system"
                for m in buffered
            ) if buffered else False

            if not others_spoken:
                # Force silence — nobody else has responded yet
                instruction = (
                    f"你已经连续说了{own_reply_count}轮话了，还没有其他人回应。"
                    f"请给别人说话的机会。\n"
                    f"本轮必须选择：[选择] 0\n[回复] 无"
                )
                logger.debug(
                    f"SelectiveGroupChatAssembler: rate-limiting "
                    f"'{display_name}' after {own_reply_count} self-replies"
                )

        messages.append(Message(role="user", content=instruction))

        return messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_buffered_messages(self, ctx: AssemblyContext) -> list[dict]:
        """从 AssemblyContext 中提取 buffered 消息列表。

        优先从 user_request.metadata["buffered"] 读取结构化数据。
        若不存在（fallback），返回空列表。
        """
        if ctx.user_request and ctx.user_request.metadata:
            buffered = ctx.user_request.metadata.get("buffered")
            if buffered and isinstance(buffered, list):
                return buffered

        return []

    def _extract_previous_replies(self, history: List[Message]) -> list[str]:
        """从历史中提取该 Agent 自己之前说过的回复。

        策略（MVP）：
        1. 丢弃所有 role="user" 消息（之前的缓冲 dump）
        2. 保留最近 2 条 role="assistant" 消息
        3. 用正则提取 [回复] 内容，丢弃 [选择] 标记
        4. 跳过内容为"无"或空的 assistant 消息

        Args:
            history: 当前会话的完整对话历史。

        Returns:
            list[str]: 最近 2 条有效回复内容。
        """
        replies = []

        for msg in reversed(history):
            if msg.role != "assistant":
                continue

            content = msg.content or ""

            # Strip [选择] marker
            content = _RE_SELECT_STRIP.sub("", content)

            # Try to extract [回复] content
            reply_match = _RE_REPLY_EXTRACT.search(content)
            if reply_match:
                reply_text = reply_match.group(1).strip()
            else:
                reply_text = content.strip()

            # Skip empty or "no reply" responses
            if not reply_text or reply_text == "无":
                continue

            replies.append(reply_text)

            if len(replies) >= 2:
                break

        # Reverse back to chronological order
        replies.reverse()
        return replies

    def _count_own_replies(self, history: List[Message]) -> int:
        """Count all assistant messages in history (no cap, unlike _extract_previous_replies).

        Used for rate limiting — if agent has spoken too many times without
        anyone else responding, force silence.

        Args:
            history: Current session conversation history.

        Returns:
            int: Total number of non-empty assistant replies.
        """
        count = 0
        for msg in reversed(history):
            if msg.role != "assistant":
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            # Extract [回复] content
            reply_match = _RE_REPLY_EXTRACT.search(content)
            if reply_match:
                reply_text = reply_match.group(1).strip()
            else:
                reply_text = content
            # Count only if agent actually said something
            if reply_text and reply_text != "无":
                count += 1
        return count
