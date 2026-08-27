"""Tool Governance E2E 测试 — 真实 LLM + Gate 审批链路。

端到端验证：
  1. 真实 LLM 驱动 agent 调用 shell 工具（gate=True）
  2. 触发 ApprovalRequested 事件 → 自动批准
  3. 工具执行成功 → agent 复述输出 → finish_agent → FINISHED

审批在本测试中由 _AutoApproveConsole 自动批准（收到审批请求即 resolve）。
生产交互式场景下，审批由用户输入 /approve <id> 完成。

运行:
    python agents/tool-governance-demo/test_e2e.py
"""

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.runtime.kernel import Kernel
from harness.runtime.types import ApprovalRequested, AgentFinished


class _AutoApproveConsole:
    """不读 stdin 的 console；收到审批请求即自动批准。"""

    def __init__(self):
        self.events = []
        self.approval_requests = 0
        self.approvals_resolved = 0
        self.approval_pids = []  # 记录每个审批请求的发起 pid
        self.kernel = None  # 后置注入

    async def send(self, event):
        self.events.append(event)
        if isinstance(event, ApprovalRequested):
            self.approval_requests += 1
            self.approval_pids.append(event.pid)
            print(f"  [审批请求] {event.pid} → {event.tool_name} "
                  f"(id={event.approval_id})")
            print(f"    参数: {event.arguments}")
            # 自动批准
            if self.kernel is not None:
                if self.kernel.approval_broker.resolve(event.approval_id, True):
                    self.approvals_resolved += 1
                    print(f"    [自动批准] {event.approval_id} ✓")
                else:
                    print(f"    [批准失败] {event.approval_id} 已裁决或不存在")
        elif isinstance(event, AgentFinished):
            print(f"  [AgentFinished] {event.pid} "
                  f"error={event.error}")

    async def receive(self):
        # 本测试不读 stdin，永不返回
        while True:
            await asyncio.sleep(3600)


async def main():
    console = _AutoApproveConsole()
    kernel = Kernel(console)
    console.kernel = kernel  # 后置注入，供自动审批使用

    script = str(Path(__file__).parent / "workflow.py")
    result = kernel.spawn_from_script(script, parent=None)
    pids = [a["pid"] for a in result["agents"]]
    print(f"已 spawn: {pids}")

    # 等待 agent 完成（oneshot 自动 FINISHED），超时 120s
    deadline = time.time() + 120
    while not kernel.all_finished() and time.time() < deadline:
        await asyncio.sleep(0.5)

    print(f"\n=== 结果 ===")
    for pid, rt in kernel.runtime_table.items():
        print(f"  {pid}: state={rt.state.value}, rounds={rt.round_count}, "
              f"error={rt.error}")
        if rt.last_output:
            print(f"    output: {rt.last_output[:200]}")

    # ── 断言 ──
    failures = []

    if console.approval_requests < 1:
        failures.append("未触发任何审批请求（shell gate 未生效？）")
    if console.approvals_resolved < 1:
        failures.append("审批请求未被成功批准")
    if not console.approval_pids or not console.approval_pids[0]:
        failures.append("审批请求缺少发起 pid（governance layer pid 接线异常）")
    if not kernel.all_finished():
        failures.append("agent 未在超时内完成（可能 LLM 未调用工具）")
    for pid, rt in kernel.runtime_table.items():
        if rt.error:
            failures.append(f"agent '{pid}' 有错误: {rt.error}")

    print(f"\n=== 汇总 ===")
    print(f"  审批请求数: {console.approval_requests}")
    print(f"  自动批准数: {console.approvals_resolved}")
    print(f"  全部完成:   {kernel.all_finished()}")

    if failures:
        print(f"\n❌ E2E 失败（{len(failures)}）:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\n✅ 工具治理层 E2E 通过：真实 LLM → shell gate 审批 → 执行 → 完成")


if __name__ == "__main__":
    asyncio.run(main())
