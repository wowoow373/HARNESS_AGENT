# batch-01 — MVP 设计文档

> **目标**：构建框架 MVP（最小可行产品）— DI 容器、生命周期编排器、配置加载器、LLM 适配器。这是所有后续批次的基石。MVP 即可独立运行，不需要等到所有批次完成。
>
> **依赖**：无（第一批次）
>
> **产出**：`harness/core/*`、`harness/interfaces/*`、`harness/adapters/*`、`harness/config/*`、`harness/messaging/*`、`harness/di.py`

---

## 一、范围定义

### 1.1 做什么

| 模块 | 职责 | 产出文件 |
|------|------|---------|
| **DIContainer** | 预构造实例注册与按接口类型解析 | `harness/core/container.py` |
| **LifecycleOrchestrator** | 按三阶段（初始化→循环→结束）编排组件调用 | `harness/core/orchestrator.py` |
| **异常体系** | 框架所有异常的基类和核心异常类 | `harness/core/exceptions.py` |
| **内部数据类型** | 编排器内部使用的数据结构定义 | `harness/core/types.py` |
| **组件接口类型** | 占位接口类型，作为 DI 容器的注册 key | `harness/interfaces/__init__.py` |
| **MinimalLLMAdapter** | 零依赖 OpenAI 兼容 LLM 适配器 | `harness/adapters/llm_adapter.py` |
| **ConfigLoader** | 读取 TOML 配置文件，返回结构化配置对象 | `harness/config/loader.py` |
| **消息构造工具** | OpenAI 兼容格式的消息构造函数 | `harness/messaging/builder.py` |
| **装配入口** | `Harness.from_container()` 工厂方法 + `harness/di.py` | `harness/di.py` |

### 1.2 不做什么

- ❌ 任何组件具体实现（那是 batch-03 ~ 08）
- ❌ Hook 系统（那是 batch-09）
- ❌ CLI 入口 / `main.py`（那是 batch-10）
- ❌ 正式的 Protocol/ABC 接口定义（后续版本）

---

## 二、DIContainer 设计

### 2.1 设计原则

采用**预构造实例注册**模式：用户创建组件实例并手动注入依赖，然后注册到容器。容器仅负责存储和按接口类型解析，不管理对象生命周期。

```
设计意图：
  - 个人开发者可以完全控制组件创建
  - 无需理解复杂的 DI 作用域概念（singleton/transient/scoped）
  - 同一个实例注册后可被框架在多个位置使用
    （如 MemoryBackend 被 ContextAssembler 和 Sensor 共享）
```

### 2.2 接口设计

```python
class DIContainer:
    """依赖注入容器 — 预构造实例注册模式。

    职责：存储已创建的组件实例，按接口类型解析。
    不管理对象生命周期，不创建实例。
    """

    def register(self, interface: type, instance: Any) -> None:
        """注册一个组件实例。

        Args:
            interface: 组件的抽象接口类型（用于 resolve 时的 key）
            instance: 已创建的组件实例

        Raises:
            DuplicateRegistrationError: 同一接口类型已注册过
        """
        ...

    def resolve(self, interface: type) -> Any:
        """按接口类型解析并返回已注册的实例。

        Args:
            interface: 组件的抽象接口类型

        Returns:
            已注册的组件实例

        Raises:
            ComponentNotRegisteredError: 接口类型未注册
        """
        ...

    def is_registered(self, interface: type) -> bool:
        """检查接口类型是否已注册。"""
        ...

    def list_registered(self) -> Dict[type, Any]:
        """返回所有已注册的 (接口类型 → 实例) 映射。

        Returns:
            Dict[type, Any]: 注册表副本
        """
        ...
```

### 2.3 关键设计决策

| 决策 | 理由 |
|------|------|
| key 用 `type` 而非 `str` | 编译期可检查，避免字符串拼写错误 |
| 不允许覆盖注册 | 防止意外替换；如需替换，先明确判断或提供 `replace` 参数 |
| 不管理生命周期 | 简单；用户控制创建和销毁 |
| `resolve()` 抛异常而非返回 None | 尽早暴露配置错误（fail-fast） |

### 2.4 内部数据结构

```python
# 容器内部存储
_registry: Dict[type, Any] = {}   # interface_type → instance
```

