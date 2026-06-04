# batch-08: InputAdapter — 架构设计

> 版本: 1.0
> 依赖: batch-02-1（InputAdapter 接口、UserRequest/Response 类型）

---

## 1. 设计目标

实现 InputAdapter 接口的默认实现 `CliAdapter`，提供命令行交互能力：

1. **stdin 输入**：从标准输入逐行读取用户输入，转为标准化的 UserRequest
2. **stdout 输出**：将 LLM Response 格式化输出到标准输出
3. **退出信号处理**：空输入或 `/exit` 时返回可被编排器识别的退出信号
4. **最小依赖**：不依赖任何外部库或框架组件

---

## 2. 架构位置

CliAdapter 在框架中的位置：

```
  ┌──────────────────┐
  │   InputAdapter   │
  │ receive()/send() │
  └────────┬─────────┘
           │
  ┌────────┴─────────┐
  │    CliAdapter    │
  │                  │
  │ stdin → receive()│
  │ send() → stdout  │
  └──────────────────┘
```

InputAdapter 调用时机：
- `receive()`：会话初始化时（Phase 1），以及后续每轮用户有新输入时（Phase 2 外层循环）
- `send()`：每次 LLM 返回包含 text 的 Response 时

---

## 3. 接口回顾

InputAdapter 接口已在 `harness/interfaces/input_adapter.py` 中定义：

```python
@runtime_checkable
class InputAdapter(Protocol):
    def receive(self) -> UserRequest:
        """接收用户输入并返回标准化请求。"""
        ...

    def send(self, response: Response) -> None:
        """将 Agent 响应返回给用户。"""
        ...
```

### 3.1 UserRequest 结构（来自 `harness/interfaces/types.py`）

```python
@dataclass
class UserRequest:
    text: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    system_state: SystemState = field(default_factory=SystemState)
    session_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
```

### 3.2 Response 结构

```python
@dataclass
class Response:
    text: Optional[str] = None
    thinking: Optional[str] = None
    tool_uses: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
```

---

## 4. CliAdapter 设计

### 4.1 类结构

```python
class CliAdapter:
    """InputAdapter 的命令行实现。

    从 stdin 读取用户输入并转为 UserRequest，将 Response 格式化输出到 stdout。
    支持 session_id 自动生成、退出信号检测。
    """

    def __init__(self, session_id: Optional[str] = None):
        """初始化。

        Args:
            session_id: 会话标识。为 None 时自动生成（基于时间戳）。
        """
        self._session_id = session_id or self._generate_session_id()
        self._prompt = "> "

    def receive(self) -> UserRequest:
        """从 stdin 读取一行用户输入并返回 UserRequest。

        行为：
        - 打印提示符（prompt）到 stdout
        - 从 stdin 读取一行文本
        - 去除首尾空白
        - 返回 UserRequest（session_id 自动填充）

        Returns:
            UserRequest: 标准化用户请求。
        """
        ...

    def send(self, response: Response) -> None:
        """将 Response 输出到 stdout。

        行为：
        - 如果 response.text 非空：直接打印到 stdout
        - 如果 response.thinking 非空且处于 debug 模式：打印思考过程
        - 如果仅有 tool_uses：不输出（工具调用在内部处理）

        Args:
            response: LLM 返回的 Response 对象。
        """
        ...
```

### 4.2 `receive()` 详细行为

1. 显示提示符 `"> "`（可通过 `prompt` 属性自定义）
2. 阻塞等待 stdin 输入一行
3. 去除首尾空白字符
4. 构造并返回 `UserRequest(text=line, session_id=self._session_id)`
5. EOF（Ctrl+D）时：返回 `UserRequest(text="")` → 编排器识别为退出信号

### 4.3 `send()` 详细行为

1. 若 `response.text` 非空 → `print(response.text)` 到 stdout
2. 若仅有 `tool_uses` 无 `text` → 不输出任何内容（内部工具循环）
3. `thinking` 内容仅在 debug 模式下输出（通过 `SystemState.run_mode` 控制）

### 4.4 退出信号兼容

CliAdapter 本身不做退出判断（这是编排器的职责）。编排器通过以下条件识别退出：
- `UserRequest.text` 为空字符串 → 退出
- `UserRequest.text` 为 `"/exit"` → 退出
- `UserRequest.metadata["exit"]` 为 True → 退出

CliAdapter 的 `receive()` 返回的 UserRequest 自然兼容这些条件：
- 空输入 → `text=""` → 编排器识别为退出
- 用户输入 `/exit` → `text="/exit"` → 编排器识别为退出

### 4.5 可配置属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | str | `"> "` | 输入提示符 |
| `session_id` | str | 自动生成 | 会话标识 |

---

## 5. 组件树

```
harness/interfaces/
├── input_adapter.py       # InputAdapter Protocol (已存在，无需修改)

harness/components/input_adapter/
├── __init__.py            # 导出 CliAdapter (NEW)
├── cli_adapter.py         # CliAdapter 实现 (NEW)

tests/
├── test_input_adapter.py  # CliAdapter 单元测试 (NEW)
```

---

## 6. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 使用 stdin/stdout | 最通用的 I/O 方式，兼容管道、重定向、TTY |
| 2 | `send()` 不输出 tool_uses | 工具调用是框架内部行为，用户只关心最终文本响应 |
| 3 | session_id 自动生成 | 简化用户使用，同时支持显式指定 |
| 4 | 不依赖 curses/readline | 保持最小依赖，框架用户可通过 DI 替换为更丰富的实现 |
| 5 | 退出判断留给编排器 | 职责分离：InputAdapter 只负责 I/O，编排器负责流程控制 |
| 6 | `send()` 直接 print | 最简单实现；用户可通过替换 InputAdapter DI 注册改用富文本/WebSocket 等输出 |
