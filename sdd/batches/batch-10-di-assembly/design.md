# batch-10: DI Assembly — 架构设计

> 版本: 1.0
> 依赖: batch-01 ~ batch-09 全部（所有组件实现、Hook 系统、编排器均已就绪）

---

## 1. 设计目标

实现框架的最终装配层，提供声明式（YAML）和命令式（Python API）两种装配方式，
以及 CLI 入口、领域模板骨架和端到端集成测试。

1. **YAML assembly** — 通过 YAML 文档声明组件注册，框架自动构造 DI 容器
2. **CLI 入口** — `main.py` 提供 `harness init` / `harness run` 命令
3. **Profile 模板** — `coding-assistant` 领域模板骨架
4. **集成验证** — 端到端测试覆盖完整生命周期
5. **全局验收** — 对照 `sdd/06-acceptance.md` 逐条验证

---

## 2. 架构定位

```
                             ┌──────────────────────────────────┐
                             │          main.py                  │
                             │  CLI 入口: harness init | run     │
                             │                                   │
                             │  ┌──────────────────────────────┐ │
                             │  │   YamlAssembler (NEW)         │ │
                             │  │   harness.yaml → DIContainer  │ │
                             │  └──────────┬───────────────────┘ │
                             │             │                     │
                             │             ▼                     │
                             │  ┌──────────────────────────────┐ │
                             │  │   DIContainer (已有)          │ │
                             │  │   register / resolve          │ │
                             │  └──────────┬───────────────────┘ │
                             └─────────────┼─────────────────────┘
                                           │
                              ┌────────────┼────────────┐
                              │            ▼            │
                              │  ┌──────────────────┐   │
                              │  │      Harness     │   │
                              │  │  from_container()│   │
                              │  └────────┬─────────┘   │
                              │           │             │
                              │           ▼             │
                              │  ┌──────────────────┐   │
                              │  │LifecycleOrch'ator│   │
                              │  └──────────────────┘   │
                              │       已有（无需修改）    │
                              └─────────────────────────┘

profiles/coding-assistant/        ← 领域模板骨架 (NEW)
├── harness.yaml                  ← YAML 装配声明
├── AGENTS.md                     ← Agent 指导文件
└── README.md                     ← 使用说明
```

**关键约束：**
- YAML assembly 是 DIContainer + Harness 的**上层封装**，不是替代
- 现有的 Python API 装配方式（`container.register(Interface, instance)`）**完全保留**，不受影响
- 用户可选择：纯 YAML、纯 Python API、或两者混合

---

## 3. YAML 装配设计

### 3.1 设计哲学

YAML assembly 解决 **80% 的简单装配场景**：用户只需声明"用什么实现类 + 传什么参数"。
对于复杂场景（动态构造、条件装配、自定义初始化逻辑），用户仍使用 Python API。

核心原则：
- **声明式 > 命令式**（对简单场景）
- **显式 > 隐式**（所有依赖关系在 YAML 中明确写出）
- **渐进增强**（从 YAML 入手，需要时可切换到 Python API）

### 3.2 YAML 文件格式

一个完整的 `harness.yaml` 示例（包含全部 7 个组件的注册）：

```yaml
# harness.yaml — Harness Agent 装配声明
# 此文件描述 DI 容器的组件注册和框架配置

harness:
  version: "1.0"
  profile: coding-assistant

  # ── 组件注册 ──────────────────────────────────────────
  components:
    # InputAdapter（必需）
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
      params:
        prompt: "> "

    # GuideProvider（可选）
    - interface: GuideProvider
      implementation: harness.components.guide_provider.FileGuideProvider
      params:
        paths:
          - AGENTS.md
          - CLAUDE.md

    # MemoryBackend（可选）
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: ./memory

    # ContextAssembler（可选，依赖 MemoryBackend）
    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      params:
        max_history: 50
      inject:
        memory: MemoryBackend   # ← 引用其他已注册的组件

    # Sensor（可选，依赖 MemoryBackend）
    - interface: Sensor
      implementation: harness.components.sensor.LoggingSensor
      inject:
        memory: MemoryBackend

    # SystemToolProvider（可选）
    - interface: SystemToolProvider
      implementation: harness.components.tool.DefaultSystemToolProvider

    # MCPAdapter（可选 — 不注册即裁切 MCP 功能）
    # 需要嵌套配置：mcp_configs 列表 + transforms 字典
    - interface: MCPAdapter
      implementation: harness.components.mcp_manager.DefaultMCPAdapter
      params:
        mcp_configs:
          - name: filesystem
            command: npx
            args:
              - -y
              - "@anthropic-ai/mcp-server-filesystem"
              - /path/to/allowed/dir
            transport: stdio
        transforms: {}   # ToolTransform 字典，按 MCP tool 名 key

  # ── Hook 注册 ──────────────────────────────────────────
  hooks:
    - event: before_llm_call
      handler: my_project.hooks.log_messages_to_file
    - event: on_error
      handler: my_project.hooks.send_alert

  # ── LLM 配置 ───────────────────────────────────────────
  llm:
    provider: openai           # "openai" | "anthropic" | "custom"
    model: gpt-4o
    # base_url 和 api_key 从环境变量读取，也可显式指定
    # base_url: https://api.openai.com/v1
    # api_key: ${OPENAI_API_KEY}
```

