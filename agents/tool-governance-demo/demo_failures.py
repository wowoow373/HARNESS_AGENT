"""面试演示：工具抛异常 + 工具一直未响应，agent 如何被兜底并继续。

真实 LLM 驱动 agent「failure_demo」依次调用三个工具：
  1. slow_query        → 卡住 10s，治理层 3s 超时兜底
  2. unreliable_divide → 抛异常，治理层捕获兜底
  3. safe_echo         → 正常，agent 换它完成任务

观察点：agent 全程不崩溃、不被阻塞，最终用第三个工具完成任务。

运行:
    python agents/tool-governance-demo/demo_failures.py
"""

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.runtime.kernel import Kernel
from harness.runtime.types import AgentFinished


class _DemoConsole:
    """不读 stdin；只收集 AgentFinished 事件。"""

    def __init__(self):
        self.finished = []

    async def send(self, event):
        if isinstance(event, AgentFinished):
            self.finished.append(event)

    async def receive(self):
        while True:
            await asyncio.sleep(3600)


async def main():
    print("=" * 60)
    print("  工具治理层故障兜底演示（真实 LLM）")
    print("  预期：slow_query 超时 → unreliable_divide 抛异常 → safe_echo 成功")
    print("=" * 60)
    print()

    console = _DemoConsole()
    kernel = Kernel(console)

    script = str(Path(__file__).parent / "failures_workflow.py")
    result = kernel.spawn_from_script(script, parent=None)
    pids = [a["pid"] for a in result["agents"]]
    print(f"已 spawn: {pids}")
    print()

    # 等待 agent 完成（oneshot 自动 FINISHED），超时 120s
    deadline = time.time() + 120
    while not kernel.all_finished() and time.time() < deadline:
        await asyncio.sleep(0.5)

    print()
    print("=" * 60)
    print("  agent 最终输出")
    print("=" * 60)
    for pid, rt in kernel.runtime_table.items():
        print(f"\n[{pid}] state={rt.state.value}, rounds={rt.round_count}, "
              f"error={rt.error}")
        if rt.last_output:
            print(f"{rt.last_output}")

    print()
    print("=" * 60)
    failures = []
    if not kernel.all_finished():
        failures.append("agent 未在超时内完成")
    for pid, rt in kernel.runtime_table.items():
        if rt.error:
            failures.append(f"agent '{pid}' 有错误: {rt.error}")

    if failures:
        print("❌ 演示失败:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ 演示完成：agent 未被故障阻塞，换工具继续并完成任务")
        print()
        print("  关键点：")
        print("    - slow_query 卡住 → 治理层 3s 主动判定超时，返回错误结果")
        print("    - unreliable_divide 抛异常 → 治理层被动捕获，返回错误结果")
        print("    - agent 拿到两个错误后，换 safe_echo 正常完成任务")
        print("    - 全程异常不穿透到 agent 主循环，agent 状态始终正常")


if __name__ == "__main__":
    asyncio.run(main())
