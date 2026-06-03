# 04 — 批次路线图

> 定义所有开发批次的执行顺序、依赖关系和产出目标。agent 在开始工作时先读此文件，确定自己在整体中的位置。

---

## 一、批次总览

| # | 批次名称 | 目标一句话 | 依赖 | 主要产出 |
|---|---------|-----------|------|---------|
| 01 | mvp | DI 容器 + 生命周期编排 + 配置加载 + LLM 适配器 + 组件接口占位 | 无 | `harness/core/*`, `harness/interfaces/*`, `harness/adapters/*`, `harness/config/*`, `harness/messaging/*`, `harness/di.py` |
| 02 | interfaces | 所有组件的抽象接口 + 大包对象定义 | 01 | `harness/interfaces/*` |
| 02-1 | interface-tests | 将 core/ 迁移为正式接口类型 + 全量接口测试 | 02 | 更新 `harness/core/*`, `harness/adapters/*`, `harness/messaging/*`; 新增 `tests/test_interfaces_*.py` |
| 03 | memory-backend | MemoryBackend 接口 + MdMemory 实现 | 02-1 | `harness/components/memory_backend/` |
| 04 | guide-provider | GuideProvider 接口 + FileGuideProvider 实现 | 02-1 | `harness/components/guide_provider/` |
| 05 | context-assembler | ContextAssembler 接口 + SimpleAssembler 实现 | 02-1, 03, 04 | `harness/components/context_assembler/` |
| 06 | tool-mcp-manager | Tool 抽象 + ToolRegistry + MCPManager + 系统基础 Tool | 02-1 | `harness/components/tool/`, `harness/components/mcp_manager/` |
| 07 | sensor | Sensor 接口 + LoggingSensor 实现 | 02-1, 03 | `harness/components/sensor/` |
| 08 | input-adapter | InputAdapter 接口 + CliAdapter 实现 | 02-1 | `harness/components/input_adapter/` |
| 09 | hooks | Hook 系统（注册、链式调用、所有 Hook 点） | 01 | `harness/hooks/*` |
| 10 | di-assembly | 端到端装配 + 集成测试 + 最少示例 | 01-09 | `main.py`, 集成测试, profiles/coding-assistant/ |

---

## 二、批次间数据流依赖

组件间的接口依赖决定了实现顺序。以下标记了每个批次实际需要其依赖组件提供的**数据结构**（非运行时调用）。

```
01-mvp          ← 无依赖
    ↓
02-interfaces      ← 依赖 01（DI 容器基础设施，用于接口注册）
    ↓
02-1-interface-tests ← 依赖 02（将 _Minimal* 迁移为正式类型，使后续批次面向最终类型开发）
    ↓
    ├── 03-memory-backend     ← 依赖 02-1（MemoryBackend 接口、MemoryItem 类型）
    ├── 04-guide-provider     ← 依赖 02-1（GuideProvider 接口、GuidesBundle 等类型）
    ├── 06-tool-mcp-manager   ← 依赖 02-1（Tool/ToolRegistry/MCPManager 接口）
    ├── 07-sensor             ← 依赖 02-1（Sensor 接口、Trajectory 类型）
    │                            + 依赖 03（MemoryBackend 实例用于写入）
    ├── 08-input-adapter      ← 依赖 02-1（InputAdapter 接口、UserRequest 类型）
    │
    └── 05-context-assembler  ← 依赖 02-1（ContextAssembler 接口）
                                + 依赖 03（从 MemoryBackend 读取实例）
                                + 依赖 04（需要使用 GuidesBundle 类型）

09-hooks            ← 依赖 01（DI 容器基础设施 + 生命周期点）
    ↓
10-di-assembly      ← 依赖 01-09 全部
```

---

## 三、各批次范围说明

### 01 — mvp（最小可行产品）

**范围：**
- `DIContainer` 类：预构造实例注册模式（`register(interface, instance)` + `resolve(interface)`）
- `LifecycleOrchestrator` 类：按三阶段（初始化→循环→结束）编排组件调用
- `MinimalLLMAdapter`：零依赖 OpenAI 兼容适配器，支持从环境变量和 ``harness/config/.env`` 读取配置
- 组件接口占位类型（`harness/interfaces/`）：作为 DI 容器注册 key
- 内部数据结构（`harness/core/types.py`）：`_Minimal*` 类型
- 消息构造工具（`harness/messaging/`）：OpenAI 兼容消息格式转换
- TOML 配置解析器（`harness/config/`）：读取 `profile.toml`，返回结构化配置
- 组件缺失时不阻塞，WARNING 日志 + 跳过对应步骤

