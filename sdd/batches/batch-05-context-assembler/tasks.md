# batch-05 — ContextAssembler 默认实现 任务清单

> 按顺序逐条执行，完成后勾选。

---

## 阶段 1：创建目录结构与模块骨架

### Task-5.1：创建 `harness/components/context_assembler/` 包

- [ ] 确保 `harness/components/` 目录存在（batch-03/batch-04 已创建）
- [ ] 创建 `harness/components/context_assembler/` 目录
- [ ] 创建 `harness/components/context_assembler/__init__.py`

**产出**：
```
harness/components/
├── __init__.py
├── memory_backend/          ← batch-03 已创建
│   └── ...
├── guide_provider/          ← batch-04 已创建
│   └── ...
└── context_assembler/       ← batch-05 新增
    └── __init__.py
```

**验证方式**：`python -c "import harness.components.context_assembler"` 无错误

---

### Task-5.2：创建 `simple_assembler.py` 模块骨架

- [ ] 创建 `harness/components/context_assembler/simple_assembler.py`
- [ ] 添加文件 docstring
- [ ] 添加 `SimpleAssembler` 类定义和构造函数签名（含 `__init__` 文档字符串）
- [ ] 添加 `assemble(inputs)` 方法骨架（返回空 `List[Message]`）
- [ ] 在 `__init__.py` 中导出 `SimpleAssembler`

**产出**：
```python
# harness/components/context_assembler/__init__.py
from .simple_assembler import SimpleAssembler

__all__ = ["SimpleAssembler"]
```

**验证方式**：`from harness.components.context_assembler import SimpleAssembler` 无 ImportError

---

## 阶段 2：System prompt 格式化

### Task-5.3：实现 `_render_guides()` — GuidesBundle 渲染

在 `simple_assembler.py` 中实现：

- [ ] `_render_guides(self, guides: GuidesBundle) -> str`
- [ ] identity 非空时输出 `# Identity\n\n{内容}\n\n`
- [ ] capabilities 非空列表时输出 `# Capabilities\n\n- {item1}\n- {item2}\n...\n\n`
- [ ] rules 非空列表时输出 `# Rules\n\n- {rule1}\n- {rule2}\n...\n\n`
- [ ] constraints 非空列表时输出 `# Constraints\n\n- {constraint1}\n...\n\n`
- [ ] examples 非空列表时输出 `# Examples\n\n## Example 1\nInput:...\nOutput:...\n\n`
- [ ] 空字段（空字符串/空列表）跳过，不输出空标题
- [ ] 所有字段为空时返回空字符串

**Examples 子渲染格式**：
```
## Example 1: {title 或 "Example N"}

输入:
{input}

输出:
{output}
```

**验证方式**：单元测试 — 填充全部字段的 GuidesBundle，仅 identity 的 GuidesBundle，空 GuidesBundle

---

### Task-5.4：实现 `_render_example()` — 单个 Example 渲染

- [ ] `_render_example(cls, example: Example, index: int) -> str`
- [ ] 如果有 title，使用 title；否则使用 `"Example {index}"`
- [ ] 渲染为 `## Example N: {title}\n\n输入:\n{input}\n\n输出:\n{output}\n\n`
- [ ] 处理 `input` 或 `output` 为空的 Example（不崩溃）

**验证方式**：单元测试 — 有 title / 无 title 的 Example，空字段 Example

---

## 阶段 3：Tool 格式化

### Task-5.5：实现 `_render_tools()` — ToolDefinition 列表渲染

在 `simple_assembler.py` 中实现：

- [ ] `_render_tools(self, tools: List[ToolDefinition]) -> str`
- [ ] 非空列表时输出 `# Available Tools\n\n`
- [ ] 每个 tool 渲染为 `- **{name}**({参数签名}) — {description}\n`
- [ ] 参数签名从 `ToolDefinition.parameters` dict 生成，格式 `name: type, name: type`
- [ ] 无参数的 tool 渲染为 `**{name}**() — {description}`
- [ ] 空列表时返回空字符串

