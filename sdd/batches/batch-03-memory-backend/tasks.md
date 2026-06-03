# batch-03 — MemoryBackend 默认实现 任务清单

> 按顺序逐条执行，完成后勾选。

---

## 阶段 1：创建目录结构与模块骨架

### Task-3.1：创建 `harness/components/memory_backend/` 包

- [ ] 创建 `harness/components/` 目录
- [ ] 创建 `harness/components/__init__.py`（空文件）
- [ ] 创建 `harness/components/memory_backend/` 目录
- [ ] 创建 `harness/components/memory_backend/__init__.py`

**产出**：
```
harness/components/
├── __init__.py
└── memory_backend/
    └── __init__.py
```

**验证方式**：`python -c "import harness.components.memory_backend"` 无错误

---

### Task-3.2：创建 `md_memory.py` 模块骨架

- [ ] 创建 `harness/components/memory_backend/md_memory.py`
- [ ] 添加文件 docstring
- [ ] 添加 `MdMemory` 类定义和构造函数签名
- [ ] 在 `__init__.py` 中导出 `MdMemory`

**产出**：
```python
# harness/components/memory_backend/__init__.py
from .md_memory import MdMemory

__all__ = ["MdMemory"]
```

**验证方式**：`from harness.components.memory_backend import MdMemory` 无 ImportError

---

## 阶段 2：实现存储层（文件 I/O）

### Task-3.3：实现目录管理逻辑

在 `MdMemory.__init__` 中实现：

- [ ] 展开 `~` 为用户主目录（`Path.expanduser()`）
- [ ] 如果根目录不存在，自动创建（`os.makedirs`）
- [ ] 对于每个 namespace 子目录，确保存在或创建
- [ ] 将根目录绝对路径存储为 `self._root: Path`

**验证方式**：
- 传入不存在的路径，确认目录被创建
- 传入 `~/test-memory`，确认展开到正确的用户主目录

---

### Task-3.4：实现 frontmatter 解析器

在 `md_memory.py` 中实现私有方法：

- [ ] `_parse_frontmatter(text: str) -> tuple[dict, str]`
- [ ] 识别 `---` 分隔的 YAML frontmatter
- [ ] 解析 `key: value` 行（字符串、数字、布尔值）
- [ ] 处理多行字符串（缩进续行）
- [ ] 解析 `metadata:` 下的嵌套键值对
- [ ] 返回 `(frontmatter_dict, body_text)`

**支持的 frontmatter 格式**：
```yaml
---
key: my-key
namespace: episodic
timestamp: 1717430400.0
metadata:
  session_id: abc123
---
```

**边界条件**：
- 无 frontmatter 的文件：返回 `({}, full_text)`
- frontmatter 格式错误：记录 WARNING，返回 `({}, full_text)`

**验证方式**：单元测试覆盖正常解析、无 frontmatter、格式错误三种情况

---

### Task-3.5：实现 `.md` 文件的读写

- [ ] `_write_md_file(key, value_str, namespace, metadata_dict)` — 生成 frontmatter + body 写入文件
- [ ] `_read_md_file(filepath)` — 读取文件，解析 frontmatter，返回 `MemoryItem`
- [ ] `_filepath_for(key, namespace)` — 返回 `{namespace}/{key}.md` 的完整路径
- [ ] `_namespace_dir(namespace)` — 确保 namespace 子目录存在，返回其 Path

**验证方式**：
- 写入后读取，确认 MemoryItem 各字段正确
- 写入覆盖测试：两次写入同一 key，确认文件被更新

---

## 阶段 3：实现内存索引

### Task-3.6：实现启动时索引构建

- [ ] `_build_index()` — 扫描根目录下所有 namespace 子目录
- [ ] 遍历每个子目录中的 `.md` 文件（跳过 `MEMORY.md`）
- [ ] 对每个文件调用 `_read_md_file()` 解析
- [ ] 构建 `self._index: Dict[str, Dict[str, MemoryItem]]`
- [ ] 记录加载统计日志（"Loaded X memories across Y namespaces"）

**索引结构**：
```python
_index = {
    "episodic": {
        "session-001": MemoryItem(key="session-001", ...),
        "session-002": MemoryItem(key="session-002", ...),
    },
    "semantic": {...},
}
```

**验证方式**：
- 创建含多个 `.md` 文件的目录，启动后确认索引条目数量正确
- 含非 `.md` 文件的目录，确认不报错

---

### Task-3.7：实现索引增量更新

- [ ] `write()` 调用后，同步更新 `self._index[namespace][key]`
- [ ] 如果 namespace 在 `_index` 中不存在，自动创建条目

**验证方式**：
- `write()` 后立即 `read()`，确认返回刚写入的值（不重新扫描磁盘）

---

## 阶段 4：实现 MemoryBackend 接口方法

### Task-4.1：实现 `read()`

- [ ] 从 `self._index` 查找 `namespace → key`
- [ ] 找到时返回 `MemoryItem.value`
- [ ] 未找到时返回 `None`

**验证方式**：参照 AC-MEM-01 全部 4 项

---

### Task-4.2：实现 `write()`

