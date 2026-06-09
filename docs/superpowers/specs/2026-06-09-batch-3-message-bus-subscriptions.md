# Batch 3: MessageBus + 消息订阅 + 并发 + 终止

> 版本: 0.1 | 日期: 2026-06-09 | 状态: 设计评审
> 依赖: Batch 0（Async 接口层 + AsyncLifecycleOrchestrator） + Batch 1（Runtime + Kernel + AgentRuntime 骨架） + Batch 2（Workflow 脚本加载 + Runtime 管理 Tool）

---

## 一、目标

在 Batch 2 的多 agent workflow 脚本加载基础上，实现 MessageBus pub-sub 消息路由、默认订阅（child_finished）、流式订阅（显式 subscribe）、级联终止、静默检测。使多 agent 协作闭环首次完整达成——父 agent spawn workflow → 子 agent 并发运行 → subscribe 流式消息路由 → 子 agent FINISHED → child_finished 自动通知父 agent → 静默检测自动结束。

本 batch 结束时：
- `MessageBus` 类可用，支持 pub-sub 路由、定向投递、订阅者查询、退订和 publisher 清理
- `KernelBridgeAdapter.send()` 从内联降级路由切换到真实 MessageBus.publish()/direct()
- `Kernel._on_agent_finished()` 完整实现：默认订阅推送 `child_finished` 给父 agent + 级联终止（通过 MessageBus 查询订阅者并推送 `__EXIT_SENTINEL__`）
- `Kernel._monitor_quiescence()` 完整实现：检测所有非 FINISHED agent 是否 idle，若是则全体推送 `__EXIT_SENTINEL__`
- `Kernel.spawn_from_script()` 升级：将 `_pending_subscriptions` 注册到 MessageBus
- `Runtime.run_from_script()` Mode B 入口可用，直接启动 workflow 脚本无需 `root` agent
- `WorkflowFinished` SystemEvent 定义，run_from_script 返回前汇总所有 agent 最终结果
- 全部现有测试继续通过

---

## 二、前置条件

以下接口/组件已存在且**不做任何修改**：

| 组件 | 位置 | 用途 |
|------|------|------|
| `AsyncInputAdapter` Protocol | `harness/interfaces/async_input_adapter.py` | AgentRuntime 的 I/O 通道 |
| `AsyncCallLLM` Protocol | `harness/interfaces/async_call_llm.py` | async LLM 调用契约 |
| `AsyncLifecycleOrchestrator` | `harness/core/async_orchestrator.py` | 三阶段编排（_phase_loop 仅执行一轮） |
| `KernelBridgeAdapter` | `harness/runtime/bridge_adapter.py` | 实现 AsyncInputAdapter，**本 batch 修改** send() 路由逻辑 |
| `AgentRuntime` / `AgentState` | `harness/runtime/agent_runtime.py` | 状态机 + oneshot/continuous + parent/children + run() |
| `Kernel` | `harness/runtime/kernel.py` | 进程表 + spawn_root / spawn_from_script / send_input / kill / end_workflow / finish_agent / list_agents / all_finished |
| `SystemConsole` Protocol | `harness/interfaces/system_console.py` | 系统级交互接口 |
| `CliConsole` | `harness/runtime/cli_console.py` | SystemConsole 默认 CLI 实现 |
| `Runtime` | `harness/runtime/runtime.py` | 顶层入口 + sync→async LLM bridge |
| `@agent` / `subscribe()` / registry | `harness/runtime/decorators.py` | Workflow 脚本声明 |
| `CompositeSystemToolProvider` + 5 Runtime tool | `harness/runtime/tools.py` | Runtime 管理 Tool 集 |
| `InternalMessage` / `__EXIT_SENTINEL__` / `AgentOutput` 等 | `harness/runtime/types.py` | 共享类型 |
| `DIContainer` | `harness/core/container.py` | DI 容器 |
| `ToolRouter` | `harness/core/tool_router.py` | Tool 执行分发 |
| `LifecycleOrchestrator` | `harness/core/orchestrator.py` | 同步版，不动 |

---

## 三、新增/修改的组件

| 组件 | 类型 | 文件 | 需展开内部设计 |
|------|------|------|--------------|
| `MessageBus` | 新增 | `harness/runtime/message_bus.py` | 是 |
| `WorkflowFinished` SystemEvent | 修改 | `harness/runtime/types.py`（追加） | 是 |
| `Kernel._on_agent_finished()` | 修改 | `harness/runtime/kernel.py` | 是（stub → 完整实现） |
| `Kernel._monitor_quiescence()` | 修改 | `harness/runtime/kernel.py` | 是（stub → 完整实现） |
| `Kernel.spawn_from_script()` | 修改 | `harness/runtime/kernel.py` | 是（+ MessageBus 订阅注册） |
| `Kernel.__init__()` | 修改 | `harness/runtime/kernel.py` | 否（创建 MessageBus 实例） |
| `KernelBridgeAdapter.send()` | 修改 | `harness/runtime/bridge_adapter.py` | 是（降级路由 → MessageBus） |
| `AgentRuntime.workflow_flag` | 修改 | `harness/runtime/agent_runtime.py` | 否（新增属性，由 Kernel 设置） |
| `Runtime.run_from_script()` | 修改 | `harness/runtime/runtime.py` | 是（新增 Mode B 入口） |
| `CliConsole.send()` | 修改 | `harness/runtime/cli_console.py` | 否（新增 WorkflowFinished 格式化） |
| `harness/runtime/__init__.py` | 修改 | `harness/runtime/__init__.py` | 否（仅加 re-export） |

### 3.1 跨 Batch 接口契约

Batch 3 完成后，暴露给 Batch 4 的稳定 import：

```python
from harness.runtime.message_bus import MessageBus
from harness.runtime.runtime import Runtime  # 新增 run_from_script 方法
from harness.runtime.types import (
    WorkflowFinished,  # 新增 SystemEvent
    # ... 其余类型不变
)
# Kernel._on_agent_finished 升级为完整实现（child_finished + 级联）
# Kernel._monitor_quiescence 升级为完整实现（idle 检测）
# KernelBridgeAdapter.send() 切换到 MessageBus
```

---

## 四、关键组件设计

### 4.1 MessageBus

#### 4.1.1 职责边界

MessageBus 是一个 **pub-sub 路由表 + 消息投递器**，做机制不做策略。

