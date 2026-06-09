# Runtime Layer 详细设计：MessageBus + 文件布局 + Batch Spec 指南

> 版本: 0.1 | 日期: 2026-06-09 | 状态: 设计评审
> 关联文档: [2026-06-08-runtime-layer-design.md](./2026-06-08-runtime-layer-design.md)（顶层设计）

---

## 一、文档定位

本文档是 Runtime Layer 顶层设计的**补充**，补全两个顶层设计中未展开的领域：

1. **MessageBus 详细设计** — 从数据流场景推导接口，伪代码验证
2. **Batch 文件布局** — 每 batch 新增/修改的文件清单、跨 batch 接口契约、依赖解耦
3. **Batch Spec 编写指南** — 后续编写各 batch 详细 spec 时的模板 + 关键设计要点清单

不重复顶层设计中已有定论的内容（AgentRuntime 状态机、Kernel 公开方法、Workflow 脚本格式等）。

---

## 二、MessageBus 详细设计

### 2.1 职责边界

MessageBus 是一个 **pub-sub 路由表 + 消息投递器**，做机制不做策略。

| 属于 MessageBus | 不属于 MessageBus |
|----------------|-------------------|
| 维护 publisher → subscribers 映射 | 退出保护（在 KernelBridgeAdapter 层） |
| publish() 时查表路由到 input_queues | 推送 `__EXIT_SENTINEL__`（在 Kernel 层） |
| direct() 时跳过订阅表直接投递 | 级联终止策略（在 Kernel._on_agent_finished） |
| 提供 get_subscribers_of() 供 Kernel 查询 | 静默检测（在 Kernel._monitor_quiescence） |

### 2.2 数据流场景与消息路径

#### 场景 A：流式订阅（subscribe → TextEvent 路由）

```
collector (publisher)                    analyzer (subscriber)
  │                                         │
  ├─ adapter.send(TextEvent("采集到28个文件"), target=None)
  │                                         │
  ▼                                         │
KernelBridgeAdapter.send()                  │
  ├─ should_exit? → false                   │
  ├─ target is None → publish path          │
  │                                         │
  ▼                                         │
MessageBus.publish(                         │
  from="collector",                         │
  event=TextEvent("采集到28个文件"))          │
  ├─ 查表: _subscriptions["collector"]      │
  │  → {"analyzer"}                         │
  ├─ 过滤已 FINISHED 的订阅者                │
  ├─ 对每个活跃订阅者:                        │
  │  input_queues["analyzer"].put_nowait(    │
  │    InternalMessage(                      │
  │      from_pid="collector",              │
  │      content="采集到28个文件",            │
  │      metadata={}                        │
  │    )                                     │
  │  )                                       │
  └─ done                                   │
                                             ▼
                                  analyzer.adapter.receive()
                                    → UserRequest(
                                        text="采集到28个文件",
                                        metadata={from: "collector"}
                                      )
```

#### 场景 B：定向投递（talk_to tool → target=pid）

```
root                                          analyzer
  │                                             │
  ├─ adapter.send(TextEvent("请重新分析"),        │
  │                target="analyzer")            │
  │                                             │
  ▼                                             │
KernelBridgeAdapter.send()                      │
  ├─ should_exit? → false                       │
  ├─ target="analyzer" → direct path            │
  │                                             │
  ▼                                             │
MessageBus.direct("analyzer",                   │
  InternalMessage(                              │
    from_pid="root",                            │
    content="请重新分析",                         │
  ))                                            │
  ├─ 忽略订阅表                                  │
  ├─ input_queues["analyzer"].put_nowait(msg)   │
  └─ done                                       │
                                                ▼
                                     analyzer.adapter.receive()
                                       → UserRequest(text="请重新分析",
                                                     metadata={from:"root"})
```

#### 场景 C：无订阅者降级（TextEvent → AgentOutput SystemEvent）

```
root
  │
  ├─ adapter.send(TextEvent("代码库有50个文件..."), target=None)
  │
  ▼
KernelBridgeAdapter.send()
  ├─ target is None → publish path,
  │   on_no_subscriber = console.send   ← 降级回调由 KBA 注入
  │
  ▼
MessageBus.publish(
  from="root",
  event=TextEvent("代码库有50个文件..."),
  on_no_subscriber=console.send
)
  ├─ 查表: _subscriptions["root"] → {} (空)
  ├─ 无订阅者 + event is TextEvent + on_no_subscriber 非空
  │  → await on_no_subscriber(AgentOutput(pid="root", content="代码库有50个文件..."))
  └─ done
      │
      ▼
  CliConsole: "[root] 代码库有50个文件..."
```

StopEvent 无订阅者时的路径：

```
MessageBus.publish(from="root", event=StopEvent(...), on_no_subscriber=<any>)
  ├─ 查表: 空
  ├─ event is StopEvent → 静默丢弃
  │   （即使 on_no_subscriber 有值也不调用——StopEvent 语义上不需要降级）
  └─ done
```

#### 场景 D：级联终止查询（Kernel._on_agent_finished）

```
Kernel._on_agent_finished(collector_runtime)
  │
  ├─ 查询: 谁订阅了 "collector"？
  │
  ▼
MessageBus.get_subscribers_of("collector")
  ├─ _subscriptions["collector"] → {"analyzer"}
  └─ return ["analyzer"]
  │
  ▼
Kernel（策略层）:
  ├─ for sub_pid in ["analyzer"]:
  │    if analyzer.state not in (FINISHED, TERMINATING):
  │      analyzer.should_exit = True
  │      input_queues["analyzer"].put_nowait(__EXIT_SENTINEL__)
  └─ done
```

注意：MessageBus **只负责查询订阅关系**，不推送 sentinel。sentinel 推送是 Kernel 的策略行为。

#### 场景 E：通信退出保护（KernelBridgeAdapter 层，不触及 MessageBus）

```
agent 已 should_exit=True，LLM 返回后仍产出了 TextEvent:
  │
  ├─ adapter.send(TextEvent("还没说完..."), target=None)
  │
  ▼
KernelBridgeAdapter.send()
  ├─ should_exit? → TRUE
  └─ return  静默丢弃，不触及 MessageBus
```

退出保护在 KBA 层，不在 MessageBus 中——这是故意的：MessageBus 不做策略，KBA 作为 agent 的 I/O 守门人在消息发出前就拦下。

#### 场景 F（补充）：默认订阅（child_finished）— 不经过 MessageBus

这是一个重要的非 MessageBus 路径，特此单独说明以避免误解：

