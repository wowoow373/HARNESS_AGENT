# 02 — 接口契约汇总

> **唯一真相源**。所有批次实现组件时，以此文件中的签名为准。组件间通过此契约解耦，装配时按此校验。
>
> **版本: 1.1** — 含 batch-11 InputAdapter 事件驱动重设计

---

## 通用大包对象

这些是跨组件传递的数据结构，在此统一定义。

### UserRequest

框架通过 `InputAdapter.receive()` 填充。每次用户输入时调用一次。

```
UserRequest:
  text         : str              — 用户主输入文本
  attachments  : List[Attachment] — 附件列表（文件、图片、链接等）
  context      : Dict[str, Any]   — 附加上下文（地理位置、当前文件等）
  system_state : SystemState      — 系统当前状态
  session_id   : str              — 会话标识
  metadata     : Dict[str, Any]   — 领域扩展桶，框架不解释
```

### SystemState

框架维护，贯穿整个生命周期。

```
SystemState:
  phase        : str              — 当前阶段（"init" | "loop" | "end"）
  session_id   : str              — 会话标识
  run_mode     : str              — 运行模式（"normal" | "debug" | "dry_run"）
  metadata     : Dict[str, Any]   — 扩展桶
```

phase 取值说明：
- `"init"` — 会话初始化阶段（Phase 1）
- `"loop"` — 多轮对话循环阶段（Phase 2）
- `"end"`  — 会话结束阶段（Phase 3）

### Attachment

```
Attachment:
  type    : str   — "file" | "image" | "url"
  content : Any   — 根据 type 不同（文件路径 / 图片数据 / URL字符串）
  meta    : Dict[str, Any]
```

### EnvState

环境状态，仅在 GuideContext 中传递给 GuideProvider。其他组件如需环境信息应从 GuideProvider 的产出（GuidesBundle）中间接获取。

```
EnvState:
  work_dir     : str              — 工作目录
  git_status   : Dict[str, Any]   — Git 状态摘要
  timestamp    : float            — 当前时间戳
  platform     : str              — "linux" | "macos" | "windows"
```

### GuidesBundle

由 `GuideProvider.get_guides()` 产出。

```
GuidesBundle:
  identity     : str              — 核心身份定义（如 "You are a coding assistant..."）
  capabilities : List[str]        — 能力清单
  rules        : List[str]        — 行为规则列表
  constraints  : List[str]        — 硬约束列表
  examples     : List[Example]    — 少样本示例（可选）
```

### Example

```
Example:
  input       : str               — 示例输入
  output      : str               — 示例预期输出
```

### AssemblyContext

框架构建的上下文大包，传入 `ContextAssembler.assemble()`。

```
AssemblyContext:
  user_request   : UserRequest         — 当前用户请求
  guides         : GuidesBundle        — 来自 GuideProvider
  available_tools: List[ToolDefinition]— 来自 ToolRouter（name, description, parameters）
  history        : List[Message]       — 当前会话的对话历史
  memories       : List[MemoryItem]    — 从 MemoryBackend 检索的记忆
  system_state   : SystemState         — 系统当前状态
  metadata       : Dict[str, Any]      — 领域扩展桶，框架不解释
```

注：`user_request` 内也含 `system_state`，顶层 `system_state` 为框架组装时的最新状态。两者通常一致，顶层字段可视为组装快照。

### Trajectory

会话结束后由框架组装，传入 `Sensor.sense()`。

```
Trajectory:
  session_id     : str                  — 会话标识
  history        : List[Message]       — 完整事件流: user → assistant(+tool_calls) → tool_result → assistant(text) → ...
  tool_calls     : List[ToolCallRecord]— 所有工具调用记录与执行结果（通过 tool_call_id 与 history 中的 ToolCall 对齐）
  final_output   : str                 — Agent 最终输出
  execution_time : float               — 执行耗时（秒）
  system_state   : SystemState         — 系统当前状态
  metadata       : Dict[str, Any]      — 扩展桶
```

### Message

对话消息单元。history 中的完整事件流通过 Message 表达：user 文本消息、assistant 文本消息（可携带 tool_calls）、tool 执行结果消息。

```
Message:
  role         : str   — "system" | "user" | "assistant" | "tool"
  content      : str   — 消息文本内容
  tool_call_id : Optional[str] — 当 role="tool" 时，关联的 ToolCall.id
  tool_calls   : Optional[List[ToolCall]] — 当 role="assistant" 时，携带的并行工具调用列表
```

### Response

单轮 LLM 调用返回。LLM 响应可同时包含文本和工具调用。

