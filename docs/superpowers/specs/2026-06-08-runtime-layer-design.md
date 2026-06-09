# Runtime Layer 顶层设计

> 版本: 0.2 | 日期: 2026-06-09 | 状态: 设计评审

---

## 一、动机

当前 Harness Agent Template 是单 Agent 模型：一个 DI 容器 → 一个 LifecycleOrchestrator → 一个三阶段生命周期。框架文档中已存在 "Recursive Harness Pattern"（组件内部 new 一个 Harness 实例），但缺少一等公民支持：

- 父子关系无追踪，无法获取子 agent 句柄
- 无标准通信机制，只能通过返回值传递结果
- 无生命周期管理，子 agent 异常时父 agent 无感知
- Workflow 编排逻辑散落在 tool 实现中，不可见不可控

本设计在现有 Harness 层之上封装一层 **Runtime**，以 OS 进程模型为类比，提供多 Agent Runtime 的生命周期管理、消息通信与 Workflow 编排能力。

**约束**：不改变现有已固定的接口（Harness、LifecycleOrchestrator、DIContainer 及各 Protocol），核心流程通过外层封装和 async 化实现。

---

## 二、架构层次

```
┌──────────────────────────────────────────────────────────────┐
│  SystemConsole  ← 用户交互的唯一入口（新接口，独立于 InputAdapter） │
└──────────────────────────┬───────────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    │   Runtime   │  新顶层入口
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │   Kernel    │  全局单例：进程表 + 消息总线 + 调度
                    └──┬──────┬───┘
                       │      │
              ┌────────▼┐ ┌───▼────────┐
              │AgentRunt│ │AgentRunt   │  × N，每个 agent 一个实例
              │  ime    │ │  ime       │
              │         │ │            │
              │ ┌─────┐ │ │ ┌────────┐ │
              │ │Harns│ │ │ │Harns   │ │  保留接口，内部委托
              │ └──┬──┘ │ │ └───┬────┘ │
              │    │    │ │     │      │
              │ ┌──▼──┐ │ │ ┌───▼───┐ │
              │ │Orch │ │ │ │Orch   │ │  改为 async
              │ └─────┘ │ │ └───────┘ │
              └─────────┘ └───────────┘
```

**命名与职责**：

| 层 | 职责 | 实例数 |
|----|------|--------|
| Runtime | 顶层入口，创建 Kernel + SystemConsole，启动事件循环，注册信号处理 | 1 |
| Kernel | 进程表维护、消息按订阅路由、spawn / kill / end_workflow、静默检测 | 1 (全局单例) |
| AgentRuntime | 每个 agent 的"进程控制块"：父子引用、状态机、模式（continuous / oneshot）、包裹 LifecycleOrchestrator | N |
| AsyncLifecycleOrchestrator | 新增，参照原 LifecycleOrchestrator 迁移为 async（阻塞点 `await`）。原版不动 | 每个 AgentRuntime 1 个 |
| Harness | 现有接口，`from_container()` 保留，外部代码不感知 Runtime 层 | 每个 AgentRuntime 1 个 |
| SystemConsole | Runtime 级别系统交互：接收命令、推送系统事件（含查询响应） | 1 |
| KernelBridgeAdapter | 实现 InputAdapter Protocol，对接 Kernel 的消息队列。Agent 对 I/O 的唯一通道 | 每个 AgentRuntime 1 个 |

**Harness 兼容性**：`Harness.from_container(container, call_llm).run()` 的公开 API 保持不变。Runtime 层在 Harness 之上，Harness 不再直接 `.run()`，而是作为 AgentRuntime 内部的执行委托对象。已有代码（YamlAssembler、examples/minimal_agent.py）不感知 Runtime 层的存在。

---

## 三、Agent 生命周期

每个 agent 是 Runtime 中的一个"进程"。其生命周期由 AgentRuntime 状态机管理。

### 3.1 状态机

```
CREATED ──→ INIT ──→ RUNNING ──→ TERMINATING ──→ FINISHED
              │          │            ▲
              │          │            │
              └──────────┴────────────┘
              任何状态均可 → TERMINATING
              （收到 exit 信号 / end_workflow / 异常 / oneshot 完成）
```

| 状态 | 含义 | 进入条件 | 离开条件 |
|------|------|---------|---------|
| CREATED | spawn 完成，Task 未创建 | Kernel.spawn() | `asyncio.create_task(run())` |
| INIT | `_phase_init()` 执行中，等首条输入 | `run()` 开始 | 收到首个 UserRequest，init 完成 |
| RUNNING | 对话循环运行中 | init 完成 | should_exit / 异常 / oneshot 本轮结束 |
| TERMINATING | `_phase_end()` 执行中 | should_exit = True | 清理完成 |
| FINISHED | 会话结束，不可逆 | `_phase_end()` 完成 | — |

**TERMINATING 的触发路径**：

| 从状态 | 触发条件 |
|--------|---------|
| CREATED | Kernel.end_workflow 在 spawn 后但 start 前调用 |
| INIT | `adapter.receive()` 返回 `__EXIT_SENTINEL__` 包装的 UserRequest |
| RUNNING | `should_exit = True`（end_workflow / finish_agent / max_rounds / 静默检测）；`adapter.receive()` 返回 `__EXIT_SENTINEL__` |
| RUNNING (await LLM 中) | LLM 返回后 `should_exit` 为 True，或 input_queue 中排队了 `__EXIT_SENTINEL__` |
| 任意状态 | 未捕获异常传播到 `run()` → finally 块执行 |

**`__EXIT_SENTINEL__` vs `should_exit`**：两者协作覆盖不同的等待场景。

- **`__EXIT_SENTINEL__`** 覆盖 agent 在 `await adapter.receive()`（WAITING_INPUT）中的场景：队列中取出 sentinel，`KernelBridgeAdapter.receive()` 将其转换为 `UserRequest(text="", metadata={"exit": True})`，现有 `_should_exit()` 方法检测到 `metadata["exit"]`。
- **`should_exit` 布尔标志** 覆盖 agent 在 `await call_llm()`（WAITING_LLM）中的场景：LLM 返回后，`AgentRuntime.run()` 的外层 while 在下一轮开始前检查 `should_exit`，为 True 则直接 break。

两者互补。`KernelBridgeAdapter.send()` 额外检查 `should_exit`：如果 agent 正在退出但 LLM 返回后仍产出了 TextEvent/StopEvent，send 静默丢弃这些输出，避免"最后一轮污染"。

### 3.2 Agent 运行模式

一个 AgentRuntime 处于两种模式之一：

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `continuous` | StopEvent 后进入 WAITING_INPUT，等待下一轮输入。只响应外部信号终止 | 交互式对话（Mode A root）、需要持续通信的 workflow agent |
| `oneshot` | 一轮对话结束（StopEvent 已发送）后自动进入 TERMINATING，不等下一轮输入 | Mode B entry agent、spawn 的子 agent（完成 entry_prompt 即退出） |

**模式判定规则**：

- **Mode A 的 root agent**：强制 `continuous`
- **Mode B 的 entry agent**：强制 `oneshot`
- **spawn_from_script 创建的子 agent**：如果脚本中显式声明了 `subscribe` 则 `continuous`，否则 `oneshot`

规则在 `AgentRuntime.__init__` 中确定，LLM 不可见。

模式仅影响 agent 完成一轮后的默认行为。无论哪种模式，`end_workflow`、`max_rounds`、异常、静默检测都可以在任何时候终止 agent。

### 3.3 终止路径汇总

