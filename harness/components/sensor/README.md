# Sensor

> **Interface**: [`Sensor`](../../interfaces/sensor.py) | **Required?**: No | **Lifecycle Phase**: End (called once)

## Interface Contract

`Sensor` 提供**反馈控制**——在会话结束时，读取完整执行轨迹，按自定义规则评估，并将沉淀的知识写入 MemoryBackend。

```python
class Sensor(Protocol):
    def sense(self, trajectory: Trajectory) -> None: ...
```

### Input: Trajectory

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | 会话标识 |
| `history` | `List[Message]` | 完整事件流：user → assistant(+tool_calls) → tool_result → ... |
| `tool_calls` | `List[ToolCallRecord]` | 所有工具调用的执行记录（含耗时、成功/失败） |
| `final_output` | `str` | Agent 最终输出 |
| `execution_time` | `float` | 执行耗时（秒） |
| `system_state` | `SystemState` | 系统状态 |
| `metadata` | `Dict[str, Any]` | 扩展桶 |

### Key Design Points

- **Side-effect component**: `sense()` returns `void`. All effects are through MemoryBackend.
- **Constructor injection**: Receives `MemoryBackend` reference via `__init__`, not from container.
- **Session-end evaluation**: Called once at session end. Evaluates the **complete multi-turn trajectory**.
- **Recursive agent pattern**: Your Sensor can internally launch another Agent for complex evaluation.

### Lifecycle

```
Session End (Phase 3):
  1. Framework assembles full Trajectory
  2. Trigger on_session_end Hook
  3. Sensor.sense(trajectory)           ← HERE
  4. Trigger after_sensor Hook (read-only observation)
  5. ToolRouter.shutdown()
  6. Cleanup
```

---

## Default Implementation: LoggingSensor

将轨迹的关键摘要写入 MemoryBackend 的 `episodic` 命名空间，供后续会话检索。

### Usage

```python
from harness.components.sensor.logging_sensor import LoggingSensor

sensor = LoggingSensor(memory=memory_instance)
sensor.sense(trajectory)
```

### Constructor

```python
LoggingSensor(memory: MemoryBackend)
```

| Param | Description |
|-------|-------------|
| `memory` | MemoryBackend instance for persisting trajectory data |

### Written Value Structure

```python
{
    "session_id": "cli-1234567890",
    "timestamp": 1234567890.123,
    "user_request": "help me debug this function",
    "final_output": "Here's the fix...",
    "execution_time": 12.5,
    "message_count": 24,
    "tool_call_count": 3,
    "tool_calls_summary": [
        {"tool_name": "read_file", "success": True, "error": None},
        {"tool_name": "shell", "success": True, "error": None},
    ],
    "history_excerpt": "user: help me debug...\nassistant: Let me look at...",  # truncated 500 chars
}
```

Stored under key `session_<session_id>` in namespace `episodic`.

---

## Implement Your Own

### Quality scoring sensor

```python
class QualitySensor:
    def __init__(self, memory: MemoryBackend, llm):
        self.memory = memory
        self._llm = llm

    def sense(self, trajectory: Trajectory) -> None:
        # Ask LLM to score the session quality
        prompt = f"Rate this agent session (1-10):\n{trajectory.final_output}"
        response = self._llm([{"role": "user", "content": prompt}], tools=None)

        self.memory.write(
            key=f"quality_{trajectory.session_id}",
            value={"score": response.text, "execution_time": trajectory.execution_time},
            namespace="sensor_raw",
        )
```

### Learning sensor (writes procedural memory)

```python
class LearningSensor:
    def __init__(self, memory: MemoryBackend, llm):
        self.memory = memory
        self._llm = llm

    def sense(self, trajectory: Trajectory) -> None:
        # Extract reusable patterns from successful sessions
        if trajectory.execution_time > 30:  # complex session
            prompt = "Extract the reusable problem-solving pattern from:\n"
            prompt += "\n".join(m.content for m in trajectory.history[-10:])
            pattern = self._llm([{"role": "user", "content": prompt}], tools=None)

            self.memory.write(
                key=f"pattern_{trajectory.session_id}",
                value=pattern.text,
                namespace="procedural",
            )
```

### Registration

```python
memory = MdMemory(path="./memory")
container.register(MemoryBackend, memory)
container.register(Sensor, LoggingSensor(memory=memory))
# or
container.register(Sensor, LearningSensor(memory=memory, llm=eval_llm))
```

> **Important**: Sensor gets `MemoryBackend` via constructor injection, NOT from the DI container. Create a shared `memory` instance and pass it to both the container and Sensor.

---

## Deep Harness Usage

Sensor 是**递归 Harness 模式**的典型入口——在 Sensor 内部启动另一个完整的 Agent 来做复杂评估：

```python
class MetaAgentSensor:
    """Launches a sub-agent to evaluate and improve the main agent."""

    def __init__(self, memory: MemoryBackend):
        self.memory = memory

    def sense(self, trajectory: Trajectory) -> None:
        # 1. Assemble evaluation context
        eval_context = {
            "history": trajectory.history,
            "tool_calls": trajectory.tool_calls,
            "final_output": trajectory.final_output,
        }

        # 2. Build a sub-harness for evaluation
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.interfaces import InputAdapter, ContextAssembler

        sub_container = DIContainer()
        sub_container.register(InputAdapter, EvalInputAdapter(eval_context))
        sub_container.register(ContextAssembler, EvalAssembler(eval_context))

        # 3. Run evaluation agent
        sub_harness = Harness.from_container(sub_container, call_llm=self._eval_llm)
        sub_harness.run()

        # 4. Write findings
        self.memory.write(
            key=f"eval_{trajectory.session_id}",
            value=sub_harness.final_output,
            namespace="sensor_raw",
        )
```

This pattern enables:
- **Quality scoring**: Score every session, track improvement over time
- **Automatic prompt optimization**: Detect patterns in failures, suggest guide improvements
- **Skill extraction**: Identify reusable strategies from successful sessions
