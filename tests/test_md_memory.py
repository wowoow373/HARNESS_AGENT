"""Test harness for MdMemory — batch-03 memory backend implementation.

Covers all acceptance criteria defined in sdd/batches/batch-03-memory-backend/acceptance.md.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from harness.components.memory_backend.md_memory import (
    MdMemory,
    _parse_yaml_value,
    parse_frontmatter,
    serialize_frontmatter,
)
from harness.core.container import DIContainer
from harness.interfaces.memory_backend import MemoryBackend
from harness.interfaces.types import MemoryItem


# ============================================================================
# Frontmatter 解析器独立单元测试
# ============================================================================


class TestParseYamlValue:
    """_parse_yaml_value() 类型转换测试。"""

    def test_null_values(self):
        assert _parse_yaml_value("null") is None
        assert _parse_yaml_value("~") is None
        # Empty string stays as empty string (not None — that's YAML null)
        assert _parse_yaml_value("") == ""

    def test_boolean_values(self):
        assert _parse_yaml_value("true") is True
        assert _parse_yaml_value("false") is False

    def test_integer_values(self):
        assert _parse_yaml_value("0") == 0
        assert _parse_yaml_value("123") == 123
        assert _parse_yaml_value("-456") == -456

    def test_float_values(self):
        assert _parse_yaml_value("3.14") == 3.14
        assert _parse_yaml_value("0.0") == 0.0
        assert _parse_yaml_value("-1.5") == -1.5

    def test_string_values(self):
        assert _parse_yaml_value("hello") == "hello"
        assert _parse_yaml_value("session-20260603-001") == "session-20260603-001"

    def test_quoted_strings(self):
        assert _parse_yaml_value('"hello"') == "hello"
        assert _parse_yaml_value("'world'") == "world"


class TestParseFrontmatter:
    """parse_frontmatter() 解析测试。"""

    def test_normal_frontmatter(self):
        text = """---
key: my-key
namespace: episodic
timestamp: 1717430400.0
metadata:
  session_id: abc123
  token_count: 15000
---

# Body text

Some content here.
"""
        fm, body = parse_frontmatter(text)
        assert fm["key"] == "my-key"
        assert fm["namespace"] == "episodic"
        assert fm["timestamp"] == 1717430400.0
        assert fm["metadata"] == {"session_id": "abc123", "token_count": 15000}
        assert "# Body text" in body
        assert "Some content here" in body

    def test_no_frontmatter(self):
        text = "# Just a markdown file\n\nNo frontmatter here."
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_unclosed_frontmatter(self):
        text = """---
key: value
No closing delimiter."""
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_text(self):
        fm, body = parse_frontmatter("")
        assert fm == {}
        assert body == ""

    def test_only_frontmatter(self):
        text = """---
key: minimal
---

"""
        fm, body = parse_frontmatter(text)
        assert fm["key"] == "minimal"
        assert body == ""

    def test_frontmatter_with_list_value(self):
        """Multi-line values should be concatenated."""
        text = """---
key: my-key
description:
  First line of description
  Second line of description
---

Body.
"""
        fm, body = parse_frontmatter(text)
        assert fm["key"] == "my-key"
        # Multi-line: lines joined by newline
        assert "First line" in fm["description"]
        assert "Second line" in fm["description"]

    def test_frontmatter_metadata_only(self):
        text = """---
metadata:
  foo: bar
  num: 42
---

