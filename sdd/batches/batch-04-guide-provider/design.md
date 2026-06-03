# batch-04 — GuideProvider 默认实现 设计文档

> **目标**：实现 `GuideProvider` 接口的第一个默认实现 — `FileGuideProvider`（从文件系统读取 AGENTS.md 等文本文件作为 Agent 指导信息）。提供前馈控制能力，使框架能跑通「启动时读取 Guide → 注入上下文 → 影响 LLM 行为」的完整链路。
>
> **依赖**：batch-02-1（正式类型 `GuidesBundle`、`GuideProvider` Protocol、`GuideContext`）
>
> **产出**：`harness/components/guide_provider/file_guide_provider.py`

---

## 一、范围与边界

### 1.1 在范围内

| # | 任务 | 说明 |
|---|------|------|
| 1 | **`FileGuideProvider` 类实现** | 实现 `get_guides(context)` 方法，从 Markdown 文件解析指导内容 |
| 2 | **Markdown 文件解析** | 按 Markdown 标题层级（H1 → identity, H2 → rules/capabilities/constraints/examples）提取内容 |
| 3 | **至少支持 identity + rules** | 最低可用标准：能从文件加载身份定义和行为规则（roadmap 明确要求） |
| 4 | **全部 5 个 GuidesBundle 字段** | 完整解析 identity、capabilities、rules、constraints、examples |
| 5 | **多文件聚合** | 支持传入文件列表，按顺序合并多个指导文件的内容 |
| 6 | **文件不存在容错** | 文件缺失时记录 WARNING 但不崩溃，返回空 GuidesBundle |
| 7 | **单元测试** | 覆盖解析逻辑 + 边界条件（空文件、缺失文件、多文件等） |

### 1.2 严格不在范围内

- ❌ 不实现动态/网络 GuideProvider（那是未来的高级实现）
- ❌ 不修改 `GuideProvider` Protocol 或 `GuidesBundle` 类型
- ❌ 不修改 `GuideContext` dataclass
- ❌ 不修改编排器中对 `GuideProvider` 的调用逻辑
- ❌ 不实现 ContextAssembler（那是 batch-05）
- ❌ 不依赖任何第三方库（仅使用 Python 标准库）
- ❌ 不做 YAML frontmatter / TOML 解析（batch-04 只解析 Markdown 标题层级）

---

## 二、核心设计决策

### 2.1 为什么用 Markdown 标题层级而非 YAML/TOML

| 维度 | Markdown 标题 | YAML frontmatter | TOML |
|------|-------------|-------------------|------|
| 人类可读性 | **最高**（自然书写） | 中（结构化但需理解语法） | 中 |
| 编辑工具 | 任何编辑器 | 任何编辑器 | 任何编辑器 |
| 格式容错 | **高**（松散解析） | 低（语法错误则失败） | 低 |
| 社区习惯 | AGENTS.md / CLAUDE.md 已是惯例 | Memory backend 的 frontmatter | profile.toml 配置 |
| 多文件合并 | **天然拼接** | 需要解析器合并 | 需要解析器合并 |

**选择 Markdown 标题层级的核心理由**：AGENTS.md 和 CLAUDE.md 已成为社区事实标准，用户已经熟悉这种格式。标题层级天然映射到 GuidesBundle 的层级结构（H1 = identity = "我是谁"，H2 = 各类指导 = "我要如何行为"）。

### 2.2 输入文件格式约定

```markdown
# 核心身份定义

You are a coding assistant specialized in Python development.
You follow best practices from PEP 8 and emphasize code readability.

## 能力清单

- 编写和审查 Python 代码
- 调试和性能优化
- 数据库设计与 SQL 优化
- Git 工作流管理

## 行为规则

- 所有代码必须通过 mypy 类型检查
- 优先使用标准库而非第三方依赖
- 在修改代码前先理解上下文
- 对于非 trivial 的修改，先提出方案再实现

## 硬约束

- 绝不修改 .git 目录下的文件
- 绝不执行未经确认的删除操作
- 绝不在生产环境直接调试

## 示例

### 示例 1: 代码审查

**输入**:
Review this function for bugs.

**输出**:
I will analyze the function systematically...

### 示例 2: 添加功能

**输入**:
Add a rate limiter to the API client.

**输出**:
I'll propose a plan first before implementing...
```

**解析规则**：

