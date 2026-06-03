"""MdMemory — MemoryBackend 的 Markdown 文件存储实现。

每个记忆项一个 .md 文件，使用 YAML frontmatter + Markdown 正文。
启动时扫描目录构建内存索引，写入时同步更新 .md 文件和 MEMORY.md 索引。

用法::

    memory = MdMemory(path="./memory")
    memory.write("pref-001", "用户偏好 Python", "semantic")
    results = memory.search("Python", "semantic")
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.interfaces.types import MemoryItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 简单 frontmatter 解析器（零外部依赖）
# ---------------------------------------------------------------------------

def _parse_yaml_value(raw: str) -> Any:
    """将 YAML 标量字符串转换为对应的 Python 类型。

    支持：null, bool, int, float, string。
    不支持：list, dict（嵌套结构由调用方处理）。

    >>> _parse_yaml_value("true")
    True
    >>> _parse_yaml_value("123")
    123
    >>> _parse_yaml_value("3.14")
    3.14
    >>> _parse_yaml_value("hello")
    'hello'
    """
    if not raw:
        return ""
    stripped = raw.strip()
    if stripped in ("null", "~", ""):
        return None
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    # 整数
    try:
        return int(stripped)
    except ValueError:
        pass
    # 浮点数
    try:
        return float(stripped)
    except ValueError:
        pass
    # 去掉引号（双引号或单引号）
    if len(stripped) >= 2:
        if (stripped.startswith('"') and stripped.endswith('"')) or \
           (stripped.startswith("'") and stripped.endswith("'")):
            return stripped[1:-1]
    return stripped


def parse_frontmatter(text: str) -> tuple:
    """解析 YAML frontmatter + Markdown 正文。

    仅支持简单格式：
    - key: value（字符串、数字、布尔值）
    - metadata: 下的嵌套 key: value 对
    - 不支持列表、多层嵌套、锚点等高级 YAML 特性

    返回 (frontmatter_dict, body_text)。
    无 frontmatter 或格式错误时返回 ({}, text)。

    模块级函数（非实例方法），方便独立测试。
    """
    if not text:
        return ({}, "")

    # 检查是否以 --- 开头
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ({}, text)

    # 查找闭合的 ---
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        logger.warning("Unclosed frontmatter delimiter '---', treating as no frontmatter")
        return ({}, text)

    # 提取 frontmatter 行和 body
    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")

    # 解析 frontmatter 内容
    fm: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    in_metadata = False
    current_key: Optional[str] = None
    current_value_lines: List[str] = []

    def _flush_scalar() -> None:
        """将暂存的 key/value 写入 fm 或 metadata。"""
        nonlocal current_key, current_value_lines
        if current_key is None:
            return
        value_str = "\n".join(current_value_lines).strip()
        parsed = _parse_yaml_value(value_str)
        if in_metadata:
            metadata[current_key] = parsed
        else:
            fm[current_key] = parsed
        current_key = None
        current_value_lines = []

    for line in fm_lines:
        # 空行 → 刷新暂存
        if not line.strip():
            _flush_scalar()
            continue

        # 检查是否为缩进续行
        if line.startswith(" ") or line.startswith("\t"):
            stripped = line.strip()
            # 如果在 metadata 块中且包含 ":"，则作为 metadata 的键值对
            if in_metadata and ":" in stripped:
                mk, _, mv = stripped.partition(":")
                metadata[mk.strip()] = _parse_yaml_value(mv.strip())
                continue
            # 否则作为当前 key 的多行值续行
            if current_key is not None:
                current_value_lines.append(stripped)
            continue

        # 检查是否为 key: value 行
        if ":" in line:
            _flush_scalar()

            key_part, _, value_part = line.partition(":")
            key_part = key_part.strip()
            value_part = value_part.strip()

            if key_part == "metadata":
                in_metadata = True
                continue

            if in_metadata and key_part:
                # metadata 下的直接键值对（无缩进情况）
                metadata[key_part] = _parse_yaml_value(value_part)
                continue

            # 顶层键值对
            in_metadata = False
            current_key = key_part
            if value_part:
                # 同行有值
                fm[current_key] = _parse_yaml_value(value_part)
                current_key = None
            else:
                # value 在后续行
                current_value_lines = []

    _flush_scalar()

    if metadata:
        fm["metadata"] = metadata

    return (fm, body)


def serialize_frontmatter(fm: dict) -> str:
    """将 frontmatter 字典序列化为 YAML frontmatter 字符串。

    格式：以 --- 开头和结尾，包含 key: value 行和 metadata 子块。
    """
    lines = ["---"]

    for key, value in fm.items():
        if key == "metadata" and isinstance(value, dict):
            lines.append("metadata:")
            for mk, mv in value.items():
                lines.append(f"  {mk}: {_serialize_scalar(mv)}")
        else:
            lines.append(f"{key}: {_serialize_scalar(value)}")

    lines.append("---")
    return "\n".join(lines) + "\n"


def _serialize_scalar(value: Any) -> str:
    """将 Python 标量值序列化为 YAML 字符串表示。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 确保浮点数包含小数点
        s = str(value)
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if isinstance(value, int):
        return str(value)
    return str(value)