**不在范围：**
- 任何组件具体实现（那是 batch-03 ~ 08）
- 正式的 Protocol/ABC 接口定义（那是 batch-02）
- Hook 系统（那是 batch-09）
- CLI 入口（那是 batch-10）

---

### 02 — interfaces（接口定义）

**范围：**
- `harness/interfaces/types.py` — 全部大包对象数据类
- 每个组件一个接口文件，定义抽象类/Protocol
- 所有方法签名、字段类型标注

**不在范围：**
- 任何实现类
- 测试（接口是纯定义，batch-02-1 完成迁移和测试）

---

### 02-1 — interface-tests（接口测试 + 类型统一）

**范围：**
- 将 `harness/core/orchestrator.py` 中的 `_Minimal*` 类型替换为正式接口类型
- 将 `harness/adapters/llm_adapter.py` 返回值类型升级为正式 `Response`
- 将 `harness/messaging/builder.py` 参数类型升级为正式类型
- 删除 `_normalize_*` 桥接方法（不再需要）
- 标记 `harness/core/types.py` 的 `_Minimal*` 类型为废弃
- 全量接口测试：正式 dataclass 类型测试 + Protocol conformance 测试 + 端到端集成测试
- 更新所有现有测试以使用正式类型

**不在范围：**
- 不修改 `harness/interfaces/` 中的接口定义
- 不新增任何组件实现
- 不修改 DI 容器和配置加载器

---

### 03 — memory-backend（记忆层）

**范围：**
- `MdMemory` 实现（Markdown 文件存储 + 启动时构建内存索引）
- 接口定义移到 `harness/interfaces/memory_backend.py`（若 batch-02-1 已就绪则直接使用）
- `read()`、`write()`、`search()`、`list_namespaces()` 完整实现
- `search()` 至少支持简单的文本匹配（关键词/子串）
- 单元测试

---

### 04 — guide-provider（前馈控制）

**范围：**
- `FileGuideProvider` 实现（从文件系统读取 `AGENTS.md` 等）
- 至少支持加载 identity、rules 两个字段
- 单元测试

---

### 05 — context-assembler（上下文工程）

**范围：**
- `SimpleAssembler` 实现（滑动窗口截断 + 拼接）
- 接收 AssemblyContext，产出自给 Message 列表
- MemoryBackend 通过构造函数注入
- 至少支持：system prompt（来自 guides）+ memories + history + user request 的拼接
- 滑动窗口：超过阈值时丢弃最旧的 message
- 单元测试

**前置条件：** 依赖 02-1、03（MemoryBackend 数据类型）和 04（GuidesBundle 数据类型）的实现已就绪。

---

### 06 — tool-mcp-manager（工具系统）

**范围：**
- `Tool` 抽象类 + `ToolDefinition` / `ToolResult` 类型
- `ToolRegistry` 实现：register / list_tools / execute
- `MCPManager` 接口 + `ServerMCPManager` + `InlineMCPManager`
- 至少一个系统基础 Tool（如文件读取工具）
- 单元测试

---

### 07 — sensor（反馈控制）

**范围：**
- `LoggingSensor` 实现（将轨迹写入 `episodic` 命名空间，MemoryBackend 通过构造函数注入）
- 至少能从 Trajectory 中提取基本信息并写入 MemoryBackend
- 单元测试

---

### 08 — input-adapter（输入适配）

**范围：**
- `CliAdapter` 实现（stdin 循环读取用户输入，stdout 打印响应）
- 能正确构造 UserRequest
- 单元测试

---

### 09 — hooks（Hook 系统）

**范围：**
- `HookManager` 实现（注册、链式调用）
- 所有 11 个 Hook 事件的常量定义
- 单个 Hook 失败不阻塞后续 Hook 执行
- 单元测试

---

### 10 — di-assembly（装配集成）

**范围：**
- `main.py` CLI 入口（`harness init / run`）
- 完整 DI 容器装配，全部默认组件实例连接
- 端到端集成测试：从 InputAdapter 进、LLM 调用（可用 mock）、到 Sensor 写出
- `coding-assistant` 模板最小骨架
- 全局验收标准验证（对照 `06-acceptance.md`）

**注意**：`_Minimal*` → 正式类型的迁移已在 batch-02-1 完成，batch-10 不再涉及类型替换。

---

## 四、agent 使用指南

1. 进入 `batches/batch-XX-name/` 文件夹
2. 先读 `design.md` — 理解本批次要做什么、怎么设计
3. 再读 `tasks.md` — 按顺序逐条执行，完成后勾选
4. 最后对照 `acceptance.md` — 确认所有验收标准通过
5. 移入下一批次

**注意：** 每个批次完成后，对应接口定义需同步回 `sdd/02-interfaces.md`（如果批次实现中发现了需要修正的接口细节）。
