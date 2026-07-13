# 多人实时群聊系统（Runtime 版）顶层设计方案

> 基于 Harness Agent Runtime 构建的多 Agent + 用户实时群聊系统。
> **核心原则：零框架核心修改，全部通过新增组件与 Workflow 脚本配置实现。**

---

## 一、设计目标

构建一个基于现有 Harness Runtime 的多人实时群聊系统，支持：

- 多个 Agent 与真实用户共处一个"聊天房间"
- Agent 能够像真人一样"抢话"、被打断、插话
- 每个 Agent 有差异化的响应节奏（通过时间随机性）
- 保留现有 Web Chat 的表情包处理方式
- MVP 阶段不过度复杂化决策逻辑，以时间随机性为主

---

## 二、核心原则

### 2.1 不改核心，只换组件

本设计**绝不修改**以下框架核心文件：

- `harness/core/async_orchestrator.py`
- `harness/runtime/agent_runtime.py`
- `harness/runtime/kernel.py`
- `harness/runtime/message_bus.py`
- `harness/interfaces/` 下的所有 Protocol 定义

所有群聊行为通过以下方式实现：

1. **新增组件**：实现现有 Protocol 的新类，注册到 DI 容器
2. **Workflow 脚本**：`@agent` + `@subscribe` 声明多 Agent 拓扑
3. **新增 WebServer**：扩展前端，对接 MessageBus

### 2.2 MVP 优先，接受技术债

以下妥协在 MVP 阶段被明确接受：

| 妥协项 | 说明 | 后续改进方向 |
|--------|------|-------------|
| 历史记录粒度不一致 | Orchestrator 把整段缓冲 dump 写入 `_history`，Assembler 读取时过滤压缩 | Room History |
| `[选择]` 标记泄露进 history | LLM 的原始输出（含 `[选择]`）原样进入 history，Assembler 读取时正则过滤 | 改 Orchestrator 或引入 Room History |
| 无全局一致上下文 | 每个 Agent 只能看到自己收到的消息，视角天然不一致 | Room History |
| 规则硬编码在 Assembler | 群聊规则、表情规则写死在 Assembler 代码里 | 通过 GuideProvider 注入 |
| 用户消息不切分 | 用户发送的消息作为一个整体进入缓冲 | 动态语义切分 |

---

## 三、架构概览

```
┌─────────────────────────────────────────┐
│           7. Web 前端层                 │
│   多参与者气泡、消息渲染、表情替换、      │
│   输入框、实时状态展示                   │
├─────────────────────────────────────────┤
│           6. 传输适配层                  │
│   WebSocket 连接 ↔ GroupChatWebServer   │
│   用户消息直接 publish 到 MessageBus    │
├─────────────────────────────────────────┤
│           5. 消息总线层                  │
│   MessageBus：publisher → subscribers   │
│   "user" 作为特殊 publisher             │
├─────────────────────────────────────────┤
│           4. Agent 个体层                │
│   每个 Agent 包含：                       │
│   • FlexibleGroupChatInputAdapter       │
│     └─ 内部包装 KernelBridgeAdapter     │
│   • AtomicOutputAdapter                 │
│     └─ 内部包装 FlexibleGroupChatInputAdapter
│   • SelectiveGroupChatAssembler         │
│   • Async Orchestrator + LLM            │
├─────────────────────────────────────────┤
│           3. 运行时调度层                │
│   Kernel：进程表、队列管理、生命周期      │
│   （复用，零修改）                        │
├─────────────────────────────────────────┤
│           2. 启动配置层                  │
│   Workflow 脚本：定义 Agent、订阅关系、   │
│   各自的性格参数（metadata）              │
├─────────────────────────────────────────┤
│           1. 基础设施层                  │
│   LLM 调用、事件类型、DI 容器             │
│   （复用，零修改）                        │
└─────────────────────────────────────────┘
```

---

## 四、数据契约

### 4.1 UserRequest.metadata["buffered"]

`FlexibleGroupChatInputAdapter.receive()` 返回的 `UserRequest` 中，`text` 字段为兜底摘要（便于调试日志），**真正的结构化数据**放在 `metadata["buffered"]` 中。

