# batch-03 — MemoryBackend 默认实现 设计文档

> **目标**：实现 `MemoryBackend` 接口的第一个默认实现 — `MdMemory`（基于 Markdown 文件的简单记忆存储）。提供跨会话的持久化记忆能力，使框架能跑通「Sensor 写入记忆 → 后续会话检索记忆」的完整链路。
>
> **依赖**：batch-02-1（正式类型 `MemoryItem`、`MemoryBackend` Protocol）
>
> **产出**：`harness/components/memory_backend/md_memory.py`

---

## 一、范围与边界

### 1.1 在范围内

| # | 任务 | 说明 |
|---|------|------|
| 1 | **`MdMemory` 类实现** | 完整的 `read()` / `write()` / `search()` / `list_namespaces()` 方法 |
| 2 | **Markdown 文件存储** | 每个记忆项一个 `.md` 文件，使用 YAML frontmatter + Markdown 正文 |
| 3 | **启动时内存索引** | 启动时扫描所有 `.md` 文件，构建内存索引加速 `search()` |
| 4 | **`MEMORY.md` 索引文件** | 每个 namespace 目录下维护一个索引文件，列出该空间所有记忆 |
| 5 | **子串匹配搜索** | `search()` 对 key 和 value 做简单子串匹配（大小写不敏感） |
| 6 | **单元测试** | 覆盖 4 个方法 + 边界条件（空目录、大文件、并发写等） |

### 1.2 严格不在范围内

- ❌ 不实现向量搜索 / 语义搜索（那是未来的高级 MemoryBackend）
- ❌ 不修改 `MemoryBackend` Protocol 或 `MemoryItem` 类型
- ❌ 不修改编排器中对 `MemoryBackend` 的调用逻辑
- ❌ 不实现 Sensor（那是 batch-07）
- ❌ 不实现 ContextAssembler（那是 batch-05）
- ❌ 不实现多线程安全（batch-03 阶段单线程，后续按需添加）
- ❌ 不依赖任何第三方库（仅使用 Python 标准库）

---

## 二、核心设计决策

### 2.1 为什么用 Markdown 而非 JSONL

| 维度 | JSONL | Markdown |
|------|-------|----------|
| 人类可读性 | 中（需理解 JSON 结构） | **高**（天然可读，支持格式化文本） |
| 可审计性 | 中（一行 JSON 仍可 grep） | **高**（可在任何编辑器打开阅读） |
| 结构化元数据 | JSON 原生 | YAML frontmatter |
| 正文内容 | JSON 字符串（需转义） | **原生 Markdown**（自然书写） |
| 文件粒度 | 单文件多记录 | 每记忆一个独立文件 |
| 解析复杂度 | `json.loads()` 每行 | `yaml` 库解析 frontmatter（或手写简单解析器） |

**选择 Markdown 的核心理由**：Harness 的定位是「个人开发者最易裁剪的框架模板」。MD 记忆库可以直接用编辑器浏览和修改，无需任何工具。这与已有社区实践（Claude Code 的 `/memory` 系统）一致。

### 2.2 存储格式设计

```
~/.harness/memory/                  ← 默认存储根目录（可通过 path 参数修改）
├── MEMORY.md                       ← 全局索引（可选，列出所有 namespace）
├── episodic/                       ← episodic 命名空间
│   ├── MEMORY.md                   ← 该空间的索引文件
│   ├── session-20260603-001.md     ← 记忆项文件
│   └── session-20260603-002.md
├── semantic/
│   ├── MEMORY.md
│   └── user-prefers-python.md
├── procedural/
│   ├── MEMORY.md
│   └── git-workflow-pattern.md
├── sensor_raw/
│   └── ...
└── system/
    └── ...
```

### 2.3 单个 `.md` 记忆文件格式

```markdown
---
key: session-20260603-001
namespace: episodic
timestamp: 1717430400.0
metadata:
  session_id: abc123
  token_count: 15000
---

# 对话摘要：2026-06-03

用户询问了关于 Python async/await 的使用方式。
Agent 提供了详细的代码示例和最佳实践建议。

## 关键决策
- 项目决定使用 asyncio 而非 trio
- 所有 IO 操作将迁移为异步模式
```

**字段说明**：
- `key`：唯一标识符（同时也是文件名的主体部分）
- `namespace`：命名空间（冗余存储，方便复制单个文件时自描述）
- `timestamp`：写入时间戳（Unix epoch float）
- `metadata`：扩展元数据（YAML dict，对应 `MemoryItem.metadata`）
- frontmatter 之后的所有内容为 `value`（Markdown 正文）

### 2.4 `MEMORY.md` 索引文件格式