| 终止方式 | 触发者 | 机制 | 适用范围 |
|---------|--------|------|---------|
| `oneshot` 自动结束 | Runtime（AgentRuntime.run） | 本轮 StopEvent 后 `should_exit = True` | oneshot agent |
| `end_workflow(flag)` | 父 agent (LLM tool) 或 用户 (/end) | Kernel 向 workflow 所有 agent 推 `__EXIT_SENTINEL__` + 设 `should_exit` | workflow 级 |
| `finish_agent()` | agent 自身 (LLM tool) | Kernel 向自己的 input_queue 推 `__EXIT_SENTINEL__` + 设 `should_exit` | 单个 agent |
| `max_rounds` | Runtime（AgentRuntime.run） | 轮次计数器超限 → `should_exit = True` | 安全网 |
| 静默检测 | Kernel 监控协程 | 所有 agent 都在 WAITING_INPUT → 向全体推送 `__EXIT_SENTINEL__` | Mode B 结束机制 |
| 级联终止 | Kernel（agent FINISHED 时） | agent FINISHED → 向显式订阅了该 agent 的其他 agent 推送 `__EXIT_SENTINEL__` | 有 subscribe 关系的 agent |
| `/exit` 命令 | 用户 (SystemConsole) | Kernel 向全体推送 `__EXIT_SENTINEL__` | 交互式退出 |
| SIGINT (Ctrl+C) | OS 信号 | Runtime 信号处理器 → 两阶段优雅关闭 | 全局 |
| 未捕获异常 | asyncio Task | `AgentRuntime.run()` finally 执行 → `runtime.error` 记录 → `_on_agent_finished` 通知父 | 单个 agent |

### 3.4 run() 协程伪代码

```
async def run():
    self.state = INIT
    self.started_at = time.time()

    try:
        await self._orchestrator._phase_init()
        # _phase_init 内部 adapter.receive() 等待首条 UserRequest

        self.state = RUNNING
        self.round_count = 0

        while not self.should_exit and self.round_count < self.max_rounds:
            await self._orchestrator._phase_loop()
            # _phase_loop 返回意味着本轮结束（StopEvent 已发送）

            self.round_count += 1

            # oneshot 模式：一轮完成即退出
            if self.mode == "oneshot":
                self.should_exit = True
                break

            # 下一轮输入（可能在此收到 __EXIT_SENTINEL__）
            if self.should_exit:
                break
            request = await self.adapter.receive()
            if request.metadata.get("exit"):
                self.should_exit = True

    except Exception as e:
        self.error = f"{type(e).__name__}: {e}"

    finally:
        self.state = TERMINATING
        try:
            await asyncio.shield(self._orchestrator._phase_end())
        except asyncio.CancelledError:
            pass  # SIGINT 强制退出
        except Exception as e:
            if not self.error:
                self.error = f"_phase_end failed: {e}"

        self.state = FINISHED
        self.last_output = self._extract_last_output()
        self._finished.set()
```

---

## 四、Kernel

全局单例。**做机制，不做策略**——不编排 workflow、不决定 agent 行为。

### 4.1 数据结构

```
runtime_table:   dict[pid, AgentRuntime]    # 进程表
input_queues:    dict[pid, asyncio.Queue]   # 每个 agent 的输入队列
message_bus:     MessageBus                 # pub-sub 路由表
_tasks:          dict[pid, asyncio.Task]    # 每个 agent 的协程引用
workflow_table:  dict[flag, list[pid]]      # workflow flag → agent 列表
_spawn_counter:  int                        # spawn 计数，用于生成 workflow_flag
_console:        SystemConsole              # 系统控制台引用
_shutdown:       bool                       # 全局退出标志
```

### 4.2 公开方法

```
spawn_root(harness) → pid
  创建 Mode A 根 agent（强制 continuous，pid="root"）
  返回 "root"

spawn_from_script(script_path, parent=None) → {workflow_flag, agents: [pid]}
  加载 workflow 脚本，遍历 @agent 注册项创建 AgentRuntime
  返回 workflow_flag 和 pid 列表
  parent 为 None 时（Mode B），子 agent 的 parent = None

send_input(pid, UserRequest)
  框架内部 API：直接写入目标 agent 的 input_queue
  用于 entry_prompt 注入、child_finished 通知、peer_terminated 通知

kill(pid)
  设置 agent.should_exit = True，推送 __EXIT_SENTINEL__ 到 input_queues[pid]

end_workflow(flag)
  对 workflow_table[flag] 中所有非 FINISHED agent 调用 kill()
  workflow_table[flag] 条目在调用后保留，供后续查询

finish_agent(pid)
  等同于 kill(pid)，语义上更明确是 agent "自己完成"而非"被杀死"

list_agents() → dict[pid, AgentInfo]
  返回 runtime_table 的只读快照

all_finished() → bool
  所有 runtime_table 中的 agent 是否均为 FINISHED
```

### 4.3 spawn_from_script 流程

```
1. self._spawn_counter += 1
   workflow_flag = f"wf_{self._spawn_counter:03d}"

2. 清空全局 registry，强制加载脚本：
   _agent_registry.clear()
   _subscription_registry.clear()
   spec = importlib.util.spec_from_file_location("_workflow_script", script_path)
   module = importlib.util.module_from_spec(spec)
   sys.modules["_workflow_script"] = module
   spec.loader.exec_module(module)
   # 模块中 @agent 和 subscribe() 调用填充了 _agent_registry 和 _subscription_registry

3. 对 _agent_registry 中的每项创建 AgentRuntime：
   for name, blueprint in _agent_registry.items():
       harness = blueprint["factory"]()
       runtime = AgentRuntime(
           pid=name,
           harness=harness,
           parent=parent,
           kernel=self,
           mode=_resolve_mode(name, has_subscriptions),
       )
       runtime.adapter = KernelBridgeAdapter(pid=name, kernel=self, runtime=runtime)
       self.runtime_table[name] = runtime
       self.input_queues[name] = asyncio.Queue()
       if parent:
           parent.children.append(name)

4. 注册订阅关系：
   for sub in _subscription_registry:
       self.message_bus.subscribe(subscriber=sub.subscriber, publisher=sub.publisher)

5. 推送 SystemConsole 事件：
   for name in _agent_registry:
       self._console.send(AgentSpawned(pid=name, parent=parent.pid if parent else None))

6. 启动协程：
   for name, runtime in self.runtime_table.items():
       if runtime.state == CREATED:
           task = asyncio.create_task(runtime.run())
           self._tasks[name] = task
           task.add_done_callback(lambda t, r=runtime: asyncio.create_task(self._on_agent_finished(r)))

7. 记录 workflow 映射：
   self.workflow_table[workflow_flag] = list(_agent_registry.keys())

8. 投递 entry_prompt：
   for name, blueprint in _agent_registry.items():
       self.send_input(name,
           UserRequest(text=blueprint["entry_prompt"],
                       metadata={"workflow_flag": workflow_flag}))

9. 返回：
   return {
       "workflow_flag": workflow_flag,
       "agents": [{"pid": n, "parent": parent.pid if parent else None} for n in _agent_registry]
   }
```

**回滚**：步骤 3-6 中任意步骤失败 → 对已创建的 AgentRuntime 调用 kill() → 异常透传给 tool caller。步骤 2 中加载失败 → 异常直接透传。

### 4.4 静默检测

Mode B 下 entry agent 之间通过 subscribe 通信。当所有 agent 都完成工作、进入 WAITING_INPUT 且无人会再产生输出时，Runtime 需要检测到这一"静默"状态并终止所有 agent。

```
# Kernel 内部，run() 中启动的监控协程
async def _monitor_quiescence(self):
    while True:
        await asyncio.sleep(1)  # 每秒检查一次

        non_finished = [r for r in self.runtime_table.values()
                        if r.state != AgentState.FINISHED]

        if not non_finished:
            return

        # 所有非 FINISHED agent 都在 WAITING_INPUT 子状态？
        all_idle = all(r._idle_for_quiescence() for r in non_finished)
        if all_idle:
            for r in non_finished:
                r.should_exit = True
                self.input_queues[r.pid].put_nowait(__EXIT_SENTINEL__)
            return
```

`AgentRuntime._idle_for_quiescence()` 在 RUNNING 状态且不在 LLM 调用或 tool 执行中时返回 True。检测到静默后，所有 agent 被推送 `__EXIT_SENTINEL__`，从 WAITING_INPUT 中被唤醒，进入 TERMINATING。

### 4.5 级联终止

agent FINISHED 时，通知所有显式订阅了它的其他 agent：