### 2.5 边界条件

- 注册 `None` 实例 → 抛出 `ValueError`（不允许空实例）
- 用非类型参数调用 `register(interface="foo", ...)` → 抛出 `TypeError`
- `resolve()` 未注册类型 → 抛出 `ComponentNotRegisteredError`，消息中包含接口类型名
- 多线程安全：v1 不保证（batch-01 单线程），后续按需添加

---

## 三、LifecycleOrchestrator 设计

### 3.1 三阶段编排模型

编排器是框架的核心控制流引擎。它从 DI 容器解析组件，按固定顺序调用它们。

```
阶段一：会话初始化（Session Init）
  ┌────────────────────────────────────────────┐
  │ 1. 从容器 resolve(InputAdapter)            │
  │ 2. 调用 InputAdapter.receive() → UserRequest│
  │ 3. 从容器 resolve(GuideProvider)           │
  │ 4. 构建 GuideContext → GuideProvider        │
  │    .get_guides() → GuidesBundle (缓存)     │
  │ 5. 从容器 resolve(MemoryBackend)           │
  │ 6. MemoryBackend.search(                   │
  │      user_request.text, "episodic")         │
  │    → List[MemoryItem] (填入 AssemblyContext)│
  │ 7. 创建 ToolRouter（框架内部）              │
  │ 8. 从容器 resolve_optional(                 │
  │      SystemToolProvider)                     │
  │    → ToolRouter.register_provider()          │
  │ 9. 从容器 resolve_optional(MCPAdapter)       │
  │    → ToolRouter.register_provider()          │
  │ 10. ToolRouter.list_tools()                  │
  │    → List[ToolDefinition] (缓存)            │
  │ 11. 构建初始 AssemblyContext                 │
  └────────────────────────────────────────────┘
                    ↓
阶段二：多轮对话循环（Conversation Loop）
  ┌────────────────────────────────────────────┐
  │ 外层循环（每轮用户输入触发）：              │
  │   12. 从容器 resolve(ContextAssembler)      │
  │   13. ContextAssembler.assemble(            │
  │         assembly_context) → List[Message]   │
  │   14. 调用 LLM (外部注入) → Response        │
  │                                             │
  │   内层循环（Tool call 连续生成）：           │
  │     15. 判断 Response 内容：                │
  │         - tool_uses → ToolRouter.execute()  │
  │           → 结果追加到 message list         │
  │           → 回到步骤 14                     │
  │         - text → InputAdapter.send()        │
  │           → 跳出内层循环                    │
  │                                             │
  │   16. 等待下一轮用户输入：                   │
  │       InputAdapter.receive()                │
  │       → 更新 AssemblyContext                │
  │       → 回到步骤 12                         │
  │                                             │
  │   17. 退出信号 → 进入阶段三                 │
  └────────────────────────────────────────────┘
                    ↓
阶段三：会话结束（Session End）
  ┌────────────────────────────────────────────┐
  │ 18. 组装完整 Trajectory                     │
  │ 19. 从容器 resolve(Sensor)                 │
  │ 20. Sensor.sense(trajectory) → void        │
  │ 21. ToolRouter.shutdown() → 分发清理       │
  │ 22. 会话结束清理                            │
  └────────────────────────────────────────────┘
```

### 3.2 接口设计

