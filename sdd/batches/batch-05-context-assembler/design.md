# batch-05 — ContextAssembler 默认实现 设计文档

> **目标**：实现 `ContextAssembler` 接口的第一个默认实现 — `SimpleAssembler`（将 Harness 所有信息源组装成发给 LLM 的最终消息列表）。提供滑动窗口历史截断 + 直接拼接 guides/memories/history 的基线组装能力，使框架能跑通「GuidesBundle → system prompt → 对话历史 → LLM 调用」的完整链路。
>
> **依赖**：batch-02-1（正式类型 `AssemblyContext`、`Message`、`ContextAssembler` Protocol），batch-03（`MemoryBackend` 实例，可选注入），batch-04（`GuidesBundle` 类型）
>
> **产出**：`harness/components/context_assembler/simple_assembler.py`

---

## 一、范围与边界

### 1.1 在范围内

| # | 任务 | 说明 |
|---|------|------|
| 1 | **`SimpleAssembler` 类实现** | 实现 `assemble(inputs)` 方法，将 AssemblyContext 转为 `List[Message]` |
| 2 | **Guide → system 消息转换** | 将 GuidesBundle 的 identity/rules/capabilities/constraints/examples 渲染为单条 system Message |
| 3 | **Memory → system 消息追加** | 将 AssemblyContext.memories 列表渲染为附加的 system 内容（可选，由 `include_memories` 控制） |
| 4 | **Tool → system 消息注入** | 将 available_tools 的 ToolDefinition 列表描述注入 system prompt（可选，由 `include_tools` 控制） |
| 5 | **滑动窗口历史截断** | 对 history 列表按消息数量滑动窗口截断，超出 `max_history` 时保留最近 N 条，同时跨窗口保留所有 system 消息 |
| 6 | **消息顺序保证** | 固定输出顺序：system(guides) → history(sliding window) → user |
| 7 | **可选 MemoryBackend 注入** | 构造函数接受可选的 MemoryBackend，用于超越框架基线的定制检索 |
| 8 | **单元测试** | 覆盖组装逻辑 + 滑动窗口 + 边界条件（空历史、空 guides、空 memories 等） |

### 1.2 严格不在范围内

- ❌ 不实现智能上下文窗口（token 计数截断）—— 那是未来的高级 ContextAssembler
- ❌ 不实现消息摘要 / 压缩（summarization）
- ❌ 不修改 `ContextAssembler` Protocol 或 `AssemblyContext` 类型
- ❌ 不修改编排器中对 `ContextAssembler` 的调用逻辑
- ❌ 不实现 Tool 注册（那是 batch-06）
- ❌ 不实现 Sensor（那是 batch-07）
- ❌ 不依赖任何第三方库（仅使用 Python 标准库）

---

## 二、核心设计决策

### 2.1 为什么用滑动窗口（消息计数）而非 Token 截断

| 维度 | 消息计数滑动窗口 | Token 计数截断 |
|------|-----------------|---------------|
| 实现复杂度 | **低**（数消息数量） | 中（需要 tokenizer） |
| 外部依赖 | **零** | 需要 tiktoken 或等效 tokenizer |
| 可预测性 | **高**（精确的消息条数） | 中（token 数依赖模型 + 消息内容） |
| 上下文利用率 | 低（可能浪费或溢出） | **高**（精确控制） |
| system 消息保持 | **天然支持**（保留所有 system） | 需要额外逻辑 |

**选择消息计数滑动窗口的核心理由**：batch-05 是 ContextAssembler 的**基线实现**，目标是"先能跑通"。消息计数截断零外部依赖、实现简单、可预测。用户可在后续替换为基于 token 计数的实现（通过实现 `ContextAssembler` Protocol 并注册到 DI 容器）。这是「先简单、可工作、可替换」的务实策略。

### 2.2 消息组装顺序设计

```
system (guides + memories + tools)
  ↓
history (sliding window)
  ↓
user (当前请求)
```

**为什么 system 在最前面**：LLM API（OpenAI / Anthropic）要求 system 消息出现在对话历史之前。system 消息设定 Agent 身份和行为约束，必须在 user/assistant 交替之前声明。

**为什么 user 在最后面**：每次 `assemble()` 调用对应一轮新的用户输入。将当前 `user_request.text` 作为最后一条 user 消息追加，LLM 收到后将其视为最新一轮对话的起点。

### 2.3 System 消息渲染格式

GuidesBundle 的 5 个字段渲染为单条 system Message：