Body.
"""
        fm, body = parse_frontmatter(text)
        assert fm["metadata"] == {"foo": "bar", "num": 42}


class TestSerializeFrontmatter:
    """serialize_frontmatter() 序列化测试。"""

    def test_basic_serialize(self):
        fm = {"key": "my-key", "namespace": "episodic", "timestamp": 1717430400.0}
        result = serialize_frontmatter(fm)
        assert result.startswith("---\n")
        assert "key: my-key\n" in result
        assert "namespace: episodic\n" in result
        assert "timestamp: 1717430400.0\n" in result
        assert result.rstrip().endswith("---")

    def test_serialize_with_metadata(self):
        fm = {
            "key": "k",
            "namespace": "ns",
            "timestamp": 1.0,
            "metadata": {"session_id": "abc", "count": 10},
        }
        result = serialize_frontmatter(fm)
        assert "metadata:\n" in result
        assert "  session_id: abc\n" in result
        assert "  count: 10\n" in result

    def test_roundtrip(self):
        """serialize → parse 应该保持数据一致。"""
        fm = {"key": "k", "namespace": "ns", "timestamp": 1.0}
        serialized = serialize_frontmatter(fm)
        body = "test body"
        full_text = serialized + body
        parsed_fm, parsed_body = parse_frontmatter(full_text)
        assert parsed_fm["key"] == "k"
        assert parsed_fm["namespace"] == "ns"
        assert parsed_body == body


# ============================================================================
# MdMemory 功能验收测试
# ============================================================================


class TestMdMemoryInit:
    """AC-MEM-06: 目录管理。"""

    def test_init_creates_directory(self, tmp_path):
        """AC-MEM-06.1: 传入不存在的路径，__init__ 自动创建目录。"""
        new_dir = tmp_path / "new_memory_dir"
        assert not new_dir.exists()
        m = MdMemory(path=str(new_dir))
        assert new_dir.exists()
        assert m.list_namespaces() == []

    def test_init_loads_existing(self, tmp_path):
        """AC-MEM-06.2: 传入已存在且有旧记忆的目录，正确加载。"""
        # 先创建一个实例并写入数据
        m1 = MdMemory(path=str(tmp_path))
        m1.write("k1", "v1", "ns1")
        m1.write("k2", "v2", "ns2")

        # 新实例加载同一目录
        m2 = MdMemory(path=str(tmp_path))
        assert m2.read("k1", "ns1") == "v1"
        assert m2.read("k2", "ns2") == "v2"
        assert "ns1" in m2.list_namespaces()
        assert "ns2" in m2.list_namespaces()

    def test_init_empty_directory(self, tmp_path):
        """AC-MEM-06.3: 传入空目录，启动正常。"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        m = MdMemory(path=str(empty_dir))
        assert m.list_namespaces() == []

    def test_init_expands_tilde(self, tmp_path, monkeypatch):
        """AC-MEM-06.4: 路径中包含 ~，正确展开。"""
        # 模拟 HOME 目录为 tmp_path
        monkeypatch.setenv("HOME", str(tmp_path))
        m = MdMemory(path="~/test-memory-expand")
        assert str(tmp_path) in str(m._root)
        assert m._root.exists()


