# Batch-12: Runtime 层 — 多 Agent 并发编排

> **版本**: 1.1
> **状态**: 设计中
> **批次**: batch-12

---

## 一、定位与边界

### 1.1 问题

现有 Harness 框架是**单 Agent** 模型：一个 `LifecycleOrchestrator` 管理一个会话。无法表达"Agent 创建多个子 Agent 并发协作"的场景。

### 1.2 目标

在不修改 `harness/core/` 和 `harness/interfaces/` 的前提下，新增 `harness/runtime/` 层，提供：

- Agent Runtime 的并发执行能力（每个 Agent 独立线程 + 独立 Orchestrator）
- Agent 间的父子关系与通信（类似 OS 进程模型：fork + pipe + signal）
- Workflow 的自举能力（Agent 写 Python 文件 → Runtime 执行 → 声明式定义拓扑）

### 1.3 明确不做

- 不修改 `LifecycleOrchestrator`、`DIContainer`、`InputAdapter` Protocol
- 不替换现有的单 Agent 使用方式（`Harness.from_container().run()` 继续工作）
- 不引入分布式 / 跨进程通信（限定单进程内多线程）
- 不在 Runtime 层处理 Agent 的业务逻辑

### 1.4 边界图

```
┌──────────────────────────────────────────────────────────────────┐
│                      用户代码 (User Code)                        │
│  my_workflow.py  │  自定义 ContextAssembler  │  自定义 Sensor    │
└──────────────────────────────┬───────────────────────────────────┘
                               │ 实现接口 / 调用 API
┌──────────────────────────────▼───────────────────────────────────┐
│                   harness/runtime/  (本次新增)                    │
│                                                                  │
│  AgentRuntime   MessageBus   AgentHandle   RuntimeManager        │
│  BusAdapter     RuntimeAdapter             Workflow Tools        │
└──────────────────────────────┬───────────────────────────────────┘
                               │ 依赖接口 / 注册实例
┌──────────────────────────────▼───────────────────────────────────┐
│               harness/core/ + harness/interfaces/  (不改)         │
│                                                                  │
│  LifecycleOrchestrator   DIContainer   InputAdapter(Protocol)    │
│  ContextAssembler        ToolRouter     Hook System              │
└──────────────────────────────────────────────────────────────────┘
```

**核心约束**：Runtime 层只依赖 `harness/interfaces/` 中的 Protocol 和类型。唯一例外是 `Harness.from_container()` 的调用。

---

## 二、核心抽象

### 2.1 RuntimeManager — Workflow 生命周期管理

每个 Workflow 有一个独立的 `RuntimeManager` 实例。一个 RuntimeManager 只管自己 Workflow 内的 Agent，不跨 Workflow 边界。

**获取方式**：模块级变量 `harness.runtime._current_runtime`。`launch_workflow` tool 创建新的 RuntimeManager 并设为当前，workflow Python 文件通过 `Runtime.current()` 获取。

**职责**：
- 维护 Agent 树（本 Workflow 内）
- 持有 MessageBus 实例
- 生成 agent_id / workflow_id
- `register(harness, name)` → `AgentHandle`
- `start()` → 启动所有 Agent 线程 + 启动转发循环线程
- `shutdown()` → 给所有子 Agent 发 SHUTDOWN → join 线程
- 消息转发循环（独立 daemon 线程）

**转发循环线程**：`Runtime.start()` 时创建。循环 drain MessageBus → 查订阅表 → push 到目标 Agent 的 BusAdapter mailbox。当所有 Agent 线程退出后循环结束。

### 2.2 AgentRuntime — Agent 运行实例

一个 `AgentRuntime` 代表一个正在运行的 Agent。

**职责**：
- 持有 `Harness` 实例
- 在独立线程中执行 `harness.run()`（即 `orchestrator.run()`）
- 记录父子关系（`parent: Optional[AgentRuntime]`，`children: List[AgentRuntime]`）
- 暴露 `AgentHandle` 供外部通信