```markdown
# Identity

You are a coding assistant specialized in Python development.
...

# Capabilities

- 编写和审查 Python 代码
- 调试和性能优化

# Rules

- 所有代码必须通过 mypy 类型检查
- 优先使用标准库而非第三方依赖

# Constraints

- 绝不修改 .git 目录下的文件
- 绝不执行未经确认的删除操作

# Examples

## Example 1: 代码审查
Input: ...
Output: ...
```

**如果某字段为空列表/空字符串，跳过该 section**（不输出空标题）。

**Memories 追加到 system 末尾**（当 `include_memories=True`）：

```markdown
# Relevant Memories

- [episodic/session-001] 对话摘要：用户询问了 Python async/await...
- [semantic/user-pref] 用户偏好 Python，使用 black 格式化
```

**Tools 追加到 system 末尾**（当 `include_tools=True`）：

```markdown
# Available Tools

- **read_file**(path: str) — 读取文件内容
- **write_file**(path: str, content: str) — 写入文件
...
```

### 2.4 滑动窗口逻辑

```python
max_history = 50  # 保留最近 50 条消息
```

**窗口规则**：

1. 遍历 history 列表，**始终保留所有 system 消息**（role == "system"）
2. 对非 system 消息（user/assistant/tool），保留最近 `max_history` 条
3. system 消息不计入 `max_history` 配额
4. system 消息始终放在 history 段的最前面（位于 guides system message 之后）

**示例**：假设 `max_history=10`，history 有 3 条 system + 25 条 user/assistant/tool = 28 条总计：
- 保留：所有 3 条 system + 最近 10 条 user/assistant/tool = 13 条
- 丢弃：最早的 15 条 user/assistant/tool

### 2.5 MemoryBackend 可选注入

遵循 SDD §02 的设计约定：

- **框架基线**：编排器在每轮外层循环前自动执行 `memory.search(user_request.text, namespace="episodic")`，结果填入 `AssemblyContext.memories`。`SimpleAssembler` 的最低行为只需消费 `AssemblyContext.memories`。
- **组件增强**：当用户通过构造函数注入 `MemoryBackend` 实例时，`SimpleAssembler` 可在 `assemble()` 内执行超越框架基线的定制检索（如跨 namespace 检索 `semantic`/`procedural`、使用不同 query 策略），并自行决定如何与 `AssemblyContext.memories` 合并/去重。

```python
# 仅消费框架基线（最常见用法）
assembler = SimpleAssembler(max_history=50)

# 注入 MemoryBackend 以执行定制检索
assembler = SimpleAssembler(max_history=50, memory=md_memory_instance)
```

---

## 三、SimpleAssembler 类设计

### 3.1 构造函数

```python
class SimpleAssembler:
    """ContextAssembler 的基线实现。

    将 GuidesBundle、memories、tools、history 组装为发给 LLM 的 Message 列表。
    使用滑动窗口按消息数量截断历史，保留所有 system 消息。

    用法::

        assembler = SimpleAssembler(max_history=50)
        assembler = SimpleAssembler(max_history=50, memory=memory_instance)
        messages = assembler.assemble(assembly_context)
        # messages[0].role == "system"  # guides + memories + tools
        # messages[-1].role == "user"   # 当前用户请求
    """

    def __init__(
        self,
        max_history: int = 50,
        memory: Optional["MemoryBackend"] = None,
        include_tools: bool = True,
        include_memories: bool = True,
    ):
        """初始化 SimpleAssembler。

        Args:
            max_history: 滑动窗口大小（保留最近 N 条非 system 消息）。
                         默认为 50。设为 0 表示不保留任何历史。
                         设为负数表示无限制（保留全部历史）。
            memory: 可选 MemoryBackend 实例，用于超越框架基线的定制检索。
                    为 None 时仅消费 AssemblyContext.memories（框架基线结果）。
            include_tools: 是否在 system prompt 中包含 available_tools 描述。
                           默认为 True。
            include_memories: 是否在 system prompt 中包含 memories。
                              默认为 True。即使为 False，通过 memory 参数注入的
                              定制检索结果仍会被包含（如果 memory 不为 None）。
        """
```

### 3.2 方法签名

```python
def assemble(self, inputs: "AssemblyContext") -> List["Message"]:
    """将 AssemblyContext 组装为 LLM 消息列表。

    组装顺序：system(guides) → history(sliding window) → user

    Args:
        inputs: AssemblyContext，包含 guides、history、memories、
                available_tools、user_request 等信息源。

    Returns:
        List[Message]: 组装后的消息列表，可直接发给 LLM。
                       最少包含 1 条 system + 1 条 user 消息。
    """
```