```
async def _on_agent_finished(self, runtime):
    duration = time.time() - runtime.started_at

    # 1. 推送 SystemConsole
    await self._console.send(AgentFinished(
        pid=runtime.pid,
        result=runtime.last_output,
        duration=duration,
        error=runtime.error,
    ))

    # 2. 默认订阅：通知父 agent
    if runtime.parent and runtime.parent.state != AgentState.FINISHED:
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

    # 3. 级联：通知显式订阅了该 agent 的其他 agent
    subscribers = self.message_bus.get_subscribers_of(runtime.pid)
    for sub_pid in subscribers:
        sub_runtime = self.runtime_table.get(sub_pid)
        if sub_runtime and sub_runtime.state not in (AgentState.FINISHED, AgentState.TERMINATING):
            sub_runtime.should_exit = True
            self.input_queues[sub_pid].put_nowait(__EXIT_SENTINEL__)
```

级联只影响显式 `subscribe` 的 agent。父 agent 通过默认订阅收到 `child_finished` 消息而非 sentinel，因此不受级联影响。

### 4.6 系统输入处理循环

```
async def _handle_system_input(self):
    while not self._shutdown:
        command = await self._console.receive()

        if isinstance(command, CommandTalk):
            self.send_input(command.pid, UserRequest(text=command.text))

        elif isinstance(command, CommandListAgents):
            info = self.list_agents()
            await self._console.send(AgentsListed(agents=info))

        elif isinstance(command, CommandKill):
            if command.pid in self.runtime_table:
                self.kill(command.pid)
                await self._console.send(AgentStateChanged(
                    pid=command.pid, new=AgentState.TERMINATING))
            else:
                await self._console.send(CommandError(
                    command="/kill", error=f"pid '{command.pid}' 不存在"))

        elif isinstance(command, CommandEndWorkflow):
            if command.flag in self.workflow_table:
                self.end_workflow(command.flag)
            else:
                await self._console.send(CommandError(
                    command="/end", error=f"flag '{command.flag}' 不存在"))

        elif isinstance(command, CommandExit):
            for pid, agent in self.runtime_table.items():
                if agent.state != AgentState.FINISHED:
                    agent.should_exit = True
                    self.input_queues[pid].put_nowait(__EXIT_SENTINEL__)
            self._shutdown = True
            break

        elif isinstance(command, CommandTalkDirect):
            # Mode B: /talk <pid> <text>
            if command.pid in self.runtime_table:
                self.send_input(command.pid, UserRequest(text=command.text))
            else:
                await self._console.send(CommandError(
                    command="/talk", error=f"pid '{command.pid}' 不存在"))
```

---

## 五、通信模型

### 5.1 三层通信级别

```
┌───────────────────────────────────────────────────────────┐
│ 级别 1: 默认订阅 — 父 spawn 子，自动收子 FINISHED 的最终输出   │
│         spawn 时 Kernel 自动建立，无需声明 subscribe           │
├───────────────────────────────────────────────────────────┤
│ 级别 2: 流式订阅 — 脚本中显式声明 subscribe(A).to(B)         │
│         A 收到 B 每轮结束后的 TextEvent                        │
├───────────────────────────────────────────────────────────┤
│ 级别 3: 定向投递 — adapter.send(event, target=pid)           │
│         精确一对一，忽略订阅关系，用于 talk_to tool           │
└───────────────────────────────────────────────────────────┘
```

### 5.2 默认订阅（child_finished）

父 agent 通过 `spawn_workflow` 创建子 agent 后，Kernel 自动建立默认订阅——父不需要声明 `subscribe`。

子 agent FINISHED → Kernel._on_agent_finished → 向父的 input_queue 投递 UserRequest：

```python
UserRequest(
    text=f"[{child_pid}] {'异常退出' if error else '已完成'}。\n{last_output}",
    metadata={
        "type": "child_finished",
        "pid": child_pid,
        "workflow_flag": workflow_flag,
        "duration": execution_time,
        "error": error,   # None | str
    }
)
```

父 agent 的下一轮 `adapter.receive()` 自然收到。LLM 看到 `metadata.type = "child_finished"` 即知这是子 agent 完成通知，`metadata.pid` 和 `metadata.workflow_flag` 用于区分是哪个子 agent、哪个 workflow。

### 5.3 流式订阅

Workflow 脚本中声明：

```python
subscribe("analyzer").to("collector")
```

含义：analyzer 订阅 collector 的每轮输出。

collector 调用 `adapter.send(TextEvent(...))` 时：
1. `KernelBridgeAdapter.send()` → `MessageBus.publish(from="collector", event=TextEvent)`
2. MessageBus 查订阅表 → analyzer 订阅了 collector
3. 消息投递到 `input_queues["analyzer"]`
4. analyzer 从 `adapter.receive()` 收到 UserRequest(text=..., metadata={from: "collector"})

collector 调用 `adapter.send(StopEvent(...))` 时同样投递，但 StopEvent 内容不在 UserRequest.text 中体现（metadata 中标记 `stop: true`）。

每轮 StopEvent 触发后，本轮最后一条 TextEvent 推给订阅者。

**中间输出（ToolCallEvent、ToolResultEvent）不推送给订阅者**。订阅者只看到每轮最终产出的 TextEvent。

### 5.4 定向投递

所有消息通过同一 `InputAdapter.send()` 接口，`target` 参数区分路由模式：

```python
# 普通输出：走订阅路由
adapter.send(TextEvent("分析完成"))  # target=None，广播给订阅者

# 定向投递：忽略订阅，直接投到目标 agent（talk_to tool 内部使用）
adapter.send(TextEvent("请重新分析"), target="analyzer")
```

两种模式共用同一条链路：`adapter.send()` → `KernelBridgeAdapter` → `MessageBus`。
- `target=None`：MessageBus 按订阅表广播
- `target=pid`：MessageBus 忽略订阅表，直接投递到 `input_queues[pid]`

### 5.5 无订阅者的降级

TextEvent 通过 `adapter.send(target=None)` 发送时，若 MessageBus 中无订阅者，降级为 `AgentOutput` SystemEvent 推给 SystemConsole：

```
adapter.send(TextEvent("结果..."))
→ MessageBus.publish(): 无订阅者
→ console.send(AgentOutput(pid=..., content="结果..."))
```

StopEvent 无订阅者时直接丢弃。

### 5.6 通信退出保护

`KernelBridgeAdapter.send()` 在发消息前检查 agent 的 `should_exit`：如果 agent 正在退出（should_exit 为 True），消息被静默丢弃。这防止 agent 在 LLM 返回后产生的"最后一轮输出"污染订阅者。

---

## 六、Workflow 脚本

### 6.1 脚本格式

子 agent 的装配完全复用现有 `DIContainer` + `Harness.from_container()` 模式。每个 agent 有独立的 DI 容器。

```python
# workflow.py — 由 LLM (Write 工具) 生成

from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import (
    InputAdapter, MemoryBackend, Sensor, ContextAssembler,
    SystemToolProvider,
)
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider

from harness.runtime import agent, subscribe


# ── agent 装配 ──
# @agent 将注册信息填入全局 _agent_registry
# Kernel 在 spawn_from_script 时调用这些工厂函数获取 Harness 实例

@agent("collector", entry_prompt="采集代码库中所有 .py 文件，统计行数和依赖")
def assemble_collector():
    container = DIContainer()
    memory = MdMemory(path="./memory/collector")
    # 不需要注册 InputAdapter — AgentRuntime 会挂载 KernelBridgeAdapter
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, SimpleAssembler(max_history=50))
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent("analyzer", entry_prompt="分析 collector 采集的数据，找出代码质量问题",
       metadata={"description": "代码质量分析器"})
def assemble_analyzer():
    container = DIContainer()
    memory = MdMemory(path="./memory/analyzer")
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, SimpleAssembler(max_history=100))
    container.register(Sensor, LoggingSensor(memory=memory))
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


# ── 拓扑声明 ──
subscribe("analyzer").to("collector")
```

