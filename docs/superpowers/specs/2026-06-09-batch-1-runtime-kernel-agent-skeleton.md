# Batch 1: Runtime + Kernel + AgentRuntime 骨架

> 版本: 0.1 | 日期: 2026-06-09 | 状态: 设计评审
> 依赖: Batch 0 接口契约

---

## 一、目标

在 Batch 0 的 async 基础之上，建立 Runtime-Kernel-AgentRuntime 骨架。实现 Mode A（单 agent 交互式对话）走通 `INIT → RUNNING → FINISHED` 完整生命周期。

本 batch 结束时：
- `AgentState` 状态机 + `AgentRuntime.run()` 协程可用，支持 continuous / oneshot 两种模式
- `Kernel` 全局单例可用，支持 spawn_root / send_input / kill / end_workflow / finish_agent / list_agents / all_finished
- `SystemConsole` Protocol 定义完成，`CliConsole` 默认实现可用
- `Runtime.run(harness)` Mode A 入口可启动单 agent 交互式对话
- SIGINT 两阶段处理器可用（优雅退出 + 强制终止）
- 全部现有测试继续通过

---

## 二、前置条件

以下接口/组件已存在且**不做任何修改**：

| 组件 | 位置 | 用途 |
|------|------|------|
| `AsyncInputAdapter` Protocol | `harness/interfaces/async_input_adapter.py` | AgentRuntime 的 I/O 通道 |
| `AsyncCallLLM` Protocol | `harness/interfaces/async_call_llm.py` | async LLM 调用契约 |
| `AsyncLifecycleOrchestrator` | `harness/core/async_orchestrator.py` | 三阶段生命周期编排（_phase_loop 仅执行一轮） |
| `KernelBridgeAdapter` | `harness/runtime/bridge_adapter.py` | 实现 AsyncInputAdapter，内联降级路由 |
| `InternalMessage` / `__EXIT_SENTINEL__` / `AgentOutput` | `harness/runtime/types.py` | 共享类型（AgentOutput 本 batch 正式化） |
| `DIContainer` | `harness/core/container.py` | DI 容器，Harness 持有引用 |
| `LifecycleOrchestrator` | `harness/core/orchestrator.py` | 同步版，不动 |
| 所有现有 `harness.interfaces.types` | `harness/interfaces/types.py` | DTO，全部复用 |

---

## 三、新增/修改的组件

| 组件 | 类型 | 文件 | 需展开内部设计 |
|------|------|------|--------------|
| `SystemConsole` Protocol | 新增 | `harness/interfaces/system_console.py` | 是 |
| `SystemCommand` union type | 新增 | `harness/runtime/types.py`（追加） | 是 |
| `SystemEvent` union type | 新增 | `harness/runtime/types.py`（追加） | 是 |
| `AgentState` 枚举 | 新增 | `harness/runtime/agent_runtime.py` | 是 |
| `AgentRuntime` 类 | 新增 | `harness/runtime/agent_runtime.py` | 是 |
| `Kernel` 类 | 新增 | `harness/runtime/kernel.py` | 是 |
| `CliConsole` 类 | 新增 | `harness/runtime/cli_console.py` | 是 |
| `SIGINT handler` | 新增 | `harness/runtime/signals.py` | 是 |
| `Runtime` 类 | 新增 | `harness/runtime/runtime.py` | 是 |
| `harness/interfaces/__init__.py` | 修改 | `harness/interfaces/__init__.py` | 否（仅加 re-export） |
| `harness/runtime/__init__.py` | 修改 | `harness/runtime/__init__.py` | 否（新增 re-export） |

### 3.1 跨 Batch 接口契约

Batch 1 完成后，暴露给 Batch 2 的稳定 import：

```python
from harness.interfaces.system_console import SystemConsole
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.cli_console import CliConsole
from harness.runtime.signals import create_sigint_handler
from harness.runtime.runtime import Runtime
from harness.runtime.types import (
    InternalMessage, __EXIT_SENTINEL__,
    CommandTalk, SystemCommand,
    AgentSpawned, AgentStateChanged, AgentFinished,
    AgentOutput, RuntimeStarted, RuntimeStopped,
    SystemEvent,
)
```

---

## 四、关键组件设计

### 4.1 AgentState 枚举

```python
# harness/runtime/agent_runtime.py

import enum

class AgentState(enum.Enum):
    """Agent 生命周期状态机。

    CREATED ──→ INIT ──→ RUNNING ──→ TERMINATING ──→ FINISHED
      │           │          │              ▲
      │           │          │              │
      └───────────┴──────────┴──────────────┘
      任何非 FINISHED 状态均可 → TERMINATING
    """

    CREATED     = "created"      # spawn 完成，Task 未启动
    INIT        = "init"         # _phase_init() 执行中，等待首条输入
    RUNNING     = "running"      # 对话循环运行中
    TERMINATING = "terminating"  # _phase_end() 执行中
    FINISHED    = "finished"     # 不可逆终态
```

不在 `TERMINATING` 中添加子状态区分触发原因（exit/error/oneshot/max_rounds）。触发原因通过 `AgentRuntime.error` 字符串字段区分，足够用于日志和调试。

### 4.2 AgentRuntime 类

#### 4.2.1 属性