```
collector FINISHED
  │
  ▼
Kernel._on_agent_finished(collector_runtime)
  ├─ 1. console.send(AgentFinished("collector", ...))   → SystemConsole
  ├─ 2. send_input(parent, UserRequest(...))             → 直接入队 parent 的 input_queue
  │      └─ 不经过 MessageBus.publish()
  ├─ 3. subscribers = message_bus.get_subscribers_of("collector")
  │      → 级联终止（通过 MessageBus 查询订阅关系，
  │         但 sentinel 由 Kernel 直接推送，不走 publish）
  └─ done
      │
      ▼
  parent.adapter.receive()
    → UserRequest(text="[collector] 已完成。...",
                  metadata={type: "child_finished", pid: "collector", ...})
```

**关键区别**：
- **默认订阅（child_finished）**：走 `Kernel.send_input(parent)` → 直接 `put_nowait` 到父的 input_queue。不经过 MessageBus。
- **显式订阅（subscribe 声明）**：走 `MessageBus.publish()` → 查订阅表路由。
- **级联终止**：走 `MessageBus.get_subscribers_of()` 查询订阅关系，但 sentinel 推送由 Kernel 直接执行。

这三个路径互相独立。父 agent 不会收到重复通知——检查 parent 是否也显式 subscribe 了子 agent，若是则跳过默认订阅推送（在 `_on_agent_finished` 中去重）。

### 2.3 接口定义

```python
class MessageBus:
    """pub-sub 路由表 + 消息投递。

    职责边界：
    - 维护 publisher → subscribers 映射
    - publish() 时查表路由到 input_queues
    - direct() 时跳过订阅表直接投递
    - 提供 get_subscribers_of() 供 Kernel 级联终止查询
    - 不负责退出保护（在 KernelBridgeAdapter 层）
    - 不负责 sentinel 推送（在 Kernel 层）
    """

    def __init__(
        self,
        input_queues: dict[str, asyncio.Queue],
        console: 'SystemConsole | None' = None,
    ):
        """初始化 MessageBus。

        Args:
            input_queues: Kernel 维护的 per-agent 输入队列。MessageBus 持有引用以投递消息。
            console: SystemConsole 引用，用于 publish() 内部 fallback 降级。
                     可选；为 None 时降级回调由调用方通过 on_no_subscriber 参数注入。
        """

    def subscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """建立订阅：subscriber 接收 publisher 的每轮 TextEvent/StopEvent。

        纯内存操作（dict/set），同步方法。

        Raises:
            ValueError: subscriber_pid == publisher_pid（不允许自订阅）
        """

    async def publish(
        self,
        from_pid: str,
        event: 'Event',
        on_no_subscriber: 'Callable[[SystemEvent], Awaitable[None]] | None' = None,
    ) -> None:
        """向 from_pid 的所有订阅者广播 event。

        async 因为有降级路径需要 await on_no_subscriber。

        **可路由事件类型**：仅 TextEvent 和 StopEvent。其他事件类型
        （ThinkingEvent、ToolCallEvent、ToolResultEvent）不应传入 publish()
        ——调用方（KernelBridgeAdapter.send）在调用 publish 前过滤。

        无订阅者时：
        - event is TextEvent → 调用 on_no_subscriber；若为 None，
          尝试 self._console.send（内部 fallback）；都不可用则静默丢弃
        - event is StopEvent → 静默丢弃（不调用 on_no_subscriber）

        订阅者已 FINISHED（其 input_queue 已从 input_queues 中移除）→ 跳过
        """

    def direct(self, target_pid: str, message: 'InternalMessage') -> None:
        """定向投递：跳过订阅表，直接投递到 target_pid 的队列。

        纯 dict 查找 + asyncio.Queue.put_nowait，同步方法。

        Raises:
            KeyError: target_pid 不在 input_queues 中
        """

    def get_subscribers_of(self, publisher_pid: str) -> list[str]:
        """返回订阅了 publisher_pid 的所有 pid 列表。

        无订阅者返回空列表。纯查询，无副作用。
        """

    def unsubscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """取消订阅。agent FINISHED 时由 Kernel 调用来清理订阅表。

        如果订阅关系不存在，静默返回（幂等）。
        """

    def remove_publisher(self, publisher_pid: str) -> None:
        """移除 publisher 的所有订阅关系。publisher FINISHED 时由 Kernel 调用。"""
```

### 2.4 内部数据模型

```python
from dataclasses import dataclass, field
import time

@dataclass
class InternalMessage:
    """MessageBus 内部消息格式。

    与 UserRequest 的区别：
    - UserRequest 是 LLM 看到的"用户输入"，经 ContextAssembler 组装进 messages
    - InternalMessage 是 MessageBus 内部的投递单元，
      经 KernelBridgeAdapter.receive() 转换为 UserRequest
    """
    from_pid: str
    content: str               # TextEvent.content 或 "" (for StopEvent)
    metadata: dict = field(default_factory=dict)
    # metadata 示例：
    #   {"stop": True}             — 原始 event 为 StopEvent
    #   {}                         — 原始 event 为 TextEvent
    created_at: float = field(default_factory=time.time)
```

订阅表结构：

```python
# publisher_pid → {subscriber_pid, ...}
_subscriptions: dict[str, set[str]]
```

为什么是单向映射（publisher → subscribers）而非双向：

- `publish(from_pid)` 需要快速查 "谁订阅了 from_pid"
- `get_subscribers_of(publisher_pid)` 同样是这个方向
- 反向查询 "谁被 subscriber_pid 订阅" 目前没有场景需要
- 如果后续需要（如 agent FINISHED 时清理它的出向订阅），加一个 `_reverse: dict[str, set[str]]` 即可

### 2.5 方法伪代码

#### subscribe

```
def subscribe(subscriber_pid, publisher_pid):
    if subscriber_pid == publisher_pid:
        raise ValueError(f"self-subscription not allowed: {subscriber_pid}")

    if publisher_pid not in self._subscriptions:
        self._subscriptions[publisher_pid] = set()

    self._subscriptions[publisher_pid].add(subscriber_pid)
```

#### publish（核心方法）

