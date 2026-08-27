"""Tool Governance Demo workflow — 演示工具治理层的 Gate 审批 + 弹性策略。

启动方式（Mode B）:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/tool-governance-demo/workflow.py')
"

运行后，agent 会尝试调用 shell 工具（高风险，gate=True），
前台会显示审批请求，输入 /approve <id> 批准，或 /deny <id> 拒绝。

治理策略在本文件顶层注册（进程级单例 policy_registry）：
- shell       → gate=True（需人工审批）+ 单次超时 30s
- write_file  → gate=True（写文件同样审批）
- read_file   → timeout=10 + 重试 2 次（传输层故障可重试）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 sys.path（使得脚本可以在任意位置被加载）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)
from harness.runtime.decorators import agent

# ── 工具治理策略注册 ──────────────────────────────────────────────
# 进程级单例：在 agent 启动（_start_agent）之前由 exec_module 执行，
# 因此工具首次被调用时策略已就绪。
from harness.core.governance.policy import (
    policy_registry,
    ToolPolicy,
    RetryPolicy,
)

# 高风险工具：shell 执行任意命令 → 需人工审批
policy_registry.register(
    "shell",
    ToolPolicy(gate=True, timeout=30),
)

# 写文件同样审批
policy_registry.register(
    "write_file",
    ToolPolicy(gate=True),
)

# 读文件：低风险，但给一个 10s 超时 + 2 次重试（传输层故障可重试）
policy_registry.register(
    "read_file",
    ToolPolicy(
        timeout=10,
        retry=RetryPolicy(max_attempts=2, backoff="fixed", base_delay=0.5),
    ),
)


def _assemble_agent(name: str) -> Harness:
    """装配一个带 LLM + 内置工具（read_file/write_file/shell）的 agent。"""
    container = DIContainer()

    memory = MdMemory(path=f"./memory/tool_governance_demo/{name}")
    container.register(MemoryBackend, memory)
    container.register(InputAdapter, object())  # 由 KernelBridgeAdapter 覆盖
    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=100, memory=memory),
    )
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(SystemToolProvider, DefaultSystemToolProvider())

    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "governance_demo",
    entry_prompt=(
        "你是 subagent「governance_demo」，任务：\n"
        "1. 用 shell 工具执行命令：echo \"governance layer works\"\n"
        "2. 把 shell 命令的输出内容原样复述给你的父 agent\n"
        "3. 调用 finish_agent 工具结束自己\n\n"
        "注意：shell 工具是高危操作，会触发人工审批，请耐心等待审批通过。"
        "现在请立即执行。"
    ),
    metadata={"role": "governance_demo", "task": "演示 shell gate 审批"},
)
def assemble_governance_demo():
    return _assemble_agent("governance_demo")