```
AgentRuntime:
  # 标识
  pid: str                              # 在 Kernel 中的唯一标识
  mode: "continuous" | "oneshot"        # 运行模式

  # 引用
  _harness: Harness                     # 原始 Harness 实例
  _orchestrator: AsyncLifecycleOrchestrator
  _kernel: Kernel                       # 全局单例引用
  adapter: KernelBridgeAdapter          # I/O 通道，构造后挂载

  # 状态
  state: AgentState                     # 当前状态（对外只读快照）
  should_exit: bool                     # 退出标志（Kernel 可外部设置）
  error: str | None                     # 异常信息，None 表示正常
  _idle_since: float | None             # 进入 waiting_input 的时间戳

  # 计数与限制
  round_count: int                      # 已完成轮数
  max_rounds: int                       # 硬上限（安全网），默认 1000

  # 结果
  started_at: float                     # run() 开始时间戳
  last_output: str                      # 最终输出文本
  _finished: asyncio.Event              # 用于 await 该 agent 完成
```

**`_idle_since` 方案**（方案 A）：当 agent 进入 `adapter.receive()` 等待时设为当前时间，`receive()` 返回后设为 `None`。`_idle_for_quiescence()` 只需检查 `_idle_since is not None`。简单可靠，不暴露 orchestrator 内部细节。

#### 4.2.2 构造函数

```python
class AgentRuntime:
    def __init__(
        self,
        *,
        pid: str,
        mode: str,              # "continuous" | "oneshot"
        harness: 'Harness',
        kernel: 'Kernel',
        parent: 'AgentRuntime | None' = None,
        max_rounds: int = 1000,
    ):
        self.pid = pid
        self.mode = mode
        self._harness = harness
        self._kernel = kernel
        self.parent = parent
        self.max_rounds = max_rounds

        self.state = AgentState.CREATED
        self.should_exit = False
        self.error = None
        self._idle_since = None
        self.round_count = 0
        self.started_at = 0.0
        self.last_output = ""
        self._finished = asyncio.Event()

        self.adapter = None  # 由 Kernel.spawn_root() 挂载

        # 子 agent 追踪（Batch 2+ 使用）
        self.children: list[str] = []

        # 创建 AsyncLifecycleOrchestrator
        # call_llm 和 adapter 在 spawn_root 中设置后通过
        # _init_orchestrator() 完成装配
        self._orchestrator: 'AsyncLifecycleOrchestrator | None' = None

    def _init_orchestrator(self, call_llm: 'AsyncCallLLM | None'):
        """在 Kernel 设置好 adapter 和 call_llm 后调用。"""
        self._orchestrator = AsyncLifecycleOrchestrator(
            container=self._harness.container,
            adapter=self.adapter,
            call_llm=call_llm,
        )
```

#### 4.2.3 run() 协程

```
async def run():
    self.state = INIT
    self.started_at = time.time()

    try:
        # 阶段一：等待首条 UserRequest
        ctx = await self._orchestrator._phase_init()

        # _phase_init 中收到 exit → should_exit_flag 为 True
        if self._orchestrator._should_exit_flag:
            self.should_exit = True

        self.state = RUNNING

        # 外层循环 — 每轮对话一次迭代
        while not self.should_exit and self.round_count < self.max_rounds:
            # 阶段二：执行一轮（内循环在 orchestrator 内部）
            await self._orchestrator._phase_loop(ctx)
            self.round_count += 1

            # oneshot 模式：一轮后自动退出
            if self.mode == "oneshot":
                self.should_exit = True
                break

            # 等待下一轮输入
            if self.should_exit:
                break

            self._idle_since = time.time()
            request = await self.adapter.receive()
            self._idle_since = None

            if request.metadata.get("exit"):
                self.should_exit = True
                break

            # 更新 ctx 用于下一轮
            ctx = AssemblyContext(
                user_request=request,
                guides=self._orchestrator._cached_guides,
                available_tools=self._orchestrator._cached_tools,
                history=self._orchestrator._history,
                memories=ctx.memories,
            )

    except Exception as e:
        self.error = f"{type(e).__name__}: {e}"

    finally:
        self.state = TERMINATING
        try:
            traj = self._orchestrator._build_trajectory()
            await asyncio.shield(self._orchestrator._phase_end(traj))
        except asyncio.CancelledError:
            pass  # SIGINT 第二阶段强制退出
        except Exception as e:
            if not self.error:
                self.error = f"_phase_end failed: {e}"

        self.state = FINISHED
        self.last_output = self._extract_last_output()
        self._finished.set()
```

**关键设计决策**：`_phase_init` 中 `should_exit_flag` 设为 True 后，run() 中同步设置 `self.should_exit = True`。外层 while 条件在第一轮开始前就为 False，跳过 `_phase_loop`，直接进入 finally → TERMINATING → FINISHED。

#### 4.2.4 辅助方法

```python
def _idle_for_quiescence(self) -> bool:
    """判断 agent 是否在等待输入（静默检测用）。

    Returns:
        True 如果 agent 在 RUNNING 状态且正在等待 adapter.receive()。
    """
    return (
        self.state == AgentState.RUNNING
        and self._idle_since is not None
    )

def _extract_last_output(self) -> str:
    """从 orchestrator._history 中提取最后一条 assistant role 的内容。"""
    if self._orchestrator and self._orchestrator._history:
        for msg in reversed(self._orchestrator._history):
            if msg.role == "assistant" and msg.content:
                return msg.content
    return ""
```

### 4.3 Kernel 类

#### 4.3.1 数据结构

```python
class Kernel:
    """全局单例。进程表 + 消息路由 + 调度。

    做机制不做策略——不编排 workflow、不决定 agent 行为。
    """

    def __init__(self, console: 'SystemConsole'):
        # 进程表
        self.runtime_table: dict[str, 'AgentRuntime'] = {}
        self.input_queues: dict[str, asyncio.Queue] = {}
        self._tasks: dict[str, asyncio.Task] = {}

        # workflow（Batch 1 仅单 agent，workflow_table 预留）
        self.workflow_table: dict[str, list[str]] = {}
        self._spawn_counter: int = 0

        # 基础设施
        self._console = console
        self.message_bus = None  # Batch 3 替换为 MessageBus
        self._shutdown: bool = False

        # Batch 2-3 预留
        self._pending_subscriptions: list[tuple[str, str]] = []
```

