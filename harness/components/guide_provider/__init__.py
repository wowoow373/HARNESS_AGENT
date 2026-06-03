"""GuideProvider 默认实现 — FileGuideProvider。

从 AGENTS.md / CLAUDE.md 等 Markdown 文件读取 Agent 指导信息。
"""

from .file_guide_provider import FileGuideProvider

__all__ = ["FileGuideProvider"]