> **关于 MCPAdapter**: `mcp_configs` 是与 `DefaultMCPAdapter.__init__` 签名匹配的列表，
> 每个元素包含 `name`、`command`、`args`、`transport` 等字段。`transforms` 是
> `Dict[str, ToolTransform]` 格式。这两个字段作为 `params` 传递给构造函数。

### 3.3 YAML 各字段详解

#### `harness.version`
框架版本号。保留字段，当前固定为 `"1.0"`。

#### `harness.profile`
领域模板名称，如 `coding-assistant`、`travel-assistant`。

#### `harness.components` — 组件注册列表

每个组件条目：

| 字段 | 必需 | 说明 |
|------|------|------|
| `interface` | ✅ | 组件接口的短名（如 `InputAdapter`），映射到 `harness.interfaces` 中的 Protocol/ABC |
| `implementation` | ✅ | 实现类的完整模块路径（如 `harness.components.input_adapter.CliAdapter`） |
| `params` | ❌ | 传递给实现类 `__init__` 的关键字参数（key-value 结构） |
| `inject` | ❌ | 从已有注册中注入的依赖。key 是构造函数参数名，value 是 `interface` 短名（表示引用该组件） |

**接口短名映射表：**

| 短名 | 完整接口 | 必需? | 默认实现 |
|------|---------|-------|---------|
| `InputAdapter` | `harness.interfaces.InputAdapter` | ✅ 必需 | `CliAdapter` |
| `GuideProvider` | `harness.interfaces.GuideProvider` | ❌ 可选 | `FileGuideProvider` |
| `MemoryBackend` | `harness.interfaces.MemoryBackend` | ❌ 可选 | `MdMemory` |
| `ContextAssembler` | `harness.interfaces.ContextAssembler` | ❌ 可选 | `SimpleAssembler` |
| `Sensor` | `harness.interfaces.Sensor` | ❌ 可选 | `LoggingSensor` |
| `SystemToolProvider` | `harness.interfaces.SystemToolProvider` | ❌ 可选 | `DefaultSystemToolProvider` |
| `MCPAdapter` | `harness.interfaces.MCPAdapter` | ❌ 可选（不注册=裁切 MCP） | `DefaultMCPAdapter` |

> **注意**：`MCPAdapter` 注册时需要 `mcp_configs` 和 `transforms` 参数（与 `DefaultMCPAdapter.__init__` 签名一致），`inject` 字段不适用于 MCPAdapter（它不依赖其他 DI 组件）。

#### `harness.hooks` — Hook 注册列表

| 字段 | 必需 | 说明 |
|------|------|------|
| `event` | ✅ | Hook 事件名（如 `before_llm_call`），与 `harness/hooks/events.py` 中的常量值一致 |
| `handler` | ✅ | Hook 函数的完整模块路径（如 `my_project.hooks.my_handler`） |

#### `harness.llm` — LLM 配置

| 字段 | 必需 | 说明 |
|------|------|------|
| `provider` | ✅ | LLM 提供商标识（`"openai"` / `"anthropic"` / `"custom"`） |
| `model` | ❌ | 模型名（默认从环境变量 `LLM_MODEL` 读取） |
| `base_url` | ❌ | API endpoint（默认从环境变量 `LLM_BASE_URL` 读取） |
| `api_key` | ❌ | API key（默认从环境变量 `OPENAI_API_KEY` 读取，支持 `${ENV_VAR}` 语法） |

