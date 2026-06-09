"""Tests for harness.runtime.decorators — @agent / subscribe / registry."""

import sys
import tempfile
import os
import pytest
from harness.runtime.decorators import (
    _agent_registry,
    _subscription_registry,
    SubRecord,
    agent,
    subscribe,
)


@pytest.fixture(autouse=True)
def _clear_registries():
    """Clear registries before each test for isolation."""
    _agent_registry.clear()
    _subscription_registry.clear()


class TestAgentDecorator:
    """@agent decorator tests."""

    def test_registers_factory_in_registry(self):
        """@agent registers factory in _agent_registry."""
        @agent("test_agent", entry_prompt="do something")
        def make_harness():
            return "fake_harness"

        assert "test_agent" in _agent_registry
        bp = _agent_registry["test_agent"]
        assert bp["name"] == "test_agent"
        assert bp["entry_prompt"] == "do something"
        assert bp["metadata"] == {}
        assert callable(bp["factory"])
        assert bp["factory"]() == "fake_harness"

    def test_registers_metadata(self):
        """@agent stores metadata when provided."""
        @agent("worker", entry_prompt="work", metadata={"desc": "a worker"})
        def make_harness():
            return "h"

        assert _agent_registry["worker"]["metadata"] == {"desc": "a worker"}

    def test_duplicate_name_raises(self):
        """Duplicate @agent name raises ValueError."""
        @agent("dup", entry_prompt="first")
        def factory1():
            return "h1"

        with pytest.raises(ValueError, match="already registered"):
            @agent("dup", entry_prompt="second")
            def factory2():
                return "h2"

    def test_factory_preserved_as_callable(self):
        """factory is preserved as the original callable."""
        @agent("a", entry_prompt="go")
        def my_factory():
            return object()

        assert _agent_registry["a"]["factory"] is my_factory


class TestSubscribe:
    """subscribe() function tests."""

    def test_adds_sub_record(self):
        """subscribe("A").to("B") appends SubRecord to registry."""
        subscribe("analyzer").to("collector")

        assert len(_subscription_registry) == 1
        rec = _subscription_registry[0]
        assert rec.subscriber == "analyzer"
        assert rec.publisher == "collector"

    def test_multiple_calls_accumulate(self):
        """Multiple subscribe calls accumulate, not overwrite."""
        subscribe("A").to("B")
        subscribe("A").to("C")

        assert len(_subscription_registry) == 2

    def test_self_subscription_raises(self):
        """Self-subscription raises ValueError."""
        with pytest.raises(ValueError, match="Self-subscription"):
            subscribe("X").to("X")


class TestRegistryIsolation:
    """Registry clearing + importlib loading."""

    def test_clear_empties_both_registries(self):
        """clear() empties both registries."""
        _agent_registry["x"] = {}
        _subscription_registry.append(SubRecord("a", "b"))

        _agent_registry.clear()
        _subscription_registry.clear()

        assert len(_agent_registry) == 0
        assert len(_subscription_registry) == 0

    def test_importlib_load_fills_registry(self):
        """importlib loading of @agent script fills registry."""
        script = '''
from harness.runtime.decorators import agent, subscribe

@agent("loader_test", entry_prompt="load")
def make_harness():
    return "loaded"
'''
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False
        ) as f:
            f.write(script)
            path = f.name

        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_test_script", path
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_test_script"] = module
            spec.loader.exec_module(module)

            assert "loader_test" in _agent_registry
            assert _agent_registry["loader_test"]["entry_prompt"] == "load"
        finally:
            os.unlink(path)
            sys.modules.pop("_test_script", None)
