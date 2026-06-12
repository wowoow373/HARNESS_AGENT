"""Tests for Kernel extensibility: adapter context injection and virtual publishers."""

import os
import sys
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import _agent_registry, _subscription_registry
from harness.runtime.kernel import Kernel, _resolve_adapter


class _MockConsole:
    """Mock SystemConsole — records send calls."""

    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


class _FakeKernel:
    """Minimal Kernel stub for _resolve_adapter tests."""

    pass


class _FakeRuntime:
    """Minimal AgentRuntime stub for _resolve_adapter tests."""

    pass


class _InjectableAdapter:
    """AsyncInputAdapter stub that accepts kernel context injection."""

    def __init__(self):
        self.injected = None

    def _inject_kernel_context(self, pid, kernel, runtime):
        self.injected = {"pid": pid, "kernel": kernel, "runtime": runtime}


class _PlainAdapter:
    """AsyncInputAdapter stub without injection support."""

    pass


class _FakeContainer:
    """DI container stub that returns a fixed adapter."""

    def __init__(self, adapter):
        self._adapter = adapter

    def resolve(self, iface):
        return self._adapter


class TestResolveAdapter:
    """Tests for _resolve_adapter kernel context injection."""

    def test_injects_kernel_context_when_method_present(self):
        """Custom adapter with _inject_kernel_context receives dependencies."""
        adapter = _InjectableAdapter()
        container = _FakeContainer(adapter)
        kernel = _FakeKernel()
        runtime = _FakeRuntime()

        result = _resolve_adapter(container, pid="agent_1", kernel=kernel, runtime=runtime)

        assert result is adapter
        assert adapter.injected == {
            "pid": "agent_1",
            "kernel": kernel,
            "runtime": runtime,
        }

    def test_returns_plain_adapter_without_injection(self):
        """Adapter without _inject_kernel_context is returned unchanged."""
        adapter = _PlainAdapter()
        container = _FakeContainer(adapter)

        result = _resolve_adapter(
            container, pid="agent_2", kernel=_FakeKernel(), runtime=_FakeRuntime()
        )

        assert result is adapter

    def test_falls_back_to_kernel_bridge_adapter(self):
        """When container cannot resolve AsyncInputAdapter, KBA is used."""
        class _FailingContainer:
            def resolve(self, iface):
                raise RuntimeError("no adapter registered")

        kernel = _FakeKernel()
        runtime = _FakeRuntime()
        result = _resolve_adapter(
            _FailingContainer(), pid="agent_3", kernel=kernel, runtime=runtime
        )

        from harness.runtime.bridge_adapter import KernelBridgeAdapter

        assert isinstance(result, KernelBridgeAdapter)
        assert result._pid == "agent_3"


def _write_script_with_subscribe(publisher: str):
    """Create a temp workflow script subscribing to the given publisher."""
    content = f'''from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import agent, subscribe

@agent("worker", entry_prompt="work")
def assemble_worker():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

subscribe("worker").to("{publisher}")
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(content)
        path = f.name
    return path


class TestVirtualPublishers:
    """Tests for subscribe() validation with virtual publishers."""

    def setup_method(self):
        _agent_registry.clear()
        _subscription_registry.clear()

    def teardown_method(self):
        _agent_registry.clear()
        _subscription_registry.clear()

    def test_allows_user_virtual_publisher(self):
        """subscribe(...).to('user') is accepted without declaring a @agent."""
        path = _write_script_with_subscribe("user")
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            assert "worker" in kernel.runtime_table
            assert result["workflow_flag"].startswith("wf_")
        finally:
            os.unlink(path)
            sys.modules.pop("_workflow_script", None)

    def test_rejects_unknown_publisher(self):
        """subscribe(...).to('unknown_publisher') still raises ValueError."""
        path = _write_script_with_subscribe("unknown_publisher")
        try:
            kernel = Kernel(_MockConsole())
            with pytest.raises(ValueError, match="references unknown"):
                kernel.spawn_from_script(path)
        finally:
            os.unlink(path)
            sys.modules.pop("_workflow_script", None)