### 3.3 内部数据结构

```python
# 滑动窗口配置
_max_history: int

# 可选 MemoryBackend 引用（为 None 时不执行定制检索）
_memory: Optional[MemoryBackend]

# 控制开关
_include_tools: bool
_include_memories: bool
```

**无缓存**：`assemble()` 每次调用都重新构建消息列表。AssemblyContext 的内容每轮都可能变化（history 增长、memories 更新），缓存无意义。

---

## 四、核心实现逻辑

### 4.1 assemble() 主流程

```
assemble(inputs)
  ├── 1. 构建 system 消息
  │      ├── 1a. 渲染 GuidesBundle → system 消息文本（_render_guides()）
  │      ├── 1b. 如果 include_memories=True，追加 AssemblyContext.memories（_render_memories()）
  │      ├── 1c. 如果 _memory 不为 None，执行定制检索并追加（_custom_retrieval()）
  │      ├── 1d. 如果 include_tools=True，追加 available_tools 描述（_render_tools()）
  │      └── 1e. 创建 system Message
  ├── 2. 应用滑动窗口截断 history（_apply_sliding_window()）
  │      ├── 2a. 分离 system 消息和非 system 消息
  │      ├── 2b. 从非 system 消息中取最近 max_history 条
  │      └── 2c. 合并：所有 system 消息（原始） + 截断后的非 system 消息
  ├── 3. 构建当前 user 消息（user_request.text）
  ├── 4. 组装最终列表：[system] + history_window + [user]
  └── 5. 返回 List[Message]
```

### 4.2 GuidesBundle 渲染逻辑

```python
def _render_guides(self, guides: GuidesBundle) -> str:
    """将 GuidesBundle 渲染为 system prompt 文本。

    对每个非空字段，生成对应的 Markdown section：
    - identity → "# Identity\n{内容}"
    - capabilities → "# Capabilities\n- item1\n- item2\n..."
    - rules → "# Rules\n- rule1\n- rule2\n..."
    - constraints → "# Constraints\n- constraint1\n..."
    - examples → "# Examples\n## Example 1\nInput:...\nOutput:..."

    空字段（空字符串/空列表）跳过，不输出空标题。

    Returns:
        str: 渲染后的 Markdown 文本。如果所有字段为空，返回空字符串。
    """
```

### 4.3 Memories 渲染逻辑

```python
def _render_memories(self, memories: List[MemoryItem]) -> str:
    """将记忆列表渲染为 system prompt 追加文本。

    格式：
    # Relevant Memories

    - [{namespace}/{key}] {value 的前 200 字符摘要}...

    空列表时返回空字符串。

    Returns:
        str: 渲染后的 Markdown 文本。
    """
```

### 4.4 Tools 渲染逻辑

```python
def _render_tools(self, tools: List[ToolDefinition]) -> str:
    """将工具定义列表渲染为 system prompt 追加文本。

    格式：
    # Available Tools

    - **{name}**({parameters}) — {description}
    ...

    空列表时返回空字符串。

    Returns:
        str: 渲染后的 Markdown 文本。
    """
```

### 4.5 滑动窗口逻辑

```python
def _apply_sliding_window(
    self, history: List[Message], max_history: int
) -> List[Message]:
    """对历史消息应用滑动窗口截断。

    规则：
    1. system 消息（role == "system"）始终保留，不计入窗口配额
    2. 非 system 消息保留最近 max_history 条
    3. 如果 max_history <= 0:
       - max_history == 0: 丢弃所有非 system 消息（仅保留 system）
       - max_history < 0: 保留全部（无截断）
    4. 返回顺序：system 消息（保持原始相对顺序）+ 截断后的非 system 消息

    Args:
        history: 原始历史消息列表。
        max_history: 滑动窗口大小。

    Returns:
        List[Message]: 截断后的消息列表。
    """
    if max_history < 0:
        return list(history)  # 无限制

    system_msgs = [m for m in history if m.role == "system"]
    non_system_msgs = [m for m in history if m.role != "system"]

    if max_history == 0:
        return system_msgs

    # 保留最近 max_history 条非 system 消息
    truncated = non_system_msgs[-max_history:]

    return system_msgs + truncated
```

### 4.6 定制检索逻辑（可选）