```python
class LifecycleOrchestrator:
    """生命周期编排器 — 按三阶段固定顺序驱动组件调用。

    编排器不实现任何业务逻辑。它只做"在正确的时间、以正确的顺序、
    调用正确的组件方法"。所有业务行为由注入的组件决定。

    LLM 调用通过构造函数注入的 call_llm 可调用对象实现，
    编排器不知道 LLM 的具体实现。
    """

    def __init__(self, container: DIContainer, call_llm: Callable):
        """初始化编排器。

        Args:
            container: DI 容器，用于解析组件实例
            call_llm: LLM 调用函数，签名:
                      (messages: List[Message], tools: List[ToolDefinition])
                      → Response
        """
        ...

    def run(self) -> None:
        """启动并运行完整的会话生命周期。

        这是编排器的唯一公开入口。内部依次执行：
        1. _phase_init()     — 会话初始化
        2. _phase_loop()     — 多轮对话循环
        3. _phase_end()      — 会话结束

        Raises:
            ComponentNotRegisteredError: 必需的组件未注册
            OrchestratorError: 编排流程中的其他错误
        """
        ...

    # --- 私有方法 ---

    def _phase_init(self) -> AssemblyContext:
        """阶段一：会话初始化。

        步骤：
        1. resolve InputAdapter → receive() → UserRequest
        2. resolve GuideProvider → get_guides() → GuidesBundle
        3. resolve MemoryBackend → search() → List[MemoryItem]
        4. 创建 ToolRouter（框架内部），resolve_optional SystemToolProvider → register_provider
        5. resolve_optional MCPAdapter → register_provider
        6. ToolRouter.list_tools() → List[ToolDefinition]
        7. 构建并返回 AssemblyContext

        产出的 AssemblyContext 被缓存，供 _phase_loop 使用。
        """
        ...

    def _phase_loop(self, ctx: AssemblyContext) -> None:
        """阶段二：多轮对话循环。

        外层循环（每轮用户输入触发一次）：
          1. ContextAssembler.assemble(ctx) → messages
          2. 进入内层循环

        内层循环（同一轮内 tool_use 连续生成）：
          3. self.call_llm(messages, tools) → Response
          4. 处理 Response（注意：text 和 tool_uses 可共存）：
             a. 如有 tool_uses → 每个 tool 经 ToolRouter 串行执行
                → tool_use + tool_result 追加到 messages 尾部
             b. 如有 text → 追加 assistant message 到 messages
                → InputAdapter.send(response)
                → 跳出内层循环
             c. 如仅有 tool_uses 无 text → 回到步骤 3（继续内层循环）
          5. 回到外层循环：InputAdapter.receive() → 判断退出
             → 更新 ctx → 回到步骤 1

        退出条件：用户发出退出信号或框架收到终止信号。

        关键约束：
        - 内层循环不走 ContextAssembler.assemble()
        - tool result 直接追加到当前 message list 尾部回传 LLM
        - 单次 LLM 响应可同时包含 text 和 tool_uses（非互斥）
        - 多 Tool 按顺序串行执行
        """
        ...

    def _phase_end(self, trajectory: Trajectory) -> None:
        """阶段三：会话结束。

        组装 Trajectory → Sensor.sense() → 清理。
        """
        ...

    def _build_trajectory(self) -> Trajectory:
        """从会话记录组装完整的 Trajectory 对象（含 session_id、完整事件流 history、tool_calls）。"""
        ...

    def _should_exit(self, user_request: UserRequest) -> bool:
        """判断是否应该退出会话。

        退出条件：
        - UserRequest.text 为 None 或空字符串
        - UserRequest.text 匹配退出关键词（如 "/exit", "/quit"）
        - InputAdapter 发出 EOF 信号
        """
        ...
```

### 3.3 组件缺失处理策略

由于 batch-01 是第一批次，后续组件接口和实现都还不存在。编排器需要能够**优雅处理缺失组件**：

```python
# 编排器中的组件解析策略
def _resolve_optional(self, interface: type) -> Optional[Any]:
    """尝试解析组件，不存在时返回 None 并记录 WARNING 日志。

    使用 resolve + except 而非 is_registered + resolve，
    保证原子性（单次 dict 查找），避免两次调用之间的 TOCTOU 窗口。
    """
    try:
        return self.container.resolve(interface)
    except ComponentNotRegisteredError:
        logger.warning(f"Component {interface.__name__} not registered, skipping")
        return None
```

| 策略 | 场景 | 行为 |
|------|------|------|
| **必需组件** | InputAdapter（至少用于 I/O） | 缺失时抛异常，无法运行 |
| **可选组件** | GuideProvider, MemoryBackend, Sensor 等 | 缺失时 skip + WARNING 日志，不阻塞流程 |
| **LLM callable** | call_llm 参数 | None 时 tool_use 循环不可用，编排器仅验证控制流。**此模式仅用于调试和测试**，生产环境必须传入有效 LLM 适配器。Harness.from_container() 在 call_llm=None 时会输出 WARNING 日志。 |

