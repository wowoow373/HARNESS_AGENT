# customer-service Agent 前端与终端输出设计

**日期**: 2026-07-12
**依赖**: [详细设计](./2026-07-12-customer-service-agent-detailed-design.md)
**目标读者**: 前端开发者、集成工程师

---

## 1. 输出架构概述

### 1.1 双通道输出的核心问题

Runtime Mode B 默认只有一个 `SystemConsole` 实例——`CliConsole`。而 customer-service 需要同时向**终端（CLI）** 和**浏览器（WebSocket）** 推送推理过程。

同时，`CliConsole` 收到的 `AgentOutput` 只有 `pid` + `content`（原始文本），前端无法从中解析出结构化的推理步骤（round/phase/candidates/triples/scores）。

### 1.2 解决方案：双通道 + 结构化旁路

```
                        ┌─── TextEvent ───→ KBA ──→ MessageBus ──→ AgentOutput ──→ CliConsole ──→ 终端
Agent.adapter.send() ──┤
                        └─── 结构化 dict ──→ FrontendBus ──→ WebSocket Server ──→ 浏览器
```

**设计原则**：
- **TextEvent 通道**：不改 Runtime 骨架，走标准 AgentOutput → CliConsole 路径。每个 adapter 负责将 LLM 输出**格式化为人类可读的终端文本**。
- **FrontendBus 通道**：新增的轻量旁路。每个 adapter 将**解析后的结构化数据**推入 `FrontendBus`。WebSocket server 从中读取并广播给浏览器。

### 1.3 不改 Runtime 骨架

| 组件 | 是否新增 | 说明 |
|---|---|---|
| `FrontendBus` | ✅ 新增 | `agents/customer-service/frontend_bus.py`，独立于 Runtime |
| `MultiplexConsole` | ✅ 新增 | 可选；如不需要终端输出可跳过 |
| `CustomerServiceConsole` | ✅ 新增 | 包装 CliConsole，增强终端格式化 |
| Runtime Kernel / AgentRuntime / MessageBus / KBA | ❌ 不改 | — |

---

## 2. FrontendBus 设计

### 2.1 接口

```python
# agents/customer-service/frontend_bus.py

import asyncio
import json
import time
from typing import Any


class FrontendBus:
    """Agent → 前端的结构化事件广播器。

    每个 adapter 在 send() 中调用 bus.emit(event) 推送结构化事件。
    WebSocket server 通过 subscribe() 注册消费者。
    """

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """注册一个消费者队列。WebSocket server 调用。"""
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def emit(self, event: dict) -> None:
        """向所有订阅者广播事件。Adapter 的 send() 中调用。

        线程安全：所有 adapter 运行在同一 event loop 中，
        put_nowait 不阻塞。
        """
        event["_timestamp"] = time.time()
        for q in self._queues:
            q.put_nowait(event)
```

### 2.2 事件类型定义

```python
# 所有前端事件的统一结构
# {
#     "type": str,        # 事件类型（见下表）
#     "pid": str,         # 来源 agent
#     "round": int,       # QA 轮次（非 QA agent 为 0）
#     "phase": str,       # direction | evidence | validation | answer | intent | fallback
#     "data": dict,       # 类型特定的结构化数据
# }
```

| type | 来源 Agent | 触发时机 | data 内容 |
|---|---|---|---|
| `intent_classified` | router | LLM 输出意图分类后 | `{intent, confidence, slots}` |
| `direction_start` | direction | 开始为 expandable 节点生成方向 | `{node_id, confirmed_triples}` |
| `direction_output` | direction | 候选方向生成完毕 | `{node_id, candidates: [[subj, rel]], remaining_question}` |
| `evidence_start` | evidence | 开始确认一个方向 | `{direction: [subj, rel], node_id}` |
| `evidence_output` | evidence | triple 确认完毕 | `{direction, triple: {subj, rel, obj}, valid: bool, source_passage, select_idx}` |
| `evidence_empty` | direction | 本轮无候选方向 | `{reason: "no_candidates" | "all_tried"}` |
| `validation_start` | validation | 开始全局校验 | `{graph_summary: {node_count, leaf_count}}` |
| `validation_output` | validation | 校验完毕 | `{decisions: {node_id: KEEP|DISCARD}, answer: str|null, keep_count, discard_count}` |
| `loop_continue` | validation | 继续下一轮 | `{next_round, expandable_count}` |
| `qa_answer` | validation | 找到最终答案 | `{answer, sources: [str], rounds}` |
| `qa_fallback` | validation | 无法回答 | `{reason: "max_hops"\|"no_expandable"\|"no_progress", rounds}` |
| `task_intent` | router | 意图为 task | `{confidence, slots}` |
| `fallback_intent` | router | 意图为 fallback | `{confidence}` |
| `workflow_complete` | (系统) | Workflow 完成 | `{agents: [{pid, output, error}]}` |

