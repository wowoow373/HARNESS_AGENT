"""MemoryBackend 默认实现 — MdMemory。

基于 Markdown 文件的简单记忆存储，每个记忆项一个 .md 文件。
"""

from .md_memory import MdMemory

__all__ = ["MdMemory"]
