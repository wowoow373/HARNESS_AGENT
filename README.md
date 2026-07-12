# Harness Agent Template — `showcase/agents`

> 这是 `showcase/agents` 分支，用于演示基于 Harness Runtime 构建的**领域 Agent**。
>
> 框架整体架构、接口契约和核心组件说明请查看 [`master` 分支的 README](https://github.com/wowoow373/HARNESS_AGENT/blob/master/README.md)。

---

## 本分支演示什么？

在 master 的 Runtime 多 Agent 能力之上，本分支实现了两个可直接运行的网页级 Agent 案例：

| 案例 | 路径 | 核心效果 |
|------|------|---------|
| **chat-web** | [`agents/chat-web/`](agents/chat-web/) | 把命令行交互替换成 WebSocket 网页聊天，支持自定义工具、emoji 渲染和会话记忆。 |
| **group-chat** | [`agents/group-chat/`](agents/group-chat/) | 多 Agent 与人类用户共处一个聊天室，展现差异化性格、自然抢话/插话、选择式回复与潜水机制。 |
| **trajectory-analyst** | `agents/trajectory-analyst/`（计划中） | 轨迹分析元 Agent，读取 Harness 自身的开发/运行轨迹，辅助框架自我迭代。 |

两个案例都基于同一套 Harness Runtime 接口，通过替换 `InputAdapter` / `ContextAssembler` / 添加自定义组件完成。

---

## chat-web：WebSocket 网页聊天助手

一个可在浏览器里对话的单 Agent，效果类似 ChatGPT 网页版。

### 运行

```bash
cd agents/chat-web
python server.py
# 打开 http://localhost:8000
```

### 关键替换

| 模块 | 默认实现 | 本案例替换为 | 目的 |
|------|---------|-------------|------|
| `AsyncInputAdapter` | `KernelBridgeAdapter` | `WebSocketAdapter` | 接收/发送 WebSocket 消息 |
| `SystemToolProvider` | `DefaultSystemToolProvider` | 自定义 `WebToolProvider` | 注入 `web_search`、`weather` |
| Hook | 无 | `before_assemble` emoji 约束 | 限制 LLM 只能输出预定义 `:name:` 表情 |

### 效果

- 浏览器实时收发消息
- 支持 `:happy:` `:laugh:` `:cool:` `:cry:` `:cute:` 五种表情渲染
- 可调用 `web_search` / `weather` 工具

详细说明：[agents/chat-web/README.md](agents/chat-web/README.md)

---

## group-chat：多人实时群聊 Agent

一个多 Agent + 人类用户共处的聊天室，演示 Agent 之间的动态互动。

### 运行

```bash
cd agents/group-chat
python server.py
# 打开 http://localhost:8000
```

### 关键设计

| 能力 | 实现方式 |
|------|---------|
| 差异化响应节奏 | 每个 Agent 配置不同的 `min_wait` / `max_wait` / `jitter` |
| 自然抢话/插话 | `AtomicOutputAdapter` 把回复按句子原子化切分，中间插入随机延迟 |
| 选择式回复 | `SelectiveGroupChatAssembler` 让 LLM 先选消息再回复，支持 `选择 0` 表示潜水 |
| 人类输入 | Kernel 允许 `subscribe(...).to("user")`，无需把用户声明为 `@agent` |

### 自定义组件

- `harness/components/input_adapter/flexible_group_chat_input_adapter.py` — 带缓冲窗口的输入适配器
- `harness/components/input_adapter/atomic_output_adapter.py` — 原子化输出切分适配器
- `harness/components/context_assembler/selective_group_chat_assembler.py` — 选择式群聊上下文组装器

### 效果

- 多个 Agent 同时在线，性格不同
- Agent 会等待、插话、潜水
- 用户可随时加入对话

详细说明：[agents/group-chat/README.md](agents/group-chat/README.md)

---

## customer-service：客服场景多意图 Agent

面向客服中常见的「知识问答 + 业务办理」混合咨询场景，演示如何在 Harness Runtime 上构建低幻觉、高任务完成率的领域 Agent。

### 运行

```bash
# Terminal 1: 启动 WebSocket 服务
python agents/customer-service/server.py

# Terminal 2: 启动 Runtime workflow
python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
```

浏览器打开 http://localhost:8000，或在终端输入 `/talk router 改签规则是什么？`。

### 核心设计

| 能力 | 实现方式 | 效果 |
|------|---------|------|
| 多意图分流 | Router Agent 将用户请求划分为知识问答、业务办理、异常兜底三类 | 降低复杂场景下的意图混淆与上下文丢失 |
| 验证式知识问答 | Direction → Evidence → Validation 三阶段 Workflow，每步只做「是否有依据」的判断 | 复杂多跳问题准确率 **93%**，反事实跟随率 **89%** |
| 分层任务记忆 | 按步骤依赖 / 步骤内经验 / 全局摘要三级粒度组织记忆 | 改签/退款等多步任务历史信息不再丢失 |
| 错误驱动边界提炼 | 失败任务自动总结为显式操作约束与步骤提示 | 轻量模型任务完成率从 **45% 提升至 61%** |
| 场景化评测 | 覆盖 QA、业务办理、异常兜底三类的标准化打分 | 量化支撑链路持续迭代 |

### 关键替换

| 模块 | 默认实现 | 本案例替换为 | 目的 |
|------|---------|-------------|------|
| `ContextAssembler` | `SimpleAssembler` | `customer_service_assembler.py` | 注入 QA 状态与任务记忆 |
| `GuideProvider` | `FileGuideProvider` | `customer_service_guide_provider.py` | 按 Agent 路由加载不同 AGENTS.md |
| Workflow | 单 Agent | Kernel 多 Agent 编排 | Router / Direction / Evidence / Validation / Task / Fallback 协同 |

详细说明：[agents/customer-service/README.md](agents/customer-service/README.md)

---

## trajectory-analyst：轨迹分析元 Agent（计划中）

一个用于**辅助 Harness 自我迭代**的元 Agent。它的核心职责是读取和分析 Harness 框架自身的开发轨迹与运行轨迹——包括代码变更、设计决策、Runtime 运行日志、Agent 行为记录等——并据此提出改进建议、生成下一阶段的实现计划或自动修复简单问题。

### 目标能力

- 读取 git 历史、PR 记录、设计文档，理解框架演进方向
- 分析 Runtime 运行轨迹，定位多 Agent 协作中的异常或低效模式
- 根据现有组件接口和约束，生成可落地的改造方案
- 作为 Harness 自我改进的入口，推动框架从“被人类维护”走向“被 Agent 辅助维护”

### 当前状态

🔜 尚未实现，处于设计阶段。本案例将验证 Harness Runtime 是否足够自举：让 Agent 读取自身文档、理解自身架构、并产出可执行的改进计划。

---

## 对核心框架的改动

本分支为了支持上述案例，对框架核心做了最小化扩展（这些改动已单独整理到可合入 master 的 PR）：

| 文件 | 改动 |
|------|------|
| `harness/runtime/kernel.py` | `_resolve_adapter` 支持向自定义适配器注入 `pid`/`kernel`/`runtime`；`spawn_from_script` 允许 `subscribe(...).to("user")` 虚拟发布者。 |

所有案例专属组件（`agents/*`、三个 group-chat 组件、设计文档）保留在本分支，不进入 master。

---

## 测试

```bash
# 框架核心测试
pytest tests/ --ignore=tests/test_real_llm_trace.py -v

# chat-web 测试
pytest agents/chat-web/tests/ -v

# group-chat e2e 测试
pytest agents/group-chat/test_e2e.py -v
```

---

## 许可证

MIT
