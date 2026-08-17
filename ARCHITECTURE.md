# Harness Agent Template — 架构设计文档

> **定位**：一个面向个人开发者与小型团队的模块化 Agent Harness 模板。核心不是提供最强的性能，而是提供最方便的裁剪与扩展能力。
>
> **核心理念**：框架只定义接口契约与编排流程，所有具体行为由用户通过"实现接口 + 依赖注入"来自定义。
>
> **版本: 1.1** — 全部 11 个批次已完成（2026-06-04）

---

## 一、架构范式

### 1.1 微内核 + 插件架构 (Microkernel + Plugin Architecture)

框架内核极度精简，仅负责：
- **生命周期编排**：按固定顺序调用各组件
- **Hook 生命周期拦截**：在关键节点插入自定义逻辑，组件间通过标准接口交互不直接耦合
- **依赖注入容器**：管理组件实例的注册与解析

所有业务逻辑（如何压缩上下文、如何评估质量、如何存储记忆）都是满足接口契约的**插件**。

### 1.2 控制流与数据流分离

```
控制流（框架固定，按会话生命周期）：
  【会话初始化】InputAdapter.receive() → GuideProvider → MemoryBackend → ToolRouter → ContextAssembler
  【多轮循环】   ContextAssembler → LLM → [Tool] → InputAdapter.send(事件流) → InputAdapter.receive() → ContextAssembler
  【会话结束】   Sensor → MemoryBackend

数据流（组件自定义）：
  Sensor 在会话结束时写入 MemoryBackend
  ContextAssembler 在每轮外层循环开始时从 MemoryBackend 读取
  GuideProvider 从文件系统/动态逻辑读取
```

### 1.3 领域模板驱动

不同场景（Coding、旅行、研究）通过**领域模板**快速启动。模板包含：
- 推荐的组件装配方案
- 默认组件实现骨架
- 领域特定的接口扩展约定

---

## 二、核心组件

### 2.1 组件总览

| 组件 | 职责 | 输入 | 输出/副作用 |
|------|------|------|------------|
| **InputAdapter** | 输入输出适配：接收用户输入，将事件流呈现给前端 | 用户原始输入 | 标准化 UserRequest / 推送 AdapterEvent 事件流 |
| **GuideProvider** | 前馈控制：行动前提供指导 | 当前会话状态、环境状态 | GuidesBundle |
| **ContextAssembler** | 上下文工程：拼接所有信息给 LLM | 大包对象（guides、history、memories、system_state） | List[Message] |
| **ToolRouter** | 工具路由：合并多个 Provider 的工具并按名分发执行（框架内部，非 DI） | SystemToolProvider + MCPAdapter | 合并工具列表 / 路由执行结果 |
| **SystemToolProvider** | 系统工具提供者：管理本地实现的 Tool 集合（DI 插件，用户可替换） | 框架调用请求 | 工具元信息 / 执行结果 |
| **MCPAdapter** | MCP 适配层：消费外部 MCP Server，经转换后暴露工具（DI 插件，不注册即裁切） | 外部 MCP Server | 工具元信息 / 执行结果 |
| **Tool** | 工具执行层：被 ToolRouter 通过 Provider 调用以描述能力或执行操作 | 框架调用请求 | 工具元信息（供发现）/ 执行结果（供上下文） |
| **Sensor** | 反馈控制：在**会话结束时**评估完整多轮轨迹并沉淀知识 | Trajectory（完整多轮执行日志） | 写入 MemoryBackend |
| **MemoryBackend** | 记忆层：跨会话持久化 | key/value/namespace | 存储与检索结果 |
| **Hook** | 生命周期拦截：在关键节点插入自定义逻辑 | HookContext | 修改 context 中的数据 |

### 2.2 组件关系图

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
           │    │Provider    │  │  (DI 插件，可裁切) │
           │    │(DI 插件)   │  │                   │
           │    └────────────┘  │  ┌─────────────┐  │
           │                    │  │ MCPClient   │──┤→ 外部 MCP Server
           │                    │  └─────────────┘  │
           │                    │  ┌─────────────┐  │
           │                    │  │ Transform   │  │
           │                    │  │ Pipeline    │  │
           │                    │  └─────────────┘  │
           │                    └───────────────────┘
           │
           └────────────────────┐
                                │
                      ┌─────────┴─────────┐
                      │      Sensor       │
                      │ (构造注入 memory) │
                      └───────────────────┘
