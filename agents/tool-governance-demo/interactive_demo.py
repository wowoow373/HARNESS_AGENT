"""工具治理层交互式演示（Mode A）—— 完整的用户对话 runtime。

与 demo_failures.py 的区别：这是单 agent 交互式对话，用户在命令行输入，
agent 调用工具（可能超时/异常），框架兜底后 agent 继续，用户可接着对话。

运行:
    python agents/tool-governance-demo/interactive_demo.py

示例对话:
    > 帮我查一下今天的销售数据
    ...（agent 调 slow_query → 超时，框架兜底，agent 回复查询失败）
    > 那算一下转化率，100 除以 0
    ...（agent 调 unreliable_divide → ZeroDivisionError，框架兜底）
    > /exit
"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目根加入 sys.path（import harness）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    InputAdapter,
    MemoryBackend,
    ContextAssembler,
    Sensor,
    SystemToolProvider,
)
from harness.interfaces.guide_provider import GuideProvider, GuideContext
from harness.interfaces.types import GuidesBundle
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime

from demo_tools import demo_tools, register_policies


class _DataAssistantGuide:
    """内联 GuideProvider：告诉 agent 它的角色和可用工具。"""

    def get_guides(self, context: GuideContext) -> GuidesBundle:
        return GuidesBundle(identity=(
            "你是数据分析助手。你可以使用以下工具帮助用户完成数据查询和分析：\n"
            "- slow_query：查询销售数据（连接数据库）\n"
            "- unreliable_divide：计算两个数的比值\n"
            "- safe_echo：向用户发送一条消息\n\n"
            "当用户要求查询数据或做分析时，使用这些工具。"
        ))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="工具治理层交互式演示（Mode A，真实 LLM）",
    )
    parser.add_argument(
        "--resume", metavar="CONV_ID", default=None,
        help="恢复指定会话（如 conv-20260827-214950-7f8d）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="配合 --resume 强制接管所有权 / 降级 manifest 校验",
    )
    args = parser.parse_args()

    # 注册治理策略（进程级单例）
    register_policies()

    container = DIContainer()

    memory = MdMemory(path="./memory/interactive_demo")
    container.register(MemoryBackend, memory)
    container.register(InputAdapter, CliAdapter())
    container.register(GuideProvider, _DataAssistantGuide())
    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=50, memory=memory),
    )
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(
        SystemToolProvider,
        DefaultSystemToolProvider(tools=demo_tools(), use_builtins=False),
    )

    harness = Harness.from_container(container, call_llm=MinimalLLMAdapter())

    console = CliConsole(mode="mode_a")
    Runtime(console).run(harness, resume=args.resume, force=args.force)


if __name__ == "__main__":
    main()
