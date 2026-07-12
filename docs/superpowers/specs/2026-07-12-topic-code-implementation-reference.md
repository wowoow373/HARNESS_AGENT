# topic_code 验证式多跳问答实现细节参考

**日期**: 2026-07-12  
**目标**: 为 `harness_agent` 集成提供 `topic_code` 的精确实现说明、输入输出格式与代码路径  
**核心复用策略**: **不直接导入 `topic_code` 的包或调用其 `CoreController` 等类**，而是提取其经过验证的 prompt 模板、解析逻辑与控制流程，在 `agents/customer-service/` 内重新实现  
**目标读者**: 负责集成落地的工程师

---

## 1. 整体架构

`topic_code` 的核心是多跳问答推理系统，采用 **Generator-Validator 双角色迭代架构**：

```
用户问题 + 文档 corpus
        ↓
┌─────────────────────────────────────────────┐
│           CoreController.run()               │
│  循环：expand → validate → expand → ...      │
│                                              │
│  Expand 阶段：                               │
│    1. Generator.draft_generate_list_v3()    │
│       → 输出候选方向 [(subj, rel)]           │
│    2. Retriever.retrieve()                  │
│       → 输出 Top-K passages                 │
│    3. Generator.final_generate_v3()         │
│       → 输出 triple (subj|rel|obj|SELECT:idx)│
│       → 或 INVALID                          │
│                                              │
│  Validate 阶段：                             │
│    4. Validator.score_graph_with_raw()      │
│       → 输出 KEEP/DISCARD + ANSWER          │
│    5. Pruning / AnswerSelection             │
│       → 确定下一轮 expandable 节点           │
└─────────────────────────────────────────────┘
        ↓
最终答案 / 排名路径
```

核心设计哲学：**Generator 负责“提出假设”，Validator 负责“批判验证”**。Validator 只基于 triple 图做判断，看不到原始 passages，从而强制模型依赖结构而非编造。

---

## 2. 复用策略说明（重要）

### 2.1 不直接依赖 `topic_code` 包

`topic_code` 是一个学术研究代码库，其模块结构、依赖（如 `unsloth`）、导入路径都与 `harness_agent` 不完全兼容。因此 **customer-service Agent 不直接 `import topic_code` 或调用 `CoreController` / `APIGeneratorEngine` / `APIValidatorEngine` 等类**。

### 2.2 提取什么

从 `topic_code` 中提取并保留以下资产：

| 资产类型 | 具体项 | 用途 |
|---|---|---|
| **System Prompt** | `CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY` | Direction Agent 的 system prompt |
| **System Prompt** | `CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY` | Evidence Agent 的 system prompt |
| **System Prompt** | `CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY` | Validation Agent 的 system prompt |
| **User Content Builder** | `build_core_draft_v3_user_content()` | 构造 Direction Agent 输入 |
| **User Content Builder** | `build_core_final_v3_user_content()` | 构造 Evidence Agent 输入 |
| **User Content Builder** | `build_core_validator_content_from_merger()` | 构造 Validation Agent 输入 |
| **Parser** | `parse_draft_v3_output()` / `parse_draft_list()` | 解析方向生成输出 |
| **Parser** | `_parse_final()` | 解析 triple 确认输出 |
| **Parser** | `parse_validator_decisions()` / `parse_validator_answer()` | 解析校验输出 |
| **控制流程** | `CoreController.run()` 主循环 | 参考其 expand → validate 迭代逻辑 |
| **数据结构** | `SubGraphMerger` 设计 | 参考其节点/边/路径抽象，可用 networkx 重新实现 |

### 2.3 重新实现什么

在 `agents/customer-service/` 中重新实现：

- `Direction Agent`：调用 LLM API，使用提取的 draft prompt 和 parser。
- `Evidence Agent`：内部固定调用检索器 + LLM API，使用提取的 final prompt 和 parser。
- `Validation Agent`：调用 LLM API，使用提取的 validator prompt 和 parser。
- `SubGraphManager`：基于 `networkx.DiGraph` 重新实现，支持序列化到 MemoryBackend。
- `QA Workflow`：重新实现 expand → validate 循环，调度三个 Agent。

