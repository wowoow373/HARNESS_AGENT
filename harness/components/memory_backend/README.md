# MemoryBackend

> **Interface**: [`MemoryBackend`](../../interfaces/memory_backend.py) | **Required?**: No | **Lifecycle Phase**: Init (read) + End (write)

## Interface Contract

`MemoryBackend` 提供跨会话的持久化存储与检索。框架在会话初始化时检索记忆，Sensor 在会话结束时写入知识。

```python
class MemoryBackend(Protocol):
    def read(self, key: str, namespace: str) -> Optional[Any]: ...
    def write(self, key: str, value: Any, namespace: str) -> None: ...
    def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]: ...
    def list_namespaces(self) -> List[str]: ...
```

| Method | When Called | Purpose |
|--------|-------------|---------|
| `search(query, namespace, limit)` | Session init (Phase 1) | Retrieves relevant memories for assembly context |
| `read(key, namespace)` | On demand | Direct key lookup |
| `write(key, value, namespace)` | Session end (Phase 3) | Persists knowledge (called by Sensor) |
| `list_namespaces()` | Diagnostics | Lists all known namespaces |

### Namespace Conventions

| Namespace | Purpose | Writer | Reader |
|-----------|---------|--------|--------|
| `episodic` | Session summaries | Sensor | ContextAssembler |
| `semantic` | Facts / user preferences | Sensor | ContextAssembler |
| `procedural` | Reusable skill patterns | Sensor | ContextAssembler |
| `sensor_raw` | Raw sensor evaluations | Sensor | Sensor (cross-session) |
| `system` | System state cache | Framework | Framework |

> These are community conventions, not enforced by the framework.

---

## Default Implementation: MdMemory

`MdMemory` 使用 **Markdown 文件** 作为存储后端——每个记忆项一个 `.md` 文件，使用 YAML frontmatter + Markdown 正文。零外部依赖。

```
memory/
├── episodic/
│   ├── MEMORY.md
│   ├── session_cli-123456.md
│   └── session_cli-789012.md
├── semantic/
│   ├── MEMORY.md
│   └── pref-python.md
└── procedural/
    └── MEMORY.md
```

### Usage

```python
from harness.components.memory_backend.md_memory import MdMemory

memory = MdMemory(path="./memory")          # auto-creates dir, builds index
memory.write("pref-001", "User prefers Python", namespace="semantic")
results = memory.search("Python", namespace="semantic", limit=5)
```

### Constructor

```python
MdMemory(path: str = "~/.harness/memory")
```

| Param | Default | Description |
|-------|---------|-------------|
| `path` | `"~/.harness/memory"` | Root directory; auto-created; supports `~` expansion |

### Key Behaviors

- **Startup scan**: Reads all `.md` files on init, builds in-memory index (O(1) lookups)
- **Atomic writes**: Writes to `.tmp` file then `os.replace()` for crash safety
- **MEMORY.md index**: Each namespace gets a `MEMORY.md` with one-line-per-memory links
- **Search**: Case-insensitive substring match on `key` + `value`, newest-first, truncated to `limit`
- **Read**: O(1) from in-memory index, no disk I/O

---

## Implement Your Own

MemoryBackend 是最常见的替换点——你可能想接入 PostgreSQL、Redis、向量数据库等。

### Minimal Example

```python
class MyMemory:
    def read(self, key: str, namespace: str):
        # Your lookup logic
        return self._db.get(f"{namespace}:{key}")

    def write(self, key: str, value, namespace: str) -> None:
        # Your persist logic
        self._db.set(f"{namespace}:{key}", str(value))

    def search(self, query: str, namespace: str, limit: int = 10):
        # Your search logic (keyword, vector, hybrid...)
        rows = self._db.query(
            f"SELECT * FROM {namespace} WHERE content LIKE '%{query}%' LIMIT {limit}"
        )
        return [MemoryItem(key=r.key, value=r.content, namespace=namespace,
                           timestamp=r.ts) for r in rows]

    def list_namespaces(self):
        return self._db.list_tables()
```

> You only need matching method signatures — no inheritance required.

### Registration

```python
container.register(MemoryBackend, MyMemory(path="./my_memory"))
```

---

## Deep Harness Usage

你的 MemoryBackend 实现内部可以用 `DIContainer` 再装配一个完整的 Harness Agent 来做智能记忆管理——例如记忆 consolidation、摘要生成、rerank 等：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, ContextAssembler, MemoryBackend, Sensor
from harness.adapters.llm_adapter import MinimalLLMAdapter

class AgenticMemory:
    """Memory backend that uses a sub-harness for memory consolidation."""

    def __init__(self, path: str, sub_llm=None):
        self._storage = MdMemory(path=path)
        self._sub_llm = sub_llm or MinimalLLMAdapter()

    def write(self, key: str, value, namespace: str) -> None:
        self._storage.write(key, value, namespace)
        # Periodically trigger memory consolidation via a sub-harness
        if self._should_consolidate(namespace):
            self._consolidate(namespace)

    def _consolidate(self, namespace: str) -> None:
        """Run a sub-harness to summarize and consolidate related memories."""
        memories = self._storage.search("", namespace, limit=50)

        # Assemble a sub-harness dedicated to memory consolidation
        sub_container = DIContainer()
        sub_container.register(InputAdapter, ConsolidationAdapter(memories))
        sub_container.register(ContextAssembler, ConsolidationAssembler(memories))
        sub_container.register(MemoryBackend, self)
        sub_container.register(Sensor, ConsolidationSensor(memory=self))

        sub_harness = Harness.from_container(sub_container, call_llm=self._sub_llm)
        sub_harness.run()

    def search(self, query: str, namespace: str, limit: int = 10):
        keyword_results = self._storage.search(query, namespace, limit)
        # Optionally re-rank with a sub-harness
        return keyword_results
```

This is the core recursive pattern of Harness：any module can assemble its own `DIContainer` + `Harness.from_container()`
to get a fully functional agent — with its own guides, memory, tools, sensors, and lifecycle.