| 属于 MessageBus | 不属于 MessageBus |
|----------------|-------------------|
| 维护 publisher → subscribers 映射 | 退出保护（在 KernelBridgeAdapter 层） |
| publish() 时查表路由到 input_queues | 推送 `__EXIT_SENTINEL__`（在 Kernel 层） |
| direct() 时跳过订阅表直接投递 | 级联终止策略（在 `Kernel._on_agent_finished`） |
| 提供 get_subscribers_of() 供 Kernel 查询 | 静默检测（在 `Kernel._monitor_quiescence`） |
| unsubscribe() / remove_publisher() 清理 | 事件类型过滤（在 KBA 层） |

#### 4.1.2 类定义

```python
# harness/runtime/message_bus.py

from dataclasses import dataclass, field
import time
from typing import Callable, Awaitable

from ..interfaces.types import TextEvent, StopEvent


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
        input_queues: dict,
        console=None,
    ):
        """初始化 MessageBus。

        Args:
            input_queues: Kernel 维护的 per-agent 输入队列 dict[str, asyncio.Queue]。
                          MessageBus 持有引用以投递消息。
            console: SystemConsole 引用，用于 publish() 内部 fallback 降级。
                     可选；为 None 时降级回调由调用方通过 on_no_subscriber 参数注入。
        """
        self._input_queues = input_queues
        self._console = console

        # publisher_pid → {subscriber_pid, ...}
        self._subscriptions: dict[str, set[str]] = {}
```

#### 4.1.3 公开方法

**subscribe(subscriber_pid, publisher_pid)**

```python
def subscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
    """建立订阅：subscriber 接收 publisher 的每轮 TextEvent/StopEvent。

    纯内存操作（dict/set），同步方法。

    Raises:
        ValueError: subscriber_pid == publisher_pid（不允许自订阅）
    """
    if subscriber_pid == publisher_pid:
        raise ValueError(
            f"Self-subscription not allowed: "
            f"'{subscriber_pid}' cannot subscribe to itself."
        )

    if publisher_pid not in self._subscriptions:
        self._subscriptions[publisher_pid] = set()

    self._subscriptions[publisher_pid].add(subscriber_pid)
```

**publish(from_pid, event, on_no_subscriber=None)** — 核心方法

```
async def publish(self, from_pid, event, on_no_subscriber=None):
    """向 from_pid 的所有订阅者广播 event。

    async 因为有降级路径需要 await on_no_subscriber。

    可路由事件类型：仅 TextEvent 和 StopEvent。调用方（KernelBridgeAdapter.send）
    在调用 publish 前过滤中间事件（ThinkingEvent、ToolCallEvent、ToolResultEvent）。

    无订阅者时：
    - event is TextEvent → 调用 on_no_subscriber；若为 None，
      尝试 self._console.send（内部 fallback）；都不可用则静默丢弃
    - event is StopEvent → 静默丢弃（不调用 on_no_subscriber）

    订阅者已 FINISHED（其 input_queue 已从 input_queues 中移除）→ 跳过
    """
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
                from .types import AgentOutput
                await on_no_subscriber(
                    AgentOutput(pid=from_pid, content=event.content)
                )
            elif self._console is not None:
                from .types import AgentOutput
                await self._console.send(
                    AgentOutput(pid=from_pid, content=event.content)
                )
            # else: 静默丢弃
        # StopEvent + 无订阅者 → 静默丢弃（无论 callbacks 如何）
        return

    # 构造内部消息
    from .types import InternalMessage
    msg = InternalMessage(
        from_pid=from_pid,
        content=event.content if isinstance(event, TextEvent) else "",
        metadata={"stop": True} if isinstance(event, StopEvent) else {},
    )

    # 广播到所有活跃订阅者
    for sub_pid in active_subscribers:
        self._input_queues[sub_pid].put_nowait(msg)
```

**direct(target_pid, message)**

```python
def direct(self, target_pid: str, message) -> None:
    """定向投递：跳过订阅表，直接投递到 target_pid 的队列。

    message 是由调用方（KernelBridgeAdapter）构造的 InternalMessage 实例。
    direct() 直接入队，不做重新包装。

    纯 dict 查找 + asyncio.Queue.put_nowait，同步方法。

    Raises:
        KeyError: target_pid 不在 input_queues 中
    """
    if target_pid not in self._input_queues:
        raise KeyError(
            f"target_pid '{target_pid}' not found in input_queues"
        )

    self._input_queues[target_pid].put_nowait(message)
```

**get_subscribers_of(publisher_pid)**

```python
def get_subscribers_of(self, publisher_pid: str) -> list[str]:
    """返回订阅了 publisher_pid 的所有 pid 列表。

    无订阅者返回空列表。纯查询，无副作用。
    """
    return list(self._subscriptions.get(publisher_pid, set()))
```

**unsubscribe(subscriber_pid, publisher_pid)**

```python
def unsubscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
    """取消单个订阅关系。

    与 remove_publisher() 的区别：
    - unsubscribe("A", "B")：仅移除 A→B 这一条订阅关系
    - remove_publisher("B")：移除所有指向 B 的订阅关系

    Batch 3 中 unsubscribe 的直接调用场景有限——agent FINISHED 时
    使用 remove_publisher()（一步清理该 publisher 的所有订阅者）。
    unsubscribe() 保留给后续需要精细操作订阅表的场景（如动态
    修改订阅拓扑）。

    如果订阅关系不存在，静默返回（幂等）。
    """
    if publisher_pid in self._subscriptions:
        self._subscriptions[publisher_pid].discard(subscriber_pid)
        # 如果该 publisher 再无订阅者，清理空 set
        if not self._subscriptions[publisher_pid]:
            del self._subscriptions[publisher_pid]
```

**remove_publisher(publisher_pid)**

```python
def remove_publisher(self, publisher_pid: str) -> None:
    """移除 publisher 的所有订阅关系。publisher FINISHED 时由 Kernel 调用。"""
    self._subscriptions.pop(publisher_pid, None)
```

#### 4.1.4 边界条件

| 条件 | publish 行为 | direct 行为 |
|------|-------------|------------|
| 目标 queue 不存在（agent FINISHED）| 跳过该订阅者 | raise KeyError |
| 发布者无订阅者 + TextEvent + on_no_subscriber 非空 | 调用 on_no_subscriber | N/A |
| 发布者无订阅者 + TextEvent + on_no_subscriber 为 None | 尝试 self._console.send，都不可用则静默丢弃 | N/A |
| 发布者无订阅者 + StopEvent | 静默丢弃（不管 on_no_subscriber） | N/A |
| 订阅者自己订阅自己 | subscribe 时 raise ValueError | N/A |
| 重复 subscribe 同一对 | 幂等（set 自动去重） | N/A |
| target_pid 不在 input_queues | N/A | raise KeyError |
| from_pid 从未被 subscribe 过 | _subscriptions.get 返回 {}, 走降级 | N/A |

---

### 4.2 `KernelBridgeAdapter.send()` 升级

