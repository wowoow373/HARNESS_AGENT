# Runtime I/O 指南：命令、事件与交互模式

> 适用版本: Batch 4+ | 读者: Harness Agent 用户

---

## 概述

Harness Agent Runtime 有两层 I/O 通道，分别服务于不同的目的：

```
┌──────────────────────────────────────────────────────┐
│  终端 (stdin/stdout)                                  │
│  ┌────────────────────────────────────────────────┐  │
│  │  SystemConsole (/命令, 系统事件)                  │  │
│  │  你 ↔ Kernel（管理多 agent）                     │  │
│  └────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────┐  │
│  │  Agent I/O (对话内容, tool 调用)                  │  │
│  │  agent ↔ agent（MessageBus 消息路由）            │  │
│  │  [LLM 内部通信，终端不可见]                       │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

- **SystemConsole**：你与 Runtime 的交互界面。输入 `/` 命令管理 agent，看到系统事件通知。
- **Agent I/O**：agent 之间的消息通信。TextEvent 通过 MessageBus 在 agent 间路由，中间事件（Thinking/ToolCall/ToolResult）降级到终端显示。

---

## 启动模式

### Mode A：交互式对话

```bash
python main.py
```

启动后进入与 root agent 的交互对话。纯文本输入直接发送给 root agent。

```
[系统] Runtime 启动
[系统] Agent spawned: root

你好，我是代码分析助手。有什么可以帮你？

你: 分析当前目录的代码质量
[root] 好的，我将创建一个 workflow 来分析代码...
[系统] Agent spawned: collector, parent=root
[系统] Agent spawned: analyzer, parent=root
[系统] Agent finished: collector (12.5s, 正常完成)
[系统] Agent finished: analyzer (8.2s, 正常完成)
[root] 分析完成！发现 3 个问题: 1) 重复代码 2) ...
```

### Mode B：直接启动 Workflow

```bash
python -c "
from harness.runtime import Runtime
from harness.runtime.cli_console import CliConsole
Runtime(CliConsole(mode='mode_b')).run_from_script('workflow.py')
"
```

不创建 root agent，直接运行 workflow 脚本。所有 agent 完成后显示汇总结果。

```
[系统] Runtime 启动
[系统] Agent spawned: collector
[系统] Agent spawned: analyzer
[collector] 采集到 28 个文件，总计 4500 行
[analyzer] 分析完成，发现 3 个问题...
[系统] Agent finished: collector (12.5s, 正常完成)
[系统] Agent finished: analyzer (8.2s, 正常完成)
[系统] Workflow wf_001 完成:
  collector    正常  1轮  12.5s
    → 采集到 28 个文件，总计 4500 行
  analyzer     正常  2轮  8.2s
    → 分析完成，发现 3 个问题...
[系统] 所有 agent 已完成。按 Enter 退出...
```

---

## 系统命令参考

所有系统命令以 `/` 开头，由 Kernel 直接处理，不经过任何 agent 的 LLM。

### `/agents` — 查看所有 agent 状态

```
你: /agents
[系统] Agents (3):
  PID          STATE         MODE        ROUNDS  PARENT
  ------------ ------------- ----------- ------- ------------
  root         running       continuous  5       -
  collector    finished      oneshot     1       root
  analyzer     running       continuous  3       root
