# 工具治理层（Tool Governance Layer）设计

日期：2026-08-27
状态：已确认（方案 A）

## 一、背景与目标

### 现状

工具调用路径：

```
AgentRuntime.run()
  → AsyncLifecycleOrchestrator._phase_loop()
    → 内层 LLM+tool 循环
      → tool_router.execute(name, args)   # 同步调用
        → ToolRouter 按名称路由
          → CompositeSystemToolProvider（用户工具 + runtime 工具）
          → MCPAdapter（外部 MCP server）
```

现状问题：

1. **同步阻塞**：`ToolRouter.execute()` 是同步调用，直接发生在 async 编排循环里。
   MCP server 挂起或慢响应会冻结整个进程的事件循环（所有 agent 共用一个
   asyncio loop），外部服务故障直接向 Agent 主循环传导。
2. **无弹性策略**：没有超时、没有重试。
3. **无权限管控**：高风险工具（删除文件、写外部系统等）无需人工确认即执行。

### 目标

1. 设计统一 Tool 接入层（治理层），方便工具调用策略迭代——策略改动只动这一层。
2. 内置超时、重试等弹性策略，避免外部服务故障向 Agent 主循环传导。
3. 引入 Gate 机制，对高风险工具执行权限校验与人工确认（前台 CLI 审批）。
4. **接口不变**：`Tool` / `SystemToolProvider` / `MCPAdapter` / `ToolRouter`
   接口零改动，MCP 侧无感知。

### 已确认的决策

| 决策点 | 结论 |
|---|---|
| 审批通道 | Console 命令式：`/approve <id>` / `/deny <id>`，复用 CliConsole 独占的 stdin 通道 |
| 策略配置 | 代码注册式：`PolicyRegistry` API，挂 Kernel 单例 |
| 执行模型 | 异步化包装：治理层 `async execute()`，内部 `asyncio.to_thread` + `wait_for` |
| 故障语义 | 全部吸收为 `ToolResult(success=False)`，主循环永不因工具故障崩溃 |
| 审批无响应 | 审批超时（默认 300s）视为拒绝 |

## 二、总体架构

```
┌─ AgentRuntime ── AsyncLifecycleOrchestrator ─────────────┐
│                                                          │
│   LLM tool_uses ──> await governance.execute(name, args) │
│                            │                             │
└────────────────────────────┼─────────────────────────────┘
                             ▼
              ┌─ ToolGovernanceLayer（每 agent 一个实例）─┐
              │  1. PolicyRegistry.lookup(name) → Policy  │
              │  2. Gate.check() ──高风险──> ApprovalBroker│
              │  3. Resilience: to_thread + wait_for      │
              │     + retry/backoff                       │
              │  4. 故障 ──> ToolResult(success=False)    │
              └───────────────┬────────────────────────────┘
                              ▼ (接口不变)
              ToolRouter ──> SystemToolProvider / MCPAdapter

ApprovalBroker <── Kernel._handle_system_input 路由 /approve /deny
        ▲
CliConsole.send(ApprovalRequested 事件) ──> 前台展示
```

### 组件清单

新增 4 个文件：

| 文件 | 职责 |
|---|---|
| `harness/core/governance/__init__.py` | 包导出（ToolPolicy / RetryPolicy / PolicyRegistry / ToolGovernanceLayer / ApprovalBroker） |
| `harness/core/governance/policy.py` | `ToolPolicy` / `RetryPolicy` dataclass + `PolicyRegistry`（精确名、fnmatch 通配、默认策略三级匹配） |
| `harness/core/governance/layer.py` | `ToolGovernanceLayer`：`async execute(name, args) -> ToolResult`，编排 gate → retry → timeout 顺序 |
| `harness/core/governance/approval.py` | `ApprovalBroker`：挂 Kernel，管理 pending 审批（`asyncio.Future` 表），`request()` 发事件并等裁决（带超时），`resolve()` 由 Kernel 命令循环回调 |

修改 4 处：

| 文件 | 修改 |
|---|---|
| `harness/runtime/types.py` | 新增 `ApprovalRequested`（SystemEvent）、`CommandApprove` / `CommandDeny`（SystemCommand） |
| `harness/runtime/kernel.py` | 创建 PolicyRegistry + ApprovalBroker；`_handle_system_input` 增加两个命令分支；spawn 路径把 broker/registry 传入编排器 |
| `harness/core/async_orchestrator.py` | `_phase_init` 建 governance layer 包裹 tool_router；`_phase_loop` 中 `tool_router.execute()` → `await governance.execute()`（约 3 行改动） |
| `harness/runtime/cli_console.py` | 解析 `/approve <id>` / `/deny <id>`；渲染 `ApprovalRequested` 事件 |