### 3.4 YamlAssembler 类设计

```python
class YamlAssembler:
    """从 YAML 装配声明构建 DIContainer。

    职责：
    1. 解析 harness.yaml → 结构化配置对象
    2. 按组件声明顺序动态 import 实现类
    3. 解析 inject 依赖（确保被依赖组件先注册）
    4. 构造组件实例并注册到 DIContainer
    5. 构建并返回 Harness 实例

    LLM 适配器由 YamlAssembler 根据 harress.llm 配置段自动创建，
    不作为 DI 组件注册（LLM 适配器通过 call_llm 参数传给 Harness.from_container()）。

    用法::

        assembler = YamlAssembler()
        harness = assembler.load("harness.yaml").assemble()
        harness.run()
    """

    def load(self, path: str) -> "YamlAssembler":
        """加载并解析 YAML 装配文件。"""
        ...

    def assemble(self) -> Harness:
        """按 YAML 声明构建 DIContainer 和 Harness 实例。"""
        ...
```

### 3.5 依赖解析逻辑（`inject` 字段）

```
组件注册顺序在 YAML 中由上到下。
inject 引用的组件必须已注册（在前面的条目中定义）。

正确:
  components:
    - interface: MemoryBackend      # ← 先注册
      implementation: ...MdMemory
    - interface: ContextAssembler   # ← 后引用
      inject:
        memory: MemoryBackend       # ✅ MemoryBackend 已注册

错误:
  components:
    - interface: ContextAssembler   # ← 先声明但引用了未注册的组件
      inject:
        memory: MemoryBackend       # ❌ MemoryBackend 尚未注册
    - interface: MemoryBackend      # ← 后注册
      implementation: ...MdMemory
```

`inject` 中的值（如 `MemoryBackend`）是接口短名，YamlAssembler 会将其解析为完整的接口类型并调用 `container.resolve(InterfaceClass)` 获取实例。

### 3.6 错误处理

| 错误场景 | 异常类型 | 说明 |
|---------|---------|------|
| YAML 文件不存在 | `FileNotFoundError` | 明确提示文件路径 |
| YAML 语法错误 | `yaml.YAMLError` | 含行号和上下文 |
| 实现类 import 失败 | `ImportError` | 含完整模块路径 |
| inject 依赖未找到 | `DependencyNotSatisfiedError` (NEW) | 含引用的接口名 |
| `InputAdapter` 未注册 | `AssemblyValidationError` | 含提示信息 |
| 未知 interface 短名 | `UnknownInterfaceError` (NEW) | 含短名和可用列表 |

---

## 4. CLI 入口设计

### 4.1 `main.py` — 命令路由

```python
"""
Harness Agent Template — CLI 入口。

用法:
    python main.py init --profile coding-assistant <output-dir>
    python main.py run [--config harness.yaml]

命令:
    init    从领域模板生成新项目
    run     按装配配置启动 Agent
"""

# 使用 argparse 做命令路由，不引入第三方 CLI 框架依赖
```

### 4.2 `harness init` 命令

```
python main.py init --profile coding-assistant my-agent

效果：
  1. 创建 my-agent/ 目录
  2. 复制 profiles/coding-assistant/ 下的模板文件：
     - harness.yaml（装配声明）
     - AGENTS.md（Agent 指导文件）
     - README.md（使用说明）
  3. 输出初始化成功信息
```

**参数：**
- `--profile` / `-p`：领域模板名称（默认 `coding-assistant`）
- `output-dir`（位置参数）：输出目录路径

**行为：**
- 输出目录已存在时：提示用户，询问是否覆盖（或使用 `--force`）
- 模板不存在时：产生明确错误信息，列出可用模板列表

### 4.3 `harness run` 命令

```
python main.py run --config harness.yaml

效果：
  1. 加载 YAML 配置文件
  2. 构建 DI 容器，装配所有组件
  3. 启动会话生命周期
```

**参数：**
- `--config` / `-c`：YAML 装配文件路径（默认 `./harness.yaml`）
- `--debug` / `-d`：启用 DEBUG 日志级别

**行为：**
- 无 `harness.yaml` 时：降级为纯默认组件装配（类似 `examples/minimal_agent.py` 的行为）
- `Ctrl+C` 时：优雅退出，打印退出信息

---

## 5. Profile 模板设计