### 2.4 为什么这样设计

1. **避免依赖耦合**：`topic_code` 的 `src/` 不是稳定包，直接导入会引入环境、路径、依赖问题。
2. **保持 Harness 风格**：新代码完全使用 Harness 的 Agent、MemoryBackend、ContextAssembler、InputAdapter 等抽象。
3. **便于改造**：重新实现后，prompt 可以逐步改写为客服领域 wording，而不受 `topic_code` 原始代码约束。
4. **面试叙事清晰**：可以明确说“我参考了验证式问答的研究 insight，在 Runtime 上重新实现了产品化版本”。

### 2.1 主入口

| 文件 | 作用 |
|---|---|
| `/home/wowoow/topic_code/scripts/run_core.py` | Phase 4 主推理入口，读取配置、加载模型、跑数据集 |
| `/home/wowoow/topic_code/scripts/run_inference.py` | Phase 3 推理入口（Generator only，无 Validator） |
| `/home/wowoow/topic_code/scripts/eval_core.py` | 端到端评估入口 |

### 2.2 推荐配置（API 模式，无 GPU）

`/home/wowoow/topic_code/configs/core_api_v2.yaml`

```yaml
generator:
  type: "api"
  model: ""              # 从 .env 读取
  api_key: ""            # 从 .env 读取
  base_url: null
  draft_temperature: 0.7
  draft_max_tokens: 1024
  final_temperature: 0.3
  final_max_tokens: 1024

validator:
  type: "api"
  model: ""
  api_key: ""
  base_url: null
  temperature: 0.0
  max_tokens: 4096

core:
  K: 2                   # 每轮每个节点最多生成几个方向
  max_hops: 4            # 最大迭代轮数
  top_k_retrieve: 5      # 每方向检索 passage 数
  pruning_policy: "noop"
  answer_selector: "rank_all"

retriever:
  type: "dense"
  model: "BAAI/bge-small-en-v1.5"

io:
  dataset: "data/raw/2WikiMultihopQA/2WikiMultihopQA/dev.json"
  output_dir: "outputs/core_api_v2"
  limit: 20
```

### 2.3 环境变量

`/home/wowoow/topic_code/.env` 示例：

```bash
LLM_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

---

## 3. CoreController：主循环控制器

### 3.1 位置

`/home/wowoow/topic_code/src/core/controller.py`

### 3.2 初始化签名

```python
class CoreController:
    def __init__(
        self,
        generator,                          # APIGeneratorEngine 或 GeneratorEngine
        validator: BaseValidatorEngine,     # APIValidatorEngine 或 ValidatorEngine
        retriever: RetrieverStub,           # BM25Retriever 或 DenseRetriever
        K: int = 2,
        max_hops: int = 4,
        top_k_retrieve: int = 5,
        pruning_policy: PruningPolicy | None = None,
        answer_selector: AnswerSelector | None = None,
        draft_temperature: float = 0.7,
        draft_max_new_tokens: int = 128,
        final_temperature: float = 0.3,
        final_max_new_tokens: int = 128,
    )
