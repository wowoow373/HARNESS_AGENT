# 可信业务 Agent 客服前端集成设计

**日期**: 2026-07-12  
**范围**: 产品级高层设计，聚焦 `harness_agent` Runtime 与 `topic_code` 验证式问答能力的端到端集成  
**目标读者**: 产品/工程负责人、面试官

---

## 1. 背景与目标

### 1.1 背景

现有两个独立课题：

- **`topic_code`（可信业务 Agent 算法研究）**：实现了基于 Generator-Validator 双角色的多跳问答链路，在复杂 QA 数据集上达到 **93% 答案准确率** 与 **89% 反事实跟随率**。
- **`harness_agent`（Agent 开发平台）**：设计了模块化 Agent Runtime，支持多 Agent 生命周期管理、上下文组织与交互适配，已验证 Chat-Web、Group-Chat 等场景。

两个课题目前尚未连接成一个可演示的完整系统。

### 1.2 目标

搭建一个**最小可演示的客服 Agent 系统**，证明：

1. `topic_code` 的验证式问答能力可以被产品化为客服场景的知识问答链路。
2. `harness_agent` 的 Runtime 可以编排多 Agent 协作拓扑。
3. 用户可以通过浏览器界面与终端同时观察到多跳推理的完整过程。

### 1.3 非目标

- 不实现完整的业务办理逻辑（改签、退款等）。
- 不做生产级部署、性能优化、安全加固。
- 不替换 `topic_code` 的已有训练/评估脚本。

---

## 2. 设计范围

本期聚焦 **Option B（产品文档 + 最小 demo）**：

- **必做**:
  - 入口 `Router Agent`：识别用户意图（知识问答 / 业务办理 / 异常兜底）。
  - 三个平级 Runtime Agent 实现验证式问答链路：
    - `Direction Agent`（方向生成）
    - `Evidence Agent`（证据锚定）
    - `Validation Agent`（全局校验）
  - `Task Agent`、`Fallback Agent` 作为架构占位，保证三意图链路可通。
  - 基于 `harness_agent/agents/chat-web` 的浏览器界面 + 终端事件输出。
  - 使用 `topic_code` 的 QA 数据集验证效果。

- **不做**:
  - 不训练新模型，直接复用 `topic_code` 的 prompt 与模型配置。
  - 不实现真实业务规则引擎。
  - 不做大规模前端改造，只调整信息展示。

---

## 3. 高层架构

### 3.1 整体拓扑

```
用户（浏览器 / 终端）
        ↓
InputAdapter（WebSocket / CLI）
        ↓
Harness Runtime / Message Bus
        ↓
┌─────────────────────────────────────────────────────┐
│              Customer Service Workflow               │
│         （平级编排 Router / Direction / Evidence       │
│          / Validation / Task / Fallback）             │
└─────────────────────────────────────────────────────┘
        ↓
OutputAdapter → 浏览器界面 + 终端事件流
```

### 3.2 关键原则

- **所有 Agent 平级**：`Direction / Evidence / Validation` 与 `Router` 一样都是 Runtime 级 Agent，不由某个父 Agent 内部 spawn，便于前端观察和事件追踪。
- **Retriever 内嵌于 Evidence Agent**：检索不是 Tool 选择，也不是 Workflow 独立节点，而是 `Evidence Agent` 内部上下文编排的固定步骤。
- **状态集中管理**：`SubGraphMerger` 作为多跳推理的状态图，由 Workflow 维护并传递给各 Agent。

---

## 4. Agent 角色与职责

### 4.1 Router Agent（入口意图识别）

- **职责**: 判断用户当前请求属于哪类意图。
- **输入**: 用户消息 + 会话历史摘要。
- **输出**: `intent ∈ {qa, task, fallback}` + 置信度 + 预抽取槽位。
- **判定标准**:
  - `qa`: 政策咨询、事实性问题（如“改签规则是什么？”）。
  - `task`: 明确要办理业务（如“我要改签”）。
  - `fallback`: 意图不明、敏感、低置信度或超出范围。

### 4.2 Direction Agent（方向生成）

- **职责**: 基于当前已确认事实，提出下一步探索方向。
- **输入**: 原始问题 + 已确认 triples + 当前证据段落 + 候选数量 K。
- **输出**: 剩余子问题 + 候选方向列表 `[(subj, rel)]`。
- **产品价值**: 让模型不再直接生成答案，而是先判断“下一步该查什么”，降低幻觉。

### 4.3 Evidence Agent（证据锚定）

