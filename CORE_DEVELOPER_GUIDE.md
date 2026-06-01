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
5. [数据结构速查](#5-数据结构速查)
6. [装配与启动](#6-装配与启动)
7. [LLM 集成](#7-llm-集成)
8. [配置系统](#8-配置系统)
9. [异常处理](#9-异常处理)
10. [添加新 Batch 的步骤](#10-添加新-batch-的步骤)
11. [测试指南](#11-测试指南)
12. [常见问题](#12-常见问题)

---

## 1. 概述

Harness 是一个 **模块化 Agent 框架的 MVP（最小可行产品）**。它提供：

1. **按固定顺序调度组件** — 三阶段生命周期（初始化 → 对话循环 → 结束）
2. **管理组件注册与解析** — 预构造实例的 DI 容器
3. **开箱即用的 LLM 适配器** — 零依赖 OpenAI 兼容客户端，自动从 .env 读取配置

框架 **不包含**任何业务逻辑。你的 Agent 具体做什么（如何压缩上下文、如何评估质量、如何存储记忆），完全由你实现的组件决定。

### 文件地图

```
harness/
├── __init__.py              # 导出 Harness 入口类
├── di.py                    # Harness 装配入口（from_container + run）
├── core/                    # 内核：DI 容器 + 编排器 + 异常 + 数据类型
│   ├── __init__.py          # 导出所有公开 API
│   ├── exceptions.py        # 异常体系
│   ├── container.py         # DIContainer（注册/解析/查询）
│   ├── types.py             # 内部数据结构
│   └── orchestrator.py      # LifecycleOrchestrator（三阶段编排）
├── interfaces/              # 组件接口类型（占位）
│   └── __init__.py
├── adapters/                # 外部系统适配器
│   └── llm_adapter.py       # MinimalLLMAdapter
├── config/                  # 配置模块
│   ├── loader.py            # ConfigLoader + ProfileConfig
│   └── .env                 # API 配置模板
└── messaging/               # 消息构造
    └── builder.py           # assistant / tool_result 消息构造
```

---

## 2. 一分钟快速开始

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.core.orchestrator import (
    InputAdapter, Sensor,
    _MinimalUserRequest, _MinimalResponse,
)
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 1. 创建容器 + 注册组件
container = DIContainer()

class CliAdapter:
    def receive(self):
        text = input("> ")
        return _MinimalUserRequest(text=text)
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
│ 2. 检查退出信号（/exit 等）                      │
│    → 如果是退出：跳转到阶段三                     │
│ 3. GuideProvider.get_guides()  → GuidesBundle   │
│ 4. MemoryBackend.search()      → List[Memory]   │
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

> **接口类型（如 `InputAdapter`、`Sensor`）当前是空类占位符**，仅作为 `container.register()` 的 key。batch-02 会替换为正式的 Protocol/ABC 定义。

### 4.1 InputAdapter（必需）

输入输出适配器，框架与外部世界的唯一通道。

```python
class MyAdapter:
    def receive(self) -> _MinimalUserRequest:
        """获取用户下一轮输入。返回空 text 或 exit 元数据表示退出。"""
        ...

    def send(self, response: _MinimalResponse) -> None:
        """将 Agent 响应发送给用户。"""
        ...
```

| 方法 | 调用时机 | 返回值含义 |
|------|---------|-----------|
| `receive()` | 阶段一入口 + 每轮外层循环末尾 | `text=None/""` → 退出；`text="/exit"` → 退出；正常文本 → 继续 |
| `send(response)` | 每次 LLM 返回 text 时 | 无返回值 |

**退出信号**（`_should_exit` 的判断逻辑）：
- `text` 为 `None` 或仅空白字符
- `text` 匹配 `/exit`
- `metadata` 中包含 `"exit": True`

> **注意**：退出检查在**阶段一 receive() 之后立即执行**，也在**每轮外层循环末尾**执行。第一轮用户输入 `/exit` 不会触发 LLM 调用。

### 4.2 GuideProvider（可选）

前馈指导提供者，在会话开始时提供 Agent 的身份定义和行为规则。

```python
class MyGuideProvider:
    def get_guides(self, ctx: _MinimalAssemblyContext) -> _MinimalGuidesBundle:
        """根据上下文返回指导信息。只调用一次，结果被缓存。"""
        ...
```

返回值 `_MinimalGuidesBundle` 包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `identity` | `str` | 核心身份（如 "You are a coding assistant..."） |
| `capabilities` | `List[str]` | 能力清单 |
| `rules` | `List[str]` | 行为规则 |
| `constraints` | `List[str]` | 硬约束 |
| `examples` | `List[Dict[str, str]]` | 少样本示例 |

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

上下文组装器，将框架的所有信息源拼成发给 LLM 的 message 列表。

```python
class MyAssembler:
    def assemble(self, ctx: _MinimalAssemblyContext) -> List[Dict[str, Any]]:
        """将 AssemblyContext 转为 OpenAI 兼容的 message 列表。"""
        ...
```

输入 `_MinimalAssemblyContext` 包含：

| 字段 | 说明 |
|------|------|
| `user_request` | 当前用户请求 |
| `guides` | GuidesBundle（阶段一缓存） |
| `available_tools` | 可用工具定义列表 |
| `history` | 当前会话的对话历史 |
| `memories` | 从 MemoryBackend 检索的记忆 |
| `system_state` | 系统当前状态 |
| `metadata` | 扩展字段 |

> ⚠️ **关键提醒**：框架把 `ctx.history` 传给你的 assembler，但**不会自动把它合并到 messages 中**。你的 `assemble()` 方法必须显式处理 `ctx.history`，否则多轮对话会失忆。
>
> 示例写法：
> ```python
> def assemble(self, ctx):
>     messages = []
>     if ctx.guides and ctx.guides.identity:
>         messages.append({"role": "system", "content": ctx.guides.identity})
>     # ← 必须手动包含历史
>     messages.extend(ctx.history)
>     if ctx.user_request and ctx.user_request.text:
>         messages.append({"role": "user", "content": ctx.user_request.text})
>     return messages
> ```

**如果你不注册 ContextAssembler**，框架使用内置降级逻辑（`_fallback_assemble`）。降级逻辑**有意设计为极简**：只拼接 system identity + 当前 user 输入，不包含对话历史、memories、tools。它仅用于调试和验证编排流程，**生产环境强烈建议注册自己实现的 ContextAssembler**。

### 4.5 ToolRegistry（可选）

管理所有 Tool 的注册与调度执行。

```python
class MyToolRegistry:
    def register(self, tool) -> None:
        """注册一个工具实例。在框架初始化时调用。"""
        ...

    def list_tools(self) -> List[Dict[str, Any]]:
        """返回所有可用工具的 OpenAI 兼容 tool definition 列表。"""
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
class MySensor:
    def sense(self, trajectory: _MinimalTrajectory) -> None:
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

## 5. 数据结构速查

编排器内部使用以下数据结构。它们以 `_Minimal` 前缀命名，batch-02 会被 `harness/interfaces/` 中的正式类型替换。

### 5.1 _MinimalUserRequest

```python
@dataclass
class _MinimalUserRequest:
    text: Optional[str]            # 用户输入文本
    metadata: Dict[str, Any]       # 扩展元数据（如 {"exit": True}）
```

### 5.2 _MinimalResponse

```python
@dataclass
class _MinimalResponse:
    text: Optional[str] = None                    # LLM 文本输出
    thinking: Optional[str] = None                # 推理过程（DeepSeek/OpenAI o-series）
    tool_uses: List[_MinimalToolCall] = []        # 工具调用列表
    stop_reason: str = "end_turn"                 # "end_turn" | "tool_use" | ...
```

### 5.3 _MinimalToolCall

```python
@dataclass
class _MinimalToolCallFunction:
    name: str = ""                                # 函数名
    arguments: str = "{}"                         # JSON 编码的参数串

@dataclass
class _MinimalToolCall:
    id: str                                       # tool call 唯一标识
    type: str = "function"                        # 固定为 "function"
    function: _MinimalToolCallFunction            # 函数名与参数

    def parse_arguments(self) -> Dict[str, Any]:  # 将 arguments JSON 解析为 dict
```

### 5.4 _MinimalGuidesBundle

```python
@dataclass
class _MinimalGuidesBundle:
    identity: str = ""                            # 核心身份
    capabilities: List[str] = []                  # 能力清单
    rules: List[str] = []                         # 行为规则
    constraints: List[str] = []                   # 硬约束
    examples: List[Dict[str, str]] = []           # 少样本示例
```

### 5.5 _MinimalAssemblyContext

```python
@dataclass
class _MinimalAssemblyContext:
    user_request: Optional[_MinimalUserRequest] = None
    guides: Optional[_MinimalGuidesBundle] = None
    available_tools: List[Dict[str, Any]] = []    # 可用工具定义
    history: List[Dict[str, Any]] = []            # 当前会话对话历史
    memories: List[Dict[str, Any]] = []           # 检索到的记忆
    system_state: Dict[str, Any] = {}             # 系统状态
    metadata: Dict[str, Any] = {}                 # 扩展桶
```

### 5.6 _MinimalTrajectory

```python
@dataclass
class _MinimalTrajectory:
    user_request: Optional[_MinimalUserRequest] = None
    history: List[Dict[str, Any]] = []            # 完整对话历史
    tool_calls: List[Dict[str, Any]] = []         # 工具调用执行记录
    final_output: str = ""                        # Agent 最终输出
    execution_time: float = 0.0                   # 执行耗时（秒）
    system_state: Dict[str, Any] = {}             # 系统状态
    metadata: Dict[str, Any] = {}                 # 扩展桶
```

### 5.7 何时用哪个结构

```
InputAdapter.receive() 产出 → _MinimalUserRequest
GuideProvider.get_guides() 产出 → _MinimalGuidesBundle
ContextAssembler.assemble() 消费 ← _MinimalAssemblyContext
call_llm 产出 → _MinimalResponse (包含 _MinimalToolCall)
Sensor.sense() 消费 ← _MinimalTrajectory
```

---

## 6. 装配与启动

### 6.1 注册模式

```python
container = DIContainer()

# 创建实例 → 注册到容器
adapter = CliAdapter()
container.register(InputAdapter, adapter)

guide = FileGuideProvider("AGENTS.md")
container.register(GuideProvider, guide)

# 同一个实例可以注册到多个接口（如 MemoryBackend 被多处共享）
memory = JsonlMemory("./memory.jsonl")
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

配置好 `.env` 后，`MinimalLLMAdapter()` 零参数即可直连第三方 API。

### 7.2 自定义 LLM 适配器

你可以提供任何实现了 `call_llm` 签名的可调用对象：

```python
# 函数形式
def my_llm(messages: List[Dict], tools: Optional[List[Dict]] = None) -> _MinimalResponse:
    resp = some_llm_sdk.chat(messages, tools)
    return _MinimalResponse(
        text=resp.content,
        thinking=resp.reasoning,
        tool_uses=[...],
        stop_reason="end_turn",
    )

# 类实例形式（实现 __call__）
class MyLLMAdapter:
    def __call__(self, messages, tools=None) -> _MinimalResponse:
        ...

# 注入
harness = Harness.from_container(container, call_llm=my_llm)
```

**签名约定**：

```
(messages: List[Dict], tools: Optional[List[Dict]]) → _MinimalResponse
```

### 7.3 finish_reason 映射

适配器需要将 LLM API 的 `finish_reason` 映射为编排器内部标准值：

| API finish_reason | 内部 stop_reason |
|-------------------|-----------------|
| `"stop"` | `"end_turn"` |
| `"tool_calls"` | `"tool_use"` |
| `"length"` | `"end_turn"` |
| 其他未知值 | 原样保留 |

---

## 8. 配置系统

### 8.1 profile.toml 格式

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

### 8.2 使用方式

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

### 8.3 校验规则

| 检查项 | 失败时 |
|--------|--------|
| 文件不存在 | `ConfigNotFoundError` |
| TOML 语法错误 | `ConfigParseError` |
| `[meta]` 段不是 TOML table | `ConfigValidationError` |
| `meta.name` 为空 | `ConfigValidationError` |
| `meta.template` 为空 | `ConfigValidationError` |
| `modules.<key>` 值非 bool | `ConfigValidationError` |
| `[modules]` 段缺失 | 不报错，`modules` 返回空 dict |

---

## 9. 异常处理

### 9.1 异常层次

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

### 9.2 使用建议

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

## 10. 添加新 Batch 的步骤

### 10.1 步骤总览

```
1. 创建 sdd/batches/batch-XX-your-module/ 设计文档
   ├── design.md      — 接口设计 + 关键决策
   ├── tasks.md       — 实现任务拆分
   └── acceptance.md  — 验收标准

2. 实现组件
   ├── harness/interfaces/<name>.py        — 正式接口定义（Protocol/ABC）
   └── harness/components/<name>/          — 至少一个默认实现

3. 编写测试
   └── tests/test_<name>.py

4. 注册到 DI 容器 + 对接编排器
```

### 10.2 实现组件前要回答的问题

| 问题 | 示例 |
|------|------|
| 这个组件在生命周期的**哪个阶段**被调用？ | GuideProvider → 阶段一 |
| 它**消费**什么数据结构？ | ContextAssembler 消费 `AssemblyContext` |
| 它**产出**什么数据结构？ | Sensor 产出 void（副作用写入 MemoryBackend） |
| 它是**必需**还是**可选**？ | InputAdapter 必需，Sensor 可选 |
| 它需要**外部依赖**吗？ | MemoryBackend 需要注入给 Sensor |

### 10.3 如何对接编排器

编排器通过 `_resolve_optional()` 解析可选组件。如果你想添加一个新的生命周期步骤：

1. 在 `orchestrator.py` 的占位接口区添加新的空类：

```python
class YourNewComponent:
    """[PLACEHOLDER] 你的新组件接口。"""
    pass
```

2. 在对应生命周期阶段调用它：

```python
# 例如在 _phase_init 中添加：
your_comp = self._resolve_optional(YourNewComponent)
if your_comp:
    result = your_comp.do_something(ctx)
```

3. 在 `harness/core/__init__.py` 中导出。

### 10.4 实现 MemoryBackend 示例

```python
# harness/components/memory_backend/jsonl_memory.py
import json
import os
from typing import Any, List, Optional

class JsonlMemory:
    """JSONL 文件存储的 MemoryBackend 实现。"""

    def __init__(self, path: str):
        self.path = path
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    item = json.loads(line)
                    self._index[f"{item['namespace']}:{item['key']}"] = item

    def read(self, key: str, namespace: str) -> Optional[Any]:
        item = self._index.get(f"{namespace}:{key}")
        return item["value"] if item else None

    def write(self, key: str, value: Any, namespace: str) -> None:
        item = {"key": key, "value": value, "namespace": namespace}
        self._index[f"{namespace}:{key}"] = item
        with open(self.path, "a") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def search(self, query: str, namespace: str, limit: int = 10) -> list:
        results = [
            v for k, v in self._index.items()
            if k.startswith(f"{namespace}:") and query.lower() in str(v).lower()
        ]
        return results[:limit]

    def list_namespaces(self) -> List[str]:
        return list(set(k.split(":")[0] for k in self._index.keys()))
```

---

## 11. 测试指南

### 11.1 测试组件

所有组件通过 mock 其他依赖来独立测试：

```python
from harness.core.container import DIContainer
from harness.core.orchestrator import (
    InputAdapter, LifecycleOrchestrator,
    _MinimalUserRequest, _MinimalResponse,
)

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
                    return _MinimalUserRequest(text=t)
                return _MinimalUserRequest(text="")
            def send(self, resp):
                self.outputs.append(resp.text)

        container.register(InputAdapter, MockAdapter())

        # Mock LLM
        def mock_llm(msgs, tools):
            return _MinimalResponse(text="mock reply")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch._cached_guides = _MinimalGuidesBundle()
        orch._cached_tools = []

        # 测试单个阶段
        ctx = orch._phase_init()
        assert ctx.user_request.text == "hello"

        orch._phase_loop(ctx)
        adapter = container.resolve(InputAdapter)
        assert adapter.outputs[0] == "mock reply"
```

### 11.2 运行测试

```bash
# 运行所有单元测试
pytest tests/ --ignore=tests/test_real_llm_trace.py -v

# 运行单个测试文件
pytest tests/test_orchestrator.py -v

# test_real_llm_trace.py 需要有效的 API key，单独运行：
python tests/test_real_llm_trace.py
```

---

## 12. 常见问题

### Q: 为什么注册组件不需要继承基类？

A: Core 采用 duck typing 模式。编排器只关心你的对象有没有对应的方法（`receive()`、`send()` 等），不关心继承关系。空占位类（如 `InputAdapter`）仅作为 `container.register()` 的类型 key。batch-02 会引入正式的 `Protocol` 定义。

### Q: 如何让多个组件共享同一个 MemoryBackend 实例？

A: 创建同一个实例，注册到不同接口：

```python
memory = JsonlMemory("./memory.jsonl")
container.register(MemoryBackend, memory)
# Sensor 通过构造函数注入同一个 memory 实例
sensor = LoggingSensor(memory=memory)
container.register(Sensor, sensor)
```

### Q: ContextAssembler 和 _fallback_assemble 是什么关系？

A: 如果你注册了 ContextAssembler，编排器用它组装消息。如果没有，使用内置的 `_fallback_assemble`——但它不包含对话历史，多轮对话会"失忆"。**生产环境强烈建议注册 ContextAssembler**。

### Q: _Minimal 前缀的数据结构什么时候会变？

A: batch-02 会创建 `harness/interfaces/` 目录，把这些 `_Minimal*` 类型替换为正式的接口类型。届时字段名可能会有变化（如 `tool_calls` → 与 SDD 完全对齐），但语义不变。

### Q: 如何添加自定义的退出条件？

A: 在 InputAdapter 返回的 `_MinimalUserRequest` 中设置 `metadata={"exit": True}`，或让 `text` 匹配 `/exit`、`/quit`、`/bye` 关键词，或让 `text` 为 `None`/空/仅空白。

### Q: 框架如何处理错误？

A: 在 `_resolve_optional` 中缺失组件 → WARNING + 跳过。组件方法内部抛异常 → 被各阶段的 try/except 捕获 → WARNING + 继续。`_phase_end` 在 `finally` 中执行，保证异常时也会做清理和 Sensor 调用。未知异常被包装为 `OrchestratorError` 向上抛出。

### Q: 可以在运行中热替换组件吗？

A: 目前不直接支持。DIContainer 没有 `unregister()` 方法（设计上假设一次装配、全生命周期使用）。如需热替换，建议创建新容器或等待后续版本。

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