### 6.2 @agent 装饰器参数

```python
@agent(
    name: str,                    # pid，在本次 spawn 内唯一
    entry_prompt: str,            # 必需：agent 的第一条消息。Kernel spawn 时作为 UserRequest.text 投递
    metadata: dict | None = None  # 可选：透传给父 agent 的 LLM，出现在 spawn_workflow 返回值的 agents[].metadata 中
)
```

- `entry_prompt`：子 agent 自己 LLM 看到的第一条消息，决定子 agent 的初始行为方向
- `metadata`：父 agent 的 LLM 在 `spawn_workflow` 返回值中看到的附加信息，帮助父 agent 理解子 agent 能力

### 6.3 subscribe 声明

```python
subscribe(subscriber: str).to(publisher: str)
```

在模块顶层声明。Kernel 在 `spawn_from_script` 时读取 `_subscription_registry` 并注册到 MessageBus。

声明了 `subscribe` 的 agent 的 mode 自动设为 `continuous`（需要持续通信）。未声明 `subscribe` 的 agent 为 `oneshot`。

### 6.4 Registry 隔离

`@agent` 和 `subscribe` 向模块级全局变量 `_agent_registry` 和 `_subscription_registry` 写入。

Kernel.spawn_from_script 在加载脚本前清空这两个 registry，并用 `importlib.util.spec_from_file_location` 以固定模块名 `"_workflow_script"` 加载，每次覆盖 `sys.modules` 中的同名条目。这确保：

- 每次 spawn 获得干净的 registry
- 多次 spawn 同一脚本不会累积注册项
- 不同脚本的 spawn 互不污染

---

## 七、SystemConsole

与 `InputAdapter` 完全独立——不共用、不继承。

### 7.1 接口定义

```python
class SystemConsole(Protocol):
    """Runtime 系统级交互接口。

    和 InputAdapter 的区别：
    - InputAdapter：一个 agent 的 stdin/stdout
    - SystemConsole：整个 Runtime 的"控制台"，处理系统命令和事件
    """

    async def receive(self) -> SystemCommand:
        """接收用户输入，返回解析后的系统命令。"""
        ...

    async def send(self, event: SystemEvent) -> None:
        """推送系统级事件。"""
        ...
```

### 7.2 SystemCommand 类型

| 命令 | 触发条件 | Kernel 行为 |
|------|---------|------------|
| `CommandTalk(pid, text)` | 纯文本输入（无 `/` 前缀） | 调用 `send_input(pid, UserRequest(text))` |
| `CommandKill(pid)` | `/kill <pid>` | `kill(pid)` → 推送 `AgentStateChanged` 事件 |
| `CommandListAgents()` | `/agents` | `list_agents()` → 推送 `AgentsListed` 事件 |
| `CommandEndWorkflow(flag)` | `/end <flag>` | `end_workflow(flag)` →
 推送 `AgentStateChanged` 事件 |
| `CommandExit()` | `/exit` | 向全体推送 `__EXIT_SENTINEL__`，设 `_shutdown = True` |
| `CommandTalkDirect(pid, text)` | `/talk <pid> <text>` (Mode B) | `send_input(pid, UserRequest(text))` |

### 7.3 SystemEvent 类型

| 事件 | 触发时机 | 类型 |
|------|---------|------|
| `AgentSpawned(pid, parent)` | spawn 完成 | 通知 |
| `AgentStateChanged(pid, old, new)` | 状态转移 | 通知 |
| `AgentFinished(pid, result, duration, error)` | agent 进入 FINISHED | 通知 |
| `AgentOutput(pid, content)` | agent 的 TextEvent 无订阅者 | 降级通知 |
| `AgentsListed(agents)` | `/agents` 响应 | 查询响应 |
| `CommandError(command, error)` | 系统命令执行失败 | 查询响应 |
| `RuntimeStarted()` | 事件循环启动 | 生命周期 |
| `RuntimeStopped()` | 所有 agent FINISHED | 生命周期 |
| `WorkflowFinished(results)` | Mode B: 所有 agent FINISHED，run_from_script 返回前 | 汇总 |

### 7.4 CliConsole 默认实现

**`receive()`**：使用 `asyncio.to_thread(sys.stdin.readline)` 将同步阻塞转为异步。解析规则：

```
如果输入以 "/" 开头 → 解析为系统命令:
  /agents          → CommandListAgents()
  /kill <pid>      → CommandKill(pid)
  /end <flag>      → CommandEndWorkflow(flag)
  /exit            → CommandExit()
  /talk <pid> <t>  → CommandTalkDirect(pid, text)

否则 → CommandTalk(pid="root", text=input)   # Mode A，路由到根 agent
```

Mode B 下纯文本无目标时，推送 `CommandError(command="<text>", error="纯文本需 /talk <pid> 指定目标")` 给 `send()`。

**`send()`**：根据事件类型格式化输出到 stdout：

```
AgentSpawned     → "[系统] Agent spawned: {pid}"
AgentFinished    → "[系统] Agent finished: {pid} ({duration:.1f}s)"
AgentOutput      → "[{pid}] {content}"
AgentsListed     → "[系统] Agents:\n  {pid:12} {state:12} {uptime}"
CommandError     → "[系统] 错误: {error}"
WorkflowFinished → 格式化每个 agent 的最终结果
```

---

## 八、LLM 上下文模型

核心原则：**LLM 只通过 InputAdapter 接口看世界**。`adapter.receive()` 进，`adapter.send()` 出。框架内部的复杂协作（Kernel 调度、MessageBus 路由、AgentRuntime 状态机）对 LLM 完全不可见。

### 8.1 LLM 可感知的信息来源

| 来源 | 注入方式 | LLM 看到的 |
|------|---------|-----------|
| 用户输入 | SystemConsole → Kernel → input_queue → `adapter.receive()` | `UserRequest.text` |
| entry_prompt | Kernel.spawn → `send_input` → `adapter.receive()` | `UserRequest(text=entry_prompt, metadata={workflow_flag})` |
| spawn_workflow 返回值 | ToolRouter → `ToolResult.content` → 追加到 messages | `{"workflow_flag": "wf_001", "agents": [{pid, parent, metadata}]}` |
| child_finished（默认订阅） | `_on_agent_finished` → `send_input(parent)` → `adapter.receive()` | `UserRequest(text="[pid] 完成...", metadata={type:"child_finished", pid, workflow_flag, error})` |
| 流式订阅输出 | MessageBus → input_queue[subscriber] → `adapter.receive()` | `UserRequest(text=..., metadata={from: publisher_pid})` |
| talk_to 定向消息 | `adapter.send(target=pid)` → input_queue[target] → `adapter.receive()` | `UserRequest(text=..., metadata={from: sender_pid})` |
| 退出信号 | `__EXIT_SENTINEL__` → `adapter.receive()` | `UserRequest(text="", metadata={exit: True})` |
| peer_terminated | 级联终止 → `send_input(subscriber)` → `adapter.receive()` | `UserRequest(text="", metadata={type:"peer_terminated", pid})` |

### 8.2 LLM 不可见的内容

| 不可见内容 | 去向 |
|-----------|------|
| 其他 agent 的 ToolCallEvent / ToolResultEvent | 仅该 agent 自身 + 其订阅者（如有） |
| AgentSpawned / AgentFinished / AgentStateChanged 系统事件 | SystemConsole 显示，不进入任何 input_queue |
| AgentState 状态变更 | Kernel 内部 |
| 子 agent 的完整 Trajectory | MemoryBackend，跨会话持久化；父 agent 不可见 |
| SystemConsole 上的 `/agents` `/kill` `/end` 等命令 | Kernel 直接处理，不路由到 agent |

### 8.3 LLM 可调用的 Runtime 管理 Tool

这些 Tool 注册在默认 SystemToolProvider 中，agent 通过正常的 tool-calling 流程调用：