```
async def publish(from_pid, event, on_no_subscriber=None):
    subscribers = self._subscriptions.get(from_pid, set())

    # 过滤：只投递给 input_queues 中仍存在的订阅者
    # （agent FINISHED 后其 input_queue 可能已被 Kernel 清理）
    active_subscribers = {
        pid for pid in subscribers
        if pid in self._input_queues
    }

    if not active_subscribers:
        # 无活跃订阅者
        if isinstance(event, TextEvent):
            # 降级优先级：on_no_subscriber > self._console > 静默丢弃
            if on_no_subscriber is not None:
                await on_no_subscriber(
                    AgentOutput(pid=from_pid, content=event.content)
                )
            elif self._console is not None:
                await self._console.send(
                    AgentOutput(pid=from_pid, content=event.content)
                )
            # else: 静默丢弃
        # StopEvent + 无订阅者 → 静默丢弃（无论 callbacks 如何）
        return

    # 构造内部消息
    msg = InternalMessage(
        from_pid=from_pid,
        content=event.content if isinstance(event, TextEvent) else "",
        metadata={"stop": True} if isinstance(event, StopEvent) else {},
    )

    # 广播到所有活跃订阅者
    for sub_pid in active_subscribers:
        self._input_queues[sub_pid].put_nowait(msg)
```

#### direct

```
def direct(target_pid, message):
    if target_pid not in self._input_queues:
        raise KeyError(f"target_pid '{target_pid}' not found in input_queues")

    msg = InternalMessage(
        from_pid=message.from_pid,
        content=message.content,
        metadata=message.metadata,
    )

    self._input_queues[target_pid].put_nowait(msg)
```

#### get_subscribers_of

```
def get_subscribers_of(publisher_pid):
    return list(self._subscriptions.get(publisher_pid, set()))
```

#### unsubscribe

```
def unsubscribe(subscriber_pid, publisher_pid):
    if publisher_pid in self._subscriptions:
        self._subscriptions[publisher_pid].discard(subscriber_pid)
        # 如果该 publisher 再无订阅者，清理空 set
        if not self._subscriptions[publisher_pid]:
            del self._subscriptions[publisher_pid]
```

#### remove_publisher

```
def remove_publisher(publisher_pid):
    self._subscriptions.pop(publisher_pid, None)
```

### 2.6 边界条件

| 条件 | publish 行为 | direct 行为 |
|------|-------------|------------|
| 目标 queue 不存在（agent FINISHED）| 跳过该订阅者 | raise KeyError |
| 发布者无订阅者 + TextEvent + on_no_subscriber 非空 | 调用 on_no_subscriber | N/A |
| 发布者无订阅者 + TextEvent + on_no_subscriber 为 None | 静默丢弃 | N/A |
| 发布者无订阅者 + StopEvent | 静默丢弃（不管 on_no_subscriber） | N/A |
| 订阅者自己订阅自己 | subscribe 时 raise ValueError | N/A |
| 重复 subscribe 同一对 | 幂等（set 自动去重） | N/A |
| target_pid 不在 input_queues | N/A | raise KeyError |
| from_pid 从未被 subscribe 过 | _subscriptions.get 返回 {}, 走降级 | N/A |

### 2.7 publish() 内部降级 vs on_no_subscriber 回调

MessageBus 构造函数接受可选的 `console` 引用用于内部降级。`publish()` 也接受 `on_no_subscriber` 回调。两者互补：

- **`on_no_subscriber` 参数（外部注入）**：由 KBA 在调用 publish 时传入 `console.send`。允许调用方自己决定降级策略（如 Mode B 下可能要用不同的格式化方式）
- **`console` 引用（内部 fallback）**：当 `on_no_subscriber` 为 None 且 `event is TextEvent` 时，尝试用内部 `console` 降级。如果两者都没有，静默丢弃

优先级：`on_no_subscriber`（显式） > `self._console`（内部） > 静默丢弃

---

## 三、Batch 文件布局

### 3.1 设计决策：方案 B — Protocol 进 interfaces，Orchestrator 进 core

`AsyncLifecycleOrchestrator` 和 `LifecycleOrchestrator` 是同一种东西（三阶段生命周期编排），仅同步/异步的差异。放在同一个包 `harness/core/` 下符合就近原则。

`AsyncInputAdapter` 和 `SystemConsole` 是两个新 Protocol，放 `harness/interfaces/` 符合现有约定（所有 Protocol 都在 interfaces）。

`harness/runtime/` 收窄为真正属于 Runtime 层的东西：进程模型（Kernel, AgentRuntime）、消息总线（MessageBus）、工作流脚本加载（decorators, tools）、系统控制台实现（CliConsole）。

### 3.2 完整包结构

```
harness/
├── interfaces/                         # ← 新增 2 个 Protocol 文件
│   ├── input_adapter.py                #   (不动) InputAdapter Protocol
│   ├── async_input_adapter.py          # + AsyncInputAdapter Protocol
│   └── system_console.py               # + SystemConsole Protocol
│
├── core/                               # ← 新增 1 个文件
│   ├── orchestrator.py                 #   (不动) LifecycleOrchestrator
│   └── async_orchestrator.py           # + AsyncLifecycleOrchestrator
│
├── runtime/                            # ← 全新子包
│   ├── __init__.py
│   ├── kernel.py                       #   Kernel 全局单例
│   ├── agent_runtime.py                #   AgentRuntime + AgentState
│   ├── bridge_adapter.py               #   KernelBridgeAdapter
│   ├── message_bus.py                  #   MessageBus + InternalMessage
│   ├── cli_console.py                  #   CliConsole (实现 SystemConsole)
│   ├── decorators.py                   #   @agent + subscribe + registry
│   ├── tools.py                        #   spawn_workflow / end_workflow / ...
│   ├── signals.py                      #   SIGINT 两阶段 handler
│   └── runtime.py                      #   Runtime 顶层入口
│
├── di.py                               # (不动)
├── hooks/                              # (不动)
├── adapters/                           # (不动)
├── components/                         # (不动)
├── messaging/                          # (不动)
└── config/                             # (不动)
```

### 3.3 逐 Batch 文件清单

#### Batch 0：Async 接口层 + AsyncLifecycleOrchestrator