**线程中未捕获异常处理**：`AgentRuntime.run()` 内 `try/except` 包裹 `harness.run()`。catch 后：设自身状态为 `crashed`，publish 到 `{agent_id}.error` topic。父 Agent 通过订阅该 topic 获知崩溃。**MVP 阶段父 Agent 收到后如何处理不做**，留后续。

```python
def run(self):
    try:
        self._harness.run()
    except Exception as e:
        self._status = AgentStatus.CRASHED
        self._bus.publish(f"{self.agent_id}.error", ErrorMessage(...))
```

**状态**（MVP 使用）：`running` / `waiting` / `crashed` / `done`

**静默/超时**：留 `workflow_idle_timeout` 配置项。MVP 置为 `None`（永不超时）。后续版本实现"所有 Agent 都在 waiting 且 bus 无消息 → 判断为完成"的逻辑。

### 2.3 AgentHandle — 通信句柄

父 Agent 持有子 Agent 引用的唯一途径。类比 OS 的 PID + 文件描述符。

**MVP 暴露的能力**：

| 操作 | 含义 | 方向 |
|------|------|------|
| `handle.output.subscribe(target)` | 该 Agent 的 output 路由到 target | 声明式 |
| `handle.kill()` | 向该 Agent mailbox 发 SHUTDOWN | 父 → 子 |
| `handle.signal(name)` | 向该 Agent mailbox 发命名信号 | 父 → 子 |
| `handle.status` | 查询状态（running/waiting/done/crashed） | 只读 |

**后续版本预留**：条件订阅 `subscribe(target, when={...})`、`handle.on(condition).then(action)` 触发规则。

**设计原则**：Handle 只能由创建者获得。Agent 不能获取 sibling 的 Handle，只能通过 MessageBus 通信。这保证了拓扑的可控性。

### 2.4 MessageBus — Workflow 内消息总线

Workflow 范围内的 pub/sub 消息路由器。精确匹配 topic。

**核心语义**：

```
publish(topic: str, message) → void
subscribe(topic: str, target: AgentHandle) → void
```

**MVP Topic 命名约定**：

| Topic | 发布者 | 含义 |
|-------|--------|------|
| `{agent_id}.output` | BusAdapter.send(StopEvent) | Agent 的 LLM 输出 |
| `{agent_id}.error` | AgentRuntime (on crash) | Agent 执行异常 |
| `{agent_id}.signal.{name}` | AgentHandle.signal() | 父发送的命名信号 |

**默认订阅**：`spawn` 时自动建立 `child.output → parent` 的订阅。父 Agent 不需要显式声明。Workflow Python 中只声明非父的订阅（如 coder.output → reviewer）。

**消息类型**：BusMessage、StopEventMessage、ShutdownMessage、SignalMessage 等 — 具体类型定义留给详细设计阶段。

---

## 三、InputAdapter 的两种新实现

### 3.1 BusAdapter — 子 Agent 专用

实现 `InputAdapter` Protocol。对接 MessageBus。

```
receive():
    首次调用 → 返回 initial_input（父 Agent 在 workflow Python 中传入的 initial_prompt）
    后续调用 → 阻塞在 mailbox（queue.Queue.get()）
    收到 ShutdownMessage → 返回 UserRequest(text="", metadata={"exit": True})

send(event):
    TextEvent → 缓存为 last_text
    StopEvent → 提取 last_text → publish 到 {agent_id}.output
    其他事件 → 默认不发布（留配置接口，后续版本开放）
```

### 3.2 RuntimeAdapter — 根 Agent 专用

实现 `InputAdapter` Protocol。用于根 Agent（人直接交互的那个）。

```
接收端（receive）:
    同时监听两个输入源:
    - stdin_queue: 独立线程中 sys.stdin.readline() 的结果
    - bus_queue:   RuntimeManager 推送的子 Agent 消息

    轮询逻辑:
        优先 bus_queue → 返回 UserRequest(带 source_agent metadata)
        其次 stdin_queue → 返回 UserRequest(用户输入)
        都为空时 sleep(0.1)

发送端（send）:
    委托 CliAdapter 处理终端输出（格式、着色、stdout/stderr 分发）
```

