"""Debate workflow: two agents (pro/con) debate a motion, then end.

Mode B 启动方式:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('examples/debate_workflow.py')
"
"""

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
from harness.runtime.decorators import agent, subscribe

MOTION = "Python 应该强制使用类型注解"


def _assemble_agent(name: str) -> Harness:
    """Assemble a single debate agent with LLM + memory."""
    container = DIContainer()

    memory = MdMemory(path=f"./memory/debate/{name}")
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
    "pro",
    entry_prompt=(
        f"你参加一场辩论，辩题是：「{MOTION}」。你代表**正方（赞成）**。\n\n"
        "重要规则：\n"
        "- 你的对手 'con' 已经存在，你们通过 subscribe 机制互相收听\n"
        "- 你**不需要也不应该**使用 spawn_workflow 或 end_workflow 工具\n"
        "- 你可以用 list_agents 查看对方状态\n"
        "- 用 finish_agent 工具来结束自己\n\n"
        "辩论流程：\n"
        "1. 你先发表开场陈词，阐述正方观点\n"
        "2. 对方会反驳，你收到消息后继续辩论\n"
        "3. 辩论 2-3 轮后发表最后总结，然后调用 finish_agent\n\n"
        "现在请发表你的开场陈词。"
    ),
)
def assemble_pro():
    return _assemble_agent("pro")


@agent(
    "con",
    entry_prompt=(
        f"你参加一场辩论，辩题是：「{MOTION}」。你代表**反方（反对）**。\n\n"
        "重要规则：\n"
        "- 你的对手 'pro' 已经存在，你们通过 subscribe 机制互相收听\n"
        "- 你**不需要也不应该**使用 spawn_workflow 或 end_workflow 工具\n"
        "- 你可以用 list_agents 查看对方状态\n"
        "- 用 finish_agent 工具来结束自己\n\n"
        "辩论流程：\n"
        "1. 等听到 pro 的开场陈词后再开始反驳\n"
        "2. 每轮针对对手观点进行反驳，不要重复已说过的内容\n"
        "3. 辩论 2-3 轮后发表最后总结，然后调用 finish_agent\n\n"
        "现在请等待对手发言。"
    ),
)
def assemble_con():
    return _assemble_agent("con")


# 双向订阅 —— 双方都能看到对方的发言
subscribe("pro").to("con")
subscribe("con").to("pro")