```

**关键约束**：
- ContextAssembler **只从 MemoryBackend 读取记忆**，永不直接接触 Sensor
- Sensor **直接操作 MemoryBackend**，其评估结果通过记忆层间接影响下一轮上下文
- **记忆检索**：框架在会话初始化阶段自动执行基线检索（`namespace="episodic"`）并填入 `AssemblyContext.memories`，结果缓存复用；ContextAssembler 可通过注入的 MemoryBackend 在 `assemble()` 内执行额外定制检索（跨 namespace、不同 query 策略）
- 所有组件通过 **DI 容器** 装配，组件间依赖通过构造函数注入
- **ToolRouter 是框架内部组件**（非 DI），由编排器在 `_phase_init()` 中创建，合并 SystemToolProvider 和 MCPAdapter
- **SystemToolProvider 和 MCPAdapter 是两个独立的 DI 插件**，各自实现自己的 Protocol，可独立替换或裁切
- **MCPAdapter 不注册到 DI 即表示裁切 MCP 功能**
- **所有 Tool（系统 + MCP）统一走 ToolRouter 分发执行**，执行前后触发 Hook
- MCPAdapter 通过内部 MCPClient 连接外部 MCP Server，持有其生命周期（含 shutdown）

---

## 三、组件接口契约

### 3.1 GuideProvider（前馈控制）

**职责**：在 Agent 行动前，提供所有指导性输入。

**接口方法**：
```
get_guides(context: GuideContext) → GuidesBundle
```

**输入大包对象 GuideContext**：
- user_request：用户当前请求
- system_state：系统当前状态（会话阶段、运行模式、资源状态等）
- env_state：环境状态（工作目录、git 状态、时间等）
- metadata：扩展元数据桶

**输出 GuidesBundle**：
- identity：核心身份定义
- capabilities：能力清单
- rules：行为规则列表
- constraints：硬约束列表
- examples：少样本示例（可选）

**实现示例**：
- **FileGuideProvider**：从文件系统读取静态配置（如 AGENTS.md）

---

### 3.2 ContextAssembler（上下文工程）

**职责**：将 Harness 当前所有信息源组装成发给 LLM 的最终消息列表。

**接口方法**：
```
assemble(inputs: AssemblyContext) → List[Message]
```

**输入大包对象 AssemblyContext**：
- user_request：来自 InputAdapter 的标准化请求
- guides：来自 GuideProvider 的 GuidesBundle
- available_tools：来自 ToolRouter 的工具发现列表（name、description、parameters）
- history：原始或部分压缩的对话历史
- memories：从 MemoryBackend 检索的记忆项列表
- system_state：系统当前状态（会话阶段、运行模式、资源状态等）
- metadata：领域扩展桶

**实现示例**：
- **SimpleAssembler**：滑动窗口截断 + 直接拼接 guides、memories、history

---

### 3.3 Sensor（反馈控制）

**职责**：读取执行轨迹，按自定义规则评估，并将沉淀的知识写入 MemoryBackend。

**接口方法**：
```
sense(trajectory: Trajectory) → void
```

**输入大包对象 Trajectory**：
- session_id：会话标识
- history：完整事件流（user → assistant(+tool_calls) → tool_result → assistant(text) → ...）
- tool_calls：所有工具调用记录与执行结果（通过 tool_call_id 与 history 中的 ToolCall 对齐）
- final_output：Agent 最终输出
- execution_time：执行耗时
- system_state：系统当前状态（会话阶段、运行模式、资源状态等）
- metadata：扩展元数据桶

**关键设计**：
- Sensor 是**副作用组件**，不显式返回值给框架
- Sensor 通过**构造函数注入**获得 MemoryBackend 引用，在 `sense()` 方法内自行决定写入内容
- Sensor 在**会话结束阶段**统一调用，评估完整的多轮 Trajectory
- 用户可在 Sensor 内部接入另一个 Agent 做复杂评估

**实现示例**：
- **LoggingSensor**（已实现：`harness/components/sensor/logging_sensor.py`）：将轨迹记录到 MemoryBackend 的 `episodic` 命名空间，供后续检索

---

### 3.4 MemoryBackend（记忆层）

**职责**：跨会话的持久化存储与检索。

**接口方法**：
```
read(key: str, namespace: str) → Optional[Any]
write(key: str, value: Any, namespace: str) → void
search(query: str, namespace: str, limit: int = 10) → List[MemoryItem]
list_namespaces() → List[str]
```

**命名空间约定**（非强制，仅社区约定）：
| Namespace | 用途 | 典型写入者 | 典型读取者 |
|-----------|------|-----------|-----------|
| episodic | 事件记忆（对话摘要） | Sensor | ContextAssembler |
| semantic | 事实知识（用户偏好） | Sensor | ContextAssembler |
| procedural | 技能/可复用模式 | Sensor | ContextAssembler |
| sensor_raw | Sensor 原始评估 | Sensor | Sensor（跨会话） |
| system | 系统状态缓存 | Framework | 框架内部 |

**实现示例**：
- **MdMemory**：基于 Markdown 文件的记忆存储，每个记忆项一个 `.md` 文件，简单可审计

---

### 3.5 MCPAdapter（MCP适配层）

**职责**：消费外部 MCP Server，经声明式/程序化转换后暴露工具。MCPAdapter 是 **DI 插件**，不注册即裁切 MCP 功能。

**接口方法**：
```
get_tools() → List[ToolDefinition]
execute(name: str, args: Dict[str, Any]) → ToolResult
shutdown() → None
```

**调用时机**：
- `get_tools()`：会话初始化阶段（通过内部 MCPClient 发现外部工具，经 ToolTransform 转换后返回）
- `execute()`：运行时（ToolRouter 按名路由到此处）
- `shutdown()`：会话结束阶段（关闭 MCP Server 子进程连接）

**内部转换管道**（两级）：
1. **ToolTransform（声明式）**：改名（`expose_as`）、隐藏（`hidden`）、注入默认值（`arg_defaults`），覆盖 90% 场景
2. **MCPHandler（程序化）**：当声明式不够用时提供 `transform_schema` / `transform_args` / `transform_result` 钩子

**实现示例**：
- **DefaultMCPAdapter**：接收 `MCPServerConfig` 列表和 `ToolTransform` 字典，管理 MCPClient 子进程生命周期和转换管道

---

### 3.6 Tool（工具执行层）

**职责**：工具的实际执行层。被 ToolRouter 通过 Provider 统一调度。

**接口方法**：
```
get_definition() → ToolDefinition       # name、description、parameters
execute(args: Dict[str, Any]) → ToolResult  # 工具执行
```

**关键设计**：Tool是框架内部执行抽象，用户不直接实现Tool接口（通过 SystemToolProvider 或 MCPAdapter 间接注入）。`BaseTool` ABC 和 `@inline_tool` 装饰器是辅助构建 Tool 实例的便利工具。

---

### 3.7 InputAdapter（输入适配器，batch-11 重设计）

**职责**：接收用户的原始输入并转化为标准化的请求；将编排器产生的事件流以前后台分离的方式呈现给用户。

**接口方法**：
```
receive() → UserRequest
send(event: AdapterEvent) → void
```

**调用时机**：
- `receive()`：会话初始化时调用，以及后续每轮用户有新输入时调用
- `send()`：编排器内层循环中按 LLM 输出字段顺序逐一推送事件。**不再**在每次 LLM 返回 text 时集中调用一次，而是每个语义单元（thinking、tool_call、tool_result、text、stop）即时推送

**事件类型**（batch-11 新增，定义在 `harness.interfaces.types`）：

| 事件 | 对应 LLM Response 字段 | 触发时机 | 前端分类 |
|------|----------------------|---------|---------|
| **ThinkingEvent** | `response.thinking` | thinking 非空时立即推送 | 后台（debug） |
| **ToolCallEvent** | `response.tool_uses[i]` | 每个 tool_use 执行前推送 | 后台 |
| **ToolResultEvent** | 工具执行完毕 | 每个 tool 执行完成后推送（含耗时/成功/错误） | 后台 |
| **TextEvent** | `response.text` | text 非空时推送 | **前台** |
| **StopEvent** | `response.stop_reason` | 内层循环 break 后推送 | 会话控制 |

**关键设计变迁（batch-11）**：
- `send()` 签名从 `send(response: Response)` 改为 `send(event: AdapterEvent)`，实现**前后台分离**
- 编排器**不再裸用 `logger.info("🔧 ...")`** 输出工具调用，改为推送 `ToolCallEvent` / `ToolResultEvent`
- `_summarize_args()` / `_summarize_result()` 从编排器迁移到前端实现（CliAdapter）
- 前端自主决定每个事件类型的呈现方式：`TextEvent` → stdout（前台对话），工具/thinking 事件 → stderr（后台状态），`StopEvent` → no-op
- `ThinkingEvent` 默认仅 debug 模式输出，不再被静默丢弃

**实现示例**：
- **CliAdapter**（已实现：[harness/components/input_adapter/cli_adapter.py](harness/components/input_adapter/cli_adapter.py)）：从 stdin 读取输入，`isinstance` 分发事件：TextEvent→stdout，其余→stderr

---

### 3.8 Hook（钩子系统）

**职责**：在框架生命周期的关键节点拦截并修改数据。每个 Hook 接收 HookContext，其中始终携带 `system_state` 和该阶段的数据对象。

**预留生命周期点（11 个）**：

```
before_guide_generation    → 修改 GuideContext
after_guide_generation     → 修改 GuidesBundle
before_assemble            → 修改 AssemblyContext
after_assemble             → 修改 Message 列表
before_llm_call            → 修改 Message 列表
after_llm_call             → 修改 Response
before_tool_execute        → 修改 ToolCall
after_tool_execute         → 修改 ToolResult
after_sensor               → 观察 Sensor 副作用（只读，在 Sensor.sense() 执行后触发）
on_error                   → 异常处理介入
on_session_end             → 会话结束清理
```

**核心用途**：
- Sensor 在会话结束阶段由框架直接调用（`on_session_end` Hook 触发后，执行 `Sensor.sense()`，然后再触发 `after_sensor` Hook）
- 用户可在不修改框架源码的情况下扩展行为
- 多个 Hook 可链式执行，单个 Hook 失败不阻塞后续 Hook

---

## 四、数据流详解

### 4.1 多轮对话数据流

一个完整会话由**多轮对话**组成，框架将数据流拆分为三个阶段：

#### 阶段一：会话初始化（整个会话只执行一次）

```
1. InputAdapter.receive() → UserRequest
   ↓
