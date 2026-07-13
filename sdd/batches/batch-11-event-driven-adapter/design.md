# batch-11: Event-Driven Adapter — 架构设计

> 版本: 1.0
> 依赖: batch-08（InputAdapter 接口、CliAdapter 实现）、batch-01（编排器）

---

## 1. 设计目标

将 `InputAdapter.send()` 从"接收一个整包 Response"改为"接收独立的事件对象"，实现按 LLM 输出字段驱动的前端推送。

核心动机：
1. **前后台分离** — CliAdapter 作为前台，工具调用/系统状态走独立通道（stderr），对话文本走 stdout
2. **信息透明** — thinking、tool_call、tool_result 作为一等事件推送给前端，不再被丢弃
3. **编排器解耦** — 编排器不再替前端做展示决策（不再裸用 `logger.info("🔧 ...")`），只负责产生事实
4. **可扩展** — 新增事件类型无需改接口签名

---

## 2. 架构变更

### 2.1 变更前后对比

```
变更前（整包 Response）:
  LLM返回 Response → 编排器内层循环攒批 → 最终 adapter.send(response)
  问题: thinking 丢弃、tool call 只写 logger、前端拿到的是"最后一口"

变更后（事件驱动）:
  LLM返回 Response →
    thinking字段   → adapter.send(ThinkingEvent)      ← 立即推送
    tool_uses[0]   → adapter.send(ToolCallEvent)       ← 立即推送
    (执行工具...)   → adapter.send(ToolResultEvent)     ← 立即推送
    tool_uses[1]   → adapter.send(ToolCallEvent)       ← 立即推送
    (执行工具...)   → adapter.send(ToolResultEvent)     ← 立即推送
    text字段       → adapter.send(TextEvent)            ← 立即推送
  break内层循环    → adapter.send(StopEvent)           ← 可选

  每次都立刻推，不攒。
```

### 2.2 组件关系

```
  ┌──────────────────┐
  │ LifecycleOrch.   │  只负责: 解析 LLM Response 字段 → 执行工具 → 推事件
  │                  │  不负责: 格式化输出 / emoji / 日志展示
  └────────┬─────────┘
           │ adapter.send(event: AdapterEvent)
           ▼
  ┌──────────────────┐
  │   InputAdapter   │  前端: 自主决定如何渲染每个事件
  │  (CliAdapter)    │
  │                  │
  │ TextEvent → stdout (前台对话)
  │ ThinkingEvent → stderr (后台, debug模式)
  │ ToolCallEvent → stderr (后台)
  │ ToolResultEvent → stderr (后台)
  │ StopEvent → no-op (会话控制)
  └──────────────────┘
```

---

## 3. 新增事件类型

### 3.1 事件类型定义（添加到 `harness/interfaces/types.py`）

```python
from __future__ import annotations
from typing import Union

# ── 新增：Adapter 事件类型 ──

@dataclass
class ThinkingEvent:
    """LLM 返回 thinking/reasoning_content 字段时推送。"""
    content: str


@dataclass
class ToolCallEvent:
    """LLM 返回 tool_uses 字段中的一项，执行前推送。"""
    call_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent:
    """工具执行完成后推送。"""
    call_id: str = ""
    tool_name: str = ""
    success: bool = True
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class TextEvent:
    """LLM 返回 text 字段时推送。"""
    content: str = ""


@dataclass
class StopEvent:
    """内层循环结束，等待下一轮用户输入。"""
    stop_reason: str = "end_turn"


# Union 类型别名，用于 InputAdapter.send() 签名
AdapterEvent = Union[ThinkingEvent, ToolCallEvent, ToolResultEvent, TextEvent, StopEvent]
```

### 3.2 事件触发时机