```python
UserRequest(
    text="[群聊摘要] 4条消息",  # 人类可读的兜底摘要
    metadata={
        "buffered": [
            {
                "from": "user",           # 发送者的 pid
                "from_name": "主人",       # 显示名称（供 LLM 阅读）
                "content": "今天天气真好",
                "timestamp": 1718000000.0,
            },
            {
                "from": "xiaohong",
                "from_name": "小红",
                "content": "我可以一起吗？",
                "timestamp": 1718000001.5,
            },
        ]
    }
)
```

### 4.2 消息来源身份映射

| 消息来源 | `from` (pid) | `from_name` (显示名) | 获取方式 |
|----------|-------------|---------------------|----------|
| 真实用户 | `"user"` | Workflow 脚本中 `user_name` 配置 | WebServer 配置 |
| Agent | Agent 的 `pid` | `@agent(metadata={"display_name": "..."})` | Workflow 脚本 |

**重要**：绝不在 LLM 上下文里用"用户"二字作为发送者名称，否则 LLM 会倾向只回复用户。一律使用显示名。

---

## 五、新增组件清单

| # | 组件名称 | 类型 | 说明 |
|---|---------|------|------|
| 1 | `FlexibleGroupChatInputAdapter` | AsyncInputAdapter | 带缓冲窗口和随机触发的输入适配器 |
| 2 | `SelectiveGroupChatAssembler` | ContextAssembler | 要求 LLM 先选择后回复的上下文组装器 |
| 3 | `AtomicOutputAdapter` | AsyncInputAdapter 装饰器 | 解析 LLM 结构化输出并原子化发送 |
| 4 | `GroupChatWorkflow` | Workflow 脚本 | 定义多 Agent、订阅关系、性格参数 |
| 5 | `GroupChatWebServer` | FastAPI + WebSocket | 扩展现有 chat-web，支持多参与者显示 |

**复用的现有组件**（不做修改）：

- Kernel、AgentRuntime、AsyncLifecycleOrchestrator
- MessageBus（复用现有 pub-sub 机制）
- 现有事件类型（TextEvent、StopEvent、InternalMessage 等）

---

## 六、各组件详细设计

### 6.1 FlexibleGroupChatInputAdapter（灵活输入适配器）

每个 Agent 的核心"节奏控制器"。

**职责**：
- 从 Kernel 的 `input_queues[pid]` 中拉取消息
- 维护一个短期消息缓冲窗口
- 在合适的时机把缓冲中的消息打包成 `UserRequest`
- 决定 Agent 何时"思考"和"开口"

**实现方式**：
- 内部持有 `KernelBridgeAdapter` 实例，代理 `send()` 调用
- `receive()` 自行实现缓冲逻辑
- 在 Workflow 脚本中注册为 `AsyncInputAdapter`
- Kernel 的 `_resolve_adapter` 需要能识别此类型并完成 `kernel`/`runtime` 注入

**触发逻辑**：

```
1. 阻塞等待第一条消息到达
2. 收到第一条消息后，启动计时器：
   min_deadline = now + min_wait + random_jitter
   max_deadline = now + max_wait + random_jitter
3. 持续非阻塞接收新消息，追加到缓冲，不重置计时器
4. 检查：
   - 若缓冲非空且 now >= min_deadline → 触发，返回 UserRequest
   - 若缓冲非空且 now >= max_deadline → 强制触发
   - 若缓冲为空 → 继续等待（即使 max_deadline 到了也不触发）
```