---

## 3. MultiplexConsole 设计

用于同时向终端和 WebSocket 推送系统事件（AgentSpawned、AgentFinished 等）。

```python
# agents/customer-service/multiplex_console.py

class MultiplexConsole:
    """将 SystemEvent 同时转发到 CliConsole 和 WebSocket。

    实现 SystemConsole Protocol（duck typing）。
    """

    def __init__(self, cli_console, ws_broadcaster):
        self._cli = cli_console
        self._ws = ws_broadcaster

    async def send(self, event):
        # 终端输出
        await self._cli.send(event)
        # WebSocket 广播（AgentOutput 事件对前端最重要）
        await self._ws.broadcast_system_event(event)

    async def receive(self):
        # 输入仅从 CLI 读取
        return await self._cli.receive()
```

---

## 4. CustomerServiceConsole 设计（可选）

> **MVP 注意**：终端输出可直接使用 `CliConsole`——每种 Agent 的 TextEvent 会以 `[{pid}] {content}` 格式打印，功能完整。CustomerServiceConsole 仅增加颜色和图标装饰，可在后续迭代中添加。

包装 CliConsole，增强终端输出的可读性。

```python
# agents/customer-service/customer_service_console.py

class CustomerServiceConsole:
    """增强的终端输出：颜色、图标、缩进。

    实现 SystemConsole Protocol。
    """

    # Agent 角色 → 终端颜色
    COLORS = {
        "router":     "\033[36m",  # 青色
        "direction":  "\033[33m",  # 黄色
        "evidence":   "\033[32m",  # 绿色
        "validation": "\033[35m",  # 紫色
        "task_agent": "\033[34m",  # 蓝色
        "fallback":   "\033[31m",  # 红色
    }
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"

    def __init__(self, cli_console):
        self._cli = cli_console

    async def send(self, event):
        if isinstance(event, AgentOutput):
            color = self.COLORS.get(event.pid, "")
            # 格式化：[direction] → 🔍 Direction
            label = self._agent_label(event.pid)
            prefix = f"{color}{label}{self.RESET}"
            # 缩进多行内容
            content = event.content.strip()
            lines = content.split("\n")
            if len(lines) == 1:
                print(f"{prefix} {lines[0]}")
            else:
                print(prefix)
                for line in lines:
                    print(f"  {self.DIM}│{self.RESET} {line}")
        else:
            await self._cli.send(event)

    async def receive(self):
        return await self._cli.receive()

    def _agent_label(self, pid: str) -> str:
        labels = {
            "router":     "🎯 Router",
            "direction":  "🔍 Direction",
            "evidence":   "📎 Evidence",
            "validation": "🛡️  Validation",
            "task_agent": "📋 Task",
            "fallback":   "⚠️  Fallback",
        }
        return labels.get(pid, f"[{pid}]")
```

---

## 5. 各 Agent 的输出格式

每个 Agent 的 `adapter.send()` 负责两条输出路径。以下是每个 Agent 的具体输出规范。

### 5.1 Router

**Adapter.send() 逻辑**：