从 Batch 0-2 的内联降级路由切换到真实 MessageBus。

**修改前**（Batch 0-2 内联降级路由）：

```python
# target=None, TextEvent → console.send(AgentOutput(...))
# target=None, StopEvent → 静默丢弃
# target=pid → input_queues[target].put_nowait(InternalMessage(...))
```

**修改后**（Batch 3 MessageBus 路由）：

```python
# harness/runtime/bridge_adapter.py — send() 方法中

async def send(self, event, target=None):
    if self._runtime.should_exit:
        return  # 退出保护：丢弃"最后一轮污染"

    # ── 事件类型分流 ──
    # 仅 TextEvent/StopEvent 参与 MessageBus 路由；
    # 中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole
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
        # 定向投递：走 MessageBus.direct()
        self._kernel.message_bus.direct(
            target,
            InternalMessage(
                from_pid=self._pid,
                content=event.content if isinstance(event, TextEvent) else "",
                metadata={"stop": True} if isinstance(event, StopEvent) else {},
            )
        )
    else:
        # pub-sub 路由：走 MessageBus.publish()
        await self._kernel.message_bus.publish(
            from_pid=self._pid,
            event=event,
            on_no_subscriber=(
                self._kernel._console.send
                if isinstance(event, TextEvent) else None
            )
        )
```

**兼容性说明**：`InternalMessage` 和 `AgentOutput` 的 import 路径不变（`from .types import ...`）。`ThinkingEvent` / `ToolCallEvent` / `ToolResultEvent` 的 import 路径不变（`from ..interfaces.types import ...`）。send() 的对外签名不变——只改内部路由实现。

---

### 4.3 `Kernel._on_agent_finished()` 完整实现

从 Batch 1-2 的 stub（仅推送 `AgentFinished` 到 SystemConsole）升级为完整实现。

```
async def _on_agent_finished(self, runtime):
    """agent FINISHED 时的回调（由 Task.done_callback 触发）。

    执行顺序：
    1. 推送 AgentFinished 到 SystemConsole
    2. 默认订阅：通知父 agent（child_finished）
    3. 级联终止：通过 MessageBus 查询订阅者并推送 __EXIT_SENTINEL__
    4. 清理订阅表：remove_publisher
    """
    from .types import AgentFinished

    duration = time.time() - runtime.started_at

    # ── 1. 推送 SystemConsole ──
    await self._console.send(AgentFinished(
        pid=runtime.pid,
        result=runtime.last_output,
        duration=duration,
        error=runtime.error,
    ))

    # ── 2. 默认订阅：通知父 agent ──
    # 如果父 agent 也显式 subscribe 了本 agent，跳过默认订阅推送，
    # 避免父 agent 收到两份通知（一份 child_finished + 一份 subscribe 流式输出）
    if runtime.parent and runtime.parent.state != AgentState.FINISHED:
        parent_subscribed = (
            self.message_bus is not None
            and runtime.parent.pid in self.message_bus.get_subscribers_of(runtime.pid)
        )
        if not parent_subscribed:
            self.send_input(runtime.parent.pid, UserRequest(
                text=f"[{runtime.pid}] {'异常退出' if runtime.error else '已完成'}。\n{runtime.last_output}",
                metadata={
                    "type": "child_finished",
                    "pid": runtime.pid,
                    "workflow_flag": runtime.workflow_flag,
                    "duration": duration,
                    "error": runtime.error,
                }
            ))

    # ── 3. 级联终止：通知显式订阅者 ──
    # 通过 MessageBus 查询谁订阅了本 agent，向这些 agent 推送 __EXIT_SENTINEL__。
    # 关键：父 agent 不受级联影响（顶层设计 Section 四.5 明确约定）。
    # 父 agent 通过默认订阅收到 child_finished（步骤 2）正常继续运行。
    if self.message_bus is not None:
        parent_pid = runtime.parent.pid if runtime.parent else None
        subscribers = self.message_bus.get_subscribers_of(runtime.pid)
        for sub_pid in subscribers:
            # 跳过父 agent —— 父不受级联影响
            if sub_pid == parent_pid:
                continue
            sub_runtime = self.runtime_table.get(sub_pid)
            if sub_runtime and sub_runtime.state not in (
                AgentState.FINISHED, AgentState.TERMINATING
            ):
                sub_runtime.should_exit = True
                self.input_queues[sub_pid].put_nowait(__EXIT_SENTINEL__)

        # ── 4. 清理订阅表 ──
        self.message_bus.remove_publisher(runtime.pid)
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 默认订阅与显式订阅的去重 | 检查父 agent 是否也在 `get_subscribers_of(child)` 中；若是则跳过默认订阅推送 | 避免父 agent 收到两份通知（`child_finished` + subscribe 流式输出）。推送两份等效通知会让 LLM 困惑 |
| 级联终止的推送方式 | Kernel 直接 `put_nowait(__EXIT_SENTINEL__)`，不经过 MessageBus.publish() | sentinel 是控制信号不是业务消息；MessageBus 只负责查询订阅关系，不推送 sentinel |
| 父 agent 与级联终止的关系 | 父 agent 不受级联影响——级联循环中显式 `if sub_pid == parent_pid: continue` | 顶层设计 Section 四.5 明确："父 agent 通过默认订阅收到 child_finished 消息而非 sentinel，因此不受级联影响"。即使父 agent 显式 subscribe 了子 agent，子 FINISHED 时父也不应被级联终止——父 agent 有自己的生命周期，子 agent 完成不应强制父退出 |
| 清理订阅表 | 级联终止推送 sentinel 后立即 `remove_publisher` | 防止死 agent 积累在订阅表中；被级联终止的 subscribe agent 收到 sentinel 后会自行 TERMINATING → FINISHED，不会再有输出 |
| `message_bus` 为 None 的防御 | `if self.message_bus is not None` | Kernel 在 Batch 1-2 测试中可能以 message_bus=None 构造。Batch 3 后 message_bus 总是非空 |
| `runtime.workflow_flag` | AgentRuntime 尚未有此属性 | **需要在 AgentRuntime 构造时记录**。在 `spawn_from_script()` 和 `spawn_root()` 中，创建 AgentRuntime 后设置 `runtime.workflow_flag = workflow_flag`；spawn_root 使用 `"wf_root"` |

#### 4.3.1 AgentRuntime 新增 `workflow_flag` 属性

`workflow_flag` 由 Kernel 在 spawn 时设置，不在 `AgentRuntime.__init__` 中赋值（AgentRuntime 不关心自身属于哪个 workflow——那是 Kernel 层面的概念）。属性声明在 `__init__` 中初始化为 `None`，Kernel 随后设置实际值。

```python
# harness/runtime/agent_runtime.py — __init__ 中新增:

self.workflow_flag: str | None = None  # 由 Kernel.spawn_root / spawn_from_script 设置
```

在 `Kernel.spawn_root()` 中：
```python
runtime.workflow_flag = "wf_root"
```

在 `Kernel.spawn_from_script()` 中：
```python
runtime.workflow_flag = workflow_flag
```

---

### 4.4 `Kernel._monitor_quiescence()` 完整实现

从 Batch 1-2 的 stub（仅等 `all_finished()`）升级为完整实现：

```python
async def _monitor_quiescence(self) -> None:
    """静默检测监控协程。

    每秒检查一次：如果所有非 FINISHED agent 都处于 idle 状态
    （在 RUNNING 状态且在 adapter.receive() 中等待），
    则向全体推送 __EXIT_SENTINEL__ 触发优雅退出。

    Mode B 的核心结束机制：当所有 agent 完成工作进入 WAITING_INPUT，
    无人会再产生输出时，静默检测自动终止所有 agent。
    """
    while not self._shutdown:
        await asyncio.sleep(1)

        non_finished = [
            r for r in self.runtime_table.values()
            if r.state != AgentState.FINISHED
        ]

        if not non_finished:
            return  # 全部 FINISHED → 监控结束

        # 所有非 FINISHED agent 都在等待输入？
        all_idle = all(
            r._idle_for_quiescence() for r in non_finished
        )
        if all_idle:
            for r in non_finished:
                r.should_exit = True
                if r.pid in self.input_queues:
                    self.input_queues[r.pid].put_nowait(__EXIT_SENTINEL__)
            return  # 静默检测触发 → 监控结束
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 轮询间隔 | 1 秒 | 合理默认值——远小于用户感知延迟，远大于 CPU 浪费阈值。后续可通过 `Kernel(quiescence_interval=N)` 参数化 |
| idle 判断依据 | `AgentRuntime._idle_for_quiescence()` | Batch 1 已实现：`state == RUNNING and _idle_since is not None`。agent 在 `adapter.receive()` 调用前设置 `_idle_since = time.time()`，返回后设为 `None` |
| oneshot agent 在 idle 检测中的行为 | oneshot agent 一轮完成后 `should_exit = True` → 外层 while 退出 → TERMINATING → FINISHED。不会被静默检测捕获 | oneshot agent 不会进入"等待输入"的 idle 状态——它们自动退出。静默检测主要覆盖 continuous agent |
| 级联终止与静默检测的交互 | collector FINISHED → 级联推送 sentinel → analyzer 被唤醒 → TERMINATING → FINISHED → `all_finished()` True → `_monitor_quiescence` 返回 | 两者互补。级联终止精准唤醒有订阅关系的 agent；静默检测作为兜底覆盖没有订阅关系但都在 idle 的情况 |
| 重复 sentinel | agent 收到第一次 sentinel 后 `should_exit=True`，外层 while break → TERMINATING。第二次 sentinel 仍在队列中，但 agent 已不会调用 receive() | 幂等——sentinel 只是放在队列里，不会造成错误 |

---

### 4.5 `Kernel.spawn_from_script()` 升级

在 MessageBus 创建后，将 Batch 2 暂存的 `_pending_subscriptions` 注册到 MessageBus。同时清空 `_pending_subscriptions` 避免重复注册。

```python
# Kernel.spawn_from_script() 中，步骤 4（暂存订阅关系）替换为：

# ── 步骤 4: 注册订阅关系到 MessageBus ──
if self.message_bus is not None:
    for sub in decorators._subscription_registry:
        self.message_bus.subscribe(sub.subscriber, sub.publisher)
```

同时，在 `__init__` 中清理历史遗留的 `_pending_subscriptions`：

```python
# Kernel.__init__() 中:
self.message_bus = MessageBus(
    input_queues=self.input_queues,
    console=self._console,
)
# _pending_subscriptions 不再需要——Batch 2 的暂存数据如果有残留，
# 在首次 spawn_from_script 时统一清理
if self._pending_subscriptions:
    import logging
    logging.getLogger(__name__).warning(
        f"Clearing {len(self._pending_subscriptions)} stale pending "
        f"subscriptions from Batch 2 — these will NOT be registered."
    )
self._pending_subscriptions = []  # 清空 Batch 2 遗留
```

**向后兼容**：如果有 Batch 2 期间遗留的 `_pending_subscriptions` 条目（极其罕见——需要跨 Kernel 实例持久化），它们在被清空前不会注册到 MessageBus。实践中每个 Runtime 实例创建一个 Kernel，不会有遗留。

---

### 4.6 `Kernel.__init__()` 调整

```python
def __init__(self, console: 'SystemConsole'):
    # ... 现有初始化代码 ...

    # Batch 3: 创建 MessageBus（替代 Batch 1-2 的 self.message_bus = None）
    from .message_bus import MessageBus
    self.message_bus = MessageBus(
        input_queues=self.input_queues,
        console=console,
    )

    # _pending_subscriptions 清空（如有 Batch 2 遗留数据）
    self._pending_subscriptions = []
```

**重要**：`input_queues` 在 `__init__` 时为空 dict，后续 `spawn_root()` / `spawn_from_script()` 中增量添加。`MessageBus` 持有的是 `input_queues` 的引用——后续添加的 agent queue 自动对 MessageBus 可见。

### 4.7 `WorkflowFinished` SystemEvent

Mode B `run_from_script()` 返回前，汇总所有 agent 最终结果：

```python
# harness/runtime/types.py（追加）

@dataclass
class WorkflowFinished:
    """Mode B: 所有 agent FINISHED，workflow 执行完成。

    run_from_script 在返回前推送此事件到 SystemConsole。
    """
    workflow_flag: str
    agents: list[dict]
    # agents 中每项:
    #   {"pid": str, "output": str, "error": str|None, "rounds": int, "duration": float}
```

`SystemEvent` union type 更新：

```python
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
    | WorkflowFinished  # Batch 3 新增
)
```

### 4.8 `Runtime.run_from_script()` Mode B 入口

