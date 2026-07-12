"""customer_service_workflow.py — 6-agent topology for multi-hop QA customer service.

Launch:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/customer-service/customer_service_workflow.py')
"
Then type: /talk router 改签规则是什么？
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CS_PATH = str(Path(__file__).resolve().parent)
if _CS_PATH not in sys.path:
    sys.path.insert(0, _CS_PATH)

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    AsyncInputAdapter,
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
from harness.runtime.decorators import agent, subscribe

from shared.retriever import InMemoryRetriever
from agents.router.adapter import RouterAdapter
from agents.router.assembler import RouterAssembler
from agents.direction.adapter import DirectionAdapter
from agents.direction.assembler import DirectionAssembler
from agents.evidence.adapter import EvidenceAdapter
from agents.evidence.assembler import EvidenceAssembler
from agents.validation.adapter import ValidationAdapter
from agents.validation.assembler import ValidationAssembler
from agents.task_agent.assembler import TaskAssembler
from agents.fallback.assembler import FallbackAssembler


# ═══════════════════════════════════════════════════════════════════════════
# Agent assembly functions
# ═══════════════════════════════════════════════════════════════════════════

@agent(
    "router",
    entry_prompt="你是客服系统入口路由。等待用户消息...",
    metadata={"role": "入口意图识别"},
)
def assemble_router():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, RouterAdapter(memory=memory))
    container.register(ContextAssembler, RouterAssembler())
    guide_path = Path(__file__).parent / "AGENTS_router.md"
    if guide_path.exists():
        container.register(GuideProvider, FileGuideProvider(paths=[str(guide_path)]))
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "direction",
    entry_prompt="你是方向生成Agent。等待任务...",
    metadata={"role": "方向生成"},
)
def assemble_direction():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, DirectionAdapter(memory=memory))
    container.register(ContextAssembler, DirectionAssembler(K=2))
    # ★ Worker agent — no tools needed (processes structured tasks only)
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "evidence",
    entry_prompt="你是证据锚定Agent。等待任务...",
    metadata={"role": "证据锚定"},
)
def assemble_evidence():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    # MVP: load corpus from data/ or use empty
    try:
        import json
        corpus_path = Path(__file__).parent / "data" / "corpus.json"
        if corpus_path.exists():
            with open(corpus_path) as f:
                raw = json.load(f)
                corpus = [(item["title"], item["sentences"]) for item in raw]
        else:
            corpus = []
    except Exception:
        corpus = []
    retriever = InMemoryRetriever(corpus)
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, EvidenceAdapter(memory=memory))
    container.register(ContextAssembler, EvidenceAssembler(
        retriever=retriever, memory=memory, top_k=5,
    ))
    # ★ Worker agent — no tools needed
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "validation",
    entry_prompt="你是全局校验Agent。等待任务...",
    metadata={"role": "全局校验"},
)
def assemble_validation():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/shared")
    container.register(MemoryBackend, memory)
    container.register(AsyncInputAdapter, ValidationAdapter(memory=memory))
    container.register(ContextAssembler, ValidationAssembler(memory=memory))
    # ★ Worker agent — no tools needed
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "task_agent",
    entry_prompt="你是业务办理助手。等待任务...",
    metadata={"role": "业务办理占位"},
)
def assemble_task():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/task")
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, TaskAssembler())
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "fallback",
    entry_prompt="你是异常兜底助手。等待任务...",
    metadata={"role": "异常兜底占位"},
)
def assemble_fallback():
    container = DIContainer()
    memory = MdMemory(path="./memory/customer_service/fallback")
    container.register(MemoryBackend, memory)
    container.register(ContextAssembler, FallbackAssembler())
    container.register(SystemToolProvider, DefaultSystemToolProvider())
    container.register(Sensor, LoggingSensor(memory=memory))
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


# ═══════════════════════════════════════════════════════════════════════════
# Subscription topology
# ═══════════════════════════════════════════════════════════════════════════

subscribe("router").to("user")
subscribe("task_agent").to("router")
subscribe("fallback").to("router")

# Virtual subscriptions: force direction/evidence/validation into continuous mode.
# Framework auto-detects mode based on has_subscriptions; without these they'd
# be oneshot and exit after one round, breaking the multi-hop loop.
subscribe("direction").to("user")
subscribe("evidence").to("user")
subscribe("validation").to("user")
