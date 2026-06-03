# batch-04 — GuideProvider 默认实现 任务清单

> 按顺序逐条执行，完成后勾选。

---

## 阶段 1：创建目录结构与模块骨架

### Task-4.1：创建 `harness/components/guide_provider/` 包

- [ ] 确保 `harness/components/` 目录存在（batch-03 已创建）
- [ ] 创建 `harness/components/guide_provider/` 目录
- [ ] 创建 `harness/components/guide_provider/__init__.py`

**产出**：
```
harness/components/
├── __init__.py
├── memory_backend/          ← batch-03 已创建
│   └── ...
└── guide_provider/          ← batch-04 新增
    └── __init__.py
```

**验证方式**：`python -c "import harness.components.guide_provider"` 无错误

---

### Task-4.2：创建 `file_guide_provider.py` 模块骨架

- [ ] 创建 `harness/components/guide_provider/file_guide_provider.py`
- [ ] 添加文件 docstring
- [ ] 添加 `FileGuideProvider` 类定义和构造函数签名
- [ ] 添加 `get_guides(context)` 方法骨架（返回空 GuidesBundle）
- [ ] 在 `__init__.py` 中导出 `FileGuideProvider`

**产出**：
```python
# harness/components/guide_provider/__init__.py
from .file_guide_provider import FileGuideProvider

__all__ = ["FileGuideProvider"]
```

**验证方式**：`from harness.components.guide_provider import FileGuideProvider` 无 ImportError

---

## 阶段 2：实现 Markdown 解析器

### Task-4.3：实现标题层级识别与 section 分类

在 `file_guide_provider.py` 中实现：

- [ ] `SECTION_KEYWORDS` 类属性（字典：字段名 → 关键词列表）
- [ ] `_identify_section(heading: str) -> Optional[str]` — 根据 H2 标题文本返回字段名
- [ ] 大小写不敏感匹配
- [ ] 支持中英文关键词

**关键词表**：
| 字段 | 关键词 |
|------|--------|
| capabilities | 能力, capabilit, 技能, skill |
| rules | 规则, rule, 行为, behavior, behaviour |
| constraints | 约束, constraint, 限制, limit |
| examples | 示例, example, 范例, sample |

**验证方式**：单元测试 — 各种中英文标题的匹配和未匹配情况

---

### Task-4.4：实现行级 Markdown 解析器

在 `file_guide_provider.py` 中实现：

- [ ] `_parse_markdown_guides(text: str) -> GuidesBundle`
- [ ] 识别 H1 (`# `) → 进入 identity 模式，收集后续内容
- [ ] 识别 H2 (`## `) → 调用 `_identify_section()` 切换当前 section
- [ ] 识别 H3 (`### `) → 在 examples section 下开始新的示例分组
- [ ] 识别列表项 (`- ` / `* `) → 在 capabilities/rules/constraints section 下收集为列表元素
- [ ] 识别普通文本 → 在 identity section 下追加到 identity 字符串
- [ ] 识别示例中的 `**输入**:` / `**输出**:` 标记 → 配对为 Example
- [ ] 空行处理：在 identity section 中保留，在其他 section 中忽略

**状态机描述**：
```
初始状态: current_section = "identity"
遍历每一行:
  ├── 以 "# " 开头  → H1: 将所有已收集的 H1 内容文本追加到 identity, current_section = "identity"
  ├── 以 "## " 开头 → H2: 调用 _identify_section(), 切换 current_section
  ├── 以 "### " 开头→ H3: 仅在 current_section=="examples" 时，开始新的 example 分组
  ├── 以 "- " 或 "* " 开头 → 列表项: 根据 current_section 收集到对应列表字段
  ├── 匹配 "**输入**:" / "**输出**:" → 仅在 examples 模式下配对
  ├── 空行 → 在 identity 模式下保留（段落分隔），否则跳过
  └── 其他文本 → 在 identity 模式下追加到 identity 字符串
```

**验证方式**：
- 解析含有全部 5 个字段的完整 AGENTS.md
- 解析只有 identity 的最小文件
- 解析多个示例的文件

---

### Task-4.5：实现示例（Examples）解析

- [ ] 在 examples section 下，按 `###` 标题分组
- [ ] 每个分组内查找 `**输入**:` / `**输出**:` 行
- [ ] 配对为 `Example(input=..., output=...)`
- [ ] 支持 `Input:` / `Output:` 等变体格式（英文标签）
- [ ] 无 `###` 分组时，整个 examples section 作为一个示例（或跳过）

**支持的输入/输出标记格式**：
- `**输入**:` / `**输出**:`
- `**Input**:` / `**Output**:`
- `输入:` / `输出:` (无加粗标记)
- `Input:` / `Output:` (英文)

**验证方式**：单元测试 — 有/无子标题的示例解析，中英文标记

---

## 阶段 3：实现文件读取与合并

### Task-4.6：实现单文件读取与解析

- [ ] `_read_and_parse(filepath: Path) -> Optional[GuidesBundle]`
- [ ] 文件不存在 → WARNING，返回 None
- [ ] 编码错误 → WARNING，返回 None
- [ ] 文件为空 → 返回空 GuidesBundle
- [ ] 正常读取 → 调用 `_parse_markdown_guides()` 并返回

