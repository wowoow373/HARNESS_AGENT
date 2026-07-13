# customer-service Agent 详细设计

**日期**: 2026-07-12
**状态**: 详细设计，待评审
**依赖**: [topic_code 实现参考](./2026-07-12-topic-code-implementation-reference.md)、[集成高层设计](./2026-07-12-customer-service-agent-integration-design.md)
**目标读者**: 负责在 `agents/customer-service/` 落地的工程师

---

## 1. 设计概述

### 1.1 双层架构映射

本设计严格遵循 `harness_agent` 的双层架构原则：

| 层 | 职责 | 本设计中的体现 |
|---|---|---|
| **Runtime 层**（机制） | Agent 生命周期、消息路由、pub-sub 拓扑 | Kernel 管理 6 个 AgentRuntime；MessageBus 承载 agent 间 TextEvent 广播；KernelBridgeAdapter 作为默认 I/O 通道 |
| **Agent 层**（策略） | 通过可插拔 DI 组件决定每个 Agent 的行为 | 每个 Agent 有独立的 DIContainer，通过自定义 AsyncInputAdapter + ContextAssembler 实现角色差异 |

**核心原则**：不改 Runtime 骨架代码。所有定制逻辑通过 DI 组件（AsyncInputAdapter、ContextAssembler）和 workflow 脚本（`@agent` + `subscribe()`）实现。

### 1.2 与 topic_code 的对齐策略

| topic_code 组件 | customer-service 中的实现方式 |
|---|---|
| `Generator.draft_generate_list_v3()` | Direction Agent 的 ContextAssembler + LLM 调用 |
| `Retriever.retrieve()` | Evidence Agent 的 ContextAssembler 内确定性代码（构造注入） |
| `Generator.final_generate_v3()` | Evidence Agent 的 ContextAssembler + LLM 调用 |
| `Validator.score_graph_with_raw()` | Validation Agent 的 ContextAssembler + LLM 调用 |
| `CoreController.run()` 主循环 | Direction → Evidence → Validation 的消息闭环，循环状态存 MemoryBackend |
| `SubGraphMerger` | `SubGraphManager`（networkx 重新实现） |
| `src/prompts.py` 中的 3 个 system prompt | 分别硬编码在 Direction/Evidence/Validation 的 ContextAssembler 中 |
| `parse_draft_v3_output` / `_parse_final` / `parse_validator_decisions` / `parse_validator_answer` | 分别在 Direction/Evidence/Validation 的 AsyncInputAdapter.send() 中调用 |

---

## 2. 整体拓扑

### 2.1 Agent 列表与通信拓扑

```
                         ┌──────────────────────┐
                         │         user         │ (virtual publisher)
                         └──────────┬───────────┘
                                    │ subscribe
                                    ▼
┌──────────┐  subscribe  ┌──────────────────┐
│ task_agent│◄───────────│      router       │───subscribe───► fallback
└──────────┘             └────────┬─────────┘               (oneshot)
                                  │ subscribe
                                  │ (qa intent → kernel.send_input to direction)
                                  ▼
                    ┌─────────────────────┐
                    │     direction       │◄──────── subscribe ──────────┐
                    └────────┬────────────┘                             │
                             │ kernel.send_input                        │
                             │ (task per candidate)                     │
                             ▼                                          │
                    ┌─────────────────────┐                             │
                    │     evidence        │                             │
                    └────────┬────────────┘                             │
                             │ kernel.send_input                        │
                             │ (when pending.received == pending.total) │
                             ▼                                          │
                    ┌─────────────────────┐                             │
                    │    validation       │─────────────────────────────┘
                    └─────────────────────┘
                             │ (no answer → kernel.send_input to direction)
                             │ (answer found → kernel.send_input to router
                             │              + kernel.end_workflow)
```

**6 个 Agent 的 mode 与订阅关系**：

| Agent | Mode | 订阅 |
|---|---|---|
| `router` | continuous | `subscribe("router").to("user")` |
| `direction` | continuous | `subscribe("direction").to("user")`（虚拟订阅，仅用于强制 continuous mode） |
| `evidence` | continuous | `subscribe("evidence").to("user")`（虚拟订阅，仅用于强制 continuous mode） |
| `validation` | continuous | `subscribe("validation").to("user")`（虚拟订阅，仅用于强制 continuous mode） |
| `task_agent` | continuous | `subscribe("task_agent").to("router")` |
| `fallback` | continuous | `subscribe("fallback").to("router")` |

> **为什么需要虚拟订阅**：框架在 `Kernel.spawn_from_script()` 中根据 `has_subscriptions` 自动决定 mode——有订阅 = `continuous`，无订阅 = `oneshot`。Direction/Evidence/Validation 之间通过 `kernel.send_input()` 直投通信（不走 pub-sub），如果不加虚拟订阅会被设为 `oneshot`，一轮后退出，导致第二轮循环消息进入无人读取的队列。虚拟订阅不产生实际消息流——"user" 是虚拟 publisher，不会向这些 Agent 推送消息。

**关键设计决策**：Direction/Evidence/Validation 之间不使用 pub-sub 订阅，而是通过 `kernel.send_input()` 直接投递结构化任务。原因：
- QA 循环是严格的 request-response 模式，不是事件广播
- 每个任务携带结构化 metadata（direction tuple、graph state 等），需要精确路由
- pub-sub 的 TextEvent 承载的是 LLM 原始输出文本，而任务分发需要的是**解析后的结构化数据**

### 2.2 通信路径汇总

| 路径 | 机制 | 载荷 |
|---|---|---|
| user → router | CliConsole `/talk router` → Kernel → input_queue | UserRequest(text=用户消息) |
| router → direction | `kernel.send_input()` | UserRequest(metadata={question, expandable, triples, ...}) |
| router → task_agent | pub-sub TextEvent | task_agent 的 ContextAssembler 从 TextEvent 提取 intent |
| router → fallback | pub-sub TextEvent | fallback 的 ContextAssembler 从 TextEvent 提取 intent |
| direction → evidence | `kernel.send_input()` | UserRequest(metadata={direction, corpus, triples, ...}) |
| evidence → validation | `kernel.send_input()` | UserRequest(metadata={trigger: "evidence_complete"}) |
| validation → direction | `kernel.send_input()` | UserRequest(metadata={expandable_nodes, graph_state, ...}) |
| validation → router | `kernel.send_input()` | UserRequest(metadata={type: "qa_answer", answer, sources}) |
| 所有 agent → 前端 | KBA.send(TextEvent) → AgentOutput | 人类可读的推理步骤展示 |

---

## 3. 共享状态设计

QA 循环的全局状态存储在 MemoryBackend 的 `qa_state` namespace 下，key 为 `loop`。

### 3.1 状态结构