```
Response:
  text       : Optional[str]      — LLM 文本输出
  thinking   : Optional[str]      — 思考/推理过程（如有）
  tool_uses  : List[ToolCall]     — 工具调用列表（可为空）
  stop_reason: str                — 停止原因（"end_turn" | "tool_use" | "max_tokens" 等）
```

### ToolCall

单次工具调用请求（执行前）。遵循 OpenAI 原生 tool call 格式。

```
ToolCall:
  id        : str                  — 工具调用唯一标识
  type      : str                  — 固定为 "function"
  function  : ToolCallFunction     — 函数名与参数
```

### ToolCallFunction

```
ToolCallFunction:
  name      : str                  — 函数名
  arguments : str                  — JSON 编码的参数串
```

### ToolDefinition

Tool 的元信息，用于 LLM 的 tool schema 生成和 ToolRouter 发现。

```
ToolDefinition:
  name        : str
  description : str
  parameters  : Dict[str, Any]   — JSON Schema 格式的参数定义
```

### ToolCallRecord

单次工具调用的完整执行记录（执行后）。

```
ToolCallRecord:
  tool_call_id : str               — 关联的 ToolCall.id，用于与 history 对齐
  tool_name    : str
  arguments    : Dict[str, Any]
  result       : Any
  started_at   : float            — 执行开始时间戳
  finished_at  : float            — 执行完成时间戳
  error        : Optional[str]    — 如果执行失败，记录错误信息
```

### ToolResult

工具执行返回结果。

```
ToolResult:
  success : bool
  content : Any                 — 成功时返回的工具结果
  error   : Optional[str]       — 失败时的错误信息
```

### ToolTransform

单个 MCP 工具的声明式转换配置。由用户在装配 MCPAdapter 时提供。

```
ToolTransform:
  expose_as             : Optional[str]      — 重命名暴露（如 "filesystem_read" → "read_remote_file"）
  description_override  : Optional[str]      — 覆盖工具描述使其更适配 LLM
  hidden                : bool = False        — 设为 True 则该工具对 LLM 不可见
  arg_defaults          : Dict[str, Any]      — 参数默认值注入（如注入 cwd、认证信息）
  arg_transform         : Optional[Callable]  — 高级：程序化参数转换（优先于 arg_defaults）
  result_transform      : Optional[Callable]  — 高级：程序化结果转换（脱敏、格式化等）
```

注：`arg_transform` 和 `result_transform` 为高级程序化转换钩子。当声明式配置（`expose_as`、`arg_defaults` 等）不够用时使用。两者均为可选，默认不启用。

### Adapter 事件类型（batch-11 新增）

`InputAdapter.send()` 的入参。5 种独立 dataclass，通过 `AdapterEvent` Union 别名约束。

```
AdapterEvent = Union[ThinkingEvent, ToolCallEvent, ToolResultEvent, TextEvent, StopEvent]

ThinkingEvent:
  content : str = ""                  — LLM 思考/推理过程（后台）

ToolCallEvent:
  call_id   : str = ""                — 工具调用唯一标识
  tool_name : str = ""                — 工具名称
  arguments : Dict[str, Any]          — 调用参数

ToolResultEvent:
  call_id     : str = ""              — 工具调用唯一标识
  tool_name   : str = ""              — 工具名称
  success     : bool = True           — 执行是否成功
  result      : Any = None            — 成功时的结果
  error       : Optional[str] = None  — 失败时的错误信息
  duration_ms : float                 — 执行耗时（毫秒）

TextEvent:
  content : str = ""                  — 模型文本回复（前台对话）

StopEvent:
  stop_reason : str = "end_turn"      — 本轮结束原因
```

设计决策：使用独立 dataclass + Union 而非基类继承，与代码库现有风格一致，`isinstance` / `match-case` 分发无需基类。

### MemoryItem

从 MemoryBackend 检索出的记忆项。

```
MemoryItem:
  key       : str
  value     : Any
  namespace : str
  timestamp : float              — 写入时间戳
  metadata  : Dict[str, Any]
```

---

## 组件接口

---

### InputAdapter（batch-11 重设计）

职责：输入输出适配。接收用户原始输入转为标准化请求，将编排器产生的事件流以前后台分离的方式呈现给用户。

```
interface InputAdapter:
    receive() → UserRequest
    send(event: AdapterEvent) → void
```

**AdapterEvent 类型**（`Union[ThinkingEvent, ToolCallEvent, ToolResultEvent, TextEvent, StopEvent]`）：