#### 4.3.2 公开方法

**spawn_root(harness) → pid**

```python
def spawn_root(self, harness: 'Harness', call_llm: 'AsyncCallLLM | None' = None) -> str:
    """创建 Mode A 根 agent。

    Args:
        harness: 装配好的 Harness 实例。
        call_llm: async LLM callable。已在 Runtime 入口层做过
                  sync→async 桥接。

    Returns:
        pid: 固定为 "root"。
    """
    pid = "root"

    # 1. 创建 AgentRuntime
    runtime = AgentRuntime(
        pid=pid,
        mode="continuous",  # Mode A root 强制 continuous
        harness=harness,
        kernel=self,
        parent=None,
    )

    # 2. 挂载 KernelBridgeAdapter
    from .bridge_adapter import KernelBridgeAdapter
    runtime.adapter = KernelBridgeAdapter(pid=pid, kernel=self, runtime=runtime)

    # 3. 初始化 orchestrator（call_llm 在 Runtime 入口层已桥接为 async）
    runtime._init_orchestrator(call_llm=call_llm)

    # 4. 注册到进程表
    self.runtime_table[pid] = runtime
    self.input_queues[pid] = asyncio.Queue()

    # 5. 推送 SystemConsole 事件
    from .types import AgentSpawned
    asyncio.create_task(self._console.send(AgentSpawned(pid=pid, parent=None)))

    # 6. 启动 asyncio Task
    task = asyncio.create_task(runtime.run())
    self._tasks[pid] = task
    task.add_done_callback(
        lambda t, r=runtime: asyncio.create_task(self._on_agent_finished(r))
    )

    # 7. 记录 workflow
    self.workflow_table["wf_root"] = [pid]

    return pid
```

**send_input(pid, UserRequest)**

```python
def send_input(self, pid: str, request: 'UserRequest') -> None:
    """向指定 agent 投递 UserRequest。

    框架内部 API——用于 entry_prompt 注入、child_finished 通知等。
    """
    if pid in self.input_queues:
        self.input_queues[pid].put_nowait(request)
    else:
        logger.warning(f"send_input: pid '{pid}' not in input_queues")
```

**kill(pid)**

```python
def kill(self, pid: str) -> None:
    """设置 agent.should_exit = True，推送 __EXIT_SENTINEL__。"""
    agent = self.runtime_table.get(pid)
    if agent and agent.state != AgentState.FINISHED:
        agent.should_exit = True
        if pid in self.input_queues:
            self.input_queues[pid].put_nowait(__EXIT_SENTINEL__)
```

**end_workflow(flag)**

```python
def end_workflow(self, flag: str) -> None:
    """对 workflow_table[flag] 中所有非 FINISHED agent 调用 kill()。"""
    pids = self.workflow_table.get(flag, [])
    for pid in pids:
        self.kill(pid)
```

**finish_agent(pid)**

```python
def finish_agent(self, pid: str) -> None:
    """等同于 kill(pid)，语义上更明确是 agent "自己完成"。"""
    self.kill(pid)
```

**list_agents()**

```python
def list_agents(self) -> dict[str, dict]:
    """返回 runtime_table 的只读快照。

    Returns:
        dict[pid, {"state": str, "mode": str, "parent": str|None,
                    "rounds": int, "error": str|None}]
    """
    return {
        pid: {
            "state": r.state.value,
            "mode": r.mode,
            "parent": r.parent.pid if r.parent else None,
            "rounds": r.round_count,
            "error": r.error,
        }
        for pid, r in self.runtime_table.items()
    }
```

**all_finished()**

```python
def all_finished(self) -> bool:
    """所有 runtime_table 中的 agent 是否均为 FINISHED。"""
    return all(
        r.state == AgentState.FINISHED
        for r in self.runtime_table.values()
    )
```

#### 4.3.3 内部方法（Batch 1 stub）

**\_on_agent_finished(runtime)** — stub

```python
async def _on_agent_finished(self, runtime: 'AgentRuntime') -> None:
    """agent FINISHED 时的回调（由 Task.done_callback 触发）。

    Batch 1 stub: 仅推送 AgentFinished 事件到 SystemConsole。
    Batch 3 完整实现: + child_finished 默认订阅 + 级联终止。
    """
    from .types import AgentFinished

    duration = time.time() - runtime.started_at
    await self._console.send(AgentFinished(
        pid=runtime.pid,
        result=runtime.last_output,
        duration=duration,
        error=runtime.error,
    ))
```

**\_monitor_quiescence()** — stub

```python
async def _monitor_quiescence(self) -> None:
    """静默检测监控协程。

    Batch 1 stub: 只等待所有 agent FINISHED 后返回。
    Batch 3 完整实现: 检测 idle 后主动推送 sentinel。
    """
    while not self._shutdown:
        if self.all_finished():
            return
        await asyncio.sleep(1)
```

**\_handle_system_input()** — stub

```python
async def _handle_system_input(self) -> None:
    """系统输入处理循环。

    Batch 1 stub: 仅纯文本路由到 root（Mode A）。
    Batch 4 完整实现: /agents /kill /end /exit /talk 命令解析。
    """
    while not self._shutdown:
        command = await self._console.receive()

        if isinstance(command, CommandTalk):
            # Batch 1: 纯文本全部路由到 root
            target_pid = command.pid
            if target_pid in self.runtime_table:
                self.send_input(target_pid,
                    UserRequest(text=command.text))
            else:
                logger.warning(
                    f"No agent with pid '{target_pid}' for routing text"
                )
```

