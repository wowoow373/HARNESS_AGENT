"""Runtime Mode A — 多轮交互对话 Agent。

使用 Harness Agent Template Runtime 层启动交互式 Agent。
支持 /agents, /kill, /end, /exit, /talk 等系统命令。

与 minimal_agent.py 的区别：
- 使用 Runtime 层而非直接 harness.run()
- 支持多 Agent 管理（spawn workflow 等）
- 用户输入通过 SystemConsole（/ 前缀命令可用）

用法::

    python examples/runtime_agent.py

    > 你好
    [root] 你好！有什么我可以帮你的吗？
    > /agents
    [系统] Agents (1):
      PID          STATE         MODE        ROUNDS  PARENT
      ------------ ------------- ----------- ------- ------------
      root         running       continuous  1       -
    > /exit
    [系统] Runtime 停止
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 将项目根目录加入 sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Harness 框架
from harness.di import Harness
from harness.core.container import DIContainer

# 接口
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)

# 组件
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)

# Runtime 层
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("runtime_agent")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    """启动 Runtime Mode A 交互式 Agent。"""
    print("=" * 50)
    print("  Harness Runtime — 多 Agent 交互模式")
    print("=" * 50)
    print()
    print("系统命令:")
    print("  /agents         查看所有 Agent 状态")
    print("  /kill <pid>     终止指定 Agent")
    print("  /end <flag>     终止整个 Workflow")
    print("  /talk <pid> <m> 向指定 Agent 发消息")
    print("  /exit           优雅退出")
    print("  Ctrl+D          等同于 /exit")
    print()

    # ── DI 容器装配 ──────────────────────────────────────────────────────
    container = DIContainer()

    memory = MdMemory(path="./memory")
    container.register(MemoryBackend, memory)

    container.register(InputAdapter, CliAdapter())

    guide_paths = []
    for candidate in ["AGENTS.md", "CLAUDE.md"]:
        if os.path.exists(candidate):
            guide_paths.append(candidate)
    if guide_paths:
        container.register(GuideProvider, FileGuideProvider(guide_paths))

    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=50, memory=memory),
    )

    container.register(Sensor, LoggingSensor(memory=memory))

    container.register(SystemToolProvider, DefaultSystemToolProvider())

    # LLM 适配器 — 自动从 .env 读取配置
    llm = MinimalLLMAdapter()

    harness = Harness.from_container(container, call_llm=llm)

    # ── Runtime 启动 ──────────────────────────────────────────────────────
    console = CliConsole(mode="mode_a")
    runtime = Runtime(console)

    try:
        runtime.run(harness)
    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")


if __name__ == "__main__":
    main()