```python
async def send(self, event, target=None):
    if isinstance(event, TextEvent):
        parsed = self._parse_intent(event.content)

        if parsed["intent"] == "qa":
            # ── 前端事件 ──
            self._frontend_bus.emit({
                "type": "intent_classified",
                "pid": "router",
                "round": 0,
                "phase": "intent",
                "data": {
                    "intent": "qa",
                    "confidence": parsed["confidence"],
                    "slots": parsed.get("slots", {}),
                }
            })

            # ── 终端格式（在 content 中已由 LLM 生成）──
            # 不做额外格式化，LLM 输出即终端显示

        elif parsed["intent"] == "task":
            self._frontend_bus.emit({
                "type": "task_intent",
                "pid": "router",
                "round": 0,
                "phase": "intent",
                "data": {"confidence": parsed["confidence"], "slots": parsed.get("slots", {})}
            })

        elif parsed["intent"] == "fallback":
            self._frontend_bus.emit({
                "type": "fallback_intent",
                "pid": "router",
                "round": 0,
                "phase": "intent",
                "data": {"confidence": parsed["confidence"]}
            })

    # 答案回传路径
    if isinstance(event, TextEvent):
        meta = self._get_current_task_metadata()
        if meta.get("type") == "qa_answer":
            # 这是 Formatting Router 对最终答案的格式化输出
            self._frontend_bus.emit({
                "type": "qa_answer",
                "pid": "router",
                "round": meta.get("rounds", 0),
                "phase": "answer",
                "data": {
                    "answer": meta["answer"],
                    "sources": meta.get("sources", []),
                    "rounds": meta.get("rounds", 0),
                }
            })

    await self._kba.send(event, target)
```

**终端输出示例**：
```
🎯 Router INTENT: qa | confidence: 0.94
```

### 5.2 Direction

**Adapter.send() 逻辑**：

```python
async def send(self, event, target=None):
    if isinstance(event, TextEvent):
        remaining_q, candidates = parse_draft_v3_output(event.content)

        node_id = self._current_node_id

        # ── 前端事件 ──
        self._frontend_bus.emit({
            "type": "direction_output",
            "pid": "direction",
            "round": self._current_round,
            "phase": "direction",
            "data": {
                "node_id": node_id,
                "candidates": [[s, r] for s, r in candidates],
                "remaining_question": remaining_q,
            }
        })

        # ── 终端格式 ──
        # event.content 保持 LLM 原始输出，由 CustomerServiceConsole 美化缩进

        # ...（后续 Evidence 任务分发逻辑不变）

    await self._kba.send(event, target)
```

**终端输出示例**：
```
🔍 Direction  节点: ROOT
  │ 子问题: 改签需要满足什么条件？
  │ 候选方向:
  │   • (航班, 改签规则)
  │   • (乘客, 适用条件)
```

### 5.3 Evidence

**Adapter.send() 逻辑**：

```python
async def send(self, event, target=None):
    if isinstance(event, TextEvent):
        parsed = _parse_final(event.content)

        direction = self._current_direction  # (subj, rel)
        passages = self._current_passages
        select_idx = parsed[3] if parsed and parsed != "INVALID" else None

        # ── 前端事件 ──
        if parsed and parsed != "INVALID":
            subj, rel, obj, idx = parsed
            source = passages[idx] if idx is not None and idx < len(passages) else ""
            self._frontend_bus.emit({
                "type": "evidence_output",
                "pid": "evidence",
                "round": self._current_round,
                "phase": "evidence",
                "data": {
                    "direction": list(direction),
                    "triple": {"subj": subj, "rel": rel, "obj": obj},
                    "valid": True,
                    "source_passage": source,
                    "select_idx": idx,
                }
            })
        else:
            self._frontend_bus.emit({
                "type": "evidence_output",
                "pid": "evidence",
                "round": self._current_round,
                "phase": "evidence",
                "data": {
                    "direction": list(direction),
                    "triple": None,
                    "valid": False,
                    "reason": "INVALID" if parsed == "INVALID" else "PARSE_ERROR",
                }
            })

        # ── 终端格式 ──
        # event.content 保持 LLM 原始输出

        # ...（后续同步屏障 + Validation 触发逻辑不变）

    await self._kba.send(event, target)
```