```
[新增]
 harness/interfaces/
  └── async_input_adapter.py      # AsyncInputAdapter Protocol
                                  #   - async receive() → UserRequest
                                  #   - async send(event, target=None) → None
 harness/core/
  └── async_orchestrator.py       # AsyncLifecycleOrchestrator
                                  #   - 三阶段 async 迁移
                                  #   - 构造函数显式接收 adapter 参数
                                  #     (不走 container.resolve，避免
                                  #     DIContainer 注册新 Protocol)
 harness/runtime/
  ├── __init__.py                 # 空模块
  ├── types.py                    # 共享类型：InternalMessage, __EXIT_SENTINEL__
  │                               #   - InternalMessage: from_pid, content, metadata, created_at
  │                               #   - __EXIT_SENTINEL__: 模块级 sentinel 对象（asyncio.Queue 哨兵）
  └── bridge_adapter.py           # KernelBridgeAdapter（初版）
                                  #   - 实现 AsyncInputAdapter
                                  #   - 内联降级路由：TextEvent → SystemConsole,
                                  #     StopEvent → 丢弃
                                  #   - 退出保护（should_exit 检查）
                                  #   - 事件类型过滤：
                                  #     仅 TextEvent/StopEvent → MessageBus 路由
                                  #     中间事件（Thinking/ToolCall/ToolResult）
                                  #       → 直接降级到 SystemConsole
```

**依赖**: 无新增前置依赖。`AsyncLifecycleOrchestrator` 依赖现有 `DIContainer`、`ToolRouter`、`ContextAssembler` 等——全部已存在。

#### Batch 1：Runtime + Kernel + AgentRuntime 骨架

```
[新增]
 harness/interfaces/
  └── system_console.py           # SystemConsole Protocol
                                  #   - async receive() → SystemCommand
                                  #   - async send(event: SystemEvent) → None
 harness/runtime/
  ├── kernel.py                   # Kernel 类
  │                               #   数据结构: runtime_table, input_queues,
  │                               #     message_bus(初版为 None), workflow_table,
  │                               #     _tasks, _spawn_counter, _console, _shutdown
  │                               #   公开方法: spawn_root, send_input, kill,
  │                               #     end_workflow, finish_agent, list_agents,
  │                               #     all_finished
  ├── agent_runtime.py            # AgentRuntime + AgentState
  │                               #   AgentState: CREATED/INIT/RUNNING/
  │                               #     TERMINATING/FINISHED
  │                               #   模式: continuous / oneshot
  │                               #   run() 协程 (含 should_exit / max_rounds /
  │                               #     oneshot 自动退出逻辑)
  │                               #   _idle_for_quiescence()
  ├── cli_console.py              # CliConsole (实现 SystemConsole)
                                  #   Batch 1-3: 仅纯文本路由
                                  #   （Mode A → CommandTalk to root）
                                  #   Batch 4: 完善 / 命令解析
  ├── signals.py                  # create_sigint_handler() 工厂函数
  └── runtime.py                  # Runtime 类
                                  #   - run(harness) → Mode A 入口
                                  #   - sync→async LLM bridge
                                  #     (asyncio.to_thread)

[修改]
 harness/runtime/
  └── bridge_adapter.py           # 无代码改动。Batch 0 已定义完整，
                                  #   Batch 1 开始传入真 Kernel（替代 mock）
                                  #   进行端到端测试
```

#### Batch 2：Workflow 脚本加载

```
[新增]
 harness/runtime/
  ├── decorators.py               # @agent 装饰器 + subscribe() 函数
  │                               #   模块级全局:
  │                               #   - _agent_registry: dict[name, Blueprint]
  │                               #   - _subscription_registry: list[SubRecord]
  └── tools.py                    # Runtime 管理 Tool 集
                                  #   - create_spawn_workflow_tool(kernel)
                                  #   - create_end_workflow_tool(kernel)
                                  #   - create_finish_agent_tool(kernel)
                                  #   - create_talk_to_tool(kernel)
                                  #   - create_list_agents_tool(kernel)
                                  #   每个工厂函数返回 ToolDefinition dict

[修改]
 harness/runtime/
  └── kernel.py                   # 新增字段: _pending_subscriptions: list[tuple[str,str]]
                                  # 新增方法: spawn_from_script() 方法
                                  #   (14 步：清空 registry → 加载脚本 →
                                  #    创建 AgentRuntime → **暂存订阅关系** →
                                  #    推送 SystemConsole → 启动 task →
                                  #    记录 workflow → 投递 entry_prompt)
                                  #   ⚠ 步骤 4 在 Batch 2 暂存订阅关系到
                                  #   Kernel._pending_subscriptions，
                                  #   因为 MessageBus 在 Batch 3 才具备
                                  #   subscribe() 能力
```

**Batch 2 订阅暂存说明**：`Kernel.spawn_from_script()` 在步骤 4 读取 `_subscription_registry`，但此时 MessageBus 还是 `None`（Batch 1 遗留）或空壳。因此 Batch 2 将订阅关系存入 `Kernel._pending_subscriptions: list[tuple[str, str]]`。Batch 3 MessageBus 创建后，Kernel 在 `__init__` 或首次 `spawn_from_script` 时将所有 pending 订阅注册到 MessageBus。对 Batch 2 期间的 agent 行为无影响——因为 Batch 2 还没有 subscribe 声明的 agent 需要通信（所有子 agent 之间的通信在 Batch 3 才启用）。

> ⚠️ **Batch 2 职责边界（本 batch 明确不做）**：
> - **`subscribe` 声明仅影响 agent mode**（有订阅关系的 agent → `continuous`，否则 → `oneshot`）。**不做消息路由**——MessageBus 在 Batch 3 创建后才能投递消息
> - **`child_finished` 自动通知不实现**：`_on_agent_finished` 仍为 Batch 1 stub（仅推送 `AgentFinished` 到 SystemConsole），不构造 `child_finished` UserRequest 也不向父 agent 推送。父 agent 需通过 `list_agents` tool 主动查询或子 agent 通过 `talk_to` 主动汇报
> - **级联终止不实现**——`_on_agent_finished` 不通过 MessageBus 查询订阅者并推送 `__EXIT_SENTINEL__`
> - **静默检测完整实现不包含**——`_monitor_quiescence` 仍为 Batch 1 stub（仅等 `all_finished()`）
> - **Mode B `run_from_script` 入口不实现**——属于 Batch 3

#### Batch 3：消息订阅 + 并发 + 终止

```
[新增]
 harness/runtime/
  └── message_bus.py              # MessageBus 类
                                  #   完整实现: subscribe/publish/direct/
                                  #     get_subscribers_of/unsubscribe/
                                  #     remove_publisher
                                  #   （InternalMessage 从 harness.runtime.types 导入）

[修改]
 harness/runtime/
  ├── kernel.py                   # 完善:
  │                               #   - _on_agent_finished: 默认订阅(child_finished)
  │                               #     推送 + 级联终止(通过 MessageBus.
  │                               #     get_subscribers_of)
  │                               #   - _monitor_quiescence: 实际实现(不再是 stub)
  │                               #   - workflow_table 集成静默检测
  ├── bridge_adapter.py           # 替换内联降级路由 → 连接 MessageBus:
  │                               #   - target=None → message_bus.publish(...)
  │                               #   - target=pid → message_bus.direct(...)
  └── runtime.py                  # 新增: run_from_script(script_path) → Mode B
```