| Markdown 层级 | 映射到 GuidesBundle 字段 | 说明 |
|-------------|------------------------|------|
| `# xxx` (H1) | `identity` | 核心身份定义。所有 H1 标题下的内容（直到下一个 H1 或文件结束）合并为 identity 字段 |
| `## 能力` / `## Capabilities` | `capabilities` | 能力清单。提取列表项（`- xxx`），每项作为一个能力 |
| `## 规则` / `## Rules` / `## 行为规则` | `rules` | 行为规则列表。提取列表项，每项作为一条规则 |
| `## 约束` / `## Constraints` / `## 硬约束` | `constraints` | 硬约束列表。提取列表项，每项作为一条约束 |
| `## 示例` / `## Examples` | `examples` | 少样本示例。按 `###` 子标题分组，每组的输入/输出映射为一个 `Example` |

**标题匹配关键词**（大小写不敏感，支持中英文）：

| 目标字段 | 匹配的 H2 标题关键词 |
|---------|--------------------|
| `identity` | H1 (`# xxx`) — 所有顶层的 H1 内容 |
| `capabilities` | `能力`, `capabilit`, `技能`, `skill` |
| `rules` | `规则`, `rule`, `行为`, `behavior`, `behaviour` |
| `constraints` | `约束`, `constraint`, `限制`, `limit`, `禁止` |
| `examples` | `示例`, `example`, `范例`, `sample` |

### 2.3 多文件支持策略

`FileGuideProvider` 接受 `paths` 参数（文件名列表或单个文件名）：

```python
# 单文件
guide = FileGuideProvider("AGENTS.md")

# 多文件（按顺序合并：后者追加，不覆盖）
guide = FileGuideProvider(["AGENTS.md", "TEAM_RULES.md", ".claude/skills/coding.md"])
```

**合并规则**：
- `identity`：拼接所有文件的 identity 内容（用换行分隔）
- `capabilities`：合并所有文件的列表项（去重）
- `rules`：合并所有文件的列表项（不去重，保持顺序）
- `constraints`：合并所有文件的列表项（不去重，保持顺序）
- `examples`：合并所有文件的示例（不去重，保持顺序）

### 2.4 不引入 markdown 解析依赖

Python 标准库没有 Markdown 解析器，`FileGuideProvider` 使用**手写的行级解析器**：
- 检测 `#` 前缀判断标题层级
- 检测 `- ` / `* ` 前缀提取列表项
- 简单状态机：跟踪当前所在的 section

如果后续需要更复杂的 Markdown 支持（如嵌套列表、代码块中的 `#` 误识别），用户可替换为基于 `mistune` 等库的实现。这是「先简单、可工作、可替换」的务实策略。

---

## 三、FileGuideProvider 类设计

### 3.1 构造函数

```python
class FileGuideProvider:
    """GuideProvider 的文件系统实现。

    从 AGENTS.md / CLAUDE.md 等 Markdown 文件解析 Agent 指导信息。
    支持单文件或多文件聚合。

    用法::

        guide = FileGuideProvider("AGENTS.md")
        guide = FileGuideProvider(["AGENTS.md", "TEAM_RULES.md"])
        bundle = guide.get_guides(context)
        print(bundle.identity)   # "You are a coding assistant..."
        print(bundle.rules)      # ["规则1", "规则2", ...]
    """

    def __init__(self, paths: Union[str, List[str]]):
        """初始化 FileGuideProvider。

        Args:
            paths: 单个文件路径或文件路径列表。
                   文件不存在时不抛异常（记录 WARNING），
                   get_guides() 时返回空 GuidesBundle。
                   支持相对路径（相对于当前工作目录）和绝对路径。
                   支持 ~ 展开为用户主目录。
        """
```

### 3.2 方法签名

```python
def get_guides(self, context: "GuideContext") -> "GuidesBundle":
    """解析 Markdown 文件，返回 GuidesBundle。

    每次调用都会重新读取文件（支持热更新）。
    如果所有文件都不存在或无法读取，返回空 GuidesBundle。

    Args:
        context: 包含用户请求、系统状态、环境状态的上下文。
                 FileGuideProvider 当前不依赖 context 内容（纯静态文件读取），
                 但接受此参数以满足 GuideProvider Protocol 的签名约定。

    Returns:
        GuidesBundle: 完整的指导集。解析失败时返回默认值（所有字段为空）。
    """
```

**与 batch-03 MdMemory 的对比**：

