# Harness Agent Template

> 面向个人开发者与小型团队的**模块化 Agent 框架模板**。
>
> 核心理念：**框架只定义接口契约与编排流程，所有具体行为由你通过「实现接口 + 依赖注入」来自定义。**

---

## ✨ 特性

- **微内核架构** — 框架内核极度精简，仅负责生命周期编排、Hook 拦截与依赖注入
- **插件化扩展** — 记忆、工具、传感器等所有业务逻辑都是可替换的插件
- **三阶段生命周期** — 会话初始化 → 多轮对话循环 → 会话结束，控制流清晰固定
- **零依赖 LLM 适配器** — 内置 OpenAI 兼容 HTTP 客户端，自动从 `.env` / 环境变量读取配置
- **Duck Typing 组件** — 不强制继承基类，有对应方法就能工作
- **完整的类型与异常体系** — 16 个正式 dataclass + 8 个 Protocol + Hook 类型别名 + 完善的异常层次

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
python examples/hello_agent.py
```

输入任意内容即可与 Agent 对话，输入 `/exit` 退出。

### 3. 编写你的第一个 Agent

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.core.orchestrator import InputAdapter
from harness.interfaces.types import UserRequest
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 1. 实现输入适配器
class CliAdapter:
    def receive(self):
        text = input("> ")
        return UserRequest(text=text)

    def send(self, response):
        print(f"\n🤖 {response.text}\n")

# 2. 装配 DI 容器
container = DIContainer()
container.register(InputAdapter, CliAdapter())

# 3. 创建 LLM 适配器（零参数，自动读取 .env / 环境变量）
llm = MinimalLLMAdapter()

# 4. 启动
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
│   ├── interfaces/                   # 接口与类型定义（正式来源）
│   │   ├── __init__.py               # 导出 16 类型 + 9 接口
│   │   ├── types.py                  # 16 个正式 dataclass
│   │   ├── input_adapter.py          # InputAdapter Protocol
│   │   ├── guide_provider.py         # GuideProvider Protocol
│   │   ├── context_assembler.py      # ContextAssembler Protocol
│   │   ├── memory_backend.py         # MemoryBackend Protocol
│   │   ├── sensor.py                 # Sensor Protocol
│   │   ├── tool.py                   # Tool Protocol
│   │   ├── tool_registry.py          # ToolRegistry Protocol
│   │   ├── mcp_manager.py            # MCPManager Protocol
│   │   └── hook.py                   # Hook 类型别名 + HookContext
│   ├── adapters/                     # 外部系统适配器
│   │   └── llm_adapter.py            # MinimalLLMAdapter（OpenAI 兼容）
│   ├── config/                       # 配置模块
│   │   ├── loader.py                 # ConfigLoader + ProfileConfig
│   │   └── .env                      # API 配置
│   └── messaging/                    # 消息格式转换
│       ├── __init__.py               # 导出转换函数
│       └── builder.py                # Message↔dict + ToolDefinition→OpenAI
│   └── components/                    # 组件默认实现
│       ├── memory_backend/            # Batch-03
│       │   └── md_memory.py           # MdMemory — Markdown 文件存储
│       ├── guide_provider/            # Batch-04
│       │   └── file_guide_provider.py # FileGuideProvider — Markdown 解析
│       └── context_assembler/         # Batch-05
│           └── simple_assembler.py    # SimpleAssembler — 滑动窗口拼接
├── tests/                            # 测试套件
│   ├── test_container.py             # DI 容器测试
│   ├── test_config.py                # 配置加载器测试
│   ├── test_exceptions.py            # 异常体系测试
│   ├── test_orchestrator.py          # 编排器测试
│   ├── test_llm_adapter.py           # LLM 适配器测试
│   ├── test_messaging.py             # 消息转换层测试
│   ├── test_md_memory.py             # MdMemory 测试（Batch-03）
│   ├── test_guide_provider.py        # FileGuideProvider 测试（Batch-04）
│   ├── test_context_assembler.py     # SimpleAssembler 测试（Batch-05）
│   ├── test_black_box.py             # 黑盒集成测试（含真实 API）
│   └── test_real_llm_trace.py        # 真实 LLM 端到端 trace
├── examples/                         # 示例代码
│   └── hello_agent.py                # 最小可运行 Agent
├── sdd/                              # 软件设计文档
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
| **InputAdapter** | 输入输出适配：接收用户输入、发送 Agent 响应 | ✅ 是 |
| **GuideProvider** | 前馈控制：行动前提供身份定义与行为规则 | ❌ 否 |
| **ContextAssembler** | 上下文工程：将所有信息源组装成发给 LLM 的消息列表 | ❌ 否（强烈推荐） |
| **MemoryBackend** | 记忆层：跨会话持久化存储与检索 | ❌ 否 |
| **ToolRegistry** | 工具注册与调度执行 | ❌ 否 |
| **Sensor** | 反馈控制：会话结束时评估轨迹并沉淀知识 | ❌ 否 |
| **MCPManager** | MCP 配置入口：将用户 MCP 配置转换为框架 Tool | ❌ 否 |
| **Hook** | 生命周期拦截：在 11 个关键节点插入自定义逻辑 | ❌ 否 |

> 详细接口契约与数据结构设计，请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 🔄 生命周期

```
阶段一：会话初始化（整个会话只执行一次）
  InputAdapter.receive() → GuideProvider → MemoryBackend.search()
  → ToolRegistry.list_tools() → 组装 AssemblyContext

阶段二：多轮对话循环
  外层循环（每轮用户输入）:
    ContextAssembler.assemble() → before_llm_call Hook
    → 内层循环（tool 连续调用）: LLM → ToolRegistry.execute()
    → InputAdapter.send() → InputAdapter.receive()

阶段三：会话结束（整个会话只执行一次）
  组装 Trajectory → Sensor.sense() → 清理
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
- ✅ **Batch-02**：正式接口定义（16 个 dataclass + 8 个 Protocol + 1 个 Hook 别名）
- ✅ **Batch-02-1**：类型迁移（`_Minimal*` → 正式类型，删除桥接方法，补齐测试覆盖）

- ✅ **Batch-03**：MemoryBackend 默认实现（MdMemory — Markdown 文件存储）
- ✅ **Batch-04**：GuideProvider 默认实现（FileGuideProvider — Markdown 解析）
- ✅ **Batch-05**：ContextAssembler 默认实现（SimpleAssembler — 滑动窗口 + 拼接）

即将实现：

- ⏳ Batch-06：MCPManager 与 Tool 体系
- ⏳ Batch-07：Sensor 默认实现
- ⏳ Batch-09：Hook 生命周期拦截
- ⏳ Batch-10：完整 DI 装配方案

> 完整路线与每个 batch 的设计文档，见 [sdd/batches/](sdd/batches/)。

---

## 📄 许可证

MIT License

---

> **Harness Agent Template** — 不是最强的 Agent 框架，但是**最容易裁剪和扩展**的 Agent 框架模板。