| 事件 | 对应 LLM Response 字段 | 前端分类 |
|------|----------------------|---------|
| `ThinkingEvent(content)` | `response.thinking` | 后台（仅 debug 模式） |
| `ToolCallEvent(call_id, tool_name, arguments)` | `response.tool_uses[i]` | 后台 |
| `ToolResultEvent(call_id, tool_name, success, result, error, duration_ms)` | 工具执行完毕 | 后台 |
| `TextEvent(content)` | `response.text` | **前台** |
| `StopEvent(stop_reason)` | `response.stop_reason` | 会话控制 |

调用时机：
- `receive()`：会话初始化时由框架调用，以及后续每轮用户有新输入时由框架调用
- `send()`：编排器内层循环中按 LLM Response 字段顺序逐一调用（不再攒批，每字段即时推送）

**关键设计变迁（batch-11）**：
- `send()` 签名从 `send(response: Response)` 改为 `send(event: AdapterEvent)`
- 编排器不再裸用 `logger.info("🔧 ...")` 输出工具调用，改为推送 `ToolCallEvent` / `ToolResultEvent`
- 前端自主决定每个事件类型的呈现方式（stdout/stderr/TUI 面板），实现前后台分离

实现示例：`CliAdapter` — stdin 读取输入，`isinstance` 分发事件：TextEvent→stdout（前台），其余→stderr（后台）

---

### GuideProvider

职责：在 Agent 行动前提供所有指导性输入。

```
interface GuideProvider:
    get_guides(context: GuideContext) → GuidesBundle
```

GuideContext：
```
GuideContext:
  user_request : UserRequest    — 用户当前请求
  system_state : SystemState    — 系统当前状态（含 phase）
  env_state    : EnvState       — 环境状态
  metadata     : Dict[str, Any]
```

注：GuideContext 不再包含独立的 `phase` 字段，统一使用 `system_state.phase`。

调用时机：会话初始化时由框架调用，只调用一次。产出 GuidesBundle 后被框架缓存复用。

实现示例：`FileGuideProvider` — 从文件系统读取静态配置（如 AGENTS.md）

---

### ContextAssembler

职责：将 Harness 所有信息源组装成发给 LLM 的最终消息列表。

```
interface ContextAssembler:
    assemble(inputs: AssemblyContext) → List[Message]
```

调用时机：每轮外层循环开始时（用户有新输入），框架调用一次。

MemoryBackend 通过构造函数注入（可选，用于增强检索）：
```python
class SimpleAssembler:
    def __init__(self, memory: MemoryBackend):
        self.memory = memory  # 可选引用，用于超越框架基线的定制检索
```

设计说明：
- **框架基线**：框架在会话初始化阶段（Phase 1）自动执行 `memory.search(user_request.text, namespace="episodic")`，结果填入 `AssemblyContext.memories`。该结果在会话期间缓存复用。ContextAssembler 的最低实现只需消费 `AssemblyContext.memories`，**无需**持有 MemoryBackend 引用。
- **组件增强**：当 ContextAssembler 需要超越框架基线的检索策略时（如跨 namespace 检索 `semantic`/`procedural`、使用不同 query 策略），可通过构造函数注入 MemoryBackend 并在 `assemble()` 内执行额外检索。此时可自行决定如何合并/去重/忽略 `AssemblyContext.memories` 中的框架基线结果。

实现示例：`SimpleAssembler` — 滑动窗口截断 + 直接拼接 guides、memories、history

---

### MemoryBackend

职责：跨会话的持久化存储与检索。

```
interface MemoryBackend:
    read(key: str, namespace: str) → Optional[Any]
    write(key: str, value: Any, namespace: str) → void
    search(query: str, namespace: str, limit: int = 10) → List[MemoryItem]
    list_namespaces() → List[str]
```

调用时机：
- 会话初始化阶段：框架从 MemoryBackend 检索相关记忆（`search()`），填入 AssemblyContext
- 会话结束阶段：Sensor 调用 `write()` 写入知识

命名空间约定（非强制，社区约定）：

| namespace | 用途 | 典型写入者 | 典型读取者 |
|-----------|------|-----------|-----------|
| `episodic` | 事件记忆（对话摘要） | Sensor | ContextAssembler |
| `semantic` | 事实知识（用户偏好） | Sensor | ContextAssembler |
| `procedural` | 技能/可复用模式 | Sensor | ContextAssembler |
| `sensor_raw` | Sensor 原始评估 | Sensor | Sensor（跨会话） |
| `system` | 系统状态缓存 | Framework | 框架内部 |