**日志记录**：
- INFO: "Loaded guide from {filepath} (X rules, Y constraints)"
- WARNING: "Guide file not found: {filepath}"
- WARNING: "Failed to read guide file {filepath}: {error}"

**验证方式**：测试文件存在/不存在/编码错误场景

---

### Task-4.7：实现多文件合并逻辑

- [ ] `_merge_guides(base: GuidesBundle, new: GuidesBundle) -> GuidesBundle`
- [ ] identity：用空行拼接
- [ ] capabilities：合并列表并去重（保持首次出现顺序）
- [ ] rules：追加到末尾（不去重）
- [ ] constraints：追加到末尾（不去重）
- [ ] examples：追加到末尾（不去重）

**合并规则**：

| 字段 | 合并方式 | 原因 |
|------|---------|------|
| identity | `base + "\n\n" + new` （如 base 非空） | 多文件身份定义应串联 |
| capabilities | 合并 + 去重 | 重复的能力声明没有意义 |
| rules | 追加（不去重） | 重复的规则 = 强调，应保留 |
| constraints | 追加（不去重） | 与 rules 一致 |
| examples | 追加（不去重） | 每个示例都有价值 |

**验证方式**：合并两个各含不同字段的 GuidesBundle，验证合并结果

---

## 阶段 4：实现完整 get_guides() 方法

### Task-4.8：实现 `get_guides()` 主方法

- [ ] 接收 `GuideContext` 参数（类型标注）
- [ ] 遍历所有 `_paths`
- [ ] 对每个路径调用 `_read_and_parse()`
- [ ] 累加合并结果
- [ ] 返回最终 GuidesBundle
- [ ] 全部文件不可读时 → WARNING + 返回空 GuidesBundle

**实现参考**：
```python
def get_guides(self, context: GuideContext) -> GuidesBundle:
    result = GuidesBundle()
    any_loaded = False
    
    for filepath in self._paths:
        bundle = self._read_and_parse(filepath)
        if bundle is not None:
            result = self._merge_guides(result, bundle)
            any_loaded = True
    
    if not any_loaded:
        logger.warning(f"No guide files loaded from {self._paths}")
    
    return result
```

**验证方式**：参照 AC-GP-01 ~ AC-GP-04 全部验收项

---

## 阶段 5：编写单元测试

### Task-5.1：创建 `tests/test_guide_provider.py`

- [ ] 创建测试文件
- [ ] 使用 `tmp_path` fixture（pytest 内置，临时目录）
- [ ] 每个测试函数使用独立的临时目录和临时 Markdown 文件

**测试函数清单**（对照 acceptance.md）：

| 测试函数 | 覆盖标准 |
|---------|---------|
| `test_parse_identity` | AC-GP-01.1 |
| `test_parse_capabilities` | AC-GP-01.2 |
| `test_parse_rules` | AC-GP-01.3 |
| `test_parse_constraints` | AC-GP-01.4 |
| `test_parse_examples` | AC-GP-01.5 |
| `test_parse_minimal_file` | AC-GP-01.6 |
| `test_parse_chinese_headings` | AC-GP-02.1 |
| `test_parse_english_headings` | AC-GP-02.2 |
| `test_parse_mixed_headings` | AC-GP-02.3 |
| `test_file_not_found` | AC-GP-03.1 |
| `test_empty_file` | AC-GP-04.1 |
| `test_no_h1_heading` | AC-GP-04.2 |
| `test_unrecognized_h2` | AC-GP-04.3 |
| `test_examples_without_h3` | AC-GP-04.4 |
| `test_multi_file_merge_identity` | AC-GP-05.1 |
| `test_multi_file_merge_rules` | AC-GP-05.2 |
| `test_multi_file_merge_capabilities_dedup` | AC-GP-05.3 |
| `test_multi_file_partial_missing` | AC-GP-05.4 |
| `test_all_files_missing` | AC-GP-03.2 |
| `test_protocol_compliance` | AC-GP-08.1 |
| `test_di_registration` | AC-GP-08.2 |
| `test_returns_guides_bundle_type` | AC-GP-09.1 |
| `test_examples_type` | AC-GP-09.2 |
| `test_file_with_special_characters` | AC-GP-06.2 |
| `test_identity_with_multiple_h1s` | AC-GP-06.3 |
| `test_orchestrator_phase1_get_guides` | AC-GP-10.1 (编排器集成) |
| `test_orchestrator_without_guide_provider` | AC-GP-10.1 (可选组件缺失不崩溃) |

---

## 阶段 6：运行全部测试并修复

### Task-6.1：运行 batch-04 测试

```bash
pytest tests/test_guide_provider.py -v
```

- [ ] 所有测试通过

### Task-6.2：确保不破坏已有测试

```bash
pytest tests/ --ignore=tests/test_real_llm_trace.py -v
```

- [ ] 所有已有测试仍然通过（batch-04 新增代码不应影响已有行为）

---

## 完成标准

- [ ] `FileGuideProvider` 类完整实现 `get_guides()` 方法
- [ ] 全部 41 个验收标准通过（AC-GP-01 ~ AC-GP-10）
- [ ] 已有测试套件无回归
- [ ] `harness/components/guide_provider/__init__.py` 导出 `FileGuideProvider`