```

### 3.3 主入口 `run()`

```python
def run(self, question: str, context: list) -> dict
```

**输入参数：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | `str` | 原始多跳问题 |
| `context` | `list[tuple[str, list[str]]]` | 文档列表，每个元素为 `(title, sentences)` |

**context 示例：**

```python
[
    ("Article A", ["Passage 1...", "Passage 2..."]),
    ("Article B", ["Passage 3..."]),
]
```

**内部处理：** context 被展平为 `corpus: list[str]`，每句一个元素。

**返回值：**

```python
{
    "question": str,                     # 原始问题
    "predicted_subgraph": dict,          # 合并后的图结构 {nodes, edges}
    "inference_trace": list[dict],       # 每轮推理详细记录
    "paths": list[dict],                 # 排名后的路径
    "answer": str | None,                # Validator 给出的最终答案
    "validator_scores": dict,            # 最终节点得分 {node_id: 0|1}
    "validator_raw_output": str,         # 最终 Validator 原始输出
    "format_check": dict,                # 格式检查结果
    "num_rounds": int,                   # 实际执行轮数
    "total_nodes": int,                  # 图中总节点数
    "terminal_nodes": list,              # 占位字段
}
```

### 3.4 主循环详细流程

```python
for round_idx in range(self.max_hops):
    if not expandable:
        break

    # ===== Phase 1: Expand =====
    for node_id in expandable:
        bn = beam_node_from_merger(node_id, merger)

        # 1. Direction generation
        remaining_question, draft_candidates = self.generator.draft_generate_list_v3(
            question,
            evidence_passages=evidence_passages,      # 从 bn.accumulated_passages 解析
            confirmed_triples=bn.confirmed_triples,   # 从 ROOT 到当前节点的 triple 链
            K=self.K,
        )

        # 2. Filter tried candidates
        fresh_candidates = [
            (subj, rel) for subj, rel in draft_candidates
            if (subj.lower(), rel.lower()) not in tried_candidates.get(node_id, set())
        ]

        for subj, rel in fresh_candidates:
            # 3. Retrieve
            retrieval_query = f"{subj} {rel}"
            top_passages = self.retriever.retrieve(
                retrieval_query, corpus, self.top_k_retrieve
            )

            # 4. Final / triple confirmation
            final_raw = self.generator.final_generate_v3(
                question,
                confirmed_triples=bn.confirmed_triples,
                retrieved_passages=top_passages,
                draft_subject=subj,
                draft_relation=rel,
            )

            parsed = _parse_final(final_raw)
            # parsed 可能为：
            #   - "INVALID"：方向不成立
            #   - None：格式错误
            #   - (subj_out, rel_out, obj, select_idx)：有效 triple

            if parsed is a valid triple:
                child_id = merger.add_node(
                    triple_str=f"{subj_out} | {rel_out} | {obj}",
                    parent_id=node_id,
                    accumulated_passages=new_acc,
                    select_idx=select_idx,
                    retrieved_passages=top_passages,
                )

    # ===== Phase 2: Validate =====
    if not had_fresh_candidates:
        break

    scores, raw_output = self.validator.score_graph_with_raw(question, merger)

    # 检查是否已能回答
    answer = parse_validator_answer(raw_output)
    if answer is not None:
        final_answer = answer
        break

    # 下一轮 expandable = KEEP 节点
    expandable = {nid for nid, score in scores.items() if score == 1}
    if not expandable:
        break
```

### 3.5 终止条件

1. `max_hops` 达到上限（默认 4）。
2. Validator 输出 `ANSWER: <answer>`。
3. 本轮没有新的候选方向（`had_fresh_candidates == False`）。
4. 所有节点被 Validator 判为 `DISCARD`。

---

## 4. Generator：方向生成与三元组确认

### 4.1 位置

- API 版本：`/home/wowoow/topic_code/src/generator/api_engine.py`
- 本地版本：`/home/wowoow/topic_code/src/generator/engine.py`

### 4.2 APIGeneratorEngine 初始化

```python
class APIGeneratorEngine:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        draft_temperature: float = 0.7,
        draft_max_tokens: int = 256,
        final_temperature: float = 0.3,
        final_max_tokens: int = 128,
    )
```

### 4.3 方向生成：`draft_generate_list_v3()`

**签名：**

```python
def draft_generate_list_v3(
    self,
    question: str,
    evidence_passages: list[str],
    confirmed_triples: list[str],
    K: int,
) -> tuple[str | None, list[tuple[str, str]]]
```

**输入：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | `str` | 原始多跳问题 |
| `evidence_passages` | `list[str]` | 当前推理链已选中的证据段落 |
| `confirmed_triples` | `list[str]` | 已确认事实链，每条为 `subj | rel | obj` |
| `K` | `int` | 最多生成候选方向数 |

**输出：**

```python
(
    remaining_question: str | None,   # 基于已确认事实改写的剩余子问题
    candidates: list[tuple[str, str]] # [(subj, rel)]
)
```

**使用 Prompt：**

- System: `CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY`（`/home/wowoow/topic_code/src/prompts.py:516`）
- User builder: `build_core_draft_v3_user_content()`（`/home/wowoow/topic_code/src/prompts.py:547`）
- Parser: `parse_draft_v3_output()`（`/home/wowoow/topic_code/src/prompts.py:574`）

**期望输出格式：**

```xml
<remaining_question>
改写后的子问题
</remaining_question>