2. 框架构建 GuideContext（包含 UserRequest 的 text、attachments、context、system_state）
   → 调用 GuideProvider.get_guides() → GuidesBundle
   ↓
3. 框架从 MemoryBackend 检索相关记忆
   → memory.search(user_request.text, namespace="episodic")
   ↓
4. 框架初始化 ToolRouter，注册 SystemToolProvider 和 MCPAdapter（若已 DI 注册）
   → ToolRouter.list_tools() 返回合并后的工具定义列表
   ↓
5. 框架构建初始 AssemblyContext
   → 包含 user_request、guides、available_tools、history、memories、system_state
```

#### 阶段二：多轮对话循环

**外层循环** — 每轮用户输入时重新进入（用户每发一次新消息，`InputAdapter.receive()` 被调用一次）：

```
6. ContextAssembler.assemble() → List[Message]
   （组装当前会话完整上下文：user_request + guides + memories
    + available_tools + history + system_state）
   ↓
7. 触发 before_llm_call Hook
   ↓
   ┌─────────────────────────────────────────────────────┐
   │                                                     │
   │  内层循环 —— Toolcall 连续生成（同一轮对话内快速循环）│
   │                                                     │
   │  8. 框架将 messages 和 tools 转为 LLM 原生格式      │
   │     调用 LLM → Response                             │
   │     框架将 LLM 原生响应转为 Response 类型            │
   │     ↓                                               │
   │  9. 触发 after_llm_call Hook                        │
   │     ↓                                               │
   │  10. 按 LLM Response 字段顺序逐一推送事件：          │
   │      ├─ thinking → adapter.send(ThinkingEvent)      │
   │      │                                              │
   │      ├─ 包含 tool_uses（可与 text 共存）：           │
   │      │   → 对每个 tool_use 依次：                   │
   │      │     adapter.send(ToolCallEvent)              │
   │      │     → 触发 before_tool_execute Hook          │
   │      │     → ToolRouter.execute()（查表分发）        │
   │      │     → 触发 after_tool_execute Hook           │
   │      │     → adapter.send(ToolResultEvent)          │
   │      │   → 将 tool_use + tool_result 追加到 messages│
   │      │   → ↑ 回到步骤 8（继续内层循环）             │
   │      │                                              │
   │      └─ text → adapter.send(TextEvent)              │
   │          → adapter.send(StopEvent)                  │
   │          → 跳出内层循环 ↓                           │
   │                                                     │
   └─────────────────────────────────────────────────────┘
     ↓