| Tool | 参数 | 返回值（注入 LLM 上下文） | 副作用 |
|------|------|------------------------|--------|
| `spawn_workflow` | `script_path: str` | `{workflow_flag, agents: [{pid, parent, metadata}]}` | Kernel 加载脚本、创建 AgentRuntime、启动 asyncio Task |
| `end_workflow` | `flag: str` | `{ok: true, killed: [pid, ...]}` | 向 workflow 所有非 FINISHED agent 推送 `__EXIT_SENTINEL__` |
| `finish_agent` | 无 | `{ok: true}` | 向调用者自己的 input_queue 推送 `__EXIT_SENTINEL__`，设 `should_exit = True` |
| `talk_to` | `pid: str, text: str` | `{ok: true, target: pid}` | `adapter.send(TextEvent(text), target=pid)` → 目标 input_queue |
| `list_agents` | 无 | `{agents: [{pid, state, parent, mode}]}` | 读取 `kernel.runtime_table` 快照 |

**关键设计**：`spawn_workflow` 的返回值是 LLM 后续调用 `end_workflow`、`talk_to` 的**唯一依据**。LLM 必须从 tool 返回值中记住 `workflow_flag` 和 `pid`，框架不会在其他地方重复告知这些标识符。

### 8.4 典型交互流程：LLM 编排 Workflow

```
[LLM 上下文中的对话]

User: 帮我分析这个代码库的质量

LLM: 好的，我先创建采集和分析 agent 来并行处理。
     [调用 spawn_workflow {script_path: "workflow.py"}]

Tool 返回: {
  "workflow_flag": "wf_001",
  "agents": [
    {"pid": "collector", "parent": "root", "metadata": null},
    {"pid": "analyzer", "parent": "root", "metadata": {"description": "代码质量分析器"}}
  ]
}
← LLM 上下文中有 workflow_flag 和两个 pid

LLM: 已创建 collector 和 analyzer，正在运行中...

← 一段时间后，adapter.receive() 返回:

UserRequest {
  text: "[collector] 已完成。\n采集到 28 个文件，总计 4500 行",
  metadata: {type: "child_finished", pid: "collector", workflow_flag: "wf_001", duration: 12.5, error: null}
}

← LLM 看到 collector 完成了

UserRequest {
  text: "[analyzer] 已完成。\n发现 3 个问题: 1) 重复代码 2) 内存泄漏 3) SQL注入风险",
  metadata: {type: "child_finished", pid: "analyzer", workflow_flag: "wf_001", duration: 8.2, error: null}
}

LLM: 分析完成！发现以下问题：
     1) 重复代码 - ...
     2) 内存泄漏 - ...
     3) SQL注入风险 - ...

     [调用 end_workflow {flag: "wf_001"}]

Tool 返回: {ok: true, killed: ["collector", "analyzer"]}
```

---

## 九、两种启动模式

### 9.1 模式 A：交互式

```python
# main.py
console = CliConsole()
root_harness = Harness.from_container(container, call_llm=async_llm)
Runtime(console).run(root_harness)
```

`Runtime.run(root_harness)` 内部：

```
1. 创建 Kernel(console)
2. kernel.spawn_root(root_harness)
   → AgentRuntime(pid="root", mode="continuous", parent=None, ...)
   → adapter = KernelBridgeAdapter(pid="root", kernel=kernel, runtime=runtime)
   → runtime_table["root"] = runtime, input_queues["root"] = asyncio.Queue()
   → console.send(AgentSpawned(pid="root", parent=None))
3. task_root = asyncio.create_task(root_runtime.run())
4. task_sys  = asyncio.create_task(kernel._handle_system_input())
5. task_mon  = asyncio.create_task(kernel._monitor_quiescence())
6. await asyncio.gather(task_root, task_sys, task_mon, return_exceptions=True)
7. console.send(RuntimeStopped())
```

进程树：

```
user (逻辑根，parent = None)
  └── root agent (pid="root", mode=continuous, parent=None)
         ├── collector (pid="collector", mode=oneshot, parent=root)
         └── analyzer (pid="analyzer", mode=continuous, parent=root)
```

SystemConsole 行为：
- 纯文本无 `/` 前缀 → `CommandTalk(pid="root", text)` → 路由到 root 的 input_queue
- `/` 前缀命令 → Kernel 直接处理，不经过 agent
- SystemEvent → 格式化输出到 stdout

### 9.2 模式 B：直接启动 Workflow

```python
# main.py
console = CliConsole()
Runtime(console).run_from_script("workflow.py")
```

`Runtime.run_from_script(script_path)` 内部：

```
1. 创建 Kernel(console)
2. result = kernel.spawn_from_script(script_path, parent=None)
   → 返回 {workflow_flag, agents: [{pid}]}
   → 所有 entry agent 的 mode：有 subscribe 声明的为 continuous，否则为 oneshot
3. task_sys = asyncio.create_task(kernel._handle_system_input())
4. task_mon = asyncio.create_task(kernel._monitor_quiescence())
5. await asyncio.gather(*kernel._tasks.values(), task_sys, task_mon, return_exceptions=True)
6. # 收集最终输出
   results = {pid: {"output": r.last_output, "error": r.error, "rounds": r.round_count}
              for pid, r in kernel.runtime_table.items()}
7. console.send(WorkflowFinished(results=results))
```

进程树：

```
user (逻辑根)
  ├── collector (pid="collector", mode=oneshot, parent=None)
  └── analyzer (pid="analyzer", mode=continuous, parent=None)
```

SystemConsole 行为：
- 纯文本无 `/` 前缀 → 无 root agent 可路由，推送 `CommandError` 提示使用 `/talk`
- `/talk <pid> <text>` → `CommandTalkDirect(pid, text)` → `send_input(pid, ...)`
- `/agents` `/kill <pid>` `/end <flag>` `/exit` → 同模式 A
- agent 的 TextEvent 无订阅者 → 降级为 `AgentOutput` SystemEvent → stdout 显示

**模式 B 的结束机制**：不是依赖 max_rounds，而是静默检测。当所有 agent 完成工作进入 WAITING_INPUT，监控协程检测到静默，推送 `__EXIT_SENTINEL__` 给所有 agent，agent 依次进入 TERMINATING → FINISHED。

对于简单的 oneshot agent（如 collector），它一轮完成后自动进入 TERMINATING，不需要静默检测参与。静默检测主要覆盖 continuous agent 通过 subscribe 通信完成后的终止。

---

## 十、异常与信号处理

### 10.1 Agent 崩溃

子 agent 在执行中抛出未捕获异常时：

1. 异常传播到 `AgentRuntime.run()` → 被 `except Exception` 捕获
2. `self.error` 记录异常信息：`f"{type(e).__name__}: {e}"`
3. `finally` 块执行：`_phase_end()`（用 `asyncio.shield` 保护，防止被 cancel）
4. `state = FINISHED`，`_finished` Event 设置
5. `Task.done_callback` 触发 → `asyncio.create_task(kernel._on_agent_finished(runtime))`
6. `_on_agent_finished` 构造 `child_finished` UserRequest，`metadata.error` 携带 `runtime.error`
7. 父 agent 的 `adapter.receive()` 返回该消息
8. 父 agent 的 LLM 看到 `metadata.error` 非空 → 决定重试（调 `spawn_workflow`）或向用户汇报

**注意**：`_on_agent_finished` 通过 `asyncio.create_task` 异步执行，确保它在 `_phase_end()` 完成、`last_output` 赋值之后再运行。

### 10.2 _phase_end 的异常安全

`_phase_end()` 自身可能因 I/O 错误（磁盘满）或 MemoryBackend 故障而失败：

```
finally:
    try:
        await asyncio.shield(self._orchestrator._phase_end())
    except asyncio.CancelledError:
        pass  # 强制退出场景
    except Exception as e:
        if not self.error:
            self.error = f"_phase_end failed: {e}"
    # 无论如何，标记 FINISHED
    self.state = FINISHED
```

不因清理失败而阻止 agent 进入 FINISHED——否则 Kernel 的 `all_finished()` 永远为 False。

### 10.3 SIGINT (Ctrl+C)

`Runtime.run()` 注册信号处理器，两阶段退出：