class TestMdMemoryRead:
    """AC-MEM-01: read() — 按 key 读取记忆。"""

    def test_read_existing_key(self, tmp_path):
        """AC-MEM-01.1: 读取已存在的 key，返回正确的 value。"""
        m = MdMemory(path=str(tmp_path))
        m.write("my-key", "my value", "ns")
        assert m.read("my-key", "ns") == "my value"

    def test_read_nonexistent_key(self, tmp_path):
        """AC-MEM-01.2: 读取不存在的 key，返回 None。"""
        m = MdMemory(path=str(tmp_path))
        m.write("exists", "value", "ns")
        assert m.read("nope", "ns") is None

    def test_read_nonexistent_namespace(self, tmp_path):
        """AC-MEM-01.3: 读取不存在的 namespace，返回 None。"""
        m = MdMemory(path=str(tmp_path))
        assert m.read("key", "nonexistent") is None

    def test_read_specific_key(self, tmp_path):
        """AC-MEM-01.4: 同一 namespace 下多个 key，read 仅返回指定的。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k1", "v1", "ns")
        m.write("k2", "v2", "ns")
        m.write("k3", "v3", "ns")
        assert m.read("k2", "ns") == "v2"
        assert m.read("k1", "ns") == "v1"


class TestMdMemoryWrite:
    """AC-MEM-02: write() — 写入记忆。"""

    def test_write_creates_md_file(self, tmp_path):
        """AC-MEM-02.1: write 后对应的 .md 文件被创建在正确的子目录下。"""
        m = MdMemory(path=str(tmp_path))
        m.write("test-key", "test value", "episodic")
        expected_file = tmp_path / "episodic" / "test-key.md"
        assert expected_file.exists()

    def test_write_frontmatter_content(self, tmp_path):
        """AC-MEM-02.2: 写入的 .md 文件包含正确的 YAML frontmatter。"""
        m = MdMemory(path=str(tmp_path))
        m.write("test-key", "test value", "episodic")
        filepath = tmp_path / "episodic" / "test-key.md"
        content = filepath.read_text(encoding="utf-8")
        assert "key: test-key" in content
        assert "namespace: episodic" in content
        assert "timestamp: " in content

    def test_write_frontmatter_timestamp_is_float(self, tmp_path):
        """AC-MEM-02.2a: timestamp 是有效的 Unix epoch 浮点数。"""
        m = MdMemory(path=str(tmp_path))
        before = time.time()
        m.write("t1", "v1", "ns")
        after = time.time()
        filepath = tmp_path / "ns" / "t1.md"
        content = filepath.read_text(encoding="utf-8")
        # 从文件中提取 timestamp
        for line in content.split("\n"):
            if line.startswith("timestamp:"):
                ts_str = line.split(":", 1)[1].strip()
                ts = float(ts_str)
                assert before - 1 <= ts <= after + 1  # 允许 1 秒误差
                break
        else:
            pytest.fail("timestamp not found in frontmatter")

    def test_write_body_content(self, tmp_path):
        """AC-MEM-02.3: .md 文件正文包含 value 的字符串表示。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "hello world\nsecond line", "ns")
        filepath = tmp_path / "ns" / "k.md"
        content = filepath.read_text(encoding="utf-8")
        assert "hello world" in content
        assert "second line" in content

    def test_write_overwrite(self, tmp_path):
        """AC-MEM-02.4: 同一 key 重复 write，后一次覆盖前一次。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "first", "ns")
        m.write("k", "second", "ns")
        assert m.read("k", "ns") == "second"
        # 检查文件也被更新
        filepath = tmp_path / "ns" / "k.md"
        content = filepath.read_text(encoding="utf-8")
        assert "second" in content

    def test_write_then_read(self, tmp_path):
        """AC-MEM-02.5: write() 后立即 read() 能读到刚写入的值。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "instant", "ns")
        assert m.read("k", "ns") == "instant"

    def test_write_non_string_value(self, tmp_path):
        """AC-MEM-02.6: 写入非字符串 value，str() 转换后正确写入。"""
        m = MdMemory(path=str(tmp_path))
        m.write("int", 42, "ns")
        assert m.read("int", "ns") == "42"
        m.write("list", [1, 2, 3], "ns")
        assert m.read("list", "ns") == "[1, 2, 3]"
        m.write("dict", {"a": 1}, "ns")
        assert m.read("dict", "ns") == "{'a': 1}"

    def test_write_empty_string(self, tmp_path):
        """AC-MEM-02.7: 写入空字符串，read 返回空字符串。"""
        m = MdMemory(path=str(tmp_path))
        m.write("empty", "", "ns")
        assert m.read("empty", "ns") == ""

    def test_write_special_characters(self, tmp_path):
        """AC-MEM-02.8: 写入含特殊字符的 value，正确保存和读取。"""
        m = MdMemory(path=str(tmp_path))
        special = "中文\nemoji: 🎉\n--- frontmatter separator\n\t tab\n\\ backslash"
        m.write("special", special, "ns")
        result = m.read("special", "ns")
        assert "中文" in result
        assert "🎉" in result
        assert "---" in result
        assert "\t" in result