每个 namespace 目录下的 `MEMORY.md`：

```markdown
# episodic — 事件记忆索引

- [session-20260603-001](session-20260603-001.md) — 对话摘要：用户询问了 Python async/await...
- [session-20260603-002](session-20260603-002.md) — 对话摘要：调试了数据库连接池配置问题
```

索引文件是**可选的**、**辅助性的**。即使被删除，`MdMemory` 启动时扫描 `.md` 文件即可重建内存索引。

### 2.5 不引入 YAML 解析依赖

Python 标准库没有 YAML 解析器。为避免引入 `pyyaml` 依赖，`MdMemory` 使用**手写简单 frontmatter 解析器**：

```python
def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + Markdown 正文。

    仅支持简单格式：
    - key: value（字符串、数字、布尔值）
    - key:\n  indented lines（多行字符串）
    - 不支持嵌套 YAML 结构（除 metadata 下的简单 dict）

    返回 (frontmatter_dict, body_text)。
    """
```

如果后续需要完整的 YAML 支持，用户可安装 `pyyaml` 并替换解析逻辑。这是「先简单、可工作、可替换」的务实策略。

---

## 三、MdMemory 类设计

### 3.1 构造函数

```python
class MdMemory:
    """MemoryBackend 的 Markdown 文件存储实现。

    每个记忆项一个 .md 文件，使用 YAML frontmatter + Markdown 正文。
    启动时扫描目录构建内存索引，写入时同时更新 .md 文件和 MEMORY.md 索引。

    用法::

        memory = MdMemory(path="./memory")
        memory.write("pref-001", "用户偏好 Python", "semantic")
        results = memory.search("Python", "semantic")
    """

    def __init__(self, path: str = "~/.harness/memory"):
        """初始化 MdMemory。

        Args:
            path: 记忆库根目录路径。默认为 ~/.harness/memory。
                  目录不存在时自动创建。
                  支持 ~ 展开为用户主目录。
        """
```

### 3.2 内部数据结构

```python
# 内存索引：namespace → {key → MemoryItem}
_index: Dict[str, Dict[str, MemoryItem]]

# 根目录路径（展开后的绝对路径）
_root: Path
```

### 3.3 方法签名

```python
def read(self, key: str, namespace: str) -> Optional[Any]:
    """按 key 读取记忆值。

    从内存索引中查找（O(1)），不读磁盘。

    Args:
        key: 记忆键。
        namespace: 命名空间。

    Returns:
        Optional[Any]: 记忆值（Markdown 正文），不存在时为 None。
    """

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

def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]:
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

def list_namespaces(self) -> List[str]:
    """列出所有已知命名空间。

    从内存索引的 key 集合返回。

    Returns:
        List[str]: 命名空间名称列表。
    """
```

---

## 四、核心实现逻辑

### 4.1 初始化流程

```
__init__(path)
  ├── 1. 展开 ~ → 用户主目录
  ├── 2. 确保根目录存在（os.makedirs）
  ├── 3. 扫描所有 namespace 子目录
  │      └── 对每个 .md 文件（排除 MEMORY.md）
  │          ├── 解析 frontmatter
  │          └── 构建 MemoryItem → 写入内存索引
  └── 4. 记录启动日志（已加载 X 条记忆，Y 个 namespace）
```

### 4.2 write() 流程

```
write(key, value, namespace)
  ├── 1. 生成文件名：{key}.md
  ├── 2. 确保 namespace 子目录存在
  ├── 3. 构建 frontmatter：
  │      key, namespace, timestamp, metadata
  ├── 4. 将 value 转为字符串（如果不是 str）
  ├── 5. 写入 .md 文件（覆盖已有同名文件）
  ├── 6. 更新内存索引
  └── 7. 更新 MEMORY.md 索引文件（追加或更新条目）
```

### 4.3 search() 匹配算法

```python
def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]:
    if namespace not in self._index:
        return []

    query_lower = query.lower()
    results = []

    for item in self._index[namespace].values():
        # 匹配 key
        if query_lower in item.key.lower():
            results.append(item)
            continue
        # 匹配 value（转为字符串）
        value_str = str(item.value) if item.value else ""
        if query_lower in value_str.lower():
            results.append(item)

    # 按时间戳降序排序
    results.sort(key=lambda x: x.timestamp, reverse=True)

    if limit <= 0:
        return results[:len(results)] if limit < 0 else []
    return results[:limit]
```

---

## 五、错误处理与边界条件

