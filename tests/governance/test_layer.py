"""ToolGovernanceLayer 弹性策略测试。"""

import asyncio
import time

from harness.core.governance.layer import ToolGovernanceLayer
from harness.core.governance.policy import PolicyRegistry, ToolPolicy, RetryPolicy
from harness.interfaces.types import ToolResult


class _Tool:
    def __init__(self, delay=0.0, raise_err=None, result=None):
        self.delay = delay
        self.raise_err = raise_err
        self.result = result
        self.calls = 0

    def execute(self, name, args):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raise_err:
            raise self.raise_err
        if self.result is not None:
            return self.result
        return ToolResult(success=True, content=f"ok:{name}")


class _FakeRouter:
    def __init__(self, tools):
        self._tools = tools

    def has_tool(self, name):
        return name in self._tools

    def execute(self, name, args):
        return self._tools[name].execute(name, args)


class _FakeBroker:
    def __init__(self):
        self._pending = {}
        self.requested = []

    def request(self, pid, name, args):
        aid = f"a{len(self.requested):03x}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[aid] = fut
        self.requested.append((pid, name, args, aid))
        return aid, fut

    def resolve(self, aid, approved):
        fut = self._pending.pop(aid, None)
        if fut is not None and not fut.done():
            fut.set_result(approved)
            return True
        return False

    def cancel(self, aid):
        fut = self._pending.pop(aid, None)
        if fut is not None and not fut.done():
            fut.cancel()


def run(coro):
    return asyncio.run(coro)


def test_timeout_absorbed():
    async def _t():
        tool = _Tool(delay=0.3)
        router = _FakeRouter({"slow": tool})
        reg = PolicyRegistry()
        reg.register("slow", ToolPolicy(timeout=0.05))
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("slow", {})
        return result, tool

    result, tool = run(_t())
    assert result.success is False
    assert "timeout" in result.error
    assert tool.calls == 1


def test_retry_then_succeed():
    async def _t():
        calls = {"n": 0}

        class Flaky:
            def execute(self, name, args):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RuntimeError("flaky")
                return ToolResult(success=True, content="ok")

        router = _FakeRouter({"flaky": Flaky()})
        reg = PolicyRegistry()
        reg.register("flaky", ToolPolicy(
            retry=RetryPolicy(max_attempts=3, backoff="fixed", base_delay=0.0)))
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("flaky", {})
        return result, calls

    result, calls = run(_t())
    assert result.success is True
    assert calls["n"] == 3


def test_no_retry_on_business_failure():
    async def _t():
        tool = _Tool(result=ToolResult(success=False, content="", error="nope"))
        router = _FakeRouter({"t": tool})
        reg = PolicyRegistry()
        reg.register("t", ToolPolicy(retry=RetryPolicy(max_attempts=3, base_delay=0.0)))
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("t", {})
        return result, tool

    result, tool = run(_t())
    assert result.success is False
    assert tool.calls == 1


def test_exception_absorbed():
    async def _t():
        tool = _Tool(raise_err=ValueError("boom"))
        router = _FakeRouter({"t": tool})
        reg = PolicyRegistry()
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("t", {})
        return result

    result = run(_t())
    assert result.success is False
    assert "boom" in result.error


def test_retry_exhaustion_returns_last_error():
    async def _t():
        tool = _Tool(raise_err=ValueError("boom"))
        router = _FakeRouter({"t": tool})
        reg = PolicyRegistry()
        reg.register("t", ToolPolicy(
            retry=RetryPolicy(max_attempts=2, backoff="fixed", base_delay=0.0)))
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("t", {})
        return result, tool

    result, tool = run(_t())
    assert result.success is False
    assert "boom" in result.error
    assert tool.calls == 2


def test_gate_approve_executes():
    async def _t():
        tool = _Tool()
        router = _FakeRouter({"gated": tool})
        reg = PolicyRegistry()
        reg.register("gated", ToolPolicy(gate=True))
        broker = _FakeBroker()
        layer = ToolGovernanceLayer(router, reg, broker, pid="root")
        task = asyncio.create_task(layer.execute("gated", {}))
        while not broker.requested:
            await asyncio.sleep(0)
        aid = broker.requested[0][3]
        broker.resolve(aid, True)
        result = await task
        return result, tool

    result, tool = run(_t())
    assert result.success is True
    assert tool.calls == 1


def test_gate_deny_blocks():
    async def _t():
        tool = _Tool()
        router = _FakeRouter({"gated": tool})
        reg = PolicyRegistry()
        reg.register("gated", ToolPolicy(gate=True))
        broker = _FakeBroker()
        layer = ToolGovernanceLayer(router, reg, broker, pid="root")
        task = asyncio.create_task(layer.execute("gated", {}))
        while not broker.requested:
            await asyncio.sleep(0)
        aid = broker.requested[0][3]
        broker.resolve(aid, False)
        result = await task
        return result, tool

    result, tool = run(_t())
    assert result.success is False
    assert "denied" in result.error
    assert tool.calls == 0


def test_gate_timeout_denies():
    async def _t():
        tool = _Tool()
        router = _FakeRouter({"gated": tool})
        reg = PolicyRegistry()
        reg.register("gated", ToolPolicy(gate=True, approval_timeout=0.05))
        broker = _FakeBroker()
        layer = ToolGovernanceLayer(router, reg, broker, pid="root")
        result = await layer.execute("gated", {})
        return result, tool

    result, tool = run(_t())
    assert result.success is False
    assert "approval timeout" in result.error
    assert tool.calls == 0


def test_direct_executor_runs_inline():
    async def _t():
        tool = _Tool()
        router = _FakeRouter({"t": tool})
        reg = PolicyRegistry()
        reg.register("t", ToolPolicy(executor="direct"))
        layer = ToolGovernanceLayer(router, reg, None, pid="root")
        result = await layer.execute("t", {})
        return result

    result = run(_t())
    assert result.success is True
    assert result.content == "ok:t"