class TestMdMemorySearch:
    """AC-MEM-03: search() — 搜索记忆。"""

    @pytest.fixture
    def populated_memory(self, tmp_path):
        """创建一个预填充了测试数据的 MdMemory 实例。"""
        m = MdMemory(path=str(tmp_path))
        # 按时间顺序写入，方便测试排序
        m.write("python-guide", "Python async/await 使用指南", "episodic")
        time.sleep(0.01)
        m.write("git-pattern", "Git workflow 最佳实践", "episodic")
        time.sleep(0.01)
        m.write("user-pref", "用户偏好 Python 和 TypeScript", "episodic")
        time.sleep(0.01)
        m.write("db-config", "postgres 数据库连接池配置", "procedural")
        return m

    def test_search_finds_by_key(self, populated_memory):
        """AC-MEM-03.1: search 通过 key 匹配。"""
        results = populated_memory.search("python", "episodic")
        # 应该匹配 "python-guide" (key) 和 "用户偏好 Python..." (value)
        keys = {r.key for r in results}
        assert "python-guide" in keys
        assert "user-pref" in keys

    def test_search_finds_by_value(self, populated_memory):
        """AC-MEM-03.1: search 通过 value 匹配。"""
        results = populated_memory.search("git", "episodic")
        assert len(results) == 1
        assert results[0].key == "git-pattern"

    def test_search_case_insensitive(self, populated_memory):
        """AC-MEM-03.1: search 大小写不敏感。"""
        results_lower = populated_memory.search("python", "episodic")
        results_upper = populated_memory.search("PYTHON", "episodic")
        results_mixed = populated_memory.search("Python", "episodic")
        assert len(results_lower) == len(results_upper) == len(results_mixed)

    def test_search_matches_key_or_value(self, populated_memory):
        """AC-MEM-03.2: 匹配范围包括 key 和 value（任一匹配即返回）。"""
        # "db-config" 的 key 不包含 "postgres" 但 value 包含
        results = populated_memory.search("postgres", "procedural")
        assert len(results) == 1
        assert results[0].key == "db-config"

    def test_search_sorted_by_timestamp(self, populated_memory):
        """AC-MEM-03.3: 返回结果按时间戳降序排序（最新在前）。"""
        results = populated_memory.search("python", "episodic")
        # user-pref 是最后写入的（包含 Python），应该在前面
        assert results[0].key == "user-pref"

    def test_search_respects_limit(self, populated_memory):
        """AC-MEM-03.4: 返回结果数量不超过 limit 参数。"""
        # 写入更多匹配数据
        for i in range(10):
            populated_memory.write(f"test-{i}", "match word python", "episodic")
        results = populated_memory.search("python", "episodic", limit=3)
        assert len(results) <= 3

    def test_search_no_match(self, populated_memory):
        """AC-MEM-03.5: query 匹配不到时返回空列表。"""
        results = populated_memory.search("xyznotfound", "episodic")
        assert results == []

    def test_search_nonexistent_namespace(self, populated_memory):
        """AC-MEM-03.6: 不存在的 namespace 返回空列表。"""
        results = populated_memory.search("python", "nonexistent")
        assert results == []

    def test_search_empty_query(self, populated_memory):
        """AC-MEM-03.7: 空字符串 query 返回空列表。"""
        results = populated_memory.search("", "episodic")
        assert results == []

    def test_search_limit_zero(self, populated_memory):
        """AC-MEM-03.8: limit=0 返回空列表。"""
        results = populated_memory.search("python", "episodic", limit=0)
        assert results == []

    def test_search_limit_negative(self, populated_memory):
        """AC-MEM-03.9: limit<0 返回全部匹配结果。"""
        for i in range(20):
            populated_memory.write(f"bulk-{i}", "bulk data", "episodic")
        results = populated_memory.search("bulk", "episodic", limit=-1)
        assert len(results) == 20  # 全部 20 条

    def test_search_does_not_read_disk(self, tmp_path):
        """AC-MEM-03.10: search 过程中不读磁盘（仅查询内存索引）。

        验证方式：创建索引后删除底层 .md 文件，确认 search 仍能返回结果。
        """
        m = MdMemory(path=str(tmp_path))
        m.write("k1", "hello world", "ns")
        m.write("k2", "python guide", "ns")

        # 删除磁盘上的 .md 文件（但不影响内存索引）
        ns_dir = tmp_path / "ns"
        for md_file in ns_dir.glob("*.md"):
            md_file.unlink()

        # search 仍应正常工作（从内存索引查询，不读磁盘）
        results = m.search("hello", "ns")
        assert len(results) == 1
        assert results[0].key == "k1"

        results2 = m.search("python", "ns")
        assert len(results2) == 1
        assert results2[0].key == "k2"


class TestMdMemoryListNamespaces:
    """AC-MEM-04: list_namespaces() — 列出命名空间。"""

    def test_list_namespaces_with_data(self, tmp_path):
        """AC-MEM-04.1: 写入后 list_namespaces 包含对应 namespace。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "v", "episodic")
        assert "episodic" in m.list_namespaces()

    def test_list_namespaces_empty(self, tmp_path):
        """AC-MEM-04.2: 没有任何记忆时返回空列表。"""
        m = MdMemory(path=str(tmp_path))
        assert m.list_namespaces() == []

    def test_list_namespaces_dedup(self, tmp_path):
        """AC-MEM-04.3: 多次写入同一 namespace 后返回去重列表。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k1", "v1", "episodic")
        m.write("k2", "v2", "episodic")
        m.write("k3", "v3", "semantic")
        nss = m.list_namespaces()
        assert len(nss) == 2
        assert sorted(nss) == ["episodic", "semantic"]


# ============================================================================
# 持久化验收测试
# ============================================================================


