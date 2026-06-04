# Harness Agent Template

> 面向个人开发者与小型团队的**模块化 Agent 框架模板**。
>
> 核心理念：**框架只定义接口契约与编排流程，所有具体行为由你通过「实现接口 + 依赖注入」来自定义。**
>
> **版本: 1.0**

---

## ✨ 特性

- **微内核架构** — 框架内核极度精简，仅负责生命周期编排、Hook 拦截与依赖注入
- **插件化扩展** — 记忆、工具、传感器等所有业务逻辑都是可替换的插件
- **三阶段生命周期** — 会话初始化 → 多轮对话循环 → 会话结束，控制流清晰固定
- **零依赖 LLM 适配器** — 内置 OpenAI 兼容 HTTP 客户端，自动从 `.env` / 环境变量读取配置
- **Duck Typing 组件** — 不强制继承基类，有对应方法就能工作
- **事件驱动输出** — InputAdapter `send()` 接收独立事件对象，实现前后台分离（stdout/stderr 独立通道）
- **完整的类型与异常体系** — 23 个 dataclass 类型 + 10 个 Protocol/Hook 接口 + 完善的异常层次

---

## 🚀 快速开始

### 1. 配置 LLM

在 `harness/config/.env` 中配置 API 信息：

```ini
base_url = https://api.openai.com/v1
api-key = sk-your-key-here
model = gpt-4o
```

或使用环境变量：

```bash
export OPENAI_API_KEY="sk-your-key-here"
export LLM_MODEL="gpt-4o"
export LLM_BASE_URL="https://api.openai.com/v1"
```

### 2. 运行最小示例

```bash
# Python API 方式
python examples/minimal_agent.py

# YAML 装配方式（batch-10 新增）
python main.py run --config profiles/coding-assistant/harness.yaml
```

输入任意内容即可与 Agent 对话，输入 `/exit` 退出。

### 3. 创建你的第一个 Agent 项目

```bash
# 从模板生成项目
python main.py init --profile coding-assistant my-agent
cd my-agent

# 编辑 harness.yaml 按需替换组件，然后启动
python ../main.py run
```

### 4. 编写自定义 Agent（Python API）

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

container = DIContainer()
memory = MdMemory(path="./memory")
container.register(InputAdapter, CliAdapter())
container.register(MemoryBackend, memory)
container.register(Sensor, LoggingSensor(memory=memory))
container.register(ContextAssembler, SimpleAssembler(max_history=50))
container.register(GuideProvider, FileGuideProvider(paths=["AGENTS.md"]))