```python
def _custom_retrieval(
    self, user_request: UserRequest, existing_memories: List[MemoryItem]
) -> List[MemoryItem]:
    """如果构造函数注入了 MemoryBackend，执行超越框架基线的定制检索。

    默认策略：跨 semantic 和 procedural namespace 做额外检索，
    与 AssemblyContext.memories 合并（去重，按 key 判断）。

    用户可通过子类化覆盖此方法实现完全不同的检索策略。

    Args:
        user_request: 当前用户请求。
        existing_memories: 框架基线检索结果（AssemblyContext.memories）。

    Returns:
        List[MemoryItem]: 合并去重后的记忆列表。
    """
    if self._memory is None:
        return list(existing_memories)

    extra_memories: List[MemoryItem] = []
    for ns in ["semantic", "procedural"]:
        results = self._memory.search(user_request.text, namespace=ns, limit=5)
        extra_memories.extend(results)

    # 去重合并
    seen_keys = {m.key for m in existing_memories}
    merged = list(existing_memories)
    for m in extra_memories:
        if m.key not in seen_keys:
            merged.append(m)
            seen_keys.add(m.key)

    return merged
```

---

## 五、错误处理与边界条件

| 场景 | 行为 |
|------|------|
| AssemblyContext 所有字段为默认值 | 返回 `[system(""), user("")]` — 两条空消息。不崩溃 |
| GuidesBundle 所有字段为空 | system 消息 content 为空字符串。不输出任何 section 标题 |
| history 为空列表 | 不追加历史消息。最终列表仅含 system + user |
| history 只有 system 消息 | 保留所有 system 消息。最终列表：system(guides) + system(history) + user |
| max_history=0 | 丢弃所有非 system 历史消息，仅保留 system 消息 |
| max_history < 0 | 无截断，保留全部历史 |
| max_history > 实际非 system 消息数 | 保留全部，不多做处理 |
| memories 为空列表 | 不输出 "Relevant Memories" section |
| available_tools 为空列表 | 不输出 "Available Tools" section |
| include_memories=False | 跳过 memories 渲染，但 _custom_retrieval() 结果仍会被包含（如果 memory 不为 None） |
| include_tools=False | 跳过 tools 渲染 |
| memory=None | 不执行定制检索，仅消费 AssemblyContext.memories |
| memory 注入但 search() 抛异常 | 捕获异常，记录 WARNING，降级为仅使用 AssemblyContext.memories |
| user_request.text 为空字符串 | 生成 content="" 的 user Message |
| history 中包含 tool_call_id 的 tool 消息 | 正常保留在窗口中（与其他 role 一样计数） |
| inputs 参数为 None | TypeError（不接受 None） |

---

## 六、文件布局

### 6.1 产出文件

```
harness/components/context_assembler/
├── __init__.py                   # 导出 SimpleAssembler
└── simple_assembler.py           # SimpleAssembler 完整实现
```

### 6.2 测试文件

```
tests/
└── test_simple_assembler.py      # SimpleAssembler 单元测试
```

### 6.3 模块依赖关系（batch-05 内部）

```
harness/interfaces/types.py                    ← AssemblyContext, Message, MemoryItem,
                                                  GuidesBundle, ToolDefinition, UserRequest
                                                  （已存在，不修改）
harness/interfaces/context_assembler.py         ← ContextAssembler Protocol（已存在，不修改）
harness/interfaces/memory_backend.py            ← MemoryBackend Protocol（已存在，不修改）
    ↑
harness/components/context_assembler/simple_assembler.py  ← batch-05 新增
    ↑
tests/test_simple_assembler.py                  ← batch-05 新增
```

### 6.4 测试 fixtures 约定

测试使用 pytest 的 fixture 创建标准 AssemblyContext 实例：

```python
import pytest
from harness.interfaces.types import (
    AssemblyContext, UserRequest, GuidesBundle, Message,
    MemoryItem, SystemState, ToolDefinition,
)

@pytest.fixture
def sample_context() -> AssemblyContext:
    """创建包含所有字段填充的 AssemblyContext。"""
    return AssemblyContext(
        user_request=UserRequest(
            text="请帮我写一个 Python 函数",
            system_state=SystemState(
                phase="loop", session_id="test-001", run_mode="normal"
            ),
            session_id="test-001",
        ),
        guides=GuidesBundle(
            identity="You are a coding assistant.",
            rules=["总是先写测试", "保持代码简洁"],
        ),
        available_tools=[
            ToolDefinition(
                name="read_file",
                description="读取文件",
                parameters={"path": {"type": "string"}},
            ),
        ],
        history=[
            Message(role="system", content="会话开始"),
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好！有什么可以帮助你的？"),
        ],
        memories=[
            MemoryItem(
                key="pref-001",
                value="用户偏好 Python",
                namespace="semantic",
                timestamp=1717430400.0,
            ),
        ],
        system_state=SystemState(
            phase="loop", session_id="test-001", run_mode="normal"
        ),
    )
```