class TestPersistence:
    """AC-MEM-05: 跨实例持久化。"""

    def test_persistence_read(self, tmp_path):
        """AC-MEM-05.1/05.2: 新实例能读取旧实例写入的记忆。"""
        m1 = MdMemory(path=str(tmp_path))
        m1.write("persist-key", "persist value", "ns")
        del m1

        m2 = MdMemory(path=str(tmp_path))
        assert m2.read("persist-key", "ns") == "persist value"

    def test_persistence_search(self, tmp_path):
        """AC-MEM-05.3: 新实例的 search 能找到旧实例写入的记忆。"""
        m1 = MdMemory(path=str(tmp_path))
        m1.write("pk", "unique search term", "ns")
        del m1

        m2 = MdMemory(path=str(tmp_path))
        results = m2.search("unique", "ns")
        assert len(results) == 1
        assert results[0].key == "pk"

    def test_persistence_list_namespaces(self, tmp_path):
        """AC-MEM-05.4: 新实例 list_namespaces 包含旧实例使用的 namespace。"""
        m1 = MdMemory(path=str(tmp_path))
        m1.write("k", "v", "legacy-ns")
        del m1

        m2 = MdMemory(path=str(tmp_path))
        assert "legacy-ns" in m2.list_namespaces()


# ============================================================================
# 边界条件验收测试
# ============================================================================