```python
{
    "question": str | None,          # 原始用户问题
    "round": int,                     # 当前轮次（从 1 开始）
    "max_hops": int,                  # 硬上限（默认 4）
    "phase": str,                     # "idle" | "direction" | "evidence" | "validation" | "done"
    "expandable": list[str],          # 当前可扩展节点 ID 列表
    "graph": dict,                    # SubGraphManager.to_dict()
    "pending": {
        "total": int,                 # 本轮期望的 Evidence 任务总数
        "received": int,              # 已完成的 Evidence 任务数
        "results": list[dict],        # 已收集的 Evidence 结果
    },
    "K": int,                         # 每节点最多候选方向数（默认 2）
    "top_k_retrieve": int,            # 每方向检索 passage 数（默认 5）
    "tried_candidates": dict,         # {node_id: [(subj_lower, rel_lower)]}
    "answer": str | None,             # 最终答案
    "sources": list[str] | None,      # 答案来源引用
}
```

### 3.2 读写权限

| Agent | 读 | 写 |
|---|---|---|
| Router | — | 初始化 question, round, expandable, graph |
| Direction | expandable, graph, tried_candidates, K | pending.total, tried_candidates, phase |
| Evidence | graph, pending | graph（添加节点）, pending.received, pending.results |
| Validation | 全部 | round, expandable, phase, pending(重置), answer, sources |

### 3.3 初始化（Router 判定 QA intent 后写入）

```python
memory.write("loop", "qa_state", {
    "question": "改签规则是什么？",
    "round": 1,
    "max_hops": 4,
    "phase": "direction",
    "expandable": ["ROOT"],
    "graph": SubGraphManager().to_dict(),
    "pending": {"total": 0, "received": 0, "results": []},
    "K": 2,
    "top_k_retrieve": 5,
    "tried_candidates": {},
    "answer": None,
    "sources": None,
})
```

---

## 4. 各 Agent 详细设计

### 4.1 Router Agent

#### DI 容器组成

```python
container = DIContainer()

# ★ MemoryBackend — 共享实例（QA 状态存储）
memory = MdMemory(path=f"./memory/customer_service/shared")
container.register(MemoryBackend, memory)

# AsyncInputAdapter — 自定义：解析 LLM 输出并按 intent 路由
# ★ 构造注入 memory（供 send() 中初始化 QA 状态）
container.register(AsyncInputAdapter, RouterAdapter(memory=memory))

# ContextAssembler — 自定义：组装意图分类 prompt
container.register(ContextAssembler, RouterAssembler())

# GuideProvider — 静态身份定义
container.register(GuideProvider, FileGuideProvider(paths=["AGENTS.md"]))

# SystemToolProvider — 默认（无需工具）
container.register(SystemToolProvider, DefaultSystemToolProvider())

# Sensor
container.register(Sensor, LoggingSensor(memory=memory))
```

#### Agent 角色

识别用户意图（`qa` / `task` / `fallback`），将请求路由到对应的下游 Agent。**Router 的 LLM 只做分类，不做对话。**

#### I/O 契约

| 方向 | 格式 | 说明 |
|---|---|---|
| **输入** | `UserRequest(text=<用户消息>, metadata={from: "user"})` | 用户通过 `/talk router <消息>` 发送 |
| **输出（qa）** | `kernel.send_input("direction", UserRequest(metadata={...}))` | 初始化 memory.loop，发送首轮 Direction 任务 |
| **输出（task）** | `adapter.send(TextEvent)` 走 pub-sub | task_agent 订阅 router，从 TextEvent 提取 intent |
| **输出（fallback）** | `adapter.send(TextEvent)` 走 pub-sub | fallback 订阅 router |
| **接收答案** | `UserRequest(metadata={type: "qa_answer", answer, sources})` | Validation 发回的最终答案 |

#### RouterAdapter（AsyncInputAdapter）

```python
class RouterAdapter:
    """
    KBA 的轻量包装。在 send() 中解析 LLM 意图分类输出，按 intent 路由。

    构造注入：
    - memory: MemoryBackend（用于初始化 QA 共享状态）

    路由逻辑：
    - qa → 初始化 memory.loop → kernel.send_input("direction", ...)
    - task → 将原始用户消息注入 TextEvent metadata，task_agent 通过 pub-sub 收到
    - fallback → 不做额外处理（fallback 通过 pub-sub 收到 TextEvent）
    - 收到 type="qa_answer" → 正常进入 ContextAssembler → LLM 格式化答案
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._current_user_message = ""  # 保存原始用户消息

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        request = await self._kba.receive()
        # 保存原始用户消息（供 task/fallback 路由时使用）
        if request.text and not request.metadata.get("type"):
            self._current_user_message = request.text
        return request

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            parsed = self._parse_intent(event.content)
            # parsed = {"intent": "qa", "confidence": 0.94, "slots": {}}

            if parsed["intent"] == "qa":
                # 1. 初始化共享状态
                self._memory.write("loop", "qa_state", {...初始状态...})

                # 2. 向 Direction 发送首轮任务
                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_user_message,
                        "expandable_nodes": [{
                            "node_id": "ROOT",
                            "confirmed_triples": [],
                            "evidence_passages": [],
                        }],
                    }
                ))

            elif parsed["intent"] == "task":
                # ★ 将原始用户消息附加到 metadata，供 task_agent 使用
                event.content = self._current_user_message

            elif parsed["intent"] == "fallback":
                # fallback 通过 pub-sub 收到 TextEvent
                pass

        # 始终委托给 KBA（前端可见 TextEvent）
        await self._kba.send(event, target)

    def _parse_intent(self, text: str) -> dict:
        """从 LLM 输出解析意图。期望格式:
        INTENT: qa
        CONFIDENCE: 0.94
        SLOTS: {}
        """
        ...
```

#### RouterAssembler（ContextAssembler）

```python
class RouterAssembler:
    """
    组装意图分类 prompt。

    普通输入（from="user"）→ 组装分类 prompt。
    答案回传（type="qa_answer"）→ 组装答案格式化 prompt。
    """

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata if ctx.user_request else {}

        # 路径 A：Validation 发回的答案 → 格式化输出
        if meta.get("type") == "qa_answer":
            system = "你是客服助手，请根据以下信息回答用户问题。引用来源。"
            user = (
                f"用户问题：{meta.get('question', '')}\n\n"
                f"答案：{meta['answer']}\n\n"
                f"参考来源：\n" + "\n".join(f"- {s}" for s in meta.get("sources", []))
            )
            return [
                Message(role="system", content=system),
                Message(role="user", content=user),
            ]

        # 路径 B：用户消息 → 意图分类
        system = """你是客服系统入口路由。分析用户消息，判定意图。

意图类型：
- qa: 政策咨询、知识问答、事实性问题（如"改签规则是什么？""赔偿标准？"）
- task: 明确要办理业务（如"我要改签""帮我退款"）
- fallback: 意图不明、敏感问题、超出客服范围

输出格式（严格遵守）：
INTENT: <qa|task|fallback>
CONFIDENCE: <0-1>
SLOTS: <JSON dict>
"""
        return [
            Message(role="system", content=system),
            Message(role="user", content=ctx.user_request.text),
        ]
```

#### 职责边界

- ✅ 意图分类
- ✅ 按意图路由到下游
- ✅ QA 答案的最终格式化输出
- ❌ 不存储对话历史（由 ContextAssembler 的 history 参数自然处理）
- ❌ 不做 QA 推理

---

### 4.2 Direction Agent

#### DI 容器组成