```

STATE 可能值：`created` | `init` | `running` | `terminating` | `finished`  
MODE 可能值：`continuous`（持续等待输入）| `oneshot`（一轮后自动退出）

### `/kill <pid>` — 终止指定 agent

```
你: /kill collector
[系统] Agent collector: finished → terminating
```

- 向目标 agent 推送退出信号
- agent 完成当前操作后进入 TERMINATING → FINISHED
- 父 agent 会收到 `child_finished` 通知
- 已 FINISHED 的 agent 不受影响

### `/end <flag>` — 终止整个 workflow

```
你: /end wf_001
[系统] Agent wf_001: active → terminating
```

- 对 workflow 中所有非 FINISHED agent 推送退出信号
- workflow flag 可通过 `spawn_workflow` tool 返回值获取，或查看 `/agents`
- root agent 的 flag 固定为 `wf_root`

### `/exit` — 优雅退出 Runtime

```
你: /exit
[系统] Runtime 停止
```

- 向所有非 FINISHED agent 推送退出信号
- 等待所有 agent 进入 FINISHED
- `Ctrl+D`（EOF）效果等同于 `/exit`

### `/talk <pid> <text>` — 定向发送消息（Mode B）

```
你: /talk analyzer 请重新分析第3个问题
```

- 仅在 Mode B 下使用（Mode A 下纯文本自动路由到 root agent）
- 消息以 `UserRequest` 形式投递到目标 agent 的 input_queue
- 目标必须是活跃 agent（state ≠ FINISHED）

---

## 系统事件输出

你在终端看到的所有 `[系统]` 消息都是 SystemConsole 事件。以下是完整列表：

| 事件 | 触发时机 | 示例输出 |
|------|---------|---------|
| `RuntimeStarted` | Runtime 启动 | `[系统] Runtime 启动` |
| `RuntimeStopped` | 所有 agent 结束 | `[系统] Runtime 停止` |
| `AgentSpawned` | agent 创建 | `[系统] Agent spawned: collector, parent=root` |
| `AgentStateChanged` | 状态变更 | `[系统] Agent collector: running → terminating` |
| `AgentFinished` | agent 完成 | `[系统] Agent finished: collector (12.5s, 正常完成)` |
| `AgentOutput` | agent 输出无订阅者 | `[collector] 采集到 28 个文件...` |
| `WorkflowFinished` | Mode B 全部完成 | 格式化表格汇总 |
| `AgentsListed` | `/agents` 响应 | 格式化状态表格 |
| `CommandError` | 命令执行失败 | `[系统] 错误: pid 'ghost' 不存在` |

---

## Mode A vs Mode B 交互对比

| 行为 | Mode A | Mode B |
|------|--------|--------|
| 纯文本 | → root agent | ❌ 报错（需 `/talk`） |
| `/agents` | ✅ | ✅ |
| `/kill <pid>` | ✅ | ✅ |
| `/end <flag>` | ✅ | ✅ |
| `/exit` | ✅ | ✅ |
| `/talk <pid> <text>` | 可用但不必要（可用纯文本直接跟 root 对话） | ✅ 主要通信方式 |
| 结束后 | 用户 `/exit` 退出 | 按 Enter 退出 |
| EOF (Ctrl+D) | 同 `/exit` | 同 `/exit` |
| 空输入 | 忽略 | 运行中：报错提示；结束后：等同 `/exit` |

---

## 常见交互场景

### 场景 1：查看 workflow 进度

```
你: /agents
[系统] Agents (3):
  ...（查看各 agent 状态）
```

### 场景 2：提前终止不想要的子 agent

```
你: /kill slow_collector
[系统] Agent slow_collector: running → terminating

# 片刻后：
[系统] Agent finished: slow_collector (45.3s, 正常完成)

# root agent 收到 child_finished 通知，可以决定下一步
[root] collector 已完成。现在基于已有数据继续分析...
```

### 场景 3：终止整个 workflow 重来

```
你: /end wf_001
[系统] Agent wf_001: active → terminating
[系统] Agent finished: collector (12.5s, 正常完成)
[系统] Agent finished: analyzer (8.2s, 正常完成)

# 现在可以 spawn 新的 workflow
你: 用不同的策略重新分析
[root] 好的，创建新的 workflow...
```

### 场景 4：Mode B 下与 agent 交互

```
# Mode B 启动，agent 们已经在运行
[collector] 采集完成，等待分析指令...

# 给 analyzer 发指令
你: /talk analyzer 重点关注安全漏洞
[analyzer] 好的，我将聚焦安全分析...

# 片刻后
[analyzer] 安全分析完成，发现 2 个 SQL 注入风险

# 给 collector 发指令
你: /talk collector 再采集一下 tests/ 目录
[collector] 正在采集 tests/ 目录...

# 全部完成后按回车退出
[系统] 所有 agent 已完成。按 Enter 退出...
```

### 场景 5：强制退出

```
# 第一次 Ctrl+C：优雅退出
^C
[系统] Agent root: running → terminating
[系统] Agent finished: root (120.5s, 正常完成)
[系统] Runtime 停止

# 如果卡住了，第二次 Ctrl+C：强制终止
^C^C
# 立即终止所有协程，跳过清理
```
