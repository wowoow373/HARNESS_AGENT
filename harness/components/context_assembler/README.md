# ContextAssembler

> **Interface**: [`ContextAssembler`](../../interfaces/context_assembler.py) | **Required?**: No (strongly recommended) | **Lifecycle Phase**: Loop (outer, every turn)

## Interface Contract

`ContextAssembler` 是**上下文工程**的核心——将所有信息源（guides、memories、history、tools、user request）组装成发给 LLM 的最终消息列表。

```python
class ContextAssembler(Protocol):
    def assemble(self, inputs: AssemblyContext) -> List[Message]: ...
```

### Input: AssemblyContext

| Field | Type | Source |
|-------|------|--------|
| `user_request` | `Optional[UserRequest]` | InputAdapter.receive() |
| `guides` | `Optional[GuidesBundle]` | GuideProvider (cached from init) |
| `available_tools` | `List[ToolDefinition]` | ToolRouter.list_tools() (cached) |
| `history` | `List[Message]` | Current session conversation |
| `memories` | `List[MemoryItem]` | MemoryBackend.search() (cached from init) |
| `system_state` | `SystemState` | Framework-maintained |
| `metadata` | `Dict[str, Any]` | Extension bucket |

### Output: `List[Message]`

Can return `List[Message]` or `List[dict]` — the orchestrator auto-converts via `messages_to_dicts()`.

### Lifecycle

```
Outer loop (every user turn):
  1. ContextAssembler.assemble(assembly_context) → List[Message]
  2. [Hooks: before_llm_call]
  3. Inner loop: LLM ↔ Tool execution
  4. InputAdapter.receive() → update assembly_context
  5. Repeat from step 1
```

> Called **every outer-loop turn**. Tool results in the inner loop are appended directly to the message list — they do NOT go through `assemble()` again.

### Framework Baseline

框架在初始化阶段自动执行基线记忆检索（`memory.search(..., namespace="episodic")`），结果填入 `AssemblyContext.memories`。ContextAssembler 的最低实现只需消费此字段。

当需要**超越框架基线的检索策略**时（跨 namespace、不同 query），可通过构造函数注入 `MemoryBackend` 并在 `assemble()` 内执行额外检索。

---

## Default Implementation: SimpleAssembler

滑动窗口截断 + 直接拼接。零外部依赖。

### Usage

```python
from harness.components.context_assembler.simple_assembler import SimpleAssembler

assembler = SimpleAssembler(max_history=50)
# Enhanced: with extra memory retrieval in assemble()
assembler = SimpleAssembler(max_history=50, memory=memory_instance)

messages = assembler.assemble(assembly_context)
# messages[0].role == "system"   # guides + tools + memories
# messages[-1].role == "user"    # current user request
```

### Constructor

```python
SimpleAssembler(
    max_history: int = 50,
    memory: Optional[MemoryBackend] = None,
    include_tools: bool = True,
    include_memories: bool = True,
)
```

| Param | Default | Description |
|-------|---------|-------------|
| `max_history` | `50` | Sliding window size for non-system messages. `0` = keep only system, `<0` = unlimited |
| `memory` | `None` | Optional MemoryBackend for enhanced retrieval beyond baseline |
| `include_tools` | `True` | Whether to list available tools in system prompt |
| `include_memories` | `True` | Whether to include memories in system prompt |

### Assembly Pipeline

```
1. Enhanced memory retrieval (if self._memory is set)
   → search("semantic", limit=5) with error fallback
2. Build system message:
   {identity}
   ## Capabilities
   ## Rules
   ## Constraints
   ## Available Tools
   ## Relevant Memories    (skipped if empty)
3. Sliding window on history (preserves all system messages)
4. Append current user request
5. Return [system_msg] + history_window + [user_msg]
```

### Sliding Window

| `max_history` | Behavior |
|---------------|----------|
| `> 0` | Keep last N non-system messages |
| `0` | Discard all non-system — keep only system messages |
| `< 0` | No truncation (keep everything) |

> **System messages are always preserved** and don't count toward the window quota.

---

## Implement Your Own

### Context-aware assembler with token budgeting

```python
class TokenBudgetAssembler:
    def __init__(self, max_tokens: int = 8000, memory=None):
        self._max_tokens = max_tokens
        self._memory = memory

    def assemble(self, ctx: AssemblyContext) -> list:
        messages = []

        # System prompt
        system = self._build_system_prompt(ctx)
        messages.append(Message(role="system", content=system))

        # History with token-aware truncation
        remaining = self._max_tokens - self._count_tokens(system)
        for msg in reversed(ctx.history):
            tokens = self._count_tokens(msg.content)
            if tokens > remaining:
                break
            messages.insert(1, msg)  # insert after system, before user
            remaining -= tokens

        # User request
        messages.append(Message(role="user", content=ctx.user_request.text))
        return messages
```

### RAG-enhanced assembler

```python
class RAGAssembler:
    def __init__(self, vector_store, memory):
        self._vector_store = vector_store
        self._memory = memory

    def assemble(self, ctx: AssemblyContext) -> list:
        # Vector search for relevant docs
        docs = self._vector_store.search(ctx.user_request.text, top_k=5)

        # Build context-rich system prompt
        context_text = "\n".join(d.page_content for d in docs)
        system = f"{ctx.guides.identity}\n\n## Project Context\n{context_text}"

        return [
            Message(role="system", content=system),
            *ctx.history,
            Message(role="user", content=ctx.user_request.text),
        ]
```

### Registration

```python
container.register(ContextAssembler, SimpleAssembler(max_history=50, memory=memory))
# or
container.register(ContextAssembler, RAGAssembler(vector_store, memory))
```

> **Critical**: If you don't register a ContextAssembler, the framework uses a built-in fallback that only concatenates system identity + current user input — **no conversation history, no memories, no tools**. Production use strongly requires registering your own assembler.

---

## Deep Harness Usage

当对话历史过长时，ContextAssembler 内部可以用子 Harness 做**智能上下文压缩**——将长历史总结为精简摘要：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, ContextAssembler

class CompressingAssembler:
    """When history exceeds threshold, runs a sub-harness to summarize."""

    def __init__(self, sub_llm, max_history: int = 30, compress_threshold: int = 50):
        self._sub_llm = sub_llm
        self._max_history = max_history
        self._compress_threshold = compress_threshold

    def assemble(self, ctx: AssemblyContext) -> list:
        history = list(ctx.history)

        # 历史过长时，启动一个子 Harness 做压缩
        if len(history) > self._compress_threshold:
            old_history = history[:-self._max_history]
            recent = history[-self._max_history:]

            sub_container = DIContainer()
            sub_container.register(InputAdapter, SummarizeAdapter(old_history))
            sub_container.register(ContextAssembler, SummarizeAssembler(old_history))

            sub_harness = Harness.from_container(sub_container, call_llm=self._sub_llm)
            sub_harness.run()

            summary = Message(role="system", content=sub_harness._final_output)
            history = [summary] + recent

        # 正常组装
        return [
            Message(role="system", content=self._build_system_prompt(ctx)),
            *history,
            Message(role="user", content=ctx.user_request.text),
        ]
```