#### 4.3.4 内部方法逐 Batch 完成度矩阵

| 方法 | Batch 1 | Batch 2 | Batch 3 | Batch 4 |
|------|---------|---------|---------|---------|
| `_on_agent_finished(runtime)` | stub：仅推送 AgentFinished 到 SystemConsole | （不变） | 完整实现：+ child_finished + 级联终止 | （不变） |
| `_monitor_quiescence()` | stub：`while not all_finished(): sleep(1)` | （不变） | 完整实现：idle 检测 + 推送 sentinel | （不变） |
| `_handle_system_input()` | stub：纯文本路由到 root | （不变） | （不变） | 完整实现：命令解析 |
| `spawn_root(harness)` | **完整实现** | （不变） | （不变） | （不变） |
| `spawn_from_script(...)` | — | 完整实现 | 升级：pending subscriptions | （不变） |

### 4.4 SystemConsole Protocol

```python
# harness/interfaces/system_console.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class SystemConsole(Protocol):
    """Runtime 系统级交互接口。

    和 AsyncInputAdapter 的区别：
    - AsyncInputAdapter：一个 agent 的 stdin/stdout
    - SystemConsole：整个 Runtime 的"控制台"，处理系统命令和事件
    """

    async def receive(self) -> 'SystemCommand':
        """接收用户输入，返回解析后的系统命令。"""
        ...

    async def send(self, event: 'SystemEvent') -> None:
        """推送系统级事件。"""
        ...
```

### 4.5 SystemCommand / SystemEvent 类型（types.py 追加）

```python
# harness/runtime/types.py（追加）

from dataclasses import dataclass


# ── SystemCommand（Batch 1 最小版本）───────────────────────

@dataclass
class CommandTalk:
    """纯文本输入，路由到指定 agent。"""
    pid: str
    text: str


# Batch 1 仅此一个命令类型。
# Batch 4 追加: CommandKill, CommandListAgents, CommandEndWorkflow,
# CommandExit, CommandTalkDirect
SystemCommand = CommandTalk


# ── SystemEvent（Batch 1 最小版本）────────────────────────

@dataclass
class AgentSpawned:
    """agent spawn 完成通知。"""
    pid: str
    parent: str | None = None


@dataclass
class AgentStateChanged:
    """agent 状态变更通知。"""
    pid: str
    old: str
    new: str


@dataclass
class AgentFinished:
    """agent 进入 FINISHED 通知。"""
    pid: str
    result: str = ""
    duration: float = 0.0
    error: str | None = None


@dataclass
class RuntimeStarted:
    """Runtime 启动完成。"""
    pass


@dataclass
class RuntimeStopped:
    """Runtime 所有 agent 已结束。"""
    pass


# AgentOutput 从 Batch 0 的临时类型正式化——不再标记为"临时"。
# 已存在于 harness/runtime/types.py，无需重复定义。

SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | 'AgentOutput' | RuntimeStarted | RuntimeStopped
)
```

### 4.6 CliConsole 类

```python
# harness/runtime/cli_console.py

import sys

class CliConsole:
    """SystemConsole 默认 CLI 实现。

    receive(): asyncio.to_thread(sys.stdin.readline)
    send(): 按事件类型格式化输出到 stdout
    """

    async def receive(self) -> SystemCommand:
        """从 stdin 读取一行，路由到 root agent。

        Batch 1: 纯文本全部路由到 root。
        Batch 4: 追加 "/" 前缀命令解析。
        """
        line = await asyncio.to_thread(sys.stdin.readline)
        line = line.rstrip("\n")
        return CommandTalk(pid="root", text=line)

    async def send(self, event: 'SystemEvent') -> None:
        """按事件类型格式化输出到 stdout。"""
        if isinstance(event, AgentSpawned):
            print(f"[系统] Agent spawned: {event.pid}")

        elif isinstance(event, AgentFinished):
            status = "异常退出" if event.error else "正常完成"
            print(
                f"[系统] Agent finished: {event.pid} "
                f"({event.duration:.1f}s, {status})"
            )

        elif isinstance(event, AgentOutput):
            print(f"[{event.pid}] {event.content}")

        elif isinstance(event, AgentStateChanged):
            print(
                f"[系统] Agent {event.pid}: "
                f"{event.old} → {event.new}"
            )

        elif isinstance(event, RuntimeStarted):
            print("[系统] Runtime 启动")

        elif isinstance(event, RuntimeStopped):
            print("[系统] Runtime 停止")
```

**设计要点**：
- `send()` 使用同步 `print()`。SystemConsole.send 的 `async` 签名兼容未来的异步 I/O（如 WebSocket console），CLI 实现中 print 不需要 await
- `asyncio.to_thread(sys.stdin.readline)` 在 asyncio 单线程模型下安全：`readline` 在后台线程阻塞
- SIGINT / Ctrl+D / EOF 时 `readline` 返回空字符串 → `CommandTalk(pid="root", text="")` → root agent 的 `_should_exit()` 检测空 text → 正常退出

### 4.7 SIGINT 处理器