### 关键边界

- `Tool` / `SystemToolProvider` / `MCPAdapter` / `ToolRouter` 接口零改动。
- 无策略注册时治理层是**透传层**（仅 to_thread 化，行为等价），默认零治理
  开销——不破坏任何现有测试语义。这是兼容性的硬约束。
- runtime tools（spawn_workflow / end_workflow / finish_agent / talk_to /
  list_agents）预注册为 `executor="direct"`：它们操作 kernel 内存态，
  在事件循环内直接调用，放线程里反而引入竞态；且默认 `gate=False`。

## 三、审批时序（Gate 链路）

以 agent `root` 调用高风险工具 `delete_file` 为例：

```
Orchestrator        GovernanceLayer       ApprovalBroker      Kernel/Console        用户
    │ await execute("delete_file", args)    │                  │                  │
    │─────────────────>│                     │                  │                  │
    │                  │ lookup → gate=True  │                  │                  │
    │                  │ broker.request(pid, name, args)        │                  │
    │                  │────────────────────>│                  │                  │
    │                  │                     │ 生成 approval_id │                  │
    │                  │                     │ console.send(ApprovalRequested)    │
    │                  │                     │─────────────────>│ 打印:           │
    │                  │                     │                  │ [审批] root 请求 │
    │                  │                     │                  │ delete_file(...) │
    │                  │                     │  future 等待     │ /approve a3f9    │
    │                  │                     │  (wait_for 超时) │ /deny a3f9       │
    │                  │                     │                  │<── /approve a3f9 │
    │                  │                     │<── resolve(a3f9,✓) 命令循环路由     │
    │                  │<── approved ────────│                  │                  │
    │                  │ Resilience 执行（retry+timeout）       │                  │
    │<── ToolResult ───│                     │                  │                  │
```

关键决策：

1. **approval_id**：短 id（4 位 hex，如 `a3f9`）。同一时刻 pending 数量极少，
   碰撞时重生成。
2. **审批期间 agent 状态**：编排器在 `await` 中挂起，事件循环照常服务其他
   agent 与 console 命令循环——主循环不冻结。
3. **审批超时**：`wait_for(future, timeout=policy.approval_timeout)`，
   超时视为拒绝，返回 `ToolResult(success=False, error="approval timeout")`。
4. **审批结果落盘**：裁决（approved/denied/timeout + 工具名 + 参数摘要）
   写入 SessionLog metadata，resume 后可追溯。崩溃恢复时不恢复 pending
   状态——被中断的调用由现有 `interrupted_at` 机制处理。
5. **多 agent 并发审批**：`/approve` 只带 id 不带 pid，id 全局唯一，
   天然支持并发。未知/已裁决 id → `CommandError("无此审批请求或已裁决")`。
6. **kill 与 pending 审批**：agent 被 kill 时 orchestrator task 取消，
   `wait_for` 抛 `CancelledError` → 治理层在 finally 中让 broker 清理
   pending future，console 推送"审批已取消"提示。

## 四、策略 API（代码注册式）

```python
# harness/core/governance/policy.py

@dataclass
class RetryPolicy:
    max_attempts: int = 1           # 1 = 不重试
    backoff: str = "exponential"    # "fixed" | "exponential"
    base_delay: float = 0.5         # 秒
    retry_on: tuple = ("timeout", "exception")  # 哪些失败类别可重试

@dataclass
class ToolPolicy:
    timeout: float = 60.0           # 单次执行超时（秒）
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    gate: bool = False              # True = 需人工审批
    approval_timeout: float = 300.0 # 审批等待超时（秒）
    executor: str = "thread"        # "thread"(to_thread) | "direct"(事件循环内直接调)

class PolicyRegistry:
    def register(self, pattern: str, policy: ToolPolicy) -> None:
        # pattern 支持 fnmatch 通配："delete_*"、"mcp_fs_*"、"*"
    def set_default(self, policy: ToolPolicy) -> None
    def lookup(self, tool_name: str) -> ToolPolicy:
        # 匹配顺序：精确名 > 通配（注册顺序后者优先）> default > 内置默认
```