**终端输出示例**：
```
📎 Evidence  (航班, 改签规则)
  │ ✅ 航班 | 改签规则 | 起飞前2小时
  │    来源: 第3条：旅客可在起飞前2小时申请改签服务

📎 Evidence  (乘客, 适用条件)
  │ ✅ 乘客 | 适用条件 | 非特价舱位
  │    来源: 第5条：非特价舱位旅客适用本规则
```

### 5.4 Validation

**Adapter.send() 逻辑**：

```python
async def send(self, event, target=None):
    if isinstance(event, TextEvent):
        decisions = parse_validator_decisions(event.content, id_map)
        answer = parse_validator_answer(event.content)

        keep_nodes = [nid for nid, s in decisions.items() if s == 1]
        discard_nodes = [nid for nid, s in decisions.items() if s == 0]

        # ── 前端事件 ──
        self._frontend_bus.emit({
            "type": "validation_output",
            "pid": "validation",
            "round": self._current_round,
            "phase": "validation",
            "data": {
                "decisions": {
                    display_id: ("KEEP" if score == 1 else "DISCARD")
                    for display_id, score in decisions.items()
                },
                "answer": answer,
                "keep_count": len(keep_nodes),
                "discard_count": len(discard_nodes),
            }
        })

        if answer is not None:
            self._frontend_bus.emit({
                "type": "qa_answer",
                "pid": "validation",
                "round": self._current_round,
                "phase": "answer",
                "data": {
                    "answer": answer,
                    "sources": graph.get_sources(),
                    "rounds": self._current_round,
                }
            })
        elif self._current_round >= self._max_hops or not expandable:
            self._frontend_bus.emit({
                "type": "qa_fallback",
                "pid": "validation",
                "round": self._current_round,
                "phase": "answer",
                "data": {
                    "reason": "max_hops" if self._current_round >= self._max_hops else "no_expandable",
                    "rounds": self._current_round,
                }
            })
        else:
            self._frontend_bus.emit({
                "type": "loop_continue",
                "pid": "validation",
                "round": self._current_round,
                "phase": "validation",
                "data": {
                    "next_round": self._current_round + 1,
                    "expandable_count": len(expandable),
                }
            })

        # ── 终端格式 ──
        # event.content 保持 LLM 原始输出

        # ...（后续终止/继续逻辑不变）

    await self._kba.send(event, target)
```

**终端输出示例**：
```
🛡️  Validation  Round 1
  │ N0: KEEP   航班 | 改签规则 | 起飞前2小时
  │ N1: KEEP   乘客 | 适用条件 | 非特价舱位
  │
  │ 💡 ANSWER: 非特价舱位乘客可在起飞前2小时申请改签
```

---

## 6. 前端界面设计

### 6.1 整体布局

```
┌──────────────────────────────────────────────────────┐
│  Customer Service Agent                    🟢 已连接  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ 对话区域 ────────────────────────────────────┐  │
│  │                                                │  │
│  │  [用户] 改签规则是什么？                        │  │
│  │                                                │  │
│  │  [Agent] 根据查询结果，非特价舱位乘客可在       │  │
│  │          起飞前2小时申请改签。                  │  │
│  │                                                │  │
│  │          📚 参考来源                            │  │
│  │          · 第3条：旅客可在起飞前2小时...        │  │
│  │          · 第5条：非特价舱位旅客...             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ 推理过程 ────────────────────────────────────┐  │
│  │                                                │  │
│  │  ▶ Round 1                          🟢 已完成  │  │
│  │  │  🔍 方向生成                                │  │
│  │  │     候选 1: 航班 | 改签规则 | ?              │  │
│  │  │     候选 2: 乘客 | 适用条件 | ?              │  │
│  │  │                                             │  │
│  │  │  📎 证据锚定                                │  │
│  │  │     ✅ 航班 | 改签规则 | 起飞前2小时         │  │
│  │  │        来源: 第3条：旅客可在起飞前2小时...   │  │
│  │  │     ✅ 乘客 | 适用条件 | 非特价舱位          │  │
│  │  │        来源: 第5条：非特价舱位旅客...        │  │
│  │  │                                             │  │
│  │  │  🛡️ 全局校验                                │  │
│  │  │     N0: KEEP ✓                              │  │
│  │  │     N1: KEEP ✓                              │  │
│  │  │     💡 答案: 非特价舱位乘客可在起飞前2小时.. │  │
│  │  ▼                                             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
├──────────────────────────────────────────────────────┤
│  [输入消息...]                              [发送]   │
└──────────────────────────────────────────────────────┘
```