```python
# harness/runtime/runtime.py

def run_from_script(self, script_path: str) -> None:
    """Mode B 入口 — 直接启动 workflow 脚本。

    不创建 root agent。从脚本加载 agent 并启动，等待所有 agent
    FINISHED（通过静默检测或自然结束），汇总结果后返回。

    用法:
        console = CliConsole()
        Runtime(console).run_from_script("workflow.py")
    """
    try:
        asyncio.run(self._run_from_script_async(script_path))
    except KeyboardInterrupt:
        pass

async def _run_from_script_async(self, script_path: str) -> None:
    """Mode B 异步主流程。"""
    from .kernel import Kernel
    from .signals import create_sigint_handler
    from .types import RuntimeStarted, RuntimeStopped, WorkflowFinished

    # 1. 创建 Kernel
    self._kernel = Kernel(self._console)

    # 2. 从脚本 spawn agent（无 parent）
    result = self._kernel.spawn_from_script(script_path, parent=None)
    # result = {"workflow_flag": "wf_001", "agents": [...]}

    # 3. 启动系统输入处理（Mode B 下用户仍可 /talk, /agents 等 — Batch 4 完整化）
    task_sys = asyncio.create_task(
        self._kernel._handle_system_input()
    )

    # 4. 启动静默检测
    task_mon = asyncio.create_task(
        self._kernel._monitor_quiescence()
    )

    # 5. 注册信号处理
    handler = create_sigint_handler(self)
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, handler)
    except NotImplementedError:
        pass

    # 6. 推送启动事件
    await self._console.send(RuntimeStarted())

    # 7. 等待全部完成
    try:
        await asyncio.gather(
            *self._kernel._tasks.values(),
            task_sys,
            task_mon,
            return_exceptions=True,
        )
    finally:
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, RuntimeError):
            pass

    # 8. 收集最终输出
    results = {}
    for pid, r in self._kernel.runtime_table.items():
        results[pid] = {
            "pid": pid,
            "output": r.last_output,
            "error": r.error,
            "rounds": r.round_count,
            "duration": time.time() - r.started_at if r.started_at else 0.0,
        }

    # 9. 推送 WorkflowFinished
    await self._console.send(WorkflowFinished(
        workflow_flag=result["workflow_flag"],
        agents=list(results.values()),
    ))

    # 10. 推送停止事件
    await self._console.send(RuntimeStopped())
```

**CliConsole WorkflowFinished 格式化**：

```python
# harness/runtime/cli_console.py — send() 中新增:

elif isinstance(event, WorkflowFinished):
    print(f"[系统] Workflow {event.workflow_flag} 完成:")
    for agent in event.agents:
        status = "异常" if agent.get("error") else "正常"
        print(f"  {agent['pid']:12} {status}  "
              f"{agent['rounds']}轮  {agent['duration']:.1f}s")
        if agent.get("output"):
            print(f"    → {agent['output'][:200]}")
```

**与 `_run_async()` (Mode A) 的区别**：

| 方面 | Mode A (`run()`) | Mode B (`run_from_script()`) |
|------|-----------------|---------------------------|
| root agent | 创建 `pid="root"` continuous agent | 不创建 |
| agent 来源 | `spawn_root(harness)` | `spawn_from_script(script_path, parent=None)` |
| 结束机制 | 用户 `/exit` 或 SIGINT 或 root FINISHED | 静默检测（所有 agent idle）或 SIGINT |
| 汇总输出 | 无（交互式，root 自己汇报） | `WorkflowFinished` event |
| stdin 路由 | 纯文本 → root agent | `/talk <pid>` 定向（Batch 4 完整化） |

#### 4.8.1 Mode B 已知限制（Batch 4 解决）

**`_handle_system_input` 阻塞问题**：Mode B 下所有 agent FINISHED 后，`_monitor_quiescence` 和 agent tasks 全部完成，但 `_handle_system_input` 仍在 `console.receive()`（stdin readline）处阻塞。`asyncio.gather` 因此不会返回，`run_from_script` 挂起。Batch 4 通过 `/exit` 命令处理（设置 `_shutdown=True` 让 `_handle_system_input` 退出）完整解决。Batch 3 期间务实方案：用户按 Enter 或 Ctrl+C 结束。

#### 4.8.2 双向 subscribe 保护

`subscribe("A").to("B")` + `subscribe("B").to("A")` 可能导致无限的对轮循环。不主动检测或禁止——这在某些 workflow 模式中是合法的（如辩论、迭代优化）。`max_rounds` 硬上限（默认 1000）作为唯一安全网。若需限制，由 workflow 脚本作者通过 oneshot agent 或 `finish_agent` tool 控制。

---

## 五、数据流

### 5.1 流式订阅（subscribe → TextEvent 路由）

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

### 5.2 默认订阅（child_finished）— 不经过 MessageBus

```
collector FINISHED
  │
  ▼
Kernel._on_agent_finished(collector_runtime)
  ├─ 1. console.send(AgentFinished("collector", ...))   → SystemConsole
  │
  ├─ 2. parent (root) 是否显式 subscribe 了 collector?
  │      → MessageBus.get_subscribers_of("collector") → []
  │      → parent 不在其中 → 推送 child_finished
  │
  ├─ 3. send_input("root", UserRequest(
  │      text="[collector] 已完成。\n采集到28个文件",
  │      metadata={type: "child_finished", pid: "collector", ...}
  │    ))
  │    → input_queues["root"].put_nowait(UserRequest)
  │    └─ 不经过 MessageBus.publish() —— 直接入队
  │
  ├─ 4. 级联：MessageBus.get_subscribers_of("collector") → ["analyzer"]
  │    → analyzer 不是 FINISHED/TERMINATING
  │    → analyzer.should_exit = True
  │    → input_queues["analyzer"].put_nowait(__EXIT_SENTINEL__)
  │
  └─ 5. MessageBus.remove_publisher("collector")

  ▼
root.adapter.receive()
  → UserRequest(text="[collector] 已完成。...",
                metadata={type: "child_finished", pid: "collector", ...})
```

**关键区别**：
- **默认订阅（child_finished）**：走 `Kernel.send_input(parent)` → 直接入队父的 input_queue。不经过 MessageBus。
- **显式订阅（subscribe 声明）**：走 `MessageBus.publish()` → 查订阅表路由。
- **级联终止**：走 `MessageBus.get_subscribers_of()` 查询订阅关系，但 sentinel 推送由 Kernel 直接执行。

### 5.3 完整多 agent 协作推演（Mode B）