#### Batch 4：系统命令 + 信号处理

```
[修改]
 harness/runtime/
  ├── kernel.py                   # 完善: _handle_system_input 命令解析循环
  ├── cli_console.py              # 完善:
  │                               #   - receive() 命令解析规则
  │                               #     (/agents /kill /end /exit /talk)
  │                               #   - send() 格式化输出
  ├── signals.py                  # 完善: Runtime.run() 中 signal handler 注册
  └── runtime.py                  # 完善:
                                  #   - asyncio.gather(return_exceptions=True)
                                  #   - asyncio.shield 保护 _phase_end
```

#### Batch 5：打磨

```
[可能新增]
 harness/runtime/
  └── error.py                    # (可选) AgentErrorInfo 类型，标准化 error 信息

[修改]
 harness/runtime/
  └── agent_runtime.py            # error → child_finished.metadata.error 对齐
 docs/                            # 文档更新
 examples/                        # 新示例替换旧 Recursive Harness Pattern
```

### 3.4 关键依赖解耦：KBA ↔ MessageBus

**问题**：KernelBridgeAdapter（Batch 0）依赖 MessageBus（Batch 3）的 publish/direct。

**解决方案**：Batch 0-2 期间 KBA 使用内联降级路由；Batch 3 切换到真实 MessageBus。

**Batch 0-2 代码路径**（bridge_adapter.py 初版）：

```python
class KernelBridgeAdapter:
    def __init__(self, pid, kernel, runtime):
        self._pid = pid
        self._kernel = kernel
        self._runtime = runtime

    async def receive(self) -> UserRequest:
        item = await self._kernel.input_queues[self._pid].get()
        if item is __EXIT_SENTINEL__:
            return UserRequest(text="", metadata={"exit": True})
        elif isinstance(item, InternalMessage):
            return UserRequest(
                text=item.content,
                metadata={**item.metadata, "from": item.from_pid}
            )
        elif isinstance(item, UserRequest):
            return item

    async def send(self, event, target=None):
        if self._runtime.should_exit:
            return  # 退出保护

        # ── 事件类型分流 ──
        # 仅 TextEvent/StopEvent 参与 MessageBus 路由；
        # 中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole
        if not isinstance(event, (TextEvent, StopEvent)):
            # 中间事件：直接推给 SystemConsole（不经过 MessageBus）
            if isinstance(event, (ThinkingEvent, ToolCallEvent, ToolResultEvent)):
                await self._kernel._console.send(
                    AgentOutput(
                        pid=self._pid,
                        content=f"[{type(event).__name__}] {event.content}"
                    )
                )
            return

        if target is not None:
            # 定向投递：不经过 MessageBus，直接入队
            self._kernel.input_queues[target].put_nowait(
                InternalMessage(
                    from_pid=self._pid,
                    content=event.content if isinstance(event, TextEvent) else "",
                    metadata={"stop": True} if isinstance(event, StopEvent) else {},
                )
            )
        else:
            # Batch 0-2 降级路由（无 MessageBus）：
            # TextEvent → SystemConsole, StopEvent → 丢弃
            if isinstance(event, TextEvent):
                await self._kernel._console.send(
                    AgentOutput(pid=self._pid, content=event.content)
                )
            # StopEvent → 丢弃
```

**Batch 3 替换**（bridge_adapter.py 更新）：

```python
async def send(self, event, target=None):
    if self._runtime.should_exit:
        return

    # ── 事件类型分流（同 Batch 0-2 逻辑）──
    if not isinstance(event, (TextEvent, StopEvent)):
        if isinstance(event, (ThinkingEvent, ToolCallEvent, ToolResultEvent)):
            await self._kernel._console.send(
                AgentOutput(
                    pid=self._pid,
                    content=f"[{type(event).__name__}] {event.content}"
                )
            )
        return

    if target is not None:
        self._kernel.message_bus.direct(
            target,
            InternalMessage(
                from_pid=self._pid,
                content=event.content if isinstance(event, TextEvent) else "",
                metadata={"stop": True} if isinstance(event, StopEvent) else {},
            )
        )
    else:
        await self._kernel.message_bus.publish(
            from_pid=self._pid,
            event=event,
            on_no_subscriber=(
                self._kernel._console.send
                if isinstance(event, TextEvent) else None
            )
        )
```

**为什么这个过渡是安全的**：Batch 0-2 期间只有单 agent（Mode A root）或刚 spawn 出来的子 agent。子 agent 间的 subscribe 通信在 Batch 3 才引入，所以在无 MessageBus 的期间，所有 TextEvent 降级到 SystemConsole 是正确的行为。

### 3.5 `__init__.py` 更新

新增模块需要更新对应的 `__init__.py` 以保持可发现性：

| Batch | 需更新的 `__init__.py` | 新增 re-export |
|-------|----------------------|---------------|
| Batch 0 | `harness/interfaces/__init__.py` | `AsyncInputAdapter` |
| Batch 0 | `harness/core/__init__.py` | `AsyncLifecycleOrchestrator` |
| Batch 1 | `harness/interfaces/__init__.py` | `SystemConsole` |
| Batch 1 | `harness/runtime/__init__.py` | (创建模块) |
| Batch 2 | `harness/runtime/__init__.py` | `agent`, `subscribe` (decorators) |

### 3.6 已知耦合：kernel ↔ agent_runtime ↔ bridge_adapter

```
kernel.py → agent_runtime.py → bridge_adapter.py → kernel.py
   (创建)      (创建 KBA 实例)     (引用 kernel.input_queues)
```

三者形成循环引用。在 Python 中这可通过运行时属性访问（`runtime._kernel` 在 AgentRuntime 构造后设置）解决——不是 import 级别的环。但**意味着这三个模块不能单独单元测试**：测试一个就需要另外两个存在（至少以 stub 形式）。Batch 1 的测试策略需考虑这一约束。

### 3.7 跨 Batch 接口契约

每个 batch 完成后，暴露给后续 batch 的稳定 import 接口：