<next_facts>
1. subject | relation | ?
2. subject | relation | ?
</next_facts>
```

### 4.4 三元组确认：`final_generate_v3()`

**签名：**

```python
def final_generate_v3(
    self,
    question: str,
    confirmed_triples: list[str],
    retrieved_passages: list[str],
    draft_subject: str = "",
    draft_relation: str = "",
) -> str
```

**输入：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | `str` | 原始问题 |
| `confirmed_triples` | `list[str]` | 已确认事实链 |
| `retrieved_passages` | `list[str]` | 检索到的候选段落 |
| `draft_subject` | `str` | 候选方向 subject |
| `draft_relation` | `str` | 候选方向 relation |

**输出：** 原始字符串，需用 `_parse_final()` 解析。

**解析结果：**

- `"INVALID"`：方向不成立
- `None`：格式错误
- `(subj, rel, obj, select_idx)`：有效 triple

**使用 Prompt：**

- System: `CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY`（`/home/wowoow/topic_code/src/prompts.py:615`）
- User builder: `build_core_final_v3_user_content()`
- Parser: `_parse_final()`（`/home/wowoow/topic_code/src/core/controller.py:39`）

**期望输出格式：**

```text
subject | relation | object | SELECT: idx
```

或：

```text
INVALID
```

---

## 5. Retriever：证据检索

### 5.1 位置

`/home/wowoow/topic_code/src/retriever/stub.py`

### 5.2 抽象接口

```python
class RetrieverStub:
    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]: ...
```

### 5.3 实现类

| 类 | 说明 |
|---|---|
| `BM25Retriever` | 基于 `rank_bm25.BM25Okapi` 的词袋检索 |
| `DenseRetriever` | 基于 `sentence_transformers` + BGE 的稠密向量检索 |

### 5.4 调用点

`/home/wowoow/topic_code/src/core/controller.py:247`

```python
retrieval_query = _draft_to_query(subj, rel)  # f"{subj} {rel}"
top_passages = self.retriever.retrieve(
    retrieval_query, corpus, self.top_k_retrieve
)
```

### 5.5 输入输出

**输入：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | `str` | 检索查询，由 `subj + " " + rel` 拼接 |
| `corpus` | `list[str]` | 展平后的候选文档句子 |
| `top_k` | `int` | 返回 passage 数量 |

**输出：**

```python
list[str]  # Top-K 文档段落，按相关性排序
```

**注意：** 当前实现不返回分数或索引。若需展示来源，可通过 passages 内容匹配或扩展实现。

---

## 6. Validator：全局校验

### 6.1 位置

`/home/wowoow/topic_code/src/core/validator.py`

### 6.2 抽象接口

```python
class BaseValidatorEngine(ABC):
    @abstractmethod
    def score_graph(self, question: str, merger: SubGraphMerger) -> dict[str, int]: ...

    @abstractmethod
    def score_graph_with_raw(
        self, question: str, merger: SubGraphMerger
    ) -> tuple[dict[str, int], str]: ...
