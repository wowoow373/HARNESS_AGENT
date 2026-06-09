# tests/runtime/test_monitor_quiescence.py

"""Tests for Kernel._monitor_quiescence full implementation.

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import pytest
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.types import __EXIT_SENTINEL__


# ── Helpers ──

class _MockConsole:
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


class _MockHarness:
    def __init__(self):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = None


def _make_runtime(kernel, pid, mode="continuous"):
    harness = _MockHarness()
    rt = AgentRuntime(
        pid=pid, mode=mode, harness=harness,
        kernel=kernel,
    )
    rt.started_at = 10.0
    return rt


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# ── Tests ──


@async_test
async def test_quiescence_returns_when_all_finished():
    """Monitors exits immediately when all agents are FINISHED."""
    console = _MockConsole()
    k = Kernel(console)

    rt = _make_runtime(k, "a")
    rt.state = AgentState.FINISHED
    k.runtime_table["a"] = rt
    k.input_queues["a"] = asyncio.Queue()

    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)


@async_test
async def test_quiescence_pushes_sentinel_when_all_idle():
    """When all non-FINISHED agents are idle, pushes sentinel to all."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_b = _make_runtime(k, "b")
    rt_a.state = AgentState.RUNNING
    rt_b.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle
    rt_b._idle_since = 100.0  # idle

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)

    assert rt_a.should_exit is True
    assert rt_b.should_exit is True

    qa = k.input_queues["a"].get_nowait()
    qb = k.input_queues["b"].get_nowait()
    assert qa is __EXIT_SENTINEL__
    assert qb is __EXIT_SENTINEL__


@async_test
async def test_quiescence_does_not_push_when_not_all_idle():
    """If any agent is still active (not idle), no sentinel is pushed."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_b = _make_runtime(k, "b")
    rt_a.state = AgentState.RUNNING
    rt_b.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle
    rt_b._idle_since = None   # NOT idle (e.g., in call_llm)

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    async def _check_and_stop():
        await asyncio.sleep(2.5)
        assert rt_a.should_exit is False
        assert rt_b.should_exit is False
        k._shutdown = True

    await asyncio.gather(
        k._monitor_quiescence(),
        _check_and_stop(),
    )


@async_test
async def test_quiescence_ignores_finished_agent():
    """A FINISHED agent is excluded from non_finished — only RUNNING idle matters."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_a.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle

    rt_b = _make_runtime(k, "b")
    rt_b.state = AgentState.FINISHED  # excluded from non_finished

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    # rt_a is the only non-FINISHED RUNNING agent → is idle → sentinel pushed
    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)

    assert rt_a.should_exit is True
    qa = k.input_queues["a"].get_nowait()
    assert qa is __EXIT_SENTINEL__
