# Harness — 开发者框架指南

> 一个面向个人开发者与小型团队的**模块化 Agent 框架模板**。
> 核心原则：**框架只定义接口契约与编排流程，所有具体行为由你通过「实现接口 + 依赖注入」来自定义。**

---

## 目录

1. [核心理念](#1-核心理念)
2. [五分钟上手：装配你的第一个 Agent](#2-五分钟上手装配你的第一个-agent)
3. [架构全景](#3-架构全景)
4. [四大设计支柱](#4-四大设计支柱)
   - [4.1 任意替换模块 → 配置出领域 Agent](#41-任意替换模块--配置出领域-agent)
   - [4.2 模块内递归使用 Harness → 实现更深功能](#42-模块内递归使用-harness--实现更深功能)
   - [4.3 Agent 自举：自己读接口、装配模块、启动子 Agent](#43-agent-自举自己读接口装配模块启动子-agent)
   - [4.4 一句话启动：非开发者也能用](#44-一句话启动非开发者也能用)
5. [组件体系速查](#5-组件体系速查)
6. [装配模式](#6-装配模式)
7. [生命周期与数据流](#7-生命周期与数据流)
8. [Hook 系统](#8-hook-系统)
9. [扩展模式](#9-扩展模式)
10. [后期规划：Workflow 多 Agent 协作](#10-后期规划workflow-多-agent-协作)
11. [文档导航](#11-文档导航)

---

## 1. 核心理念

```
          ┌──────────────────────────────────────┐
          │           Harness 框架内核             │
          │  ┌──────────┐  ┌───────────────────┐  │
          │  │DIContainer│  │LifecycleOrchestrator│ │
          │  │(注册/解析) │  │(三阶段编排)        │  │
          │  └──────────┘  └───────────────────┘  │
          │  ┌──────────┐  ┌───────────────────┐  │
          │  │ToolRouter │  │   HookManager      │  │
          │  │(合并路由)  │  │ (11个拦截点)       │  │
          │  └──────────┘  └───────────────────┘  │
          └─────────┬────────────┬────────────────┘
                    │            │
          ┌─────────▼────────────▼────────────────┐
          │         你的插件（全部可替换）          │
          │  InputAdapter  GuideProvider          │
          │  ContextAssembler  MemoryBackend      │
          │  Sensor  SystemToolProvider           │
          │  MCPAdapter  MCPHandler  Hook         │
          └───────────────────────────────────────┘
```

Harness 不是"一个 Agent 框架"——它是**一个 Agent 框架的骨架**。内核只做三件事：

1. **按固定顺序调度组件**（三阶段生命周期）
2. **管理组件的注册与解析**（预构造实例的 DI 容器）
3. **在关键节点插入拦截逻辑**（11 个 Hook 点）

其余一切——如何压缩上下文、如何存储记忆、如何评估质量、如何呈现输出——都是你的插件。

---

## 2. 五分钟上手：装配你的第一个 Agent

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, MemoryBackend, Sensor, ContextAssembler, GuideProvider
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.guide_provider.file_guide_provider import FileGuideProvider

# 1. 创建共享实例
memory = MdMemory(path="./memory")

# 2. 注册组件——用默认实现，也可以换成你自己的
container = DIContainer()
container.register(InputAdapter, CliAdapter())              # 必需
container.register(MemoryBackend, memory)
container.register(Sensor, LoggingSensor(memory=memory))
container.register(ContextAssembler, SimpleAssembler(max_history=50))
container.register(GuideProvider, FileGuideProvider("AGENTS.md"))

# 3. 启动
llm = MinimalLLMAdapter()  # 零配置，自动从 .env 读取
Harness.from_container(container, call_llm=llm).run()
```

跑起来后，在终端输入任意内容即可对话，输入 `/exit` 退出。

> 完整可运行代码见 [examples/minimal_agent.py](../examples/minimal_agent.py)。

---

## 3. 架构全景

```
┌──────────────────────────────────────────────────────────────────────┐
│                            InputAdapter                              │
│                  receive() / send(event: AdapterEvent)               │
└──────────────────────────┬──────────────────────┬────────────────────┘
                           │ UserRequest           │ AdapterEvent 流
                           ▼                       ▲
                  ┌─────────────────┐              │
                  │  GuideProvider  │              │
                  └────────┬────────┘              │
                           │ GuidesBundle          │
                           ▼                       │
    ┌──────────────┐ ┌──────────────────┐ ┌───────┴───────┐
    │ MemoryBackend│◀│ ContextAssembler │ │  编排器内层循环  │
    └──────┬───────┘ └──────────────────┘ │  逐字段推送事件  │
           ▲                  ▲            └───────┬───────┘
           │                  │                    │
           │    ┌─────────────┴─────────────┐      │
           │    │   ToolRouter (框架内部)     │◀─────┘
           │    │  ┌──────────┬──────────┐   │
           │    │  │ 系统Tool │ MCP Tool │   │
           │    │  └─────┬────┴────┬─────┘   │
           │    └────────┼─────────┼─────────┘
           │             │         │
           │    ┌────────▼───┐  ┌──▼──────────────┐
           │    │SystemTool  │  │  MCPAdapter      │
           │    │Provider    │  │  (不注册即裁切)   │
           │    └────────────┘  └───────────────────┘
           │
           └────────────────────┐
                                │
                      ┌─────────┴─────────┐
                      │      Sensor       │
                      │ (构造注入 memory)  │
                      └───────────────────┘
```

**关键约束**：
- Sensor 和 ContextAssembler 不直接通信，通过 MemoryBackend 解耦
- ToolRouter 是框架内部组件（非 DI），合并 SystemToolProvider + MCPAdapter
- MCPAdapter 不注册即裁切 MCP 功能
- 所有组件通过 **DI 容器** 装配，依赖通过构造函数注入

---

## 4. 四大设计支柱

### 4.1 任意替换模块 → 配置出领域 Agent

每个组件都只定义接口契约（Protocol），没有任何实现绑定。更换一个模块 = 实现对应 Protocol + 注册到容器。

**场景：把 CLI Agent 变成 WebSocket Agent**

```python
# 只换 InputAdapter，其余组件不动
class WebSocketAdapter:
    def receive(self) -> UserRequest:
        data = await self._ws.recv()
        return UserRequest(text=json.loads(data)["text"])

    def send(self, event: AdapterEvent) -> None:
        if isinstance(event, TextEvent):
            await self._ws.send(json.dumps({"type": "text", "content": event.content}))
        # ...

# CLI → WebSocket：只改一行注册
container.register(InputAdapter, WebSocketAdapter(ws))
```

**场景：把 Markdown 记忆换成 PostgreSQL**

```python
class PgMemory:
    def search(self, query, namespace, limit=10):
        rows = self._db.execute(
            "SELECT * FROM memories WHERE namespace=%s AND content ILIKE %s LIMIT %s",
            (namespace, f"%{query}%", limit)
        )
        return [MemoryItem(key=r[0], value=r[1], namespace=namespace) for r in rows]
    # ...

# MdMemory → PgMemory：只改一行注册
container.register(MemoryBackend, PgMemory(dsn="postgresql://..."))
```

**组件与可替换维度**：

| 组件 | 典型替换场景 |
|------|------------|
| InputAdapter | CLI → WebSocket / HTTP / TUI / 语音 |
| GuideProvider | 静态文件 → 数据库 / API / 动态 LLM 生成 |
| ContextAssembler | 滑动窗口 → Token 预算 / RAG / 智能压缩 |
| MemoryBackend | Markdown 文件 → PostgreSQL / Redis / 向量数据库 |
| Sensor | 日志记录 → 质量评分 / 技能提取 / 自动 prompt 优化 |
| SystemToolProvider | 内置工具 → 自定义工具集 / 领域专用工具 |
| MCPAdapter | 不注册（裁切） / stdio → HTTP / 自定义转换管道 |

> 每个组件的接口契约、调用时机、默认实现、替换示例详见 [harness/components/](../harness/components/) 下对应目录的 README。

---

### 4.2 模块内递归使用 Harness → 实现更深功能

任何一个你的模块实现，内部都可以用 `DIContainer` + `Harness.from_container()` 再装配一个完整的 Harness Agent——形成**递归架构**。

**核心模式**：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, ContextAssembler

# 在你的模块方法内部：
sub_container = DIContainer()
sub_container.register(InputAdapter, MyOneShotAdapter(input_data))
sub_container.register(ContextAssembler, MyAssembler(input_data))
# 子 Harness 可以有独立的 tools、guides、sensor...

sub_harness = Harness.from_container(sub_container, call_llm=self._sub_llm)
sub_harness.run()
# sub_harness._final_output 就是子 Agent 的产出
```

**最典型的四个使用场景**：

| 模块 | 子 Harness 用途 | 触发频率 |
|------|----------------|---------|
| **Sensor** | 会话结束时评估质量、提取技能、自动优化 prompt | 一次/会话 |
| **Tool** | 复杂多步骤工具（代码审查、调研、重构），子 Agent 有自己的 tools | 按需 |
| **GuideProvider** | 扫描项目、分析代码库、动态生成 system prompt | 一次/会话 |
| **ContextAssembler** | 历史过长时启动子 Agent 做智能压缩/摘要 | 按需 |

> 详细示例见各组件的 README：[Sensor](../harness/components/sensor/README.md)、[Tool](../harness/components/tool/README.md)、[GuideProvider](../harness/components/guide_provider/README.md)、[ContextAssembler](../harness/components/context_assembler/README.md)。

---

### 4.3 Agent 自举：自己读接口、装配模块、启动子 Agent

这是 Harness 区别于其他框架的**核心设计决策**：

- **接口即文档**：`harness/interfaces/` 下的 Protocol 定义既是类型契约，也是 AI Agent 可读的"使用说明书"。每个 Protocol 的 docstring 明确写了职责、调用时机、参数类型、返回值。
- **组件 README 即手册**：`harness/components/*/README.md` 告诉 Agent 每个组件怎么替换、怎么注册、怎么写自定义实现。
- **YAML 装配即配置**：Agent 可以通过修改 `harness.yaml` 来声明式地调整组件组合，无需写代码。

**一个 Agent 自举的典型流程**：

```
用户: "帮我创建一个代码审查 Agent，要能用 shell 工具、审查结果存 PostgreSQL"

Agent 内部流程:
  1. 阅读 harness/interfaces/ 了解有哪些组件接口
  2. 阅读 harness/components/tool/README.md 了解 SystemToolProvider 的替换方式
  3. 阅读 harness/components/memory_backend/README.md 了解 MemoryBackend 的替换方式
  4. 编写 PgMemory 类（实现 MemoryBackend Protocol）
  5. 在 harness.yaml 中声明组件装配
  6. python main.py run --config harness.yaml
```

**Agent 启动物性子 Agent**：

```python
# Agent 在 Tool 内部装配子 Harness
class StartSubAgentTool(BaseTool):
    """让主 Agent 能启动任意配置的子 Agent"""

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        sub_container = DIContainer()
        sub_container.register(InputAdapter, OneShotAdapter(args["task"]))
        sub_container.register(ContextAssembler, TaskAssembler(args["task"]))
        # Agent 自己决定给子 Agent 装配哪些 tools
        if args.get("allow_shell"):
            sub_container.register(SystemToolProvider, DefaultSystemToolProvider())

        sub_harness = Harness.from_container(sub_container, call_llm=self._llm)
        sub_harness.run()
        return ToolResult(success=True, content=sub_harness._final_output)
```

> 关键洞察：接口定义（Protocol docstrings）+ 组件文档（READMEs）构成了 Agent **可自主阅读和执行的"API 手册"**。

---

### 4.4 一句话启动：非开发者也能用

非开发者用户不需要理解接口、不需要写代码。他们只需要**启动默认 Agent，然后对话**——Agent 会自己完成自举。

```bash
# 1. 配置好 LLM 后，一句话启动
python main.py run

# 2. 默认 CLI 启动，用户描述需求
> 帮我做一个代码审查助手，能用 shell 工具跑测试，审查结果存 PostgreSQL

# 3. Agent 自举流程（用户不可见）
#    → 阅读 harness/interfaces/ 的 Protocol 文档
#    → 阅读 harness/components/tool/README.md
#    → 阅读 harness/components/memory_backend/README.md
#    → 写 PgMemory 类实现 MemoryBackend Protocol
#    → 更新 harness.yaml，换掉 MdMemory → PgMemory
#    → 重启，新配置生效

# 4. 用户继续对话——已经是新的代码审查 Agent 了
> 审查 src/main.py
🔧 shell(command=pytest tests/) → OK (1.2s)
结果已存入 PostgreSQL ✅
```

**对比传统框架**：传统框架需要用户先学习概念、写代码、配置——Harness 把这一步交给了 Agent 自己。

**Profile 模板**（开发者的快捷方式）：

对于有明确需求的开发者，Profile 模板是一个可选的加速手段——但**不是必需路径**：

```bash
# 开发者快捷方式：从模板生成
python main.py init --profile coding-assistant my-agent
# 非开发者路径：直接启动，对话自举
python main.py run
```

---

## 5. 组件体系速查

| 组件 | 接口 | 必需？ | 生命周期阶段 | 一句话职责 |
|------|------|--------|------------|-----------|
| **InputAdapter** | `receive() → UserRequest` / `send(event)` | ✅ 是 | Init + Loop | 输入输出通道，前后台分离 |
| **GuideProvider** | `get_guides(ctx) → GuidesBundle` | ❌ | Init（缓存） | 前馈：提供身份、规则、约束 |
| **ContextAssembler** | `assemble(ctx) → List[Message]` | ❌ 强烈建议 | Loop（每轮） | 上下文工程：拼装 LLM 输入 |
| **MemoryBackend** | `read/write/search/list` | ❌ | Init + End | 跨会话记忆存储与检索 |
| **Sensor** | `sense(trajectory) → void` | ❌ | End | 反馈：评估轨迹，沉淀知识 |
| **SystemToolProvider** | `get_tools() / execute()` | ❌ | Init + Loop | 管理本地 Tool 集合 |
| **MCPAdapter** | `get_tools() / execute() / shutdown()` | ❌ 不注册即裁切 | Init + Loop + End | 消费外部 MCP Server |
| **MCPHandler** | `transform_schema/args/result` | ❌ | Init + Loop | MCP 工具的程序化转换 |
| **Hook** | `(HookContext) → None` | ❌ | 11 个拦截点 | 生命周期拦截 |

> 每个组件的完整文档（接口契约、默认实现、替换示例）：[harness/components/](../harness/components/)

---

## 6. 装配模式

### Python API（命令式，灵活）

```python
container = DIContainer()
container.register(InputAdapter, CliAdapter())
container.register(MemoryBackend, MdMemory(path="./memory"))
container.register(ContextAssembler, SimpleAssembler(max_history=50))
Harness.from_container(container, call_llm=MinimalLLMAdapter()).run()
```

### YAML（声明式，80% 场景）

```yaml
# harness.yaml
harness:
  version: "1.0"
  profile: coding-assistant

  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter

    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: ./memory

    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      params:
        max_history: 50
      inject:
        memory: MemoryBackend

    - interface: Sensor
      implementation: harness.components.sensor.LoggingSensor
      inject:
        memory: MemoryBackend

  hooks:
    - event: before_llm_call
      handler: my_project.hooks.log_request

  llm:
    provider: openai
    model: gpt-4o
```

```bash
python main.py run --config harness.yaml
```

### CLI 脚手架

```bash
python main.py init --profile coding-assistant my-agent
cd my-agent
python ../main.py run
```

---

## 7. 生命周期与数据流

```
阶段一：会话初始化（一次）
  InputAdapter.receive() → GuideProvider.get_guides() → MemoryBackend.search()
  → ToolRouter 注册工具 → 组装 AssemblyContext（缓存 guides/tools/memories）

阶段二：多轮对话循环
  外层（每轮用户输入）:
    ContextAssembler.assemble() → [Hook] → LLM
    → 内层（tool 连续调用）:
        [ToolCallEvent] → ToolRouter.execute() → [ToolResultEvent] → 继续 LLM
    → [TextEvent] → [StopEvent] → InputAdapter.receive()

阶段三：会话结束（一次，finally 块中执行）
  组装 Trajectory → [on_session_end Hook] → Sensor.sense()
  → [after_sensor Hook] → ToolRouter.shutdown() → 清理
```

**关键约束**：
- GuidesBundle、tools、memories 在阶段一获取后**缓存复用**
- 内层循环（tool calls）**不走** ContextAssembler，结果直接追加到消息列表
- 阶段三在 `finally` 块中执行，异常时也会触发 Sensor
- 事件驱动输出（batch-11）：编排器推送 5 种 AdapterEvent，前端自主决定前后台展示

---

## 8. Hook 系统

11 个生命周期拦截点，函数签名 `(HookContext) → None`，通过修改 `HookContext.data` 实现拦截：

```
before_guide_generation  → 修改 GuideContext
after_guide_generation   → 修改 GuidesBundle
before_assemble          → 修改 AssemblyContext
after_assemble           → 修改 List[Message]
before_llm_call          → 修改 List[Message]
after_llm_call           → 修改 Response
before_tool_execute      → 修改 ToolCall
after_tool_execute       → 修改 ToolResult
after_sensor             → 观察 Trajectory（只读）
on_error                 → 异常处理介入
on_session_end           → 会话结束清理
```

**注册 Hook**：

```python
# Python API
def log_request(ctx: HookContext) -> None:
    print(f"LLM call with {len(ctx.data)} messages")

harness.register_hook("before_llm_call", log_request)
```

```yaml
# YAML
hooks:
  - event: before_llm_call
    handler: my_project.hooks.log_request
```

---

## 9. 扩展模式

### Metadata 扩展桶

多个大包对象（`UserRequest`、`AssemblyContext`、`Trajectory`、`GuideContext` 等）均包含 `metadata: Dict[str, Any]` 字段。框架不解释 metadata 内容——组件实现者可约定特定 key 的含义，让领域信息在组件间传递而不污染通用接口。

### Duck Typing

组件不需要继承任何基类。编排器通过 duck typing 调用——有对应方法就能工作。`harness/interfaces/` 下的 Protocol 提供类型标注参考，同时作为 `container.register()` 的类型 key。

### 接口扩展

```python
# 用户可以定义领域特定的子接口
class CodeContextAssembler:
    def assemble(self, ctx: AssemblyContext) -> list:
        # 代码领域特有的上下文组装逻辑
        ...
# 注册到容器——框架只调用标准的 assemble() 方法
container.register(ContextAssembler, CodeContextAssembler())
```

---

## 10. 后期规划：Workflow 多 Agent 协作

> **状态：计划中，尚未实现。**

### 10.1 概述

在现有递归 Harness（[4.2](#42-模块内递归使用-harness--实现更深功能)）基础上，Workflow 系统让 **Agent 能够编写和发起多 Agent 协作流程**——不是写死在工作流引擎里，而是 Agent 动态编排的。

### 10.2 核心机制：InputAdapter 作为 Agent 间通信协议

Sub-agent 之间不共享内存、不直接 RPC——它们通过 **InputAdapter 互相通信**。这与现有架构完全一致：每个 sub-agent 也是一个完整的 Harness 实例，有自己的 `InputAdapter`。

```python
# 管道通信：Agent A 的输出 → Agent B 的输入
class PipeAdapter:
    """将一个 Agent 的输出事件路由到另一个 Agent 的输入。"""

    def __init__(self, source_adapter):
        self._inbox = queue.Queue()    # 接收上游消息
        self._outbox = queue.Queue()   # 发送到下游

    def receive(self) -> UserRequest:
        # 从上游 Agent 的输出队列取消息
        event = self._inbox.get()
        return UserRequest(text=event.content if hasattr(event, 'content') else str(event))

    def send(self, event: AdapterEvent) -> None:
        # 推送到下游 Agent 的输入队列
        self._outbox.put(event)
```

### 10.3 三种基本拓扑

```
1. Pipeline（串行管道）
   [Agent A] → PipeAdapter → [Agent B] → PipeAdapter → [Agent C]
   适用：代码生成 → 代码审查 → 修复

2. Fan-out（并行分发）
                    ┌→ [Agent B1]（安全检查）
   [Agent A] → 广播 ─┼→ [Agent B2]（性能审查）
                    └→ [Agent B3]（风格审查）
   适用：多维度并行审查、对抗性验证

3. Debate（对抗辩论）
   [Agent A] ←→ PipeAdapter ←→ [Agent B]
   适用：方案辩论、真伪验证
```

### 10.4 Agent 编写 Workflow 的流程

```
主 Agent 收到用户需求: "帮我审查这个 PR 的安全性、性能和代码风格"

主 Agent 内部:
  1. 识别需要三个审查维度 → 选择 fan-out 拓扑
  2. 为每个 sub-agent 编写 GuideProvider 配置（不同的审查 focus）
  3. 装配三个 sub-harness + 对应的 PipeAdapter
  4. 并行启动，收集结果
  5. 汇总三个审查报告 → 返回用户

伪代码:
  results = await workflow.fan_out(
      task="Review PR #42",
      agents=[
          {"guide": "You are a security auditor...", "tools": ["read_file", "shell"]},
          {"guide": "You are a performance expert...", "tools": ["read_file"]},
          {"guide": "You are a style reviewer...", "tools": ["read_file"]},
      ]
  )
```

### 10.5 与现有架构的关系

Workflow 不是另起炉灶——它是现有递归 Harness 模式的自然延伸：

| 现有能力 | Workflow 扩展 |
|---------|-------------|
| 单模块内启动一个子 Harness | 启动 N 个子 Harness，按拓扑编排 |
| 子 Harness 结果由调用方收集 | Sub-agent 通过 PipeAdapter 互相发送事件 |
| `Harness.from_container().run()` | `Workflow.run(topology, agents)` |

> 关键不变：每个 sub-agent 仍然是一个完整的 Harness 实例——有自己的 DIContainer、InputAdapter、Tools、Sensor。Workflow 层只负责**连接它们的 InputAdapter** 和**控制执行顺序**。

---

## 11. 文档导航

| 文档 | 面向 | 内容 |
|------|------|------|
| [README.md](../README.md) | 所有人 | 项目概览与快速开始 |
| **本文 (FRAMEWORK.md)** | **开发者** | **完整框架指南：理念、架构、四大支柱、装配、扩展** |
| [harness/interfaces/](../harness/interfaces/) | 开发者 + **Agent** | 接口 Protocol 定义（Agent 可读的契约文档） |
| [harness/components/*/README.md](../harness/components/) | 开发者 + **Agent** | 每个组件的接口说明、默认实现、替换示例 |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | 架构参考 | 详细架构设计、组件关系、数据流 |
| [CORE_DEVELOPER_GUIDE.md](../CORE_DEVELOPER_GUIDE.md) | 开发者 | 数据类型速查、LLM 集成、消息转换、测试 |
| [sdd/](../sdd/) | 贡献者 | 软件设计文档与分批实现计划 |
| [examples/](../examples/) | 开发者 | 可运行的示例代码 |
| [profiles/](../profiles/) | 所有人 | 领域模板（coding-assistant 等） |

---

> **Harness Agent Template** — 不是最强的 Agent 框架，但是**最容易裁剪、扩展和自举**的 Agent 框架模板。
