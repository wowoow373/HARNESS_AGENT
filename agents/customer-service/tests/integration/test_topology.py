"""Integration tests for workflow topology (agent spawn + subscriptions)."""
import pytest
from harness.runtime.decorators import _agent_registry, _subscription_registry
import importlib.util


class TestWorkflowTopology:

    def test_all_six_agents_spawned(self, workflow_script_path):
        _agent_registry.clear()
        _subscription_registry.clear()
        spec = importlib.util.spec_from_file_location("_wf_test", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert len(_agent_registry) == 6
        assert "router" in _agent_registry
        assert "direction" in _agent_registry
        assert "evidence" in _agent_registry
        assert "validation" in _agent_registry
        assert "task_agent" in _agent_registry
        assert "fallback" in _agent_registry

    def test_subscriptions_include_virtual(self, workflow_script_path):
        _agent_registry.clear()
        _subscription_registry.clear()
        spec = importlib.util.spec_from_file_location("_wf_test2", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        subs = {(s.subscriber, s.publisher) for s in _subscription_registry}
        assert ("direction", "user") in subs
        assert ("evidence", "user") in subs
        assert ("validation", "user") in subs
        assert ("router", "user") in subs

    def test_each_agent_has_entry_prompt(self, workflow_script_path):
        _agent_registry.clear()
        _subscription_registry.clear()
        spec = importlib.util.spec_from_file_location("_wf_test3", workflow_script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, blueprint in _agent_registry.items():
            assert blueprint["entry_prompt"], f"{name} has empty entry_prompt"
