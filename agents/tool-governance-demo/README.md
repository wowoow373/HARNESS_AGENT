# Tool Governance Demo

演示 Harness **工具治理层（Tool Governance Layer）**：Gate 审批、超时、异常兜底。

**前置条件**：`harness/config/.env` 已配置真实 LLM（`base_url` / `api-key` / `model`）。
所有命令在**项目根目录**运行。

## 脚本清单 & 运行命令（面试速查）

| # | 脚本 | 运行命令 | 演示内容 |
|---|---|---|---|
| 1 | [demo_failures.py](demo_failures.py) | `python agents/tool-governance-demo/demo_failures.py` | **超时 + 异常兜底**：工具卡死 / 工具抛异常，agent 换工具继续 |
| 2 | [workflow.py](workflow.py) | `python main.py workflow agents/tool-governance-demo/workflow.py` | **Gate 审批（交互式）**：shell 高危工具需前台 `/approve` |
| 3 | [test_e2e.py](test_e2e.py) | `python agents/tool-governance-demo/test_e2e.py` | **Gate 审批（自动审批）**：同上，但自动批准，无需人工输入 |

> 面试推荐顺序：先跑 **#1**（无需交互，最快最直观），再跑 **#2**（展示前台审批交互）。

---

## 演示一：超时 + 异常兜底（推荐先看）

```bash
python agents/tool-governance-demo/demo_failures.py
```

真实 LLM 驱动 agent 依次调用三个工具：

| 工具 | 故障 | 治理层兜底 |
|---|---|---|
| `slow_query` | `sleep(10)` 一直未响应 | `timeout=3` 主动判定超时 |
| `unreliable_divide` | `raise RuntimeError` 自己抛异常 | 被动捕获异常 |
| `safe_echo` | 正常 | agent 换它完成任务 |

**预期输出**：

```text
    [工具] slow_query 被调用，开始执行（将卡住 10s，治理层 3s 超时）...
    [工具] unreliable_divide 被调用，抛出异常...
    [工具] safe_echo 执行成功

[failure_demo] state=finished, error=None
... slow_query 超时、unreliable_divide 抛异常，两者都被工具治理层拦截
而未中断 agent，最终 safe_echo 正常回显成功...

✅ 演示完成：agent 未被故障阻塞，换工具继续并完成任务
```

**面试讲点**：
- 超时是框架**主动**判定的（工具卡住，既不返回也不报错，只能靠计时器）
- 异常是工具**自己抛出**、框架**被动**捕获的（Python 异常沿调用栈冒泡，`except` 接住）
- 两者都收敛成 `ToolResult(success=False)` 喂回 LLM，agent 换工具继续，主循环不崩溃

---

## 演示二：Gate 审批（交互式）

```bash
python main.py workflow agents/tool-governance-demo/workflow.py
```

agent 会尝试调用 `shell`（高危工具，`gate=True`），前台弹出审批请求：

```text
[审批] governance_demo 请求执行工具 shell
  参数: {"command": "echo \"governance layer works\""}
  /approve a3f9 批准    /deny a3f9 拒绝
```

输入 `/approve a3f9` 批准（工具执行），或 `/deny a3f9` 拒绝（agent 收到错误结果）。
审批默认 300s 超时，超时视为拒绝。

**面试讲点**：Gate 在**执行前**拦截高危工具，批准才执行；拒绝/超时都不执行工具，且不会被重试。

---

## 演示三：Gate 审批（自动审批，无需输入）

```bash
python agents/tool-governance-demo/test_e2e.py
```

与演示二同一条链路，但收到审批请求时自动批准。适合跑给面试官看「完整链路」而不打断演示。

---

## 治理策略（在 [workflow.py](workflow.py) / [failures_workflow.py](failures_workflow.py) 顶层注册）

```python
from harness.core.governance.policy import policy_registry, ToolPolicy, RetryPolicy

# Gate：shell 高危工具需审批
policy_registry.register("shell", ToolPolicy(gate=True, timeout=30))

# 超时：slow_query 3s 超时
policy_registry.register("slow_query", ToolPolicy(timeout=3))

# 重试（可选）：read_file 超时/异常重试 2 次
policy_registry.register("read_file", ToolPolicy(
    timeout=10,
    retry=RetryPolicy(max_attempts=2, backoff="fixed", base_delay=0.5),
))
```

`policy_registry` 是**进程级单例**，策略是运维决策，注册一次全局生效。

## 治理层能力速览

| 能力 | 触发方式 | 结果 |
|---|---|---|
| Gate 审批 | `ToolPolicy(gate=True)` | 前台 `/approve` `/deny`，超时视为拒绝 |
| 超时 | `ToolPolicy(timeout=N)` | 超时 → `success=False`，不传导到主循环 |
| 异常兜底 | 工具 `raise` | 捕获 → `success=False`，不穿透 |
| 重试 | `RetryPolicy(max_attempts=N)` | 传输层故障（超时/异常）可重试；业务失败（`success=False`）不重试 |
| 故障隔离 | 默认行为 | 所有工具故障收敛为错误结果，agent 主循环永不崩溃 |

## 单元测试

```bash
python -m pytest tests/governance/ tests/runtime/test_approval.py tests/runtime/test_approval_commands.py -v
```