llm = MinimalLLMAdapter()  # auto-reads .env
Harness.from_container(container, call_llm=llm).run()
```

---

## 📁 项目结构

```
harness_agent/
├── harness/                          # 框架核心
│   ├── __init__.py                   # 导出 Harness 入口类
│   ├── di.py                         # 装配入口（from_container + run）
│   ├── core/                         # 内核
│   │   ├── container.py              # DIContainer（注册/解析/查询）
│   │   ├── orchestrator.py           # LifecycleOrchestrator（三阶段编排）
│   │   ├── types.py                  # ⚠️ DEPRECATED — 旧类型定义，已废弃
│   │   ├── exceptions.py             # 异常体系
│   │   ├── config.py                 # 配置模型
│   │   └── llm_adapter.py            # re-export 包装（指向 adapters/）
│   │   ├── tool_router.py              # ToolRouter（框架内部，合并 Provider 路由）
│   ├── interfaces/                   # 接口与类型定义（正式来源）
│   │   ├── __init__.py               # 导出 17 类型 + 10 接口
│   │   ├── types.py                  # 23 个正式 dataclass（含事件类型）
│   │   ├── input_adapter.py          # InputAdapter Protocol
│   │   ├── guide_provider.py         # GuideProvider Protocol
│   │   ├── context_assembler.py      # ContextAssembler Protocol
│   │   ├── memory_backend.py         # MemoryBackend Protocol
│   │   ├── sensor.py                 # Sensor Protocol
│   │   ├── tool.py                   # Tool Protocol
│   │   ├── system_tool_provider.py   # SystemToolProvider Protocol
│   │   ├── mcp_adapter.py            # MCPAdapter Protocol
│   │   ├── mcp_handler.py            # MCPHandler Protocol
│   │   └── hook.py                   # Hook 类型别名 + HookContext
│   ├── adapters/                     # 外部系统适配器
│   │   └── llm_adapter.py            # MinimalLLMAdapter（OpenAI 兼容）
│   ├── config/                       # 配置模块
│   │   ├── loader.py                 # ConfigLoader（TOML profile 加载）
│   │   └── yaml_assembler.py         # YamlAssembler（YAML DI 装配）
│   ├── hooks/                        # Hook 系统（Batch-09）
│   │   ├── events.py                 # 11 个生命周期事件常量
│   │   └── hook_manager.py           # HookManager（注册/注销/触发）
│   ├── messaging/                    # 消息格式转换
│   │   ├── __init__.py               # 导出转换函数
│   │   └── builder.py                # Message↔dict + ToolDefinition→OpenAI
│   └── components/                    # 组件默认实现
│       ├── memory_backend/            # Batch-03
│       │   └── md_memory.py           # MdMemory — Markdown 文件存储
│       ├── guide_provider/            # Batch-04
│       │   └── file_guide_provider.py # FileGuideProvider — Markdown 解析
│       ├── context_assembler/         # Batch-05
│       │   └── simple_assembler.py    # SimpleAssembler — 滑动窗口拼接
│       ├── tool/                      # Batch-06
│       │   ├── base.py               # BaseTool ABC
│       │   ├── inline_tool.py        # @inline_tool 装饰器
│       │   ├── system_tools.py       # ReadFileTool / WriteFileTool / ShellTool
│       │   └── default_system_tool_provider.py
│       ├── mcp_manager/              # Batch-06
│       │   ├── mcp_client.py         # MCPClient (JSON-RPC stdio)
│       │   └── default_mcp_adapter.py # DefaultMCPAdapter
│       ├── sensor/                   # Batch-07
│       │   └── logging_sensor.py      # LoggingSensor — 轨迹写入 episodic 记忆
│       └── input_adapter/            # Batch-08 + Batch-11
│           └── cli_adapter.py         # CliAdapter — 事件驱动 stdin/stdout/stderr
├── tests/                            # 测试套件（598 tests）
│   ├── test_container.py             # DI 容器测试
│   ├── test_config.py                # 配置加载器测试
│   ├── test_exceptions.py            # 异常体系测试
│   ├── test_orchestrator.py          # 编排器测试
│   ├── test_llm_adapter.py           # LLM 适配器测试
│   ├── test_messaging.py             # 消息转换层测试
│   ├── test_md_memory.py             # MdMemory 测试
│   ├── test_guide_provider.py        # FileGuideProvider 测试
│   ├── test_context_assembler.py     # SimpleAssembler 测试
│   ├── test_sensor.py                # LoggingSensor 测试
│   ├── test_input_adapter.py         # CliAdapter 测试
│   ├── test_e2e_sensor_adapter.py    # Sensor + CliAdapter E2E 测试
│   ├── test_tool_router.py           # ToolRouter 测试
│   ├── test_system_tool_provider.py  # SystemToolProvider 测试
│   ├── test_mcp_adapter.py           # MCPAdapter 测试
│   ├── test_e2e_tool_flow.py         # Tool E2E 测试
│   ├── test_hooks.py                 # Hook 系统测试（Batch-09）
│   ├── test_yaml_assembler.py        # YamlAssembler 测试（Batch-10）
│   ├── test_e2e_assembly.py          # 端到端装配测试（Batch-10）
│   ├── test_black_box.py             # 黑盒集成测试
│   └── test_real_llm_trace.py        # 真实 LLM 端到端 trace
├── main.py                           # CLI 入口：harness init / run（Batch-10）
├── profiles/                         # 领域模板（Batch-10）
│   └── coding-assistant/
│       ├── profile.toml              # 模板元数据
│       ├── harness.yaml              # DI 装配声明
│       ├── AGENTS.md                 # Agent 指导
│       └── README.md                 # 使用说明
├── examples/                         # 示例代码
│   ├── minimal_agent.py              # 最小多轮对话 Agent（Python API）
│   └── AGENTS.md                     # 示例指导文件
├── sdd/                              # 软件设计文档（SDD）
│   ├── 01-architecture.md            # 架构总览
│   ├── 02-interfaces.md              # 接口设计
│   ├── 03-project-structure.md       # 项目结构
│   ├── 04-roadmap.md                 # 路线图
│   ├── 05-conventions.md             # 编码约定
│   ├── 06-acceptance.md              # 验收标准
│   └── batches/                      # 分批实现计划
├── ARCHITECTURE.md                   # 完整架构设计文档
└── CORE_DEVELOPER_GUIDE.md           # 核心开发者指南
```

---

## 🏗️ 核心组件

| 组件 | 职责 | 必需？ |
|------|------|--------|
| **InputAdapter** | 输入输出适配：接收用户输入，以事件驱动方式推送 Agent 响应流 | ✅ 是 |
| **GuideProvider** | 前馈控制：行动前提供身份定义与行为规则 | ❌ 否 |
| **ContextAssembler** | 上下文工程：将所有信息源组装成发给 LLM 的消息列表 | ❌ 否（强烈推荐） |
| **MemoryBackend** | 记忆层：跨会话持久化存储与检索 | ❌ 否 |
| **ToolRouter** | 工具路由：合并 SystemToolProvider 和 MCPAdapter，按名分发执行（框架内部） | 框架内部 |
| **SystemToolProvider** | 系统工具提供者：管理本地 Tool 集合 | ❌ 否 |
| **MCPAdapter** | MCP 适配层：消费外部 MCP Server，经转换后暴露工具 | ❌ 否 |
| **Sensor** | 反馈控制：会话结束时评估轨迹并沉淀知识 | ❌ 否 |
| **Hook** | 生命周期拦截：在 11 个关键节点插入自定义逻辑 | ❌ 否 |

> 详细接口契约与数据结构设计，请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 🔄 生命周期

```
阶段一：会话初始化（整个会话只执行一次）
  InputAdapter.receive() → GuideProvider → MemoryBackend.search()
  → ToolRouter 注册 SystemToolProvider + MCPAdapter → 组装 AssemblyContext

