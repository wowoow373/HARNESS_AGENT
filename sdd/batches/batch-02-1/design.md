# Batch-02-1: 接口测试 + 类型迁移 — 设计文档

> **目标**：将 harness 内核从 batch-01 的 `_Minimal*` 临时类型全部迁移到 batch-02 定义的正式接口类型，补齐 batch-01 验收标准中的测试覆盖缺口，同步更新开发者文档。
>
> **依赖**：batch-01（内核 MVP）、batch-02（interfaces 正式类型定义）
>
> **权威来源**：[sdd/02-interfaces.md](../../02-interfaces.md)、[batch-02 design.md](../batch-02-interfaces/design.md)

---

## 一、范围与边界

### 1.1 在范围内

| # | 任务 | 说明 |
|---|------|------|
| 1 | **转换层建设** | `messaging/builder.py` 新增 `Message ↔ dict` 双向转换、`ToolDefinition → OpenAI tool format` |
| 2 | **源码类型迁移** | `orchestrator.py`、`llm_adapter.py`、`builder.py` 中所有 `_Minimal*` 替换为正式类型 |
| 3 | **桥接方法删除** | `orchestrator.py` 中 3 个 `_normalize_*` 方法删除 |
| 4 | **旧类型废弃** | `core/types.py` 标记 deprecated |
| 5 | **测试迁移** | 5 个测试文件中 ~173 处 `_Minimal*` 引用更新为正式类型 |
| 6 | **测试 gap 补齐** | AC-ORCH-14、AC-EDGE-03/04/05 + 转换层单元测试 |
| 7 | **文档同步** | `CORE_DEVELOPER_GUIDE.md` 示例代码更新为正式类型 |

### 1.2 严格不在范围内

- ❌ 不修改编排器控制流逻辑（三阶段结构、循环条件、组件 resolve 顺序不变）
- ❌ 不修改 `harness/core/container.py`（DI 容器）
- ❌ 不修改 `harness/config/loader.py` 的解析/校验逻辑（仅补齐测试）
- ❌ 不修改 `harness/core/exceptions.py`
- ❌ 不修改任何 batch-02 接口文件（Protocol / types.py）
- ❌ 不实现任何组件（那是 batch-03 ~ 09）
- ❌ 不添加新功能（仅做类型迁移 + 测试补全）

---

## 二、核心设计决策

### 2.1 迁移策略：逐文件、保持行为不变

迁移遵循 **"先建转换层 → 再迁移外围 → 最后迁移核心"** 的顺序：

```
阶段 1: messaging/builder.py  ← 先建 Message↔dict 转换工具
    ↓
阶段 2: adapters/llm_adapter.py  ← 外围适配器，独立迁移
    ↓
阶段 3: core/orchestrator.py  ← 核心编排器，依赖转换层
    ↓
阶段 4: core/types.py  ← 标记废弃
    ↓
阶段 5~6: tests/  ← 测试迁移 + gap 补齐
```

每个阶段完成后运行测试确认无回归。

### 2.2 删除 `_normalize_*`：信任 Protocol 合约

batch-02 已通过 `Protocol` + `@runtime_checkable` 定义了组件的方法签名和返回类型。迁移后编排器信任组件返回的就是正式类型，不再做防御性类型转换。

**删除的方法**：
- `_normalize_user_request()` — 13 行
- `_normalize_guides()` — 13 行  
- `_normalize_response()` — 35 行（含嵌套 `_MinimalToolCall` 构造）

**替代方案**：调用点直接使用 DI 容器返回的对象，不经过转换层。

### 2.3 转换层归属：`messaging/builder.py`

`Message` ↔ `dict` 和 `ToolDefinition` → OpenAI format 的转换逻辑放在 `messaging/builder.py`，与已有的 `build_assistant_message()`、`build_tool_result_message()` 形成统一的"框架类型 ↔ OpenAI 格式"转换层。

**这不是在实现 ContextAssembler 或 SystemToolProvider/MCPAdapter 的功能** — 它是纯格式转换，不涉及"上下文里放什么"或"工具有哪些"的决策逻辑。

### 2.4 `_history` 类型：`List[Dict]` → `List[Message]`

编排器内部 `_history` 当前存储 OpenAI-format dict，迁移后改为 `List[Message]`。原因：

- `AssemblyContext.history` 是 `List[Message]`，直接传入避免临时转换
- `Trajectory.history` 是 `List[Message]`，`_build_trajectory` 直接使用
- LLM 调用时通过 `message_to_dict()` 转换（仅在一处发生）

### 2.5 `_should_exit` 逻辑适配

`_MinimalUserRequest.text: Optional[str]` → `UserRequest.text: str`。

```python
# 迁移前
if user_request.text is None:       # None 表示 EOF
    return True
if user_request.text.strip() == "": # 空字符串
    return True

# 迁移后
if not user_request.text:           # "" 表示空输入（含 EOF）
    return True
if user_request.text.strip() == "": # 仅空白字符
    return True
```