```python
container = DIContainer()

# ★ MemoryBackend — 共享（读 graph、tried_candidates；写 pending.total）
memory = MdMemory(path=f"./memory/customer_service/shared")
container.register(MemoryBackend, memory)

# AsyncInputAdapter — 自定义：管理多节点循环 + 解析 LLM 输出
# ★ 构造注入 memory
container.register(AsyncInputAdapter, DirectionAdapter(memory=memory))

# ContextAssembler — 自定义：组装 draft prompt
container.register(ContextAssembler, DirectionAssembler(K=2))

# GuideProvider — 不需要（prompt 在 Assembler 中硬编码）
# 不注册

# SystemToolProvider — 默认（无需工具）
container.register(SystemToolProvider, DefaultSystemToolProvider())

# Sensor
container.register(Sensor, LoggingSensor(memory=memory))
```

#### Agent 角色

基于当前已确认事实（confirmed_triples）和证据段落（evidence_passages），对每个 expandable 节点生成下一步探索方向 `[(subj, rel)]`。实现 topic_code 的 `draft_generate_list_v3()` 功能。

#### I/O 契约

| 方向 | 格式 | 说明 |
|---|---|---|
| **输入** | `UserRequest(metadata={task: "generate_directions", question, expandable_nodes: [{node_id, confirmed_triples, evidence_passages}], K})` | 来自 Router 或 Validation |
| **输出（LLM）** | TextEvent(content=`<remaining_question>...\n<next_facts>\n1. subj \| rel \| ?`) | 由 DirectionAdapter.send() 解析 |
| **输出（任务分发）** | `kernel.send_input("evidence", UserRequest(metadata={direction: (subj, rel), ...}))` | 每个候选方向一个任务 |

#### 多节点处理策略

当 `expandable_nodes` 包含多个节点时，Direction **逐节点处理**（每次 AgentRuntime turn 处理一个节点）：

1. Adapter.receive() 收到含 N 个 expandable_nodes 的 task
2. Adapter 将 N 个节点存入实例变量 `_pending_nodes`，弹出第一个节点
3. 返回仅含第一个节点信息的 UserRequest
4. ContextAssembler → LLM → Adapter.send() 解析 candidates
5. Adapter.send() 中：将本节点的 Evidence 任务**暂存**到 `_accumulated_tasks`
6. 若 `_pending_nodes` 非空 → `kernel.send_input("direction", {下一个节点})`（自触发）
7. 若 `_pending_nodes` 为空 → 全部节点处理完毕 → 一次性发送所有 `_accumulated_tasks` 到 Evidence → 设置 `pending.total`

**为什么先积累、后发送**：确保 Evidence 处理第一个任务时 `pending.total` 已经是最终值，避免 Evidence 过早触发 Validation。

#### DirectionAdapter（AsyncInputAdapter）

```python
class DirectionAdapter:
    """
    管理多节点循环 + LLM 输出解析 + Evidence 任务分发。

    状态变量（实例级）：
    - _pending_nodes: list[dict]  — 待处理的 expandable 节点
    - _accumulated_tasks: list[dict]  — 累积的 Evidence 任务
    - _current_question: str  — 当前问题
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._pending_nodes = []
        self._accumulated_tasks = []
        self._current_question = ""
        self._current_node_id = None

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        raw = await self._kba.receive()
        meta = raw.metadata

        if meta.get("task") == "generate_directions":
            # 新任务：初始化待处理节点列表
            self._current_question = meta["question"]
            self._pending_nodes = list(meta["expandable_nodes"])
            self._accumulated_tasks = []
            # 弹出第一个节点
            first_node = self._pending_nodes.pop(0)
            return UserRequest(
                text="",
                metadata={
                    "task": "generate_directions",
                    "question": meta["question"],
                    "node_id": first_node["node_id"],
                    "confirmed_triples": first_node["confirmed_triples"],
                    "evidence_passages": first_node["evidence_passages"],
                    "K": meta.get("K", 2),
                }
            )

        # 非任务消息透明传递
        return raw

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            # 1. 解析 LLM 输出
            remaining_q, candidates = parse_draft_v3_output(event.content)
            # candidates: [(subj, rel), ...]

            # 2. 读取 memory，过滤已尝试方向
            state = self._memory.read("loop", "qa_state")
            node_id = state.get("_current_node_id", "ROOT")  # 从 state 获取
            tried = state.get("tried_candidates", {}).get(node_id, [])
            fresh = [
                (s, r) for s, r in candidates
                if (s.lower(), r.lower()) not in tried
            ]

            # 3. 为每个候选方向构造 Evidence 任务（暂存）
            for subj, rel in fresh:
                self._accumulated_tasks.append({
                    "task": "confirm_triple",
                    "question": self._current_question,
                    "direction": (subj, rel),
                    "confirmed_triples": state["graph"].get_path_triples(node_id),
                    "corpus": state.get("corpus", []),
                    "node_id": node_id,
                })

            # 4. 更新 tried_candidates
            tried.extend([(s.lower(), r.lower()) for s, r in fresh])
            state["tried_candidates"][node_id] = tried
            self._memory.write("loop", "qa_state", state)

            # 5. 检查是否还有待处理节点
            if self._pending_nodes:
                # 自触发：处理下一个节点
                next_node = self._pending_nodes.pop(0)
                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": self._current_question,
                        "node_id": next_node["node_id"],
                        "confirmed_triples": next_node["confirmed_triples"],
                        "evidence_passages": next_node["evidence_passages"],
                        "K": state.get("K", 2),
                    }
                ))
            else:
                # 所有节点处理完毕 → 发送全部累积的 Evidence 任务
                if self._accumulated_tasks:
                    state["pending"]["total"] = len(self._accumulated_tasks)
                    state["pending"]["received"] = 0
                    state["pending"]["results"] = []
                    state["phase"] = "evidence"
                    self._memory.write("loop", "qa_state", state)

                    for task in self._accumulated_tasks:
                        self._kernel.send_input("evidence", UserRequest(
                            text="", metadata=task
                        ))
                else:
                    # 无候选方向 → 直接触发 Validation（所有方向已尝试或无法生成）
                    self._kernel.send_input("validation", UserRequest(
                        text="", metadata={"trigger": "direction_empty"}
                    ))

        # 始终委托给 KBA（前端可见）
        await self._kba.send(event, target)
```

#### DirectionAssembler（ContextAssembler）

```python
class DirectionAssembler:
    """
    组装 topic_code 的 draft system prompt + user content。

    使用从 topic_code 提取的：
    - CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY (system)
    - build_core_draft_v3_user_content() (user content builder)
    """

    def __init__(self, K: int = 2):
        self._K = K

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata

        system = CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_draft_v3_user_content(
            question=meta["question"],
            evidence_passages=meta.get("evidence_passages", []),
            confirmed_triples=meta.get("confirmed_triples", []),
            K=meta.get("K", self._K),
        )

        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

#### 职责边界

- ✅ 对每个 expandable 节点生成候选方向
- ✅ 过滤已尝试方向
- ✅ 积累并分发 Evidence 任务
- ✅ 设置 pending.total（同步屏障）
- ❌ 不做检索
- ❌ 不做 triple 确认
- ❌ 不维护 SubGraph（只读路径信息）

---

### 4.3 Evidence Agent

#### DI 容器组成

```python
container = DIContainer()