```python
# harness/runtime/signals.py

import signal
from .types import __EXIT_SENTINEL__


def create_sigint_handler(runtime: 'Runtime'):
    """创建两阶段 SIGINT 处理器。

    第一阶段（首次 Ctrl+C）：
      推送 __EXIT_SENTINEL__ 给所有 agent，优雅退出。
    第二阶段（再次 Ctrl+C）：
      强制 task.cancel() 所有协程，立即终止。

    返回的 handler 由 Runtime.run() 通过
    loop.add_signal_handler(signal.SIGINT, handler) 注册。
    """
    def _on_sigint():
        kernel = runtime._kernel
        if kernel is None:
            return

        if runtime._sigint_count == 0:
            # 第一阶段：优雅退出
            runtime._sigint_count = 1
            for pid, agent in kernel.runtime_table.items():
                if agent.state.value != "finished":
                    agent.should_exit = True
                    if pid in kernel.input_queues:
                        kernel.input_queues[pid].put_nowait(__EXIT_SENTINEL__)
        else:
            # 第二阶段：强制终止
            for task in kernel._tasks.values():
                if not task.done():
                    task.cancel()

    return _on_sigint
```

### 4.8 Runtime 类

```python
# harness/runtime/runtime.py

import asyncio
import signal

class Runtime:
    """Runtime 顶层入口。

    创建 Kernel + 启动事件循环 + 注册信号处理。

    用法:
        console = CliConsole()
        root_harness = Harness.from_container(container, call_llm=my_llm)
        Runtime(console).run(root_harness)
    """

    def __init__(self, console: 'SystemConsole'):
        self._console = console
        self._kernel: 'Kernel | None' = None
        self._sigint_count: int = 0

    def run(self, harness) -> None:
        """同步入口 — 启动整个 Runtime。

        Args:
            harness: 装配好的 Harness 实例。其 container 属性
                     和 call_llm 属性用于创建 AgentRuntime
                     的内部 AsyncLifecycleOrchestrator。
        """
        try:
            asyncio.run(self._run_async(harness))
        except KeyboardInterrupt:
            pass  # SIGINT 已在 _on_sigint 中处理

    async def _run_async(self, harness) -> None:
        """异步主流程。"""
        from .kernel import Kernel
        from .signals import create_sigint_handler
        from .types import RuntimeStarted, RuntimeStopped

        # 1. sync→async LLM bridge（在 Runtime 入口层，不侵入 orchestrator）
        call_llm = getattr(harness, 'call_llm', None)
        if call_llm and not asyncio.iscoroutinefunction(call_llm):
            original = call_llm
            async def _async_wrapper(msgs, tools):
                return await asyncio.to_thread(original, msgs, tools)
            call_llm = _async_wrapper

        # 2. 创建 Kernel
        self._kernel = Kernel(self._console)

        # 3. spawn root agent
        self._kernel.spawn_root(harness, call_llm=call_llm)

        # 4. 启动三个协程
        root_runtime = self._kernel.runtime_table["root"]
        task_root = self._kernel._tasks["root"]
        task_sys = asyncio.create_task(
            self._kernel._handle_system_input()
        )
        task_mon = asyncio.create_task(
            self._kernel._monitor_quiescence()
        )

        # 5. 注册信号处理
        handler = create_sigint_handler(self)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, handler)
        except NotImplementedError:
            pass  # 非主线程或无信号支持的平台

        # 6. 推送启动事件
        await self._console.send(RuntimeStarted())

        # 7. 等待全部完成
        try:
            await asyncio.gather(
                task_root, task_sys, task_mon,
                return_exceptions=True,
            )
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass

        # 8. 推送停止事件
        await self._console.send(RuntimeStopped())
```

**Harness 集成点说明**：`AgentRuntime._init_orchestrator()` 从 `self._harness.container` 获取 DI 容器，将 `call_llm`（已桥接为 async）和 `adapter`（KernelBridgeAdapter）传入 `AsyncLifecycleOrchestrator` 构造函数。Harness 自身不需要修改——它只需暴露 `container` 属性（已存在）。

---

## 五、数据流

### 5.1 Mode A 单次对话完整路径

```
用户输入 "hello" → stdin
  │
  ▼
CliConsole.receive()
  → asyncio.to_thread(sys.stdin.readline)
  → CommandTalk(pid="root", text="hello")
  │
  ▼
Kernel._handle_system_input()  [stub — 纯文本路由]
  → send_input("root", UserRequest(text="hello"))
  → input_queues["root"].put_nowait(UserRequest)
  │
  ▼
AgentRuntime.run()  [外层 while]
  │  await adapter.receive() 被唤醒
  │  _idle_since = None
  ▼
AsyncLifecycleOrchestrator._phase_loop(ctx)
  │  组装上下文 → await call_llm → TextEvent("Hello! How can I help?")
  │  → StopEvent("end_turn")
  │  → 内层 break → return
  ▼
AgentRuntime.run()  [回到外层 while]
  │  round_count += 1
  │  mode="continuous" → 不退出
  │  _idle_since = time.time()
  │  await adapter.receive()  [阻塞，等待下一轮]
  │
  ▼
[并行] KernelBridgeAdapter.send(TextEvent)  [在 orchestrator 内部调用]
  │  should_exit? → false
  │  target=None, TextEvent
  │  → Batch 0-2 降级路由:
  │    console.send(AgentOutput(pid="root", content="Hello! How can I help?"))
  │
  ▼
CliConsole.send(AgentOutput)
  → print("[root] Hello! How can I help?")
```

### 5.2 oneshot 模式退出路径

```
AgentRuntime.run():
  ...
  await _orchestrator._phase_loop(ctx)  # 执行一轮
  self.round_count += 1
  if self.mode == "oneshot":
      self.should_exit = True
      break                               # 退出外层 while
  → finally:
      _phase_end(traj)
      state = FINISHED
      _finished.set()

Kernel._on_agent_finished(runtime):      # Task.done_callback 触发
  → console.send(AgentFinished(pid=..., ...))
```