```
Runtime(console).run_from_script("workflow.py")
  │
  ├─ 1. Kernel(console) — 创建 MessageBus
  ├─ 2. spawn_from_script("workflow.py", parent=None)
  │     → 加载脚本，创建 collector (oneshot) + analyzer (continuous)
  │     → subscribe("analyzer").to("collector") 注册到 MessageBus
  │     → Task 启动 + entry_prompt 投递
  │
  ├─ 3. collector.run():
  │     → entry_prompt → call_llm → TextEvent("采集到28个文件...")
  │     → KBA.send(TextEvent, target=None)
  │     → MessageBus.publish(from="collector", event=TextEvent)
  │     → 查表: analyzer 订阅了 collector
  │     → input_queues["analyzer"].put_nowait(InternalMessage(...))
  │     → 内层 StopEvent → oneshot → should_exit=True
  │     → TERMINATING → FINISHED
  │
  ├─ 4. collector FINISHED → _on_agent_finished(collector):
  │     → console.send(AgentFinished("collector", ...))
  │     → parent=None → 跳过默认订阅
  │     → get_subscribers_of("collector") → ["analyzer"]
  │     → analyzer.should_exit = True
  │     → input_queues["analyzer"].put_nowait(__EXIT_SENTINEL__)  [级联终止]
  │     → remove_publisher("collector")
  │
  ├─ 5. analyzer:
  │     → 收到 collector 的 TextEvent → call_llm → "分析完成，发现3个问题..."
  │     → TextEvent/StopEvent → MessageBus.publish → 无订阅者 → 降级到 AgentOutput
  │     → 下一轮 adapter.receive() → 收到 __EXIT_SENTINEL__
  │     → should_exit=True → TERMINATING → FINISHED
  │
  ├─ 6. analyzer FINISHED → _on_agent_finished(analyzer):
  │     → console.send(AgentFinished("analyzer", ...))
  │     → parent=None → 跳过默认订阅
  │     → get_subscribers_of("analyzer") → [] → 无级联
  │     → remove_publisher("analyzer")
  │
  ├─ 7. _monitor_quiescence:
  │     → non_finished = [] → return
  │
  ├─ 8. 收集 results:
  │     collector: {output: "采集到28个文件...", rounds: 1, error: None}
  │     analyzer:  {output: "分析完成...", rounds: 2, error: None}
  │
  └─ 9. console.send(WorkflowFinished(agents=[...]))
       console.send(RuntimeStopped())
```

### 5.4 去重：父 agent 同时有默认订阅和显式订阅

```
场景：root spawn 了 analyzer，且 workflow 脚本中声明了 subscribe("root").to("analyzer")

analyzer FINISHED → _on_agent_finished(analyzer):
  ├─ parent = root
  ├─ get_subscribers_of("analyzer") → ["root"] (因为脚本中 subscribe("root").to("analyzer"))
  ├─ parent.pid ("root") 在 subscribers 中 → 跳过默认订阅推送（去重）
  ├─ 级联终止：sub_pid "root" == parent_pid → continue（父不受级联影响）
  └─ root 通过 subscribe 流式路由收到了 analyzer 的最后一轮 TextEvent。
     root 既不会收到重复的 child_finished，也不会被级联终止强制退出。

去重逻辑：
  parent_subscribed = parent.pid in message_bus.get_subscribers_of(child.pid)
  if not parent_subscribed:
      send_input(parent, child_finished UserRequest)
  # 级联循环中：
  if sub_pid == parent_pid:
      continue  # 父不受级联影响

父 agent 的安全保证（两层防护）：
  1. 级联循环跳过父 agent → 父不会被强制退出
  2. child_finished 去重 → 父不会收到重复通知（subscribe 流已经给了父子 agent 的输出）
```

如果 analyzer 正常完成（产生了 TextEvent），root 通过 subscribe 已经收到了 analyzer 的输出——不需要再收到 `child_finished` 重复通知。如果 analyzer 异常退出（未产生最终 TextEvent），root 通过 subscribe 流会收到 `InternalMessage(metadata={stop: True})` 表明子 agent 已停止——从中推断子 agent 的状态。两重防护确保父 agent 既不会被意外终止，也不会收到冗余信息。

---

## 六、错误处理

### 6.1 异常分类与策略

| 异常来源 | 处理位置 | 策略 |
|---------|---------|------|
| `MessageBus.publish()` 中 `input_queues[sub_pid]` KeyError | publish() | 已过滤：只投递给 `input_queues` 中存在的 pid。agent FINISHED 后 queue 可能被清理——跳过该订阅者 |
| `MessageBus.direct()` 中 target_pid 不存在 | direct() | raise KeyError → 调用方（KBA.send）不 catch → 传播到 AgentRuntime.run() → error 记录 |
| `MessageBus.subscribe()` 自订阅 | subscribe() | raise ValueError → spawn_from_script 中加载脚本时捕获 → ToolResult(success=False, error=...) |
| `_on_agent_finished` 中 `send_input` 到已 FINISHED 的父 agent | `Kernel.send_input()` | Batch 1 已有防御：`pid in input_queues` 检查 → WARNING 日志 + 跳过 |
| 级联终止推 sentinel 到已 FINISHED agent | `_on_agent_finished` | state 检查：`not in (FINISHED, TERMINATING)` → 跳过 |
| `_monitor_quiescence` 中 agent 在检查后、推送 sentinel 前 FINISHED | 推送循环 | `r.pid in self.input_queues` 防御。queue 可能已被清理 → 跳过 |
| `run_from_script` 中脚本加载失败 | `spawn_from_script` | 异常传播到 `asyncio.run()` → `KeyboardInterrupt` 除外，其他异常让程序以非零退出码退出 |
| `WorkflowFinished` 中 `r.started_at` 为 0 | `_run_from_script_async` | `if r.started_at else 0.0` 防御；agent 在 CREATED 状态就失败时 started_at 可能为 0 |

### 6.2 重复 sentinel 幂等性

| 场景 | 行为 |
|------|------|
| agent 同时被级联终止和静默检测推送 sentinel | 第一次收到 sentinel → `should_exit=True` → 外层 while break。第二次 sentinel 仍在队列中——agent 已不会调用 receive()。幂等 |
| agent 在 call_llm 期间收到 sentinel（入队但未被 receive） | LLM 返回后 while 条件检查 `should_exit` → True → break。sentinel 留在队列中。幂等 |
| `_on_agent_finished` 中对已 TERMINATING agent 推送 sentinel | `state not in (FINISHED, TERMINATING)` 检查 → 跳过 |

### 6.3 MessageBus 防御性编程

```python
# publish() 中
active_subscribers = {
    pid for pid in subscribers
    if pid in self._input_queues  # queue 可能已被清理
}

# direct() 中
if target_pid not in self._input_queues:
    raise KeyError(...)  # 调用方应确保 target 存在
```

---

## 七、测试策略

### 7.1 测试框架与模式

与现有测试保持一致：pytest + 手动 mock（不使用 mock 库）。

### 7.2 单元测试场景

#### MessageBus