**配置参数**（放在 `@agent` 的 `metadata` 中）：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_wait` | float | 1.0 | 最短等待时间（秒） |
| `max_wait` | float | 5.0 | 最长等待时间（秒） |
| `jitter` | float | 0.5 | 随机抖动上限（秒） |

**差异化节奏**：

每个 Agent 独立拥有一份 `FlexibleGroupChatInputAdapter` 实例，因此：
- 不同 Agent 可以配置不同的 `min_wait` / `max_wait` / `jitter`
- 有的 Agent 急性子（`min_wait=0.5`），有的慢半拍（`min_wait=2.5`）
- 节奏差异是群聊自然感的主要来源

MVP 阶段通过 Workflow 脚本的 `@agent(metadata={...})` 为每个 Agent 注入不同参数即可实现，无需复杂的统一调度策略。

**MVP 简化**：
- 不做复杂的内容打分
- 不做 @ 检测加权
- 不做 LLM 辅助决策
- 纯靠时间参数 + 随机抖动制造差异

### 6.2 SelectiveGroupChatAssembler（选择性上下文组装器）

把 `FlexibleGroupChatInputAdapter` 交过来的缓冲消息，整理成 LLM 能理解的对话上下文。

**核心职责**：
- 消费 `UserRequest.metadata["buffered"]` 中的结构化消息
- 给缓冲中的消息编号
- 注入群聊规则、性格设定、表情规则
- 过滤/压缩 `_history` 中的旧记录

**历史记录处理策略（MVP 妥协）**：

由于 `AsyncLifecycleOrchestrator` 会把每回合的 `UserRequest.text`（整段缓冲 dump）追加到 `_history`，历史中存在大量冗余信息。Assembler 采取以下策略：

1. **丢弃所有旧的 `role="user"` 消息**：这些消息是之前回合的缓冲 dump，信息已过时
2. **保留最近 2 条 `role="assistant"` 消息**：这是该 Agent 自己之前说过的话
3. **压缩 assistant 消息**：用正则提取 `[回复]` 内容，丢弃 `[选择]` 标记，包装为 "你之前说过：" 的 system 提示

**LLM 看到的消息列表**：

```python
[
    # 1. 身份 + 群聊规则 + 表情规则
    Message(role="system", content="你是{显示名}，{性格}...\n\n## 群聊规则\n...\n\n## 表情规则\n..."),

    # 2. 自己之前说过的话（从 history 过滤提取，可选）
    Message(role="system", content="你之前说过：\n- \"...\"\n- \"...\""),

    # 3. 当前缓冲消息编号（核心）
    Message(role="system", content="最近群聊消息：\n1. [{from_name}] {content}\n2. [{from_name}] {content}\n..."),

    # 4. 指令
    Message(role="user", content="请从以上消息中选择一条回复。输出格式：\n[选择] <编号或0>\n[回复] <内容或'无'>"),
]
```

**群聊规则（硬编码）**：

```text
## 群聊规则
- 当前是一个多人群聊，你在和几个朋友聊天
- 你只能从"最近群聊消息"中选择一条回复
- 如果没人说话值得回，输出 [选择] 0 和 [回复] 无
- 回复要口语化、简短，像真人微信聊天
- 不要重复别人已经说过的内容
- 不要过度热情，偶尔潜水也是正常的
```

**表情规则（硬编码，复用 chat-web 的 5 个 ID）**：

```text
## 表情规则
- 可用表情（ONLY these 5）:
  :happy: — 开心、高兴
  :laugh: — 大笑、觉得好笑
  :cool:  — 酷、赞、认可
  :cry:   — 悲伤、无奈
  :cute:  — 可爱、温柔
- 每条回复最多用两个表情，自然插入在对应情绪的句子末尾
- 绝不用 Unicode 表情（如 😊 👍）
- 如果没合适的情绪，不用表情
```

**LLM 输出格式要求**：

LLM 必须输出两段式结构：

```text
[选择] <消息编号；不想回复则填 0>
[回复] <回复内容；不想回复则填"无">
```

示例：

```text
[选择] 3
[回复] 带飞盘我可以一起！:happy:
```

### 6.3 AtomicOutputAdapter（原子化输出适配器）

负责把 LLM 的结构化输出解析并发送出去。

**实现方式**：
- 作为 `AsyncInputAdapter` 的装饰器实现
- 内部包装 `FlexibleGroupChatInputAdapter`（或 `KernelBridgeAdapter`）
- 在 Workflow 脚本中注册为最外层的 `AsyncInputAdapter`
- 代理 `receive()` 调用
- 拦截 `send(TextEvent)` 做解析和切分

**处理流程**：

```
1. 收到 TextEvent(content="[选择] 3\n[回复] 带飞盘我可以一起！:happy:")
2. 正则提取 [选择] → choice = 3
3. 正则提取 [回复] → reply = "带飞盘我可以一起！:happy:"
4. 如果 choice == 0 或 reply == "无" → 直接返回，不发送任何消息
5. 如果 reply 有内容：
   a. 按句子切分（按中文标点：。！？\n）
   b. 每个短句作为独立 TextEvent 发送
   c. 短句之间加入 150-400ms 随机延迟