```
Batch 0 → Batch 1:
  from harness.interfaces.async_input_adapter import AsyncInputAdapter
  from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
  from harness.runtime.bridge_adapter import KernelBridgeAdapter
  from harness.runtime.types import InternalMessage, __EXIT_SENTINEL__

Batch 1 → Batch 2:
  from harness.interfaces.system_console import SystemConsole
  from harness.runtime.kernel import Kernel
  from harness.runtime.agent_runtime import AgentRuntime, AgentState
  from harness.runtime.cli_console import CliConsole
  from harness.runtime.signals import create_sigint_handler
  from harness.runtime.runtime import Runtime

Batch 2 → Batch 3:
  from harness.runtime.decorators import agent, subscribe
  from harness.runtime.tools import (
      create_spawn_workflow_tool,
      create_end_workflow_tool,
      create_finish_agent_tool,
      create_talk_to_tool,
      create_list_agents_tool,
  )
  # Kernel 新增方法: spawn_from_script()

Batch 3 → Batch 4:
  from harness.runtime.message_bus import MessageBus, InternalMessage
  # Kernel._on_agent_finished 支持级联终止
  # Runtime.run_from_script 支持 Mode B

Batch 4 → Batch 5:
  # 所有接口稳定，Batch 5 只做文档和打磨
```

### 3.8 内部方法逐 Batch 完成度矩阵

以下 Kernel 内部方法在多个 batch 中逐步完善，本表记录每个 batch 的版本状态：

| 方法 | Batch 1 | Batch 2 | Batch 3 | Batch 4 |
|------|---------|---------|---------|---------|
| `_on_agent_finished(runtime)` | stub：仅推送 `AgentFinished` 到 SystemConsole | （不变） | **完整实现**：+ 默认订阅推 `child_finished` 到父 agent + 通过 MessageBus 查询订阅者并级联推送 sentinel | （不变） |
| `_monitor_quiescence()` | stub：`while not all_finished(): await asyncio.sleep(1)` | （不变） | **完整实现**：检查所有非 FINISHED agent 是否 idle，若是则全体推送 sentinel | （不变） |
| `_handle_system_input()` | stub：仅 `readline` → 纯文本路由到 root（Mode A） | （不变） | （不变） | **完整实现**：`/agents` `/kill` `/end` `/exit` `/talk` 命令解析 + `SystemEvent` 查询响应 |
| `spawn_root(harness)` | **完整实现** | （不变） | （不变） | （不变） |
| `spawn_from_script(path, parent)` | — | **完整实现**（含 `_pending_subscriptions` 暂存） | 升级：将 `_pending_subscriptions` 注册到 MessageBus | （不变） |

说明：
- **stub**：方法存在且可调用，但只做最小必要工作让系统跑通
- **完整实现**：行为符合顶层设计 spec
- **（不变）**：本 batch 不修改该方法
- **—**：方法尚不存在

---

## 四、Batch Spec 编写指南

### 4.1 Batch Spec 模板

每份 batch spec 文档（`docs/superpowers/specs/YYYY-MM-DD-batch-N-<topic>.md`）按以下结构编写：

```markdown
# Batch N: <标题>

> 版本: X.X | 日期: YYYY-MM-DD | 状态: 设计评审
> 依赖: Batch N-1 的接口契约（列出具体 import）

## 一、目标

一句话描述 + bullet list：本 batch 结束时，用户可以做什么。

## 二、前置条件

必须已经可用的接口/类（从上一 batch 的契约中选出）。

## 三、新增/修改的组件

| 组件 | 类型 | 文件 | 是否需展开内部设计 |
|------|------|------|------------------|
| Foo  | 新增 | harness/xxx/foo.py | 是 |
| Bar  | 修改 | harness/xxx/bar.py | 否（仅加一个方法） |

## 四、关键组件设计

（仅对"需展开内部设计"的组件）

每个组件：
- 类签名 / 公开方法
- 内部数据模型（状态机、关键 dict、枚举）
- 核心方法伪代码
- 与其他组件的交互序列

## 五、数据流

本 batch 引入的新的消息/数据流动路径。画文本线图。

## 六、错误处理

本 batch 需要处理的异常场景 + 处理策略。

## 七、测试策略

- 单元测试场景（含 mock 策略）
- 集成测试场景
- 需要 mock 的外部依赖

## 八、验收标准

可验证的 checklist，格式："给定 X，当 Y，则 Z"
```

### 4.2 逐 Batch 设计要点

以下是每个 batch 在编写 spec 时**必须展开回答**的关键问题清单。这些问题应该在各自 batch spec 的"关键组件设计"和"数据流"章节中得到回答。

#### Batch 0：Async 接口层 + AsyncLifecycleOrchestrator

| # | 要点 | 关键问题 |
|---|------|---------|
| **0** | **Outer-loop 归属** ⚠ | **核心架构决策**。顶层设计 Section 3.4 中 `AgentRuntime.run()` 有外层 while 循环（控制 max_rounds, should_exit, adapter.receive），Section 12 中 `AsyncLifecycleOrchestrator` 参照 `LifecycleOrchestrator` 迁移——而现有的 `LifecycleOrchestrator._phase_loop()` **自己就有外层 while 循环**（等待 adapter.receive）。两个外循环不兼容。必须选择：(A) `_phase_loop()` 重构为仅执行一轮（内循环），`AgentRuntime.run()` 拥有外循环；(B) `_phase_loop()` 保留完整内+外循环，`AgentRuntime.run()` 只调用一次。这个选择影响 max_rounds 计数位置、should_exit 与 orchestrator 内部 _should_exit_flag 的交互、以及 `_idle_for_quiescence()` 如何判断"agent 在等待输入"。**建议在 Batch 0 spec 中优先解决此问题** |
| 1 | `AsyncInputAdapter` Protocol | `send()` 的 `target` 参数默认值 `None`，语义是"走订阅路由"还是"广播给所有订阅者"？ |
| 2 | `AsyncCallLLM` Protocol | 签名用泛型 Callable 还是 `Protocol` class？tool 参数是必需还是可选？ |
| 3 | Orchestrator 构造函数 | 显式收 `adapter` 还是走 `container.resolve(AsyncInputAdapter)`？如果走 DI，需要注册新 Protocol——那就要碰 DIContainer |
| 4 | 三阶段迁移 | `_phase_init` / `_phase_loop` / `_phase_end` 中哪些调用需要 `await`？Hook 保持同步——Hook 内部是否可以调 async 函数？ |
| 5 | sync→async LLM bridge | `asyncio.to_thread` 的位置——在 `Runtime.run()` 入口层，不在 orchestrator 内部。orchestrator 只接收 `async callable` |
| 6 | KBA 初版降级路由 | 内联降级路由的行为：TextEvent → AgentOutput to console.send, StopEvent → 丢弃, target 非空 → 直接入队 input_queue。事件类型过滤：仅 TextEvent/StopEvent 走路由，Thinking/ToolCall/ToolResult → 直接降级到 SystemConsole |