# ★ MemoryBackend — 共享（读 corpus/graph；写 graph/pending）
memory = MdMemory(path=f"./memory/customer_service/shared")
container.register(MemoryBackend, memory)

# AsyncInputAdapter — 自定义：解析 LLM triple 输出 + 同步屏障检查
# ★ 构造注入 memory
container.register(AsyncInputAdapter, EvidenceAdapter(memory=memory))

# ContextAssembler — 自定义：强制检索 + 组装 final prompt
# ★ 构造注入 Retriever + memory
retriever = InMemoryRetriever(corpus)
container.register(ContextAssembler, EvidenceAssembler(
    retriever=retriever,
    memory=memory,
    top_k=5,
))

# GuideProvider — 不需要
# SystemToolProvider — 默认（检索不走 Tool）
container.register(SystemToolProvider, DefaultSystemToolProvider())

# Sensor
container.register(Sensor, LoggingSensor(memory=memory))
```

#### Agent 角色

接收一个候选方向 `(subj, rel)`，强制执行检索 → LLM 确认 triple。实现 topic_code 的 `retrieve() + final_generate_v3()` 功能。

**检索是 ContextAssembler 内的确定性代码**，不是 Tool。LLM 只看到检索结果（passages），不需要决定"是否检索"。

#### I/O 契约

| 方向 | 格式 | 说明 |
|---|---|---|
| **输入** | `UserRequest(metadata={task: "confirm_triple", question, direction: (subj, rel), confirmed_triples, corpus, node_id})` | 来自 Direction |
| **输出（LLM）** | TextEvent(content=`subj \| rel \| obj \| SELECT: idx` 或 `INVALID`) | 由 EvidenceAdapter.send() 解析 |
| **输出（触发 Validation）** | `kernel.send_input("validation", ...)` | 仅当 `pending.received == pending.total` |

#### EvidenceAdapter（AsyncInputAdapter）

```python
class EvidenceAdapter:
    """
    解析 LLM triple 输出 + 同步屏障检查。

    构造注入：memory（读/写 QA 共享状态）

    在 send() 中：
    1. 解析 LLM 输出为 triple 或 INVALID
    2. 有效 triple → 更新 memory 中的 graph
    3. 递增 pending.received
    4. 若 received == total → 触发 Validation
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._memory = memory
        self._current_direction = None  # (subj, rel)，由 receive() 设置

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel

    async def receive(self) -> UserRequest:
        request = await self._kba.receive()
        meta = request.metadata
        if meta.get("direction"):
            self._current_direction = tuple(meta["direction"])
        return request

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            # 1. 解析 LLM 输出
            parsed = _parse_final(event.content)
            # parsed: "INVALID" | None | (subj, rel, obj, select_idx)

            state = self._memory.read("loop", "qa_state")
            graph = SubGraphManager.from_dict(state["graph"])

            if parsed and parsed != "INVALID":
                subj, rel, obj, select_idx = parsed
                child_id = graph.add_node(
                    triple_str=f"{subj} | {rel} | {obj}",
                    parent_id=state.get("_current_node_id"),
                    accumulated_passages=...,
                    select_idx=select_idx,
                    retrieved_passages=self._get_last_passages(),
                )
                state["graph"] = graph.to_dict()
                result = {"valid": True, "triple": (subj, rel, obj),
                          "node_id": child_id, "select_idx": select_idx}
            else:
                result = {"valid": False, "reason": "INVALID" if parsed == "INVALID" else "PARSE_ERROR"}

            # 3. 更新 pending
            state["pending"]["received"] += 1
            state["pending"]["results"].append(result)
            self._memory.write("loop", "qa_state", state)

            # 4. 同步屏障：所有 Evidence 任务完成 → 触发 Validation
            if state["pending"]["received"] >= state["pending"]["total"]:
                self._kernel.send_input("validation", UserRequest(
                    text="",
                    metadata={
                        "task": "validate_graph",
                        "question": state["question"],
                        "trigger": "evidence_complete",
                    }
                ))

        await self._kba.send(event, target)
```

#### EvidenceAssembler（ContextAssembler）

```python
class EvidenceAssembler:
    """
    强制检索 + 组装 topic_code 的 final system prompt + user content。

    ★ 构造注入 Retriever + MemoryBackend
    — 检索是必然执行的确定性代码，不是 Tool 选择。
    """

    def __init__(self, retriever: RetrieverStub, memory: MemoryBackend, top_k: int = 5):
        self._retriever = retriever
        self._memory = memory
        self._top_k = top_k
        self._last_passages = []  # 供 adapter 取用

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        meta = ctx.user_request.metadata
        subj, rel = meta["direction"]

        # 步骤 1：确定性检索（不由 LLM 决策）
        query = f"{subj} {rel}"
        passages = self._retriever.retrieve(
            query,
            corpus=meta["corpus"],
            top_k=self._top_k,
        )
        self._last_passages = passages

        # ★ 检索无结果 → 短路：直接标记 INVALID，不浪费 LLM 调用
        if not passages:
            # 通过 metadata 标记让 adapter 知道应该跳过 LLM
            ctx.user_request.metadata["_no_passages"] = True
            ctx.user_request.metadata["retrieved_passages"] = []
            return [
                Message(role="system", content="返回 INVALID"),
                Message(role="user", content="INVALID"),
            ]

        # 步骤 2：将 passages 注入 metadata（供 adapter 写入 graph）
        ctx.user_request.metadata["retrieved_passages"] = passages

        # 步骤 3：组装 final prompt
        system = CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_final_v3_user_content(
            question=meta["question"],
            confirmed_triples=meta.get("confirmed_triples", []),
            retrieved_passages=passages,
            draft_subject=subj,
            draft_relation=rel,
        )

        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

#### 职责边界

- ✅ 确定性检索（代码层强制执行）
- ✅ 基于 passages 做 triple 确认
- ✅ 更新 SubGraph（添加节点）
- ✅ 同步屏障检查（触发 Validation）
- ❌ 不做方向生成
- ❌ 不做全局校验
- ❌ 检索不走 Tool（不在 LLM 的决策空间内）

---

### 4.4 Validation Agent

#### DI 容器组成

```python
container = DIContainer()

# ★ MemoryBackend — 共享（读 graph/round/max_hops；写 round/expandable/answer）
memory = MdMemory(path=f"./memory/customer_service/shared")
container.register(MemoryBackend, memory)

# AsyncInputAdapter — 自定义：解析 KEEP/DISCARD + ANSWER + 终止/循环判断
# ★ 构造注入 memory
container.register(AsyncInputAdapter, ValidationAdapter(memory=memory))

# ContextAssembler — 自定义：组装 validator prompt
# ★ 构造注入 memory
container.register(ContextAssembler, ValidationAssembler(memory=memory))

# GuideProvider — 不需要
# SystemToolProvider — 默认
container.register(SystemToolProvider, DefaultSystemToolProvider())

# Sensor
container.register(Sensor, LoggingSensor(memory=memory))
```

#### Agent 角色

站在全图视角判断：每个节点是否可靠（KEEP/DISCARD）、当前证据是否足以回答问题（ANSWER）。实现 topic_code 的 `score_graph_with_raw()` 功能。

**Validation 是循环的"决策门"**——它决定终止还是继续，以及下一轮的 expandable 集合。

#### I/O 契约

| 方向 | 格式 | 说明 |
|---|---|---|
| **输入** | `UserRequest(metadata={task: "validate_graph", question, trigger})` | 来自 Evidence（或 Direction 空结果） |
| **输出（继续循环）** | `kernel.send_input("direction", UserRequest(metadata={expandable_nodes: [...]}))` | answer=None 且 round < max_hops |
| **输出（找到答案）** | `kernel.send_input("router", ...)` + `kernel.end_workflow()` | answer 非空 |
| **输出（无法回答）** | `kernel.send_input("router", ...)` + `kernel.end_workflow()` | round >= max_hops 或 expandable 为空 |

#### 终止条件（全部在 ValidationAdapter.send() 中判断）

```python
if answer is not None:
    # 条件 1：Validator 给出了明确答案 → 成功终止
    action = "answer_found"

elif current_round >= max_hops:
    # 条件 2：达到最大轮数 → 兜底终止
    answer = "抱歉，暂时无法回答这个问题。"
    action = "max_hops"

elif not expandable:
    # 条件 3：无 KEEP 节点 → 无更多探索方向
    answer = "抱歉，暂时无法回答这个问题。"
    action = "no_expandable"

elif new_node_count == 0:
    # 条件 4：本轮没有新 triple 加入 graph → 无进展终止
    # （对应 topic_code 的 had_fresh_candidates 检查）
    # 防止所有方向都已尝试时的空转循环
    answer = "抱歉，暂时无法回答这个问题。"
    action = "no_progress"

else:
    # 继续下一轮
    action = "continue"
```

#### ValidationAdapter（AsyncInputAdapter）

```python
class ValidationAdapter:
    """
    解析 LLM 输出（KEEP/DISCARD + ANSWER）+ 终止/循环判断。

    构造注入：memory（读/写 QA 共享状态）

    在 send() 中：
    1. 解析 LLM 输出
    2. 判断终止条件（answer / max_hops / expandable / no_progress）
    3. 终止：发回答给 Router + end_workflow
    4. 继续：发下一轮任务给 Direction
    """

    def __init__(self, memory: MemoryBackend):
        self._kba = None
        self._kernel = None
        self._runtime = None
        self._memory = memory

    def _inject_kernel_context(self, pid, kernel, runtime):
        self._kba = KernelBridgeAdapter(pid, kernel, runtime)
        self._kernel = kernel
        self._runtime = runtime

    async def receive(self) -> UserRequest:
        return await self._kba.receive()

    async def send(self, event, target=None):
        if isinstance(event, TextEvent):
            # 1. 读取状态
            state = self._memory.read("loop", "qa_state")

            # 2. 解析 LLM 输出
            graph = SubGraphManager.from_dict(state["graph"])
            prev_node_count = len([n for n in graph._graph.nodes if n != "ROOT"])
            id_map = graph.get_id_map()  # internal_id → display_id (N0, N1, ...)
            decisions = parse_validator_decisions(event.content, id_map)
            answer = parse_validator_answer(event.content)
            # decisions: {internal_id: 0|1}  (1=KEEP, 0=DISCARD)
            # answer: str | None

            # 3. 更新 scores
            graph.update_scores(decisions)
            state["graph"] = graph.to_dict()
            state["validator_scores"] = decisions
            state["validator_raw_output"] = event.content

            # 4. 确定 expandable + 计算本轮新增节点数
            expandable = [nid for nid, score in decisions.items() if score == 1]
            new_node_count = len([n for n in graph._graph.nodes if n != "ROOT"]) - prev_node_count

            # 5. 终止条件判断
            if answer is not None:
                # 成功：有答案
                state["phase"] = "done"
                state["answer"] = answer
                state["sources"] = graph.get_sources()
                self._memory.write("loop", "qa_state", state)

                # 发给 Router 格式化输出
                self._kernel.send_input("router", UserRequest(
                    text="",
                    metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": answer,
                        "sources": state["sources"],
                    }
                ))
                # 终止 workflow
                self._kernel.end_workflow(self._runtime.workflow_flag)

            elif state["round"] >= state["max_hops"]:
                # 兜底：超过最大轮数
                state["phase"] = "done"
                state["answer"] = "抱歉，暂时无法回答这个问题，请咨询人工客服。"
                self._memory.write("loop", "qa_state", state)
                self._kernel.send_input("router", UserRequest(
                    text="", metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": state["answer"],
                        "sources": [],
                    }
                ))
                self._kernel.end_workflow(self._runtime.workflow_flag)

            elif not expandable:
                # 无更多探索方向
                state["phase"] = "done"
                state["answer"] = "抱歉，暂时无法回答这个问题，请咨询人工客服。"
                self._memory.write("loop", "qa_state", state)
                self._kernel.send_input("router", UserRequest(
                    text="", metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": state["answer"],
                        "sources": [],
                    }
                ))
                self._kernel.end_workflow(self._runtime.workflow_flag)

            elif new_node_count == 0:
                # ★ 本轮无新 triple 加入 graph → 无进展终止
                # 对应 topic_code 的 had_fresh_candidates 检查
                state["phase"] = "done"
                state["answer"] = "抱歉，暂时无法回答这个问题，请咨询人工客服。"
                self._memory.write("loop", "qa_state", state)
                self._kernel.send_input("router", UserRequest(
                    text="", metadata={
                        "type": "qa_answer",
                        "question": state["question"],
                        "answer": state["answer"],
                        "sources": [],
                    }
                ))
                self._kernel.end_workflow(self._runtime.workflow_flag)

            else:
                # 继续下一轮：更新状态并发回 Direction
                state["round"] += 1
                state["expandable"] = expandable
                state["phase"] = "direction"
                state["pending"] = {"total": 0, "received": 0, "results": []}
                self._memory.write("loop", "qa_state", state)

                # 为下一轮构造 Direction 任务
                expandable_nodes = []
                for nid in expandable:
                    expandable_nodes.append({
                        "node_id": nid,
                        "confirmed_triples": graph.get_path_triples(nid),
                        "evidence_passages": graph.get_accumulated_passages(nid),
                    })

                self._kernel.send_input("direction", UserRequest(
                    text="",
                    metadata={
                        "task": "generate_directions",
                        "question": state["question"],
                        "expandable_nodes": expandable_nodes,
                        "K": state.get("K", 2),
                    }
                ))

        # 始终委托给 KBA（前端可见 KEEP/DISCARD + ANSWER）
        await self._kba.send(event, target)
```

#### ValidationAssembler（ContextAssembler）

```python
class ValidationAssembler:
    """
    组装 topic_code 的 validator system prompt + user content。

    构造注入：memory（读 graph state）

    使用从 topic_code 提取的：
    - CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY (system)
    - build_core_validator_content_from_merger() (user content builder)

    ★ Validator 只接收 triple 图结构，不接收原始 passages。
      这是 topic_code 的核心设计——强制模型依赖结构而非编造。
    """

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    def assemble(self, ctx: AssemblyContext) -> List[Message]:
        state = self._memory.read("loop", "qa_state")
        graph = SubGraphManager.from_dict(state["graph"])

        system = CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY
        user = build_core_validator_content_from_merger(
            question=state["question"],
            merger=graph,
        )

        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
```

#### 职责边界

- ✅ 全局图校验（KEEP/DISCARD）
- ✅ 答案提取（ANSWER）
- ✅ 终止条件判断（answer / max_hops / expandable）
- ✅ 下一轮 expandable 确定
- ✅ 驱动 workflow 终止（end_workflow）
- ❌ 不做方向生成
- ❌ 不做检索
- ❌ 不接收原始 passages（只看 triple 图）

---

### 4.5 Task Agent（占位）

#### DI 容器组成

```python
container = DIContainer()
container.register(AsyncInputAdapter, ...)  # 默认 KBA
container.register(ContextAssembler, TaskAssembler())
container.register(GuideProvider, FileGuideProvider(paths=["AGENTS.md"]))
container.register(SystemToolProvider, DefaultSystemToolProvider())
container.register(MemoryBackend, MdMemory(path="./memory/customer_service/task"))
container.register(Sensor, LoggingSensor(memory=memory))
```

#### Agent 角色

承接 `intent=task` 的业务办理请求。**本期仅做最小占位**：确认用户意图、请求必要信息（如订单号）。

#### I/O 契约

| 方向 | 格式 |
|---|---|
| **输入** | 订阅 Router 的 TextEvent，从内容中提取 intent |
| **输出** | TextEvent → 前端可见的确认话术 |

---

### 4.6 Fallback Agent（占位）

#### DI 容器组成

同 Task Agent，使用 `FallbackAssembler` + `FileGuideProvider`。

#### Agent 角色

承接 `intent=fallback` 的异常/低置信度请求。**本期仅做最小占位**：输出标准兜底话术 + 转人工建议。

---

## 5. 完整流程追踪

以下以用户问"改签规则是什么？"为例，追踪一个完整的 QA 循环（Round 1 产生答案）。

### Phase 0：启动

```
Runtime(console).run_from_script("customer_service_workflow.py")
  → Kernel.spawn_from_script()
    → 创建 6 个 AgentRuntime
    → 注册订阅关系
    → 注入 entry_prompt
    → 启动所有 Agent 的 asyncio Task
```

### Phase 1：用户输入 → Router

```
[终端] /talk router 改签规则是什么？

CliConsole.receive()
  → CommandTalkDirect(pid="router", text="改签规则是什么？")

Kernel._handle_system_input()
  → kernel.send_input("router", UserRequest(text="改签规则是什么？"))

Router's AgentRuntime loop:
  adapter.receive() → UserRequest(text="改签规则是什么？")

  _phase_loop():
    ContextAssembler(RouterAssembler).assemble():
      → [system: 意图分类 prompt, user: "改签规则是什么？"]

    LLM 调用:
      → "INTENT: qa\nCONFIDENCE: 0.94\nSLOTS: {}"

    adapter.send(TextEvent(content="INTENT: qa\nCONFIDENCE: 0.94\nSLOTS: {}"))

    RouterAdapter.send():
      1. _parse_intent() → {"intent": "qa", "confidence": 0.94}
      2. intent == "qa" →
         a. memory.write("loop", "qa_state", {初始化状态...})
         b. kernel.send_input("direction", UserRequest(metadata={
              task: "generate_directions",
              expandable_nodes: [{node_id: "ROOT", ...}]
            }))
      3. kba.send(TextEvent) → [router] INTENT: qa... (终端可见)
```

### Phase 2：Direction 生成候选方向

```
Direction's AgentRuntime loop:
  adapter.receive() → UserRequest(metadata={task, expandable_nodes: [1项]})

  DirectionAdapter.receive():
    1. 存储 _pending_nodes = [node_info]
    2. 弹出第一个节点
    3. 返回单节点 UserRequest

  _phase_loop():
    ContextAssembler(DirectionAssembler).assemble():
      → [system: CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY,
         user: build_core_draft_v3_user_content(
           question="改签规则是什么？",
           evidence_passages=[],
           confirmed_triples=[],
           K=2
         )]

    LLM 调用 (temperature=0.7):
      → "<remaining_question>改签需要满足什么条件？</remaining_question>
         <next_facts>
         1. 航班 | 改签规则 | ?
         2. 乘客 | 适用条件 | ?
         </next_facts>"

    adapter.send(TextEvent(content=...))

    DirectionAdapter.send():
      1. parse_draft_v3_output(content) → ("改签需要满足什么条件？",
         [("航班", "改签规则"), ("乘客", "适用条件")])
      2. 读 memory → tried_candidates[ROOT] = []
      3. 全部 fresh → 构造 2 个 Evidence 任务(暂存)
      4. 更新 tried_candidates
      5. _pending_nodes 为空 →
         a. pending.total = 2, pending.received = 0
         b. kernel.send_input("evidence", task1)
         c. kernel.send_input("evidence", task2)
      6. kba.send(TextEvent) → [direction] ... (终端可见)
```

### Phase 3：Evidence 确认 triple（任务 1）

```
Evidence's AgentRuntime loop:
  adapter.receive() → UserRequest(metadata={
    task: "confirm_triple",
    direction: ("航班", "改签规则"),
    confirmed_triples: [],
    corpus: [...],
    node_id: "ROOT"
  })

  _phase_loop():
    ContextAssembler(EvidenceAssembler).assemble():
      步骤 1: query = "航班 改签规则"
      步骤 2: passages = self._retriever.retrieve(query, corpus, top_k=5)
         → ["第3条：旅客可在起飞前2小时...", "第7条：改签需支付..."]
      步骤 3: 注入 metadata["retrieved_passages"] = passages
      步骤 4: 组装 final prompt
        → [system: CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY,
           user: build_core_final_v3_user_content(...)]

    LLM 调用 (temperature=0.3):
      → "航班 | 改签规则 | 起飞前2小时 | SELECT: 1"

    adapter.send(TextEvent(content="航班 | 改签规则 | 起飞前2小时 | SELECT: 1"))

    EvidenceAdapter.send():
      1. _parse_final(content) → ("航班", "改签规则", "起飞前2小时", 1)
      2. 读 memory → graph
      3. graph.add_node("航班 | 改签规则 | 起飞前2小时", parent="ROOT", ...)
         → child_id = "abc123"
      4. pending.received = 1, pending.results = [{valid: True, node_id: "abc123"}]
      5. memory.write(...)
      6. pending.received(1) < pending.total(2) → 不触发 Validation
      7. kba.send(TextEvent) → [evidence] ... (终端可见)
```

### Phase 4：Evidence 确认 triple（任务 2）

```
Evidence's AgentRuntime loop:
  ...同上...

  LLM 调用:
    → "乘客 | 适用条件 | 非特价舱位 | SELECT: 0"

  EvidenceAdapter.send():
    1. pending.received = 2, pending.total = 2
    2. ★ pending.received == pending.total → 触发 Validation！
    3. kernel.send_input("validation", UserRequest(metadata={
         task: "validate_graph",
         question: "改签规则是什么？"
       }))
```

### Phase 5：Validation 全局校验 → 找到答案

```
Validation's AgentRuntime loop:
  adapter.receive() → UserRequest(metadata={task: "validate_graph"})

  _phase_loop():
    ContextAssembler(ValidationAssembler).assemble():
      → 读 memory → graph (含 N1, N2 两个节点)
      → system: CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY
      → user: "Question: 改签规则是什么？
                Graph (2 nodes):
                [N0] 航班 | 改签规则 | 起飞前2小时
                [N1] 乘客 | 适用条件 | 非特价舱位"

    LLM 调用 (temperature=0.0):
      → "<structure>: 两节点构成因果链...
         <semantic>: 合理...
         <comprehensive>: 完整...
         <rethink>: 无需补充...
         Final decision logic: 两个节点相互支持...

         Node N0: KEEP
         Node N1: KEEP

         ANSWER: 非特价舱位乘客可在起飞前2小时申请改签"

    adapter.send(TextEvent(content=...))

    ValidationAdapter.send():
      1. parse_validator_decisions(content, id_map)
         → {"abc123": 1, "def456": 1}  (都是 KEEP)
      2. parse_validator_answer(content)
         → "非特价舱位乘客可在起飞前2小时申请改签"
      3. answer is NOT None →
         a. memory.write(answer="非特价...", phase="done")
         b. kernel.send_input("router", UserRequest(metadata={
              type: "qa_answer",
              answer: "非特价舱位乘客可在起飞前2小时申请改签",
              sources: ["第3条：旅客可在起飞前2小时...", "第5条：非特价舱位..."]
            }))
         c. kernel.end_workflow("wf_001")
      4. kba.send(TextEvent) → [validation] KEEP/KEEP/ANSWER (终端可见)
```

### Phase 6：Router 格式化最终答案

```
Router's AgentRuntime loop:
  adapter.receive() → UserRequest(metadata={
    type: "qa_answer",
    answer: "非特价舱位乘客可在起飞前2小时申请改签",
    sources: [...]
  })

  _phase_loop():
    ContextAssembler(RouterAssembler).assemble():
      → 识别 type="qa_answer" → 组装答案格式化 prompt

    LLM 调用:
      → "根据查询结果，非特价舱位乘客可在起飞前2小时申请改签。

         参考来源：
         - 第3条：旅客可在起飞前2小时申请改签服务
         - 第5条：非特价舱位旅客适用本规则"

    adapter.send(TextEvent(content=...))
      → [router] 根据查询结果... (终端可见，用户看到答案)

  → 下一轮 receive() 收到 EXIT_SENTINEL
  → should_exit = True
  → AgentRuntime 进入 finally → FINISHED
```

### Phase 7：终止

```
kernel.end_workflow("wf_001"):
  → router.should_exit = True (已处理完答案)
  → direction.should_exit = True (在 receive() 中阻塞)
  → evidence.should_exit = True (在 receive() 中阻塞)
  → validation.should_exit = True (已在 send() 后退出)
  → task_agent.should_exit = True
  → fallback.should_exit = True

所有 Agent FINISHED → Runtime 推送 WorkflowFinished → 终端显示 "[系统] Workflow wf_001 完成"
```

---

## 6. SubGraphManager 设计

基于 `networkx.DiGraph` 重新实现，提供与 `topic_code.SubGraphMerger` 等价的功能。

```python
import networkx as nx
from uuid import uuid4


class SubGraphManager:
    """
    多跳推理状态图。

    节点属性：
    - triple_str: "subj | rel | obj"，ROOT 节点为 "ROOT"
    - accumulated_passages: str
    - select_idx: int | None
    - retrieved_passages: list[str]
    - creation_order: int
    - score: int (0=DISCARD, 1=KEEP, -1=未评分)
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        self._counter = 0
        # 创建 ROOT 节点
        self._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)

    def add_node(
        self,
        triple_str: str,
        parent_id: str | None = None,
        accumulated_passages: str | None = None,
        select_idx: int | None = None,
        retrieved_passages: list[str] | None = None,
    ) -> str:
        node_id = uuid4().hex[:12]
        self._counter += 1
        self._graph.add_node(
            node_id,
            triple_str=triple_str,
            accumulated_passages=accumulated_passages,
            select_idx=select_idx,
            retrieved_passages=retrieved_passages or [],
            creation_order=self._counter,
            score=-1,
        )
        if parent_id:
            self._graph.add_edge(parent_id, node_id)
        return node_id

    def get_path_triples(self, node_id: str) -> list[str]:
        """从 ROOT（不含）到指定节点（含）的 triple 链"""
        triples = []
        for ancestor in nx.ancestors(self._graph, node_id):
            if ancestor != "ROOT":
                triples.append(self._graph.nodes[ancestor]["triple_str"])
        if node_id != "ROOT":
            triples.append(self._graph.nodes[node_id]["triple_str"])
        return triples

    def get_leaf_nodes(self) -> list[str]:
        return [n for n in self._graph.nodes
                if n != "ROOT" and self._graph.out_degree(n) == 0]

    def get_accumulated_passages(self, node_id: str) -> str:
        """从 ROOT 到指定节点路径上所有 passages 的拼接"""
        passages = []
        # 简化实现：沿路径收集
        current = node_id
        while current is not None and current != "ROOT":
            ap = self._graph.nodes[current].get("accumulated_passages", "")
            if ap:
                passages.append(ap)
            preds = list(self._graph.predecessors(current))
            current = preds[0] if preds else None
        return "\n".join(reversed(passages))

    def update_scores(self, decisions: dict[str, int]):
        """decisions: {internal_id: 0|1}"""
        for nid, score in decisions.items():
            if nid in self._graph.nodes:
                self._graph.nodes[nid]["score"] = score

    def get_id_map(self) -> dict[str, str]:
        """internal_id → display_id (N0, N1, ...)"""
        non_root = [n for n in self._graph.nodes if n != "ROOT"]
        non_root.sort(key=lambda n: self._graph.nodes[n].get("creation_order", 0))
        return {nid: f"N{i}" for i, nid in enumerate(non_root)}

    def get_sources(self) -> list[str]:
        """提取所有 KEEP 节点的来源段落"""
        sources = []
        for nid in self._graph.nodes:
            if nid != "ROOT" and self._graph.nodes[nid].get("score") == 1:
                triple = self._graph.nodes[nid]["triple_str"]
                passages = self._graph.nodes[nid].get("retrieved_passages", [])
                idx = self._graph.nodes[nid].get("select_idx")
                if idx is not None and idx < len(passages):
                    sources.append(f"[{triple}] {passages[idx]}")
        return sources

    def to_dict(self) -> dict:
        return nx.node_link_data(self._graph, edges="edges")

    @classmethod
    def from_dict(cls, data: dict) -> "SubGraphManager":
        instance = cls()
        instance._graph = nx.node_link_graph(data, edges="edges")
        if "ROOT" not in instance._graph.nodes:
            instance._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)
        instance._counter = len([n for n in instance._graph.nodes if n != "ROOT"])
        return instance