### 6.2 前端 JS 事件处理

```javascript
// 处理 FrontendBus 事件
function handleFrontendEvent(data) {
    switch (data.type) {
        case 'intent_classified':
            showIntentBadge(data.data.intent, data.data.confidence);
            if (data.data.intent === 'qa') {
                startReasoningPanel();
            }
            break;

        case 'direction_output':
            addDirectionCard(data.round, data.data);
            break;

        case 'evidence_output':
            addEvidenceCard(data.round, data.data);
            break;

        case 'validation_output':
        case 'qa_answer':
        case 'qa_fallback':
        case 'loop_continue':
            addValidationCard(data.round, data.data, data.type);
            break;
    }
}
```

### 6.3 推理面板的 DOM 结构

```html
<div class="reasoning-panel">
  <div class="round-group" data-round="1">
    <div class="round-header" onclick="toggleRound(this)">
      <span class="round-indicator">▶</span>
      <span class="round-title">Round 1</span>
      <span class="round-badge done">已完成</span>
    </div>
    <div class="round-body">
      <!-- Direction -->
      <div class="phase-card direction">
        <div class="phase-header">🔍 方向生成</div>
        <div class="phase-body">
          <div class="candidate-list">
            <div class="candidate-item">
              <span class="candidate-subj">航班</span>
              <span class="candidate-rel">改签规则</span>
            </div>
            <div class="candidate-item">
              <span class="candidate-subj">乘客</span>
              <span class="candidate-rel">适用条件</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Evidence -->
      <div class="phase-card evidence">
        <div class="phase-header">📎 证据锚定</div>
        <div class="phase-body">
          <div class="evidence-item valid">
            <div class="evidence-triple">航班 | 改签规则 | 起飞前2小时</div>
            <div class="evidence-source">来源: 第3条：旅客可在起飞前2小时申请改签服务</div>
          </div>
          <div class="evidence-item valid">
            <div class="evidence-triple">乘客 | 适用条件 | 非特价舱位</div>
            <div class="evidence-source">来源: 第5条：非特价舱位旅客适用本规则</div>
          </div>
        </div>
      </div>

      <!-- Validation -->
      <div class="phase-card validation">
        <div class="phase-header">🛡️ 全局校验</div>
        <div class="phase-body">
          <div class="decision-list">
            <div class="decision-item keep">N0: KEEP ✓</div>
            <div class="decision-item keep">N1: KEEP ✓</div>
          </div>
          <div class="answer-box">💡 非特价舱位乘客可在起飞前2小时申请改签</div>
        </div>
      </div>
    </div>
  </div>
</div>
```

### 6.4 关键 CSS