11. 等待用户下一轮输入：
    → InputAdapter.receive() 获取新 UserRequest
    → 更新 AssemblyContext 中的 user_request 和 system_state
    → ↑ 回到步骤 6（重新进入外层循环，ContextAssembler.assemble() 完整重新执行）
    
    当用户发出退出信号（EOF / 特定指令）或框架收到终止信号时：
    → 进入阶段三
```

#### 阶段三：会话结束（整个会话只执行一次）

```
12. 框架组装完整 Trajectory（包含多轮所有对话记录、工具调用、思考过程）
    ↓
13. 触发 on_session_end Hook
    ↓
14. Sensor.sense(trajectory)
    → Sensor 通过构造注入的 MemoryBackend 自行决定写入内容
    ↓
15. 触发 after_sensor Hook（只读观察 Sensor 写入结果）
    ↓
16. ToolRouter.shutdown() → 统一清理各 Provider 资源（如 MCPAdapter 关闭 MCP Server 连接）
    ↓
17. 会话结束
```

**关键设计**：
- `GuidesBundle`、`available_tools`、`memories` 在阶段一获取后**缓存复用**，不随每轮重新构建
- Sensor 在**会话结束阶段**统一评估完整的多轮 Trajectory，而非每轮触发
- **外层循环**（步骤 6→11）：用户每次新输入触发 `InputAdapter.receive()`，然后完整执行 `ContextAssembler.assemble()`
- **内层循环**（步骤 8→10）：`tool_use` 触发同一轮内的快速循环，tool result 直接追加到当前 message list 后回传 LLM 继续生成，**不重新走 `ContextAssembler.assemble()`**
- **事件驱动推送**（batch-11）：编排器按 LLM Response 字段顺序逐一推送 `AdapterEvent`（`ThinkingEvent` → `ToolCallEvent` → `ToolResultEvent` → `TextEvent` → `StopEvent`），前端自主决定每个事件类型的呈现方式（前后台分离）
- 多 tool 场景下，`ToolRouter` **按顺序串行执行**，每个 Tool 独立触发 before/after_tool_execute Hook，并在执行前后推送 `ToolCallEvent` / `ToolResultEvent`
- LLM 单次响应可**同时包含 text 和 tool_uses**（非互斥），框架分别处理
- 框架内部包含**轻量转换层**：将框架类型（Message、ToolDefinition、ToolResult）与 LLM 原生格式互转

### 4.2 跨会话记忆流动

```
会话 N（结束阶段）:
  Sensor 评估完整多轮 Trajectory → 写入 MemoryBackend (namespace="episodic")

