# GuideProvider

> **Interface**: [`GuideProvider`](../../interfaces/guide_provider.py) | **Required?**: No | **Lifecycle Phase**: Init (called once, cached)

## Interface Contract

`GuideProvider` 提供**前馈控制**——在 Agent 开始行动前，注入身份定义、能力清单、行为规则和硬约束。

```python
class GuideProvider(Protocol):
    def get_guides(self, context: GuideContext) -> GuidesBundle: ...
```

### Input: GuideContext

| Field | Type | Description |
|-------|------|-------------|
| `user_request` | `Optional[UserRequest]` | 当前用户请求 |
| `system_state` | `SystemState` | 系统阶段、运行模式 |
| `env_state` | `Optional[EnvState]` | 工作目录、git 状态、平台 |
| `metadata` | `Dict[str, Any]` | 扩展桶 |

### Output: GuidesBundle

| Field | Type | Description |
|-------|------|-------------|
| `identity` | `str` | 核心身份（e.g. "You are a coding assistant..."） |
| `capabilities` | `List[str]` | 能力清单 |
| `rules` | `List[str]` | 行为规则 |
| `constraints` | `List[str]` | 硬约束（不可违反） |
| `examples` | `List[Example]` | 少样本示例（每个含 `input`/`output`） |

### Lifecycle

```
Session Init:
  1. InputAdapter.receive() → UserRequest
  2. Framework builds GuideContext with user_request + system_state + env_state
  3. GuideProvider.get_guides(context) → GuidesBundle
  4. GuidesBundle is CACHED for entire session
```

> Called **once per session**. The returned `GuidesBundle` is cached and reused across all conversation turns.

---

## Default Implementation: FileGuideProvider

从 **AGENTS.md / CLAUDE.md** 等 Markdown 文件解析指导信息。使用手写行级解析器，**零外部依赖**。

### Usage

```python
from harness.components.guide_provider.file_guide_provider import FileGuideProvider

# Single file
guide = FileGuideProvider("AGENTS.md")

# Multiple files (merged)
guide = FileGuideProvider(["AGENTS.md", "TEAM_RULES.md"])

bundle = guide.get_guides(context)
print(bundle.identity)    # "You are a coding assistant..."
print(bundle.rules)       # ["规则1", "规则2", ...]
```

### Constructor

```python
FileGuideProvider(paths: Union[str, List[str]])
```

| Param | Description |
|-------|-------------|
| `paths` | Single path or list; supports `~` expansion. Missing files → WARNING, not error. |

### Markdown Format

`FileGuideProvider` 按 Markdown 标题层级解析：

```markdown
# You are a coding assistant specializing in Python      → identity

## Capabilities                                            → capabilities[]
- Write and debug Python code
- Refactor legacy codebases

## Rules                                                    → rules[]
- Always explain your reasoning before writing code
- Prefer type hints

## Constraints                                              → constraints[]
- Never modify files outside the project directory
- Do not execute commands without user confirmation

## Examples                                                 → examples[]
### Example 1
**输入**: Refactor this function
**输出**: Here's the refactored version...
```

- **H1** (`# `) → `identity`
- **H2** (`## `) → matched by keywords to `capabilities` / `rules` / `constraints` / `examples`
- **H3** (`### `) → new example within examples section
- **List items** (`- ` / `* `) → collected into capabilities/rules/constraints lists
- **`**输入**:**` / `**输出**:**` → example input/output markers

### Multi-file Merging

When multiple files are provided:

| Field | Merge Strategy |
|-------|---------------|
| `identity` | Concatenated with double newline |
| `capabilities` | Merged + deduplicated (first-seen wins) |
| `rules` | Appended (no dedup) |
| `constraints` | Appended (no dedup) |
| `examples` | Appended (no dedup) |

---

## Implement Your Own

### Static (file-based) variant

```python
class JsonGuideProvider:
    def __init__(self, path: str):
        import json
        with open(path) as f:
            self._data = json.load(f)

    def get_guides(self, context: GuideContext) -> GuidesBundle:
        return GuidesBundle(
            identity=self._data.get("identity", ""),
            capabilities=self._data.get("capabilities", []),
            rules=self._data.get("rules", []),
            constraints=self._data.get("constraints", []),
        )
```

### Dynamic (context-aware) variant

```python
class ContextualGuideProvider:
    def get_guides(self, context: GuideContext) -> GuidesBundle:
        # Select different guides based on user request content
        if context.user_request and "debug" in context.user_request.text:
            return GuidesBundle(
                identity="You are a debugging specialist...",
                rules=["Always check logs first", "Isolate the problem"],
            )
        return GuidesBundle(
            identity="You are a general assistant...",
        )
```

### Registration

```python
container.register(GuideProvider, FileGuideProvider("AGENTS.md"))
# or
container.register(GuideProvider, ContextualGuideProvider())
```

---

## Deep Harness Usage

GuideProvider 在会话初始化时调用一次，天然适合用子 Harness 做深度分析——比如扫描项目结构后动态生成 system prompt：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, ContextAssembler

class ProjectAwareGuide:
    """Analyzes the project with a sub-harness to generate tailored guides."""

    def __init__(self, sub_llm):
        self._sub_llm = sub_llm

    def get_guides(self, context: GuideContext) -> GuidesBundle:
        # 装配一个 one-shot 子 Harness 来分析项目并生成 system prompt
        sub_container = DIContainer()
        sub_container.register(InputAdapter, AnalysisAdapter(context))
        sub_container.register(ContextAssembler, AnalysisAssembler(context))

        sub_harness = Harness.from_container(sub_container, call_llm=self._sub_llm)
        sub_harness.run()

        return GuidesBundle(
            identity=sub_harness._final_output or "You are a helpful assistant.",
            capabilities=["Code analysis", "Refactoring"],
        )
```

