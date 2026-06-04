# 03 — 项目代码目录边界

> 定义框架源码的文件/目录结构。所有批次产出的代码必须遵守此布局，避免跨批次文件冲突。
>
> **版本: 1.0**

---

## 一、仓库顶层布局

```
harness_agent/
├── sdd/                          # SDD 规格文件（当前目录）
├── harness/                      # 框架源码
│   ├── core/                     # 内核：DI 容器、生命周期编排、异常
│   ├── interfaces/               # 组件接口类型（Protocol + 大包对象）
│   ├── adapters/                 # 外部系统适配器（LLM API）
│   ├── config/                   # 配置模块：TOML 加载 + YAML 装配
│   │   ├── loader.py            # TOML 配置加载器（ConfigLoader）
│   │   └── yaml_assembler.py    # YAML 装配加载器（YamlAssembler）
│   ├── messaging/                # 消息构造工具
│   ├── components/               # 各组件默认实现
│   ├── hooks/                    # Hook 系统
│   └── di.py                     # 装配入口（Harness 类）
├── profiles/                     # 领域模板
│   └── coding-assistant/
│       ├── profile.toml          # 模板元数据
│       ├── harness.yaml          # DI 装配声明
│       ├── AGENTS.md             # Agent 指导
│       └── README.md
├── tests/                        # 测试
│   ├── test_yaml_assembler.py    # YamlAssembler 单元测试
│   └── test_e2e_assembly.py      # 端到端装配集成测试
├── examples/                     # 示例项目
├── main.py                       # CLI 入口（harness init / run）
├── ARCHITECTURE.md               # 完整架构文档
└── README.md                     # 项目简介
```

---

## 二、各目录职责

### `harness/core/`

**放什么：**
- DI 容器实现
- 生命周期编排器（按固定顺序调用组件的方法）
- ToolRouter（框架内部，合并 SystemToolProvider/MCPAdapter，按名路由执行）
- 内部数据类型定义

**不放什么：**
- 任何组件接口定义（放 `interfaces/`）
- 任何具体组件实现（放 `components/`）

**模块边界：** `core/` 可以 import `interfaces/` 中的抽象类，**绝不能** import `components/` 中的具体实现。

---

### `harness/interfaces/`

**放什么：**
- 每个组件的抽象接口（Python `Protocol` 或 `ABC`）
- 大包对象的数据类定义（`UserRequest`、`AssemblyContext`、`Trajectory` 等）
- 如有枚举/常量，放此处

**不放什么：**
- 任何实现代码
- 任何依赖具体实现的逻辑

**命名约定：** 文件名 `xxx.py` 对应组件名 `Xxx`（如 `input_adapter.py` → `InputAdapter` 接口）

**文件列表：**
```
harness/interfaces/
├── __init__.py
├── types.py                  # 所有大包对象（UserRequest, SystemState, Attachment, EnvState,
│                             #   GuidesBundle, Example, AssemblyContext, Trajectory,
│                             #   Message, Response, ToolCall, ToolCallFunction,
│                             #   ToolDefinition, ToolCallRecord, ToolResult, ToolTransform, MemoryItem）
├── input_adapter.py          # InputAdapter 接口
├── guide_provider.py         # GuideProvider 接口 + GuideContext
├── context_assembler.py      # ContextAssembler 接口
├── memory_backend.py         # MemoryBackend 接口
├── sensor.py                 # Sensor 接口
├── tool.py                   # Tool 接口
├── system_tool_provider.py   # SystemToolProvider 接口（batch-06）
├── mcp_adapter.py            # MCPAdapter 接口（batch-06）
├── mcp_handler.py            # MCPHandler 接口（batch-06）
└── hook.py                   # Hook 接口 + HookContext
```

---

### `harness/components/`

**放什么：**
- 每个组件的默认实现

**不放什么：**
- 接口定义（放 `interfaces/`）
- 框架内核逻辑（放 `core/`）

**命名约定：** 目录名 `xxx/` 对应组件名，内部 `xxx_impl.py` 为默认实现

**文件列表：**
```
harness/components/
├── __init__.py
├── guide_provider/                # Batch-04（已实现）
│   ├── __init__.py
│   └── file_guide_provider.py    # FileGuideProvider（默认实现）
├── context_assembler/             # Batch-05（已实现）
│   ├── __init__.py
│   └── simple_assembler.py       # SimpleAssembler（默认实现，构造注入 MemoryBackend）
├── memory_backend/                # Batch-03（已实现）
│   ├── __init__.py
│   └── md_memory.py              # MdMemory（默认实现，启动时构建内存索引）
├── tool/                          # Batch-06（已实现）
│   ├── __init__.py
│   ├── base.py                   # BaseTool ABC（Tool 的便利基类）
│   ├── inline_tool.py            # @inline_tool 装饰器（将函数快速转为 Tool）
│   ├── system_tools.py           # 系统基础 Tool（ReadFileTool、WriteFileTool、ShellTool）
│   └── default_system_tool_provider.py  # DefaultSystemToolProvider（默认实现）
├── mcp_manager/                   # Batch-06（已实现）
│   ├── __init__.py
│   ├── default_mcp_adapter.py    # DefaultMCPAdapter（默认实现，含 ToolTransform + MCPHandler 管道）
│   └── mcp_client.py             # MCPClient（stdio/SSE 子进程管理）
├── input_adapter/                 # Batch-08（待实现）
│   └── __init__.py
└── sensor/                        # Batch-07（待实现）
    └── __init__.py
```

