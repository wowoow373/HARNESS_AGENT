# Runtime 层架构

> 在 Harness 单 Agent 模型之上封装的**多 Agent Runtime**，提供进程模型、消息通信、Workflow 编排能力。

---

## 为什么需要 Runtime 层？

Harness 的核心是**单 Agent 模型**：一个 DI 容器 → 一个 LifecycleOrchestrator → 一个三阶段生命周期。但要构建多 Agent 协作（pipeline、fan-out、debate），需要：

- 多个 Agent 并发运行，各自独立的生命周期
- Agent 之间通过消息通信（不是通过函数返回值）
- 用户能查看、干预、终止任意 Agent
- Workflow 脚本化，一次 spawn 多个 agent

Runtime 层以 **OS 进程模型** 为类比，提供这些能力。

---

## 架构层次

```
┌──────────────────────────────────────────────┐
│  SystemConsole  ← 用户交互入口                │
│  /agents /kill /end /exit /talk               │
└──────────────────┬───────────────────────────┘
                   │
            ┌──────┴──────┐
            │   Runtime   │  顶层入口
            └──────┬──────┘
                   │
            ┌──────┴──────┐
            │   Kernel    │  全局单例
            │  进程表      │
            │  消息总线    │
            │  调度        │
            └──┬──────┬───┘
               │      │
      ┌────────▼┐ ┌───▼────────┐
      │AgentRun │ │AgentRun    │  × N
      │  time   │ │  time      │
      └─────────┘ └────────────┘
```

| 层 | 职责 | 实例数 |
|----|------|--------|
| Runtime | 顶层入口，创建 Kernel + SystemConsole，启动事件循环 | 1 |
| Kernel | 进程表、消息路由、spawn/kill/end_workflow | 1 |
| AgentRuntime | 每个 Agent 的进程控制块：状态机、模式（continuous/oneshot） | N |
| SystemConsole | 接收系统命令、推送系统事件到终端 | 1 |
| KernelBridgeAdapter | Agent 对 I/O 的唯一通道，对接 Kernel 的消息队列 | 每 Agent 1 个 |

---

## Agent 生命周期

```
CREATED → INIT → RUNNING → TERMINATING → FINISHED
              ↑        ↑           ↑
              └────────┴───────────┘
              任何状态均可 → TERMINATING
```

每轮对话：`receive()` → LLM 调用 → TextEvent 输出 → StopEvent → 返回循环。

---

## 两种启动模式

### Mode A：交互式对话

```bash
python main.py run --runtime
```

- 创建 root agent（continuous）
- 用户纯文本 → root agent
- root 可以通过 `spawn_workflow` 创建子 agent
- 用户 `/exit` 退出

### Mode B：直接启动 Workflow

```bash
python main.py workflow examples/debate_workflow.py
```

- 不创建 root agent
- 从脚本加载所有 agent 并启动
- Agent 全部 continuous，不会自动退出
- 用户 `/end <flag>` 或 `/exit` 手动终止

---

## 通信模型

三层通信：

| 级别 | 机制 | 说明 |
|------|------|------|
| 流式订阅 | `subscribe("A").to("B")` | A 实时收到 B 每轮的 TextEvent |
| 定向投递 | `/talk <pid> <msg>` | 用户直接向指定 agent 发消息 |
| 默认订阅 | child_finished | 父 agent 自动收到子 agent 的完成通知 |

### 消息身份

| 来源 | metadata |
|------|----------|
| subscribe（agent 互发） | `{"from": "alice"}` |
| `/talk`（用户发的） | `{"from": "user", "type": "talk"}` |
| entry_prompt | `{"workflow_flag": "wf_001"}` |

---

## Workflow 脚本

```python
from harness.runtime.decorators import agent, subscribe

@agent("alice", entry_prompt="你叫 Alice。说一句话打招呼。")
def assemble_alice():
    container = DIContainer()
    # ... 装配 Harness ...
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())

@agent("bob", entry_prompt="你叫 Bob。等 Alice 的消息再回复。")
def assemble_bob():
    # ...

subscribe("bob").to("alice")  # bob 收听 alice 的输出
```

---

## 系统命令

| 命令 | 功能 |
|------|------|
| `/agents` | 查看所有 agent 状态（PID/STATE/MODE/ROUNDS） |
| `/talk <pid> <msg>` | 向指定 agent 发送消息 |
| `/kill <pid>` | 终止单个 agent |
| `/end <flag>` | 终止整个 workflow |
| `/exit` | 优雅退出 Runtime |

详见 [IO 指南](io-guide.md)。
