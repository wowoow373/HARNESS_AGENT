# batch-05 — ContextAssembler 默认实现 验收标准

> 对照 [design.md](design.md) 的验收清单。所有标准必须在 batch-05 完成时通过。

---

## 一、功能验收

### AC-CA-01：`assemble()` 基本组装功能

- [ ] **AC-CA-01.1**：包含 GuidesBundle 时，输出首条 Message 的 role 为 "system"，内容包含 identity 文本
- [ ] **AC-CA-01.2**：包含 GuidesBundle.rules 时，system 消息中包含 rules 列表内容
- [ ] **AC-CA-01.3**：包含 UserRequest 时，最终输出最后一条 Message 的 role 为 "user"，内容为 UserRequest.text
- [ ] **AC-CA-01.4**：包含对话历史时，history 消息出现在 system 消息之后、user 消息之前，保持原有 role 和 content
- [ ] **AC-CA-01.5**：`assemble()` 返回值类型为 `List[Message]`，每个元素为 `Message` 实例

### AC-CA-02：系统消息格式化

- [ ] **AC-CA-02.1**：GuidesBundle 为空（所有字段默认值）时，仍创建 system 消息（含默认/空内容），不崩溃
- [ ] **AC-CA-02.2**：GuidesBundle 包含全部 5 个字段（identity、capabilities、rules、constraints、examples）时，所有字段内容均出现在 system 消息中
- [ ] **AC-CA-02.3**：`inputs.guides = None` 时，仍创建 system 消息，不崩溃，不抛异常
- [ ] **AC-CA-02.4**：identity 含 emoji、中文标点、特殊 Unicode 字符时，内容被完整保留

### AC-CA-03：滑动窗口截断

- [ ] **AC-CA-03.1**：history 消息数 ≤ max_history 时，所有历史消息均保留
- [ ] **AC-CA-03.2**：history 消息数 > max_history 时，最旧的非 system 消息被丢弃，最近 N 条保留
- [ ] **AC-CA-03.3**：system role 的消息**始终保留**，不参与截断计数
- [ ] **AC-CA-03.4**：多条 system 消息全部保留，其余非 system 消息按窗口截断
- [ ] **AC-CA-03.5**：`max_history=0` 时，仅保留 system 消息 + 当前 user 消息
- [ ] **AC-CA-03.6**：`max_history=1`、且无 system 消息时，仅保留最近 1 条历史消息 + user 消息
- [ ] **AC-CA-03.7**：history 为空列表时，不崩溃，user 消息仍在输出中

---

## 二、工具格式化验收

### AC-CA-04：available_tools 注入

- [ ] **AC-CA-04.1**：available_tools 包含条目时，每个 Tool 的 name 和 description 出现在 system 消息中
- [ ] **AC-CA-04.2**：available_tools 为空列表时，system 消息中显示 "(none)" 或等同的无工具指示
- [ ] **AC-CA-04.3**：`include_tools=False` 时，即使 available_tools 有内容也不注入 system prompt

---

## 三、记忆注入验收

### AC-CA-05：Memory 插入

- [ ] **AC-CA-05.1**：memories 包含条目时，所有 MemoryItem 内容出现在 system 消息中
- [ ] **AC-CA-05.2**：memories 为空列表时，system 消息中不包含记忆 section
- [ ] **AC-CA-05.3**：`include_memories=False` 时，即使 memories 有内容也不注入
- [ ] **AC-CA-05.4**：多条 MemoryItem 全部按顺序包含在 system 消息中

### AC-CA-06：可选 MemoryBackend 注入

- [ ] **AC-CA-06.1**：未注入 MemoryBackend（`memory=None`）时，`assemble()` 仅消费 `AssemblyContext.memories`（框架基线），正常工作
- [ ] **AC-CA-06.2**：注入 MemoryBackend 后，`assemble()` 可调用 `memory.search()` 执行额外跨 namespace 检索，结果合并到 system 消息
- [ ] **AC-CA-06.3**：MemoryBackend 已注入但 `include_memories=False` 时，不执行额外检索（尊重用户开关）
- [ ] **AC-CA-06.4**：MemoryBackend.search() 抛异常时，不崩溃，降级为仅使用框架基线 `AssemblyContext.memories`，记录 WARNING

---

## 四、边界与容错验收

### AC-CA-07：边界输入处理