| 测试 | 验证点 |
|------|--------|
| 构造：空订阅表 | `_subscriptions` 为空 dict |
| `subscribe("A", "B")` 建立订阅 | `_subscriptions["B"]` 含 `"A"` |
| `subscribe` 幂等 | 两次 `subscribe("A", "B")` → `_subscriptions["B"]` 仍为 `{"A"}`（set 去重） |
| `subscribe("A", "A")` → ValueError | 自订阅被拒绝 |
| `publish()` 路由到活跃订阅者 | input_queues["A"] 收到 InternalMessage |
| `publish()` 跳过已 FINISHED 的订阅者 | queue 被 pop 的 pid 不在 active_subscribers 中 |
| `publish()` 无订阅者 + TextEvent + on_no_subscriber | on_no_subscriber 被调用 |
| `publish()` 无订阅者 + TextEvent + 无 callback | 不崩溃，静默丢弃 |
| `publish()` 无订阅者 + StopEvent | on_no_subscriber 不被调用（即使有值） |
| `publish()` 内部降级优先级 | on_no_subscriber > self._console |
| `direct(target, msg)` 正确投递 | input_queues[target] 收到 InternalMessage |
| `direct()` target 不存在 → KeyError | raise KeyError |
| `get_subscribers_of("B")` 返回列表 | 返回 `["A"]`（或空列表） |
| `unsubscribe("A", "B")` 移除订阅 | `_subscriptions["B"]` 不含 `"A"` |
| `unsubscribe` 对不存在的订阅幂等 | 不崩溃 |
| `remove_publisher("B")` 清理全部 | `_subscriptions` 不含 `"B"` |
| `remove_publisher` 对不存在的 publisher 幂等 | 不崩溃 |

#### KernelBridgeAdapter.send() 升级

| 测试 | 验证点 |
|------|--------|
| TextEvent, target=None → MessageBus.publish 被调用 | 参数正确传递 |
| StopEvent, target=None → MessageBus.publish 被调用 | 参数正确，on_no_subscriber=None |
| target=pid → MessageBus.direct 被调用 | InternalMessage 正确构造 |
| should_exit=True → 所有消息被丢弃 | publish/direct 不被调用 |
| ThinkingEvent → 降级到 console | MessageBus 不被调用 |
| ToolCallEvent → 降级到 console | MessageBus 不被调用 |
| ToolResultEvent → 降级到 console | MessageBus 不被调用 |

#### Kernel._on_agent_finished() 完整实现

| 测试 | 验证点 |
|------|--------|
| agent FINISHED → 推送 AgentFinished 到 console | console.send 收到 AgentFinished |
| 子 agent FINISHED → 父收到 child_finished | send_input 被调用，UserRequest metadata 含 type="child_finished" |
| 子 agent FINISHED + 父已显式 subscribe → 跳过 child_finished | send_input 不被调用（去重） |
| 子 agent FINISHED + 父已 FINISHED → 跳过 | send_input 不被调用 |
| 子 agent FINISHED + 有显式订阅者 → 级联终止 | 订阅者收到 __EXIT_SENTINEL__ |
| 子 agent FINISHED + 父在订阅者中 → 父被级联跳过 | 父 subscription 正常收到 TextEvent，不收到 sentinel |
| 子 agent FINISHED + 订阅者已在 TERMINATING → 跳过 | sentinel 不被推送 |
| 子 agent FINISHED + 订阅者已 FINISHED → 跳过 | sentinel 不被推送 |
| 子 agent FINISHED → remove_publisher 被调用 | MessageBus._subscriptions 不含该 pid |
| 无 message_bus（防御）→ 不崩溃 | 测试 message_bus=None 时 _on_agent_finished 正常运行 |
| `_pending_subscriptions` 非空时清空 → WARNING 日志 | 日志正确输出 |

#### Kernel._monitor_quiescence() 完整实现

| 测试 | 验证点 |
|------|--------|
| 所有 agent idle → 推送 sentinel | 所有非 FINISHED agent 收到 __EXIT_SENTINEL__ |
| 无 agent idle → 不推送 | sentinel 不被推送 |
| 全部 FINISHED → 返回 | 协程正常返回 |
| 有 agent 在 call_llm 中 → 不推送 | 该 agent _idle_since 为 None，all_idle=False |
| oneshot agent 在 TERMINATING → 不算 idle | _idle_for_quiescence 为 False（state != RUNNING） |

#### Runtime.run_from_script()

| 测试 | 验证点 |
|------|--------|
| 加载最小 workflow 脚本 → 全部 FINISHED | run_from_script 正常返回 |
| 返回前推送 WorkflowFinished | console.send 收到 WorkflowFinished event |
| WorkflowFinished 含正确 agents 数据 | output / error / rounds / duration 正确 |
| 脚本无 @agent → 异常传播 | run_from_script 抛出异常 |
| 脚本加载失败 → 异常传播 | run_from_script 抛出异常 |

### 7.3 集成测试场景

| 测试 | 验证点 |
|------|--------|
| Mode B: collector + analyzer 完整协作 | collector 产出 → analyzer 收到 subscribe 消息 → 全部 FINISHED → WorkflowFinished |
| Mode B: 多 agent 并发运行 | 所有 agent Task 被创建并启动 |
| Mode A: root spawn workflow → child_finished 通知 | root 通过 adapter.receive() 收到 child_finished UserRequest |
| 级联终止：publisher FINISHED → subscriber 退出 | subscriber 收到 __EXIT_SENTINEL__ → FINISHED |
| 静默检测：所有 agent idle → 自动结束 | _monitor_quiescence 推送 sentinel → 所有 agent FINISHED |
| subscribe 消息路由端到端 | collector KBA.send(TextEvent) → MessageBus → analyzer receive 收到 |
| 父 agent subscribe 子 agent → 去重生效 + 父免于级联 | root 通过 subscribe 收到子输出，不收到 child_finished 重复通知，也不收到 cascading sentinel |
| subscribe 引用未知 agent → spawn_from_script 报错 | ValueError 含未知 agent name |

### 7.4 需要的 Mock/Stub

| Mock 对象 | 最小接口 | 用途 |
|----------|---------|------|
| `MockConsole` | `async send(event)` + spy | MessageBus / _on_agent_finished / run_from_script 测试 |
| `MockAgentRuntime` | `pid`, `state`, `_idle_for_quiescence()`, `should_exit` | _monitor_quiescence 测试 |
| `MockMessageBus` | subscribe / publish / direct / get_subscribers_of / remove_publisher | KBA 测试 |
| `MockAsyncLLM` | `async __call__(msgs, tools) → Response` | AgentRuntime run() 测试 |
| `MockHarness` | `container` 属性 + `call_llm` | spawn_from_script 测试 |

### 7.5 现有测试回归

Batch 3 修改了以下文件的内部实现（不含 `__init__.py` re-export）：