- [ ] 将 value 转为字符串（如果不是 str）
- [ ] 生成时间戳（`time.time()`）
- [ ] 调用 `_write_md_file()` 写入 `.md` 文件
- [ ] 更新 `self._index`
- [ ] 更新 `MEMORY.md` 索引文件（追加或更新条目）

**验证方式**：参照 AC-MEM-02 全部 8 项

---

### Task-4.3：实现 `search()`

- [ ] 对指定 namespace 的所有 MemoryItem 做子串匹配
- [ ] 匹配范围：`key` + `str(value)`
- [ ] 大小写不敏感
- [ ] 按 `timestamp` 降序排序
- [ ] 截断到 `limit` 条
- [ ] namespace 不存在时返回 `[]`

**验证方式**：参照 AC-MEM-03 全部 8 项

---

### Task-4.4：实现 `list_namespaces()`

- [ ] 返回 `list(self._index.keys())`

**验证方式**：参照 AC-MEM-04 全部 3 项

---

## 阶段 5：实现 MEMORY.md 索引文件管理

### Task-5.1：实现索引文件的写入

- [ ] `_update_memory_index_md(key, value_str, namespace)` — 追加或更新一行到 `{namespace}/MEMORY.md`
- [ ] 索引文件格式：`- [key](key.md) — value第一行（截断到100字符）`
- [ ] 如果 key 已存在于索引中，更新对应行；否则追加

**验证方式**：
- `write()` 后检查 `MEMORY.md` 内容格式
- 重复 `write()` 不产生重复索引条目

---

## 阶段 6：编写单元测试

### Task-6.1：创建 `tests/test_md_memory.py`

- [ ] 创建测试文件
- [ ] 使用 `tmp_path` fixture（pytest 内置，临时目录）
- [ ] 每个测试函数使用独立的临时目录，确保测试隔离

**测试函数清单**（对照 acceptance.md）：

| 测试函数 | 覆盖标准 |
|---------|---------|
| `test_read_existing_key` | AC-MEM-01.1 |
| `test_read_nonexistent_key` | AC-MEM-01.2 |
| `test_read_nonexistent_namespace` | AC-MEM-01.3 |
| `test_read_specific_key` | AC-MEM-01.4 |
| `test_write_creates_md_file` | AC-MEM-02.1 |
| `test_write_frontmatter_content` | AC-MEM-02.2, 02.2a |
| `test_write_body_content` | AC-MEM-02.3 |
| `test_write_overwrite` | AC-MEM-02.4 |
| `test_write_then_read` | AC-MEM-02.5 |
| `test_write_non_string_value` | AC-MEM-02.6 |
| `test_write_empty_string` | AC-MEM-02.7 |
| `test_write_special_characters` | AC-MEM-02.8 |
| `test_search_finds_by_key` | AC-MEM-03.1 |
| `test_search_finds_by_value` | AC-MEM-03.1 |
| `test_search_case_insensitive` | AC-MEM-03.1 |
| `test_search_sorted_by_timestamp` | AC-MEM-03.3 |
| `test_search_respects_limit` | AC-MEM-03.4 |
| `test_search_no_match` | AC-MEM-03.5 |
| `test_search_nonexistent_namespace` | AC-MEM-03.6 |
| `test_search_empty_query` | AC-MEM-03.7 |
| `test_search_limit_zero` | AC-MEM-03.8 |
| `test_search_limit_negative` | AC-MEM-03.9 |
| `test_list_namespaces_with_data` | AC-MEM-04.1 |
| `test_list_namespaces_empty` | AC-MEM-04.2 |
| `test_list_namespaces_dedup` | AC-MEM-04.3 |
| `test_persistence_across_instances` | AC-MEM-05.1~05.4 |
| `test_init_creates_directory` | AC-MEM-06.1 |
| `test_init_loads_existing` | AC-MEM-06.2 |
| `test_init_empty_directory` | AC-MEM-06.3 |
| `test_init_expands_tilde` | AC-MEM-06.4 |
| `test_ignores_non_md_files` | AC-MEM-07.1 |
| `test_skips_malformed_frontmatter` | AC-MEM-07.2 |
| `test_skips_no_frontmatter` | AC-MEM-07.3 |
| `test_protocol_compliance` | AC-MEM-09.1 |
| `test_di_registration` | AC-MEM-09.2 |
| `test_search_returns_memory_items` | AC-MEM-10.1 |

---

## 阶段 7：运行全部测试并修复

### Task-7.1：运行 batch-03 测试

```bash
pytest tests/test_md_memory.py -v
```

- [ ] 所有测试通过

### Task-7.2：确保不破坏已有测试

```bash
pytest tests/ --ignore=tests/test_real_llm_trace.py -v
```

- [ ] 所有已有测试仍然通过（batch-03 新增代码不应影响已有行为）

---

## 完成标准

- [ ] `MdMemory` 类完整实现 4 个 MemoryBackend 方法
- [ ] 全部 ~33 个验收标准通过
- [ ] 已有测试套件无回归
- [ ] `harness/components/memory_backend/__init__.py` 导出 `MdMemory`
