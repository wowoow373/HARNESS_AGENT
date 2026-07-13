# Harness Agent Template

> 面向个人开发者与小型团队的**模块化 Agent 框架模板**。
>
> 核心理念：**框架只定义接口契约与编排流程，所有具体行为由你通过「实现接口 + 依赖注入」来自定义。**
>
> **v2.0** — 新增 Runtime 多 Agent 协作层 | 920 tests passing

---

## Showcases

本仓库已内置以下领域 Agent 案例：

| 案例 | 路径 | 说明 |
|------|------|------|
| **chat-web** | [`agents/chat-web/`](agents/chat-web/) | WebSocket 网页聊天助手 |
| **group-chat** | [`agents/group-chat/`](agents/group-chat/) | 多人实时群聊 Agent |
| **customer-service** | [`agents/customer-service/`](agents/customer-service/) | 客服场景多意图 Agent（知识问答 / 业务办理 / 异常兜底）|

完整案例总览、运行方式与架构说明见 [agents/README.md](agents/README.md)。

---

## 为什么选 Harness？

**1. 任意替换模块 → 配置出你的领域 Agent。**
每个组件只有接口契约，没有实现绑定。换 MemoryBackend、换 InputAdapter、换 Sensor——都是一行注册代码的事。

**2. 多 Agent Runtime — 进程模型 + 消息通信。**
`spawn_workflow` 一键创建多个子 Agent，`subscribe` 声明 Agent 间消息路由。支持 pipeline、fan-out、debate、群聊等任意拓扑。用户通过 `/agents` `/kill` `/talk` 实时管理。

**3. Agent 自举：读接口、装配模块、启动子 Agent。**
Protocol 接口的 docstring 是 AI 可读的契约文档，组件 README 是"怎么替换"的操作手册。Agent 可以自己读这些文档，写实现，配 YAML，启动任意配置的子 Agent。

**4. 一句话启动。**
```bash
# Mode A: 交互式对话
python main.py run --runtime

# Mode B: 直接启动 Workflow 脚本
python main.py workflow examples/debate_workflow.py
```

---

## 快速开始

```bash
# 1. 配置 LLM
echo 'base_url = https://api.openai.com/v1
api-key = sk-your-key
model = gpt-4o' > harness/config/.env

# 2. 交互模式
python main.py run --runtime
> 你好
[root] 你好！有什么可以帮你的？

# 3. Workflow 模式
python main.py workflow examples/debate_workflow.py
```

```python
# 开发者：装配自定义 Agent
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, MemoryBackend, ContextAssembler
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.memory_backend.md_memory import MdMemory

container = DIContainer()
container.register(MemoryBackend, MdMemory(path="./memory"))
container.register(ContextAssembler, SimpleAssembler(max_history=50))

harness = Harness.from_container(container, call_llm=MinimalLLMAdapter())
Runtime(CliConsole()).run(harness)  # Mode A 启动
```

---

## 架构一览

```
你的插件（全部可替换）
  InputAdapter  GuideProvider  ContextAssembler
  MemoryBackend  Sensor  SystemToolProvider
  MCPAdapter  MCPHandler  Hook
          ▲
          │ 实现接口 + 注册到容器
          │
  ┌───────┴──────────────────────────────────────┐
  │        Harness 微内核（不修改）               │
  │  DIContainer  LifecycleOrchestrator          │
  │  ToolRouter    HookManager                   │
  ├──────────────────────────────────────────────┤
  │        Runtime 层（新增）                     │
  │  AgentRuntime  Kernel  MessageBus            │
  │  SystemConsole  CliConsole                   │
  └──────────────────────────────────────────────┘
```

**三阶段生命周期**：初始化 → 多轮循环（LLM ↔ Tool）→ 结束（Sensor 评估 + 记忆沉淀）

**Runtime 多 Agent**：Agent 状态机（CREATED→INIT→RUNNING→TERMINATING→FINISHED）、Kernel 进程表 + 消息总线、subscribe 流式通信、Workflow 脚本化

---

## 文档导航

| 文档 | 面向 | 内容 |
|------|------|------|
| **[docs/runtime/architecture.md](docs/runtime/architecture.md)** | **所有人** | **Runtime 层架构概览：进程模型、通信、Workflow** |
| **[docs/runtime/io-guide.md](docs/runtime/io-guide.md)** | **用户** | **命令参考、交互模式、常见场景** |
| [docs/FRAMEWORK.md](docs/FRAMEWORK.md) | 开发者 | 完整框架指南：四大支柱、装配、生命周期、扩展 |
| [harness/interfaces/](harness/interfaces/) | 开发者 + Agent | Protocol 接口定义 |
| [harness/components/](harness/components/) | 开发者 + Agent | 组件文档与默认实现 |
| [sdd/](sdd/) | 贡献者 | 软件设计文档 |
| [docs/superpowers/specs/](docs/superpowers/specs/) | 贡献者 | Runtime 层设计文档 |
| [examples/](examples/) | 开发者 | 可运行示例 |

### 示例

| 文件 | 说明 |
|------|------|
| [examples/runtime_agent.py](examples/runtime_agent.py) | Runtime Mode A 交互式 Agent |
| [examples/minimal_agent.py](examples/minimal_agent.py) | 经典 sync 路径示例（不依赖 Runtime） |
| [examples/debate_workflow.py](examples/debate_workflow.py) | 三人辩论 Workflow 脚本 |

### Agent 指导文件

| 文件 | 说明 |
|------|------|
| [docs/runtime/AGENTS_EXAMPLE.md](docs/runtime/AGENTS_EXAMPLE.md) | Runtime 工具 few-shot 示例（复制为 AGENTS.md 即可启用） |

---

## 测试

```bash
pytest tests/ --ignore=tests/test_real_llm_trace.py -v    # 920 tests
```

---

## 路线图

**已完成（v2.0）** 🎉

- ✅ Batch-01~11：内核 + 全部组件 + DI 装配 (v1.0)
- ✅ **Runtime 层**：多 Agent 进程模型、MessageBus pub-sub、Workflow 脚本化
- ✅ 系统命令：`/agents` `/kill` `/end` `/talk` `/exit`
- ✅ Mode A（交互式）+ Mode B（Workflow 直接启动）
- ✅ KBA DI 可注册：支持自定义 I/O 策略（batch window / immediate 等）

**计划中**

- 🔜 KBA I/O 策略可定制（batch_window、batch_count 模式）
- 🔜 子 Agent 日志可观测（read_log tool）
- 🔜 WebSocket / HTTP SystemConsole

---

## 许可证

MIT

---

> **Harness Agent Template** — 不是最强的 Agent 框架，但是**最容易裁剪、扩展和自举**的 Agent 框架模板。