```css
/* 推理面板 */
.reasoning-panel {
  background: #f8fafc;
  border-radius: 12px;
  margin: 12px 0;
  overflow: hidden;
}

.round-group {
  border-left: 3px solid #e2e8f0;
  margin-left: 8px;
  transition: border-color 0.3s;
}

.round-group.active {
  border-left-color: #3b82f6;
}

.round-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 600;
  font-size: 14px;
  user-select: none;
}

.round-indicator {
  font-size: 10px;
  transition: transform 0.2s;
  display: inline-block;
}

.round-group.open .round-indicator {
  transform: rotate(90deg);
}

.round-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  margin-left: auto;
}

.round-badge.done { background: #dcfce7; color: #166534; }
.round-badge.active { background: #dbeafe; color: #1e40af; }

/* 阶段卡片 */
.phase-card {
  margin: 6px 14px;
  border-radius: 8px;
  overflow: hidden;
}

.phase-card.direction { background: #fef9c3; border: 1px solid #fde047; }
.phase-card.evidence  { background: #dcfce7; border: 1px solid #86efac; }
.phase-card.validation { background: #f3e8ff; border: 1px solid #d8b4fe; }

.phase-header {
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
}

.phase-body {
  padding: 8px 12px;
  font-size: 13px;
}

/* Triple 展示 */
.evidence-item {
  padding: 6px 0;
  border-bottom: 1px solid rgba(0,0,0,0.05);
}

.evidence-triple {
  font-family: "SF Mono", "Fira Code", monospace;
  font-size: 13px;
  color: #1e293b;
}

.evidence-source {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
}

.evidence-item.invalid .evidence-triple {
  color: #dc2626;
  text-decoration: line-through;
}

/* 决策展示 */
.decision-item {
  padding: 3px 0;
  font-family: monospace;
  font-size: 13px;
}

.decision-item.keep { color: #166534; }
.decision-item.discard { color: #991b1b; }

/* 答案框 */
.answer-box {
  margin-top: 8px;
  padding: 10px 14px;
  background: #eff6ff;
  border: 1px solid #93c5fd;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #1e40af;
}
```

---

## 7. WebSocket Server 设计

### 7.1 架构

```python
# agents/customer-service/server.py

import asyncio
import json
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from frontend_bus import FrontendBus

app = FastAPI()
frontend_bus = FrontendBus()  # 由 workflow 脚本注入到各 adapter

# WebSocket 连接管理
connected_clients: list[WebSocket] = []


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)

    # 订阅 FrontendBus
    queue = frontend_bus.subscribe()

    try:
        while True:
            # 从 FrontendBus 取事件 → 推送到浏览器
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:
                break
    except Exception:
        pass
    finally:
        connected_clients.remove(websocket)


@app.get("/")
async def root():
    return HTMLResponse(content=open("static/index.html").read())
```

### 7.2 启动方式

```bash
# 终端 1：启动 WebSocket server
python agents/customer-service/server.py

# 终端 2：启动 Runtime workflow
python -c "
from harness.runtime.runtime import Runtime
from agents.customer_service.customer_service_console import CustomerServiceConsole
from harness.runtime.cli_console import CliConsole

cli = CliConsole(mode='mode_b')
console = CustomerServiceConsole(cli)
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
```

### 7.3 workflow 脚本中注入 FrontendBus

`FrontendBus` 实例在 workflow 脚本中创建，通过构造函数注入到各 adapter：

```python
# customer_service_workflow.py
from frontend_bus import FrontendBus

# ★ 与 server.py 共享同一 FrontendBus 实例
# 实际实现中使用 module-level singleton 或依赖注入
frontend_bus = FrontendBus()

@agent("direction", ...)
def assemble_direction():
    ...
    container.register(AsyncInputAdapter,
        DirectionAdapter(memory=memory, frontend_bus=frontend_bus))
    ...
```

---

## 8. CLI 终端输出：完整效果

以用户问"改签规则是什么？"为例：

```
[系统] Runtime 启动
[系统] Agent spawned: router
[系统] Agent spawned: direction
[系统] Agent spawned: evidence
[系统] Agent spawned: validation
[系统] Agent spawned: task_agent
[系统] Agent spawned: fallback

──────────────────────────────────────────────────
/talk router 改签规则是什么？

🎯 Router  INTENT: qa | confidence: 0.94

── Round 1 ──────────────────────────────────────

🔍 Direction  节点: ROOT
  │ 子问题: 改签需要满足什么条件？
  │ 候选方向:
  │   • (航班, 改签规则)
  │   • (乘客, 适用条件)

📎 Evidence  (航班, 改签规则)
  │ ✅ 航班 | 改签规则 | 起飞前2小时
  │    来源: 第3条：旅客可在起飞前2小时申请改签服务

📎 Evidence  (乘客, 适用条件)
  │ ✅ 乘客 | 适用条件 | 非特价舱位
  │    来源: 第5条：非特价舱位旅客适用本规则

🛡️  Validation  Round 1
  │ N0: KEEP   航班 | 改签规则 | 起飞前2小时
  │ N1: KEEP   乘客 | 适用条件 | 非特价舱位
  │
  │ 💡 ANSWER: 非特价舱位乘客可在起飞前2小时申请改签

🎯 Router  最终回答:
  │ 根据查询结果，非特价舱位乘客可在起飞前2小时申请改签。
  │
  │ 参考来源：
  │   · 第3条：旅客可在起飞前2小时申请改签服务
  │   · 第5条：非特价舱位旅客适用本规则

[系统] Agent finished: router (3.2s, 正常完成)
[系统] Agent finished: validation (0.8s, 正常完成)
[系统] Agent finished: evidence (1.5s, 正常完成)
[系统] Agent finished: direction (1.2s, 正常完成)
[系统] Workflow wf_001 完成:
  router       正常  2轮  3.2s
  direction    正常  1轮  1.2s
  evidence     正常  2轮  1.5s
  validation   正常  1轮  0.8s
[系统] Runtime 停止
```