```

### 6.3 实现类

| 类 | 说明 |
|---|---|
| `APIValidatorEngine` | 调用 LLM API 进行验证（推荐用于 demo） |
| `ValidatorEngine` | 本地模型验证 |
| `MockValidatorEngine` | 固定分数，用于测试 |

### 6.4 APIValidatorEngine

**初始化：**

```python
def __init__(
    self,
    model: str,
    api_key: str,
    base_url: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
)
```

**核心方法：**

```python
def score_graph_with_raw(
    self, question: str, merger: SubGraphMerger
) -> tuple[dict[str, int], str]
```

**内部流程：**

1. `build_validator_prompt(question, merger)` → 生成 prompt + `id_map`
2. 调用 LLM API
3. `parse_validator_decisions(text, id_map)` → 解析 `Node Nx: KEEP/DISCARD`
4. `parse_validator_answer(text)` → 解析 `ANSWER: ...`

### 6.5 Prompt 构建

**入口：** `build_core_validator_content_from_merger()`（`/home/wowoow/topic_code/src/prompts.py`）

**System Prompt：** `CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY`（`/home/wowoow/topic_code/src/prompts.py:365`）

**输入给模型的内容：**

```text
Question: <原始问题>

Graph (N nodes):
[N0] subj | rel | obj
[N1] subj | rel | obj
...
```

**注意：** Validator 只接收 triple 图，不接收原始 passages。

### 6.6 输出格式

```text
<structure>: ...
<semantic>: ...
<comprehensive>: ...
<rethink>: ...
Final decision logic: ...

Node N0: KEEP
Node N1: DISCARD
...

ANSWER: <answer>
```

或：

```text
ANSWER: NONE
```

### 6.7 解析函数

| 函数 | 位置 | 作用 |
|---|---|---|
| `parse_validator_decisions(text, id_map)` | `validator.py:192` | 解析 KEEP/DISCARD，返回 `{internal_id: 0\|1}` |
| `parse_validator_answer(text)` | `validator.py` | 解析 `ANSWER:` 行 |
| `ValidatorFormatChecker.check(output, num_nodes)` | `validator.py:59` | 检查输出格式完整性 |

### 6.8 Pruning 与 Answer Selection

**Pruner：** `/home/wowoow/topic_code/src/core/pruning.py`

| 类 | 作用 |
|---|---|
| `NoOpPruningPolicy` | 保留所有节点，由 Validator 驱动筛选 |
| `NodeScorePruningPolicy` | 按得分、深度、创建顺序取 Top-K |

**AnswerSelector：** `/home/wowoow/topic_code/src/core/answer_selection.py`

| 类 | 作用 |
|---|---|
| `RankAllSelector` | 提取所有 root→leaf 路径，按叶节点得分排序 |

---

## 7. SubGraphMerger：推理状态图

### 7.1 位置

`/home/wowoow/topic_code/src/subgraph/merger.py`

### 7.2 数据结构

底层：`networkx.DiGraph`

### 7.3 节点属性

| 属性 | 类型 | 说明 |
|---|---|---|
| `triple_str` | `str` | 节点表示的三元组，如 `"subj | rel | obj"`，ROOT 节点为 `"ROOT"` |
| `accumulated_passages` | `str` | 从 ROOT 到当前节点路径上累积的 passages |
| `select_idx` | `int \| None` | 当前节点选中的 passage 索引 |
| `retrieved_passages` | `list[str]` | 当前节点检索到的所有 passages |
| `creation_order` | `int` | 创建顺序 |

### 7.4 核心方法

```python
class SubGraphMerger:
    def add_node(
        self,
        triple_str: str,
        parent_id: str | None = None,
        accumulated_passages: str | None = None,
        select_idx: int | None = None,
        retrieved_passages: list[str] | None = None,
        creation_order: int | None = None,
    ) -> str
```

**返回：** 新节点内部 ID（uuid hex）。

```python
    def get_path_triples(self, node_id: str) -> list[str]
```

**返回：** 从 ROOT（不含）到指定节点（含）的 triple 链。

```python
    def get_leaf_nodes(self) -> list[str]
```

**返回：** 所有叶节点 ID。

```python
    def get_union_view(self) -> nx.DiGraph
```

**返回：** 按 `triple_str` 去重合并后的图视图。

```python
    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, data: dict) -> "SubGraphMerger"