- [ ] **AC-CA-07.1**：AssemblyContext 所有可选字段均为 None（`user_request=None, guides=None, history=[], memories=[], tools=[]`），返回单条 system role 消息 + 单条空 user 消息，不崩溃
- [ ] **AC-CA-07.2**：UserRequest.text 为空字符串时，最终 user 消息的 content 为 ""
- [ ] **AC-CA-07.3**：identity 文本极长（5000+ 字符）时，不做截断，完整保留
- [ ] **AC-CA-07.4**：history 包含 tool role 消息时，消息的 role 和 tool_call_id 在截断后保持正确

---

## 五、接口符合性验收

### AC-CA-08：Protocol 符合性

- [ ] **AC-CA-08.1**：`SimpleAssembler` 实例可通过 `isinstance(assembler, ContextAssembler)` 检查（`@runtime_checkable`）
- [ ] **AC-CA-08.2**：`SimpleAssembler` 可成功注册到 DI 容器：`container.register(ContextAssembler, SimpleAssembler())`
- [ ] **AC-CA-08.3**：`assemble()` 方法签名与 `ContextAssembler` Protocol 一致（接收 `AssemblyContext`，返回 `List[Message]`）

### AC-CA-09：类型正确性

- [ ] **AC-CA-09.1**：`assemble()` 返回值类型为 `List[Message]`
- [ ] **AC-CA-09.2**：输出中的每条 Message 均为 `Message` 实例，role 为有效值（"system"|"user"|"assistant"|"tool"）
- [ ] **AC-CA-09.3**：system 消息中不包含 tool_call_id（role="system" 时 tool_call_id 为 None）

---

## 六、端到端集成验收

### AC-CA-10：E2E with MdMemory

- [ ] **AC-CA-10.1**：创建 MdMemory（tmp_path），写入若干 memory → 创建 SimpleAssembler(memory=md_memory) → 构建 AssemblyContext（含 memrory + guides + history） → 调用 assemble() → 验证输出中同时包含记忆内容和 guides 内容
- [ ] **AC-CA-10.2**：MdMemory 中无匹配记忆时，system 消息仅包含 guides 内容，不崩溃

### AC-CA-11：E2E with FileGuideProvider

- [ ] **AC-CA-11.1**：创建临时 AGENTS.md 文件（含完整 H1 + rules + constraints）→ FileGuideProvider 解析 → GuidesBundle 传入 AssemblyContext → SimpleAssembler.assemble() → 验证 system 消息中包含 identity、rules、constraints
- [ ] **AC-CA-11.2**：临时 AGENTS.md 为空文件 → FileGuideProvider 返回空 GuidesBundle → SimpleAssembler 不崩溃，返回默认 system 消息

### AC-CA-12：完整管线 E2E

- [ ] **AC-CA-12.1**：FileGuideProvider + MdMemory + SimpleAssembler 全管线：创建 AGENTS.md（含 identity + rules）→ FileGuideProvider.get_guides() → 创建 MdMemory + 写入记忆 → MdMemory.search() → 构建 AssemblyContext（guides=guides, memories=search_results, history=[若干 message], user_request=user） → SimpleAssembler.assemble() → 验证输出消息列表结构和内容正确

---

## 七、回归验收

### AC-CA-13：已有测试无回归

- [ ] **AC-CA-13.1**：运行 `pytest tests/ --ignore=tests/test_real_llm_trace.py -v`，所有已有测试通过
- [ ] **AC-CA-13.2**：`harness/interfaces/context_assembler.py` 未被修改
- [ ] **AC-CA-13.3**：`harness/interfaces/types.py` 未被修改
- [ ] **AC-CA-13.4**：`harness/components/memory_backend/` 下文件未被修改
- [ ] **AC-CA-13.5**：`harness/components/guide_provider/` 下文件未被修改

---

## 八、不验证的内容

以下内容明确不在 batch-05 验收范围内：

- ❌ 不验证 Token 计数截断（batch-05 只做消息计数滑动窗口）
- ❌ 不验证消息摘要/压缩（summarization）
- ❌ 不验证编排器中 ContextAssembler 的实际调用（编排器已有调用逻辑，batch-05 只提供实现）
- ❌ 不验证与 ToolRegistry 的集成（ToolRegistry 在 batch-06）
- ❌ 不验证与 Sensor 的集成（Sensor 在 batch-07）
- ❌ 不验证复杂的 prompt 模板引擎（batch-05 使用硬编码模板）
- ❌ 不验证 RAG 或 embedding-based 检索
- ❌ 不验证多轮对话上下文优化（超出滑动窗口范围）