阶段二：多轮对话循环
  外层循环（每轮用户输入）:
    ContextAssembler.assemble() → before_llm_call Hook
    → 内层循环（tool 连续调用）: LLM → ToolRouter.execute()
    → InputAdapter.send(事件流) → InputAdapter.receive()

阶段三：会话结束（整个会话只执行一次）
  组装 Trajectory → on_session_end Hook → Sensor.sense()
  → after_sensor Hook → ToolRouter.shutdown() → 清理
```

> 完整数据流与调用时机，请参阅 [CORE_DEVELOPER_GUIDE.md](CORE_DEVELOPER_GUIDE.md)。

---

## 🧪 运行测试

```bash
# 运行所有测试（跳过需要真实 API Key 的测试）
pytest tests/ --ignore=tests/test_real_llm_trace.py -v

# 运行单个测试文件
pytest tests/test_orchestrator.py -v
pytest tests/test_container.py -v
pytest tests/test_config.py -v
pytest tests/test_messaging.py -v

# 真实 LLM 集成测试（需要配置 API Key）
python tests/test_real_llm_trace.py
```

---

## 📖 文档导航

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 完整架构设计：范式、组件、接口契约、数据流、配置与装配 |
| [CORE_DEVELOPER_GUIDE.md](CORE_DEVELOPER_GUIDE.md) | 开发者指南：快速开始、组件实现、数据结构速查、测试指南、FAQ |
| [sdd/01-architecture.md](sdd/01-architecture.md) | SDD 架构总览 |
| [sdd/02-interfaces.md](sdd/02-interfaces.md) | 正式接口与类型定义 |
| [sdd/04-roadmap.md](sdd/04-roadmap.md) | 项目路线图与分批计划 |

---

## 🛣️ 路线图

当前已完成的批次：

- ✅ **Batch-01**：内核 MVP（DI 容器、三阶段编排器、配置加载器、LLM 适配器、异常体系、消息构造）
- ✅ **Batch-02**：正式接口定义（17 个 dataclass + 10 个 Protocol/Hook 接口 + 1 个 Hook 别名）
- ✅ **Batch-02-1**：类型迁移（`_Minimal*` → 正式类型，删除桥接方法，补齐测试覆盖）
- ✅ **Batch-03**：MemoryBackend 默认实现（MdMemory — Markdown 文件存储）
- ✅ **Batch-04**：GuideProvider 默认实现（FileGuideProvider — Markdown 解析）
- ✅ **Batch-05**：ContextAssembler 默认实现（SimpleAssembler — 滑动窗口 + 拼接）
- ✅ **Batch-06**：Tool 与 MCP 体系（ToolRouter + SystemToolProvider + MCPAdapter + MCPHandler）
- ✅ **Batch-07**：Sensor 默认实现（LoggingSensor — 轨迹持久化到 episodic 命名空间）
- ✅ **Batch-08**：InputAdapter 默认实现（CliAdapter — stdin/stdout 命令行交互）
- ✅ **Batch-09**：Hook 生命周期拦截系统（11 个事件、HookManager、Orchestrator 集成）
- ✅ **Batch-10**：DI 装配集成（YAML 装配、CLI 入口、Profile 模板、端到端测试）
- ✅ **Batch-11**：事件驱动适配器（InputAdapter `send()` 改为事件驱动，前后台分离，5 种事件类型）

🎉 **全部 11 个批次已完成！** 598 个测试全部通过。

> 完整路线与每个 batch 的设计文档，见 [sdd/batches/](sdd/batches/)。

---

## 📄 许可证

MIT License

---

> **Harness Agent Template** — 不是最强的 Agent 框架，但是**最容易裁剪和扩展**的 Agent 框架模板。