```

**作用：** 序列化/反序列化，便于存入 MemoryBackend。

---

## 8. Prompt 库关键路径

### 8.1 Draft / 方向生成

| 名称 | 位置 | 用途 |
|---|---|---|
| `CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:516` | V3 方向生成 system prompt |
| `build_core_draft_v3_user_content()` | `src/prompts.py:547` | 构建 user content |
| `parse_draft_v3_output()` | `src/prompts.py:574` | 解析 V3 输出 |
| `parse_draft_list()` | `src/prompts.py:439` | 解析候选列表 |

### 8.2 Final / 三元组确认

| 名称 | 位置 | 用途 |
|---|---|---|
| `CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:615` | V3 三元组确认 system prompt |
| `build_core_final_v3_user_content()` | `src/prompts.py` | 构建 user content |
| `_parse_final()` | `src/core/controller.py:39` | 解析 final 输出 |

### 8.3 Validator / 全局校验

| 名称 | 位置 | 用途 |
|---|---|---|
| `CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:365` | Validator system prompt |
| `build_core_validator_content_from_merger()` | `src/prompts.py` | 从 merger 构建 prompt |
| `parse_validator_decisions()` | `src/core/validator.py:192` | 解析 KEEP/DISCARD |
| `parse_validator_answer()` | `src/core/validator.py` | 解析 ANSWER |

---

## 9. 与 customer-service 集成的映射

### 9.1 复用策略：提取 Prompt，重新实现

| topic_code 组件 | 处理方式 | 原因 |
|---|---|---|
| `CoreController` | **参考其流程，重新实现 QA Workflow** | 学术研究代码与 Harness 生命周期、接口不兼容 |
| `APIGeneratorEngine` | **参考实现，重新封装为 Agent** | 需适配 Harness 的 Agent / ContextAssembler 抽象 |
| `APIValidatorEngine` | **参考实现，重新封装为 Agent** | 需适配 Harness 的 Agent / MemoryBackend 抽象 |
| `DenseRetriever` / `BM25Retriever` | **参考实现，重新封装** | 检索逻辑简单，可直接重写；或复用 `sentence-transformers` / `rank-bm25` |
| `SubGraphMerger` | **参考设计，用 networkx 重新实现** | 需序列化到 Harness `MemoryBackend` |
| `src/prompts.py` | **直接提取并保留** | 这是经过验证的核心资产，应整体迁移 |

### 9.2 Agent 映射

| customer-service Agent | topic_code 参考 | 在新项目中的实现 |
|---|---|---|
| **Direction Agent** | `APIGeneratorEngine.draft_generate_list_v3()` 的 prompt + parser | 新 Agent，使用提取的 draft system prompt + user content builder + parser |
| **Evidence Agent** | `Retriever.retrieve()` + `APIGeneratorEngine.final_generate_v3()` 的 prompt + parser | 新 Agent，内部固定调用检索器 + final system prompt + parser |
| **Validation Agent** | `APIValidatorEngine.score_graph_with_raw()` 的 prompt + parser | 新 Agent，使用提取的 validator system prompt + parser |
| **SubGraphManager** | `SubGraphMerger` | 基于 `networkx.DiGraph` 重新实现 |
| **QA Workflow** | `CoreController.run()` 的主循环 | 用 Harness Runtime 重新编排 Direction → Evidence → Validation 循环 |

### 9.3 需要提取并保留的代码片段

#### 9.3.1 System Prompts（直接复制）

| Prompt | topic_code 位置 |
|---|---|
| `CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:516` |
| `CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:615` |
| `CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY` | `src/prompts.py:365` |

#### 9.3.2 User Content Builders（复制并适配）

| Builder | topic_code 位置 |
|---|---|
| `build_core_draft_v3_user_content()` | `src/prompts.py:547` |
| `build_core_final_v3_user_content()` | `src/prompts.py` |
| `build_core_validator_content_from_merger()` | `src/prompts.py` |

#### 9.3.3 Parsers（复制并保留）

| Parser | topic_code 位置 |
|---|---|
| `parse_draft_v3_output()` | `src/prompts.py:574` |
| `parse_draft_list()` | `src/prompts.py:439` |
| `_parse_final()` | `src/core/controller.py:39` |
| `parse_validator_decisions()` | `src/core/validator.py:192` |
| `parse_validator_answer()` | `src/core/validator.py` |

#### 9.3.4 控制流程（参考并重新实现）

| 流程 | topic_code 位置 |
|---|---|
| expand → validate 主循环 | `src/core/controller.py:187-386` |
| 状态图维护 | `src/subgraph/merger.py` |
| 路径提取与排名 | `src/core/controller.py:56-103`, `src/core/answer_selection.py` |

### 9.4 输入输出等价关系

**Direction Agent：**

```python
# topic_code 输入
draft_generate_list_v3(
    question,
    evidence_passages,
    confirmed_triples,
    K
)