实现示例：`MdMemory` — 基于 Markdown 文件的记忆存储，每个记忆项一个 `.md` 文件，启动时构建内存索引

---

### Sensor

职责：读取完整执行轨迹，按自定义规则评估，将沉淀的知识写入 MemoryBackend。

```
interface Sensor:
    sense(trajectory: Trajectory) → void
```

调用时机：会话结束阶段，在 `on_session_end` Hook 触发之后、`after_sensor` Hook 触发之前，由框架调用。

设计要点：
- Sensor 是**副作用组件**，不显式返回值给框架
- Sensor 通过**构造函数注入**获得 MemoryBackend 引用（与 ContextAssembler 一致）
- Sensor 在会话结束时统一评估**完整的多轮 Trajectory**
- 用户可在 Sensor 内部接入另一个 Agent 做复杂评估

```python
class LoggingSensor:
    def __init__(self, memory: MemoryBackend):
        self.memory = memory

    def sense(self, trajectory: Trajectory) -> None:
        # 通过 self.memory.write(...) 写入
        ...
```

实现示例：`LoggingSensor` — 将轨迹记录到 MemoryBackend 的 `episodic` 命名空间

---

### Tool

职责：单个工具的实际执行层，被 ToolRouter 统一调度。

```
interface Tool:
    get_definition() → ToolDefinition
    execute(args: Dict[str, Any]) → ToolResult
```

调用时机：
- `get_definition()`：会话初始化阶段（ToolRouter 通过 Provider 收集工具元信息）
- `execute()`：运行时（LLM 请求执行工具时）

设计要点：Tool 是实现细节，框架用户通过 SystemToolProvider 提供本地 Tool，通过 MCPAdapter 提供 MCP 来源 Tool。`BaseTool` ABC 和 `@inline_tool` 装饰器是辅助构建 Tool 实例的便利工具。

---

### ToolRouter

职责：框架内部组件（非 DI，不由用户替换）。合并 SystemToolProvider 和 MCPAdapter，维护 `tool_name → provider` 路由表，统一对外提供工具列表和按名分发执行。

```
class ToolRouter:
    _routes: Dict[str, ToolProvider]  # tool_name → provider

    register_provider(provider: ToolProvider) → None
    list_tools() → List[ToolDefinition]
    execute(name: str, args: Dict[str, Any]) → ToolResult
    shutdown() → None
```

调用时机：
- `register_provider()`：会话初始化阶段（编排器将已解析的 SystemToolProvider 和 MCPAdapter 注册进去）
- `list_tools()`：会话初始化阶段（合并所有 Provider 的工具列表给 ContextAssembler）
- `execute()`：运行时（LLM 请求执行工具时，查表分发到对应 Provider）
- `shutdown()`：会话结束阶段（分发到各 Provider 清理，如 MCPAdapter 关闭 MCP Server 子进程连接）

设计要点：
- ToolRouter 由编排器在 `_phase_init()` 中直接实例化，不经过 DI 容器
- 执行前后触发 `before_tool_execute` / `after_tool_execute` Hook
- 同名工具后注册的覆盖先注册的，SystemToolProvider 先注册（MCPAdapter 可覆盖系统工具）

---

### SystemToolProvider

职责：系统工具提供者 — 管理本地实现的 Tool 集合。DI 插件，用户可替换。

```
interface SystemToolProvider:
    get_tools() → List[ToolDefinition]
    execute(name: str, args: Dict[str, Any]) → ToolResult
```

调用时机：
- `get_tools()`：会话初始化阶段（ToolRouter 收集工具元信息）
- `execute()`：运行时（ToolRouter 按名路由到此处）

默认实现：`DefaultSystemToolProvider` — 内置 `ReadFileTool`、`WriteFileTool`、`ShellTool`，用户可通过继承或装饰器（`@inline_tool`）扩展。

---

### MCPAdapter

职责：MCP 适配层 — 消费外部 MCP Server，经声明式/程序化转换后暴露工具。DI 插件，不注册即裁切 MCP 功能。

```
interface MCPAdapter:
    get_tools() → List[ToolDefinition]
    execute(name: str, args: Dict[str, Any]) → ToolResult
    shutdown() → None
```

调用时机：
- `get_tools()`：会话初始化阶段（通过内部 MCPClient 发现外部工具，经 ToolTransform 转换后返回）
- `execute()`：运行时（ToolRouter 按名路由到此处，内部经 MCPHandler 程序化转换后执行）
- `shutdown()`：会话结束阶段（关闭 MCP Server 子进程连接）