**设计意图**：batch-01 中的编排器是一个**可运行的骨架**。即使没有真实组件，也能用 mock 验证整个流程的控制流。

### 3.4 LLM 交互协议（内层循环数据流）

编排器本身不实现 LLM 调用，但它定义了与 LLM 交互的**数据协议**。`call_llm` 可调用对象和编排器之间通过以下约定通信：

#### 3.4.1 call_llm 签名约定

```python
Callable[[List[Dict], List[ToolDefinition]], _MinimalResponse]
#         ↑ messages      ↑ tools             ↑ 返回值
```

**messages** 格式：遵循 OpenAI 兼容的 message dict 格式：
```python
# 标准消息
{"role": "system", "content": "You are a helpful assistant"}
{"role": "user", "content": "Hello"}
{"role": "assistant", "content": "Hi there!"}

# tool_use 消息（LLM 产出，含 tool_calls 块）
{"role": "assistant", "content": None,
 "tool_calls": [{"id": "call_1", "type": "function",
                  "function": {"name": "read", "arguments": '{"path":"/x"}'}}]}

# tool_result 消息（编排器追加，回传 LLM）
{"role": "tool", "tool_call_id": "call_1",
 "content": "file contents here..."}
```

**tools** 格式：ToolDefinition 列表，直接传给 LLM 的 `tools` 参数。

#### 3.4.2 _MinimalResponse 的 tool_uses 结构

```python
@dataclass
class _MinimalToolCall:
    """单次工具调用请求。遵循 OpenAI tool call 格式。"""
    id: str                              # tool call 唯一 ID（如 "call_abc123"）
    name: str                            # 函数名
    arguments: str                       # JSON 编码的参数字符串
```

`_MinimalResponse.tool_uses` 是 `List[_MinimalToolCall]`，不再是模糊的 `List[Any]`。

#### 3.4.3 内层循环 message 追加规则

内层循环中，编排器负责将 LLM 的 tool_use 响应和执行结果追加到 message list 尾部：

```
第 N 次 LLM 调用前 messages:
  [..., {"role": "user", "content": "read /tmp/x"}]

LLM 返回: Response(tool_uses=[ToolCall(id="c1", name="read", ...)], text=None)
  → 编排器构造 assistant message（含 tool_calls 块）→ 追加到 messages
  → 编排器调用 ToolRouter.execute("read", parsed_args) → ToolResult
  → 编排器构造 tool result message → 追加到 messages

第 N+1 次 LLM 调用前 messages:
  [..., {"role": "user", "content": "read /tmp/x"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "read", "arguments": '{"path":"/tmp/x"}'}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "file contents here..."}]
```

#### 3.4.4 text 与 tool_uses 共存处理

架构明确要求"LLM 单次响应可同时包含 text 和 tool_uses"。编排器的处理逻辑：

```python
if response.tool_uses:
    # 1. 构造含 tool_calls 的 assistant message 追加到 messages
    # 2. 执行所有 tools，追加 tool result messages
    # 3. 如有 response.text，也追加到同一 assistant message 的 content 字段
    #    （或构造独立的 assistant message）
if response.text:
    # 发送给用户
    adapter.send(response)
    # 跳出内层循环
if not response.tool_uses and not response.text:
    # 空响应，跳出内层循环（防御性处理）
    break
```

**关键**：text + tool_uses 共存时 → 先执行 tools 并追加到 messages → 同时把 text 发给用户 → 跳出内层循环。这样用户能立即看到部分结果，而 tool 执行结果保留在 messages 中供下一轮（如有）或供 Trajectory 记录。

### 3.5 关键设计决策

| 决策 | 理由 |
|------|------|
| LLM 调用通过 `call_llm` 可调用对象注入 | 编排器不绑定任何 LLM SDK，测试时可用 mock |
| Messages 用 dict 格式（OpenAI 兼容） | 通用性最强，大多数 LLM SDK 原生支持 |
| Tool arguments 保持 JSON string 格式 | 与 OpenAI tool call 格式一致，编排器在调用 ToolRouter 时 parse |
| GuidesBundle/available_tools 在阶段一获取后缓存 | 避免每轮重复调用 GuideProvider 和 ToolRouter.list_tools() |
| 外层/内层循环分离 | 外层对应"用户新输入"，内层对应"tool_use 连续链" |
| text+tool_uses 共存时先执行 tools 再发送 text | 保持 messages 完整性，同时不阻塞用户看到响应 |
| 退出判断放在外层循环末尾 | 用户在每轮结束后决定继续或退出 |
| `_phase_init` 返回 AssemblyContext | 数据流显式，便于测试中间状态 |