注册入口（用户装配代码 / workflow 脚本中）：

```python
kernel.policy_registry.register("delete_file", ToolPolicy(gate=True, timeout=10))
kernel.policy_registry.register("mcp_*", ToolPolicy(
    timeout=30, retry=RetryPolicy(max_attempts=3)))
kernel.policy_registry.set_default(ToolPolicy(timeout=60))
```

内置默认（零配置行为）：`ToolPolicy(timeout=60, max_attempts=1, gate=False)`，
加 runtime tools 的 `executor="direct"` 预注册。

**registry 挂 Kernel 而非 DI 容器**：策略是进程级运维决策（不是 agent 装配
决策）；Kernel 是进程级单例，且 console 命令循环也在 Kernel——后续加
`/policy` 查看命令时路径最短。接线路径：AgentRuntime 持有 `self._kernel`，
`_init_orchestrator` 时从 kernel 取 `policy_registry` / `approval_broker`
传入编排器构造参数。

## 五、故障语义矩阵

治理层把所有出口收敛为 `ToolResult`，编排器主循环永不因工具故障异常退出：

| 场景 | 治理层行为 | LLM 看到的 ToolResult | 重试？ |
|---|---|---|---|
| 正常返回 | 透传 | 原始结果 | — |
| 执行超时（wait_for） | 取消等待，计 1 次失败 | `success=False, error="tool 'X' timeout after 60s (attempt N/M)"` | 若 `timeout ∈ retry_on` 且有余量 |
| Provider 抛异常 | 捕获，计 1 次失败 | `success=False, error="TypeError: ..."` | 若 `exception ∈ retry_on` |
| Provider 返回 `success=False` | 透传（业务失败≠故障） | 原始结果 | **不重试** |
| Gate 拒绝 | 不执行 | `success=False, error="denied by operator"` | 否 |
| 审批超时 | 不执行 | `success=False, error="approval timeout"` | 否 |
| 重试耗尽 | 最后一次错误原样返回 | 同上，attempt=M/M | — |
| 工具不存在 | 透传现有 KeyError → 编排器现有分支处理 | 现有行为不变 | 否 |
| Agent 被 kill（task cancel） | `CancelledError` 向上传播（**不吸收**），finally 清理 broker pending | —（编排器终止流程） | — |

两个刻意设计：

1. **`CancelledError` 不吸收**：kill 语义必须能打断工具执行，否则 `/kill`
   对卡死的 agent 失效。治理层只在 finally 里做 broker 清理，然后 re-raise。
2. **`success=False` 不重试**：区分传输层故障（超时/异常，可能瞬时）与
   业务层失败（工具明确拒绝）。否则 LLM 传错参数导致的失败会被无谓重试。

**超时取消的局限**（明确告知使用者）：`asyncio.to_thread` 的线程无法被真正
杀死，超时后线程在后台继续运行直到自然结束。治理层保证的是**主循环不再
等待**（故障不传导），不保证线程回收。MCP 类工具的下次调用是新调用，
不受影响。

## 六、测试策略

复用现有测试基建（`tests/` 下 fake 组件模式），新增 `tests/governance/`：

| 测试文件 | 覆盖点 |
|---|---|
| `test_policy_registry.py` | 精确/通配/默认三级匹配优先级；后注册覆盖；lookup 永不返回 None |
| `test_resilience.py` | 超时吸收（fake 工具 sleep 超过 timeout）；重试次数与退避；`success=False` 不重试；异常吸收；`CancelledError` 穿透 |
| `test_gate.py` | gate=True 触发 ApprovalRequested；approve → 执行；deny → 错误结果；审批超时 → 错误结果；重复 resolve 幂等；kill 清理 pending |
| `test_layer_integration.py` | 编排器走通 governance 路径；无策略注册时与现状等价（透传性）；runtime tools executor=direct 不经线程 |
| `test_console_commands.py` | `/approve` `/deny` 解析；无效 id → CommandError；ApprovalRequested 渲染 |

回归保障：现有 `test_tool_router.py`、`test_e2e_tool_flow.py`、
`test_mcp_adapter.py` 全部不动且必须通过。E2E 用 slow_tool + gated_tool
的 fake provider 跑完整 CLI 会话。
