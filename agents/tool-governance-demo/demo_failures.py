"""面试演示：工具超时 + 工具抛异常，agent 如何被框架兜底并继续。

真实 LLM 驱动 agent「failure_demo」依次调用三个工具：
  slow_query（卡死→超时） → unreliable_divide（抛异常） → safe_echo（成功）

本脚本不打印任何旁白，只透传框架自身产生的事件流：
  ToolCallEvent   —— 工具被调用（工具名 + 参数）
  ToolResultEvent —— 工具结果（success / error / duration_ms）
  TextEvent       —— agent 的文本输出

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
from harness.runtime.types import AgentOutput


class _DemoConsole:
    """透传框架事件流：打印 AgentOutput，过滤冗长的 ThinkingEvent。"""

    async def send(self, event):
        if isinstance(event, AgentOutput):
            if event.content.startswith("[ThinkingEvent]"):
                return  # 思考过程冗长，与演示主题无关
            print(f"[{event.pid}] {event.content}")

    async def receive(self):
        while True:
            await asyncio.sleep(3600)


async def main():
    console = _DemoConsole()
    kernel = Kernel(console)

    script = str(Path(__file__).parent / "failures_workflow.py")
    kernel.spawn_from_script(script, parent=None)

    # 等待 agent 完成（oneshot 自动 FINISHED），超时 120s
    deadline = time.time() + 120
    while not kernel.all_finished() and time.time() < deadline:
        await asyncio.sleep(0.5)

    if not kernel.all_finished():
        print("[超时] agent 未在 120s 内完成")
        sys.exit(1)

    for pid, rt in kernel.runtime_table.items():
        if rt.error:
            print(f"[{pid}] error={rt.error}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