### 5.3 SIGINT 优雅退出路径

```
用户 Ctrl+C
  │
  ▼
_on_sigint()  [第一阶段]
  → 所有 agent: should_exit = True
  → input_queues[pid].put_nowait(__EXIT_SENTINEL__)
  │
  ▼
AgentRuntime.run()  [在 adapter.receive() 中阻塞的 agent]
  │  input_queues["root"].get() → __EXIT_SENTINEL__
  │  → _should_exit 检测 → break
  │  → finally: _phase_end → FINISHED
  │
  │  [在 call_llm() 中等 LLM 响应的 agent]
  │  LLM 返回后 → 下一轮 while 条件: should_exit = True → break
  │  → finally: _phase_end → FINISHED
  │
  ▼
all_finished() → True
_monitor_quiescence 退出
_handle_system_input: _shutdown 仍为 False — 需要等下一轮 receive() 或 Ctrl+C 第二阶段
  │
  ▼
⚠ 问题: _handle_system_input 在 await console.receive() 处阻塞。
   stdin readline 在线程中阻塞。SIGINT 不中断 asyncio.to_thread。
   用户需要再按一次 Ctrl+C（第二阶段）→ task.cancel() 才能完全退出。
   或者用户按 Enter → readline 返回空字符串 → 路由给 root → root
   可能已 FINISHED——send_input 中 logger.warning + 跳过。
   → _shutdown 应在 Runtime 层面在 all_finished 后设为 True。
```

**改进**：在 `Runtime._run_async` 中，`asyncio.gather` 返回后（说明 root task 和 monitor 都已退出），`_shutdown` 设为 True，但 `_handle_system_input` 仍在 `console.receive()` 处阻塞。SIGINT 第一阶段后应**同时**设置 `_shutdown = True`，让 `_handle_system_input` 在下次循环时退出。但 `readline` 是阻塞的——需要额外的机制。

**Batch 1 的务实方案**：接受这个限制。Mode A 下用户自然按 Enter 或 Ctrl+D 结束 stdin。SIGINT 两阶段机制（第一次优雅、第二次强制）在实际交互中够用。如需优化，Batch 4 可引入 `asyncio.wait_for` + `loop.add_reader` 替代 `to_thread(readline)`。

---

## 六、错误处理

| 异常来源 | 处理位置 | 策略 |
|---------|---------|------|
| `adapter.receive()` 异常 | `AgentRuntime.run()` | 传播到 `except Exception` → 记录 `self.error` → finally 执行 `_phase_end` |
| `_phase_loop` 内 `call_llm()` 异常 | `AgentRuntime.run()` | 同同步版——传播到 run() → `_phase_end` 在 finally 中执行 |
| `_phase_end` 自身异常 | `AgentRuntime.run()` finally | `asyncio.shield` 保护；catch 后记录到 `self.error`，不阻止 FINISHED |
| `_phase_end` 中 `CancelledError` | `AgentRuntime.run()` finally | catch 后跳过清理——SIGINT 第二阶段 |
| `asyncio.to_thread` 中 stdin 异常 | `CliConsole.receive()` | `readline` 在 EOF 时返回空字符串 → `CommandTalk(text="")` → root 的 `_should_exit()` 检测空 text → 退出 |
| `Kernel.input_queues[pid]` KeyError | `send_input()` / `kill()` | 调用前检查 pid 是否在 `runtime_table` 中；不存在则 WARNING 日志 + 忽略 |
| `Kernel.spawn_root` 中 `asyncio.create_task` 失败 | `spawn_root()` | 异常传播给 Runtime.run()，由 asyncio.run() 处理 |
| `AgentRuntime._orchestrator` 为 None 时调用 `_phase_loop` | `AgentRuntime.run()` | `_init_orchestrator` 在构造时检查 call_llm 和 adapter 是否设置；未设置则 raise RuntimeError |

### `__EXIT_SENTINEL__` 边界条件

| 场景 | 行为 |
|------|------|
| init 阶段收到 sentinel | `_phase_init` 中 `_should_exit()` 检测 → `UserRequest(text="", metadata={exit:True})` → `should_exit_flag=True` → `AgentRuntime.run()` 中 `self.should_exit = True` → while 跳过 → finally → FINISHED |
| loop 阶段在 receive() 中收到 sentinel | `adapter.receive()` → `UserRequest(metadata={exit:True})` → `request.metadata.get("exit")` → `should_exit = True` → while break |
| loop 阶段在 call_llm 中收到 sentinel | sentinel 入队但 `receive()` 尚未被调用。LLM 返回后 while 条件检查 `should_exit` → True → break |
| 重复 sentinel | 第一次取到 sentinel 后 `should_exit` 已为 True，外层 while 已 break。第二次 sentinel 仍在队列中——不影响（agent 不会再次 `receive()`） |

---

## 七、测试策略

### 7.1 单元测试场景

#### AgentState

| 测试 | 验证点 |
|------|--------|
| 枚举值正确定义 | 5 个成员：CREATED/INIT/RUNNING/TERMINATING/FINISHED |
| 枚举值可用 `.value` 访问 | 字符串形式正确 |

#### AgentRuntime