#### Batch 1：Runtime + Kernel + AgentRuntime 骨架

| # | 要点 | 关键问题 |
|---|------|---------|
| 1 | `AgentState` 枚举 | TERMINATING 是否需要子状态区分触发原因（exit/error/oneshot/max_rounds）？对日志/调试的价值 vs 复杂度 |
| 2 | `AgentRuntime.run()` | `_idle_for_quiescence()` 的判断逻辑：检查 `_orchestrator` 的哪个内部状态？"不在 LLM 调用中"如何判断（orchestrator 内部 _phase_loop 的 while 是否提供了状态标记）？ |
| 3 | `Kernel` 初始化 | MessageBus 在 Batch 1 传 `None` 还是创建无订阅功能的空壳？`_tasks` dict 在 `__init__` 还是每次 spawn 时增量更新？ |
| 4 | `Kernel.spawn_root()` | 是否复用 `spawn_from_script` 的内部逻辑，还是独立实现？两者共享的"创建一个 AgentRuntime 并启动"逻辑是否应抽取为 `_spawn_one()` 私有方法？ |
| 5 | `SystemCommand` union type | 各 command 数据类的字段定义：`CommandTalk(pid, text)`, `CommandKill(pid)`, `CommandListAgents()`, `CommandEndWorkflow(flag)`, `CommandExit()`, `CommandTalkDirect(pid, text)` |
| 6 | `CliConsole.receive()` | `asyncio.to_thread(sys.stdin.readline)` 的线程安全问题：asyncio 单线程模型下，`to_thread` 返回的数据在 event loop 中处理。是否需要在 `readline` 和 `input()` 之间选择（readline 更可控）？ |
| 7 | `Runtime.run()` | `asyncio.gather(task_root, task_sys, task_mon)` — task_mon 在 Batch 1 是 stub（永远 sleep 或等待所有 agent FINISHED 才返回），还是已有 Interval 轮询的雏形？ |
| 8 | Mode A 的 CliConsole | 纯文本路由到 `root` agent 的前提：`spawn_root` 后 `pid="root"` 必须固定。如果用户 spawn 了第二个 root（虽然设计不允许）呢？ |

#### Batch 2：Workflow 脚本加载

| # | 要点 | 关键问题 |
|---|------|---------|
| 1 | `@agent` 装饰器 | `_agent_registry[name]` 的 value schema：`{name, entry_prompt, metadata, factory}`。`factory` 是否应返回 `Harness` 而非任意 callable？ |
| 2 | `subscribe()` | 多次调用 `subscribe("A").to("B")` 的行为——累加还是覆盖？错误处理：subscribe 中引用的 name 不在 `_agent_registry` 中——应该在 subscribe 调用时报错还是在 `spawn_from_script` 时统一校验？ |
| 3 | importlib 加载 | `sys.modules["_workflow_script"]` 覆盖时，旧模块的副作用（如已注册的 hook、已打开的文件描述符）是否需要清理？Python 的模块 gc 能否自动回收？ |
| 4 | `spawn_from_script` 回滚 | 步骤 3-6 中任一步失败 → 对已创建的 AgentRuntime 调用 kill() → 异常透传给 tool caller。需要记录已创建的 pid 列表用于回滚 |
| 5 | Tool 工厂函数 + 注册机制 | 5 个 tool 的 `ToolDefinition`（name, description, parameters JSON Schema）。Tool 的 execute 函数如何获取 `kernel` 引用——通过闭包（工厂函数捕获 kernel）还是注册到 DI 容器？**关键集成点**：这些 tool 定义最终需要能被 `SystemToolProvider` 返回给 `ToolRouter`。注册路径：谁调用工厂函数 → ToolDefinition 字典 → 如何注入到每个 agent 的 `SystemToolProvider`？可能方案：(a) Runtime 入口层注入到 container，(b) `AsyncLifecycleOrchestrator` 构造时自动追加，(c) 每个 agent 的 DI 容器在 spawn 时自动注册 |
| 6 | entry_prompt 注入时机 | 步骤 5-6（启动 Task）在步骤 8（投递 entry_prompt）之前——agent 启动后先 `await adapter.receive()` 才收到 entry_prompt，确保 agent 在 spawn 过程中不提前执行。但如果 agent 的 `receive()` 在 entry_prompt 入队之前就调用呢？需要确认 Task 创建和 Queue.put 的顺序保证 |
| 7 | Registry 隔离边界 | `_agent_registry` 和 `_subscription_registry` 是 `decorators.py` 的模块级全局变量，Kernel.spawn_from_script 在加载脚本前清空。如果两个 Runtime 实例并发调用 spawn_from_script（虽然 asyncio 单线程下不会真正并发）——模块级全局变量是安全的吗？单线程 + 同步清空 → 安全 |

#### Batch 3：消息订阅 + 并发 + 终止

| # | 要点 | 关键问题 |
|---|------|---------|
| 1 | MessageBus | 按 Section 2 的接口+伪代码。需补充：`_subscriptions` 管理——agent FINISHED 后 Kernel 调用 `remove_publisher(pid)` 清理（防止死 agent 积累） |
| 2 | 默认订阅 vs 显式订阅 | 默认订阅（child_finished）走 `Kernel._on_agent_finished` → `send_input(parent)`，不经过 MessageBus。显式订阅（subscribe）走 MessageBus.publish()。确保 parent 不会收到两份通知——检查 parent 是否也显式 subscribe 了子 agent |
| 3 | 级联 + 静默的交互 | collector FINISHED → 级联推送 `__EXIT_SENTINEL__` → analyzer 被唤醒 → analyzer TERMINATING → FINISHED。静默检测此时也看到 all_idle → 推送 sentinel → analyzer 可能收到第二次 sentinel。KernelBridgeAdapter.receive() 中对重复 sentinel 的处理——第一次收到 sentinel 后 agent 进入 TERMINATING，第二次 `input_queues[pid].get()` 可能已经取不到（queue 可能被清理）或取到但 `should_exit` 已为 True——幂等 |
| 4 | KBA 升级 | 从内联降级路由切换到 MessageBus。`on_no_subscriber` 回调签名匹配：`Callable[[SystemEvent], Awaitable[None]]` —— CliConsole.send 的签名必须匹配 |
| 5 | Mode B `run_from_script` | 和 `run()` 的差异：不创建 root agent；仍启动 `_handle_system_input`（Mode B 下用户仍可 `/talk`, `/agents`, `/kill`, `/end`, `/exit`）；结束条件：静默检测 or `/exit` or 所有 agent FINISHED |
| 6 | 静默检测 | 1s 轮询间隔的权衡——太短浪费 CPU，太长 Mode B 结束时用户感知延迟。1s 是合理的默认值吗？是否需要可配置？ |