6. 最后发送 StopEvent
```

**切分原则（MVP）**：
- 按中文标点切分：`。`、`！`、`？`、换行
- 每段作为一个原子消息
- 不合并显示，每个短句就是一个独立消息气泡

**容错处理**：
- 找不到 `[选择]` 标记 → 把整个输出当作普通回复，不切分直接发送
- `[回复]` 为空但 `[选择]` 非 0 → 视为不想回复，不发送
- 解析失败 → 不发送，仅记录日志

**关于 `[选择]` 标记泄露的说明**：

`AsyncLifecycleOrchestrator` 会在 `adapter.send(TextEvent)` **之后**，把原始的 `response.text`（含 `[选择]`）追加到 `_history`。`AtomicOutputAdapter` 无法阻止这一行为（不改核心）。

**MVP 策略**：接受泄露。`SelectiveGroupChatAssembler` 在读取 history 时正则过滤掉 `[选择]` 部分。LLM 看到 history 里自己的旧输出带有 `[选择]` 标记，反而有助于维持输出格式的稳定性。

### 6.4 MessageBus 订阅关系

复用现有 Runtime 的 MessageBus，职责不变：

- 维护 publisher → subscribers 的订阅关系
- 把 TextEvent 广播给所有活跃订阅者
- 把消息投递到对应 Agent 的输入队列

**群聊场景下的订阅关系**：

```python
subscribe("xiaoming").to("xiaohong")
subscribe("xiaoming").to("user")
subscribe("xiaohong").to("xiaoming")
subscribe("xiaohong").to("user")
```

- 每个 Agent 订阅所有其他 Agent
- 每个 Agent 订阅 `"user"` 这个角色
- 用户订阅所有 Agent（由 WebServer 实现，不是 MessageBus 订阅）
- MessageBus 不将消息回发给发送者（现有行为）

### 6.5 用户消息进入 MessageBus

用户不是 Agent，不创建 AgentRuntime。用户消息的投递方式：

```python
# 在 GroupChatWebServer 中
kernel.message_bus.publish(
    from_pid="user",
    event=TextEvent(content=user_input),
)
```

`"user"` 是一个虚拟 publisher pid，不是真实 Agent。MessageBus 会把它路由给所有订阅了 `"user"` 的 Agent。

### 6.6 Web 前端（扩展现有）

复用并扩展现有 chat-web 前端。

**新增能力**：
- 多参与者显示：不同头像/颜色区分用户和不同 Agent
- 消息气泡：每个 TextEvent 独立渲染，不合并
- 表情渲染：把 `:happy:`、`:laugh:` 等 ID 映射为图片或 Unicode 表情
- 输入框：用户发送的消息直接调用 `kernel.message_bus.publish("user", ...)`
- 可选：显示"正在输入"提示

**与现有 chat-web 的区别**：
- 现有 chat-web：每个 WebSocket 连接创建一个独立 Agent（单聊）
- 群聊版：一个房间对应一个 Kernel + 多个 Agent，WebSocket 只负责用户 I/O

---

## 七、效果展示

以下为实际运行截图，展示群聊系统的三大核心能力。

### 7.1 差异化性格与产品推广

小明（热情话痨）在群里自然安利"喵喵智能音箱"，小红（高冷敷衍）只回"6"、"不"、"..."。同一群聊中不同 Agent 展现出截然不同的说话风格——这由 `SelectiveGroupChatAssembler` 注入的性格设定和 `AtomicOutputAdapter` 的原子化输出来实现。

![产品推广以及不同性格效果](../../agents/group-chat/images/promotion-and-personality.png)

### 7.2 自然抢话与插话

小明的长消息被原子化切分为短句发送，中间留出时间窗口。小红在小明还没说完时就连续插话提问——"哪家火锅？！"、"新开的吗！"、"什么锅底？"——完美复刻了真实群聊的抢话感。这是**原子化输出切分**（第 6.3 节）的直接效果。

![插话效果](../../agents/group-chat/images/interruption.png)

### 7.3 用户参与群聊

用户作为群成员直接参与对话（绿色气泡）。Agent 能看到用户消息并实时回复，形成真正的多对多群聊体验。用户消息通过 `kernel.message_bus.publish("user", ...)` 进入 MessageBus，被所有订阅了 `"user"` 的 Agent 接收。

![用户参与的聊天效果](../../agents/group-chat/images/user-participation.png)

---

## 八、LLM 上下文完整示例

以 Agent "小红" 在某一轮触发为例，她看到的上下文如下：

```python
[
    Message(role="system", content="""
你是小红，性格活泼外向，喜欢接话，说话带点小俏皮。

## 群聊规则
- 当前是一个多人群聊，你在和几个朋友聊天
- 你只能从"最近群聊消息"中选择一条回复
- 如果没人说话值得回，输出 [选择] 0 和 [回复] 无
- 回复要口语化、简短，像真人微信聊天
- 不要重复别人已经说过的内容
- 不要过度热情，偶尔潜水也是正常的

## 表情规则
- 可用表情（ONLY these 5）:
  :happy: — 开心、高兴
  :laugh: — 大笑、觉得好笑
  :cool:  — 酷、赞、认可
  :cry:   — 悲伤、无奈
  :cute:  — 可爱、温柔
- 每条回复最多用两个表情，自然插入在对应情绪的句子末尾
- 绝不用 Unicode 表情（如 😊 👍）
- 如果没合适的情绪，不用表情
"""),

    Message(role="system", content="""
你之前说过：
- "带飞盘我可以一起！:happy:"
- "我们三点半见吧。"
"""),

    Message(role="system", content="""
最近群聊消息：
1. [小明] 今天天气真好
2. [主人] 我们去公园吧
3. [主人] 带上飞盘怎么样？
4. [小刚] 我可以一起吗？
"""),

    Message(role="user", content="""
请从以上消息中选择一条回复。输出格式：
[选择] <编号或0>
[回复] <内容或'无'>
"""),
]
```

**说明**：
- `你之前说过` 是从 `_history` 里提取的该 Agent 自己的旧回复，最多 2 条
- `最近群聊消息` 是从 `metadata["buffered"]` 编号的当前缓冲
- 所有 `from_name` 都是显示名，不出现"用户"二字

---

## 九、消息完整生命周期

### 阶段 1：消息产生

用户在前端输入一段文字，点击发送。

用户消息**默认不切分**，按照用户的原始输入发送。

### 阶段 2：消息进入 MessageBus

`GroupChatWebServer` 调用 `kernel.message_bus.publish("user", TextEvent(content=...))`。

MessageBus 根据订阅表，把消息投递到每个订阅者的输入队列中。

### 阶段 3：Input Adapter 缓冲

每个 Agent 的 `FlexibleGroupChatInputAdapter` 独立地从自己的输入队列取消息。

第一条消息到达时，启动该 Agent 的"最短等待计时器"。

后续消息陆续到达，被追加到同一个缓冲窗口中。

计时器不会被新消息重置，但缓冲内容持续增长。

### 阶段 4：触发 LLM 调用

当最短等待时间到达时，Input Adapter 检查缓冲：
- 如果有消息，把缓冲打包成一个 `UserRequest(metadata={"buffered": [...]})`
- 交给 Orchestrator 进入下一轮

如果最长等待时间到达时仍未触发，则强制触发（缓冲非空时）。

不同 Agent 的最短/最长等待时间不同，加上随机抖动，导致它们不会同时触发。

### 阶段 5：上下文组装

Orchestrator 调用 `SelectiveGroupChatAssembler`。

Assembler 把 `UserRequest` 中的缓冲消息：
1. 解析成带编号的消息列表
2. 从 `ctx.history` 提取该 Agent 自己之前的回复（最多 2 条）
3. 注入系统提示（身份、群聊规则、表情规则）
4. 输出最终消息列表

### 阶段 6：LLM 生成回复

LLM 看到第 8 节所示的上下文，输出：

```text
[选择] 3
[回复] 带飞盘我可以一起！:happy:
```

### 阶段 7：输出解析与原子化发送

`AtomicOutputAdapter` 解析 LLM 输出：
- `[选择] 3` 被丢弃，不暴露给外部
- `[回复] 带飞盘我可以一起！:happy:` 被提取

检查回复长度：
- 如果已经是一个短句，直接发送
- 如果较长，按中文标点切分成多个短句

例如：
- 原始回复：`"带飞盘我可以一起！我们三点半见吧。"`
- 切分为：
  1. `"带飞盘我可以一起！:happy:"`
  2. `"我们三点半见吧。"`

每个短句作为独立 `TextEvent`，间隔 150-400ms 随机延迟后发送。

**注意**：`AsyncLifecycleOrchestrator` 在此之后会把原始 `response.text`（含 `[选择]`）追加到 `_history`。这是已接受的 MVP 妥协。

### 阶段 8：广播给其他参与者

每个 `TextEvent` 进入 MessageBus，广播给所有订阅者。

其他 Agent 的 Input Adapter 在自己的缓冲窗口中看到这些新消息。

### 阶段 9：循环重复

其他 Agent 可能因此触发新的 LLM 调用，产生新的回复。

用户也可能继续输入。

整个系统持续运转，直到 workflow 被显式结束（`/end` 或 `/exit`）。

### 阶段 10：前端渲染

前端收到每个 `TextEvent`，立即渲染为一个气泡。

同一 Agent 的连续短句显示为连续气泡，不合并。

表情 ID 被替换为对应表情图片或符号。

---

## 十、抢话机制设计细节

### 9.1 抢话是如何发生的

抢话不是通过特殊逻辑实现的，而是**自然涌现**的：

1. **输入时间差**：Agent A 的等待时间短，Agent B 的等待时间长。A 先触发，B 还在观察。
2. **消息切分**：用户或 Agent 的长消息被切成多段。A 看到第一段就回复了，第二段还没到达。
3. **输出切分**：Agent A 的回复也被切成多段。B 看到 A 的第一段后就可以插话，不需要等 A 说完。
4. **随机抖动**：即使配置相同，随机抖动也会避免碰撞。

### 9.2 抢话后的修正

如果 Agent 基于不完整信息回复了，后续消息到达后：

- 该 Agent 下一轮会看到完整上下文
- 可以补充："哦原来要带飞盘，我也去！"
- 其他 Agent 也可以指出："小明你抢话了，人家说的是飞盘"

这种"误解-澄清"本身就是真实群聊的一部分。

---

## 十一、上下文一致性策略

### 10.1 不追求全局强一致

本设计接受一个基本事实：**不同 Agent 看到的上下文不完全相同**。

原因是：
- 每个 Agent 触发时间不同
- 每个 Agent 的缓冲窗口内容不同
- 每个 Agent 不会收到自己发出的消息（但会从自己的历史中间接知道）

### 10.2 保证局部一致

所有 Agent 都从同一个 MessageBus 接收消息，所以：
- 消息内容一致
- 消息来源一致
- 消息时间戳一致

差异只在于"收到了多少条"和"什么时候触发"。

### 10.3 Room History 的可选增强

如果后续需要更强的一致性，可以引入 Room History：

- 一个全局共享的完整消息时间线
- 所有已完成的原子短句按时间顺序记录
- `SelectiveGroupChatAssembler` 优先从 Room History 读取背景
- 缓冲中未进入 Room History 的最新消息作为"实时补充"

MVP 可以先不实现 Room History，直接采用第 6.2 节的 history 过滤策略。

---

## 十二、边界情况处理

### 11.1 LLM 选择不回复

当 LLM 输出：

```text
[选择] 0
[回复] 无
```

`AtomicOutputAdapter` 直接丢弃，不发送任何消息。

这个 Agent 本轮"潜水"，但继续运行，等待下一轮触发。

### 11.2 所有 Agent 同时触发

即使有时间随机性，仍可能偶尔发生碰撞。

这不会造成系统错误，只是多个 Agent 几乎同时回复。

这正是真实群聊中"同时接话"的场景，可以接受。

### 11.3 某个 Agent 话太多

通过两个机制控制：

1. 输出原子化切分：长回复自动切成短句，中间留出时间让其他人插话
2. 冷却时间（可选，MVP 不做）：一个 Agent 发送后，短时间内降低其再次触发的概率

### 11.4 用户长时间不说话

Agent 的 Input Adapter 缓冲为空时，不会触发。

系统处于静默状态，等待用户或外部事件。

### 11.5 LLM 不遵守格式

`AtomicOutputAdapter` 容错：

- 如果找不到 `[选择]` 标记，把整个输出当作普通回复
- 如果 `[回复]` 为空但 `[选择]` 非 0，选择不发送或 fallback
- 如果解析失败，不发送，仅记录日志

### 11.6 Agent 收到自己的消息

MessageBus 不会把消息回发给发送者。

但 Agent 自己的历史中会保留自己说过的话（通过 `_history`）。

---

## 十三、MVP 范围

### 包含

- 2-3 个 Agent + 用户的群聊
- 基于时间的输入缓冲和触发
- 随机抖动
- 选择一条消息回复的上下文组装
- 原子化输出切分和延迟发送
- 表情规则
- Web 前端多参与者显示
- 用户作为虚拟 publisher 进入 MessageBus

### 不包含

- 复杂的内容打分决策
- @ 检测加权
- LLM 辅助是否回复决策
- Room History
- 用户消息的动态切分
- 冷却时间控制
- 跨 session 记忆共享（每个 Agent 仍使用独立的 MdMemory）

---

## 十四、Workflow 脚本示例

```python
"""group_chat_demo.py — 三人群聊演示。

启动方式:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('group_chat_demo.py')
"
"""

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    ContextAssembler,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)
from harness.runtime.decorators import agent, subscribe