**与 CliAdapter 的关系**：`RuntimeAdapter` 组合 `CliAdapter` 作为输出委托。stdin 读取线程是 RuntimeAdapter 自己的，**不修改 CliAdapter 的任何代码**。CliAdapter 继续作为独立的 `InputAdapter` 实现存在，现有用户无需改动。

**stdin 线程模型**：

```
RuntimeAdapter.__init__():
    self._stdin_thread = Thread(target=self._read_stdin, daemon=True)
    self._stdin_thread.start()

def _read_stdin(self):
    while True:
        line = sys.stdin.readline()
        self._stdin_queue.put(line)
```

---

## 四、Workflow 模型

### 4.1 定义方式

Workflow 是一个 Python 文件，由 Agent 通过 `write_file` tool 创建，通过 `launch_workflow` tool 执行。

Workflow Python 文件的内容遵循和根 Agent **完全相同的装配模式**：
1. 创建 `DIContainer`，注册组件（InputAdapter 用 `BusAdapter`）
2. 调用 `Harness.from_container(container, call_llm=...)` 创建 Harness
3. 调用 `Runtime.current().register(harness, name)` 获得 Handle
4. 通过 Handle 声明跨 Agent 订阅关系
5. 调用 `Runtime.current().start()` 启动

Workflow 文件执行期间（`exec()`）拓扑是静态声明的。`Runtime.start()` 后不再添加 Agent。

### 4.2 生命周期（完整流程）

```
write_file("my_workflow.py")              ← Agent LLM 调用 write_file tool
       │
launch_workflow("my_workflow.py")         ← Agent LLM 调用 launch_workflow tool
       │
       ├─ LaunchWorkflowTool.execute():
       │    ├─ 创建新的 RuntimeManager（模块级变量指向它）
       │    ├─ exec(my_workflow.py)       ← 执行 workflow Python
       │    │    ├─ DIContainer() × N
       │    │    ├─ Runtime.current().register(harness, name) × N
       │    │    │    └─ 返回 AgentHandle × N
       │    │    ├─ handle.output.subscribe(target) × M
       │    │    └─ Runtime.current().start()
       │    │         ├─ 每个 Agent: Thread(target=harness.run).start()
       │    │         ├─ 转发循环: Thread(target=_forwarding_loop, daemon=True).start()
       │    │         └─ 返回（非阻塞）
       │    └─ Tool 返回 {workflow_id, agents: {name: id, status, ...}}
       │
       ├─ 父 Agent 的 ToolRouter 收到 ToolResult
       ├─ 父 Agent LLM 在上下文中看到 workflow 创建结果
       │
       ├─ [并发] 子 Agent 在各线程中运行自己的 Orchestrator
       │    ├─ Phase1: BusAdapter.receive() → initial_prompt
       │    ├─ Phase2: LLM ↔ Tool 循环
       │    ├─ StopEvent → BusAdapter.send() → MessageBus.publish()
       │    └─ BusAdapter.receive() 阻塞等待新消息
       │
       ├─ [并发] 转发循环线程 drain bus → match → push mailbox
       │
       ├─ 父 Agent 的 RuntimeAdapter.receive() 收到子输出
       │    └─ UserRequest(text=子输出, metadata={"source_agent": agent_id})
       │
       ├─ 父 Agent LLM 处理子输出
       ├─ 父 Agent LLM 调用 kill_agent(agent_id)
       │    └─ RuntimeManager → ShutdownMessage → 子 mailbox
       │         子 BusAdapter.receive() 收到 → 空 UserRequest
       │         子 Orchestrator._should_exit() → True
       │         子 Orchestrator._phase_end() → 线程退出
       │
       └─ 所有子线程退出 → 转发循环检测到 agents_running=False → 退出
```

### 4.3 子 Agent 的退出

**正常退出路径**：
- 父调 `kill_agent` → ShutdownMessage → BusAdapter 返回空 UserRequest → `_should_exit()` True → Phase 3 → 线程退出
- 父调 `RuntimeManager.shutdown()` → 所有子同时收到 ShutdownMessage → 全部退出