#### Batch 4：系统命令 + 信号处理

| # | 要点 | 关键问题 |
|---|------|---------|
| 1 | 命令解析 | Edge cases：`/talk` 后的 text 含空行/换行（readline 只读一行）？`/kill` 不存在的 pid？空输入（用户只按回车）？Ctrl+D（EOF）？ |
| 2 | SIGINT 两阶段 | `loop.add_signal_handler(signal.SIGINT, handler)` 在 asyncio 中的限制——必须在主线程调用，不能在子线程的事件循环注册。如果 Runtime 在非主线程创建呢？ |
| 3 | `_handle_system_input` 并发 | 和 `_monitor_quiescence` 并发运行，两者都读 `runtime_table`。asyncio 单线程下不需要锁（无真正的并发），但 await 点之间状态可能变——需要用 `list()` 做快照还是接受"短暂不一致"？ |
| 4 | `CommandExit` 清理顺序 | 设 `_shutdown=True` → 推全体 sentinel → 等 gather 返回。如果此时 stdin 还有未读数据（管道输入），_handle_system_input 已退出，剩余输入丢失——是否算 bug？ |
| 5 | Mode B `/talk` 的语义 | `/talk <pid> <text>` 向指定 agent 投递 UserRequest。如果目标 agent 是 oneshot 且已 FINISHED——推送 `CommandError`？如果目标 agent 在 WAITING_INPUT——投递成功，agent 被唤醒 |

#### Batch 5：打磨

| # | 要点 | 关键问题 |
|---|------|---------|
| 1 | 异常信息标准化 | `AgentRuntime.error: str | None` → `child_finished.metadata.error` 的类型约定。`last_output` 为空时的 fallback：取 `_orchestrator._history` 最后一条 assistant role 的 content；空 history → `""` |
| 2 | System prompt 注入 | Runtime tool 说明注入到 system prompt 的格式和位置。由 ContextAssembler 还是 Kernel 负责？注入时机——每次 `assemble` 时动态注入（总是最新）还是 spawn 时注入一次？ |
| 3 | 旧代码迁移 | 现有 example 中哪些需改为 Runtime API？哪些可保留作为 sync 路径的演示？建议：新增 `examples/runtime/` 放 Runtime 示例，旧 `examples/minimal_agent.py` 保留 |
| 4 | 文档更新 | README、architecture overview、developer guide 需要新增 Runtime 章节。不覆盖旧文档，作为新功能追加 |

---

## 五、附录：MessageBus 与 KernelBridgeAdapter 的完整交互序列

```
agent (collector) 调用 adapter.send(TextEvent("..."), target=None)
        │
        ▼
KernelBridgeAdapter.send(event=TextEvent("..."), target=None)
  ├─ 1. should_exit? → false → 继续
  ├─ 2. target is None → publish 路径
  └─ 3. await message_bus.publish(
           from_pid="collector",
           event=TextEvent("..."),
           on_no_subscriber=console.send_if_text_event
         )
              │
              ▼
      MessageBus.publish()
        ├─ 4. 查 _subscriptions["collector"] → {"analyzer"}
        ├─ 5. 过滤: analyzer 仍在 input_queues 中 → 保留
        ├─ 6. 构造 InternalMessage(from_pid="collector", content="...")
        ├─ 7. input_queues["analyzer"].put_nowait(msg)
        └─ 8. return
              │
              ▼
      analyzer 的 KernelBridgeAdapter.receive()
        ├─ 9. item = await input_queues["analyzer"].get()
        └─ 10. return UserRequest(text="...", metadata={"from": "collector"})
              │
              ▼
      AsyncLifecycleOrchestrator._phase_loop() 内
        └─ 11. 下一轮 assemble_context() 看到该 UserRequest
```

---

## 六、关键设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| MessageBus 放 `harness/runtime/` | 是 | 它依赖 input_queues 和 Kernel，是 Runtime 层内部组件 |
| `AsyncInputAdapter` Protocol 放 `harness/interfaces/` | 是 | 符合现有约定（所有 Protocol 都在 interfaces） |
| `AsyncLifecycleOrchestrator` 放 `harness/core/` | 是 | 和 LifecycleOrchestrator 就近，两者是 sync/async 变种 |
| `SystemConsole` Protocol 放 `harness/interfaces/` | 是 | 同 AsyncInputAdapter |
| `CliConsole` 实现放 `harness/runtime/` | 是 | 实现类，不放 interfaces；且依赖 Runtime 环境 |
| KBA ↔ MessageBus 解耦 | 初版内联降级，Batch 3 切换 | Batch 0-2 无 subscribe 需求，降级到 SystemConsole 是正确的 |
| MessageBus 不负责退出保护 | 保护在 KBA 层 | MessageBus 只做路由机制，不做 agent 策略 |
| MessageBus 不推送 sentinel | sentinel 推送在 Kernel 层 | 级联终止是 Kernel 的策略决策 |
| publish() 内部降级 vs on_no_subscriber 回调 | 回调优先 | 允许 KBA 自定义降级策略，MessageBus 的 console 引用作为 fallback |
| Outer-loop 归属 | 方案 A：`_phase_loop()` 只跑一轮，`AgentRuntime.run()` 拥有外循环 | `AsyncLifecycleOrchestrator._phase_loop()` 重构为单轮执行（仅内层 while），`AgentRuntime.run()` 控制 max_rounds 计数、should_exit 检查和 adapter.receive() 调用。`_idle_for_quiescence()` 在 AgentRuntime 层判断"等待输入" |
| TextEvent 投递时机 | 立即投递 | `adapter.send(TextEvent)` 后立即经过 MessageBus → 订阅者 input_queue。顶层设计 Section 5.3 总结句"StopEvent 后投递"需要修正——实际行为是 TextEvent 立即可达，只是这一个 round 内只有最终的 TextEvent 被路由（中间 ToolCall/ToolResult 不路由） |