USER_NAME = "主人"  # 用户在群聊中的显示名


def _assemble_agent(name: str, display_name: str, persona: str,
                    min_wait: float = 1.0, max_wait: float = 5.0) -> Harness:
    """装配单个群聊 Agent。"""
    container = DIContainer()

    memory = MdMemory(path=f"./memory/group_chat/{name}")
    container.register(MemoryBackend, memory)
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(SystemToolProvider, DefaultSystemToolProvider())

    # ContextAssembler — 群聊定制版
    container.register(ContextAssembler, SelectiveGroupChatAssembler())

    # AsyncInputAdapter — 由 Kernel 在 spawn 时注入 FlexibleGroupChatInputAdapter
    # 这里先占位，实际实例化由 _resolve_adapter 处理
    # 或者通过 metadata 传参，让 Kernel 知道如何构造

    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "xiaoming",
    entry_prompt="你是小明，外向活泼。现在群里很安静，随便打个招呼吧。",
    metadata={
        "display_name": "小明",
        "min_wait": 0.8,
        "max_wait": 3.0,
        "persona": "外向活泼，喜欢接话",
    },
)
def assemble_xiaoming():
    return _assemble_agent("xiaoming", "小明", "外向活泼", 0.8, 3.0)


@agent(
    "xiaohong",
    entry_prompt="你是小红，温柔可爱。等别人先说话再回复。",
    metadata={
        "display_name": "小红",
        "min_wait": 1.5,
        "max_wait": 5.0,
        "persona": "温柔可爱，偶尔卖萌",
    },
)
def assemble_xiaohong():
    return _assemble_agent("xiaohong", "小红", "温柔可爱", 1.5, 5.0)