---

### `harness/hooks/`

**放什么：**
- Hook 系统的注册、链式调用实现
- 预留 Hook 点的常量定义

**文件：**
```
harness/hooks/
├── __init__.py
├── manager.py               # HookManager：注册 Hook、按事件名触发链式调用
└── events.py                # Hook 事件名常量定义（11 个 Hook 点）
```

**模块边界：** `hooks/` 可以 import `interfaces/`，**绝不能** import `components/`。

---

### `harness/di.py`

**放什么：**
- DI 容器类 `DIContainer`（register / resolve — 预构造实例注册模式）
- `Harness.from_container()` 工厂方法（从容器解析组件并启动生命周期编排）
- `Harness.register_hook()` — Hook 注册便捷方法

**模块边界：** 这是装配层文件。可以 import `core/`、`interfaces/`、`components/`、`hooks/` 中的所有模块。

---

### `harness/config/`

**放什么：**
- `loader.py` — TOML 配置文件加载器（`ConfigLoader`），读取 `profile.toml`
- `yaml_assembler.py` — YAML 装配加载器（`YamlAssembler`），读取 `harness.yaml` 并构建 DI 容器

**模块边界：** `yaml_assembler.py` 仅导入 `interfaces/` 和 `core/container.py`（类型级别），
不导入 `components/` 中的具体实现（通过动态 import）。

---

### `main.py`

**放什么：**
- CLI 入口：`harness init`（从领域模板生成新项目）+ `harness run`（按装配配置启动 Agent）
- 降级装配逻辑（无 YAML 配置时使用全默认组件）

**模块边界：** 顶层入口文件，可以 import 所有模块。`harness run` 命令优先使用 `YamlAssembler`，
配置文件不存在时降级为 Python API 装配。

---

### `tests/`

**放什么：**
- 每个组件至少一个测试文件 `test_<component_name>.py`
- 接口契约测试（验证实现满足接口）
- 集成测试（端到端装配）

**文件：**
```
tests/
├── __init__.py
├── test_input_adapter.py
├── test_guide_provider.py
├── test_context_assembler.py
├── test_md_memory.py
├── test_sensor.py
├── test_system_tool_provider.py
├── test_mcp_adapter.py
├── test_tool_router.py
├── test_hooks.py
├── test_di.py
├── test_yaml_assembler.py          # YamlAssembler 单元测试 (NEW batch-10)
├── test_e2e_assembly.py            # 端到端装配集成测试 (NEW batch-10)
└── test_integration.py             # 端到端集成测试
```

---

### `profiles/`

每个领域模板是一个独立文件夹，包含预设的组件装配方案和默认实现骨架。

**两种配置文件的职责分工：**
- `profile.toml` — 模板元数据（name, description, template, version, modules），由 `ConfigLoader` 读取
- `harness.yaml` — DI 装配声明（组件、Hook、LLM 配置），由 `YamlAssembler` 读取
- 两者共存于 profile 目录下，各司其职

```
profiles/
├── coding-assistant/
│   ├── profile.toml           # 模板元数据（ConfigLoader）
│   ├── harness.yaml           # DI 装配声明（YamlAssembler）
│   ├── AGENTS.md              # Agent 指导文件
│   └── README.md              # 使用说明
├── travel-assistant/
│   └── ...
└── research-assistant/
    └── ...
```

---

## 三、模块边界规则

```
允许的 import 方向（从上到下）：

  harness/interfaces/          ← 任何模块都可以 import
         ▲
         │
  ┌──────┴──────┐
  │             │
harness/core/  harness/hooks/  ← 可以 import interfaces/，不能 import components/
  ▲             ▲
  │             │
  └──────┬──────┘
         │
harness/components/            ← 可以 import interfaces/，可以 import hooks/
         ▲
         │
   harness/di.py               ← 可以 import 所有模块（装配层）

禁止的 import 方向：
  - core/ → components/        ✗ 内核不依赖具体实现
  - interfaces/ → 任何实现代码  ✗ 接口不依赖实现
  - hooks/ → components/       ✗ Hook 系统不依赖组件
```

---

## 四、文件命名约定

| 类别 | 命名规则 | 示例 |
|------|---------|------|
| 接口文件 | `snake_case.py`，与接口名对应 | `input_adapter.py` |
| 实现文件 | `snake_case.py`，体现实现方式 | `cli_adapter.py`, `md_memory.py` |
| 测试文件 | `test_<component>.py` | `test_input_adapter.py` |
| 大包对象 | 单文件集中定义 | `interfaces/types.py` |
| \_\_init\_\_.py | 导出公开接口，不写逻辑 | `from .cli_adapter import CliAdapter` |