会话 N+1（初始化阶段）:
  框架从 MemoryBackend 检索记忆 → 填入 AssemblyContext.memories
  → ContextAssembler 将记忆融入上下文
  → 影响会话 N+1 的 LLM 输入
```

**关键约束**：Sensor 和 ContextAssembler 不直接通信，通过 MemoryBackend 解耦。Sensor 在**会话结束时**统一写入，供**后续会话初始化**时读取。

---

## 五、配置与装配

### 5.1 三层配置模型

| 层级 | 职责 | 载体 |
|------|------|------|
| **元数据层** | 标识"使用哪个领域模板" | `profile.toml`（TOML） |
| **装配层（声明式）** | 定义"组件如何实例化、依赖如何注入"（80% 场景） | `harness.yaml`（YAML） |
| **装配层（命令式）** | 复杂场景的编程装配（20% 场景） | Python API（`DIContainer`） |

### 5.2 TOML 元数据文件

仅作轻量身份标识，不承担装配逻辑：

```toml
[meta]
name = "my-coding-agent"
description = "个人编程助手"
template = "coding-assistant"
version = "0.1.0"

[modules]
input_adapter = true
guide_provider = true
context_assembler = true
system_tool_provider = true
mcp_adapter = false
sensor = true
memory_backend = true
```

### 5.3 YAML 装配文件（声明式，batch-10 新增）

覆盖 80% 的简单装配场景。用户通过 YAML 声明组件、参数和依赖关系，`YamlAssembler` 自动构建 DI 容器：

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
        memory: MemoryBackend   # ← 引用已注册的组件

    - interface: Sensor
      implementation: harness.components.sensor.LoggingSensor
      inject:
        memory: MemoryBackend

    - interface: SystemToolProvider
      implementation: harness.components.tool.DefaultSystemToolProvider

  hooks:
    - event: before_llm_call
      handler: my_project.hooks.log_request

  llm:
    provider: openai
    model: gpt-4o

# 使用:
#   python main.py run --config harness.yaml
```