# 全连接订阅 —— 互相收听 + 收听用户
subscribe("xiaoming").to("xiaohong")
subscribe("xiaoming").to("user")
subscribe("xiaohong").to("xiaoming")
subscribe("xiaohong").to("user")
```

---

## 十五、组件注册关系

在 Kernel spawn 时，DI 容器的注册关系如下（由 `_resolve_adapter` 处理）：

```
AgentRuntime.adapter
  └─ AtomicOutputAdapter              # 最外层，注册为 AsyncInputAdapter
      └─ FlexibleGroupChatInputAdapter  # 中间层，由 Kernel 注入 pid/kernel/runtime
          └─ KernelBridgeAdapter        # 最内层，对接 MessageBus
```

**`Kernel._resolve_adapter` 的调整**（这是允许的核心外修改）：

`Kernel` 的 `_resolve_adapter` 函数需要能识别 `FlexibleGroupChatInputAdapter` 类，并在容器 resolve 出类型时完成 `pid`/`kernel`/`runtime` 的注入。这是唯一需要修改的框架侧代码点（位于 `kernel.py` 的辅助函数中，不算 Orchestrator/Runtime 核心逻辑）。

---

## 十六、设计原则总结

1. **机制与策略分离**：Runtime 和 MessageBus 提供机制，Adapter 和 Assembler 实现策略。
2. **时间驱动优先**：MVP 用时间参数和随机性制造自然节奏，不做过重的智能决策。
3. **选择式回复**：强制 LLM 只聚焦一条消息，避免面面俱到。
4. **原子化输出**：把回复切成可被打断的短句，创造真实抢话感。
5. **接受局部不一致**：不同 Agent 的视角不同是特性，不是 bug。
6. **复用现有能力**：尽量在 Adapter 和 Assembler 层做扩展，少改框架核心。
7. **数据契约先行**：`UserRequest.metadata["buffered"]` 是 InputAdapter 与 Assembler 之间的唯一数据契约。