**崩溃路径**：AgentRuntime 内 catch → 设 crashed 状态 → publish error topic。父可订阅 error topic 获知。

**StopEvent 后不自动退出**：Agent 发出 StopEvent 表示完成一轮工作，继续阻塞等待新输入。这是有意设计——允许其他 Agent 通过订阅给该 Agent 发送后续任务。

### 4.4 关闭与 Ctrl+C

**正常关闭**（用户 `/exit` 或 Agent 完成）：

```
根 orchestrator.run() → finally → _phase_end()
  → ToolRouter.shutdown()
    → WorkflowTools.shutdown()
      → RuntimeManager.shutdown()
        → 所有子 Agent mailbox ← ShutdownMessage
        → join(timeout=N) 每个线程
        → 转发循环停止
  → 其他 Provider shutdown（MCP 等）
  → 根 orchestrator 退出
```

**Ctrl+C 关闭**：

```
Ctrl+C → KeyboardInterrupt（main 线程，BaseException）
  → orchestrator.run() 的 finally 块执行（保证）
    → 同上关闭路径
  → KeyboardInterrupt 继续传播
  → main.py catch KeyboardInterrupt → 打印退出信息
```

因为 `KeyboardInterrupt` 继承自 `BaseException` 不被 `except Exception` 吞掉，且 `finally` 保证执行，所以清理路径可靠。

### 4.5 Workflow Tools

Workflow Tools 是普通的 `Tool` 实现，注册在根 Agent 的 `SystemToolProvider` 中。通过模块级变量 `harness.runtime._current_runtime` 获取当前 RuntimeManager。

| Tool | 参数 | 返回值 | 副作用 |
|------|------|--------|--------|
| `launch_workflow` | script_path | workflow_id + agents 列表 | exec Python → 创建 Runtime → 启动线程 |
| `kill_agent` | agent_id | success/fail | push ShutdownMessage |
| `signal_agent` | agent_id, signal_name | success/fail | push SignalMessage |
| `check_workflow` | workflow_id | 各 agent status | 只读查询 |

**Tool description（schema）中需要包含创建 workflow 的示例**，让 LLM 知道如何写 workflow Python 文件。

**子 Agent 是否拥有 Workflow Tools**：由 Workflow Python 中的 DI 装配决定。如果子 Agent 的 `SystemToolProvider` 包含 `LaunchWorkflowTool`，它就能创建自己的子 Workflow（嵌套）。如果不包含则不能。这就是 `allow_sub_workflow` 的实现——不在框架层做权限检查，由装配决定。

---

## 五、ID 体系

### 5.1 命名规则

```
workflow_id:  "wf-{short_uuid}"
agent_id:     "{workflow_id}.{name}"
```

嵌套示例（一个 RuntimeManager 管理自己 Workflow 内的 Agent 树）：

```
wf-a1                       ← RuntimeManager 管理的 Workflow
├── wf-a1.coder
├── wf-a1.reviewer
└── wf-a1.tester
```

嵌套 Workflow（coder 创建的子 Workflow）会产生新的 RuntimeManager 实例：

```
父 Workflow RuntimeManager:
  wf-a1
  ├── wf-a1.root（根 Agent）
  ├── wf-a1.coder            ← 调 launch_workflow 创建子 Workflow
  └── wf-a1.reviewer

子 Workflow RuntimeManager (wf-b2):
  └── wf-b2.tester           ← coder 创建的子 Workflow 中的 Agent
```

两个 RuntimeManager 各自独立管理自己的 Agent。跨 Workflow 的消息路由由上层（coder Agent）处理——coder 收到 tester 输出后自行决定是否转发给 reviewer。

### 5.2 注入

`Runtime.register()` 统一注入：
- `agent_id` → `AgentRuntime` + `BusAdapter`
- `workflow_id` → `AgentRuntime`
- `session_id` → 继承自父 Agent

### 5.3 Agent 如何知晓来源

- 子 Agent 输出到达父 Agent 时，`UserRequest.metadata` 中携带 `source_agent` 字段
- 父 Agent 调用 tool 时，tool 返回的 JSON 中包含 `agent_id`
- LLM 把 `agent_id` 作为不透明字符串使用，不需理解格式