- **职责**: 把一个方向落地为“有来源的事实”。
- **输入**: 方向 `(subj, rel)` + 知识库 corpus + 已确认 triples。
- **内部行为**:
  1. 自动构造查询 `subj rel`。
  2. 调用 Retriever 获取 Top-K passages（非 Tool 选择，强制注入）。
  3. 调用 LLM 从 passages 中确认 triple：`subj | rel | obj` 或 `INVALID`。
- **输出**: 确认的三元组 + 来源段落 + 是否有效。
- **产品价值**: 每个事实必须“有依据”，回答忠实性由证据保证。

### 4.4 Validation Agent（全局校验）

- **职责**: 站在全图视角判断哪些节点可靠、是否能回答问题。
- **输入**: 原始问题 + 当前推理图状态（nodes / edges / triples）。
- **输出**:
  - 每个节点的 `KEEP / DISCARD` 决策。
  - 若证据充分，直接输出 `ANSWER`。
  - 推理过程（structure / semantic / comprehensive / rethink）。
- **产品价值**: 避免局部正确但全局矛盾的答案，累积性降低幻觉。

### 4.5 Task Agent（业务办理占位）

- **职责**: 承接业务办理意图，本期仅做最小流程示意。
- **输入**: 用户消息 + 槽位。
- **输出**: 确认用户意图并请求必要信息（如订单号）。
- **后续扩展**: 接入真实订单/支付/工单系统，实现改签/退款状态机。

### 4.6 Fallback Agent（异常兜底占位）

- **职责**: 处理无法回答或置信度低的场景。
- **输入**: 用户消息 + 置信度。
- **输出**: 标准兜底话术 + 转人工建议。
- **产品价值**: 明确安全边界，避免模型越界回答。

---

## 5. 多跳问答工作流

### 5.1 主循环

```
初始化 SubGraph + expandable = {ROOT}
for round in 1..max_hops:

  1. Direction Agent
     → 对每个 expandable 节点生成候选方向 [(subj, rel)]

  2. Evidence Agent（并行处理每个方向）
     → 内部自动检索
     → 输出 triple / INVALID
     → 有效 triple 加入 SubGraph

  3. Validation Agent
     → 对全图打分：KEEP / DISCARD
     → 若输出 ANSWER，终止循环并返回答案

  4. Workflow 更新 expandable = KEEP 节点集合，进入下一轮

若循环结束无答案 → 返回兜底回复
```

### 5.2 事件流（前端/终端可见）

| 事件 | 来源 | 展示内容 |
|---|---|---|
| `router_decision` | Router | 意图分类结果 + 置信度 |
| `direction_start` | Workflow | 方向生成阶段开始 |
| `direction_output` | Direction | 候选方向列表 |
| `evidence_start` | Workflow | 证据锚定阶段开始 |
| `evidence_output` | Evidence | 确认的 triple / INVALID |
| `validation_start` | Workflow | 全局校验阶段开始 |
| `validation_output` | Validation | KEEP/DISCARD + ANSWER |
| `answer` | Workflow | 最终答案 + 引用证据 |
| `fallback_reply` | Fallback | 兜底话术 |

---

## 6. 状态与记忆

### 6.1 核心状态

- **`SubGraphMerger`**: 维护多跳推理图，包含节点 triple、来源段落、父子关系、路径。
- **`expandable` 集合**: 当前可扩展节点 ID。
- **`tried_candidates`**: 已尝试过的 `(subj, rel)` 方向，避免重复。
- **会话历史**: 用户与 Agent 的多轮消息。

### 6.2 存储方式

- 推理状态（SubGraph、expandable 等）按 `session_id` 存入 `MemoryBackend`。
- 复用 `harness_agent` 的 `MdMemory` 默认实现，无需引入外部数据库。

---

## 7. 前端与终端可视化

### 7.1 浏览器界面

复用 `harness_agent/agents/chat-web/static/index.html`，主要增强：

- 消息气泡区分用户/Agent。
- 折叠面板展示推理过程：
  - 意图识别结果
  - 方向生成
  - 证据锚定（含来源段落）
  - 全局校验
  - 最终答案
- 终端风格的事件日志区域。

### 7.2 终端输出

Workflow 每产生一个事件，同步打印到终端：

```text
[Router] intent=qa, confidence=0.94
[Direction] candidates: [(航班, 改签规则), (乘客, 适用条件)]
[Evidence] (航班, 改签规则) → triple: 航班 | 改签规则 | 起飞前2小时
[Evidence] (乘客, 适用条件) → triple: 乘客 | 适用条件 | 非特价舱位
[Validation] N0: KEEP, N1: KEEP, ANSWER: 非特价舱位乘客可在起飞前2小时申请改签
```