### 5.1 `profiles/coding-assistant/` 目录结构

> **与顶层设计的协调说明**：`sdd/03-project-structure.md` 中的 profiles 结构
> （含 `profile.toml`、`input/`、`guides/` 等子目录）是早期占位设计。
> **本 batch 定义的以下结构为实际实现的结构**，在实现时应同步更新
> `sdd/03-project-structure.md` 以保持一致。两个配置文件的分工：
> - `profile.toml`（已有 ConfigLoader 支持）→ 模板元数据（name, description, template, version, modules）
> - `harness.yaml`（本 batch 新增）→ DI 装配声明（组件、Hook、LLM 配置）
> - 两者共存于 profile 目录下，各司其职。

```
profiles/coding-assistant/
├── profile.toml           # 模板元数据（已有 ConfigLoader 支持）
├── harness.yaml           # 默认 YAML 装配声明（本 batch 新增）
├── AGENTS.md              # Agent 指导文件（核心身份 + 行为规则）
└── README.md              # 使用说明
```

### 5.2 `harness.yaml`（模板默认值）

```yaml
harness:
  version: "1.0"
  profile: coding-assistant

  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
      params:
        prompt: "> "

    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: ./memory

    - interface: GuideProvider
      implementation: harness.components.guide_provider.FileGuideProvider
      params:
        paths:
          - AGENTS.md

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

    - interface: SystemToolProvider
      implementation: harness.components.tool.DefaultSystemToolProvider

  hooks: []   # 用户可在此添加自定义 Hook

  llm:
    provider: openai
```

### 5.3 `AGENTS.md`（模板默认值）

```markdown
# Coding Assistant

You are a helpful coding assistant. You help users write, review, and debug code.

## Capabilities
- Read and write files
- Execute shell commands
- Search codebase
- Answer programming questions

## Rules
- Always explain your changes before making them
- Use the tools provided to you
- Be concise and direct
```

---

## 6. 组件树

```
harness/
├── config/
│   ├── __init__.py          # (已有)
│   ├── loader.py            # (已有) TOML profile 加载器
│   └── yaml_assembler.py    # (NEW) YAML 装配加载器
├── di.py                    # (已有，无需修改) Harness 类
├── core/                    # (已有，本次不修改)
├── hooks/                   # (已有，本次不修改)
├── interfaces/              # (已有，本次不修改)
└── components/              # (已有，本次不修改)

profiles/                    # (NEW)
└── coding-assistant/
    ├── harness.yaml         # (NEW)
    ├── AGENTS.md            # (NEW)
    └── README.md            # (NEW)

main.py                      # (NEW) CLI 入口

tests/
├── test_yaml_assembler.py   # (NEW) YamlAssembler 单元测试
└── test_e2e_assembly.py     # (NEW) 端到端装配集成测试

sdd/batches/batch-10-di-assembly/
├── design.md                # (UPDATE) 本文档
├── tasks.md                 # (UPDATE) 任务清单
└── acceptance.md            # (UPDATE) 验收标准
```

---

## 7. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | YAML 而非 TOML 作为装配声明格式 | YAML 对嵌套结构和列表更友好；在容器/配置领域是主流格式；与 TOML profile 配置（已有）分属不同层次 |
| 2 | YAML assembly 是 Python API 的上层封装 | 不是替代——80% 简单场景用 YAML，20% 复杂场景用 Python API。两者产出同一个 DIContainer |
| 3 | PyYAML 作为唯一新增依赖 | Python 标准库不含 YAML 解析器。PyYAML 是最广泛使用的 YAML 库 |
| 4 | `inject` 字段使用接口短名引用组件 | 保持 YAML 可读性；避免在 YAML 中写完整 Python 类路径作为引用 |
| 5 | LLM 适配器不作为 DI 组件注册 | LLM 适配器通过 `call_llm` 参数传给 Harness，不是 DI 组件；YAML 的 `llm` 段独立管理 |
| 6 | `harness init` 不从零生成代码（只复制模板） | 模板复制是最简单可靠的初始化方式；避免代码生成器的复杂度 |
| 7 | `harness.yaml` 不指定时降级为全默认组件 | 类似 `examples/minimal_agent.py` 的行为，确保开箱即用 |
| 8 | `main.py` 使用 argparse（不引入 click/typer） | 保持零额外依赖，命令行参数简单不需复杂 CLI 框架 |
| 9 | 接口短名映射表硬编码在 YamlAssembler 中 | 映射关系稳定且数量少（7个），避免引入配置文件解析复杂性 |
| 10 | `harness init` 不依赖 LLM API key 是否存在 | init 命令仅复制模板文件，不做任何网络调用 |