**渲染示例**：
```markdown
# Available Tools

- **read_file**(path: string) — 读取文件内容
- **write_file**(path: string, content: string) — 写入文件
- **list_dir**() — 列出目录内容
```

**验证方式**：单元测试 — 单 tool、多 tool、无参数 tool、空列表

---

## 阶段 4：Memory 格式化

### Task-5.6：实现 `_render_memories()` — MemoryItem 列表渲染

在 `simple_assembler.py` 中实现：

- [ ] `_render_memories(self, memories: List[MemoryItem]) -> str`
- [ ] 非空列表时输出 `# Relevant Memories\n\n`
- [ ] 每个 MemoryItem 渲染为 `- [{namespace}/{key}] {value 前 200 字符摘要}...\n`
- [ ] value 超过 200 字符时截断并追加 `...`
- [ ] value 小于等于 200 字符时不截断、不追加 `...`
- [ ] 空列表时返回空字符串

**渲染示例**：
```markdown
# Relevant Memories

- [episodic/session-001] 对话摘要：用户询问了 Python async/await...
- [semantic/user-pref] 用户偏好 Python，使用 black 格式化
```

**验证方式**：单元测试 — 单 memory、多 memory、超 200 字符截断、空列表

---

### Task-5.7：实现 `_custom_retrieval()` — 可选 MemoryBackend 定制检索

在 `simple_assembler.py` 中实现：

- [ ] `_custom_retrieval(self, user_request: UserRequest, existing_memories: List[MemoryItem]) -> List[MemoryItem]`
- [ ] 如果 `self._memory is None`，直接返回 `existing_memories` 的副本
- [ ] 如果 `self._memory` 不为 None，对 `semantic` 和 `procedural` namespace 各执行一次 `search()`
- [ ] 与 `existing_memories` 合并去重（按 `key` 判断）
- [ ] `_memory.search()` 抛异常时：捕获异常，记录 WARNING，降级返回 `existing_memories`

**合并去重规则**：
- 以 `existing_memories` 的 key 集合为基准
- 额外检索结果中与已有 key 重复的跳过
- 保留首次出现顺序（existing_memories 在前，新结果追加在后）

**验证方式**：单元测试 — memory=None，memory 注入（正常检索），memory.search() 抛异常降级，key 冲突去重

---

## 阶段 5：滑动窗口

### Task-5.8：实现 `_apply_sliding_window()` — 滑动窗口历史截断

在 `simple_assembler.py` 中实现：

- [ ] `_apply_sliding_window(self, history: List[Message], max_history: int) -> List[Message]`
- [ ] `max_history < 0`：无截断，返回完整 history 副本
- [ ] `max_history == 0`：丢弃所有非 system 消息，仅保留 system 消息
- [ ] `max_history > 0`：保留所有 system 消息 + 最近 `max_history` 条非 system 消息
- [ ] system 消息不计入 `max_history` 配额
- [ ] 返回顺序：system 消息（保持原始相对顺序）+ 截断后的非 system 消息（保持原始相对顺序）

**消息分离规则**：
- system 消息: `m.role == "system"`
- 非 system 消息: `m.role in ("user", "assistant", "tool")` — 所有非 system role 的消息

**边界条件**：
| 场景 | 行为 |
|------|------|
| history 为空列表 | 返回空列表 |
| history 只有 system 消息 | 返回所有 system 消息 |
| max_history > 实际非 system 消息数 | 返回全部（不截断） |
| history 中包含 tool 角色消息 | 与其他非 system 消息一样计数和截断 |
| history 无 system 消息 | 仅截断非 system 消息 |

**验证方式**：单元测试 — 正常截断、max_history=0、max_history<0、全 system 历史、空历史、history 少于 max_history

---

## 阶段 6：实现 assemble() 主方法

### Task-5.9：实现 `assemble()` 主流程

在 `simple_assembler.py` 中实现：