```

---

## 7. Retriever 设计

极简实现，遵循 topic_code 的 `RetrieverStub` 接口。

```python
class RetrieverStub:
    """检索器抽象接口"""
    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        ...


class InMemoryRetriever(RetrieverStub):
    """
    简单关键词匹配检索器（MVP 版本）。

    后续可替换为 BM25Retriever（rank_bm25）或 DenseRetriever（sentence_transformers）。
    """

    def __init__(self, corpus: list[tuple[str, list[str]]]):
        """
        Args:
            corpus: [(title, [sentence_1, sentence_2, ...])]
        """
        self._flattened = []
        for title, sentences in corpus:
            for s in sentences:
                self._flattened.append(f"[{title}] {s}")

    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        # 简化：按 query 词重叠数排序
        query_terms = set(query.lower().split())
        scored = []
        for doc in self._flattened:
            doc_terms = set(doc.lower().split())
            score = len(query_terms & doc_terms)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]
```

---

## 8. Workflow 脚本结构

```python
# agents/customer-service/customer_service_workflow.py

from harness.runtime.decorators import agent, subscribe


@agent(
    "router",
    entry_prompt="你是客服系统入口路由。等待用户消息...",
    metadata={"role": "入口意图识别"},
)
def assemble_router():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, RouterAdapter(memory=memory))
    container.register(ContextAssembler, RouterAssembler())
    container.register(GuideProvider, FileGuideProvider(paths=["AGENTS_router.md"]))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())  # placeholder
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "direction",
    entry_prompt="你是方向生成Agent。等待任务...",
    metadata={"role": "方向生成"},
)
def assemble_direction():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, DirectionAdapter(memory=memory))
    container.register(ContextAssembler, DirectionAssembler(K=2))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "evidence",
    entry_prompt="你是证据锚定Agent。等待任务...",
    metadata={"role": "证据锚定"},
)
def assemble_evidence():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    corpus = load_corpus("data/corpus.json")
    retriever = InMemoryRetriever(corpus)
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, EvidenceAdapter(memory=memory))
    container.register(ContextAssembler, EvidenceAssembler(
        retriever=retriever, memory=memory, top_k=5
    ))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "validation",
    entry_prompt="你是全局校验Agent。等待任务...",
    metadata={"role": "全局校验"},
)
def assemble_validation():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, ValidationAdapter(memory=memory))
    container.register(ContextAssembler, ValidationAssembler(memory=memory))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "task_agent",
    entry_prompt="你是业务办理助手。等待任务...",
    metadata={"role": "业务办理占位"},
)
def assemble_task():
    ...