---

## 四、ConfigLoader 设计

### 4.1 职责

读取 TOML 配置文件（`profile.toml`），解析为结构化配置字典。框架内核不解释配置语义，仅做格式校验和字段提取。

### 4.2 TOML 配置格式

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
mcp_manager = true
sensor = true
memory_backend = true
```

### 4.3 接口设计

```python
@dataclass
class ProfileConfig:
    """从 profile.toml 解析出的结构化配置。"""
    name: str
    description: str
    template: str
    version: str
    modules: Dict[str, bool]    # 模块启用/禁用标志
    raw: Dict[str, Any]         # 原始 TOML 数据（完整保留）

class ConfigLoader:
    """TOML 配置文件加载器。

    职责：读取、解析、校验 profile.toml，返回 ProfileConfig。
    """

    def load(self, path: str) -> ProfileConfig:
        """加载并解析 TOML 配置文件。

        Args:
            path: profile.toml 文件路径

        Returns:
            ProfileConfig: 结构化配置对象

        Raises:
            ConfigNotFoundError: 文件不存在
            ConfigParseError: TOML 语法错误
            ConfigValidationError: 必需字段缺失或类型错误
        """
        ...

    def validate(self, config: ProfileConfig) -> None:
        """校验配置完整性。

        校验规则：
        - [meta] 段必须存在
        - meta.name 必须是非空字符串
        - meta.template 必须是非空字符串
        - [modules] 段可选，缺失时 modules 返回空 dict（由装配层根据领域模板决定默认行为）
        - modules 中的值必须是布尔类型

        Raises:
            ConfigValidationError: 校验失败
        """
        ...
```

### 4.4 边界条件

| 场景 | 行为 |
|------|------|
| 文件不存在 | `ConfigNotFoundError`（继承自 `HarnessError`），消息包含路径 |
| 文件不可读（权限） | `ConfigNotFoundError`，消息说明原因 |
| TOML 语法错误 | `ConfigParseError`，包装原始解析异常 |
| `[meta]` 段缺失 | `ConfigValidationError`，消息指出缺失字段 |
| `meta.name` 为空字符串 | `ConfigValidationError` |
| `[modules]` 段缺失 | 不报错，`modules` 字段返回空 dict（视为全部未显式禁用） |
| modules 值非 bool | `ConfigValidationError`，消息指出具体 key |

---

## 五、异常体系设计

### 5.1 异常层次结构

```
HarnessError (Exception)
├── ConfigError (HarnessError)
│   ├── ConfigNotFoundError (ConfigError)
│   ├── ConfigParseError (ConfigError)
│   └── ConfigValidationError (ConfigError)
├── ContainerError (HarnessError)
│   ├── DuplicateRegistrationError (ContainerError)
│   └── ComponentNotRegisteredError (ContainerError)
└── OrchestratorError (HarnessError)
```

### 5.2 设计原则

- 所有框架异常继承自 `HarnessError`，用户可单一 `except HarnessError` 捕获所有框架错误
- 每层异常有明确的语义前缀（`Config*`、`Container*`、`Orchestrator*`）
- 异常消息必须包含足够的上下文信息（哪个接口、哪个文件、什么原因）

### 5.3 各异常定义

```python
class HarnessError(Exception):
    """Harness 框架所有异常的基类。"""
    pass

class ConfigError(HarnessError):
    """配置相关异常的基类。"""
    pass

class ConfigNotFoundError(ConfigError):
    """配置文件不存在。"""
    pass

class ConfigParseError(ConfigError):
    """配置文件解析失败（如 TOML 语法错误）。"""
    pass

class ConfigValidationError(ConfigError):
    """配置校验失败（如必需字段缺失）。"""
    pass

class ContainerError(HarnessError):
    """DI 容器相关异常的基类。"""
    pass