- [ ] `assemble(self, inputs: AssemblyContext) -> List[Message]`
- [ ] **Step 1**: 构建 system 消息
  - 调用 `_render_guides(inputs.guides)`
  - 如果 `_include_memories` 为 True，调用 `_custom_retrieval()` 获取合并后的记忆列表，再调用 `_render_memories()` 渲染
  - 如果 `_include_tools` 为 True，调用 `_render_tools(inputs.available_tools)`
  - 用两个空行（`\n\n\n`）拼接各 section，去除首尾多余空白
  - 创建 `Message(role="system", content=system_text)`
- [ ] **Step 2**: 调用 `_apply_sliding_window(inputs.history, self._max_history)` 截断历史
- [ ] **Step 3**: 构建当前 user 消息 `Message(role="user", content=inputs.user_request.text)`
- [ ] **Step 4**: 组装最终列表 `[system_msg] + history_window + [user_msg]`
- [ ] **Step 5**: 如果 system 消息 content 为空，仍保留该 Message（不跳过）

**组装顺序保证**：
```
[system(guides + memories + tools)] + history(sliding window) + [user(current request)]
```

**边界条件**：
| 场景 | 行为 |
|------|------|
| 所有字段为默认值 | 返回 `[system(""), user("")]` — 两条空消息 |
| history 为空 | 返回 `[system, user]` |
| guides 全空 | system content 仅含 memories/tools（若有） |
| user_request.text 为空 | user message content 为空字符串 |
| inputs 为 None | TypeError（不接受 None） |

**验证方式**：完整组装测试 — 正常 case + 全部边界条件

---

## 阶段 7：编写单元测试

### Task-5.10：创建 `tests/test_simple_assembler.py`

- [ ] 创建测试文件
- [ ] 使用 pytest fixtures 构建标准测试数据

**测试 fixtures**（在测试文件中定义）：

| Fixture | 用途 |
|---------|------|
| `sample_guides` | 填充全部 5 个字段的 GuidesBundle |
| `empty_guides` | 所有字段为默认值的 GuidesBundle |
| `sample_context` | 完整的 AssemblyContext（guides + tools + history + memories） |
| `minimal_context` | 仅含 user_request 的最小 AssemblyContext |
| `sample_tools` | 3 个 ToolDefinition（含有参/无参） |
| `sample_memories` | 3 个 MemoryItem（不同 namespace） |
| `sample_history` | 10 条混合消息（3 system + 7 user/assistant） |

**测试函数清单**（对照 design.md 验收标准）：

### System prompt 格式化测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_render_guides_full` | GuidesBundle 全部 5 个字段填充，验证每个 section 出现 |
| `test_render_guides_identity_only` | 仅 identity 非空，其余字段空 |
| `test_render_guides_empty` | 所有字段空，返回空字符串 |
| `test_render_guides_skips_empty_sections` | 部分字段空时不输出空 section 标题 |
| `test_render_guides_capabilities_format` | capabilities 列表渲染为 `- item` 格式 |
| `test_render_guides_rules_format` | rules 列表渲染为 `- rule` 格式 |
| `test_render_guides_constraints_format` | constraints 列表渲染为 `- constraint` 格式 |
| `test_render_guides_examples_format` | examples 包含 input/output |

### Tool 格式化测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_render_tools_multiple` | 多个 ToolDefinition 渲染 |
| `test_render_tools_single` | 单个 ToolDefinition 渲染 |
| `test_render_tools_no_params` | 无参数 tool 渲染为 `name()` |
| `test_render_tools_empty` | 空列表返回空字符串 |
| `test_render_tools_param_signature` | 多个参数签名正确拼接 |

### Memory 格式化测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_render_memories_multiple` | 多个 MemoryItem 渲染 |
| `test_render_memories_single` | 单个 MemoryItem 渲染 |
| `test_render_memories_truncation` | value > 200 字符时截断并追加 `...` |
| `test_render_memories_no_truncation` | value <= 200 字符时不截断、不追加 `...` |
| `test_render_memories_empty` | 空列表返回空字符串 |
| `test_render_memories_namespace_key_format` | 验证 `[{namespace}/{key}]` 格式 |