| 测试 | 验证点 |
|------|--------|
| 构造：默认状态为 CREATED | state, should_exit=False, _idle_since=None |
| 构造：continuous 模式参数正确存储 | mode="continuous" |
| 构造：oneshot 模式参数正确存储 | mode="oneshot" |
| `_init_orchestrator`：正确创建 orchestrator | adapter 和 call_llm 正确传入 |
| `run()` 完整三阶段（mock 组件）| INIT→RUNNING→FINISHED，round_count≥1 |
| `run()` oneshot 一轮退出 | mode="oneshot" 时 round_count==1 即 FINISHED |
| `run()` continuous 等第二轮 | 首轮后 receive() 被调用（通过 adapter spy） |
| `run()` should_exit 提前退出 | 外部设 should_exit=True → 退出 while → FINISHED |
| `run()` 异常时 _phase_end 仍执行 | call_llm 抛异常 → error 记录 → _phase_end 在 finally 执行 |
| `run()` max_rounds 安全网 | 到达 max_rounds → 退出 |
| `_idle_since` 设置和清除 | receive() 前设置，返回后为 None |
| `_idle_for_quiescence()`：RUNNING + idle → True | _idle_since 非 None 且 state==RUNNING |
| `_idle_for_quiescence()`：RUNNING + not idle → False | _idle_since 为 None |
| `_idle_for_quiescence()`：非 RUNNING → False | 即使 _idle_since 非 None，state 不是 RUNNING |
| `_extract_last_output`：正常提取 | history 最后一条 assistant content |
| `_extract_last_output`：空 history | 返回 "" |
| `_extract_last_output`：无 assistant 消息 | 返回 "" |

#### Kernel

| 测试 | 验证点 |
|------|--------|
| `spawn_root` 创建 agent 并注册 | runtime_table["root"] 存在，pid="root", mode="continuous" |
| `spawn_root` 设置 input_queue | input_queues["root"] 非空 asyncio.Queue |
| `spawn_root` 启动 asyncio Task | _tasks["root"] 存在 |
| `spawn_root` 推送 AgentSpawned | console.send 被调用（通过 spy） |
| `send_input` 正确入队 | input_queues["root"].get_nowait() 得到 UserRequest |
| `send_input` 对不存在 pid 的防御 | WARNING 日志，不抛异常 |
| `kill` 设 should_exit + 推 sentinel | agent.should_exit=True, queue 中有 __EXIT_SENTINEL__ |
| `kill` 对已 FINISHED agent 无操作 | should_exit 不改变 |
| `end_workflow` 对 workflow 所有 agent 调 kill | 所有 agent 的 should_exit=True |
| `end_workflow` 对不存在 flag 无操作 | 不崩溃 |
| `finish_agent` 等同于 kill | agent.should_exit=True, 队列有 sentinel |
| `list_agents` 快照正确 | 返回 dict 含 state/mode/parent/rounds/error |
| `all_finished`：全部 FINISHED → True | 所有 agent state==FINISHED |
| `all_finished`：有非 FINISHED → False | 至少一个 agent state!=FINISHED |
| `_on_agent_finished` stub 推送事件 | console.send 收到 AgentFinished |
| `_monitor_quiescence` stub 等 all_finished | 所有 FINISHED 后返回 |
| `_handle_system_input` stub 纯文本路由 | CommandTalk → send_input to root |

#### CliConsole

| 测试 | 验证点 |
|------|--------|
| `send(AgentSpawned)` | 格式化输出含 pid |
| `send(AgentFinished)` | 正常/异常两种格式化 |
| `send(AgentOutput)` | 显示 [pid] 标签 |
| `send(RuntimeStarted)` | 格式化输出 |
| `send(RuntimeStopped)` | 格式化输出 |

#### Runtime

| 测试 | 验证点 |
|------|--------|
| `run()` 完整启动流程（mock 组件）| Runtime.run() 正常返回 |
| sync→async LLM bridge | 同步 call_llm 被包装为 async |
| SIGINT 优雅退出（mock） | 第一次 SIGINT → agent 收到 sentinel → FINISHED |

#### 集成测试

| 测试 | 验证点 |
|------|--------|
| KBA + 真实 Kernel | receive/send 消息完整流转 |
| AgentRuntime + Kernel + KBA | 完整 run() 流程通 |
| Runtime + CliConsole + 完整链路 | 端到端 Mode A 走通 |

### 7.2 Mock/Stub 清单

| Mock 对象 | 最小接口 | 用途 |
|----------|---------|------|
| `MockAsyncLLM` | `async __call__(msgs, tools) → Response` | AgentRuntime 和 Runtime 测试 |
| `MockConsole` | `async receive() → SystemCommand`, `async send(event)` + spy | Kernel 和 CliConsole 测试 |
| `MockHarness` | `container: DIContainer` 属性 | AgentRuntime 构造测试 |

### 7.3 现有测试回归

Batch 1 **不改动任何 Batch 0 已动过的文件**（除 `__init__.py` 追加 re-export）。引入 `AgentState` enum 不影响 `harness/interfaces/types.py` 中的现有类型。

- `pytest` 全部现有 ~25 个测试文件继续通过
- 无 import 错误（新增模块不影响已有 import 路径）

---

## 八、验收标准

### AC-1.1 AgentState + AgentRuntime
- [ ] `AgentState` 枚举 5 个成员定义在 `harness/runtime/agent_runtime.py`
- [ ] `AgentRuntime` 构造函数接受 `pid`, `mode`, `harness`, `kernel` 必选参数
- [ ] `AgentRuntime._init_orchestrator(call_llm)` 正确创建 `AsyncLifecycleOrchestrator`
- [ ] `AgentRuntime.run()` 包含外层 while（控制 max_rounds, should_exit, adapter.receive）
- [ ] `_idle_since` 在 receive() 前设为时间戳，返回后设为 None
- [ ] `_idle_for_quiescence()` 判断逻辑：state==RUNNING and _idle_since is not None
- [ ] `_extract_last_output()` 从 orchestrator._history 提取最后 assistant content
- [ ] `AgentRuntime.asyncio.shield` 保护 `_phase_end`，`CancelledError` 被跳过
- [ ] oneshot 模式：一轮后自动 `should_exit = True` + break
- [ ] continuous 模式：一轮后继续等待下一轮 `adapter.receive()`

