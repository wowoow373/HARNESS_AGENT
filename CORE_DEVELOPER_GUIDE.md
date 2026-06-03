# Harness Core — Developer Guide

> **面向人群**：需要在 Harness 框架上添加模块（batch）的开发者，以及使用 Core 装配自己 Agent 的用户。
>
> **阅读前提**：了解 Python 类型标注和依赖注入的基本概念。
>
> **读完这个文档，你就能在不读 Core 源码的情况下开始写自己的组件。**

---

## 目录

1. [概述](#1-概述)
2. [一分钟快速开始](#2-一分钟快速开始)
3. [核心架构](#3-核心架构)
4. [组件体系：你要实现的接口](#4-组件体系你要实现的接口)
5. [数据类型速查](#5-数据类型速查)
6. [装配与启动](#6-装配与启动)
7. [LLM 集成](#7-llm-集成)
8. [消息转换层](#8-消息转换层)
9. [配置系统](#9-配置系统)
10. [异常处理](#10-异常处理)
11. [添加新 Batch 的步骤](#11-添加新-batch-的步骤)
12. [测试指南](#12-测试指南)
13. [常见问题](#13-常见问题)

---

## 1. 概述

Harness 是一个 **模块化 Agent 框架**。它提供：

1. **按固定顺序调度组件** — 三阶段生命周期（初始化 → 对话循环 → 结束）
2. **管理组件注册与解析** — 预构造实例的 DI 容器
3. **开箱即用的 LLM 适配器** — 零依赖 OpenAI 兼容 HTTP 客户端
4. **完整的数据类型体系** — 16 个 dataclass + 8 个 Protocol + Hook 类型别名
5. **消息格式转换层** — 框架类型 ↔ OpenAI dict 双向转换

框架**不包含**任何业务逻辑。你的 Agent 具体做什么（如何压缩上下文、如何评估质量、如何存储记忆），完全由你实现的组件决定。

### 文件地图

```
harness/
├── __init__.py              # 导出 Harness 入口类
├── di.py                    # Harness 装配入口（from_container + run）
├── core/                    # 内核
│   ├── container.py         # DIContainer（注册/解析/查询）
│   ├── orchestrator.py      # LifecycleOrchestrator（三阶段编排）
│   ├── exceptions.py        # 异常体系
│   ├── config.py            # 配置模型
│   ├── types.py             # ⚠️ DEPRECATED — 旧类型，已废弃
│   └── llm_adapter.py       # re-export 包装（→ adapters/）
├── interfaces/              # 接口与类型定义（正式来源 ★）
│   ├── __init__.py          # 导出所有类型和接口
│   ├── types.py             # 16 个正式 dataclass
│   ├── input_adapter.py     # InputAdapter Protocol
│   ├── guide_provider.py    # GuideProvider Protocol
│   ├── context_assembler.py # ContextAssembler Protocol
│   ├── memory_backend.py    # MemoryBackend Protocol
│   ├── sensor.py            # Sensor Protocol
│   ├── tool.py              # Tool Protocol
│   ├── tool_registry.py     # ToolRegistry Protocol
│   ├── mcp_manager.py       # MCPManager Protocol
│   └── hook.py              # Hook 类型别名 + HookContext
├── adapters/                # 外部系统适配器
│   └── llm_adapter.py       # MinimalLLMAdapter
├── config/                  # 配置模块
│   ├── loader.py            # ConfigLoader + ProfileConfig
│   └── .env                 # API 配置
└── messaging/               # 消息格式转换
    ├── __init__.py           # 导出转换函数
    └── builder.py            # Message↔dict + ToolDefinition→OpenAI
```

---

## 2. 一分钟快速开始

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.core.orchestrator import InputAdapter
from harness.interfaces.types import UserRequest
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 1. 创建容器 + 注册组件
container = DIContainer()

class CliAdapter:
    def receive(self):
        text = input("> ")
        return UserRequest(text=text)
    def send(self, response):
        print(response.text)

container.register(InputAdapter, CliAdapter())

# 2. 创建 LLM 适配器（零参数 — 自动从 .env / 环境变量读取配置）
llm = MinimalLLMAdapter()

# 3. 启动
harness = Harness.from_container(container, call_llm=llm)
harness.run()
```

这就是一个**能跑起来**的 Agent。它只有最基本的输入输出和 LLM 调用，没有记忆、没有前馈指导、没有工具。接下来逐个添加组件。

---

## 3. 核心架构

### 3.1 控制流：三阶段编排

编排器 `LifecycleOrchestrator` 在 `run()` 时执行三个固定阶段，**你不可以跳过或改变它们的顺序**：

```
阶段一：会话初始化 (Phase Init) — 整个会话只执行一次
┌──────────────────────────────────────────────────┐
│ 1. InputAdapter.receive()      → UserRequest    │
│ 2. 检查退出信号（空输入 / /exit / metadata.exit） │
│    → 如果是退出：跳转到阶段三                     │
│ 3. GuideProvider.get_guides()  → GuidesBundle   │
│ 4. MemoryBackend.search()      → List[MemoryItem]│
│ 5. ToolRegistry.list_tools()   → List[ToolDef]  │
│ 6. 组装 AssemblyContext                          │
└──────────────────────────────────────────────────┘
                     ↓
阶段二：多轮对话循环 (Phase Loop)
┌── 外层循环（每轮用户输入）──────────────────────┐
│  6. ContextAssembler.assemble() → List[Message] │
│                                                  │
│  ┌─ 内层循环（tool 连续调用）─────────────────┐ │
│  │ 7. call_llm(messages, tools) → Response    │ │
│  │ 8. 如果有 tool_uses:                       │ │
│  │    → ToolRegistry.execute()                │ │
│  │    → 结果追加到 messages                   │ │
│  │    → 回到步骤 7（继续内层循环）             │ │
│  │ 9. 如果有 text:                            │ │
│  │    → InputAdapter.send()                   │ │
│  │    → 跳出内层循环                           │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│ 10. InputAdapter.receive() → 更新上下文          │
│ 11. 回到步骤 6（如果未退出）                     │
└──────────────────────────────────────────────────┘
                     ↓
阶段三：会话结束 (Phase End) — 整个会话只执行一次
┌──────────────────────────────────────────────────┐
│ 12. 组装 Trajectory（完整执行轨迹）               │
│ 13. Sensor.sense(trajectory)                     │
│ 14. 清理内部状态                                  │
└──────────────────────────────────────────────────┘
```

### 3.2 关键约束

| 规则 | 说明 |
|------|------|
| **内层循环不走 ContextAssembler** | tool 结果直接追加到 message list，不回退到 assemble 步骤 |
| **LLM 单次响应可同时含 text 和 tool_uses** | 编排器分别处理两者，互不排斥 |
| **多 Tool 串行执行** | 按 LLM 返回的顺序逐个执行 |
| **GuidesBundle 和 tools 在阶段一缓存** | 不会每轮都重新获取 |
| **阶段三在 finally 块中执行** | 即使异常退出，Sensor 也会收到轨迹 |

### 3.3 组件必需性

| 组件 | 必需？ | 缺失时的行为 |
|------|--------|------------|
| InputAdapter | **必需** | `Harness.from_container()` 直接抛异常 |
| call_llm | **生产必需** | None 时跳过所有 LLM 调用，仅用于调试 |
| GuideProvider | 可选 | 跳过，GuidesBundle 为空 |
| MemoryBackend | 可选 | 跳过，检索不到记忆 |
| ContextAssembler | 可选 | 使用内置降级组装（仅拼 system + user） |
| ToolRegistry | 可选 | tool_use 响应记录错误，不会崩溃 |
| Sensor | 可选 | 跳过，不保存轨迹 |

---

## 4. 组件体系：你要实现的接口

每个组件**不需要显式继承任何基类**。编排器通过 **duck typing** 调用它们 —— 只要你注册的对象有对应的方法，就能工作。

> 接口类型定义在 `harness/interfaces/` 下，以 Python `Protocol` 类提供。它们同时作为 `container.register()` 的类型 key。你实现的类不需要显式继承这些 Protocol，只需方法签名兼容即可。

### 4.1 InputAdapter（必需）

输入输出适配器，框架与外部世界的唯一通道。

```python
class MyAdapter:
    def receive(self) -> "UserRequest":
        """获取用户下一轮输入。
        返回 UserRequest(text="") 或 metadata={"exit": True} 表示退出。"""
        ...

    def send(self, response: "Response") -> None:
        """将 Agent 响应发送给用户。"""
        ...
```

| 方法 | 调用时机 | 返回值含义 |
|------|---------|-----------|
| `receive()` | 阶段一入口 + 每轮外层循环末尾 | `text=""` → 退出；`text="/exit"` → 退出；正常文本 → 继续 |
| `send(response)` | 每次 LLM 返回 text 时 | 无返回值 |

**退出信号**（`_should_exit` 的判断逻辑）：
- `text` 为空字符串或仅空白字符
- `text` 匹配 `/exit`
- `metadata` 中包含 `"exit": True`

> **注意**：退出检查在**阶段一 receive() 之后立即执行**，也在**每轮外层循环末尾**执行。第一轮用户输入 `/exit` 不会触发 LLM 调用。

### 4.2 GuideProvider（可选）

前馈指导提供者，在会话开始时提供 Agent 的身份定义和行为规则。

```python
class MyGuideProvider:
    def get_guides(self, ctx: "AssemblyContext") -> "GuidesBundle":
        """根据上下文返回指导信息。只调用一次，结果被缓存。"""
        ...
```

返回值 `GuidesBundle` 包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `identity` | `str` | 核心身份（如 "You are a coding assistant..."） |
| `capabilities` | `List[str]` | 能力清单 |
| `rules` | `List[str]` | 行为规则 |
| `constraints` | `List[str]` | 硬约束 |
| `examples` | `List[Example]` | 少样本示例，每个 `Example` 含 `input` 和 `output` str 字段 |

### 4.3 MemoryBackend（可选）

跨会话的持久化存储与检索。

```python
class MyMemory:
    def read(self, key: str, namespace: str) -> Optional[Any]:
        """读取指定 key 的记忆。"""
        ...

    def write(self, key: str, value: Any, namespace: str) -> None:
        """写入一条记忆。"""
        ...

    def search(self, query: str, namespace: str, limit: int = 10) -> list:
        """按 query 检索相关记忆。返回 list of dict。"""
        ...

    def list_namespaces(self) -> List[str]:
        """列出所有 namespace。"""
        ...
```

调用时机：
- `search()` → 阶段一，查询结果填入 `AssemblyContext.memories`
- `write()` → 阶段三，由 Sensor 调用

### 4.4 ContextAssembler（可选）

上下文组装器，将框架的所有信息源拼成发给 LLM 的消息列表。

```python
from harness.interfaces.types import Message

class MyAssembler:
    def assemble(self, ctx: "AssemblyContext") -> list:
        """将 AssemblyContext 转为消息列表。
        返回可以是 List[Message] 或 List[dict]，编排器会自动转换。"""
        ...
```

输入 `AssemblyContext` 包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_request` | `Optional[UserRequest]` | 当前用户请求 |
| `guides` | `Optional[GuidesBundle]` | GuidesBundle（阶段一缓存） |
| `available_tools` | `List[ToolDefinition]` | 可用工具定义列表 |
| `history` | `List[Message]` | 当前会话的对话历史 |
| `memories` | `List[MemoryItem]` | 从 MemoryBackend 检索的记忆 |
| `system_state` | `SystemState` | 系统当前状态 |
| `metadata` | `Dict[str, Any]` | 扩展字段 |

> ⚠️ **关键提醒**：`ctx.history` 现在是 `List[Message]`（不再是 dict 列表）。如果你的 `assemble()` 需要返回 dict 列表，可以使用 `harness.messaging` 中的 `message_to_dict` 做转换。

完整示例：

```python
from harness.interfaces.types import Message
from harness.messaging import message_to_dict

class MyAssembler:
    def assemble(self, ctx):
        messages = []
        # 1. system identity
        if ctx.guides and ctx.guides.identity:
            messages.append(Message(
                role="system",
                content=ctx.guides.identity,
            ))
        # 2. 对话历史（ctx.history 现在就是 List[Message]，可直接用）
        messages.extend(ctx.history)
        # 3. 当前用户输入
        if ctx.user_request and ctx.user_request.text:
            messages.append(Message(
                role="user",
                content=ctx.user_request.text,
            ))
        return messages
```

> 返回 `List[Message]` 还是 `List[dict]` 都可以 —— 编排器在调用 LLM 前会通过 `messages_to_dicts()` 统一转换。

**如果你不注册 ContextAssembler**，框架使用内置降级逻辑（`_fallback_assemble`）。降级逻辑**有意设计为极简**：只拼接 system identity + 当前 user 输入，不包含对话历史、memories、tools。它仅用于调试和验证编排流程，**生产环境强烈建议注册自己实现的 ContextAssembler**。

### 4.5 ToolRegistry（可选）

管理所有 Tool 的注册与调度执行。

```python
from harness.interfaces.types import ToolDefinition

class MyToolRegistry:
    def register(self, tool) -> None:
        """注册一个工具实例。在框架初始化时调用。"""
        ...

    def list_tools(self) -> List[ToolDefinition]:
        """返回所有可用工具的定义列表。"""
        ...

    def execute(self, name: str, args: Dict[str, Any]):
        """执行指定工具。返回的对象应有 success/content/error 属性。"""
        ...
```

**`execute()` 返回值约定**（duck typing）：

| 属性 | 类型 | 说明 |
|------|------|------|
| `success` | `bool` | 执行是否成功 |
| `content` | `Any` | 成功时的结果 |
| `error` | `Optional[str]` | 失败时的错误信息 |

### 4.6 Sensor（可选）

反馈控制组件，在会话结束时评估完整轨迹并沉淀知识到 MemoryBackend。

```python
from harness.interfaces.types import Trajectory

class MySensor:
    def sense(self, trajectory: Trajectory) -> None:
        """读取完整执行轨迹，按自定义规则评估。"""
        # 通过构造函数注入的 MemoryBackend 写入知识
        self.memory.write(...)
```

**Sensor 通过构造函数注入获得 MemoryBackend 引用**，而不是从容器 resolve：

```python
memory = MyMemory()
sensor = MySensor(memory=memory)

container.register(MemoryBackend, memory)
container.register(Sensor, sensor)
```

---

## 5. 数据类型速查

所有正式类型定义在 [harness/interfaces/types.py](harness/interfaces/types.py)，共 16 个 dataclass。旧的 `harness.core.types` 已废弃（import 时触发 `DeprecationWarning`）。

### 5.1 UserRequest

InputAdapter.receive() 的返回值。

```python
@dataclass
class UserRequest:
    text: str = ""                          # 用户输入文本（空字符串表示退出）
    attachments: List[Attachment] = []      # 附件列表
    context: Dict[str, Any] = {}            # 附加上下文
    system_state: SystemState = SystemState() # 系统快照
    session_id: str = ""                    # 会话标识
    metadata: Dict[str, Any] = {}           # 扩展元数据（如 {"exit": True}）
```

### 5.2 Response

LLM 调用的返回值。

```python
@dataclass
class Response:
    text: Optional[str] = None              # LLM 文本输出
    thinking: Optional[str] = None          # 推理过程（DeepSeek/OpenAI o-series）
    tool_uses: List[ToolCall] = []          # 工具调用列表
    stop_reason: str = "end_turn"           # "end_turn" | "tool_use" | ...
```

### 5.3 ToolCall / ToolCallFunction

```python
@dataclass
class ToolCallFunction:
    name: str = ""                          # 函数名
    arguments: str = "{}"                   # JSON 编码的参数字符串

@dataclass
class ToolCall:
    id: str = ""                            # tool call 唯一标识
    type: str = "function"                  # 固定为 "function"
    function: ToolCallFunction              # 函数名与参数
```

> **注意**：正式类型的 `ToolCall` 没有 `parse_arguments()` 方法。请使用 `json.loads(tc.function.arguments)`。

### 5.4 ToolCallRecord

单次工具调用的完整执行记录（含时间戳）。

```python
@dataclass
class ToolCallRecord:
    tool_name: str = ""                     # 工具名称
    arguments: Dict[str, Any] = {}          # 调用参数
    result: Any = None                      # 执行结果
    started_at: float = 0.0                 # 开始时间戳
    finished_at: float = 0.0                # 完成时间戳
    error: Optional[str] = None             # 失败时的错误信息
```

### 5.5 ToolDefinition

用于 LLM tool schema 生成的工具元信息。

```python
@dataclass
class ToolDefinition:
    name: str = ""                          # 工具名称
    description: str = ""                   # 工具描述
    parameters: Dict[str, Any] = {}         # JSON Schema 格式的参数定义
```

### 5.6 GuidesBundle

GuideProvider.get_guides() 的返回值。

```python
@dataclass
class GuidesBundle:
    identity: str = ""                      # 核心身份
    capabilities: List[str] = []            # 能力清单
    rules: List[str] = []                   # 行为规则
    constraints: List[str] = []             # 硬约束
    examples: List[Example] = []            # 少样本示例
```

### 5.7 Example

```python
@dataclass
class Example:
    input: str = ""                         # 示例输入
    output: str = ""                        # 示例预期输出
```

### 5.8 Message

对话消息单元。

```python
@dataclass
class Message:
    role: str = "user"                      # "system" | "user" | "assistant" | "tool"
    content: str = ""                       # 消息文本内容
    tool_call_id: Optional[str] = None      # 当 role="tool" 时关联的 tool_use 标识
```

### 5.9 MemoryItem

从 MemoryBackend 检索出的记忆项。

```python
@dataclass
class MemoryItem:
    key: str = ""                           # 记忆键
    value: Any = None                       # 记忆值
    namespace: str = ""                     # 命名空间
    timestamp: float = 0.0                  # 写入时间戳
    metadata: Dict[str, Any] = {}           # 扩展元数据
```

### 5.10 SystemState

系统当前状态，贯穿整个生命周期。

```python
@dataclass
class SystemState:
    phase: str = "init"                     # 当前阶段
    session_id: str = ""                    # 会话标识
    run_mode: str = "normal"                # "normal" | "debug" | "dry_run"
    metadata: Dict[str, Any] = {}           # 扩展桶
```

### 5.11 AssemblyContext

ContextAssembler.assemble() 的输入端。

```python
@dataclass
class AssemblyContext:
    user_request: Optional[UserRequest] = None
    guides: Optional[GuidesBundle] = None
    available_tools: List[ToolDefinition] = []
    history: List[Message] = []
    memories: List[MemoryItem] = []
    system_state: SystemState = SystemState()
    metadata: Dict[str, Any] = {}
```

### 5.12 Trajectory

Sensor.sense() 的输入端。

```python
@dataclass
class Trajectory:
    user_request: Optional[UserRequest] = None
    history: List[Message] = []             # 完整对话历史
    tool_calls: List[ToolCallRecord] = []   # 工具调用执行记录
    final_output: str = ""                  # Agent 最终输出
    execution_time: float = 0.0             # 执行耗时（秒）
    system_state: SystemState = SystemState()
    metadata: Dict[str, Any] = {}
```

### 5.13 数据流向总览

```
InputAdapter.receive() 产出      → UserRequest
GuideProvider.get_guides() 产出  → GuidesBundle（含 Example）
ContextAssembler.assemble() 消费 ← AssemblyContext
  (内含: UserRequest, GuidesBundle, ToolDefinition[], Message[], MemoryItem[], SystemState)
call_llm 产出                     → Response（含 ToolCall[]）
  ToolRegistry.execute() 记录     → ToolCallRecord
Sensor.sense() 消费              ← Trajectory
  (内含: UserRequest, Message[], ToolCallRecord[], SystemState)
```

---

## 6. 装配与启动

### 6.1 注册模式

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.core.orchestrator import (
    InputAdapter, GuideProvider, ContextAssembler,
    MemoryBackend, Sensor,
)
from harness.adapters.llm_adapter import MinimalLLMAdapter

container = DIContainer()

# 创建实例 → 注册到容器
adapter = CliAdapter()
container.register(InputAdapter, adapter)

guide = FileGuideProvider("AGENTS.md")
container.register(GuideProvider, guide)

# 同一个实例可以注册到多个接口（如 MemoryBackend 被多处共享）
memory = MdMemory("./memory")
container.register(MemoryBackend, memory)
assembler = SimpleAssembler(memory=memory)
container.register(ContextAssembler, assembler)
sensor = LoggingSensor(memory=memory)
container.register(Sensor, sensor)

# 启动
llm = MinimalLLMAdapter(model="gpt-4o")
harness = Harness.from_container(container, call_llm=llm)
harness.run()
```

### 6.2 DIContainer API

| 方法 | 说明 |
|------|------|
| `register(interface, instance)` | 注册组件。重复注册抛 `DuplicateRegistrationError` |
| `resolve(interface)` | 按类型解析，未注册抛 `ComponentNotRegisteredError` |
| `is_registered(interface)` | 检查是否已注册 |
| `list_registered()` | 返回注册表副本（修改不影响容器） |

### 6.3 装配约束

- `InputAdapter` **必须注册**，否则 `Harness.from_container()` 直接抛异常
- `call_llm` 生产环境**必须传入**，None 仅用于调试
- 其他组件全部可选，缺失时框架跳过对应步骤并记录 WARNING
- 同一个实例可以注册到多个接口类型（支持共享场景）

---

## 7. LLM 集成

### 7.1 使用内置 MinimalLLMAdapter

框架自带零依赖的 OpenAI 兼容适配器。所有参数均可选 —— 不传时自动从环境变量和 `.env` 文件读取：

```python
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 零配置：全部从环境变量 / .env 读取
adapter = MinimalLLMAdapter()

# 显式配置：参数永远优先
adapter = MinimalLLMAdapter(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
    model="gpt-4o",
    max_tokens=4096,
    temperature=0.7,
    timeout=120,
)
```

**三个核心参数的解析优先级**（`base_url`、`api_key`、`model`，为 None 时走链）：

| 优先级 | base_url | api_key | model |
|--------|----------|---------|-------|
| 1. 显式传参 | `base_url=` | `api_key=` | `model=` |
| 2. 环境变量 | `LLM_BASE_URL` | `OPENAI_API_KEY` | `LLM_MODEL` |
| 3. .env 文件 | `base_url` | `api-key` / `api_key` | `model` |
| 4. 硬编码默认 | `https://api.openai.com/v1` | `""` | `gpt-4o` |

**`.env` 文件示例**（放在 `harness/config/.env`）：

```ini
# 第三方 API 配置示例（如 DeepSeek）
base_url = https://api.deepseek.com
api-key = sk-your-key-here
model = deepseek-v4-flash
```

### 7.2 自定义 LLM 适配器

你可以提供任何实现了 `call_llm` 签名的可调用对象：

```python
from harness.interfaces.types import Response, ToolCall, ToolCallFunction

# 函数形式
def my_llm(messages: list, tools: list | None = None) -> Response:
    resp = some_llm_sdk.chat(messages, tools)
    return Response(
        text=resp.content,
        thinking=getattr(resp, "thinking", None),
        tool_uses=[
            ToolCall(id=tc.id, type="function",
                     function=ToolCallFunction(name=tc.name, arguments=tc.arguments))
            for tc in getattr(resp, "tool_calls", [])
        ],
        stop_reason="end_turn" if resp.stop else "tool_use",
    )

# 类实例形式（实现 __call__）
class MyLLMAdapter:
    def __call__(self, messages, tools=None) -> Response:
        ...

# 注入
harness = Harness.from_container(container, call_llm=my_llm)
```

**签名约定**：

```
(messages: List[Dict], tools: Optional[List[Dict]]) → Response
```

> 注意：编排器通过 `messages_to_dicts()` 和 `tool_definitions_to_openai()` 将框架类型转为 dict 后再传入 `call_llm`，所以你收到的 `messages` 和 `tools` 都是 OpenAI 兼容的 dict 格式。

### 7.3 finish_reason 映射

适配器需要将 LLM API 的 `finish_reason` 映射为编排器内部标准值：

| API finish_reason | 内部 stop_reason |
|-------------------|-----------------|
| `"stop"` | `"end_turn"` |
| `"tool_calls"` | `"tool_use"` |
| `"length"` | `"end_turn"` |
| 其他未知值 | 原样保留 |

---

## 8. 消息转换层

`harness/messaging/builder.py` 提供框架类型与 OpenAI 兼容格式之间的转换函数。编排器内部使用正式类型（`Message`、`ToolDefinition`），与 LLM API 交互时通过转换层转到 dict 格式。

### 8.1 Message ↔ dict

```python
from harness.interfaces.types import Message
from harness.messaging import message_to_dict, dict_to_message, messages_to_dicts

# Message → dict
msg = Message(role="user", content="hello")
message_to_dict(msg)  # → {"role": "user", "content": "hello"}

# dict → Message
dict_to_message({"role": "assistant", "content": "hi"})  # → Message(...)

# 批量转换（自动处理混合列表）
messages_to_dicts([msg, {"role": "system", "content": "..."}])
```

关键行为：
- `tool_call_id` 非 None 时才写入 dict；为 None 时不包含该字段
- `dict_to_message` 的 `content` 为 None 或缺失时默认 `""`
- `dict_to_message` 忽略 `tool_calls` 等 Message 不包含的字段（不抛异常）
- `messages_to_dicts` 对已是 dict 的元素直接透传，Message 对象才转换

### 8.2 ToolDefinition → OpenAI

```python
from harness.interfaces.types import ToolDefinition
from harness.messaging import tool_definition_to_openai, tool_definitions_to_openai

td = ToolDefinition(
    name="read",
    description="Read a file",
    parameters={"type": "object", "properties": {}},
)
tool_definition_to_openai(td)
# → {"type": "function", "function": {"name": "read", "description": "Read a file", "parameters": {...}}}

# 批量
tool_definitions_to_openai([td1, td2])
```

### 8.3 完整的导入列表

```python
from harness.messaging import (
    message_to_dict,
    messages_to_dicts,
    dict_to_message,
    tool_definition_to_openai,
    tool_definitions_to_openai,
    build_assistant_message,
    build_tool_result_message,
)
```

---

## 9. 配置系统

### 9.1 profile.toml 格式

```toml
[meta]
name = "my-coding-agent"           # 必需：非空字符串
description = "个人编程助手"        # 可选
template = "coding-assistant"      # 必需：非空字符串
version = "0.1.0"                  # 可选，默认 "0.1.0"

[modules]
input_adapter = true
guide_provider = true
context_assembler = true
memory_backend = false
sensor = true
```

### 9.2 使用方式

```python
from harness.config.loader import ConfigLoader

loader = ConfigLoader()
config = loader.load("./profile.toml")   # 返回 ProfileConfig
loader.validate(config)                   # 校验字段

print(config.name)        # "my-coding-agent"
print(config.template)    # "coding-assistant"
print(config.modules)     # {"input_adapter": True, "guide_provider": True, ...}
print(config.raw)         # 原始 TOML 数据的完整 dict
```

### 9.3 校验规则

| 检查项 | 失败时 |
|--------|--------|
| 文件不存在 | `ConfigNotFoundError` |
| TOML 语法错误 | `ConfigParseError` |
| `[meta]` 段不是 TOML table | `ConfigValidationError` |
| `meta.name` 为空 | `ConfigValidationError` |
| `meta.template` 为空 | `ConfigValidationError` |
| `modules.<key>` 值非 bool | `ConfigValidationError` |
| `[modules]` 段缺失 | 不报错，`modules` 返回空 dict |
| 空文件 (0 bytes) | load() 不崩溃，validate() 抛 ConfigValidationError |

---

## 10. 异常处理

### 10.1 异常层次

```
HarnessError (Exception)
├── ConfigError
│   ├── ConfigNotFoundError       — 配置文件不存在
│   ├── ConfigParseError          — TOML 语法错误
│   └── ConfigValidationError     — 必填字段缺失或类型错误
├── ContainerError
│   ├── DuplicateRegistrationError— 重复注册同一接口
│   └── ComponentNotRegisteredError— 接口类型未注册
└── OrchestratorError             — 编排流程运行时错误
```

### 10.2 使用建议

```python
# 精确捕获
try:
    harness.run()
except ConfigNotFoundError:
    print("请创建 profile.toml")
except ComponentNotRegisteredError as e:
    print(f"缺少组件: {e}")

# 统一捕获所有框架异常
try:
    harness.run()
except HarnessError as e:
    logger.error(f"框架错误: {e}")
```

---

## 11. 添加新 Batch 的步骤

### 11.1 步骤总览

```
1. 创建 sdd/batches/batch-XX-your-module/ 设计文档
   ├── design.md      — 接口设计 + 关键决策
   ├── tasks.md       — 实现任务拆分
   └── acceptance.md  — 验收标准

2. 实现组件
   ├── harness/interfaces/<name>.py        — 正式接口定义（Protocol）
   └── harness/components/<name>/          — 至少一个默认实现

3. 编写测试
   └── tests/test_<name>.py

4. 注册到 DI 容器 + 对接编排器
```

### 11.2 实现组件前要回答的问题

| 问题 | 示例 |
|------|------|
| 这个组件在生命周期的**哪个阶段**被调用？ | GuideProvider → 阶段一 |
| 它**消费**什么数据结构？ | ContextAssembler 消费 `AssemblyContext` |
| 它**产出**什么数据结构？ | Sensor 产出 void（副作用写入 MemoryBackend） |
| 它是**必需**还是**可选**？ | InputAdapter 必需，Sensor 可选 |
| 它需要**外部依赖**吗？ | MemoryBackend 需要注入给 Sensor |

### 11.3 如何对接编排器

编排器通过 `_resolve_optional()` 解析可选组件。如果你想添加一个新的生命周期步骤：

1. 在 `harness/interfaces/` 下创建 Protocol 文件

2. 在对应生命周期阶段调用它：

```python
# 例如在 _phase_init 中添加：
from ..interfaces.your_component import YourComponent

your_comp = self._resolve_optional(YourComponent)
if your_comp:
    result = your_comp.do_something(ctx)
```

3. 在 `harness/interfaces/__init__.py` 中导出新接口。

### 11.4 实现 MemoryBackend 示例

```python
# harness/components/memory_backend/md_memory.py
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.interfaces.types import MemoryItem


class MdMemory:
    """Markdown 文件存储的 MemoryBackend 实现。

    每个记忆项一个 .md 文件，使用 YAML frontmatter + Markdown 正文。
    """

    def __init__(self, path: str = "~/.harness/memory"):
        self._root = Path(path).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Dict[str, MemoryItem]] = {}
        self._build_index()

    def _build_index(self):
        """启动时扫描所有 .md 文件构建内存索引。"""
        for ns_dir in self._root.iterdir():
            if not ns_dir.is_dir():
                continue
            namespace = ns_dir.name
            self._index.setdefault(namespace, {})
            for md_file in ns_dir.glob("*.md"):
                if md_file.name == "MEMORY.md":
                    continue
                try:
                    item = self._read_md_file(md_file)
                    if item:
                        self._index[namespace][item.key] = item
                except Exception:
                    pass  # 跳过解析失败的文件

    def read(self, key: str, namespace: str) -> Optional[Any]:
        ns_items = self._index.get(namespace, {})
        item = ns_items.get(key)
        return item.value if item else None

    def write(self, key: str, value: Any, namespace: str) -> None:
        value_str = str(value)
        ts = time.time()
        item = MemoryItem(
            key=key, value=value_str, namespace=namespace, timestamp=ts
        )
        # 确保 namespace 条目存在
        self._index.setdefault(namespace, {})[key] = item
        # 写入 .md 文件
        ns_dir = self._root / namespace
        ns_dir.mkdir(exist_ok=True)
        self._write_md_file(ns_dir / f"{key}.md", item)

    def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]:
        if not query or namespace not in self._index:
            return []
        q = query.lower()
        results = [
            item for item in self._index[namespace].values()
            if q in item.key.lower() or q in str(item.value).lower()
        ]
        results.sort(key=lambda x: x.timestamp, reverse=True)
        return results[:limit]

    def list_namespaces(self) -> List[str]:
        return list(self._index.keys())

    def _read_md_file(self, filepath: Path) -> Optional[MemoryItem]:
        """解析 .md 文件，返回 MemoryItem。"""
        text = filepath.read_text(encoding="utf-8")
        fm, body = self._parse_frontmatter(text)
        if not fm.get("key"):
            return None
        return MemoryItem(
            key=fm["key"],
            value=body.strip(),
            namespace=fm.get("namespace", ""),
            timestamp=float(fm.get("timestamp", 0)),
            metadata=fm.get("metadata", {}),
        )

    def _write_md_file(self, filepath: Path, item: MemoryItem):
        """写入 .md 记忆文件。"""
        lines = [
            "---",
            f"key: {item.key}",
            f"namespace: {item.namespace}",
            f"timestamp: {item.timestamp}",
        ]
        if item.metadata:
            lines.append("metadata:")
            for mk, mv in item.metadata.items():
                lines.append(f"  {mk}: {mv}")
        lines.append("---")
        lines.append("")
        lines.append(str(item.value))
        filepath.write_text("\n".join(lines), encoding="utf-8")

    def _parse_frontmatter(self, text: str) -> tuple:
        """简单 YAML frontmatter 解析器（不引入 pyyaml 依赖）。"""
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        fm_text = parts[1].strip()
        body = parts[2].strip()
        fm: Dict[str, Any] = {}
        current_key = None
        for line in fm_text.split("\n"):
            if not line.strip():
                continue
            if line.startswith("  ") and current_key == "metadata":
                if ":" in line:
                    mk, _, mv = line.strip().partition(": ")
                    fm.setdefault("metadata", {})[mk] = mv
                continue
            if ":" in line:
                k, _, v = line.partition(": ")
                fm[k.strip()] = v.strip()
                current_key = k.strip()
        return fm, body
```

---

## 12. 测试指南

### 12.1 测试组件

所有组件通过 mock 其他依赖来独立测试：

```python
from harness.core.container import DIContainer
from harness.core.orchestrator import InputAdapter, LifecycleOrchestrator
from harness.interfaces.types import UserRequest, Response, GuidesBundle

class TestMyComponent:
    def test_basic_flow(self):
        container = DIContainer()

        # Mock InputAdapter: 提供一轮输入后退出
        class MockAdapter:
            def __init__(self):
                self.inputs = ["hello"]
                self.outputs = []
                self.idx = 0
            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]; self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")
            def send(self, resp):
                self.outputs.append(resp.text)

        container.register(InputAdapter, MockAdapter())

        # Mock LLM
        def mock_llm(msgs, tools):
            return Response(text="mock reply")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch._cached_guides = GuidesBundle()
        orch._cached_tools = []

        # 测试单个阶段
        ctx = orch._phase_init()
        assert ctx.user_request.text == "hello"

        orch._phase_loop(ctx)
        adapter = container.resolve(InputAdapter)
        assert adapter.outputs[0] == "mock reply"
```

### 12.2 运行测试

```bash
# 运行所有测试
pytest tests/ --ignore=tests/test_real_llm_trace.py -v

# 运行单个测试文件
pytest tests/test_orchestrator.py -v
pytest tests/test_config.py -v
pytest tests/test_messaging.py -v

# test_real_llm_trace.py 需要有效的 API key，单独运行：
python tests/test_real_llm_trace.py
```

---

## 13. 常见问题

### Q: 为什么注册组件不需要继承基类？

A: Core 采用 duck typing 模式。编排器只关心你的对象有没有对应的方法（`receive()`、`send()` 等），不关心继承关系。`harness/interfaces/` 下的 Protocol 类提供类型标注参考，同时作为 `container.register()` 的类型 key。

### Q: 如何让多个组件共享同一个 MemoryBackend 实例？

A: 创建同一个实例，注册到不同接口：

```python
memory = MdMemory("./memory")
container.register(MemoryBackend, memory)
# Sensor 通过构造函数注入同一个 memory 实例
sensor = LoggingSensor(memory=memory)
container.register(Sensor, sensor)
```

### Q: ContextAssembler 和 _fallback_assemble 是什么关系？

A: 如果你注册了 ContextAssembler，编排器用它组装消息。如果没有，使用内置的 `_fallback_assemble`——但它不包含对话历史，多轮对话会"失忆"。**生产环境强烈建议注册 ContextAssembler**。

### Q: 数据结构的正式类型在哪里定义？

A: 所有 16 个数据类型统一定义在 `harness/interfaces/types.py`。`harness.core.types` 中的旧类型已标记为 deprecated（导入时会触发 `DeprecationWarning`），不应在新代码中使用。

### Q: ToolCall 的 parse_arguments() 方法在哪？

A: 正式类型的 `ToolCall` 没有 `parse_arguments()` 方法（旧 `_MinimalToolCall` 才有）。请使用 `json.loads(tc.function.arguments)`。

### Q: 如何添加自定义的退出条件？

A: 在 InputAdapter 返回的 `UserRequest` 中设置 `metadata={"exit": True}`，或让 `text` 为空字符串，或让 `text` 匹配 `/exit`。

### Q: 框架如何处理错误？

A: 在 `_resolve_optional` 中缺失组件 → WARNING + 跳过。组件方法内部抛异常 → 被各阶段的 try/except 捕获 → WARNING + 继续。`_phase_end` 在 `finally` 中执行，保证异常时也会做清理和 Sensor 调用。未知异常被包装为 `OrchestratorError` 向上抛出。

### Q: 可以在运行中热替换组件吗？

A: 目前不直接支持。DIContainer 没有 `unregister()` 方法（设计上假设一次装配、全生命周期使用）。如需热替换，建议创建新容器或等待后续版本。

### Q: ContextAssembler.assemble() 应该返回什么类型？

A: 返回 `List[Message]` 或 `List[dict]` 都可以。编排器在传给 LLM 之前会通过 `messages_to_dicts()` 统一转换，该函数对 dict 元素透传、对 Message 对象做转换。建议返回 `List[Message]` 以与 `ctx.history` 的类型保持一致。

---

## 附录：快速参考卡片

### 组件实现检查清单

```
□ 我的组件需要哪些方法？（对照第 4 节）
□ 消费什么数据结构？（第 5 节）
□ 产出什么数据结构？
□ 构造函数需要注入哪些依赖？
□ 注册到容器后，测试通过了吗？
```

### 最小可运行 Agent 所需注册

```
必需:
  □ InputAdapter
  □ call_llm (生产环境)

强烈建议:
  □ ContextAssembler（否则多轮对话失忆）

可选:
  □ GuideProvider
  □ MemoryBackend
  □ ToolRegistry
  □ Sensor
```