### 滑动窗口测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_sliding_window_normal` | max_history=3，7 条非 system → 保留最近 3 条 |
| `test_sliding_window_zero` | max_history=0 丢弃所有非 system 消息 |
| `test_sliding_window_negative` | max_history<0 保留全部 |
| `test_sliding_window_all_system` | history 只有 system 消息，全部保留 |
| `test_sliding_window_preserves_system` | system 消息不计入配额，全部保留 |
| `test_sliding_window_empty_history` | 空 history 返回空列表 |
| `test_sliding_window_less_than_max` | 非 system 消息数 < max_history，全部保留 |
| `test_sliding_window_with_tool_messages` | tool role 消息正常参与窗口计数 |
| `test_sliding_window_order` | 返回顺序：system 在前，非 system 在后 |

### 定制检索测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_custom_retrieval_no_memory` | _memory=None，直接返回 existing_memories |
| `test_custom_retrieval_with_memory` | _memory 注入，执行跨 namespace 检索 |
| `test_custom_retrieval_dedup_keys` | 额外检索结果与 existing 有 key 冲突时去重 |
| `test_custom_retrieval_search_exception` | search() 抛异常时降级返回 existing_memories |

### assemble() 集成测试

| 测试函数 | 覆盖内容 |
|---------|---------|
| `test_assemble_full_context` | 完整 AssemblyContext 组装，验证消息结构和顺序 |
| `test_assemble_minimal_context` | 最小 AssemblyContext，返回 [system(""), user("")] |
| `test_assemble_message_order` | 验证 system → history → user 顺序 |
| `test_assemble_empty_history` | history 为空时仅 system + user |
| `test_assemble_include_tools_false` | include_tools=False 不渲染 tools section |
| `test_assemble_include_memories_false` | include_memories=False 不渲染 memories section |
| `test_assemble_empty_guides` | guides 全空，system 仅含 memories/tools（若有） |
| `test_assemble_empty_user_text` | user_request.text 为空，user message content="" |
| `test_assemble_none_inputs` | inputs=None 抛出 TypeError |
| `test_assemble_default_max_history` | 默认 max_history=50 的构造函数 |
| `test_assemble_custom_max_history` | 自定义 max_history 生效 |
| `test_assemble_system_message_always_first` | 验证 system 消息在列表索引 0 |
| `test_assemble_user_message_always_last` | 验证 user 消息在列表索引 -1 |
| `test_assemble_guides_section_in_system` | guides 渲染内容出现在 system message content 中 |
| `test_assemble_memories_section_in_system` | memories 渲染内容出现在 system message content 中 |
| `test_assemble_tools_section_in_system` | tools 渲染内容出现在 system message content 中 |
| `test_assemble_return_type` | 返回类型为 `List[Message]` |
| `test_assemble_each_message_has_role_and_content` | 每条 Message 有 role 和 content 属性 |

---

## 阶段 8：运行全部测试并修复

### Task-5.11：运行 batch-05 测试

```bash
pytest tests/test_simple_assembler.py -v
```

- [ ] 所有测试通过

### Task-5.12：确保不破坏已有测试

```bash
pytest tests/ --ignore=tests/test_real_llm_trace.py -v
```

- [ ] 所有已有测试仍然通过（batch-05 新增代码不应影响已有行为）

---

## 完成标准

- [ ] `SimpleAssembler` 类完整实现 `assemble()` 方法及全部辅助方法
- [ ] system prompt 渲染（guides + memories + tools）正确
- [ ] 滑动窗口历史截断逻辑正确（system 消息始终保留）
- [ ] 全部 ~40 个测试用例通过
- [ ] 已有测试套件无回归
- [ ] `harness/components/context_assembler/__init__.py` 导出 `SimpleAssembler`