`UserRequest.text` 默认值为 `""`，不再有 `None` 语义。空字符串同时覆盖"无输入"和"EOF"场景。

---

## 三、字段级迁移映射

### 3.1 `_MinimalUserRequest` → `UserRequest`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `text` | `Optional[str]` | `text` | `str` | `None` → 空字符串语义。`_should_exit()` 适配 |
| `metadata` | `Dict[str, Any]` | `metadata` | `Dict[str, Any]` | 直接映射 |
| _(无)_ | — | `attachments` | `List[Attachment]` | 新字段，默认 `[]`，迁移后暂不使用 |
| _(无)_ | — | `context` | `Dict[str, Any]` | 新字段，默认 `{}` |
| _(无)_ | — | `system_state` | `SystemState` | 新字段，默认 `SystemState()` |
| _(无)_ | — | `session_id` | `str` | 新字段，默认 `""` |

### 3.2 `_MinimalGuidesBundle` → `GuidesBundle`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `identity` | `str` | `identity` | `str` | 直接映射 |
| `capabilities` | `List[str]` | `capabilities` | `List[str]` | 直接映射 |
| `rules` | `List[str]` | `rules` | `List[str]` | 直接映射 |
| `constraints` | `List[str]` | `constraints` | `List[str]` | 直接映射 |
| `examples` | `List[Dict[str, str]]` | `examples` | `List[Example]` | `{"input": x, "output": y}` → `Example(input=x, output=y)` |

### 3.3 `_MinimalAssemblyContext` → `AssemblyContext`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `user_request` | `Optional[_MinimalUserRequest]` | `user_request` | `Optional[UserRequest]` | 类型替换 |
| `guides` | `Optional[_MinimalGuidesBundle]` | `guides` | `Optional[GuidesBundle]` | 类型替换 |
| `available_tools` | `List[Dict[str, Any]]` | `available_tools` | `List[ToolDefinition]` | Dict → 对象 |
| `history` | `List[Dict[str, Any]]` | `history` | `List[Message]` | Dict → 对象，LLM 调用前需 `message_to_dict()` |
| `memories` | `List[Dict[str, Any]]` | `memories` | `List[MemoryItem]` | Dict → 对象 |
| `system_state` | `Dict[str, Any]` | `system_state` | `SystemState` | Dict 访问 → 属性访问 |
| `metadata` | `Dict[str, Any]` | `metadata` | `Dict[str, Any]` | 直接映射 |

### 3.4 `_MinimalResponse` → `Response`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `text` | `Optional[str]` | `text` | `Optional[str]` | 直接映射 |
| `thinking` | `Optional[str]` | `thinking` | `Optional[str]` | 直接映射 |
| `tool_uses` | `List[_MinimalToolCall]` | `tool_uses` | `List[ToolCall]` | 元素类型替换 |
| `stop_reason` | `str` | `stop_reason` | `str` | 直接映射 |

### 3.5 `_MinimalToolCall` → `ToolCall`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `id` | `str` | `id` | `str` | 直接映射 |
| `type` | `str` | `type` | `str` | 直接映射 |
| `function` | `_MinimalToolCallFunction` | `function` | `ToolCallFunction` | 类型替换 |
| `parse_arguments()` | 实例方法 | _(无)_ | — | 替换为 `json.loads(tc.function.arguments)` |

### 3.6 `_MinimalToolCallFunction` → `ToolCallFunction`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `name` | `str` | `name` | `str` | 直接映射 |
| `arguments` | `str` | `arguments` | `str` | 直接映射 |

### 3.7 `_MinimalTrajectory` → `Trajectory`

| 旧字段 | 旧类型 | 新字段 | 新类型 | 迁移注意事项 |
|--------|--------|--------|--------|-------------|
| `user_request` | `Optional[_MinimalUserRequest]` | `user_request` | `Optional[UserRequest]` | 类型替换 |
| `history` | `List[Dict[str, Any]]` | `history` | `List[Message]` | Dict → 对象 |
| `tool_calls` | `List[Dict[str, Any]]` | `tool_calls` | `List[ToolCallRecord]` | Dict → 对象 |
| `final_output` | `str` | `final_output` | `str` | 取值方式从 `dict.get("content")` → `.content` |
| `execution_time` | `float` | `execution_time` | `float` | 直接映射 |
| `system_state` | `Dict[str, Any]` | `system_state` | `SystemState` | Dict 访问 → 属性访问 |
| `metadata` | `Dict[str, Any]` | `metadata` | `Dict[str, Any]` | 直接映射 |

---

## 四、新增转换工具

全部定义在 `harness/messaging/builder.py`。

### 4.1 `message_to_dict(msg: Message) → Dict[str, Any]`

将 `Message` 对象转为 OpenAI 兼容 dict：

```python
def message_to_dict(msg: Message) -> Dict[str, Any]:
    result: Dict[str, Any] = {"role": msg.role, "content": msg.content}
    if msg.tool_call_id is not None:
        result["tool_call_id"] = msg.tool_call_id
    return result


def messages_to_dicts(messages: List[Message]) -> List[Dict[str, Any]]:
    return [message_to_dict(m) for m in messages]
```