```
def _on_sigint():
    if not self._sigint_count:
        # 第一阶段：优雅退出
        self._sigint_count = 1
        for pid, agent in kernel.runtime_table.items():
            if agent.state != AgentState.FINISHED:
                agent.should_exit = True
                kernel.input_queues[pid].put_nowait(__EXIT_SENTINEL__)
    else:
        # 第二阶段：强制终止
        for task in kernel._tasks.values():
            if not task.done():
                task.cancel()

# Runtime.run() 中:
loop = asyncio.get_running_loop()
loop.add_signal_handler(signal.SIGINT, _on_sigint)
try:
    await asyncio.gather(...)
finally:
    loop.remove_signal_handler(signal.SIGINT)
```

行为：
- 第一次 Ctrl+C → 推送 `__EXIT_SENTINEL__`，等待 agent 走完 TERMINATING → FINISHED。如果在 `await call_llm()` 中，最多等一个 LLM 调用时间。
- 第二次 Ctrl+C → `task.cancel()` 强制取消所有协程。AgentRuntime.run() 中 `asyncio.shield(_phase_end())` 被 `CancelledError` 穿透，catch 后跳过清理直接 FINISHED。

退出码：
- 自然结束（包括第一次 Ctrl+C 优雅结束） → 0
- 第二次 Ctrl+C 强制结束 → 1

### 10.4 Runtime.run() 异常策略

`asyncio.gather` 使用 `return_exceptions=True`，单个 agent 异常不影响其他 agent。

---

## 十一、AsyncLifecycleOrchestrator（新增）

**不改动现有 `LifecycleOrchestrator`**。现有同步代码、测试、CLI 入口全部保留不动。

新增 `AsyncLifecycleOrchestrator`，整体结构参照原 `LifecycleOrchestrator` 的三阶段编排逻辑迁移为 async。两者并存，分别服务于同步路径（原 CLI）和 Runtime 路径（新）。

### 12.1 迁移要点

从原 `LifecycleOrchestrator` 迁移时，以下部分改为 async：

| 原同步调用 | async 化 |
|-----------|---------|
| `call_llm(...)` | `await call_llm(...)` |
| `self.container.resolve(InputAdapter).receive()` | `await self._adapter.receive()` |
| `adapter.send(event)` | `await self._adapter.send(event)` |

其余逻辑——上下文组装（ContextAssembler）、tool 执行分发（ToolRouter）、Hook 触发（HookManager）、事件推送顺序、`_should_exit()` 检测——原样保留。Hook 保持同步调用，不引入异步 Hook。

### 12.2 构造函数

```python
class AsyncLifecycleOrchestrator:
    def __init__(self, container, call_llm, adapter):
        self._container = container
        self._call_llm = call_llm          # async callable
        self._adapter = adapter            # AsyncInputAdapter (KernelBridgeAdapter)
        self._history = []
        self._context_assembler = container.resolve(ContextAssembler)
        self._tool_router = container.resolve(ToolRouter)
        self._hook_manager = container.resolve(HookManager)
        self._sensor = container.resolve(Sensor)
```

与原有 `LifecycleOrchestrator` 的区别：不通过 `container.resolve(InputAdapter)` 获取 adapter，而是在构造时显式传入 `adapter` 参数（AgentRuntime 传入 KernelBridgeAdapter）。`call_llm` 在构造时即为 async callable，不做运行时的 `iscoroutinefunction` 检测。

### 12.3 call_llm 兼容桥接

如果用户的 LLM adapter 是同步的，Runtime 入口处做一次性包装，不侵入 `AsyncLifecycleOrchestrator` 内部：

```python
# Runtime.run() 或 run_from_script() 中:
if not asyncio.iscoroutinefunction(user_call_llm):
    original = user_call_llm
    async def _async_wrapper(*args, **kwargs):
        return await asyncio.to_thread(original, *args, **kwargs)
    call_llm = _async_wrapper
else:
    call_llm = user_call_llm
```

`AsyncLifecycleOrchestrator` 只接收 `async callable`，不做兼容判断。

### 12.4 should_exit 检测

沿用原 `_should_exit()` 的检测逻辑（检查 `metadata["exit"] == True`）：
- `__EXIT_SENTINEL__` → `adapter.receive()` 返回 `UserRequest(text="", metadata={"exit": True})`
- `_should_exit()` 检测到 → while 循环 break
- Orchestrator 自己退出，不需要 AgentRuntime 的反向引用

---

## 十二、AsyncInputAdapter Protocol（新增）

**不改动现有 `InputAdapter` Protocol**。新增 `AsyncInputAdapter` Protocol，两者并存：

```python
# 现有同步接口 — 不动
class InputAdapter(Protocol):
    def receive(self) -> UserRequest: ...
    def send(self, event: Event) -> None: ...

# 新增异步接口 — Runtime 路径使用
class AsyncInputAdapter(Protocol):
    async def receive(self) -> UserRequest: ...
    async def send(self, event: Event, target: str | None = None) -> None: ...
```

`AsyncInputAdapter` 与 `InputAdapter` 的区别：
- `receive()` / `send()` 均为 `async def`，兼容 asyncio 队列和 MessageBus 异步投递
- `send()` 新增 `target` 参数：`None` 走 pub-sub 广播，指定 `pid` 走定向投递

### 13.1 KernelBridgeAdapter

`KernelBridgeAdapter` 实现 `AsyncInputAdapter`，是 Runtime 路径下所有 agent 的 I/O 通道：

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
            return UserRequest(text=item.content, metadata={**item.metadata, "from": item.from_pid})
        elif isinstance(item, UserRequest):
            return item

    async def send(self, event: Event, target=None) -> None:
        if self._runtime.should_exit:
            return   # 退出保护：丢弃"最后一轮污染"
        if target is not None:
            self._kernel.message_bus.direct(target, InternalMessage(
                from_pid=self._pid, content=event.content, metadata={}))
        else:
            self._kernel.message_bus.publish(
                from_pid=self._pid, event=event,
                on_no_subscriber=(
                    self._kernel._console.send
                    if isinstance(event, TextEvent) else None
                )
            )
```

**input_queue 消息格式转换**：

| 入队类型 | receive() 转换 |
|---------|---------------|
| `UserRequest` | 原样返回 |
| `InternalMessage(from_pid, content, metadata)` | `UserRequest(text=content, metadata={..., from: from_pid})` |
| `__EXIT_SENTINEL__` | `UserRequest(text="", metadata={"exit": True})` |

---

## 十三、完整生命周期推演

### 14.1 用例：Mode A 交互对话 + spawn workflow + 用户 /exit

**阶段 0 — 启动**

```
main.py:
  console = CliConsole()
  root_harness = Harness.from_container(...)
  Runtime(console).run(root_harness)

Runtime.run() 内部:
  1. Kernel(console)
  2. kernel.spawn_root(root_harness)
     → AgentRuntime(pid="root", mode="continuous", ...)
     → KernelBridgeAdapter(pid="root", kernel, runtime)
     → console.send(AgentSpawned("root", parent=None))
  3. asyncio.create_task(root_runtime.run())
     → state=INIT → _phase_init() → adapter.receive() → input_queues["root"].get() [阻塞]
  4. asyncio.create_task(kernel._handle_system_input())
     → console.receive() → asyncio.to_thread(sys.stdin.readline) [阻塞]
  5. asyncio.create_task(kernel._monitor_quiescence())
     → sleep(1)... 等待
```

**阶段 1 — 用户输入 "分析代码库"**

```
_handle_system_input:
  console.receive() → "分析代码库"
  → CommandTalk(pid="root", text="分析代码库")
  → kernel.send_input("root", UserRequest(text="分析代码库"))
  → input_queues["root"].put(UserRequest)

root_runtime:
  input_queues["root"].get() → UserRequest("分析代码库")
  → _phase_init() 完成
  → state=RUNNING
  → _phase_loop():
      assemble_context() → messages 包含 UserRequest
      await call_llm(messages, tools)
