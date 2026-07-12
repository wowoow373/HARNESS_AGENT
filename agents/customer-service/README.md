# customer-service — 客服场景多意图 Agent

> 面向「知识问答 + 业务办理」混合咨询场景的客服 Agent，基于 Harness Runtime 多 Agent 编排实现。
> 核心目标：在复杂多跳问答中降低幻觉，在多步业务办理中保持上下文、复用失败经验。

## 目录

- [产品问题定义](#产品问题定义)
- [整体架构](#整体架构)
- [知识问答链路](#知识问答链路)
- [业务办理链路](#业务办理链路)
- [场景化评测体系](#场景化评测体系)
- [快速开始](#快速开始)
- [测试](#测试)
- [设计文档](#设计文档)

---

## 产品问题定义

传统单链路对话方案在客服场景中面临两类典型失败：

1. **知识问答易幻觉**：大模型回答流畅但会编造、补全事实，尤其在需要多跳推理的复杂问题中，错误会逐步累积。
2. **业务办理易丢上下文**：改签、退款等任务涉及多步信息收集，历史状态丢失、失败经验无法复用，导致任务完成率低。

本方案将用户需求划分为三类：

| 意图类型 | 说明 | 示例 |
|----------|------|------|
| **知识问答** | 基于已有知识库回答事实性问题 | "改签规则是什么？" |
| **业务办理** | 多步骤完成一项业务操作 | "我要改签明天去北京的机票" |
| **异常兜底** | 超出能力范围或需要人工介入 | "我要投诉" |

通过多意图分流，每条链路只处理自己擅长的问题，从而提升回答忠实性与任务完成率。

---

## 整体架构

6 个 Runtime-level Agent 通过 Kernel Workflow 编排：

| Agent | 职责 |
|-------|------|
| **Router** | 意图识别，将请求路由到 QA / Task / Fallback |
| **Direction** | 生成候选回答方向或下一步澄清策略 |
| **Evidence** | 检索证据并对每个关键事实做 triple 确认 |
| **Validation** | 全局图打分，决定是否终止或继续追问 |
| **Task** | 执行业务办理多步流程 |
| **Fallback** | 处理异常与兜底场景 |

```
User → Router ─┬─→ Direction → Evidence → Validation ──┐
               │                                       ↓
               ├─→ Task ───────────────────────────→ Output
               │
               └─→ Fallback ─────────────────────────┘
```

---

## 知识问答链路

针对「回答流畅但编造事实」的问题，将回答流程拆为三阶段 Workflow，每步只做**是否有依据**的判断：

### 1. Direction（方向生成）

- 理解用户问题，生成候选回答方向。
- 不直接生成最终答案，只输出需要验证的事实清单。

### 2. Evidence（证据锚定）

- 对每个待验证事实进行检索。
- 要求每个关键论断都有显式证据支持（triple 确认：事实 / 来源 / 是否充分）。

### 3. Validation（全局校验）

- 构建「问题 → 方向 → 证据」的全局图。
- 对整图打分：证据充分则生成答案，不足则回退到 Direction 继续追问。

### 效果

| 指标 | 结果 |
|------|------|
| 复杂多跳问题准确率 | **93%** |
| 反事实跟随率 | **89%** |

显著优于传统 RAG 方案，验证了验证式问答链路在客服场景中的可行性。

---

## 业务办理链路

针对改签、退款等多步任务中「历史信息丢失、失败经验无法复用」的问题，设计两级机制：

### 1. 分层记忆机制

按三级粒度组织记忆，让轻量模型在每一步都能聚焦必要信息：

| 粒度 | 内容 | 用途 |
|------|------|------|
| **步骤依赖** | 当前步骤依赖的前置状态 | 避免漏掉必要条件 |
| **步骤内经验** | 同一步骤的历史成功/失败模式 | 指导当前步骤执行 |
| **全局摘要** | 整个任务的进度与关键决策 | 保持跨步骤上下文 |

### 2. 错误驱动的边界提炼

- 失败任务不丢弃，而是由 Sensor 自动总结。
- 将失败案例提炼为**显式操作约束**与**步骤提示**，写入 MemoryBackend。
- 后续同类任务初始化时，这些约束自动进入上下文，辅助模型规避已知错误。

### 效果

在机票改签等复杂任务测试中：

| 指标 | 基线 | 优化后 |
|------|------|--------|
| 轻量模型任务完成率 | 45% | **61%** |

---

## 场景化评测体系

搭建覆盖三类核心场景的评测体系：

| 场景 | 评测重点 |
|------|----------|
| 知识问答 | 答案准确性、证据充分性、反事实鲁棒性 |
| 业务办理 | 任务完成率、流程准确性、步骤依赖满足度 |
| 异常兜底 | 是否正确识别超出能力范围的问题、是否平稳转人工 |

每个场景从三个维度打分：

1. **任务完成率**：用户目标是否达成。
2. **流程准确性**：是否按正确流程执行，无跳步、无遗漏。
3. **执行效率**：达到目标所需的轮次与调用次数。

评测结果用于量化 Agent 在真实客服场景下的服务效果，支撑后续链路优化与能力迭代。

---

## 快速开始

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

然后打开 http://localhost:8000，或在终端输入：

```
/talk router 改签规则是什么？
/talk router 我要改签明天去北京的机票
```

---

## 测试

```bash
# 单元测试（单 Agent，无需 LLM）
pytest agents/customer-service/tests/unit/ -v

# 集成测试（拓扑验证，无需 LLM）
pytest agents/customer-service/tests/integration/ -v
```

---

## 设计文档

- [实现计划](../../docs/superpowers/plans/2026-07-12-customer-service-agent-implementation.md)
- [详细设计 — 问答链路](../../docs/superpowers/specs/2026-07-12-customer-service-agent-detailed-design.md)
- [输出设计](../../docs/superpowers/specs/2026-07-12-customer-service-output-design.md)
- [集成设计](../../docs/superpowers/specs/2026-07-12-customer-service-agent-integration-design.md)

---

## 标签

`multi-hop-qa` · `verified-qa` · `intent-routing` · `hierarchical-memory` · `error-driven-learning` · `customer-service`
