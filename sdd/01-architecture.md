# 01 — 架构概览

> 面向 agent 的浓缩版。全文 3-5 分钟读完。如需完整设计论证，见仓库根目录 [ARCHITECTURE.md](../ARCHITECTURE.md)。

---

## 一、定位

Harness Agent Template 是一个面向个人开发者和小型团队的**模块化 Agent 框架模板**。核心不是提供最强的性能，而是提供**最方便的裁剪与扩展能力**。

框架只定义接口契约与编排流程，所有具体行为由用户通过"实现接口 + 依赖注入"来自定义。

---

## 二、核心设计决策

### 2.1 微内核 + 插件架构

框架内核只做三件事：
- **生命周期编排** — 按固定顺序调用各组件
- **Hook 系统** — 在关键生命周期节点拦截并修改数据，组件间通过标准接口交互不直接耦合
- **依赖注入容器** — 管理组件实例的注册与解析

所有业务逻辑（如何压缩上下文、如何评估质量、如何存储记忆）都是满足接口契约的**插件**。

### 2.2 控制流与数据流分离

- **控制流**（框架固定）：会话初始化 → 多轮对话循环 → 会话结束，框架驱动
- **数据流**（组件自定义）：组件通过 MemoryBackend 间接交换数据，不直接耦合

### 2.3 领域模板驱动

不同场景（Coding、旅行、研究）通过 `profiles/` 下的领域模板快速启动。模板包含推荐的组件装配方案和默认实现骨架。

---

## 三、组件全景图

### 3.1 组件关系

```
                         ┌─────────────────────────────────┐
                         │         Core Orchestrator        │
                         │                                  │
                         │  ToolRouter (framework-internal) │
                         │  ┌─────────────────────────────┐ │
                         │  │ name → provider 路由表        │ │
                         │  │ "read_file"  → system        │ │
                         │  │ "fs_search"  → mcp           │ │
                         │  └─────────────────────────────┘ │
                         │  list_tools()  → 合并列表         │
                         │  execute()     → 查表分发         │
                         │  shutdown()    → 分发清理         │
                         └──────┬──────────────┬───────────┘
                                │              │
               ┌────────────────▼──┐  ┌────────▼──────────────┐
               │SystemToolProvider │  │ MCPAdapter             │
               │(DI 注册)           │  │ (DI 注册，不注册即裁切)   │
               │                   │  │                        │
               │ get_tools()       │  │ get_tools()            │
               │ execute()         │  │ execute()              │
               │                   │  │ shutdown()             │
               │ [ReadFileTool]    │  │                        │
               │ [WriteFileTool]   │  │ ┌────────────────────┐ │
               │ [ShellTool]       │  │ │ MCP Consumer       │ │
               │ (内置，自动注入)    │  │ │ (MCPClient)        │─┤→ 外部 MCP Server
               └───────────────────┘  │ └────────────────────┘ │
                                      │ ┌────────────────────┐ │
                                      │ │ Transform Pipeline  │ │
                                      │ │ 1. ToolTransform    │ │
                                      │ │    声明式转换        │ │
                                      │ │ 2. MCPHandler       │ │
                                      │ │    程序化转换(可选)   │ │
                                      │ └────────────────────┘ │
                                      └────────────────────────┘

                     ┌──────────────────────┐
                     │    InputAdapter      │
                     │ receive()/send(事件) │
                     └────────┬─────────────┘
                              │ UserRequest / AdapterEvent
              ┌───────────────┼───────────────┐
              ▼               ▼               │
     ┌────────────┐  ┌───────────────┐        │
     │GuideProvider│  │ContextAssembler│───────┤
     └─────┬──────┘  └───────┬───────┘       │
           │                  │               │
           │    ┌─────────────┼───────┐       ▼
           │    │             ▼       │  ┌─────────┐
           │    │  ┌─────────────────┐│  │   LLM   │
           │    │  │   ToolRouter    ││  └─────────┘
           │    │  │ (框架内部，非DI)  ││
           │    │  └────────┬────────┘│
           │    └───────────┼─────────┘
           │                │
           │    ┌───────────┼───────────┐
           │    │           │           │
           │    ▼           ▼           │
           │  ┌────────┐ ┌──────────┐   │
           │  │System  │ │MCPAdapter│   │
           │  │Tool    │ │(DI插件)   │   │
           │  │Provider│ └──────────┘   │
           │  └────────┘                │
           │                            │
           ▼                ▲           │
  ┌────────────────┐        │           │
  │ MemoryBackend  │────────┘           │
  └────────┬───────┘                    │
           ▲                            │
           │                            │
  ┌────────┴───────┐                    │
  │    Sensor      │                    │
  │ (构造注入 memory)│                    │
  └────────────────┘                    │
           │                            │
           └────────────────────────────┘
```

### 3.2 组件职责表