```

**阶段 2 — LLM 返回，用户看到回复**

```
root _phase_loop:
  response = await call_llm(...)
  → response.text = "代码库有 50 个 .py 文件..."
  → adapter.send(TextEvent("代码库有 50 个 .py 文件..."))
      → KernelBridgeAdapter.send() → should_exit? False
      → MessageBus.publish(from="root", event=TextEvent)
      → 无订阅者 → console.send(AgentOutput(pid="root", content="..."))
      → CliConsole: "[root] 代码库有 50 个 .py 文件..."
  → adapter.send(StopEvent(...))
      → 无订阅者 → 丢弃
  → 内层 break

  # 外层 while: should_exit? False, round < max_rounds
  → adapter.receive() → input_queues["root"].get() [阻塞]
```

**阶段 3 — LLM 决定 spawn workflow**

```
用户输入 "用 workflow 分析" → 同上路径到达 root
  → call_llm → response.tool_uses = [{name: "spawn_workflow", args: {script_path: "workflow.py"}}]
  → 内层循环: execute_tool("spawn_workflow", ...)
    → Kernel.spawn_from_script("workflow.py", parent=root_runtime)
    → 加载脚本，创建 collector/analyzer 的 AgentRuntime
    → entry_prompt 投递，asyncio Task 启动
    → 返回 {workflow_flag: "wf_001", agents: [{pid:"collector",...}, {pid:"analyzer",...}]}
  → ToolResult 追加到 messages
  → 内层继续... call_llm 看到 tool result
  → response.text = "已创建 workflow" (或其他)
  → adapter.send(TextEvent("已创建 workflow..."))
  → adapter.send(StopEvent(...))
  → 内层 break

  → adapter.receive() [阻塞——等用户下一条输入或 child_finished]
```

**阶段 4 — 子 agent 并发运行**

```
collector (oneshot):
  → adapter.receive() → UserRequest(text="采集代码库中...", metadata={workflow_flag:"wf_001"})
  → _phase_loop: call_llm(...) → response
  → TextEvent("采集到 28 个文件...")
  → StopEvent
  → 外层 while: mode="oneshot" → should_exit = True → break
  → finally: _phase_end() → state=FINISHED → _finished.set()

analyzer (continuous, 声明了 subscribe):
  → adapter.receive() → entry_prompt
  → _phase_loop: call_llm → 可能输出"等待 collector 数据...", adapter.receive [阻塞]
  → collector 的 TextEvent → MessageBus → input_queues["analyzer"]
  → adapter.receive() → UserRequest(text="采集到 28 个文件...", metadata={from:"collector"})
  → call_llm → "分析完成，发现 3 个问题..."
  → TextEvent/StopEvent → MessageBus → collector 已 FINISHED, 没人订阅 → 降级到 AgentOutput
  → 下一轮 adapter.receive() [阻塞]
```

**阶段 5 — 子 agent 完成通知**

```
collector FINISHED → _on_agent_finished:
  → console.send(AgentFinished("collector", ...))
  → send_input("root", UserRequest(text="[collector] 已完成。\n采集到 28 个文件", metadata={type:"child_finished", pid:"collector", ...}))
  → collector 级联：谁 subscribe 了 collector? analyzer → 推送 __EXIT_SENTINEL__ 给 analyzer

analyzer 收到 sentinel → _should_exit → break → TERMINATING → FINISHED

analyzer FINISHED → _on_agent_finished:
  → console.send(AgentFinished("analyzer", ...))
  → send_input("root", UserRequest(text="[analyzer] 已完成。\n发现 3 个问题...", metadata={...}))
  → analyzer 级联：谁 subscribe 了 analyzer? collector 已 FINISHED → 跳过
```

**阶段 6 — root 收到 child_finished，汇报**

```
root: adapter.receive() [被 child_finished 唤醒]
  → UserRequest(text="[collector] 已完成...")
  → call_llm → LLM 看到 collector 完成
  → 可能产生 TextEvent 或继续等待 analyzer 的结果
  → (内层可能没有 text → 继续 while → adapter.receive()
     → 被 analyzer 的 child_finished 唤醒)
  → [下一轮] adapter.receive() → UserRequest(text="[analyzer] 已完成...")
  → call_llm → LLM 看到全部完成
  → TextEvent("分析完成！发现以下问题：...")
  → LLM 可能调 end_workflow("wf_001")（可选——如果还有 agent 没 FINISHED）
```

**阶段 7 — 用户 /exit**

```
_handle_system_input:
  console.receive() → "/exit" → CommandExit()
  → 全体推送 __EXIT_SENTINEL__
  → _shutdown = True

root: 如果正在 WAITING_INPUT → adapter.receive() → UserRequest(metadata={exit:True})
  → _should_exit() → _phase_loop 退出 → finally → _phase_end() → FINISHED
  → _on_agent_finished → console.send(AgentFinished("root", ...))

all_finished() → True
_monitor_quiescence 检测到全部 FINISHED → 返回
_handle_system_input 退出
asyncio.gather 返回
console.send(RuntimeStopped())
main.py 退出，exit code 0
```

### 14.2 用例：Mode B 直接启动 Workflow

```
main.py: Runtime(console).run_from_script("workflow.py")

1. Kernel.spawn_from_script("workflow.py", parent=None)
   → collector (oneshot), analyzer (continuous)
   → entry_prompts 投递

2. collector 和 analyzer 的 Task 启动
3. _handle_system_input 和 _monitor_quiescence 启动

collector:
  → entry_prompt → call_llm → TextEvent → StopEvent
  → mode=oneshot → should_exit=True → TERMINATING → FINISHED
  → _on_agent_finished: 无 parent, 级联推送 __EXIT_SENTINEL__ 给 analyzer

analyzer:
  → entry_prompt → call_llm → adapter.receive() [等 collector 消息]
  → 收到 collector TextEvent → call_llm → 分析...
  → adapter.receive() [等下一轮]
  → 收到级联的 __EXIT_SENTINEL__ → _should_exit → TERMINATING → FINISHED

_all_finished() → True

run_from_script 返回前:
  results = {"collector": {"output": "采集到 28 个文件...", "error": None, ...},
             "analyzer": {"output": "发现 3 个问题...", "error": None, ...}}
  console.send(WorkflowFinished(results=results))

