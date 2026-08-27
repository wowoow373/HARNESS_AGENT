"""编排器与治理层的集成接线测试。"""

import asyncio

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.exceptions import ComponentNotRegisteredError
from harness.core.governance.layer import ToolGovernanceLayer
from harness.core.governance.policy import PolicyRegistry
from harness.interfaces.types import ToolResult


class _Tool:
    def execute(self, name, args):
        return ToolResult(success=True, content=f"ok:{name}")


class _Provider:
    def __init__(self):
        self.tool = _Tool()

    def get_tools(self):
        from harness.interfaces.types import ToolDefinition
        return [ToolDefinition(name="t", description="d", parameters={})]

    def execute(self, name, args):
        return self.tool.execute(name, args)


class _Container:
    def __init__(self):
        self._provider = _Provider()

    def resolve(self, interface):
        if interface.__name__ == "SystemToolProvider":
            return self._provider
        raise ComponentNotRegisteredError(interface)


class _Adapter:
    pid = "root"

    async def receive(self):
        from harness.interfaces.types import UserRequest
        return UserRequest(text="hi", session_id="s")

    async def send(self, event):
        pass


def run(coro):
    return asyncio.run(coro)


def test_orchestrator_builds_governance():
    async def _t():
        orch = AsyncLifecycleOrchestrator(
            _Container(), adapter=_Adapter(), call_llm=None,
            policy_registry=PolicyRegistry(), approval_broker=None,
        )
        await orch._phase_init()
        gov = orch._governance
        assert isinstance(gov, ToolGovernanceLayer)
        assert gov.has_tool("t")
        result = await gov.execute("t", {})
        assert result.success is True
        assert result.content == "ok:t"
        return orch

    orch = run(_t())
    assert isinstance(orch._governance, ToolGovernanceLayer)


def test_orchestrator_defaults_to_singleton_registry():
    async def _t():
        orch = AsyncLifecycleOrchestrator(
            _Container(), adapter=_Adapter(), call_llm=None,
        )
        await orch._phase_init()
        # 未显式传 policy_registry 时用模块级单例（runtime tools direct 预注册）
        gov = orch._governance
        assert gov is not None
        assert gov.has_tool("t")
        return orch

    orch = run(_t())
    assert orch._governance is not None