| 组件 | 职责 | 调用阶段 |
|------|------|---------|
| **InputAdapter** | 输入输出适配：接收用户输入，事件驱动推送响应流（前后台分离，batch-11） | 会话初始化 + 每轮用户输入 + LLM 响应时逐字段推送事件 |
| **GuideProvider** | 前馈控制：行动前提供指导 | 会话初始化（一次） |
| **ContextAssembler** | 上下文工程：拼接所有信息给 LLM | 每轮外层循环开始 |
| **ToolRouter** | 工具路由：合并 SystemToolProvider 和 MCPAdapter，按名分发执行（框架内部，非 DI） | 会话初始化 + 运行时 |
| **SystemToolProvider** | 系统工具提供者：管理本地实现的 Tool 集合（DI 插件，用户可替换） | 会话初始化 + 运行时 |
| **MCPAdapter** | MCP 适配层：消费外部 MCP Server，经转换后暴露工具（DI 插件，不注册即裁切） | 会话初始化 + 运行时 |
| **Sensor** | 反馈控制：评估完整多轮轨迹，沉淀知识到 MemoryBackend | 会话结束 |
| **MemoryBackend** | 记忆层：跨会话持久化与检索 | 会话初始化 + 会话结束 |
| **Hook** | 生命周期拦截：在关键节点插入自定义逻辑 | 各生命周期点 |

### 3.3 关键约束

- ContextAssembler **只从 MemoryBackend 读取**，永不直接接触 Sensor
- Sensor **直接操作 MemoryBackend**（通过构造函数注入），其评估结果通过记忆层间接影响下一轮上下文
- 所有组件通过 **DI 容器** 装配，依赖通过构造函数注入，用户创建实例后注册
- ToolRouter 是**框架内部组件**（非 DI），由编排器在 `_phase_init()` 中创建，合并 SystemToolProvider 和 MCPAdapter
- SystemToolProvider 和 MCPAdapter 是两个**独立的 DI 插件**，各自实现自己的 Protocol，可独立替换或裁切
- MCPAdapter 不注册到 DI 即表示裁切 MCP 功能
- 所有 Tool（系统 + MCP）统一走 ToolRouter 分发执行
- MCPAdapter 通过内部 MCPClient 连接外部 MCP Server，持有其生命周期（含 shutdown）

---

## 四、生命周期流程

### 阶段一：会话初始化（整个会话一次）

```
1. InputAdapter.receive() → UserRequest
2. 框架构建 GuideContext → GuideProvider.get_guides() → GuidesBundle
3. 框架从 MemoryBackend 检索相关记忆 (namespace="episodic")
4. 框架初始化 ToolRouter，注册 SystemToolProvider 和 MCPAdapter（若已 DI 注册），获取合并工具列表 (ToolRouter.list_tools())
5. 框架构建初始 AssemblyContext
   （含 user_request、guides、available_tools、history、memories、system_state）
```

### 阶段二：多轮对话循环

**外层循环**（用户每轮新输入时 `InputAdapter.receive()` 被调用）：

```
6. ContextAssembler.assemble() → List[Message]
7. 触发 before_llm_call Hook

   内层循环（Tool call 连续生成）：
   8. 框架将消息和工具定义转为 LLM 原生格式 → 调用 LLM → Response
   9. 触发 after_llm_call Hook
   10. 按 LLM Response 字段顺序逐一推送事件（batch-11 事件驱动）：
       - thinking → InputAdapter.send(ThinkingEvent)
       - 包含 tool_uses → 每个 tool 依次：
           → InputAdapter.send(ToolCallEvent)
           → 触发 before/after_tool_execute Hook
           → ToolRouter.execute()
           → InputAdapter.send(ToolResultEvent)
           → tool_use + tool_result 追加到 message list → 回到步骤 8
       - 包含 text    → InputAdapter.send(TextEvent) + InputAdapter.send(StopEvent) → 跳出内层循环
       
       （注：LLM 单次响应可同时包含 text 和 tool_uses，非互斥）

11. 等待用户下一轮输入 → InputAdapter.receive() → 更新 AssemblyContext → 回到步骤 6
    用户发出退出信号时 → 进入阶段三
```

### 阶段三：会话结束（整个会话一次）

```
12. 框架组装完整 Trajectory
13. 触发 on_session_end Hook
14. Sensor.sense(trajectory) → 写入 MemoryBackend
15. 触发 after_sensor Hook（只读观察）
16. 会话结束
```

### Hook 预留点（11 个）

```
before_guide_generation  → 修改 GuideContext
after_guide_generation   → 修改 GuidesBundle
before_assemble          → 修改 AssemblyContext
after_assemble           → 修改 Message 列表
before_llm_call          → 修改 Message 列表
after_llm_call           → 修改 Response
before_tool_execute      → 修改 ToolCall
after_tool_execute       → 修改 ToolResult
after_sensor             → 观察 Sensor 副作用（只读，在 Sensor.sense() 之后触发）
on_session_end           → 会话结束清理
on_error                 → 异常处理介入
```

### 跨会话记忆流动

```
会话 N（结束阶段）:
  Sensor 评估完整多轮 Trajectory → 写入 MemoryBackend (namespace="episodic")

会话 N+1（初始化阶段）:
  框架从 MemoryBackend 检索记忆 → ContextAssembler 融入上下文
  → 影响会话 N+1 的 LLM 输入
```