---

## 六、与现有框架的集成

### 6.1 零改动的模块

| 模块 | 原因 |
|------|------|
| `harness/interfaces/` 所有 Protocol | Runtime 通过 Protocol 消费 |
| `harness/core/orchestrator.py` | 每个 Agent 独立实例 |
| `harness/core/container.py` | 照常使用 |
| `harness/core/tool_router.py` | Workflow tools 走 SystemToolProvider |
| `harness/di.py` | `Harness.from_container()` 照常 |
| `harness/hooks/` | 每个 Agent 自己的 HookManager |
| `harness/components/` 所有现有组件 | 不变 |
| `CliAdapter` | 完全不动。RuntimeAdapter 是自己带 stdin 线程 |

### 6.2 新增模块

| 模块 | 路径 | 说明 |
|------|------|------|
| `__init__` | `harness/runtime/__init__.py` | 模块级 `_current_runtime` + `Runtime` 别名 |
| `RuntimeManager` | `harness/runtime/runtime_manager.py` | Workflow 生命周期管理 |
| `AgentRuntime` | `harness/runtime/agent_runtime.py` | Agent 线程包装 |
| `AgentHandle` | `harness/runtime/agent_handle.py` | 子 Agent 句柄 |
| `MessageBus` | `harness/runtime/message_bus.py` | pub/sub 消息总线 |
| `BusAdapter` | `harness/runtime/bus_adapter.py` | 子 Agent 的 InputAdapter |
| `RuntimeAdapter` | `harness/runtime/runtime_adapter.py` | 根 Agent 的 InputAdapter |
| Workflow Tools | `harness/runtime/workflow_tools.py` | launch/kill/signal/check |

---

## 七、设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 每个 Agent 独立线程 + 独立 Orchestrator | 复用现有编排器，不改核心 |
| 2 | BusAdapter / RuntimeAdapter 分离 | 子 Agent 和根 Agent 输入源不同 |
| 3 | RuntimeManager push，Agent 不 pull | Agent 只通过 receive() 等消息 |
| 4 | StopEvent 后不自动退出 | 允许后续消息驱动 Agent 继续工作 |
| 5 | Workflow 是 Python 文件 | 复用现有 DI 装配模式 |
| 6 | 拓扑 start() 后不可变 | 避免运行时竞态 |
| 7 | 一个 RuntimeManager 管一个 Workflow | 边界清晰，不跨层 |
| 8 | 转发循环独立 daemon 线程 | 主线程被 Agent Orchestrator 占用 |
| 9 | RuntimeAdapter 自带 stdin 线程，CliAdapter 不动 | 最小改动，向后兼容 |
| 10 | Topic 精确匹配 | MVP 够用 |
| 11 | Workflow Tool 通过模块级变量获取 RuntimeManager | 简单，不需要 DI 改造 |
| 12 | 子 Agent tool 能力由 DI 装配决定 | 与框架现有模式一致 |
| 13 | 关闭走 ToolRouter.shutdown → WorkflowTools → RuntimeManager → SHUTDOWN 级联 | 复用现有清理路径 |
| 14 | 崩溃用 try/except + error topic | 留处理接口，MVP 不深入 |

---

## 八、MVP 范围外（留接口，后续实现）

| 功能 | 处理方式 |
|------|---------|
| 条件订阅（phase/signal/flag） | 接口预留，MVP 不实现 |
| `handle.on(condition).then(action)` | MVP 不实现 |
| 静默/死锁检测（所有 Agent waiting） | 留 `workflow_idle_timeout` 配置项，MVP 为 None |
| 非 StopEvent 事件的可选发布 | 留配置接口，默认不发布 |
| Runtime 生命周期 Hooks | 留接口，MVP 不注册 |
| Agent 数量限制 | 留 `max_agents` 配置项，MVP 为 None |
| 父 Agent 对子崩溃的自动处理 | publish error topic + 设 crashed 状态，处理逻辑后续 |