# ============================================================================
# MdMemory 类
# ============================================================================


class MdMemory:
    """MemoryBackend 的 Markdown 文件存储实现。

    每个记忆项一个 .md 文件，使用 YAML frontmatter + Markdown 正文。
    启动时扫描目录构建内存索引，写入时同时更新 .md 文件和 MEMORY.md 索引。
    """

    # MEMORY.md 索引文件名
    MEMORY_INDEX_FILENAME = "MEMORY.md"

    def __init__(self, path: str = "~/.harness/memory"):
        """初始化 MdMemory。

        Args:
            path: 记忆库根目录路径。默认为 ~/.harness/memory。
                  目录不存在时自动创建。
                  支持 ~ 展开为用户主目录。
        """
        # 1. 展开 ~ → 用户主目录
        self._root: Path = Path(path).expanduser().resolve()

        # 2. 确保根目录存在
        self._root.mkdir(parents=True, exist_ok=True)

        # 3. 内存索引：namespace → {key → MemoryItem}
        self._index: Dict[str, Dict[str, MemoryItem]] = {}

        # 4. 扫描已有文件，构建内存索引
        self._build_index()

    # ------------------------------------------------------------------
    # 公开接口方法（满足 MemoryBackend Protocol）
    # ------------------------------------------------------------------

    def read(self, key: str, namespace: str) -> Optional[Any]:
        """按 key 读取记忆值。

        从内存索引中查找（O(1)），不读磁盘。

        Args:
            key: 记忆键。
            namespace: 命名空间。

        Returns:
            Optional[Any]: 记忆值（Markdown 正文），不存在时为 None。
        """
        ns = self._index.get(namespace)
        if ns is None:
            return None
        item = ns.get(key)
        if item is None:
            return None
        return item.value

    def write(self, key: str, value: Any, namespace: str) -> None:
        """写入记忆值。

        1. 将 value 转为字符串
        2. 生成 frontmatter + body 写入 .md 文件
        3. 更新内存索引
        4. 追加到 MEMORY.md 索引文件

        Args:
            key: 记忆键。
            value: 记忆值（任意类型，会被 str() 转换后写入）。
            namespace: 命名空间。
        """
        # 1. 将 value 转为字符串
        value_str = str(value) if not isinstance(value, str) else value

        # 2. 生成时间戳
        timestamp = time.time()

        # 3. 构建 MemoryItem（用于索引）
        item = MemoryItem(
            key=key,
            value=value_str,
            namespace=namespace,
            timestamp=timestamp,
            metadata={},
        )

        # 4. 写入 .md 文件（传入 timestamp 确保与内存索引一致）
        self._write_md_file(key, value_str, namespace, {}, timestamp)

        # 5. 更新内存索引
        if namespace not in self._index:
            self._index[namespace] = {}
        self._index[namespace][key] = item

        # 6. 更新 MEMORY.md 索引文件
        self._update_memory_index_md(key, value_str, namespace)

    def search(
        self, query: str, namespace: str, limit: int = 10
    ) -> List[MemoryItem]:
        """搜索相关记忆。

        对指定 namespace 的所有记忆做子串匹配：
        - 匹配范围：key + value（Markdown 正文）
        - 大小写不敏感
        - 按时间戳降序排序（最新的在前）
        - 截断到 limit 条

        Args:
            query: 搜索查询（子串）。
            namespace: 命名空间。
            limit: 最大返回条数，默认 10。

        Returns:
            List[MemoryItem]: 匹配的记忆项列表。
        """
        # 空查询返回空列表
        if not query:
            return []

        # limit=0 返回空列表
        if limit == 0:
            return []

        if namespace not in self._index:
            return []

        query_lower = query.lower()
        results: List[MemoryItem] = []

        for item in self._index[namespace].values():
            # 匹配 key
            if query_lower in item.key.lower():
                results.append(item)
                continue
            # 匹配 value
            value_str = str(item.value) if item.value else ""
            if query_lower in value_str.lower():
                results.append(item)

        # 按时间戳降序排序
        results.sort(key=lambda x: x.timestamp, reverse=True)

        # limit < 0 → 无限制
        if limit < 0:
            return results

        return results[:limit]

    def list_namespaces(self) -> List[str]:
        """列出所有已知命名空间。

        从内存索引的 key 集合返回。

        Returns:
            List[str]: 命名空间名称列表。
        """
        return list(self._index.keys())

    # ------------------------------------------------------------------
    # 内部方法 — 文件 I/O
    # ------------------------------------------------------------------

    def _namespace_dir(self, namespace: str) -> Path:
        """确保 namespace 子目录存在，返回其 Path。"""
        ns_dir = self._root / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir

    def _filepath_for(self, key: str, namespace: str) -> Path:
        """返回 {namespace}/{key}.md 的完整路径。"""
        # 使用安全的文件名：将 key 中的路径分隔符替换掉
        safe_key = str(key).replace("/", "-").replace("\\", "-")
        return self._namespace_dir(namespace) / f"{safe_key}.md"

    def _write_md_file(
        self,
        key: str,
        value_str: str,
        namespace: str,
        metadata_dict: Dict[str, Any],
        timestamp: float,
    ) -> None:
        """生成 frontmatter + body 写入 .md 文件。

        Args:
            key: 记忆键。
            value_str: 记忆值（已转为字符串）。
            namespace: 命名空间。
            metadata_dict: 扩展元数据字典。
            timestamp: Unix 时间戳（由调用方 write() 生成，保证与内存索引一致）。
        """
        filepath = self._filepath_for(key, namespace)

        fm = {
            "key": key,
            "namespace": namespace,
            "timestamp": timestamp,
            **({"metadata": metadata_dict} if metadata_dict else {}),
        }

        frontmatter_str = serialize_frontmatter(fm)
        content = frontmatter_str + value_str

        # 原子写入：先写临时文件，再重命名
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, filepath)
        except Exception:
            # 清理临时文件
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _read_md_file(self, filepath: Path) -> Optional[MemoryItem]:
        """读取 .md 文件，解析 frontmatter，返回 MemoryItem。

        解析失败时返回 None 并记录 WARNING。

        Args:
            filepath: .md 文件的完整路径。

        Returns:
            解析成功时返回 MemoryItem，否则返回 None。
        """
        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read file %s: %s", filepath, e)
            return None

        try:
            fm_dict, body = parse_frontmatter(text)
        except Exception as e:
            logger.warning(
                "Failed to parse frontmatter in %s: %s", filepath, e
            )
            return None

        # 无 frontmatter 的文件：跳过并记录 WARNING
        if not fm_dict:
            logger.warning(
                "No frontmatter found in %s, skipping.", filepath
            )
            return None

        # 从 frontmatter 提取字段
        key = fm_dict.get("key", filepath.stem)
        namespace = fm_dict.get("namespace", "")
        timestamp = fm_dict.get("timestamp", 0.0)
        metadata = fm_dict.get("metadata", {})

        # 确保类型正确
        if not isinstance(timestamp, (int, float)):
            timestamp = 0.0

        return MemoryItem(
            key=str(key),
            value=body,
            namespace=str(namespace),
            timestamp=float(timestamp),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    # ------------------------------------------------------------------
    # 内部方法 — 索引管理
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """启动时扫描根目录，构建内存索引。

        遍历所有 namespace 子目录中的 .md 文件（跳过 MEMORY.md），
        解析 frontmatter 并构建 self._index。

        不符合期望的目录结构（例如直接在根目录下的 .md 文件）会被忽略。
        """
        self._index.clear()

        if not self._root.exists():
            return

        total_memories = 0

        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue

            namespace = entry.name
            memories: Dict[str, MemoryItem] = {}

            for file_entry in sorted(entry.iterdir()):
                if not file_entry.is_file():
                    continue
                if file_entry.name == self.MEMORY_INDEX_FILENAME:
                    continue
                if not file_entry.suffix == ".md":
                    continue

                item = self._read_md_file(file_entry)
                if item is not None:
                    # 使用 frontmatter 中的 namespace（优先于目录名）
                    actual_ns = item.namespace or namespace
                    if actual_ns != namespace:
                        # frontmatter namespace 与目录名不一致，使用 frontmatter 的
                        if actual_ns not in self._index:
                            self._index[actual_ns] = {}
                        self._index[actual_ns][item.key] = item
                    else:
                        memories[item.key] = item
                    total_memories += 1

            if memories:
                self._index[namespace] = memories

        logger.info(
            "Loaded %d memories across %d namespaces from %s",
            total_memories,
            len(self._index),
            self._root,
        )

    def _update_memory_index_md(
        self, key: str, value_str: str, namespace: str
    ) -> None:
        """更新 {namespace}/MEMORY.md 索引文件。

        追加或更新一行到索引文件。
        格式：- [key](key.md) — value第一行（截断到100字符）

        Args:
            key: 记忆键。
            value_str: 记忆值（已转为字符串）。
            namespace: 命名空间。
        """
        ns_dir = self._namespace_dir(namespace)
        index_path = ns_dir / self.MEMORY_INDEX_FILENAME

        # 使用与 _filepath_for 相同的 safe_key 逻辑
        safe_key = str(key).replace("/", "-").replace("\\", "-")

        # 准备新条目行
        entry_line = self._format_index_entry(safe_key, value_str)

        if index_path.exists():
            try:
                content = index_path.read_text(encoding="utf-8")
            except Exception:
                content = ""
        else:
            # 新建索引文件
            title = f"# {namespace} — 记忆索引\n\n"
            index_path.write_text(title + entry_line + "\n", encoding="utf-8")
            return

        # 检查 key 是否已存在于索引中
        # 匹配行格式: - [key](key.md) — ...
        pattern = rf"^- \[{re.escape(safe_key)}\]\({re.escape(safe_key)}\.md\)"
        lines = content.split("\n")
        updated = False

        for i, line in enumerate(lines):
            if re.match(pattern, line):
                lines[i] = entry_line
                updated = True
                break

        if not updated:
            lines.append(entry_line)

        index_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _format_index_entry(key: str, value_str: str) -> str:
        """格式化 MEMORY.md 中的一行索引条目。

        格式：- [key](key.md) — value第一行（截断到100字符）
        """
        # 取 value 的第一行
        first_line = value_str.split("\n")[0].strip()
        # 截断到 100 字符
        if len(first_line) > 100:
            first_line = first_line[:97] + "..."
        return f"- [{key}]({key}.md) — {first_line}"