默认实现：`DefaultMCPAdapter` — 接收 `MCPServerConfig` 列表和 `ToolTransform` 字典，管理 MCPClient 子进程生命周期和转换管道。

内部转换管道（两级）：
1. **ToolTransform（声明式）**：改名（`expose_as`）、隐藏（`hidden`）、注入默认值（`arg_defaults`），覆盖 90% 场景
2. **MCPHandler（程序化）**：当声明式不够用时提供 `transform_args` / `transform_result` 钩子

---

### MCPHandler

职责：MCP 程序化转换处理器 — 当 ToolTransform 声明式配置不够用时，提供 `transform_schema` / `transform_args` / `transform_result` 三个钩子进行程序化转换。

```
interface MCPHandler:
    transform_schema(name: str, schema: Dict) → Dict
    transform_args(name: str, args: Dict) → Dict
    transform_result(name: str, result: Any) → Any
```

调用时机：在 MCPAdapter 内部调用，分别对应 schema 获取、工具执行前参数转换、工具执行后结果转换三个阶段。

设计要点：MCPHandler 是可选的。用户实现此 Protocol 后注入到 `DefaultMCPAdapter` 的 `handler` 参数。典型用例：统一认证注入（`transform_args` 中注入 token）、动态结果脱敏（`transform_result` 中过滤敏感字段）。

---

### Hook

职责：在框架生命周期的关键节点拦截并修改数据。

Hook 是函数类型，签名如下：
```
Hook = Callable[[HookContext], None]
# 通过修改 HookContext.data 实现拦截效果
```

HookContext（所有 Hook 点统一使用此结构）：
```
HookContext:
  event        : str              — 生命周期事件名
  data         : Any              — 该阶段的数据对象（可修改）
  system_state : SystemState      — 系统当前状态（所有 Hook 均可访问）
```

Hook 点列表（事件名 → data 类型），共 **11 个**：

| 事件名 | data 类型 | 用途 |
|--------|---------|------|
| `before_guide_generation` | GuideContext | 修改 GuideContext |
| `after_guide_generation` | GuidesBundle | 修改 GuidesBundle |
| `before_assemble` | AssemblyContext | 修改 AssemblyContext |
| `after_assemble` | List[Message] | 修改 Message 列表 |
| `before_llm_call` | List[Message] | 修改发往 LLM 的消息 |
| `after_llm_call` | Response | 修改 LLM 响应 |
| `before_tool_execute` | ToolCall | 修改工具调用参数 |
| `after_tool_execute` | ToolResult | 修改工具执行结果 |
| `after_sensor` | Trajectory | 只读观察 Sensor 执行后的副作用 |
| `on_session_end` | Trajectory | 会话结束清理 |
| `on_error` | Exception | 异常处理介入 |

**触发顺序**：
- Sensor 调用顺序：`on_session_end` Hook 触发 → 框架调用 `Sensor.sense(trajectory)` → `after_sensor` Hook 触发
- 多 tool 场景：每个 Tool 执行前后各触发一次 `before_tool_execute` / `after_tool_execute`
- 单个 Hook 失败不阻塞后续 Hook 执行

---

## 跨组件交互约束

- ContextAssembler **只从 MemoryBackend 读取**，永不直接接触 Sensor
- Sensor **直接操作 MemoryBackend**（构造注入），其评估结果通过记忆层间接影响后续会话上下文
- **记忆检索**：框架在会话初始化阶段（Phase 1）自动执行基线检索（`namespace="episodic"`）并填入 `AssemblyContext.memories`，结果在会话期间缓存复用；ContextAssembler 可通过构造注入的 MemoryBackend 在 `assemble()` 内执行额外定制检索（跨 namespace、不同 query 策略），此时自行决定如何使用/合并/忽略框架基线结果
- GuidesBundle、available_tools 在初始化阶段获取后**缓存复用**，不随每轮重新构建
- 内层循环（tool_use 连续生成）**不走 ContextAssembler.assemble()**，tool result 直接追加到 message list
- 多 Tool 场景下，ToolRouter **按顺序串行执行**，每个 Tool 独立触发 before/after_tool_execute Hook
- SystemToolProvider 和 MCPAdapter 是独立的 DI 插件，各自可通过 DI 注册或替换
- MCPAdapter 不注册即裁切 MCP 功能，编排器正常降级
- LLM 单次响应可同时包含 text 和 tool_uses，框架分别处理两者
- 框架内部包含轻量转换层：将 Message、ToolDefinition、ToolResult 与 LLM 原生格式互转
- DI 容器采用**预构造实例注册**模式：`container.register(Interface, instance)`，用户手动管理依赖注入