| 事件 | LLM Response 字段 | 触发时机 |
|------|------------------|---------|
| `ThinkingEvent` | `response.thinking` | thinking 非空时立即推送 |
| `ToolCallEvent` | `response.tool_uses[i]` | 每个 tool_use 执行前推送 |
| `ToolResultEvent` | 工具执行完毕 | 每个 tool 执行完成后推送 |
| `TextEvent` | `response.text` | text 非空且无更多 tool_uses 时推送 |
| `StopEvent` | `response.stop_reason` | 内层循环 break 后推送 |

### 3.3 设计决策：不使用继承

事件类型使用独立的 `@dataclass` 而非基类继承，原因：
- 与代码库现有风格一致（所有 DTO 都是独立 dataclass）
- `isinstance` / `match-case` 分发无需基类
- `AdapterEvent = Union[...]` 类型别名足够约束 `send()` 签名
- 零额外抽象层，新事件只需加一个 dataclass + 追加 Union

---

## 4. InputAdapter 接口变更

### 4.1 新接口

```python
@runtime_checkable
class InputAdapter(Protocol):
    def receive(self) -> UserRequest:
        """接收用户输入并返回标准化请求。"""
        ...

    def send(self, event: AdapterEvent) -> None:
        """接收一个前端事件并呈现给用户。

        编排器按 LLM 输出字段顺序逐一推送事件。
        前端自主决定每个事件类型的呈现方式（stdout/stderr/TUI面板/...）。
        """
        ...
```

### 4.2 变更范围

| 变更项 | 旧 | 新 |
|--------|----|----|
| `send()` 参数 | `response: Response` | `event: AdapterEvent` |
| import | `from .types import Response, UserRequest` | `from .types import AdapterEvent, UserRequest` |
| receive() | 不变 | 不变 |

---

## 5. CliAdapter 变更

### 5.1 新 `send()` 行为

```python
def send(self, event: AdapterEvent) -> None:
    """按事件类型分发到不同输出通道。

    前后台分离:
      - TextEvent → stdout（前台对话通道）
      - ThinkingEvent → stderr（后台，仅 debug 模式）
      - ToolCallEvent → stderr（后台工具状态）
      - ToolResultEvent → stderr（后台工具结果）
      - StopEvent → no-op
    """
    if isinstance(event, TextEvent):
        if event.content:
            print(event.content)
    elif isinstance(event, ThinkingEvent):
        if self._debug and event.content:
            print(f"[thinking] {event.content}", file=sys.stderr)
    elif isinstance(event, ToolCallEvent):
        summary = self._summarize_args(event.tool_name, event.arguments)
        print(f"🔧 {event.tool_name}({summary})", file=sys.stderr)
    elif isinstance(event, ToolResultEvent):
        if event.error:
            print(f"🔧 {event.tool_name} → ERROR ({event.duration_ms:.0f}ms): {event.error}",
                  file=sys.stderr)
        else:
            summary = self._summarize_result(event.result)
            print(f"🔧 {event.tool_name} → OK ({event.duration_ms:.0f}ms): {summary}",
                  file=sys.stderr)
    elif isinstance(event, StopEvent):
        pass  # 会话控制事件，无需输出
```

### 5.2 新增 `_summarize_args` / `_summarize_result` 方法

这两个方法从编排器迁移到 CliAdapter，因为参数摘要格式化是前端展示逻辑。

### 5.3 新增 `debug` 属性

```python
@property
def debug(self) -> bool:
    return self._debug

@debug.setter
def debug(self, value: bool) -> None:
    self._debug = value
```

默认 `False`，`main.py` 中 `--debug` 时设置为 `True`。

---

## 6. 编排器变更

### 6.1 内层循环重写（核心变更）