---

## 七、与前后批次的接口约定

### 7.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| `AssemblyContext` dataclass | batch-02 `interfaces/types.py` | `assemble()` 输入参数类型 |
| `Message` dataclass | batch-02 `interfaces/types.py` | `assemble()` 返回类型 `List[Message]` |
| `GuidesBundle` dataclass | batch-02 `interfaces/types.py` | 渲染 system prompt 的输入 |
| `MemoryItem` dataclass | batch-02 `interfaces/types.py` | 渲染 memories section |
| `ToolDefinition` dataclass | batch-02 `interfaces/types.py` | 渲染 tools section |
| `UserRequest` dataclass | batch-02 `interfaces/types.py` | 提取 user_request.text |
| `ContextAssembler` Protocol | batch-02 `interfaces/context_assembler.py` | SimpleAssembler 满足此 Protocol（duck typing） |
| `MemoryBackend` Protocol | batch-02 `interfaces/memory_backend.py` | 构造函数可选注入的类型标注 |
| DI 容器 | batch-01 `core/container.py` | 用户通过 `container.register(ContextAssembler, SimpleAssembler(...))` 注册 |

### 7.2 为后续批次提供的基础

| 被使用方 | 批次 | 使用方式 |
|---------|------|---------|
| 编排器 Phase 2（外层循环） | batch-01（已有） | 每轮循环调用 `context_assembler.assemble(ctx)`，将返回的 `List[Message]` 传给 LLM adapter |
| 编排器 Phase 2（内层循环） | batch-01（已有） | **不调用** ContextAssembler — tool result 直接追加到 message list |
| DI 装配 + 集成测试 | batch-10 | SimpleAssembler 作为 ContextAssembler 默认实现被装配到完整管线 |

### 7.3 DI 装配示例

```python
from harness.core.container import DIContainer
from harness.interfaces import ContextAssembler, MemoryBackend
from harness.components.context_assembler import SimpleAssembler
from harness.components.memory_backend import MdMemory

# 创建 MemoryBackend 实例（可选：SimpleAssembler 默认不需要）
memory = MdMemory(path="./my_agent_memory")

# 创建 SimpleAssembler 实例（不注入 MemoryBackend，仅消费框架基线）
assembler = SimpleAssembler(max_history=50)

# 或：注入 MemoryBackend 以启用跨 namespace 定制检索
assembler = SimpleAssembler(max_history=50, memory=memory)

# 注册到容器
container = DIContainer()
container.register(ContextAssembler, assembler)
container.register(MemoryBackend, memory)  # 编排器也需要 MemoryBackend 实例
```

---

## 八、关键设计决策汇总

| # | 决策 | 权衡与理由 |
|---|------|-----------|
| 1 | 消息计数滑动窗口而非 Token 截断 | 零外部依赖；实现简单可预测；batch-05 基线能力，后续可替换为 token 计数的实现 |
| 2 | system 消息不计入窗口配额 | system 消息量小且关键（身份定义、规则约束），跨窗口保留确保行为一致性 |
| 3 | GuidesBundle 渲染为单条 system 消息 | LLM API 要求 system 在最前面；单条消息减少 API 调用开销 |
| 4 | 空字段跳过不渲染 | 避免空标题污染 system prompt；减少无意义的 token 消耗 |
| 5 | MemoryBackend 可选注入 | 遵循 SDD §02 设计约定：框架基线 + 组件增强双模式 |
| 6 | `include_tools` / `include_memories` 开关 | 给用户精细控制权；某些场景不需要工具描述或记忆（如简单问答） |
| 7 | 定制检索降级策略 | `_memory.search()` 异常时不崩溃，降级为仅使用框架基线结果 |
| 8 | history 中原始 system 消息保留并排在前面 | 用户可能在对话中插入 system 级指令；窗口截断不应丢失这些关键消息 |
| 9 | 无缓存设计 | AssemblyContext 每轮变化，缓存无意义且可能导致过期数据 |
| 10 | user 消息放在消息列表最后 | LLM 将最后一条 user 消息视为当前轮次的起点 |