| 维度 | MdMemory | FileGuideProvider |
|------|----------|-------------------|
| 数据源 | 运行时动态写入 | 启动时文件读取 |
| 方法调用 | read/write/search 高频 | get_guides() 调用一次（会话开始） |
| 缓存策略 | 内存索引（启动时构建，写时更新） | 每次调用重读文件（支持热更新） |
| 索引 | 内存 dict 索引 | 不需要（文件数量少，内容少） |

### 3.3 内部数据结构

```python
# 文件路径列表（展开后的绝对路径）
_paths: List[Path]

# 解析后的内容缓存（可选优化，当前 batch-04 不做缓存）
# _cache: Optional[GuidesBundle]
```

---

## 四、核心实现逻辑

### 4.1 构造函数流程

```
__init__(paths)
  ├── 1. 规范化输入：单字符串 → 单元素列表
  ├── 2. 展开每个路径的 ~
  ├── 3. 转为 Path 对象
  └── 4. 不检查文件是否存在（延迟到 get_guides() 调用时）
```

### 4.2 get_guides() 解析流程

```
get_guides(context)
  ├── 1. 初始化空的 GuidesBundle
  ├── 2. 遍历 _paths
  │      ├── 2a. 文件不存在 → WARNING → 跳过
  │      ├── 2b. 读取文件内容（read_text）
  │      ├── 2c. 调用 _parse_markdown_guides() 解析
  │      └── 2d. 调用 _merge_guides() 合并到 accumulator
  ├── 3. 返回合并后的 GuidesBundle
  └── 4. 如果没有任何文件被成功读取 → WARNING → 返回空 GuidesBundle
```

### 4.3 解析器状态机

```python
def _parse_markdown_guides(self, text: str) -> GuidesBundle:
    """按 Markdown 标题层级解析指导内容。

    状态机：
    - H1 (``# ``): 收集内容到 identity 缓冲区
    - H2 (``## ``): 根据标题关键词判断字段类型，进入对应收集模式
    - H3 (``### ``): 在 examples 模式下开始新的示例分组
    - 列表项 (``- `` / ``* ``): 在 capabilities/rules/constraints 模式下收集
    - 普通文本: 在 identity/examples 模式下收集
    """
```

**状态机状态**：

```
当前 section = "identity" | "capabilities" | "rules" | "constraints" | "examples" | None
当前 example_input / example_output = "" | ""
example 项缓冲区 = []
```

### 4.4 标题匹配逻辑

```python
SECTION_KEYWORDS = {
    "capabilities": ["能力", "capabilit", "技能", "skill"],
    "rules": ["规则", "rule", "行为", "behavior", "behaviour"],
    "constraints": ["约束", "constraint", "限制", "limit"],
    "examples": ["示例", "example", "范例", "sample"],
}

def _identify_section(self, heading: str) -> Optional[str]:
    """根据 H2 标题文本，判断属于哪个 GuidesBundle 字段。
    
    Args:
        heading: H2 标题文本（已去除 ## 前缀和首尾空白）。
    
    Returns:
        字段名（"capabilities" | "rules" | "constraints" | "examples"），
        无法识别时返回 None。
    """
    heading_lower = heading.lower()
    for field, keywords in self.SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in heading_lower:
                return field
    return None
```

### 4.5 多文件合并逻辑

```python
def _merge_guides(self, base: GuidesBundle, new: GuidesBundle) -> GuidesBundle:
    """将 new 的内容合并到 base。

    合并规则：
    - identity: 拼接（用空行分隔）
    - capabilities: 合并列表（去重）
    - rules: 追加到末尾（不去重，保持顺序）
    - constraints: 追加到末尾（不去重，保持顺序）
    - examples: 追加到末尾（不去重，保持顺序）
    """
```

---

## 五、错误处理与边界条件

| 场景 | 行为 |
|------|------|
| 所有文件不存在 | 返回空 GuidesBundle（所有字段为默认值），记录 WARNING |
| 部分文件不存在 | 跳过缺失文件，解析存在的文件，记录 WARNING |
| 文件存在但为空 | 返回空 GuidesBundle（或从其他文件合并的结果） |
| 文件编码非 UTF-8 | 记录 WARNING，跳过该文件 |
| H2 标题无法匹配任何字段 | 跳过该 section 的内容（不报错） |
| 示例 section 下无 `###` 子标题 | 跳过该 section（不生成 Example） |
| 示例 section 下只有输入没有输出 | 该 Example 的 output 为空字符串 |
| paths 为空列表 | 记录 WARNING，返回空 GuidesBundle |
| paths 参数为 None | TypeError（构造函数不接受 None） |
| 文件内容只有 frontmatter（无 H1） | identity 为空字符串 |