### 5.4 依赖注入容器（命令式，Python API）

DI 容器采用**预构造实例注册**模式。YAML 装配是此 API 的上层封装，两者产出同一个 `DIContainer`：

```python
# 1. 创建共享基础设施（同一个 MemoryBackend 实例）
memory = MdMemory(path="./memory")

# 2. 创建组件实例（构造函数注入依赖）
input_adapter = CliAdapter()
guide_provider = FileGuideProvider(paths=["AGENTS.md"])
context_assembler = SimpleAssembler(memory=memory, max_history=50)
sensor = LoggingSensor(memory=memory)

# 3. 注册到容器
container = DIContainer()
container.register(InputAdapter, input_adapter)
container.register(GuideProvider, guide_provider)
container.register(ContextAssembler, context_assembler)
container.register(Sensor, sensor)
container.register(MemoryBackend, memory)
container.register(SystemToolProvider, DefaultSystemToolProvider())

# MCP 适配层（不注册即裁切）
container.register(MCPAdapter, DefaultMCPAdapter(
    mcp_configs=[MCPServerConfig(name="fs", command="npx",
                   args=["-y", "@anthropic/mcp-filesystem", "/tmp"])],
    transforms={},
))

# 4. 启动 Harness
harness = Harness.from_container(container, call_llm=my_llm)
harness.run()
```

**设计意图**：
- YAML 是"声明装配"，Python API 是"编程装配"——两者互补，产出同一个 `DIContainer`
- 用户通过构造函数参数完全控制组件行为
- 框架不关心组件如何创建，只关心它们满足接口契约
- 同一个实例注册到容器，自然保证 MemoryBackend 在 ContextAssembler 和 Sensor 之间共享
- SystemToolProvider 和 MCPAdapter 是独立的 DI 插件，不注册即裁切

---

## 六、领域模板结构

每个领域模板是一个独立的文件夹，包含预设的组件装配方案和 Agent 指导文件：

```
agents/
├── coding-assistant/
│   ├── profile.toml          # 模板元数据（ConfigLoader）
│   ├── harness.yaml          # DI 装配声明（YamlAssembler）
│   ├── AGENTS.md             # Agent 指导文件（身份+规则）
│   └── README.md             # 使用说明与自定义指南
│
├── chat-web/                 # WebSocket 网页聊天助手（showcase）
│   ├── AGENTS.md
│   ├── README.md
│   ├── server.py
│   ├── adapter/
│   ├── static/
│   ├── tools/
│   └── tests/
│
└── trajectory-analyst/       # 轨迹分析元 Agent（计划中）
```