@agent(
    "fallback",
    entry_prompt="你是异常兜底助手。等待任务...",
    metadata={"role": "异常兜底占位"},
)
def assemble_fallback():
    ...


# —— 订阅关系 ——
subscribe("router").to("user")
subscribe("task_agent").to("router")
subscribe("fallback").to("router")

# ★ 虚拟订阅：强制 direction/evidence/validation 为 continuous mode
# 框架根据 has_subscriptions 自动决定 mode，这三个 agent 走 kernel.send_input()
# 直投而非 pub-sub，无订阅会被设为 oneshot（一轮后退出）。
# 订阅 "user" 不产生实际消息流——user 是虚拟 publisher，不会向它们推送。
subscribe("direction").to("user")
subscribe("evidence").to("user")
subscribe("validation").to("user")
```

**启动方式**：

```bash
python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
```

终端输入 `/talk router 改签规则是什么？` 即可触发完整 QA 流程。

---

## 9. 与 topic_code 对齐表

| topic_code | 本设计 | 对齐说明 |
|---|---|---|
| `CoreController.run()` 主循环 | Direction → Evidence → Validation → Direction 消息闭环 | 循环由 agent 间消息传递自然驱动，不由中心控制器 |
| `CoreController.__init__(K, max_hops, top_k_retrieve, ...)` | memory.loop 中的配置字段 | 参数写入共享状态 |
| `Generator.draft_generate_list_v3()` | Direction Agent (DirectionAssembler + LLM) | 相同的 system prompt + user content builder |
| `Generator.final_generate_v3()` | Evidence Agent (EvidenceAssembler + LLM) | 相同的 system prompt + user content builder |
| `Retriever.retrieve()` | EvidenceAssembler 内部确定性代码 | 构造注入，不在 LLM 决策空间 |
| `Validator.score_graph_with_raw()` | Validation Agent (ValidationAssembler + LLM) | 相同的 system prompt + user content builder |
| `parse_draft_v3_output()` | DirectionAdapter.send() | 解析 LLM 输出得到 candidates |
| `_parse_final()` | EvidenceAdapter.send() | 解析 LLM 输出得到 triple/INVALID |
| `parse_validator_decisions()` + `parse_validator_answer()` | ValidationAdapter.send() | 解析 LLM 输出得到 scores + answer |
| `SubGraphMerger` | `SubGraphManager` (networkx) | 等价 API，支持 to_dict/from_dict |
| `tried_candidates` | memory.loop.tried_candidates | 相同机制，以 node_id 为 key |
| `pruning_policy` / `answer_selector` | ValidationAdapter.send() 中的简单逻辑 | MVP 不引入独立 Pruner/Selector |
| `configs/core_api_v2.yaml` | workflow 脚本中的 LLM 参数 | draft_temperature=0.7, final_temperature=0.3, validator_temperature=0.0 |
| 数据集推理入口 `scripts/run_core.py` | 终端 `/talk router` | 单条问题交互式输入 |

---

## 10. 关键设计决策汇总

| 决策 | 结论 | 理由 |
|---|---|---|
| 循环由谁驱动 | Direction→Evidence→Validation 消息闭环，无中心 Coordinator | topic_code 的 CoreController 本身就是纯代码循环，不是"管理 Agent" |
| 循环状态存在哪 | MemoryBackend namespace="qa_state" key="loop" | 三个 Agent 共享读写，MemoryBackend 是同进程内字典，零开销 |
| 同步屏障 | pending.total/received 计数器，在 EvidenceAdapter.send() 中检查 | 简单可靠，无需分布式协调 |
| 多节点处理 | Direction 逐节点自触发，任务积累后一次性发送 | 保证 pending.total 在 Evidence 开始处理前已确定 |
| 检索器放哪 | EvidenceAssembler 构造注入 | 确定性逻辑走代码，不确定性决策走 LLM — 检索没有"是否执行"的选择空间 |
| Router 的 LLM 做什么 | 只做意图分类 + 最终答案格式化 | 不做对话、不做推理，职责单一 |
| Worker Agent 的 LLM 输出格式 | 机器可解析的结构化文本（subj\|rel\|obj 等） | 由 Adapter 解析后路由，前端看到的是 TextEvent 的人类可读版本 |
| 终止信号 | Validation 调用 kernel.end_workflow() | 级联终止所有 Agent，Router 在收到 EXIT_SENTINEL 前已处理完答案 |
| 前端可见性 | 所有 Agent 的 TextEvent 通过 KBA → AgentOutput 自动推送终端 | 不改 Runtime，利用现有机制 |

---

*本文档为 customer-service Agent 的详细设计，与 [topic_code 实现参考](./2026-07-12-topic-code-implementation-reference.md) 和 [集成高层设计](./2026-07-12-customer-service-agent-integration-design.md) 配套使用。下一步将基于本文档输出实施计划。*