main.py 退出，exit code 0
```

---

## 十四、关键设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 并发模型 | asyncio 单线程协程 | agent 90%+ 时间在等 LLM (I/O)，asyncio 足够 |
| Kernel 角色 | 全局单例，做机制不做策略 | 邮局模型：路由消息、登记 agent、分配执行；不编排 workflow |
| Agent 运行模式 | continuous / oneshot 两态 | oneshot 解决了 Mode B 和子 agent"完成即退出"的语义；continuous 覆盖交互和持续通信场景 |
| 终止机制 | 多路径互补：oneshot 自动 / end_workflow / finish_agent / 静默检测 / 级联 / max_rounds | 覆盖所有场景，无需 LLM 发明终止协议 |
| 静默检测 | Kernel 监控协程，1s 轮询 | Mode B 的核心结束机制——当所有 agent 都在 WAITING_INPUT，无人会再产生输出 |
| 级联终止 | agent FINISHED → 订阅者收 `__EXIT_SENTINEL__` | 避免 subscribe 关系中的死锁——一个退出，另一个感知并退出 |
| SIGINT | 两阶段：优雅推送 sentinel → 强制 task.cancel | 先给 agent 机会保存轨迹，二次中断强制退出 |
| Registry 隔离 | spawn 前清空全局 registry + 固定模块名覆盖 | MPI 最简单的隔离方案，无需引入线程局部或上下文变量 |
| SystemConsole | 新接口，独立于 InputAdapter | InputAdapter 为单 agent 对话设计，承载不了多 agent Runtime 交互 |
| 查询命令响应 | SystemEvent 子类型（AgentsListed、CommandError） | 所有输出统一走 `console.send()`，不引入第二条通道 |
| 子 agent InputAdapter | AgentRuntime 强制覆盖为 KernelBridgeAdapter | 避免用户错误注册 CliAdapter 导致 stdin 争抢 |
| 父看子中间输出 | MVP 不默认看到 | 父若能看到子中间输出，则和父自己干没区别；后续提供 read_log tool |
| `last_output` 取值 | `_orchestrator._history` 最后一条 assistant role 的 content；空 history 则为 "" | 取 LLM 最终可见输出作为子 agent 的"结果摘要" |
| AgentRuntime.state vs Orchestrator._system_state.phase | MVP 保留两套，AgentRuntime.state 为权威 | 不修改现有 Orchestrator 内部状态 |

---

## 十五、与现有代码的关系

| 现有组件 | 改动 | 兼容性 |
|---------|------|--------|
| Harness | 不变 | ✅ `.from_container()` / `.register_hook()` API 不变 |
| DIContainer | 不变 | ✅ register / resolve 不变 |
| LifecycleOrchestrator | 不变 | ✅ 原有同步版本不动；新增 `AsyncLifecycleOrchestrator` 在 Runtime 路径使用 |
| InputAdapter Protocol | 不变 | ✅ 原有同步版本不动；新增 `AsyncInputAdapter` Protocol，`KernelBridgeAdapter` 实现之 |
| ToolRouter | 不变 | ✅ |
| HookManager | 不变 | ✅ Hook 保持同步；如需要异步 Hook 后续支持 |
| YamlAssembler | 不变 | ✅ 装配产物仍为 Harness，Runtime 可直接消费 |
| main.py | 改动 | 🟡 入口从 `harness.run()` 改为 `Runtime(console).run(harness)` |
| 所有其他 Protocol 接口 | 不变 | ✅ |
| AgentRuntime 状态机 | 新增 | ✅ 在 LifecycleOrchestrator 之上 |
| SystemConsole | 新增接口 | ✅ 与 InputAdapter 完全独立 |
| KernelBridgeAdapter | 新增 | ✅ 实现 InputAdapter Protocol，对接 Kernel 消息队列 |

---

## 十六、实现批次

### Batch 0: Async 接口层 + AsyncLifecycleOrchestrator

**目标**：建立 async 接口层，不改动任何现有同步代码。

- 定义 `AsyncInputAdapter` Protocol（async def receive / send）
- 定义 `AsyncCallLLM` Protocol（`Callable[..., Awaitable[LLMResponse]]`）
- 新增 `AsyncLifecycleOrchestrator`：参照 `LifecycleOrchestrator` 的三阶段编排逻辑，将 `call_llm`、`adapter.receive()`、`adapter.send()` 三个阻塞点改为 `await`。其余逻辑（ContextAssembler、ToolRouter、HookManager 调用、`_should_exit()` 检测）原样保留
- `KernelBridgeAdapter` 实现 `AsyncInputAdapter`，对接 Kernel 的 input_queue 和 MessageBus
- 同步 LLM adapter → async 的 `asyncio.to_thread` 桥接（在 Runtime 入口层做，不侵入 Orchestrator）
- 测试：用 mock async call_llm 验证 `AsyncLifecycleOrchestrator` 三阶段走通

**不改动**：`LifecycleOrchestrator`、`InputAdapter`、现有测试全部不动。

### Batch 1: Runtime + Kernel + AgentRuntime 骨架

- Runtime 类、Kernel 类、AgentRuntime 类（含状态机 + continuous/oneshot 模式）
- SystemConsole 接口 + CliConsole 默认实现
- AgentRuntime.run() 协程骨架（含 should_exit / max_rounds / oneshot 逻辑），内部委托 `AsyncLifecycleOrchestrator`
- 模式 A 的 `Runtime.run(harness)` 入口
- 测试：单 agent 模式 A 走通 INIT → RUNNING → FINISHED

> 🔌 **端到端能力**：Mode A 单 agent 交互对话已可端到端运行。`Runtime(CliConsole()).run(harness)` → Kernel.spawn_root → AgentRuntime.run() → 用户 stdin 输入 → root agent 回复 → stdout 输出。当前 main.py 尚未接入 Runtime 路径（仍走旧同步 `harness.run()`），需要新建 Runtime 入口脚本或修改 main.py。

### Batch 2: Workflow 脚本加载

- `@agent` / `subscribe` 装饰器 + registry 机制
- importlib 脚本加载（含 registry 清空 + 模块覆盖）
- `Kernel.spawn_from_script()`
- `spawn_workflow` / `end_workflow` / `finish_agent` tool
- `talk_to` tool（定向投递）
- 测试：加载最小 workflow 脚本，创建多 agent，验证 oneshot 自动结束

> ⚠️ **Batch 2 职责边界（本 batch 明确不做）**：
> - **`subscribe` 声明仅影响 agent mode**（有订阅关系的 agent 设为 `continuous`，否则 `oneshot`），**不做实际消息路由**——MessageBus 在 Batch 3 才创建
> - **`child_finished` 自动通知不实现**：`_on_agent_finished` 仍为 Batch 1 stub（仅推送 `AgentFinished` 到 SystemConsole），子 agent 完成时父 agent 不会自动收到通知。Batch 2 期间父 agent 需通过 `list_agents` tool 主动轮询或子 agent 通过 `talk_to` 主动汇报
> - **订阅关系暂存到 `_pending_subscriptions`**，Batch 3 在 MessageBus 创建后统一注册
> - **级联终止不实现**——属于 Batch 3
> - **Mode B `run_from_script` 不实现**——属于 Batch 3
>
> 🔌 **端到端能力**：**这是第一个可以验证多 agent workflow 的 batch**。Mode A 交互下 LLM 调 `spawn_workflow` → 子 agent 创建并执行 entry_prompt → oneshot 子 agent 自动 FINISHED。父 agent 虽收不到 `child_finished` 自动通知，但可通过 `list_agents` 轮询或子 agent 通过 `talk_to` 主动汇报。**最小端到端测试**：用 mock LLM（按预定计划调 `spawn_workflow`）启动 asyncio event loop，验证子 agent 从 CREATED → FINISHED 完整生命周期，父 agent 通过 `list_agents` 获知子 agent 状态。

### Batch 3: 消息订阅 + 并发 + 终止

- MessageBus pub-sub 路由
- 默认订阅（child_finished）
- 流式订阅（显式 subscribe）
- 级联终止
- 静默检测
- 多 agent 并发运行测试
- Mode B `run_from_script` 入口 + WorkflowFinished 汇总

> 🔌 **端到端能力**：**完整多 agent 协作闭环首次达成**。父 agent `spawn_workflow` → 子 agent 并发运行 → subscribe 流式消息路由 → 子 agent FINISHED → `child_finished` 自动通知父 agent → 级联终止传播退出信号 → 静默检测自动结束。Mode B `run_from_script` 直接启动 workflow 无需 root agent。**最小端到端测试**：`run_from_script("workflow.py")` 启动 → collector + analyzer 并行执行 → analyzer 收到 collector 的 subscribe 输出 → 全部 FINISHED → `WorkflowFinished` 汇总输出。

### Batch 4: 系统命令 + 信号处理

- SystemCommand 解析（/agents, /talk, /kill, /end, /exit）
- SystemEvent 查询响应类型（AgentsListed, CommandError）
- SIGINT 两阶段处理
- `_monitor_quiescence` 实现
- Runtime.run() 完整集成（return_exceptions, shield）

> 🔌 **端到端能力**：交互式多 agent 操作完整。用户 `/agents` 查看状态 → `/talk <pid>` 定向通信 → `/kill <pid>` 终止单个 agent → `/end <flag>` 终止整个 workflow → `/exit` 优雅退出。SIGINT (Ctrl+C) 两阶段退出（优雅 + 强制）。

### Batch 5: 打磨

- 异常处理对齐（AgentRuntime.error → child_finished.metadata.error）
- 文档更新
- 原 Recursive Harness Pattern 示例替换为 Runtime API
- ContextAssembler system prompt 注入 Runtime tool 说明

> 🔌 **端到端能力**：生产就绪。异常信息正确传播到父 agent，system prompt 自动包含 Runtime tool 使用说明，`main.py` 完整支持 Runtime 路径。