---

## 六、文件布局

### 6.1 产出文件

```
harness/components/guide_provider/
├── __init__.py                   # 导出 FileGuideProvider
└── file_guide_provider.py       # FileGuideProvider 完整实现
```

### 6.2 测试文件

```
tests/
└── test_guide_provider.py       # FileGuideProvider 单元测试
```

### 6.3 模块依赖关系（batch-04 内部）

```
harness/interfaces/types.py            ← GuidesBundle, Example, EnvState, SystemState, UserRequest（已存在，不修改）
harness/interfaces/guide_provider.py   ← GuideProvider Protocol, GuideContext（已存在，不修改）
    ↑
harness/components/guide_provider/file_guide_provider.py  ← batch-04 新增
    ↑
tests/test_guide_provider.py            ← batch-04 新增
```

### 6.4 测试 fixtures 约定

测试使用 pytest 的 `tmp_path` fixture 创建临时 Markdown 文件：

```python
import pytest
from pathlib import Path

@pytest.fixture
def sample_agents_md(tmp_path: Path) -> Path:
    """创建示例 AGENTS.md 文件。"""
    content = """# You are a test assistant.

## 能力
- 编写测试
- 调试代码

## 规则
- 总是先写测试
- 保持代码简洁

## 约束
- 不修改生产环境
"""
    filepath = tmp_path / "AGENTS.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath
```

---

## 七、与前后批次的接口约定

### 7.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| `GuidesBundle` dataclass | batch-02 `interfaces/types.py` | `get_guides()` 返回类型 |
| `Example` dataclass | batch-02 `interfaces/types.py` | examples 列表元素 |
| `GuideContext` dataclass | batch-02 `interfaces/guide_provider.py` | `get_guides()` 输入参数 |
| `GuideProvider` Protocol | batch-02 `interfaces/guide_provider.py` | `FileGuideProvider` 满足此 Protocol（duck typing） |
| `SystemState` dataclass | batch-02 `interfaces/types.py` | GuideContext 内嵌字段 |
| DI 容器 | batch-01 `core/container.py` | 用户通过 `container.register(GuideProvider, FileGuideProvider(...))` 注册 |

### 7.2 为后续批次提供的基础

| 被使用方 | 批次 | 使用方式 |
|---------|------|---------|
| 编排器 Phase 1 | batch-01（已有） | 调用 `guide_provider.get_guides(context)` 获取 GuidesBundle |
| ContextAssembler | batch-05 | 消费 `AssemblyContext.guides`（GuidesBundle），将其融入 system prompt |

### 7.3 DI 装配示例

```python
from harness.core.container import DIContainer
from harness.interfaces import GuideProvider
from harness.components.guide_provider import FileGuideProvider

# 创建 FileGuideProvider 实例
guide = FileGuideProvider(["AGENTS.md", "TEAM_RULES.md"])

# 注册到容器
container = DIContainer()
container.register(GuideProvider, guide)
```

---

## 八、关键设计决策汇总

| # | 决策 | 权衡与理由 |
|---|------|-----------|
| 1 | Markdown 标题层级解析，而非 YAML/TOML | AGENTS.md / CLAUDE.md 已是社区惯例；标题层级自然映射到 Guide 结构 |
| 2 | H1 → identity，H2 关键词匹配 → 其他字段 | 简单直观；用户无需学习新格式 |
| 3 | 中英文标题关键词均支持 | Harness 面向中文开发者社区 |
| 4 | 多文件聚合按顺序合并 | 后加载的文件可追加规则/约束（不覆盖），符合直觉 |
| 5 | 每次 `get_guides()` 重新读取文件（不缓存） | 支持热更新；guide 文件通常很小（<10KB），性能开销可忽略 |
| 6 | 手写行级 Markdown 解析器 | 零外部依赖；guide 文件格式简单，不需要完整 Markdown 解析器 |
| 7 | 文件缺失不崩溃 | GuideProvider 是可选组件；文件缺失时降级为空 GuidesBundle 比崩溃好 |
| 8 | 解析失败静默跳过（WARNING 记录） | 不要因为一个格式错误让整个 Agent 启动失败 |
| 9 | 列表项用 `- ` 和 `* ` 前缀识别 | 兼容两种常见 Markdown 列表风格 |
| 10 | examples 通过 `### ` 子标题分组 | 每个示例独立，结构清晰；支持输入/输出配对 |
