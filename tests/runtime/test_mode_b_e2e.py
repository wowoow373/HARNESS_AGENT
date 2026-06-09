# tests/runtime/test_mode_b_e2e.py

"""Integration tests for Batch 3: Mode B + MessageBus + cascade.

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import os
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentState


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole with spy."""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


def _write_two_agent_script():
    """Write a minimal 2-agent workflow script, return path."""
    content = '''from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import agent, subscribe

@agent("collector", entry_prompt="collect data and report")
def assemble_collector():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

@agent("analyzer", entry_prompt="analyze the collected data")
def assemble_analyzer():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

subscribe("analyzer").to("collector")
'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(content)
        path = f.name
    return path


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# ── Tests ──


@async_test
async def test_mode_b_two_agents_created_with_correct_config():
    """Mode B: collector (oneshot) + analyzer (continuous)
    both created with correct modes and workflow_flag."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        result = k.spawn_from_script(script_path, parent=None)

        # Verify result structure
        assert result["workflow_flag"].startswith("wf_")
        assert len(result["agents"]) == 2
        pids = {a["pid"] for a in result["agents"]}
        assert pids == {"collector", "analyzer"}

        # Verify subscribe registered to MessageBus
        subscribers = k.message_bus.get_subscribers_of("collector")
        assert "analyzer" in subscribers

        # Verify modes — both are continuous because subscribe declaration
        # involves both agents (collector as publisher, analyzer as subscriber).
        # Publisher needs continuous mode to keep producing output for subscribers.
        assert k.runtime_table["collector"].mode == "continuous"
        assert k.runtime_table["analyzer"].mode == "continuous"

        # Verify workflow_flag set on agents
        assert k.runtime_table["collector"].workflow_flag == result["workflow_flag"]
        assert k.runtime_table["analyzer"].workflow_flag == result["workflow_flag"]

        # Verify tasks were started
        assert "collector" in k._tasks
        assert "analyzer" in k._tasks

        # Verify entry_prompts were delivered
        qc = k.input_queues["collector"].get_nowait()
        qa = k.input_queues["analyzer"].get_nowait()
        assert qc.text == "collect data and report"
        assert qa.text == "analyze the collected data"

    finally:
        os.unlink(script_path)


@async_test
async def test_subscribe_registered_to_message_bus():
    """subscribe declaration is registered to MessageBus on spawn."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        k.spawn_from_script(script_path, parent=None)

        # Verify subscribe was registered to MessageBus (not _pending_subscriptions)
        subscribers = k.message_bus.get_subscribers_of("collector")
        assert "analyzer" in subscribers
        assert k._pending_subscriptions == []

    finally:
        os.unlink(script_path)


@async_test
async def test_cascade_termination_subscriber_receives_sentinel():
    """When publisher finishes, subscriber receives __EXIT_SENTINEL__."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        k.spawn_from_script(script_path, parent=None)

        collector = k.runtime_table["collector"]
        analyzer = k.runtime_table["analyzer"]

        # Consume entry prompts
        k.input_queues["collector"].get_nowait()
        k.input_queues["analyzer"].get_nowait()

        # Mark collector as finished (simulate _on_agent_finished callback)
        collector.state = AgentState.FINISHED
        await k._on_agent_finished(collector)

        # analyzer should have sentinel in queue from cascade
        sentinel = k.input_queues["analyzer"].get_nowait()
        assert sentinel is __import__(
            'harness.runtime.types', fromlist=['__EXIT_SENTINEL__']
        ).__EXIT_SENTINEL__
        assert analyzer.should_exit is True

    finally:
        os.unlink(script_path)
