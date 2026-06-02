# Batch-02-1: 已知测试 gap 参考

> 本文档记录了 batch-01 MVP 测试套件中已发现的覆盖缺口和迁移影响评估，供 batch-02-1 实施时参考。

---

## 一、测试覆盖缺口（batch-01 acceptance.md 中未覆盖的验收标准）

### 1.1 编排器

**AC-ORCH-14: Inner loop does not call ContextAssembler.assemble()**

- 验收标准：tool_use 连续场景中，ContextAssembler.assemble() 只在进入外层循环时被调用一次，内层 tool_use 循环中不重复调用。
- 状态：**未测试**。当前无 spy 验证 assemble() 调用次数。
- 建议：在 tool_use loop 测试中添加 spy/call counter，断言 assemble 只在外层被调用。

### 1.2 配置加载器

**AC-EDGE-03: Empty file (0 bytes)**

- 验收标准：ConfigLoader.load() 遇到空文件应抛出 ConfigParseError。
- 状态：**未测试**。
- 建议：写入空文件后调用 load()，断言抛出 ConfigParseError。

**AC-EDGE-04: Comments-only TOML**

- 验收标准：仅含注释的 TOML 文件缺少 `[meta]` 节，validate() 应抛出 ConfigValidationError。
- 状态：**未测试**。
- 建议：写入含 `# comment` 但无 `[meta]` 的文件，load 后 validate 应抛出。

**AC-EDGE-05: Large TOML file (10MB+)**

- 验收标准：ConfigLoader 能处理 10MB+ 的 TOML 文件而不崩溃或超时。
- 状态：**未测试**。
- 建议：生成一个含大量注释或冗余字段的 10MB+ TOML 文件进行加载测试。

---

## 二、迁移影响评估

### 2.1 `_Minimal*` 引用分布

batch-02-1 将 `_Minimal*` 类型全部替换为正式类型后，以下测试文件需要更新：

| 文件 | `_Minimal*` 引用数 | 涉及类型 |
|------|-------------------|---------|
| `tests/test_orchestrator.py` | ~79 | 全部 7 种 |
| `tests/test_black_box.py` | ~76 | 全部 7 种 |
| `tests/test_real_llm_trace.py` | ~14 | 6 种（无 `_MinimalAssemblyContext`） |
| `tests/test_llm_adapter.py` | ~4 | `_MinimalResponse`（其中 2 处 import 为 dead code） |

不受影响的测试文件：
- `tests/test_config.py` — 未使用 `_Minimal*`
- `tests/test_container.py` — 未使用 `_Minimal*`
- `tests/test_exceptions.py` — 未使用 `_Minimal*`

### 2.2 源码改动范围

| 文件 | 改动内容 |
|------|---------|
| `harness/core/orchestrator.py` | 15 处 `_Minimal*` 引用替换 + 删除 3 个 `_normalize_*` 方法 |
| `harness/adapters/llm_adapter.py` | 6 处：`_MinimalResponse` → `Response`，`_MinimalToolCall` → `ToolCall`，`_MinimalToolCallFunction` → `ToolCallFunction` |
| `harness/messaging/builder.py` | 3 处：参数类型替换 |
| `harness/core/types.py` | 标记为废弃（或删除 `_Minimal*` 类型） |

### 2.3 关键迁移差异点

| _Minimal* 字段 | 正式类型字段 | 迁移注意事项 |
|----------------|-------------|-------------|
| `_MinimalUserRequest.text: Optional[str]` | `UserRequest.text: str` | 退出检测 `text is None` → `not text` |
| `_MinimalAssemblyContext.history: List[Dict]` | `AssemblyContext.history: List[Message]` | LLM 调用前需要 `Message` → `dict` 转换 |
| `_MinimalAssemblyContext.available_tools: List[Dict]` | `AssemblyContext.available_tools: List[ToolDefinition]` | 构造方式改变 |
| `_MinimalAssemblyContext.memories: List[Dict]` | `AssemblyContext.memories: List[MemoryItem]` | 构造方式改变 |
| `_MinimalAssemblyContext.system_state: Dict` | `AssemblyContext.system_state: SystemState` | dict 访问 → 属性访问 |
| `_MinimalGuidesBundle.examples: List[Dict]` | `GuidesBundle.examples: List[Example]` | dict 构造 → Example 构造 |
| `_MinimalResponse.tool_uses: List[_MinimalToolCall]` | `Response.tool_uses: List[ToolCall]` | `parse_arguments()` 方法改为内联 json.loads |

---

## 三、测试代码小问题

### 3.1 Dead imports in test_llm_adapter.py

`tests/test_llm_adapter.py:16-17` 导入了 `_MinimalToolCall` 和 `_MinimalToolCallFunction`，但在文件中从未使用。迁移时可直接删除。

### 3.2 Re-export wrapper imports

多个测试通过 re-export 路径导入（如 `harness.core.llm_adapter` → 实际是 `harness.adapters.llm_adapter`），batch-02-1 迁移时需确认 import 路径一致性。

---

## 四、CORE_DEVELOPER_GUIDE.md 状态

该文档包含大量以 `_Minimal*` 类型编写的代码示例（§4、§5、§7.2、§11），目前已标注过时警告。batch-02-1 迁移完成后需整体更新示例代码为正式类型接口。
