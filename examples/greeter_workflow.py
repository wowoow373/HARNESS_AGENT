"""Greeter workflow: 一个 subagent 回复「你好」后结束。

启动方式（由父 agent 通过 spawn_workflow 工具加载）:
    spawn_workflow(script_path="examples/greeter_workflow.py")
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 sys.path（使得脚本可以在任意位置被加载）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
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


def _assemble_agent(name: str) -> Harness:
    """装配一个带 LLM + 记忆的 agent。"""
    container = DIContainer()

    memory = MdMemory(path=f"./memory/greeter/{name}")
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
    "greeter",
    entry_prompt=(
        "你是 subagent「greeter」，任务非常简单：\n"
        "1. 请向你的父 agent 回复两个字：「你好」\n"
        "2. 回复完成后，调用 finish_agent 工具结束自己\n\n"
        "现在请立即执行。"
    ),
    metadata={"role": "greeter", "task": "say hello"},
)
def assemble_greeter():
    return _assemble_agent("greeter")