### AC-1.2 Kernel
- [ ] `Kernel` 构造函数接受 `console: SystemConsole`
- [ ] `spawn_root(harness, call_llm)` 完整流程：创建 AgentRuntime → 挂载 KBA → 注册 → 启动 Task
- [ ] `spawn_root` 返回 `"root"`
- [ ] `send_input(pid, UserRequest)` 正确入队
- [ ] `kill(pid)` 设 `should_exit=True` + 推 `__EXIT_SENTINEL__`
- [ ] `end_workflow(flag)` / `finish_agent(pid)` 正确委托 kill
- [ ] `list_agents()` 返回只读快照
- [ ] `all_finished()` 正确判定全部 FINISHED
- [ ] `_on_agent_finished(runtime)` stub：仅推送 AgentFinished 到 SystemConsole
- [ ] `_monitor_quiescence()` stub：等 all_finished 后返回
- [ ] `_handle_system_input()` stub：纯文本路由到 root

### AC-1.3 SystemConsole Protocol + SystemCommand/SystemEvent
- [ ] `SystemConsole` Protocol 定义在 `harness/interfaces/system_console.py`
- [ ] `@runtime_checkable`，`isinstance(obj, SystemConsole)` 可用于运行时检查
- [ ] `CommandTalk` 定义在 `harness/runtime/types.py`
- [ ] `AgentSpawned`, `AgentStateChanged`, `AgentFinished`, `RuntimeStarted`, `RuntimeStopped` 定义在 `harness/runtime/types.py`
- [ ] `AgentOutput` 注释从"临时"改为正式 SystemEvent

### AC-1.4 CliConsole
- [ ] `CliConsole` 实现 `SystemConsole` Protocol
- [ ] `receive()` 使用 `asyncio.to_thread(sys.stdin.readline)`
- [ ] `receive()` 返回 `CommandTalk(pid="root", text=...)`
- [ ] `send()` 按事件类型格式化输出到 stdout

### AC-1.5 Runtime + Signals
- [ ] `Runtime` 构造函数接受 `console: SystemConsole`
- [ ] `Runtime.run(harness)` Mode A 入口可启动完整对话
- [ ] sync→async LLM bridge 在 Runtime 入口层（`asyncio.iscoroutinefunction` 检测）
- [ ] `asyncio.gather(task_root, task_sys, task_mon, return_exceptions=True)`
- [ ] SIGINT 两阶段处理器：第一阶段推 sentinel，第二阶段 task.cancel()
- [ ] `loop.add_signal_handler` 失败时优雅降级（catch NotImplementedError）

### AC-1.6 包结构
- [ ] `harness/interfaces/system_console.py` 创建
- [ ] `harness/runtime/agent_runtime.py` 创建
- [ ] `harness/runtime/kernel.py` 创建
- [ ] `harness/runtime/cli_console.py` 创建
- [ ] `harness/runtime/signals.py` 创建
- [ ] `harness/runtime/runtime.py` 创建
- [ ] `harness/interfaces/__init__.py` 新增 `SystemConsole` re-export
- [ ] `harness/runtime/__init__.py` 新增所有公开类 re-export

### AC-1.7 现有测试不退化
- [ ] `pytest` 全部现有测试通过
- [ ] 无 import 错误

### AC-1.8 新测试
- [ ] AgentState 测试 ≥ 2 条
- [ ] AgentRuntime 测试 ≥ 12 条
- [ ] Kernel 测试 ≥ 12 条
- [ ] CliConsole 测试 ≥ 5 条
- [ ] Runtime + signals 集成测试 ≥ 3 条

---

## 九、与后续 Batch 的接口约定

### 9.1 Batch 1 → Batch 2

```
暴露:
  from harness.interfaces.system_console import SystemConsole
  from harness.runtime.kernel import Kernel
  from harness.runtime.agent_runtime import AgentRuntime, AgentState
  from harness.runtime.cli_console import CliConsole
  from harness.runtime.signals import create_sigint_handler
  from harness.runtime.runtime import Runtime
  from harness.runtime.types import (
      CommandTalk, SystemCommand,
      AgentSpawned, AgentStateChanged, AgentFinished,
      AgentOutput, RuntimeStarted, RuntimeStopped,
      SystemEvent,
  )

Batch 2 依赖:
  - Kernel 可接受 spawn_from_script() 方法（新增）
  - AgentRuntime 的 parent/children 属性用于构建进程树
  - _pending_subscriptions 在 Batch 2 暂存订阅关系
  - SystemConsole / CliConsole 无需修改
```

### 9.2 职责边界（本 Batch 不做的内容）

以下内容**不属于 Batch 1**，在后续 batch 中实现：

- ❌ `Kernel.spawn_from_script()` — Batch 2
- ❌ `@agent` / `subscribe` 装饰器 — Batch 2
- ❌ `spawn_workflow` / `end_workflow` / `finish_agent` / `talk_to` / `list_agents` tool — Batch 2
- ❌ MessageBus — Batch 3
- ❌ 默认订阅（child_finished）、流式订阅、级联终止 — Batch 3
- ❌ 静默检测完整实现 — Batch 3
- ❌ `Runtime.run_from_script()` Mode B — Batch 3
- ❌ `/agents` `/kill` `/end` `/exit` `/talk` 命令解析 — Batch 4
- ❌ `_handle_system_input` 完整实现 — Batch 4
- ❌ KBA 降级路由 → MessageBus 升级 — Batch 3
