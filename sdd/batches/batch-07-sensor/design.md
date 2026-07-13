# batch-07: Sensor — 架构设计

> 版本: 1.0
> 依赖: batch-02-1（Sensor 接口、Trajectory 类型）、batch-03（MemoryBackend 实例用于写入）

---

## 1. 设计目标

实现 Sensor 接口的默认实现 `LoggingSensor`，在会话结束阶段将完整执行轨迹写入 MemoryBackend 的 `episodic` 命名空间。

1. **构造注入 MemoryBackend**：与 ContextAssembler 一致的依赖注入模式
2. **副作用组件**：不返回值给框架，仅写入 MemoryBackend
3. **轨迹持久化**：从 Trajectory 中提取关键信息并结构化存储
4. **健壮性**：写入失败不崩溃，记录 WARNING 日志

---

## 2. 架构位置

```
会话结束阶段（Phase 3）:
  on_session_end Hook 触发
    → 框架调用 Sensor.sense(trajectory)
    → after_sensor Hook 触发
```

Sensor 在整个框架中的位置：

```
  ┌────────────────┐
  │ MemoryBackend  │◄─── 构造注入 ───┐
  └────────┬───────┘                  │
           ▲                          │
           │                   ┌──────┴───────┐
           │                   │    Sensor    │
           │                   │ (副作用组件)  │
           │                   └──────────────┘
           │                          │
           │                   sense(trajectory)
           │                          │
           └─────────── 写入 ─────────┘
```

---

## 3. 接口回顾

Sensor 接口已在 `harness/interfaces/sensor.py` 中定义：

```python
@runtime_checkable
class Sensor(Protocol):
    def sense(self, trajectory: Trajectory) -> None:
        """评估完整执行轨迹并将知识写入 MemoryBackend。"""
        ...
```

### 3.1 Trajectory 结构（来自 `harness/interfaces/types.py`）

```python
@dataclass
class Trajectory:
    session_id: str = ""
    history: List[Message] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    final_output: str = ""
    execution_time: float = 0.0
    system_state: SystemState = field(default_factory=SystemState)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

### 3.2 MemoryBackend 写入接口

```python
interface MemoryBackend:
    write(key: str, value: Any, namespace: str) → void
```

---

## 4. LoggingSensor 设计

### 4.1 类结构

```python
class LoggingSensor:
    """Sensor 的默认实现 — 将轨迹记录到 MemoryBackend。

    MemoryBackend 通过构造函数注入。在 sense() 被调用时，
    从 Trajectory 中提取关键信息并写入 episodic 命名空间。
    """

    def __init__(self, memory: MemoryBackend):
        """初始化。

        Args:
            memory: MemoryBackend 实例，用于写入轨迹数据。
        """
        self.memory = memory

    def sense(self, trajectory: Trajectory) -> None:
        """评估完整执行轨迹并将知识写入 MemoryBackend。

        Args:
            trajectory: 完整的会话执行轨迹。
        """
        ...
```

### 4.2 写入策略

每次 `sense()` 调用产生以下写入：

| 写入项 | key 格式 | namespace | value 内容 |
|--------|---------|-----------|-----------|
| 对话摘要 | `session_{session_id}` | `episodic` | 结构化字典，包含历史对话、工具调用统计、最终输出、耗时 |

写入的 value 结构：

```python
{
    "session_id": str,
    "timestamp": float,
    "user_request": str,           # 用户原始请求文本（从 trajectory.history 的首个 role="user" 消息提取）
    "final_output": str,           # Agent 最终输出
    "execution_time": float,       # 执行耗时（秒）
    "message_count": int,          # 对话消息数量
    "tool_call_count": int,        # 工具调用总次数
    "tool_calls_summary": [        # 工具调用摘要
        {
            "tool_name": str,
            "success": bool,
            "error": Optional[str],
        }
    ],
    "history_excerpt": str,        # 对话历史摘要（前 500 字符）
}
```

### 4.3 错误处理

- MemoryBackend.write() 失败时：记录 WARNING 日志，不抛出异常
- Trajectory 为空或关键字段缺失时：写入降级内容（空字符串/0 值），不阻塞
- session_id 缺失时：使用 `"unknown"` 作为 fallback

### 4.4 日志约定

- `sense()` 被调用：INFO 级别（含 session_id 和 execution_time）
- 写入成功：DEBUG 级别（含写入的 key 和 namespace）
- 写入失败：WARNING 级别（含异常信息）

---

## 5. 组件树

```
harness/interfaces/
├── sensor.py              # Sensor Protocol (已存在，无需修改)

harness/components/sensor/
├── __init__.py            # 导出 LoggingSensor (NEW)
├── logging_sensor.py      # LoggingSensor 实现 (NEW)

tests/
├── test_sensor.py         # LoggingSensor 单元测试 (NEW)
```

---

## 6. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 构造注入 MemoryBackend | 与 ContextAssembler 模式一致，遵循 DI 装配约定 |
| 2 | 写入 episodic 命名空间 | 框架约定：episodic 用于事件记忆，会话结束写入，下轮会话开始时检索 |
| 3 | 写入失败不抛异常 | Sensor 是副作用组件，写入失败不应阻断会话结束流程 |
| 4 | 不引入额外依赖 | LoggingSensor 是最简单的 Sensor 实现，仅做轨迹持久化，不涉及 Agent 评估 |
| 5 | 使用 session_id 作为 key 前缀 | 保证每次会话写入唯一 key，支持多次会话的历史记录积累 |
