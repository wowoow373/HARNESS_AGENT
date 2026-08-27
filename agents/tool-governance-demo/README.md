# Tool Governance Demo

演示 Harness 工具治理层（Tool Governance Layer）的 **Gate 审批** 与 **超时/重试弹性策略**。

## 它做了什么

一个 subagent `governance_demo`，任务是用 `shell` 工具执行 `echo "governance layer works"`。
`shell` 是**高风险工具**（可执行任意命令），本示例将其注册为 `gate=True`，
因此 agent 调用它会触发前台人工审批——批准后才真正执行。

## 治理策略（在 [workflow.py](workflow.py) 顶层注册）

| 工具 | 策略 | 说明 |
|---|---|---|
| `shell` | `gate=True, timeout=30` | 高风险，需人工审批；单次执行超时 30s |
| `write_file` | `gate=True` | 写文件同样审批 |
| `read_file` | `timeout=10, retry×2` | 低风险；超时/异常可重试 2 次 |

```python
from harness.core.governance.policy import policy_registry, ToolPolicy, RetryPolicy

policy_registry.register("shell", ToolPolicy(gate=True, timeout=30))
policy_registry.register("write_file", ToolPolicy(gate=True))
policy_registry.register("read_file", ToolPolicy(
    timeout=10,
    retry=RetryPolicy(max_attempts=2, backoff="fixed", base_delay=0.5),
))
```

`policy_registry` 是**进程级单例**，策略是运维决策而非 agent 装配决策。
你在任意装配代码 / workflow 脚本顶层注册一次即可全局生效。

## 运行（交互式，看审批）

需要 `harness/config/.env` 里配置好真实 LLM（`base_url` / `api-key` / `model`）。

```bash
python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
Runtime(CliConsole(mode='mode_b')).run_from_script('agents/tool-governance-demo/workflow.py')
"
```

运行后前台会显示审批请求：

```text
[审批] governance_demo 请求执行工具 shell
  参数: {"command": "echo \"governance layer works\""}
  /approve a3f9 批准    /deny a3f9 拒绝
```

输入 `/approve a3f9` 批准（工具执行），或 `/deny a3f9` 拒绝（agent 收到错误结果）。
审批默认 300s 超时，超时视为拒绝。

## 自动审批的 E2E 测试

```bash
python agents/tool-governance-demo/test_e2e.py
```

该脚本用真实 LLM 驱动完整链路，并在收到审批请求时**自动批准**，
验证：真实 LLM → shell gate 审批 → 工具执行 → agent 完成 的端到端流程。

## 治理层能力速览

| 能力 | 触发方式 | 结果 |
|---|---|---|
| Gate 审批 | `ToolPolicy(gate=True)` | 前台 `/approve` `/deny`，审批超时视为拒绝 |
| 超时 | `ToolPolicy(timeout=N)` | 超时 → `ToolResult(success=False)`，不传导到主循环 |
| 重试 | `RetryPolicy(max_attempts=N)` | 传输层故障（超时/异常）按退避重试；业务失败（`success=False`）不重试 |
| 故障隔离 | 默认行为 | 所有工具故障收敛为错误结果，agent 主循环永不崩溃 |