---

## 8. 项目结构

### 8.1 新增目录

在 `harness_agent/agents/` 下新建与 `chat-web`、`group-chat` 平级的文件夹，**不修改任何已有示例目录**：

```
agents/
├── chat-web/                # 已有示例
├── group-chat/              # 已有示例
├── coding-assistant/        # 已有示例
└── customer-service/        # 本期新增
    ├── AGENTS.md            # 客服 Agent 人设与约束
    ├── README.md            # 运行说明与架构简介
    ├── server.py            # FastAPI WebSocket 入口
    ├── static/
    │   └── index.html       # 前端页面
    ├── adapter/
    │   └── websocket_adapter.py  # 可复用或微调 chat-web 的适配器
    ├── agents/
    │   ├── router_agent.py      # 入口意图识别
    │   ├── direction_agent.py   # 方向生成
    │   ├── evidence_agent.py    # 证据锚定（含内部检索）
    │   ├── validation_agent.py  # 全局校验
    │   ├── task_agent.py        # 业务办理占位
    │   └── fallback_agent.py    # 异常兜底占位
    ├── workflow/
    │   └── qa_workflow.py       # 多跳问答编排逻辑
    └── memory/                  # 会话隔离记忆目录
```

### 8.2 与 topic_code 的关系

- 不复制 `topic_code` 源码。
- 通过 `PYTHONPATH` 或软链接使 `topic_code/src/` 可被导入。
- 复用 `topic_code` 的 `CoreController`、`SubGraphMerger`、`ValidatorEngine`、`Retriever` 等核心模块。
- 复用 `topic_code/configs/core_api_v2.yaml` 的 API 模型配置，避免本地 GPU 依赖。

---

## 9. 成功标准

### 9.1 功能标准

- [ ] 浏览器可访问客服前端，输入多跳问题后能看到完整推理过程。
- [ ] 终端同步打印事件流。
- [ ] Router 能正确区分 `qa` / `task` / `fallback`。
- [ ] QA 链路能跑通 `topic_code` 的验证式循环并输出答案。
- [ ] 答案附带引用证据，可追溯来源段落。

### 9.2 效果标准

- [ ] 在 `topic_code` 的 QA 数据集上复现或接近已有指标（准确率 93%、反事实跟随率 89%）。
- [ ] 演示 3–5 个典型多跳客服问题（如改签规则、赔偿条件、会员权益等）。

---

## 10. 后续扩展路线

### 10.1 近期（1–2 个月）

- 完善 `Task Agent` 的最小状态机：订单查询 → 条件校验 → 操作确认 → 结果反馈。
- 引入简单业务规则层（如退票时间窗、票价规则）。
- 增强前端：支持多轮对话、会话列表、历史回看。

### 10.2 中期（3–6 个月）

- 将业务办理链路接入真实mock或测试环境（订单系统、支付系统）。
- 实现失败案例的自动沉淀与边界提炼（错误驱动的约束学习）。
- 建立场景化评测体系：任务完成率、流程准确性、执行效率。

### 10.3 远期（6 个月以上）

- 支持多模态输入（图片、语音）。
- 引入人工坐席接管与 A/B 测试能力。
- 探索更细粒度的 Agent 拆分（如把 Retriever 升级为可主动改写的 Evidence Agent）。

---

## 11. 风险与取舍

| 风险 | 应对 |
|---|---|
| `topic_code` 与 Harness 的接口不兼容 | 通过包装层隔离，不改动 `topic_code` 源码 |
| 演示效果过度依赖 prompt 调优 | 复用 `topic_code` 已验证的 prompt，减少重写 |
| 前端与终端事件不同步 | 统一事件模型，由 Workflow 统一发射 |
| 多 Agent 平级导致拓扑复杂 | 本期只保留 6 个 Agent，不继续拆分 |

---

## 12. 附录：与简历项目的关系

| 简历项目 | 本设计中的体现 |
|---|---|
| 可信业务 Agent 产品设计 | 多意图分流架构、验证式 QA 链路、异常兜底 |
| 知识问答链路（93% 准确率） | Direction / Evidence / Validation 三个平级 Agent |
| 业务办理链路（61% 完成率） | Task Agent 占位，后续填充 |
| 场景化评测体系 | 成功标准中的功能与效果指标 |
| Agent 开发平台 / Runtime | Harness Runtime 编排所有 Agent，标准事件适配浏览器与终端 |

---

*本设计为高层方案，不涉及具体代码实现。下一步将根据本设计输出详细实施计划。*