---

## 8. 与前后批次的接口约定

### 8.1 对前序批次的依赖

| 依赖 | 来自 | 使用方式 |
|------|------|---------|
| DIContainer | batch-01 `core/container.py` | YamlAssembler 构造并注册实例 |
| Harness.from_container() | batch-01 `di.py` | 从容器创建 Harness 实例 |
| LifecycleOrchestrator | batch-01 `core/orchestrator.py` | Harness 内部使用，不直接接触 |
| 所有接口类型 | batch-02 `interfaces/` | 接口短名映射 + 依赖注入类型 |
| 所有默认实现 | batch-03~08 `components/` | YAML 中 `implementation` 字段引用 |
| Hook 常量和 HookManager | batch-09 `hooks/` | YAML 中 `hooks` 段引用 |
| ConfigLoader（TOML） | batch-01 `config/loader.py` | 不修改，独立存在 |
| MinimalLLMAdapter | batch-01 `adapters/llm_adapter.py` | YAML `llm` 段自动创建 |

### 8.2 严格不在范围内

- ❌ 不修改 `harness/core/` 中的任何文件（container、orchestrator、tool_router 等）
- ❌ 不修改 `harness/interfaces/` 中的任何接口定义
- ❌ 不修改 `harness/hooks/` 中的 Hook 系统实现
- ❌ 不修改 `harness/components/` 中的任何组件实现
- ❌ 不修改 `harness/adapters/` 中的 LLM 适配器
- ❌ 不修改 `harness/messaging/` 中的消息构造工具
- ❌ 不修改 `harness/config/loader.py`（TOML 加载器保持不变）
- ❌ 不修改 `harness/di.py` 中现存的逻辑（Harness 类 `from_container` 和 `run` 方法保持不变）
- ✅ 允许在 `harness/di.py` 的 Harness 类上新增一个公开方法 `register_hook(event, hook)`，
  代理到 `self._orchestrator.register_hook(event, hook)`，供 YamlAssembler 在装配后注册 Hook
- ❌ 不添加除 PyYAML 之外的任何第三方依赖
- ❌ 不实现完整的 CLI 框架（不引入 click/typer/rich）
- ❌ 不实现 `harness init` 的交互式向导（首版只做模板复制）
- ❌ 不实现 YAML schema 验证（首版做基本结构校验即可）

### 8.3 为后续提供的基础

| 产出 | 被哪些后续使用 | 使用方式 |
|------|--------------|---------|
| YamlAssembler | 新增领域模板 | 每个模板有自己默认的 harness.yaml |
| main.py CLI | 最终用户 | `harness init` 和 `harness run` 的入口 |
| profiles/coding-assistant | 新增领域模板 | 作为模板参考和复制源 |
| 端到端测试 | 所有后续修改 | 验证完整生命周期不回归 |

---

## 9. 代码修改边界

### 新增文件（本次 batch）

```
harness/config/yaml_assembler.py    ← YamlAssembler 实现
main.py                             ← CLI 入口
profiles/coding-assistant/          ← coding-assistant 模板目录
  profile.toml                      ← 模板元数据（格式与现有 ConfigLoader 兼容）
  harness.yaml
  AGENTS.md
  README.md
tests/test_yaml_assembler.py        ← YamlAssembler 单元测试
tests/test_e2e_assembly.py          ← 端到端装配集成测试
```

### 修改文件（本次 batch）

```
harness/di.py                       ← 仅新增 Harness.register_hook() 公开方法（一行代理）
sdd/03-project-structure.md         ← 更新 profiles/ 目录结构以匹配实际实现
```

### 不修改的文件（本次 batch 边界）

```
harness/core/        (全部文件不修改)
harness/interfaces/  (全部文件不修改)
harness/hooks/       (全部文件不修改)
harness/components/  (全部文件不修改)
harness/adapters/    (全部文件不修改)
harness/messaging/   (全部文件不修改)
harness/config/loader.py  (TOML 加载器不修改，与 YamlAssembler 共存)
harness/config/__init__.py  (不修改)
tests/ 中已有测试文件  (不修改)
```