class DuplicateRegistrationError(ContainerError):
    """尝试重复注册同一接口类型。"""
    pass

class ComponentNotRegisteredError(ContainerError):
    """请求的接口类型未注册。"""
    pass

class OrchestratorError(HarnessError):
    """编排流程中的错误。"""
    pass
```

---

## 六、MinimalLLMAdapter 设计

### 6.1 职责与定位

**定位**：MVP 自带的最小化 LLM 调用适配器。实现后 batch-01 就能 ping 通真实的 LLM API，不需要等到 batch-10。

**职责**：
- 将编排器的 `call_llm(messages, tools) → _MinimalResponse` 签名桥接到 OpenAI 兼容 HTTP API
- 零外部依赖：仅使用 Python 标准库 `urllib.request`
- 支持任何 OpenAI 兼容 endpoint（OpenAI、Ollama、vLLM、LM Studio 等）

**与组件的关系**：`MinimalLLMAdapter` 不是框架组件（不属于 `components/`），它是 MVP 自带的参考实现。用户可以直接用它，也可以用自己的 `call_llm` 替换它。它会在后续被正式的 LLM 组件替代（batch-02-1 先将其返回值类型迁移为正式 `Response`），但在此之前它就是"能跑起来"的关键。

### 6.2 接口设计

```python
class MinimalLLMAdapter:
    """最小化 OpenAI 兼容 LLM 适配器。

    使用标准库 urllib 发送 HTTP 请求到 OpenAI 兼容 API。
    零外部依赖，开箱即用。

    用法：
        adapter = MinimalLLMAdapter(
            base_url="https://api.openai.com/v1",
            api_key="sk-xxx",
            model="gpt-4o",
        )
        # 直接作为 call_llm 注入编排器
        harness = Harness.from_container(container, call_llm=adapter)
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 120,
    ):
        """初始化适配器。

        Args:
            base_url: OpenAI 兼容 API 的 base URL。
                      支持替换为 Ollama (http://localhost:11434/v1)、
                      vLLM 等任何兼容端点。
            api_key: API 密钥。空字符串时从环境变量
                     OPENAI_API_KEY 读取。
            model: 模型名称。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度 (0.0 ~ 2.0)。
            timeout: HTTP 请求超时时间（秒）。
        """
        ...

    def __call__(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
    ) -> _MinimalResponse:
        """调用 LLM API。

        实现 call_llm 签名约定，可直接注入 LifecycleOrchestrator。

        Args:
            messages: OpenAI 格式的消息列表。
            tools: 工具定义列表（可选）。

        Returns:
            _MinimalResponse: 标准化的 LLM 响应。

        Raises:
            OrchestratorError: API 调用失败时抛出，
                               包含 HTTP 状态码和响应体。
        """
        ...

    # --- 私有方法 ---

    def _build_request_body(
        self, messages: List[Dict], tools: Optional[List[Dict]]
    ) -> Dict:
        """构建 OpenAI /v1/chat/completions 请求体。"""
        ...

    def _send_request(self, body: Dict) -> Dict:
        """发送 HTTP POST 请求，返回解析后的 JSON 响应。

        Raises:
            OrchestratorError: 网络错误、超时、非 2xx 响应。
        """
        ...

    def _parse_response(self, response_json: Dict) -> _MinimalResponse:
        """将 OpenAI chat completion 响应解析为 _MinimalResponse。

        提取逻辑：
        - response_json["choices"][0]["message"]["content"] → text
        - response_json["choices"][0]["message"]["tool_calls"] → tool_uses
        - response_json["choices"][0]["finish_reason"] → stop_reason
        """
        ...
```

### 6.3 API 请求格式

适配器发送的 HTTP 请求：

```
POST {base_url}/chat/completions
Content-Type: application/json
Authorization: Bearer {api_key}

{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are helpful"},
    {"role": "user", "content": "Hello"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {...}}
      }
    }
  ],
  "max_tokens": 4096,
  "temperature": 0.7
}
```

### 6.4 响应解析逻辑

```python
def _parse_response(self, response_json: Dict) -> _MinimalResponse:
    choice = response_json["choices"][0]
    message = choice["message"]

    # 提取 text
    text = message.get("content")  # 可能为 None（纯 tool_use 响应）

    # 提取 tool_uses
    tool_uses = []
    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            tool_uses.append(_MinimalToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"]["arguments"],  # JSON string
            ))

    # 提取 stop_reason
    stop_reason = choice.get("finish_reason", "stop")

    return _MinimalResponse(
        text=text,
        tool_uses=tool_uses,
        stop_reason=stop_reason,
    )
```

### 6.5 错误处理

| 场景 | 处理 |
|------|------|
| 网络不可达 / DNS 失败 | `OrchestratorError("LLM API unreachable: {url}")`，包装原始 `URLError` |
| HTTP 超时 | `OrchestratorError("LLM API timeout after {timeout}s")` |
| HTTP 4xx（认证失败等） | `OrchestratorError("LLM API error {status}: {body}")` |
| HTTP 5xx（服务端错误） | `OrchestratorError("LLM API server error {status}: {body}")` |
| 响应 JSON 解析失败 | `OrchestratorError("LLM API invalid JSON response")` |
| 响应体缺少 `choices` | `OrchestratorError("LLM API unexpected response format: {body}")` |
| API key 未设置（构造时为空 + 环境变量也为空）| 不立即报错，首次 `__call__` 时由 API 返回 401 → 上述 4xx 处理 |

### 6.6 API Key 读取优先级

```
1. 构造函数参数 api_key（显式传入，为 None 时走后续 fallback）
2. 环境变量 OPENAI_API_KEY
3. harness/config/.env 文件中的 api-key 或 api_key 键
4. 都未设置 → 不报错，请求发出去后由 API 返回 401
```

### 6.7 关键设计决策

| 决策 | 理由 |
|------|------|
| 零外部依赖（仅 `urllib.request`） | batch-01 即可独立运行，不需要 `pip install openai` |
| `__call__` 实现 callable 接口 | 直接作为 `call_llm` 注入编排器 |
| 支持任意 OpenAI 兼容 base_url | Ollama (`localhost:11434/v1`)、vLLM 等都可用 |
| 错误统一抛 `OrchestratorError` | 编排器的 `finally` 块能统一处理 |
| API key 支持环境变量 | 安全最佳实践，不在代码中硬编码密钥 |
| 响应中 `tool_calls` 的 `arguments` 保持 JSON string | 与 OpenAI 原生格式一致，编排器负责 parse |

---

## 七、harness/di.py 装配入口设计

### 7.1 职责

`harness/di.py` 是框架的装配层入口文件。它提供 `Harness` 类，封装从 DI 容器解析组件到启动编排的完整流程。

### 7.2 接口设计

```python
class Harness:
    """Harness Agent 框架的顶层入口。

    封装 DI 容器解析 → 编排器创建 → 生命周期启动的完整流程。
    """

    @staticmethod
    def from_container(
        container: DIContainer,
        call_llm: Optional[Callable] = None
    ) -> 'Harness':
        """从 DI 容器构造 Harness 实例。

        Args:
            container: 已装配好组件的 DI 容器
            call_llm: LLM 调用函数（可选，测试时可用 mock）

        Returns:
            Harness: 可运行的框架实例

        Raises:
            ComponentNotRegisteredError: InputAdapter 未注册（必需组件）
        """
        ...

    def run(self) -> None:
        """启动完整的会话生命周期。

        等价于 LifecycleOrchestrator.run()。
        """
        ...
```

### 7.3 使用示例

```python
# 用户代码（batch-01 最小可运行示例）
from harness.di import Harness
from harness.core.container import DIContainer
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 1. 创建组件实例并注册
container = DIContainer()
container.register(InputAdapter, CliAdapter())
container.register(GuideProvider, FileGuideProvider("AGENTS.md"))
# ... 注册更多组件

# 2. 创建 LLM 适配器（零依赖，直接 ping API）
llm = MinimalLLMAdapter(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",           # 或省略，从环境变量 OPENAI_API_KEY 读取
    model="gpt-4o",
)

# 3. 启动
harness = Harness.from_container(container, call_llm=llm)
harness.run()
```

---

## 八、模块与文件布局

### 8.1 产出文件清单

```
harness/
├── __init__.py
├── di.py                            # Harness 装配入口
├── core/                            # 内核：DI 容器 + 编排器 + 异常 + 数据类型
│   ├── __init__.py
│   ├── exceptions.py                # 异常体系
│   ├── container.py                 # DIContainer
│   ├── types.py                     # 内部数据结构
│   ├── orchestrator.py              # LifecycleOrchestrator
│   ├── config.py                    # → 重导出，指向 harness/config
│   └── llm_adapter.py               # → 重导出，指向 harness/adapters
├── interfaces/                      # 组件接口类型（占位）
│   └── __init__.py
├── adapters/                        # 外部系统适配器
│   ├── __init__.py
│   └── llm_adapter.py               # MinimalLLMAdapter
├── config/                          # 配置模块
│   ├── __init__.py
│   ├── loader.py                    # ConfigLoader + ProfileConfig
│   └── .env                         # API 配置模板
└── messaging/                       # 消息构造
    ├── __init__.py
    └── builder.py                   # assistant / tool_result message 构造
```

### 8.2 模块依赖关系（batch-01 内部）

```
core/exceptions.py          ← 无内部依赖
    ↑
    ├── core/container.py   ← 依赖 exceptions
    ├── config/loader.py    ← 依赖 exceptions
    ├── core/types.py       ← 无内部依赖（仅 stdlib json）
    │       ↑
    │       ├── core/orchestrator.py  ← 依赖 exceptions, container, types,
    │       │                            interfaces, messaging
    │       ├── messaging/builder.py  ← 依赖 types
    │       └── adapters/llm_adapter  ← 依赖 exceptions, types
    │
    └── di.py               ← 依赖 container, exceptions, orchestrator
```

### 8.3 各 __init__.py 导出约定

```python
# harness/__init__.py
__version__ = "0.1.0"

# harness/core/__init__.py
from .exceptions import (
    HarnessError, ConfigError, ConfigNotFoundError,
    ConfigParseError, ConfigValidationError,
    ContainerError, DuplicateRegistrationError,
    ComponentNotRegisteredError, OrchestratorError,
)
from .container import DIContainer
from .config import ConfigLoader, ProfileConfig
from .orchestrator import LifecycleOrchestrator
from .llm_adapter import MinimalLLMAdapter

__all__ = [
    "DIContainer", "ConfigLoader", "ProfileConfig",
    "LifecycleOrchestrator", "MinimalLLMAdapter",
    "HarnessError", "ConfigError", ...
]
```

---

## 九、关键设计决策汇总

| # | 决策 | 权衡与理由 |
|---|------|-----------|
| 1 | DI 容器：预构造实例注册 | 简单优先。不需要 scope 管理，用户完全控制 |
| 2 | 编排器：LLM 通过 callable 注入 | 解耦 SDK 依赖。mock 友好 |
| 3 | 编排器：组件缺失用 WARNING + skip | batch-01 没有真实组件，但要能跑起来验证控制流 |
| 4 | 配置：TOML 格式 | 人类可读，Python 标准库支持（3.11+ `tomllib`） |
| 5 | 异常：分层继承 | 用户可精细捕获或统一捕获 `HarnessError` |
| 6 | 编排器：三阶段显式分离 | 可独立测试每个阶段，数据流清晰 |
| 7 | ConfigLoader：独立校验方法 | 允许 load + validate 分离，用户可先看原始数据再校验 |

---

## 十、与其他批次的接口约定

batch-01 为后续批次提供以下基础设施：

| 基础设施 | 被哪些批次使用 | 使用方式 |
|----------|--------------|---------|
| `DIContainer` | 全部批次 | 注册组件实例，解析依赖 |
| `LifecycleOrchestrator` | batch-02-1, batch-10 | batch-02-1 迁移为正式类型；batch-10 完成 CLI 装配 |
| `ConfigLoader` / `ProfileConfig` | batch-10 | 加载 profile.toml |
| `HarnessError` 异常体系 | batch-02-1 ~ 10 | 所有组件和测试的异常基类 |
| `MinimalLLMAdapter` | batch-02-1 ~ 09 | batch-02-1 升级返回类型为正式 `Response`；在正式 LLM 组件就绪前提供真实 LLM 调用能力 |
| `harness/di.py` → `Harness` | batch-10 | 框架顶层入口 |