| 场景 | 行为 |
|------|------|
| 根目录不存在 | 自动创建（`os.makedirs`） |
| 根目录无写权限 | 首次 `write()` 时抛出 `OSError` 或 `PermissionError`。文件系统级错误使用 Python 标准异常是合理的，因为这是操作系统层错误而非框架逻辑错误 |
| 单个 `.md` 文件 frontmatter 解析失败 | 记录 WARNING 日志，跳过该文件，不阻塞启动 |
| `write()` 覆盖已有 key | 正常覆盖（不是错误），索引更新 |
| `read()` 不存在 key | 返回 `None` |
| `search()` 空 query | 返回空列表 |
| `search()` 不存在的 namespace | 返回空列表 |
| `search()` query 匹配不到 | 返回空列表 |
| `search()` limit=0 | 返回空列表 |
| `search()` limit<0 | 视为无限制（返回全部匹配结果） |
| 并发 write（同一进程多线程） | batch-03 不保证安全，后续按需添加 `threading.Lock` |
| value 为非字符串类型 | `str(value)` 转换后写入 |

---

## 六、文件布局

### 6.1 产出文件

```
harness/components/memory_backend/
├── __init__.py              # 导出 MdMemory
└── md_memory.py             # MdMemory 完整实现
```

### 6.2 测试文件

```
tests/
└── test_md_memory.py        # MdMemory 单元测试
```

### 6.3 模块依赖关系（batch-03 内部）

```
harness/interfaces/types.py          ← MemoryItem（已存在，不修改）
harness/interfaces/memory_backend.py ← MemoryBackend Protocol（已存在，不修改）
    ↑
harness/components/memory_backend/md_memory.py  ← batch-03 新增
    ↑
tests/test_md_memory.py              ← batch-03 新增
```

---

## 七、与前后批次的接口约定

### 7.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| `MemoryItem` dataclass | batch-02 `interfaces/types.py` | `write()` 构建 MemoryItem；`search()` 返回 `List[MemoryItem]` |
| `MemoryBackend` Protocol | batch-02 `interfaces/memory_backend.py` | `MdMemory` 满足此 Protocol（不显式继承，Duck Typing） |
| DI 容器 | batch-01 `core/container.py` | 用户通过 `container.register(MemoryBackend, MdMemory(...))` 注册 |

### 7.2 为后续批次提供的基础

| 被使用方 | 批次 | 使用方式 |
|---------|------|---------|
| 编排器 Phase 1 | batch-01（已有） | 调用 `memory.search(user_request.text, "episodic")` |
| 编排器 Phase 3 | batch-01（已有） | 间接触发：`Sensor.sense(trajectory)` → Sensor 内部调用 `memory.write()` |
| ContextAssembler | batch-05 | 可选通过构造注入 `MdMemory` 实例，执行超越框架基线的定制检索 |
| LoggingSensor | batch-07 | 必须通过构造注入 `MdMemory` 实例，在 `sense()` 中调用 `write()` |

### 7.3 DI 装配示例

```python
from harness.core.container import DIContainer
from harness.interfaces import MemoryBackend
from harness.components.memory_backend import MdMemory

# 创建共享的 MemoryBackend 实例
memory = MdMemory(path="./my_agent_memory")

# 注册到容器（同一个实例会被编排器、Sensor、ContextAssembler 共享）
container = DIContainer()
container.register(MemoryBackend, memory)
```

---

## 八、关键设计决策汇总

| # | 决策 | 权衡与理由 |
|---|------|-----------|
| 1 | Markdown 文件而非 JSONL | 人类可读性优先。个人开发者可直接编辑记忆文件 |
| 2 | 每个记忆项一个独立 `.md` 文件 | 便于手动增删；避免单文件写入冲突 |
| 3 | 手写 frontmatter 解析器，不引入 pyyaml | 零外部依赖，保持框架的「pip install 无需额外包」承诺 |
| 4 | `MEMORY.md` 作为辅助索引 | 人类可浏览的索引；程序启动时扫描 `.md` 文件而非解析 MEMORY.md |
| 5 | 启动时全量构建内存索引 | 简单可靠；记忆量在个人使用场景下（<10K 条）性能完全足够 |
| 6 | `search()` 子串匹配（大小写不敏感） | batch-03 最简实现；后续可实现向量搜索版本 |
| 7 | 覆盖式 `write()`（不检查 key 是否存在） | 简化语义；去重由调用方负责 |
| 8 | `read()` 从内存索引读取（不读磁盘） | 性能优化；写入后索引已同步 |
| 9 | 单线程模型（batch-03） | YAGNI；后续按需添加锁 |
| 10 | namespace 实现为子目录 | 天然隔离；方便文件系统浏览；`list_namespaces()` 即列出子目录 |