class TestEdgeCases:
    """AC-MEM-07: 容错。"""

    def test_ignores_non_md_files(self, tmp_path):
        """AC-MEM-07.1: 目录中存在非 .md 文件，启动时忽略不报错。"""
        ns_dir = tmp_path / "episodic"
        ns_dir.mkdir(parents=True)
        # 创建有效 .md 文件
        (ns_dir / "good.md").write_text(
            "---\nkey: good\nnamespace: episodic\ntimestamp: 1.0\n---\nvalue",
            encoding="utf-8",
        )
        # 创建非 .md 文件
        (ns_dir / ".DS_Store").write_text("ignored")
        (ns_dir / "README.txt").write_text("ignored")
        (ns_dir / "notes").write_text("ignored")

        m = MdMemory(path=str(tmp_path))
        assert m.read("good", "episodic") == "value"

    def test_skips_malformed_frontmatter(self, tmp_path, caplog):
        """AC-MEM-07.2: frontmatter 格式错误，记录 WARNING 并跳过。"""
        import logging

        caplog.set_level(logging.WARNING)
        ns_dir = tmp_path / "episodic"
        ns_dir.mkdir(parents=True)
        (ns_dir / "good.md").write_text(
            "---\nkey: good\nnamespace: episodic\ntimestamp: 1.0\n---\nvalue",
            encoding="utf-8",
        )
        # Malformed: no closing ---
        (ns_dir / "bad.md").write_text(
            "---\nkey: bad\nbroken yaml here\nno closing",
            encoding="utf-8",
        )

        m = MdMemory(path=str(tmp_path))
        # 好的文件应该正确加载
        assert m.read("good", "episodic") == "value"
        # 坏的文件应该被跳过（不在索引中）
        assert m.read("bad", "episodic") is None
        # 验证 WARNING 日志被记录
        assert any("bad.md" in record.message for record in caplog.records), \
            "Expected WARNING log for malformed frontmatter in bad.md"

    def test_skips_no_frontmatter(self, tmp_path, caplog):
        """AC-MEM-07.3: .md 文件没有 frontmatter，跳过并记录 WARNING。"""
        import logging

        caplog.set_level(logging.WARNING)
        ns_dir = tmp_path / "ns"
        ns_dir.mkdir(parents=True)
        (ns_dir / "no_fm.md").write_text(
            "# Just a regular markdown file\n\nNo frontmatter here.",
            encoding="utf-8",
        )
        (ns_dir / "with_fm.md").write_text(
            "---\nkey: has-fm\nnamespace: ns\ntimestamp: 1.0\n---\nvalue",
            encoding="utf-8",
        )

        m = MdMemory(path=str(tmp_path))
        assert m.read("has-fm", "ns") == "value"
        assert m.read("no_fm", "ns") is None  # 被跳过
        # 验证 WARNING 日志被记录
        assert any("no_fm.md" in record.message for record in caplog.records), \
            "Expected WARNING log for missing frontmatter in no_fm.md"

    def test_memory_index_md_deleted(self, tmp_path):
        """AC-MEM-07.4: MEMORY.md 被手动删除后，不影响正常读写。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k1", "v1", "ns")
        m.write("k2", "v2", "ns")

        # 删除 MEMORY.md
        index_path = tmp_path / "ns" / "MEMORY.md"
        if index_path.exists():
            index_path.unlink()

        # 读写仍然正常
        assert m.read("k1", "ns") == "v1"
        # 写入新数据（应该重建 MEMORY.md）
        m.write("k3", "v3", "ns")
        assert m.read("k3", "ns") == "v3"


# ============================================================================
# 接口符合性验收测试
# ============================================================================


class TestProtocolCompliance:
    """AC-MEM-09: Protocol 符合性。"""

    def test_protocol_compliance(self, tmp_path):
        """AC-MEM-09.1: MdMemory 可通过 isinstance 检查。"""
        m = MdMemory(path=str(tmp_path))
        assert isinstance(m, MemoryBackend)

    def test_di_registration(self, tmp_path):
        """AC-MEM-09.2: MdMemory 可成功注册到 DI 容器。"""
        m = MdMemory(path=str(tmp_path))
        container = DIContainer()
        container.register(MemoryBackend, m)
        resolved = container.resolve(MemoryBackend)
        assert resolved is m

    def test_orchestrator_phase1_search(self, tmp_path):
        """AC-MEM-09.3: 编排器使用 MdMemory 作为 MemoryBackend 时，
        Phase 1 的 search() 正常执行。

        验证方式：预填充 MdMemory → 注册到容器 → 运行编排器 →
        确认编排器无崩溃（Phase 1 的 memory.search() 正常执行）。
        """
        from harness.core.orchestrator import InputAdapter
        from harness.di import Harness
        from harness.interfaces.types import Response, UserRequest

        # 1. 预填充 MdMemory（模拟已有记忆）
        m = MdMemory(path=str(tmp_path))
        m.write("session-001", "用户讨论了 Python async/await 的使用", "episodic")
        m.write("user-pref", "用户偏好 Python 和 TypeScript", "semantic")

        # 2. 构建容器
        container = DIContainer()

        # InputAdapter：发送一条匹配 episodic 记忆的查询
        outputs = []
        receive_count = [0]

        class TestAdapter:
            def receive(self):
                receive_count[0] += 1
                if receive_count[0] == 1:
                    return UserRequest(text="我需要用 Python 写异步代码")
                return UserRequest(text="")  # 第二次调用触发退出

            def send(self, event):
                outputs.append(getattr(event, "content", str(event)))

        container.register(InputAdapter, TestAdapter())
        container.register(MemoryBackend, m)

        # Mock LLM
        def mock_llm(messages, tools=None):
            return Response(text="mock reply", stop_reason="end_turn")

        # 3. 运行编排器
        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

        # 4. 验证编排器正常完成（Phase 1 的 search() 没有抛异常）
        # TextEvent + StopEvent per turn
        text_outputs = [o for o in outputs if o == "mock reply"]
        assert len(text_outputs) == 1
        # MdMemory 中的 episodic 记忆仍然存在
        assert m.read("session-001", "episodic") == "用户讨论了 Python async/await 的使用"


class TestTypeCorrectness:
    """AC-MEM-10: 类型正确性。"""

    def test_search_returns_memory_items(self, tmp_path):
        """AC-MEM-10.1: search() 返回 MemoryItem 实例。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "v", "ns")
        results = m.search("v", "ns")
        assert len(results) == 1
        assert isinstance(results[0], MemoryItem)

    def test_memory_item_fields(self, tmp_path):
        """AC-MEM-10.2: MemoryItem 的 5 个字段全部填充正确。"""
        m = MdMemory(path=str(tmp_path))
        m.write("my-key", "my value", "my-ns")
        results = m.search("my-key", "my-ns")
        item = results[0]
        assert item.key == "my-key"
        assert item.value == "my value"
        assert item.namespace == "my-ns"
        assert isinstance(item.timestamp, float)
        assert item.timestamp > 0
        assert isinstance(item.metadata, dict)

    def test_read_return_type(self, tmp_path):
        """AC-MEM-10.3: read() 返回值类型为 Optional[Any]。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k", "v", "ns")
        assert isinstance(m.read("k", "ns"), str)
        assert m.read("noexist", "ns") is None

    def test_list_namespaces_return_type(self, tmp_path):
        """AC-MEM-10.4: list_namespaces() 返回值为 List[str]。"""
        m = MdMemory(path=str(tmp_path))
        result = m.list_namespaces()
        assert isinstance(result, list)
        m.write("k", "v", "ns")
        result = m.list_namespaces()
        assert isinstance(result, list)
        assert all(isinstance(ns, str) for ns in result)


# ============================================================================
# 大规模数据性能测试
# ============================================================================


class TestPerformance:
    """AC-MEM-08: 大规模数据性能（非严格，作为回归检测）。"""

    def test_startup_with_1000_memories(self, tmp_path):
        """AC-MEM-08.1: 1000 条记忆的目录，启动扫描时间 < 1 秒。"""
        # 预创建 1000 个 .md 文件
        ns_dir = tmp_path / "large_ns"
        ns_dir.mkdir(parents=True)
        for i in range(1000):
            filepath = ns_dir / f"mem-{i:04d}.md"
            filepath.write_text(
                f"---\nkey: mem-{i:04d}\nnamespace: large_ns\n"
                f"timestamp: {1000000.0 + i}\n---\nvalue {i}",
                encoding="utf-8",
            )

        start = time.perf_counter()
        m = MdMemory(path=str(tmp_path))
        elapsed = time.perf_counter() - start

        assert m.read("mem-0000", "large_ns") == "value 0"
        assert elapsed < 1.0, f"Startup took {elapsed:.3f}s, expected < 1.0s"

    def test_search_performance(self, tmp_path):
        """AC-MEM-08.2: 1000 条记忆中 search() 响应时间 < 10ms。"""
        m = MdMemory(path=str(tmp_path))
        for i in range(1000):
            m.write(f"mem-{i:04d}", f"value {i}", "perf_ns")

        start = time.perf_counter()
        results = m.search("value 500", "perf_ns")
        elapsed = time.perf_counter() - start

        assert len(results) == 1
        assert elapsed < 0.01, f"Search took {elapsed*1000:.1f}ms, expected < 10ms"

    def test_read_performance(self, tmp_path):
        """AC-MEM-08.3: read() 响应时间 < 1ms（从内存索引读取）。"""
        m = MdMemory(path=str(tmp_path))
        for i in range(1000):
            m.write(f"mem-{i:04d}", f"value {i}", "perf_ns")

        start = time.perf_counter()
        val = m.read("mem-0500", "perf_ns")
        elapsed = time.perf_counter() - start

        assert val == "value 500"
        assert elapsed < 0.001, f"Read took {elapsed*1000:.3f}ms, expected < 1ms"


# ============================================================================
# MEMORY.md 索引文件测试
# ============================================================================


class TestMemoryIndexMd:
    """MEMORY.md 索引文件管理测试。"""

    def test_memory_index_md_created(self, tmp_path):
        """write() 后 MEMORY.md 被创建。"""
        m = MdMemory(path=str(tmp_path))
        m.write("k1", "hello world", "ns")
        index_path = tmp_path / "ns" / "MEMORY.md"
        assert index_path.exists()

    def test_memory_index_md_format(self, tmp_path):
        """MEMORY.md 格式正确。"""
        m = MdMemory(path=str(tmp_path))
        m.write("test-key", "test value here", "ns")
        index_path = tmp_path / "ns" / "MEMORY.md"
        content = index_path.read_text(encoding="utf-8")
        assert "[test-key](test-key.md)" in content
        assert "test value here" in content

    def test_memory_index_md_no_duplicates(self, tmp_path):
        """重复 write 同一 key 不产生重复索引条目。"""
        m = MdMemory(path=str(tmp_path))
        m.write("dup-key", "first value", "ns")
        m.write("dup-key", "second value", "ns")
        index_path = tmp_path / "ns" / "MEMORY.md"
        content = index_path.read_text(encoding="utf-8")
        # 统计包含 dup-key 的行数
        count = content.count("[dup-key](dup-key.md)")
        assert count == 1, f"Expected 1 entry for dup-key, got {count}"

    def test_memory_index_md_truncates_long_value(self, tmp_path):
        """索引条目中 value 被截断到 100 字符。"""
        m = MdMemory(path=str(tmp_path))
        long_value = "A" * 200
        m.write("k", long_value, "ns")
        index_path = tmp_path / "ns" / "MEMORY.md"
        content = index_path.read_text(encoding="utf-8")
        # 找到包含 key 的行
        for line in content.split("\n"):
            if "[k](k.md)" in line:
                # 截断后应该有 "..."，且总长度有限
                assert len(line) < 200  # 合理上限
                assert "..." in line
                break
