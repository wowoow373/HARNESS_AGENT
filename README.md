# Harness Agent Template

> 面向个人开发者与小型团队的**模块化 Agent 框架模板**。
>
> 核心理念：**框架只定义接口契约与编排流程，所有具体行为由你通过「实现接口 + 依赖注入」来自定义。**
>
> **版本: 1.0** | 598 tests passing

---

## 为什么选 Harness？

**1. 任意替换模块 → 配置出你的领域 Agent。**
每个组件只有接口契约，没有实现绑定。换 MemoryBackend、换 InputAdapter、换 Sensor——都是一行注册代码的事。你需要的是编程助手还是旅行规划师？换掉 GuideProvider 和 Tool 集合即可。

**2. 模块内递归使用 Harness → 实现更深功能。**
你的 Sensor、Tool、GuideProvider、ContextAssembler 内部，随时可以用 `DIContainer` + `Harness.from_container()` 再装配一个完整子 Agent——给评估器、给代码审查工具、给记忆压缩器。

**3. Agent 自举：读接口、装配模块、启动子 Agent。**
Protocol 接口的 docstring 是 AI 可读的契约文档，组件 README 是"怎么替换"的操作手册。Agent 可以自己读这些文档，写实现，配 YAML，启动任意配置的子 Agent。

**4. 一句话启动。**
因为 Agent 能自举，非开发者用户不需要写代码——直接启动，对话即可：

```bash
python main.py run
> 帮我做一个代码审查助手，用 shell 工具，结果存 PostgreSQL
# Agent 自己读接口文档 → 写实现 → 装配 → 重启，搞定
```

---

## 快速开始

```bash
# 1. 配置 LLM（编辑 harness/config/.env）
echo 'base_url = https://api.openai.com/v1
api-key = sk-your-key
model = gpt-4o' > harness/config/.env

# 2. 一句话启动
python main.py run
> 你好！
# Agent 已启动，开始对话
```

```python
# 开发者：替换模块 —— 自定义实现 + 默认实现混用
from harness.di import Harness
from harness.core.container import DIContainer
from harness.interfaces import InputAdapter, MemoryBackend, Sensor, ContextAssembler
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.input_adapter.cli_adapter import CliAdapter        # 默认
from harness.components.context_assembler.simple_assembler import SimpleAssembler  # 默认
from my_project.memory import PgMemory      # ← 你自己的 MemoryBackend 实现
from my_project.sensor import QualitySensor # ← 你自己的 Sensor 实现

container = DIContainer()
memory = PgMemory(dsn="postgresql://...")   # 替换默认 MdMemory
container.register(InputAdapter, CliAdapter())
container.register(MemoryBackend, memory)
container.register(Sensor, QualitySensor(memory=memory, llm=MinimalLLMAdapter()))
container.register(ContextAssembler, SimpleAssembler(max_history=50))

Harness.from_container(container, call_llm=MinimalLLMAdapter()).run()
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
  ┌───────┴──────────────────────────────┐
  │        Harness 微内核（不修改）        │
  │  DIContainer  LifecycleOrchestrator  │
  │  ToolRouter    HookManager           │
  └──────────────────────────────────────┘
```

**三阶段生命周期**：初始化（装配上下文）→ 多轮循环（LLM ↔ Tool）→ 结束（Sensor 评估 + 记忆沉淀）

---

## 文档导航

| 文档 | 面向 | 内容 |
|------|------|------|
| **[docs/FRAMEWORK.md](docs/FRAMEWORK.md)** | **开发者** | **完整框架指南：四大支柱、装配、生命周期、扩展** |
| [harness/components/](harness/components/) | 开发者 + Agent | 每个组件的接口说明、默认实现、替换示例 |
| [harness/interfaces/](harness/interfaces/) | 开发者 + Agent | Protocol 接口定义（AI 可读的契约文档） |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构参考 | 详细架构设计、数据流、配置模型 |
| [CORE_DEVELOPER_GUIDE.md](CORE_DEVELOPER_GUIDE.md) | 开发者 | 数据类型速查、LLM 集成、消息转换、测试 |
| [sdd/](sdd/) | 贡献者 | 软件设计文档 |
| [examples/](examples/) | 开发者 | 可运行示例 |
| [agents/](agents/) | 所有人 | 领域 Agent 展示（chat-web、trajectory-analyst） |

### 组件文档

| 组件 | README | 默认实现 |
|------|--------|---------|
| InputAdapter | [README](harness/components/input_adapter/README.md) | CliAdapter（stdin/stdout/stderr） |
| GuideProvider | [README](harness/components/guide_provider/README.md) | FileGuideProvider（AGENTS.md 解析） |
| ContextAssembler | [README](harness/components/context_assembler/README.md) | SimpleAssembler（滑动窗口拼接） |
| MemoryBackend | [README](harness/components/memory_backend/README.md) | MdMemory（Markdown 文件存储） |
| Sensor | [README](harness/components/sensor/README.md) | LoggingSensor（轨迹写入 episodic） |
| SystemToolProvider | [README](harness/components/tool/README.md) | DefaultSystemToolProvider（read/write/shell） |
| MCPAdapter | [README](harness/components/mcp_manager/README.md) | DefaultMCPAdapter（stdio + 两级转换） |

---

## 测试

```bash
pytest tests/ --ignore=tests/test_real_llm_trace.py -v    # 598 tests
```

---

## 路线图

**已完成（v1.0）** 🎉

- ✅ Batch-01~03：内核 + 接口 + MemoryBackend
- ✅ Batch-04~06：GuideProvider + ContextAssembler + Tool/MCP
- ✅ Batch-07~09：Sensor + InputAdapter + Hooks
- ✅ Batch-10~11：DI 装配 + 事件驱动适配器

**领域 Agent Showcase（showcase/agents 分支）** 🌐

- ✅ **chat-web** — WebSocket 网页聊天助手（[agents/chat-web/](agents/chat-web/)）
  - `InputAdapter` 替换：CliAdapter → WebSocketAdapter
  - 自定义消费级工具：web_search、weather
  - 自定义 emoji 渲染系统 + before_assemble Hook 约束
  - 完整测试套件（4 模块，1444 行）
- 🔜 **trajectory-analyst** — 轨迹分析元 Agent（计划中）

**计划中（v2.0）** 🔨

- 🔜 **Workflow 多 Agent 协作**：Agent 编写/发起 workflow，sub-agent 之间通过 InputAdapter 互相通信，支持 pipeline、fan-out、debate 等拓扑。
- 🔜 PipeAdapter：将 InputAdapter 从"人类 I/O"扩展到"Agent 间通信协议"

> 详见 [sdd/04-roadmap.md](sdd/04-roadmap.md) 与 [docs/FRAMEWORK.md §10](docs/FRAMEWORK.md#10-后期规划workflow-多-agent-协作)

---

## 许可证

MIT

---

> **Harness Agent Template** — 不是最强的 Agent 框架，但是**最容易裁剪、扩展和自举**的 Agent 框架模板。