# customer-service 输入（等价）
{
    "question": question,
    "evidence_passages": evidence_passages,
    "confirmed_triples": confirmed_triples,
    "K": K
}
```

**Evidence Agent：**

```python
# topic_code 输入（分两步）
top_passages = retriever.retrieve(query, corpus, top_k)
final_generate_v3(
    question,
    confirmed_triples,
    retrieved_passages=top_passages,
    draft_subject=subj,
    draft_relation=rel,
)

# customer-service 输入（合并）
{
    "question": question,
    "direction": (subj, rel),
    "corpus": corpus,
    "confirmed_triples": confirmed_triples,
    "top_k": top_k
}
# Evidence Agent 内部自动完成 retrieve + final
```

**Validation Agent：**

```python
# topic_code 输入
score_graph_with_raw(question, merger)

# customer-service 输入
{
    "question": question,
    "graph_state": graph_state  # SubGraphManager 的序列化表示
}
```

---

## 10. 关键代码路径速查

| 功能 | 文件 | 关键函数/类 |
|---|---|---|
| 主推理循环 | `src/core/controller.py` | `CoreController.run()` |
| 方向生成 | `src/generator/api_engine.py` | `APIGeneratorEngine.draft_generate_list_v3()` |
| 三元组确认 | `src/generator/api_engine.py` | `APIGeneratorEngine.final_generate_v3()` |
| 检索 | `src/retriever/stub.py` | `DenseRetriever.retrieve()` / `BM25Retriever.retrieve()` |
| 全局校验 | `src/core/validator.py` | `APIValidatorEngine.score_graph_with_raw()` |
| 图状态 | `src/subgraph/merger.py` | `SubGraphMerger` |
| Prompt | `src/prompts.py` | `CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY`, `CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY`, `CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY` |
| 解析 | `src/prompts.py`, `src/core/controller.py`, `src/core/validator.py` | `parse_draft_v3_output`, `_parse_final`, `parse_validator_decisions`, `parse_validator_answer` |
| 配置 | `configs/core_api_v2.yaml` | API 模式配置（参考模型参数） |
| 入口 | `scripts/run_core.py` | 数据集推理（参考运行方式） |

---

## 11. 集成注意事项

1. **不导入 `topic_code` 包**：只提取 prompt、parser 和控制流程作为参考，在 `agents/customer-service/` 内重新实现。
2. **依赖最小化**：新项目只需 LLM API 客户端、`networkx`、检索库（`sentence-transformers` / `rank-bm25`），不需要 `unsloth` 等训练依赖。
3. **API 模型参数**：参考 `configs/core_api_v2.yaml` 的 `draft_temperature`、`final_temperature`、`validator.temperature` 等参数。
4. **Prompt 改写**：当前 prompt 面向通用多跳 QA，客服场景需逐步替换为业务 wording（如“改签规则”“赔偿标准”“会员权益”）。
5. **状态序列化**：新实现的 `SubGraphManager` 必须支持 `to_dict()` / `from_dict()`，以便存入 Harness `MemoryBackend`。
6. **事件流映射**：QA Workflow 中每个步骤都应发射标准事件到浏览器和终端，便于可视化。

---

*本文档为 topic_code 实现细节参考，供 customer-service Agent 集成时使用。核心复用原则是：**提取 prompt 与解析逻辑，重新实现控制流程与状态管理**。*