**两种配置文件的分工**：
- `profile.toml` — 轻量模板元数据（name, description, template, version, modules），由 `ConfigLoader` 读取
- `harness.yaml` — 完整的 DI 装配声明（组件、Hook、LLM 配置），由 `YamlAssembler` 读取并自动构建容器

**使用方式**：

```bash
# 从模板创建项目
python main.py init --profile coding-assistant my-project

# 生成的项目结构
cd my-project
ls
# harness.yaml     ← 编辑此文件替换组件/添加 Hook
# profile.toml     ← 模板元数据
# AGENTS.md        ← 编辑此文件配置 Agent 行为
# README.md        ← 使用说明

# 启动 Agent
python ../main.py run --config harness.yaml
```

---

## 七、扩展机制

### 7.1 接口扩展

用户可定义领域特定的子接口：

```python
# 用户在项目中定义
interface CodeContextAssembler extends ContextAssembler:
    # 可添加代码领域特有的方法或属性约定
    # 框架仍只调用标准的 assemble() 方法
```

### 7.2 Metadata 扩展桶

多个大包对象（UserRequest、GuideContext、AssemblyContext、Trajectory、SystemState）均包含 `metadata: Dict[str, Any]` 字段：
- 框架不解释 metadata 内容
- 组件实现者可约定特定 key 的含义
- 允许领域特定信息在组件间传递，而不污染通用接口

### 7.3 Hook 扩展

用户通过 Hook 在框架生命周期中插入任意逻辑，包括：
- 自定义 Sensor 调用时机
- 跨组件的协调逻辑
- 外部系统集成（日志上报、监控等）

---

## 八、设计原则总结

| 原则 | 体现 |
|------|------|
| **接口隔离** | 每个组件有明确的单一职责和接口契约 |
| **依赖倒置** | 高层模块（框架）依赖抽象（接口），低层模块（用户实现）实现抽象 |
| **开闭原则** | 框架对扩展开放（通过接口实现和 Hook），对修改封闭 |
| **控制反转** | 框架调用用户代码，而非用户代码调用框架 |
| **数据流解耦** | Sensor 与 ContextAssembler 通过 MemoryBackend 间接通信，不直接耦合 |
| **约定优于配置** | 命名空间、metadata key 等采用社区约定，不强制约束 |

---

## 九、与现有方案的区别

| 维度 | 本方案 | Claude Code / OpenHarness | Auton / AgentForge |
|------|--------|---------------------------|-------------------|
| **核心抽象** | 接口契约 + DI 装配 | 全功能集成 | 声明规范 + 标准 |
| **用户角色** | 组件开发者 | 终端用户 | 规范遵循者 |
| **裁剪粒度** | 组件级 + 策略级 | 不可裁剪 | 配置参数级 |
| **扩展方式** | 实现接口 + Hook | 插件/Hook | SDK 实现 |
| **配置复杂度** | 低（TOML 仅标识） | 中（JSON/YAML 配置） | 高（完整规范） |
| **目标用户** | 个人开发者、小型团队 | 专业开发者 | 企业/研究团队 |

---

## 十、会话持久化与恢复

- 内核机制（非插件），默认开启；配置面仅 `sessions.root` / `sessions.enabled`
- 存储：`./.harness/sessions/<conv_id>/agents/<pid>.jsonl`（append-only 事实，唯一不可丢）
  + `index.json`（原子重写的投影，可从 jsonl 重建）
- 热路径零 I/O：内存即时、轮次边界批量 flush（page cache）、fsync 仅在
  finalize/close；单写协程 per 文件
- 三套序号：seq（文件内严格连续，缺号=损坏）/ msg_id（跨日志因果配对）/
  LSN（会话级单调，空洞=崩溃损失证据）
- 恢复：`--resume <conv_id>`（可加 --force）；boot 四步序
  "创建所有 → 种子 → 配对修复 → 启动所有"；manifest 分级校验
  （语义关键不一致硬失败，--force 降级）
- 崩溃语义：尾部半行加载时截断；中断工具调用注入仅内存的恢复标记；
  已发未收消息按 msg_id 配对补投