---

## 9. 完整事件流时序

```
时间 ──────────────────────────────────────────────────────────→

用户输入
  │
  ▼
Router.receive()
  │
  ├─→ LLM 分类
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "intent_classified", ...})
        └─→ TextEvent → KBA → AgentOutput → CustomerServiceConsole
                                          → "🎯 Router INTENT: qa | confidence: 0.94"

Direction.receive()
  │
  ├─→ LLM 方向生成
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "direction_output", ...})
        └─→ TextEvent → KBA → AgentOutput → "🔍 Direction 节点: ROOT ..."

Evidence.receive()  ← task 1
  │
  ├─→ 检索 (确定性)
  ├─→ LLM triple 确认
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "evidence_output", ...})
        └─→ TextEvent → "📎 Evidence ✅ 航班 | 改签规则 | 起飞前2小时"

Evidence.receive()  ← task 2
  │
  ├─→ ...同上...
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "evidence_output", ...})
        ├─→ pending.received == pending.total → kernel.send_input("validation")
        └─→ TextEvent → "📎 Evidence ✅ 乘客 | 适用条件 | 非特价舱位"

Validation.receive()
  │
  ├─→ LLM 全局校验
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "validation_output", ...})
        ├─→ frontend_bus.emit({type: "qa_answer", ...})
        ├─→ kernel.send_input("router", answer)
        ├─→ kernel.end_workflow("wf_001")
        └─→ TextEvent → "🛡️ Validation ... 💡 ANSWER: ..."

Router.receive()  ← answer
  │
  ├─→ LLM 格式化
  │
  └─→ adapter.send()
        ├─→ frontend_bus.emit({type: "qa_answer", ...})
        └─→ TextEvent → "🎯 Router 最终回答: ..."

Router → EXIT_SENTINEL → FINISHED
Direction → EXIT_SENTINEL → FINISHED
Evidence → EXIT_SENTINEL → FINISHED
Validation → FINISHED

WorkflowFinished → 终端显示摘要
```

---

## 10. 设计决策汇总

| 决策 | 结论 | 理由 |
|---|---|---|
| 双通道机制 | TextEvent 走标准路径 + FrontendBus 旁路 | 不改 Runtime 骨架；TextEvent 覆盖 CLI，FrontendBus 覆盖前端 |
| 前端事件粒度 | 每个推理步骤一个事件 | 前端渐进式渲染，用户可以看到"推理正在发生" |
| 终端格式化 | CustomerServiceConsole 包装 CliConsole | 不改 CliConsole 源码；agent pid → 角色图标映射 |
| 推理面板交互 | 折叠式 Round 分组，默认展开当前轮 | 用户关注最新进展，历史可回看 |
| WebSocket 通信 | 独立 FastAPI server + FrontendBus 订阅 | 与 Runtime 进程解耦，前端独立部署 |
| 前端技术栈 | Vanilla HTML/CSS/JS（单文件） | 延续 chat-web 风格，零框架依赖 |

---

*本文档与 [详细设计](./2026-07-12-customer-service-agent-detailed-design.md) 配套使用。实现时先完成 FrontendBus + 终端输出，再搭建前端页面。*
