"""AtomicOutputAdapter — 解析 LLM 结构化输出并原子化发送。

作为 AsyncInputAdapter 的装饰器实现。内部包装 FlexibleGroupChatInputAdapter
（或其他 AsyncInputAdapter）。

职责：
- 代理 receive() → 内部 adapter
- 拦截 send(TextEvent) → 解析 [选择]/[回复] 标记
- 将回复切分为原子短句，间隔随机延迟逐个发送

处理流程：
1. 收到 TextEvent(content="[选择] 3\n[回复] 带飞盘我可以一起！:happy:")
2. 正则提取 [选择] → choice = 3
3. 正则提取 [回复] → reply = "带飞盘我可以一起！:happy:"
4. 如果 choice == 0 或 reply == "无" → 直接返回，不发送任何消息
5. 如果 reply 有内容：
   a. 按句子切分（按中文标点：。！？\\n）
   b. 每个短句作为独立 TextEvent 发送
   c. 短句之间加入 150-400ms 随机延迟
6. 最后发送 StopEvent

容错处理：
- 找不到 [选择] 标记 → 把整个输出当作普通回复，不切分直接发送
- [回复] 为空但 [选择] 非 0 → 视为不想回复，不发送
- 解析失败 → 不发送，仅记录日志
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Optional

from ...interfaces.async_input_adapter import AsyncInputAdapter
from ...interfaces.types import StopEvent, TextEvent

logger = logging.getLogger(__name__)

# ── Regex patterns for parsing LLM structured output ──
_RE_SELECT = re.compile(r'\[选择\]\s*(\d+)')
_RE_REPLY = re.compile(r'\[回复\]\s*(.+)', re.DOTALL)

# ── Chinese sentence boundary punctuation ──
_SENTENCE_SPLITTER = re.compile(r'([。！？\n，,]+)')

# ── Emoji pattern (from chat-web manifest: :happy:, :laugh:, :cool:, :cry:, :cute:) ──
_EMOJI_PATTERN = re.compile(r':(happy|laugh|cool|cry|cute):')


class AtomicOutputAdapter:
    """解析 LLM 结构化输出并原子化发送的装饰器适配器。

    实现 AsyncInputAdapter 协议。包装内部 adapter，
    receive() 直接代理，send() 做解析和切分。

    Usage::

        inner = FlexibleGroupChatInputAdapter(min_wait=1.0, max_wait=3.0)
        atomic = AtomicOutputAdapter(inner)
        container.register(AsyncInputAdapter, atomic)
    """

    def __init__(self, inner: AsyncInputAdapter):
        """初始化 AtomicOutputAdapter。

        Args:
            inner: 内部 AsyncInputAdapter（通常是 FlexibleGroupChatInputAdapter）。
        """
        self._inner = inner

    # ------------------------------------------------------------------
    # Kernel context propagation (for _resolve_adapter)
    # ------------------------------------------------------------------

    def _inject_kernel_context(self, pid, kernel, runtime) -> None:
        """Propagate kernel context injection to inner adapter."""
        if hasattr(self._inner, '_inject_kernel_context'):
            self._inner._inject_kernel_context(
                pid=pid, kernel=kernel, runtime=runtime
            )

    # ------------------------------------------------------------------
    # AsyncInputAdapter implementation
    # ------------------------------------------------------------------

    async def receive(self):
        """代理到内部 adapter.receive()。"""
        return await self._inner.receive()

    async def send(self, event, target=None):
        """拦截 TextEvent，解析结构化输出并原子化发送。

        非 TextEvent（Thinking/ToolCall/ToolResult/Stop）直接代理。
        """
        if not isinstance(event, TextEvent):
            return await self._inner.send(event, target=target)

        text = event.content

        # ── Parse structured output ──
        choice_match = _RE_SELECT.search(text)
        reply_match = _RE_REPLY.search(text)

        if choice_match is None:
            # 容错：找不到 [选择] 标记 → 整个输出当作普通回复
            logger.warning(
                f"AtomicOutputAdapter: no [选择] marker found in LLM output, "
                f"treating whole output as plain reply"
            )
            await self._inner.send(event, target=target)
            return

        choice = int(choice_match.group(1))
        reply = reply_match.group(1).strip() if reply_match else ""

        # ── Handle "no reply" case ──
        if choice == 0 or reply == "无" or not reply:
            logger.debug(
                f"AtomicOutputAdapter: agent chose not to reply "
                f"(choice={choice}, reply='{reply[:20] if reply else ''}')"
            )
            # Still send StopEvent so the orchestrator knows this round ended
            return

        # ── Split reply into atomic sentences ──
        raw_sentences = _split_sentences(reply)

        # ── Extract emoji markers into separate items (独立冒泡) ──
        # "太好吃了！:happy:" → ["太好吃了！", ":happy:"]
        # ":laugh: 再来一份" → [":laugh:", "再来一份"]
        sentences = _extract_emojis(raw_sentences)

        logger.debug(
            f"AtomicOutputAdapter: split reply into {len(sentences)} "
            f"sentence(s): {[s[:30] for s in sentences]}"
        )

        # ── Send each sentence with delay ──
        for i, sentence in enumerate(sentences):
            if not sentence.strip():
                continue

            await self._inner.send(
                TextEvent(content=sentence.strip()),
                target=target,
            )

            # Add random delay between sentences (except after the last one).
            # Longer delays (250-800ms) create natural gaps that let other
            # agents interleave mid-conversation.
            if i < len(sentences) - 1:
                delay = random.uniform(0.25, 0.8)
                await asyncio.sleep(delay)


# ── Emoji extraction regex ──
# LLM often misses the closing colon (:happy  instead of :happy:).
# Match both the canonical form and common variants.
_RE_EMOJI = re.compile(
    r'(:(?:happy|laugh|cool|cry|cute):)'    # canonical :happy:
    r'|(:(?:happy|laugh|cool|cry|cute))'     # missing closing :happy
    r'(?=\s|$|[。！？，,\n]|[:;；：])',       #   → must be at word boundary
)


def _extract_emojis(sentences: list[str]) -> list[str]:
    """Extract emoji markers from sentences into separate items.

    Each emoji becomes its own TextEvent (独立冒泡).
    Text before/after emoji becomes separate TextEvents.

    "太好吃了！:happy:" → ["太好吃了！", ":happy:"]
    ":laugh: 再来一份" → [":laugh:", "再来一份"]
    "不错:happy"   → ["不错", ":happy:"]  (normalizes missing colon)
    """
    result = []
    for s in sentences:
        if not s or not s.strip():
            continue

        # Find all emoji tokens in this sentence
        matches = list(_RE_EMOJI.finditer(s))
        if not matches:
            # No emoji — keep sentence as-is
            text = s.strip()
            if text:
                result.append(text)
            continue

        last_end = 0
        for m in matches:
            # Text before this emoji
            prefix = s[last_end:m.start()].strip()
            if prefix:
                result.append(prefix)

            # Emoji — normalize to canonical :name: format
            emoji_raw = m.group(0)
            if not emoji_raw.startswith(':'):
                emoji_raw = ':' + emoji_raw
            if not emoji_raw.endswith(':'):
                emoji_raw = emoji_raw + ':'
            result.append(emoji_raw)

            last_end = m.end()

        # Remaining text after last emoji
        suffix = s[last_end:].strip()
        if suffix:
            result.append(suffix)

    return result


# ------------------------------------------------------------------
# Sentence splitting helper
# ------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    """按中文标点符号切分文本为短句列表。

    切分规则：
    - 按 。！？换行 切分
    - 保留标点附在前一个短句末尾
    - 移除空白短句

    Args:
        text: 待切分的完整回复文本。

    Returns:
        list[str]: 短句列表。
    """
    # Split on sentence boundaries, keeping the delimiter
    parts = _SENTENCE_SPLITTER.split(text)

    sentences = []
    current = ""
    for part in parts:
        if _SENTENCE_SPLITTER.match(part):
            # Delimiter — attach to current sentence
            current += part
            # Strip trailing commas/semicolons only, NOT colons (part of :emoji:)
            current = current.rstrip("，,;；")
            sentences.append(current)
            current = ""
        else:
            current += part

    # Don't forget trailing text without delimiter
    if current.strip():
        current = current.rstrip("，,;；")
        sentences.append(current)

    # If no sentence boundaries were found, return the whole text as one sentence
    if not sentences:
        return [text]

    return sentences