### 4.2 `dict_to_message(d: Dict[str, Any]) → Message`

将 OpenAI 兼容 dict 转为 `Message` 对象：

```python
def dict_to_message(d: Dict[str, Any]) -> Message:
    return Message(
        role=d.get("role", "user"),
        content=d.get("content") or "",
        tool_call_id=d.get("tool_call_id"),
    )
```

限制：dict 中的 `tool_calls` 等非标字段在转换中丢弃（`Message` 是简化表示）。这对 history 记录场景无害。

### 4.3 `tool_definitions_to_openai(tools: List[ToolDefinition]) → List[Dict[str, Any]]`

将 `ToolDefinition` 列表包装为 OpenAI tool format：

```python
def tool_definition_to_openai(td: ToolDefinition) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": td.name,
            "description": td.description,
            "parameters": td.parameters,
        },
    }


def tool_definitions_to_openai(tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
    return [tool_definition_to_openai(t) for t in tools]
```

### 4.4 现有函数签名升级

- `build_assistant_message(response: Response)` — 参数类型从 `_MinimalResponse` → `Response`
- `build_tool_result_message(tool_call: ToolCall, ...)` — 参数类型从 `_MinimalToolCall` → `ToolCall`

---

## 五、`_should_exit` 逻辑适配

详细变更（对照 orchestrator.py L443-L453）：

```python
# 迁移后
def _should_exit(self, user_request: UserRequest) -> bool:
    # UserRequest.text 是 str（非 Optional），空字符串表示无输入
    if not user_request.text:
        return True
    if user_request.text.strip() == "":
        return True
    if user_request.text.strip() == self._EXIT_KEYWORD:
        return True
    if user_request.metadata.get("exit") is True:
        return True
    return False
```

关键变化：`text is None` 检查和 `text.strip() == ""` 检查合并为一个 `not text` 分支。

---

## 六、测试策略

### 6.1 迁移测试（改引用，不改断言逻辑）

- `tests/test_orchestrator.py` — ~79 处替换
- `tests/test_black_box.py` — ~76 处替换
- `tests/test_real_llm_trace.py` — ~14 处替换
- `tests/test_llm_adapter.py` — ~4 处替换（含 2 处 dead import 清理）

**原则**：所有测试的断言逻辑不变。如果迁移前测试通过、迁移后也通过，即证明行为保持。

### 6.2 新增测试

| 测试项 | 来源 | 类型 |
|--------|------|------|
| AC-ORCH-14 | test-gaps.md §1.1 | 编排器行为验证 |
| AC-EDGE-03 | test-gaps.md §1.2 | ConfigLoader 边界条件 |
| AC-EDGE-04 | test-gaps.md §1.2 | ConfigLoader 边界条件 |
| AC-EDGE-05 | test-gaps.md §1.2 | ConfigLoader 边界条件（性能） |
| `test_messaging.py` | 新增 | 转换层单元测试 |

### 6.3 转换层单元测试

针对 `messaging/builder.py` 新增函数的测试：

- `message_to_dict` / `dict_to_message` 往返一致性
- `Message` 三种 role 的序列化正确性（system/user/assistant/tool）
- `tool_call_id` 为 None 时不写入 dict
- `tool_definition_to_openai` 输出格式与 OpenAI spec 一致
- 空列表、边界值处理

---

## 七、与前后批次的接口约定

### 7.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| 正式类型 (16 dataclass) | batch-02 `interfaces/types.py` | 替换 `_Minimal*` import |
| Protocol 接口 (9 个) | batch-02 `interfaces/*.py` | 继续作为 DI key 和类型标注 |
| DI 容器 | batch-01 `core/container.py` | 不动 |
| 异常体系 | batch-01 `core/exceptions.py` | 不动 |

### 7.2 为后续批次提供的基础

| 产出 | 被哪些批次使用 | 使用方式 |
|------|--------------|---------|
| 正式类型无处不在的使用 | batch-03 ~ 09 | 所有组件实现直接使用正式类型，无需 `_normalize_*` |
| `Message ↔ dict` 转换 | batch-05 (ContextAssembler) | ContextAssembler 返回 `List[Message]`，编排器自动转换 |
| `ToolDefinition → OpenAI` 转换 | batch-06 (Tool/MCP) | SystemToolProvider/MCPAdapter 返回正式类型，ToolRouter 合并后编排器自动包装 |
| 废弃的 `_Minimal*` 类型 | _(无)_ | 后续批次可安全忽略 |

### 7.3 类型世界的终态

```
batch-02-1 完成后:
  正式类型 (interfaces/types.py)  ← 全框架唯一使用的类型
  _Minimal* (core/types.py)       ← 标记 deprecated，无引用
  _normalize_* 桥接方法          ← 已删除

batch-03~09:
  所有组件实现直接使用正式类型
  编排器无任何类型转换桥接代码
```