- `kernel.py`: `__init__`（创建 MessageBus）、`_on_agent_finished`（stub→完整）、`_monitor_quiescence`（stub→完整）、`spawn_from_script`（注册订阅到 MessageBus）
- `bridge_adapter.py`: `send()`（内联降级→MessageBus）
- `runtime.py`: 新增 `run_from_script`
- `agent_runtime.py`: 新增 `workflow_flag` 属性

需要验证：
- `pytest` 全部现有测试继续通过
- Batch 2 的 E2E 测试（最小多 agent workflow）不受影响——`spawn_from_script` 行为变化仅为额外调用 `MessageBus.subscribe`，不影响已有测试的逻辑
- 无 import 错误

---

## 八、验收标准

### AC-3.1 MessageBus
- [ ] `MessageBus` 类定义在 `harness/runtime/message_bus.py`
- [ ] 构造函数接受 `input_queues: dict` 和可选的 `console`
- [ ] `subscribe(subscriber, publisher)` 建立订阅关系，幂等，拒绝自订阅
- [ ] `publish(from_pid, event, on_no_subscriber)` 异步方法，正确路由到活跃订阅者
- [ ] `publish()` 降级逻辑：TextEvent 无订阅者 → on_no_subscriber > console > 静默丢弃
- [ ] `publish()` StopEvent 无订阅者 → 静默丢弃（不调用任何 callback）
- [ ] `direct(target_pid, message)` 跳过订阅表直接投递
- [ ] `get_subscribers_of(publisher)` 返回订阅者列表
- [ ] `unsubscribe(subscriber, publisher)` 幂等
- [ ] `remove_publisher(publisher)` 清理该 publisher 所有订阅

### AC-3.2 KernelBridgeAdapter 升级
- [ ] `send()` 中 `target=None` 走 `MessageBus.publish()`
- [ ] `send()` 中 `target=pid` 走 `MessageBus.direct()`
- [ ] 退出保护（`should_exit` 检查）保持不变
- [ ] 中间事件（Thinking/ToolCall/ToolResult）仍降级到 SystemConsole

### AC-3.3 Kernel._on_agent_finished() 完整实现
- [ ] 推送 `AgentFinished` 到 SystemConsole（保持原 stub 行为）
- [ ] 默认订阅：父 agent 收到 `child_finished` UserRequest（含 type/pid/workflow_flag/duration/error）
- [ ] 去重：父 agent 如已显式 subscribe 子 agent，跳过 child_finished 推送
- [ ] 父 agent 已 FINISHED → 跳过 child_finished
- [ ] 级联终止：通过 `MessageBus.get_subscribers_of()` 查询订阅者 → 推送 `__EXIT_SENTINEL__`
- [ ] 级联终止跳过已 FINISHED/TERMINATING 的订阅者
- [ ] 清理：`MessageBus.remove_publisher(pid)`

### AC-3.4 Kernel._monitor_quiescence() 完整实现
- [ ] 每秒检查所有非 FINISHED agent 是否 idle
- [ ] 全部 idle → 推送 `__EXIT_SENTINEL__` 给所有非 FINISHED agent
- [ ] 全部 FINISHED → 返回

### AC-3.5 Kernel.spawn_from_script() 升级
- [ ] spawn_from_script 中 `subscribe` 声明注册到 MessageBus（不再暂存到 `_pending_subscriptions`）
- [ ] Kernel.__init__ 中创建 MessageBus 实例（替代 `message_bus = None`）

### AC-3.6 AgentRuntime.workflow_flag
- [ ] `AgentRuntime` 新增 `workflow_flag: str | None` 属性
- [ ] `spawn_root()` 设置 `workflow_flag = "wf_root"`
- [ ] `spawn_from_script()` 设置 `workflow_flag` 为生成的 flag

### AC-3.7 WorkflowFinished + Runtime.run_from_script()
- [ ] `WorkflowFinished` dataclass 定义在 `harness/runtime/types.py`
- [ ] `SystemEvent` union type 包含 `WorkflowFinished`
- [ ] `Runtime.run_from_script(script_path)` Mode B 入口可用
- [ ] 收集所有 agent 最终结果并推送 `WorkflowFinished`
- [ ] 推送 `RuntimeStopped` 事件

### AC-3.8 CliConsole 更新
- [ ] `send(WorkflowFinished)` 格式化输出每个 agent 结果

### AC-3.9 包结构
- [ ] `harness/runtime/message_bus.py` 创建
- [ ] `harness/runtime/__init__.py` 新增 `MessageBus` re-export

### AC-3.10 现有测试不退化
- [ ] `pytest` 全部现有测试通过
- [ ] 无 import 错误
- [ ] Batch 2 E2E 测试不受影响

### AC-3.11 新测试
- [ ] MessageBus 测试 ≥ 15 条
- [ ] KBA.send() 升级测试 ≥ 8 条
- [ ] Kernel._on_agent_finished 测试 ≥ 8 条
- [ ] Kernel._monitor_quiescence 测试 ≥ 5 条
- [ ] Runtime.run_from_script 测试 ≥ 5 条
- [ ] 集成测试 ≥ 6 条

---

## 九、与后续 Batch 的接口约定

### 9.1 Batch 3 → Batch 4

```
暴露:
  from harness.runtime.message_bus import MessageBus
  from harness.runtime.runtime import Runtime  # 新增 run_from_script
  from harness.runtime.types import WorkflowFinished

Batch 4 依赖:
  - MessageBus 已完整可用，Batch 4 无需修改
  - Kernel._on_agent_finished 已完整（child_finished + 级联），Batch 4 无需修改
  - Kernel._monitor_quiescence 已完整，Batch 4 无需修改
  - Kernel._handle_system_input 仍为 stub — 这是 Batch 4 的核心任务
  - CliConsole.receive() 仍为纯文本路由 — Batch 4 完善命令解析
```

### 9.2 职责边界（本 Batch 不做的内容）

以下内容**不属于 Batch 3**，在后续 batch 中实现：

- ❌ `/agents` `/kill` `/end` `/exit` `/talk` 命令解析（Batch 4）
- ❌ `Kernel._handle_system_input` 完整实现（Batch 4）
- ❌ `CliConsole.receive()` 命令解析规则（Batch 4）
- ❌ `SystemEvent` 查询响应类型（`AgentsListed`, `CommandError`）（Batch 4）
- ❌ SIGINT 信号处理改进（Batch 4）
- ❌ 异常信息标准化（`AgentRuntime.error` → `child_finished.metadata.error` 对齐）（Batch 5）
- ❌ ContextAssembler system prompt 注入 Runtime tool 说明（Batch 5）
- ❌ 子 agent 中间输出可观测（read_log tool）（Batch 5）
- ❌ 文档更新 + 示例迁移（Batch 5）
