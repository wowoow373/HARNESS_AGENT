# Tool Governance Demo

演示 Harness **工具治理层（Tool Governance Layer）**：超时、异常兜底、Gate 审批。

**前置条件**：`harness/config/.env` 已配置真实 LLM（`base_url` / `api-key` / `model`）。
所有命令在**项目根目录**运行。

## 文件与脚本

| 文件 | 类型 | 运行命令 | 演示内容 |
|---|---|---|---|
| [demo_failures.py](demo_failures.py) | 驱动脚本 | `python agents/tool-governance-demo/demo_failures.py` | **超时 + 异常兜底**（单 agent workflow，无需交互） |
| [interactive_demo.py](interactive_demo.py) | 交互脚本 | `python agents/tool-governance-demo/interactive_demo.py` | **完整用户对话**（Mode A），用户输入、agent 调工具、失败兜底、继续对话 |
| [workflow.py](workflow.py) | workflow | `python main.py workflow agents/tool-governance-demo/workflow.py` | **Gate 审批（交互式）**：shell 高危工具需前台 `/approve` |
| [test_e2e.py](test_e2e.py) | e2e 脚本 | `python agents/tool-governance-demo/test_e2e.py` | **Gate 审批（自动批准）**：完整链路，无需人工输入 |
| [failures_workflow.py](failures_workflow.py) | workflow | （被 `demo_failures.py` 内部加载） | 三个演示工具 + agent 声明 |
| [demo_tools.py](demo_tools.py) | 共享模块 | （被上面 import） | 三个演示工具 + `register_policies()` |

> 面试推荐顺序：先跑 **demo_failures.py**（无需交互，最快最直观），再跑 **interactive_demo.py**（展示真实用户对话）。

---

## 演示一：超时 + 异常兜底（demo_failures.py）

```bash
python agents/tool-governance-demo/demo_failures.py
```

真实 LLM 驱动 agent 依次调用三个工具：

| 工具 | 故障 | 治理层兜底 |
|---|---|---|
| `slow_query` | `sleep(10)` 一直未响应 | `timeout=3` 主动判定超时 |
| `unreliable_divide` | `a/b`，b=0 抛真实 `ZeroDivisionError` | 被动捕获异常 |
| `safe_echo` | 正常 | agent 换它完成任务 |

**预期输出**（框架原生事件流）：

```text
[failure_demo] [ThinkingEvent]  ... "我先并行调用前两个工具"
[failure_demo] [ToolCallEvent]  slow_query(query='sales')
[failure_demo] [ToolResultEvent] success=False, error="tool 'slow_query' timeout after 3s", duration_ms=3001
[failure_demo] [ToolCallEvent]  unreliable_divide(a=100, b=0)
[failure_demo] [ToolResultEvent] success=False, error='ZeroDivisionError: division by zero'
[failure_demo] [ThinkingEvent]  ... "两个都失败了，我继续第三步"
[failure_demo] [ToolCallEvent]  safe_echo(...)
[failure_demo] [ToolResultEvent] success=True
[failure_demo] 三个步骤已全部执行完毕：查询超时、计算除零、结论已发送...
```

**面试讲点**：
- 超时是框架**主动**判定的（工具卡住，既不返回也不报错，只能靠计时器）
- 异常是工具**自己抛出**、框架**被动**捕获的（Python 异常沿调用栈冒泡，`except` 接住）
- 两者都收敛成 `ToolResult(success=False)` 喂回 LLM，agent 换工具继续，主循环不崩溃

---

## 演示二：完整用户对话（interactive_demo.py）

```bash
python agents/tool-governance-demo/interactive_demo.py
```

Mode A 交互式对话，用户在命令行输入，agent 决策、调工具、失败兜底、继续对话：

```text
[系统] Runtime 启动
> 帮我查一下今天的销售数据
[root] [ThinkingEvent] "用户要查销售数据，我用 slow_query"
[root] [ToolCallEvent] slow_query(query='查询今天的销售数据')
[root] [ToolResultEvent] success=False, error="timeout after 3s"
[root] [ThinkingEvent] "超时了，换个参数再试"          ← agent 自己重试
[root] [ToolCallEvent] slow_query(...) → 超时 → 再重试
[root] [ThinkingEvent] "还是超时，换 safe_echo 告诉用户"  ← agent 换工具
[root] 已经尝试了三次查询...均超时...建议稍后重试          ← 自然回复
```

输入 `/exit` 退出。

**恢复上次会话**（`--resume` 接 `.harness/sessions/` 下的 conv_id）：

```bash
python agents/tool-governance-demo/interactive_demo.py --resume <conv_id>
# 例：python agents/tool-governance-demo/interactive_demo.py --resume conv-20260827-214950-7f8d
```

恢复后 agent 带着完整上下文继续（重放历史），不是从零开始。

---

## 演示三：Gate 审批（交互式）

```bash
python main.py workflow agents/tool-governance-demo/workflow.py
```

agent 尝试调用 `shell`（高危工具，`gate=True`），前台弹出审批请求：

```text
[审批] governance_demo 请求执行工具 shell
  参数: {"command": "echo \"governance layer works\""}
  /approve a3f9 批准    /deny a3f9 拒绝
```

输入 `/approve a3f9` 批准（工具执行），或 `/deny a3f9` 拒绝（agent 收到错误结果）。
审批默认 300s 超时，超时视为拒绝。

**面试讲点**：Gate 在**执行前**拦截高危工具，批准才执行；拒绝/超时都不执行工具，且不会被重试。

---

## 演示四：Gate 审批（自动批准，无需输入）

```bash
python agents/tool-governance-demo/test_e2e.py
```

与演示三同一条链路，但收到审批请求时自动批准。适合跑给面试官看「完整链路」而不打断演示。

---

## 治理策略（在 [demo_tools.py](demo_tools.py) 的 `register_policies()` 里注册）

```python
from harness.core.governance.policy import policy_registry, ToolPolicy, RetryPolicy

# 超时：slow_query 3s 超时
policy_registry.register("slow_query", ToolPolicy(timeout=3))

# 异常兜底：unreliable_divide 给 5s 超时兜底（异常本身默认被捕获）
policy_registry.register("unreliable_divide", ToolPolicy(timeout=5))

# Gate：shell 高危工具需审批（workflow.py 里注册）
policy_registry.register("shell", ToolPolicy(gate=True, timeout=30))

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