```python
# 内层循环中，每处理一个字段就推送对应事件

response = self.call_llm(messages, tools)

# ① thinking → ThinkingEvent
if response.thinking:
    adapter.send(ThinkingEvent(content=response.thinking))

# ② tool_uses → ToolCallEvent → 执行 → ToolResultEvent
if response.tool_uses:
    # 构造 assistant message（不变）
    ...

    for tc in response.tool_uses:
        # 推送 ToolCallEvent
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError as e:
            args = {}
            adapter.send(ToolCallEvent(
                call_id=tc.id, tool_name=tc.function.name, arguments={}
            ))
            adapter.send(ToolResultEvent(
                call_id=tc.id, tool_name=tc.function.name,
                success=False, error=f"JSON parse error: {e}",
                duration_ms=0.0
            ))
            continue

        adapter.send(ToolCallEvent(
            call_id=tc.id, tool_name=tc.function.name, arguments=args
        ))

        # 执行工具（不变）
        before_ts = time.time()
        try:
            result = tool_router.execute(tc.function.name, args)
            error = None
        except Exception as e:
            result = None
            error = str(e)
        after_ts = time.time()
        duration_ms = (after_ts - before_ts) * 1000

        # 推送 ToolResultEvent
        adapter.send(ToolResultEvent(
            call_id=tc.id, tool_name=tc.function.name,
            success=(error is None), result=result, error=error,
            duration_ms=duration_ms,
        ))

        # 记录到 tool_call_records / history（不变）
        ...

    # 继续循环（不变）
    continue

# ③ text → TextEvent + StopEvent → break
if response.text:
    messages.append(...)
    adapter.send(TextEvent(content=response.text))
    adapter.send(StopEvent(stop_reason=response.stop_reason))
    self._history.append(...)
    break

# ④ 空响应
adapter.send(StopEvent(stop_reason="empty_response"))
break
```

### 6.2 移除的代码

以下 `logger.info/warning` 调用被移除（变为前端职责）：

- `logger.info(f"🔧 {tc.function.name}({args_summary})")` → 改为 `adapter.send(ToolCallEvent)`
- `logger.info(f"🔧 {tc.function.name} → OK ...")` → 改为 `adapter.send(ToolResultEvent)`
- `logger.warning(f"🔧 {tc.function.name} → ERROR ...")` → 改为 `adapter.send(ToolResultEvent)`
- `logger.warning("Tool call '...': failed to parse arguments")` → 改为 `adapter.send(ToolResultEvent)`

### 6.3 `_summarize_args` / `_summarize_result` 迁移

这两个静态方法从编排器迁移到 CliAdapter。编排器不再需要这两个方法。

---

## 7. 组件树

```
harness/interfaces/
├── types.py                # +5 个 event dataclass + AdapterEvent Union
├── input_adapter.py        # send() 签名变更
├── __init__.py             # 导出新增事件类型

harness/components/input_adapter/
├── cli_adapter.py          # send() 事件分发 + _summarize_* 迁移

harness/core/
├── orchestrator.py         # 内层循环: Response → 逐字段推送事件

tests/
├── test_input_adapter.py   # CliAdapter.send() 改为事件
├── test_orchestrator.py    # mock adapter.send() 改为接收事件
├── test_*.py               # ~10 个测试文件的 mock adapter 更新

sdd/batches/batch-11-event-driven-adapter/
├── design.md               # 本文件
├── tasks.md                # 任务清单
├── acceptance.md           # 验收标准
```

---

## 8. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 事件类型用独立 dataclass + Union，不用基类 | 与现有类型风格一致，零额外抽象层 |
| 2 | `send()` 签名改为 `send(event: AdapterEvent)` | 扩展只需追加 Union 成员，开闭原则 |
| 3 | `_summarize_args/result` 从编排器移到 CliAdapter | 格式化是前端职责，编排器不关心 |
| 4 | `logger.info("🔧 ...")` 移除，改为 `adapter.send()` | 编排器不再替前端做展示决策 |
| 5 | `StopEvent` 作为独立事件存在 | 让前端感知"本轮结束"，为后续流式/进度条留空间 |
| 6 | `ThinkingEvent` 默认不输出到 stdout | thinking 属于后台信息，CliAdapter 仅 debug 模式输出到 stderr |
| 7 | 不改变 `Response` 类型 | Response 是 LLM 适配器的返回格式，属于另一层抽象 |
| 8 | 不改变 `receive()` 签名 | 输入侧不受本次变更影响 |
