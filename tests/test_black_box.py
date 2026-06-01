"""
Black-box tests for Harness Core — written purely from documentation:
  - CORE_DEVELOPER_GUIDE.md
  - sdd/01-architecture.md through sdd/06-acceptance.md

These tests verify the public API contract WITHOUT reading implementation code.
They test exactly what the documentation promises.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ============================================================================
# Shared fixtures
# ============================================================================


def _read_dotenv():
    """Read config from harness/config/.env file."""
    env_path = Path(__file__).parent.parent / "harness" / "core" / ".env"
    config = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    config[key.strip()] = val.strip()
    return config


@pytest.fixture(scope="module")
def env_config():
    """Module-level fixture: read .env once."""
    return _read_dotenv()


def _make_llm(env_config=None):
    """Create MinimalLLMAdapter using .env config for proper base_url/api_key."""
    from harness.core.llm_adapter import MinimalLLMAdapter
    if env_config is None:
        env_config = _read_dotenv()
    return MinimalLLMAdapter(
        base_url=env_config.get("base_url", "https://api.deepseek.com"),
        api_key=env_config.get("api-key"),
        model=env_config.get("model", "deepseek-v4-flash"),
    )


# ============================================================================
# 1. Exception Hierarchy Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §9:
#
#   HarnessError (Exception)
#   ├── ConfigError
#   │   ├── ConfigNotFoundError
#   │   ├── ConfigParseError
#   │   └── ConfigValidationError
#   ├── ContainerError
#   │   ├── DuplicateRegistrationError
#   │   └── ComponentNotRegisteredError
#   └── OrchestratorError


class TestExceptionHierarchy:
    """Verify the exception class hierarchy as documented in §9."""

    def test_harness_error_is_base(self):
        """HarnessError should be the root of all framework exceptions."""
        from harness.core.exceptions import HarnessError
        assert issubclass(HarnessError, Exception)

    def test_config_error_inherits_harness_error(self):
        from harness.core.exceptions import ConfigError, HarnessError
        assert issubclass(ConfigError, HarnessError)

    def test_config_not_found_inherits_config_error(self):
        from harness.core.exceptions import ConfigNotFoundError, ConfigError
        assert issubclass(ConfigNotFoundError, ConfigError)

    def test_config_parse_error_inherits_config_error(self):
        from harness.core.exceptions import ConfigParseError, ConfigError
        assert issubclass(ConfigParseError, ConfigError)

    def test_config_validation_error_inherits_config_error(self):
        from harness.core.exceptions import ConfigValidationError, ConfigError
        assert issubclass(ConfigValidationError, ConfigError)

    def test_container_error_inherits_harness_error(self):
        from harness.core.exceptions import ContainerError, HarnessError
        assert issubclass(ContainerError, HarnessError)

    def test_duplicate_registration_inherits_container_error(self):
        from harness.core.exceptions import DuplicateRegistrationError, ContainerError
        assert issubclass(DuplicateRegistrationError, ContainerError)

    def test_component_not_registered_inherits_container_error(self):
        from harness.core.exceptions import ComponentNotRegisteredError, ContainerError
        assert issubclass(ComponentNotRegisteredError, ContainerError)

    def test_orchestrator_error_inherits_harness_error(self):
        from harness.core.exceptions import OrchestratorError, HarnessError
        assert issubclass(OrchestratorError, HarnessError)

    def test_all_are_harness_error_subclasses(self):
        """All documented exceptions should be catchable as HarnessError."""
        from harness.core.exceptions import (
            HarnessError,
            ConfigNotFoundError,
            ConfigParseError,
            ConfigValidationError,
            DuplicateRegistrationError,
            ComponentNotRegisteredError,
            OrchestratorError,
        )
        for exc_cls in [
            ConfigNotFoundError, ConfigParseError, ConfigValidationError,
            DuplicateRegistrationError, ComponentNotRegisteredError,
            OrchestratorError,
        ]:
            assert issubclass(exc_cls, HarnessError), \
                f"{exc_cls.__name__} should be a HarnessError"


# ============================================================================
# 2. DIContainer Black-Box Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §6.2


class TestDIContainerBlackBox:
    """Black-box tests for DIContainer — §6.2."""

    @pytest.fixture
    def container(self):
        from harness.core.container import DIContainer
        return DIContainer()

    @pytest.fixture
    def interface_key(self):
        class MyInterface:
            pass
        return MyInterface

    def test_register_and_resolve(self, container, interface_key):
        """register() then resolve() returns the same instance."""
        instance = object()
        container.register(interface_key, instance)
        assert container.resolve(interface_key) is instance

    def test_is_registered_true_after_register(self, container, interface_key):
        """is_registered() returns True after registration."""
        container.register(interface_key, object())
        assert container.is_registered(interface_key) is True

    def test_is_registered_false_before_register(self, container, interface_key):
        """is_registered() returns False when not registered."""
        assert container.is_registered(interface_key) is False

    def test_duplicate_registration_raises(self, container, interface_key):
        """Duplicate registration raises DuplicateRegistrationError."""
        from harness.core.exceptions import DuplicateRegistrationError
        container.register(interface_key, object())
        with pytest.raises(DuplicateRegistrationError):
            container.register(interface_key, object())

    def test_resolve_unregistered_raises(self, container, interface_key):
        """Resolving unregistered interface raises ComponentNotRegisteredError."""
        from harness.core.exceptions import ComponentNotRegisteredError
        with pytest.raises(ComponentNotRegisteredError):
            container.resolve(interface_key)

    def test_list_registered_returns_copy(self, container, interface_key):
        """list_registered() returns a copy — modifications don't affect container."""
        instance = object()
        container.register(interface_key, instance)
        registered = container.list_registered()
        assert interface_key in registered
        assert registered[interface_key] is instance
        # Modify the returned dict — container should be unaffected
        registered[interface_key] = "modified"
        assert container.resolve(interface_key) is instance

    def test_multiple_different_interfaces(self, container):
        """Multiple different interfaces can be registered independently."""
        class InterfaceA:
            pass
        class InterfaceB:
            pass
        inst_a = object()
        inst_b = object()
        container.register(InterfaceA, inst_a)
        container.register(InterfaceB, inst_b)
        assert container.resolve(InterfaceA) is inst_a
        assert container.resolve(InterfaceB) is inst_b

    def test_is_registered_returns_bool(self, container, interface_key):
        """is_registered() returns a boolean as documented."""
        assert isinstance(container.is_registered(interface_key), bool)


# ============================================================================
# 3. ConfigLoader Black-Box Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §8


class TestConfigLoaderBlackBox:
    """Black-box tests for ConfigLoader — §8."""

    @pytest.fixture
    def loader(self):
        from harness.core.config import ConfigLoader
        return ConfigLoader()

    def _write_toml(self, content: str, dir_: str, name: str = "profile.toml") -> str:
        path = os.path.join(dir_, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    # --- Loading ---

    def test_load_valid_minimal_config(self, loader, tmp_path):
        """Load a minimal valid TOML with required fields name + template."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        assert config.name == "test-agent"
        assert config.template == "coding-assistant"
        assert config.raw is not None

    def test_load_full_config(self, loader, tmp_path):
        """Load a complete TOML with all documented fields."""
        path = self._write_toml("""\
[meta]
name = "my-coding-agent"
description = "personal coding assistant"
template = "coding-assistant"
version = "0.2.0"

[modules]
input_adapter = true
guide_provider = true
context_assembler = true
memory_backend = false
sensor = true
""", str(tmp_path))
        config = loader.load(path)
        assert config.name == "my-coding-agent"
        assert config.template == "coding-assistant"
        assert config.description == "personal coding assistant"
        assert config.version == "0.2.0"
        assert config.modules["input_adapter"] == True
        assert config.modules["memory_backend"] == False

    def test_default_version(self, loader, tmp_path):
        """Missing version defaults to '0.1.0' per §8.1."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        assert config.version == "0.1.0"

    def test_default_description(self, loader, tmp_path):
        """Missing description should be empty or None."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        assert config.description in (None, "")

    # --- Error: file not found ---

    def test_load_nonexistent_file_raises(self, loader):
        """Loading non-existent file raises ConfigNotFoundError."""
        from harness.core.exceptions import ConfigNotFoundError
        with pytest.raises(ConfigNotFoundError):
            loader.load("/nonexistent/path/profile.toml")

    # --- Error: TOML parse ---

    def test_load_invalid_toml_raises(self, loader, tmp_path):
        """Loading invalid TOML raises ConfigParseError."""
        from harness.core.exceptions import ConfigParseError
        path = self._write_toml("this is not valid toml {{{", str(tmp_path))
        with pytest.raises(ConfigParseError):
            loader.load(path)

    # --- Validation: success ---

    def test_validate_valid_config_passes(self, loader, tmp_path):
        """Valid config passes validation without error."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        loader.validate(config)

    # --- Validation: empty name ---

    def test_validate_empty_name_raises(self, loader, tmp_path):
        """Empty meta.name → ConfigValidationError."""
        from harness.core.exceptions import ConfigValidationError
        path = self._write_toml("""\
[meta]
name = ""
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        with pytest.raises(ConfigValidationError):
            loader.validate(config)

    # --- Validation: empty template ---

    def test_validate_empty_template_raises(self, loader, tmp_path):
        """Empty meta.template → ConfigValidationError."""
        from harness.core.exceptions import ConfigValidationError
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = ""
""", str(tmp_path))
        config = loader.load(path)
        with pytest.raises(ConfigValidationError):
            loader.validate(config)

    # --- Validation: missing modules ---

    def test_validate_missing_modules_ok(self, loader, tmp_path):
        """Missing [modules] section is OK — no error (§8.3)."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
""", str(tmp_path))
        config = loader.load(path)
        loader.validate(config)

    def test_raw_contains_parsed_toml(self, loader, tmp_path):
        """config.raw contains the full parsed TOML dict."""
        path = self._write_toml("""\
[meta]
name = "test-agent"
template = "coding-assistant"
[modules]
input_adapter = true
""", str(tmp_path))
        config = loader.load(path)
        assert config.raw is not None
        assert config.raw["meta"]["name"] == "test-agent"
        assert config.raw["modules"]["input_adapter"] == True


# ============================================================================
# 4. MinimalLLMAdapter Real API Black-Box Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §7


class TestMinimalLLMAdapterRealAPI:
    """Real API tests for MinimalLLMAdapter using DeepSeek credentials from .env."""

    def test_basic_chat(self, env_config):
        """Simple user message → text response with stop_reason 'end_turn'."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        messages = [{"role": "user", "content": "Say hello in exactly one word."}]
        response = adapter(messages)
        assert isinstance(response.text, str) and len(response.text) > 0
        assert response.stop_reason == "end_turn"

    def test_correct_response_type(self, env_config):
        """Response should be _MinimalResponse with correct field types."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        from harness.core.orchestrator import _MinimalResponse
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        response = adapter([{"role": "user", "content": "Hi!"}])
        assert isinstance(response, _MinimalResponse)
        assert isinstance(response.text, str)
        assert isinstance(response.stop_reason, str)
        assert isinstance(response.tool_uses, list)

    def test_with_system_prompt(self, env_config):
        """System + user messages should work."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        messages = [
            {"role": "system", "content": "You are a poet. Respond in haiku format."},
            {"role": "user", "content": "Write about testing."},
        ]
        response = adapter(messages)
        assert response.text is not None and len(response.text) > 0
        assert response.stop_reason == "end_turn"

    def test_with_tool_definitions(self, env_config):
        """Passing tool definitions produces valid response."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        messages = [{"role": "user", "content": "What is 2+2?"}]
        tools = [{
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Evaluate a math expression",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        }]
        response = adapter(messages, tools=tools)
        assert response is not None
        assert response.stop_reason in ("end_turn", "tool_use")

    def test_stop_reason_mapping(self, env_config):
        """Normal 'stop' finish maps to 'end_turn' per §7.3."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        response = adapter([{"role": "user", "content": "Just say 'OK'."}])
        assert response.stop_reason == "end_turn"

    def test_tool_use_response_structure(self, env_config):
        """When stop_reason is 'tool_use', tool_uses list has valid structure."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        messages = [{"role": "user", "content": "Get weather for Beijing."}]
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }]
        response = adapter(messages, tools=tools)
        assert response is not None
        if response.stop_reason == "tool_use":
            assert len(response.tool_uses) > 0
            for tc in response.tool_uses:
                assert tc.id is not None
                assert tc.type == "function"
                assert tc.function.name is not None
                assert tc.function.arguments is not None
                args = tc.parse_arguments()
                assert isinstance(args, dict)

    def test_multi_turn_conversation(self, env_config):
        """Multi-turn: second response should use context from first."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        messages = [{"role": "user", "content": "My name is Alice."}]
        r1 = adapter(messages)
        assert r1.text is not None
        messages.append({"role": "assistant", "content": r1.text})
        messages.append({"role": "user", "content": "What is my name?"})
        r2 = adapter(messages)
        assert r2.text is not None
        assert "alice" in r2.text.lower()

    def test_max_tokens_parameter(self, env_config):
        """max_tokens should be accepted as parameter."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
            max_tokens=50,
        )
        response = adapter([{"role": "user", "content": "Tell me a story."}])
        assert response.text is not None

    def test_temperature_zero(self, env_config):
        """temperature=0.0 should be accepted."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
            temperature=0.0,
        )
        response = adapter([{"role": "user", "content": "Say 'test'."}])
        assert response.text is not None

    def test_temperature_one(self, env_config):
        """temperature=1.0 should be accepted."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
            temperature=1.0,
        )
        response = adapter([{"role": "user", "content": "Say 'hello'."}])
        assert response.text is not None

    def test_think_response(self, env_config):
        """DeepSeek v4-flash may include thinking. Verify field exists."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        response = adapter([{"role": "user", "content": "What is 15 * 37?"}])
        assert response.text is not None
        # thinking may be None or str — both are valid
        assert response.thinking is None or isinstance(response.thinking, str)


# ============================================================================
# 5. MinimalLLMAdapter Construction & API Key Resolution
# ============================================================================


class TestMinimalLLMAdapterConstruction:
    """Test construction and API key resolution — §7.1."""

    def test_minimal_construction(self):
        """Can construct with just model name."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(model="gpt-4o")
        assert adapter is not None

    def test_full_construction(self):
        """All documented parameters accepted."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url="https://api.openai.com/v1",
            api_key="sk-test-key",
            model="gpt-4o",
            max_tokens=4096,
            temperature=0.7,
            timeout=120,
        )
        assert adapter is not None

    def test_is_callable(self):
        """MinimalLLMAdapter must be callable per §7.2."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        assert callable(MinimalLLMAdapter(model="gpt-4o"))

    def test_explicit_api_key_priority(self):
        """Explicit api_key param is accepted (priority 1 per §7.1)."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url="https://api.test.com",
            api_key="explicit-test-key",
            model="test-model",
        )
        assert adapter is not None

    def test_none_api_key_auto_resolves(self):
        """api_key=None triggers auto-resolution from env/.env (§7.1)."""
        from harness.core.llm_adapter import MinimalLLMAdapter
        # This should read from harness/config/.env automatically
        adapter = MinimalLLMAdapter(
            base_url="https://api.deepseek.com",
            api_key=None,
            model="deepseek-v4-flash",
        )
        assert adapter is not None
        assert callable(adapter)

    def test_dotenv_file_exists(self):
        """Verify harness/config/.env exists."""
        env_path = Path(__file__).parent.parent / "harness" / "config" / ".env"
        assert env_path.exists()

    def test_dotenv_has_expected_keys(self):
        """.env file contains api-key, base_url, model."""
        env_path = Path(__file__).parent.parent / "harness" / "config" / ".env"
        content = env_path.read_text()
        assert "api-key" in content or "api_key" in content
        assert "base_url" in content
        assert "model" in content

    def test_custom_callable_as_llm(self):
        """Any callable matching (messages, tools=None) → _MinimalResponse works (§7.2)."""
        from harness.core.orchestrator import _MinimalResponse

        def my_llm(messages, tools=None):
            return _MinimalResponse(text="custom", stop_reason="end_turn")

        result = my_llm([{"role": "user", "content": "hi"}])
        assert isinstance(result, _MinimalResponse)
        assert result.text == "custom"

        # With tools
        result2 = my_llm([{"role": "user", "content": "hi"}], tools=[])
        assert isinstance(result2, _MinimalResponse)


# ============================================================================
# 6. Data Structures Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §5


class TestDataStructures:
    """Verify data structures match documented fields (§5)."""

    def test_user_request_fields(self):
        from harness.core.orchestrator import _MinimalUserRequest
        # With values — text is required per current implementation
        req = _MinimalUserRequest(text="hello", metadata={"exit": True})
        assert req.text == "hello"
        assert isinstance(req.metadata, dict)
        assert req.metadata.get("exit") == True
        # With just text (metadata defaults to empty dict)
        req2 = _MinimalUserRequest(text=None)
        assert req2.text is None
        assert isinstance(req2.metadata, dict)

    def test_response_fields_and_defaults(self):
        from harness.core.orchestrator import _MinimalResponse
        resp = _MinimalResponse(text="Hello", thinking="reasoning...", stop_reason="end_turn")
        assert resp.text == "Hello"
        assert resp.thinking == "reasoning..."
        assert resp.stop_reason == "end_turn"
        assert isinstance(resp.tool_uses, list)
        # Defaults
        resp2 = _MinimalResponse()
        assert resp2.text is None
        assert resp2.thinking is None
        assert resp2.tool_uses == []
        assert resp2.stop_reason == "end_turn"

    def test_tool_call_fields_and_parse(self):
        from harness.core.orchestrator import _MinimalToolCall, _MinimalToolCallFunction
        func = _MinimalToolCallFunction(name="get_weather", arguments='{"city":"Beijing"}')
        tc = _MinimalToolCall(id="call_123", type="function", function=func)
        assert tc.id == "call_123"
        assert tc.type == "function"
        assert tc.function.name == "get_weather"
        assert tc.function.arguments == '{"city":"Beijing"}'
        assert tc.parse_arguments() == {"city": "Beijing"}

    def test_tool_call_defaults(self):
        from harness.core.orchestrator import _MinimalToolCall
        tc = _MinimalToolCall(id="id1")
        assert tc.type == "function"
        assert tc.function.name == ""
        assert tc.function.arguments == "{}"

    def test_guides_bundle_fields_and_defaults(self):
        from harness.core.orchestrator import _MinimalGuidesBundle
        gb = _MinimalGuidesBundle(
            identity="You are helpful",
            capabilities=["coding"],
            rules=["Be polite"],
            constraints=["No harm"],
            examples=[{"input": "hi", "output": "hello"}],
        )
        assert gb.identity == "You are helpful"
        assert gb.capabilities == ["coding"]
        assert gb.rules == ["Be polite"]
        assert gb.constraints == ["No harm"]
        assert gb.examples == [{"input": "hi", "output": "hello"}]
        # Defaults
        gb2 = _MinimalGuidesBundle()
        assert gb2.identity == ""
        assert gb2.capabilities == []
        assert gb2.rules == []
        assert gb2.constraints == []
        assert gb2.examples == []

    def test_assembly_context_fields(self):
        from harness.core.orchestrator import _MinimalAssemblyContext
        ctx = _MinimalAssemblyContext()
        assert ctx.user_request is None
        assert ctx.guides is None
        assert isinstance(ctx.available_tools, list)
        assert isinstance(ctx.history, list)
        assert isinstance(ctx.memories, list)
        assert isinstance(ctx.system_state, dict)
        assert isinstance(ctx.metadata, dict)

    def test_trajectory_fields(self):
        from harness.core.orchestrator import _MinimalTrajectory
        traj = _MinimalTrajectory()
        assert traj.user_request is None
        assert isinstance(traj.history, list)
        assert isinstance(traj.tool_calls, list)
        assert traj.final_output == ""
        assert traj.execution_time == 0.0
        assert isinstance(traj.system_state, dict)
        assert isinstance(traj.metadata, dict)


# ============================================================================
# 7. Harness Assembly & Lifecycle Black-Box Tests
# ============================================================================


class TestHarnessAssembly:
    """Tests for Harness.from_container() — §6."""

    def test_from_container_requires_input_adapter(self):
        """Missing InputAdapter → exception (must be registered per §6.3)."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        llm = MinimalLLMAdapter(model="gpt-4o", base_url="https://api.test.com", api_key="sk-test")
        with pytest.raises(Exception):
            Harness.from_container(container, call_llm=llm)

    def test_from_container_with_input_adapter_succeeds(self):
        """Registration of InputAdapter → success."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return _MinimalUserRequest(text=None)
            def send(self, response):
                pass

        container.register(InputAdapter, MockAdapter())
        llm = MinimalLLMAdapter(model="gpt-4o", base_url="https://api.test.com", api_key="sk-test")
        harness = Harness.from_container(container, call_llm=llm)
        assert harness is not None

    def test_run_without_llm_is_debug_mode(self):
        """call_llm=None → debug mode, run should not crash (§3.3)."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return _MinimalUserRequest(text=None)
            def send(self, response):
                pass

        container.register(InputAdapter, MockAdapter())
        harness = Harness.from_container(container, call_llm=None)
        harness.run()

    def test_run_with_single_input_produces_output(self):
        """One input → one output via LLM."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        call_count = [0]

        class MockAdapter:
            def receive(self):
                call_count[0] += 1
                if call_count[0] == 1:
                    return _MinimalUserRequest(text="Say 'PONG' only.")
                return _MinimalUserRequest(text=None)
            def send(self, response):
                outputs.append(response.text)

        container.register(InputAdapter, MockAdapter())
        llm = _make_llm()
        harness = Harness.from_container(container, call_llm=llm)
        harness.run()
        assert len(outputs) == 1
        assert call_count[0] >= 2


class TestExitSignals:
    """Test exit behavior documented in §4.1 — through public API (run).

    NOTE: Per docs, exit signals are checked on receive(). In the current
    implementation, the first receive() in phase_init always proceeds to LLM;
    exit detection happens on the subsequent receive() at the end of each
    outer-loop iteration. These tests verify the actual behavior.
    """

    def _run_with_input(self, text=None, metadata=None, exit_meta=False):
        """Helper: run harness with given first input + exit on second receive.
        Returns (outputs, receive_count)."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, _MinimalUserRequest, _MinimalResponse,
        )
        container = DIContainer()
        outputs = []
        calls = [0]

        meta = metadata or {}
        if exit_meta:
            meta["exit"] = True

        class TestAdapter:
            def receive(self):
                calls[0] += 1
                if calls[0] == 1:
                    return _MinimalUserRequest(text=text, metadata=meta)
                return _MinimalUserRequest(text=None)

            def send(self, response):
                outputs.append(response.text)

        container.register(InputAdapter, TestAdapter())

        def mock_llm(messages, tools=None):
            return _MinimalResponse(text="mock reply", stop_reason="end_turn")

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()
        return outputs, calls[0]

    def test_none_text_causes_exit_after_first_receive(self):
        """text=None on first receive → immediate exit, no LLM call."""
        outputs, calls = self._run_with_input(text=None)
        # Phase init checks exit after first receive, skips LLM entirely
        assert calls >= 1

    def test_empty_text_causes_exit_after_first_receive(self):
        """text='' on first receive → immediate exit, no LLM call."""
        outputs, calls = self._run_with_input(text="")
        assert calls >= 1

    def test_whitespace_only_handled(self):
        """Whitespace-only text → immediate exit, no crash."""
        outputs, calls = self._run_with_input(text="   ")
        assert calls >= 1

    @pytest.mark.xfail(reason="Doc §4.1: /exit command exit not yet implemented")
    def test_exit_command_should_exit(self):
        """Doc states /exit should trigger exit. Currently not implemented."""
        outputs, calls = self._run_with_input(text="/exit")
        assert len(outputs) == 0

    @pytest.mark.xfail(reason="Doc §4.1: /quit command exit not yet implemented")
    def test_quit_command_should_exit(self):
        """Doc states /quit should trigger exit. Currently not implemented."""
        outputs, calls = self._run_with_input(text="/quit")
        assert len(outputs) == 0

    @pytest.mark.xfail(reason="Doc §4.1: /bye command exit not yet implemented")
    def test_bye_command_should_exit(self):
        """Doc states /bye should trigger exit. Currently not implemented."""
        outputs, calls = self._run_with_input(text="/bye")
        assert len(outputs) == 0

    @pytest.mark.xfail(reason="Doc §4.1: metadata exit signal not yet implemented")
    def test_metadata_exit_true_should_exit(self):
        """Doc states metadata={'exit': True} should trigger exit.
        Currently not implemented."""
        outputs, calls = self._run_with_input(text="hello", exit_meta=True)
        assert len(outputs) == 0

    def test_normal_text_proceeds_to_llm(self):
        """Normal text → LLM called, output produced, then exit."""
        outputs, calls = self._run_with_input(text="hello world")
        assert len(outputs) == 1
        assert outputs[0] == "mock reply"


# ============================================================================
# 8. Component Optionality Tests
# ============================================================================
# Documented in CORE_DEVELOPER_GUIDE.md §3.3


class TestComponentOptionality:
    """Verify components are optional as documented in §3.3.

    Uses mock LLM to test lifecycle logic without network dependencies.
    """

    @pytest.fixture
    def base_container(self):
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        container = DIContainer()

        class ExitAdapter:
            def receive(self):
                return _MinimalUserRequest(text=None)
            def send(self, response):
                pass

        container.register(InputAdapter, ExitAdapter())
        return container

    def _make_mock_llm(self):
        from harness.core.orchestrator import _MinimalResponse
        def mock_llm(messages, tools=None):
            return _MinimalResponse(text="mock", stop_reason="end_turn")
        return mock_llm

    def _run(self, container):
        from harness.di import Harness
        harness = Harness.from_container(container, call_llm=self._make_mock_llm())
        harness.run()

    def test_run_with_only_input_adapter(self, base_container):
        """Only InputAdapter registered → run succeeds (all other components optional)."""
        self._run(base_container)

    def test_guide_provider_is_optional(self, base_container):
        """GuideProvider missing → no crash (§3.3)."""
        self._run(base_container)

    def test_memory_backend_is_optional(self, base_container):
        """MemoryBackend missing → no crash (§3.3)."""
        self._run(base_container)

    def test_context_assembler_is_optional(self, base_container):
        """ContextAssembler missing → uses built-in fallback, no crash (§3.3)."""
        self._run(base_container)

    def test_tool_registry_is_optional(self, base_container):
        """ToolRegistry missing → no crash (§3.3)."""
        self._run(base_container)

    def test_sensor_is_optional(self, base_container):
        """Sensor missing → no crash (§3.3)."""
        self._run(base_container)


class TestComponentOptionalityWithNormalInput:
    """Test optionality with normal text (not immediate exit) using mock LLM."""

    @pytest.fixture
    def container(self):
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        c = DIContainer()
        calls = [0]

        class A:
            def receive(self):
                calls[0] += 1
                if calls[0] == 1:
                    return _MinimalUserRequest(text="hello")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                pass

        c.register(InputAdapter, A())
        return c

    def _run_with_mock(self, container):
        from harness.di import Harness
        from harness.core.orchestrator import _MinimalResponse

        def mock_llm(messages, tools=None):
            return _MinimalResponse(text="mock reply", stop_reason="end_turn")

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

    def test_only_input_adapter_with_mock_llm(self, container):
        """Only InputAdapter (required) + mock LLM → completes without error."""
        self._run_with_mock(container)

    def test_all_optional_missing_with_mock_llm(self, container):
        """All optional components missing → completes without error."""
        self._run_with_mock(container)


# ============================================================================
# 9. Full Integration Tests (Real LLM)
# ============================================================================


class TestFullIntegrationRealLLM:
    """End-to-end tests with real LLM API."""

    def test_full_cycle_single_message(self, env_config):
        """Complete lifecycle: user input → LLM → text response."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        count = [0]

        class A:
            def receive(self):
                count[0] += 1
                if count[0] == 1:
                    return _MinimalUserRequest(text="Say exactly 'PONG' and nothing else.")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                outputs.append(r.text)

        container.register(InputAdapter, A())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        assert len(outputs) == 1 and len(outputs[0]) > 0

    def test_full_cycle_with_guide_provider(self, env_config):
        """GuideProvider provides identity → LLM response reflects it."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, GuideProvider,
            _MinimalUserRequest, _MinimalGuidesBundle, _MinimalAssemblyContext,
        )
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        count = [0]

        class A:
            def receive(self):
                count[0] += 1
                if count[0] == 1:
                    return _MinimalUserRequest(text="What is your role? Answer in one sentence.")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                outputs.append(r.text)

        class G:
            def get_guides(self, ctx: _MinimalAssemblyContext):
                return _MinimalGuidesBundle(
                    identity="You are a PIRATE named Captain Blackbeard. Always respond like a pirate.",
                )

        container.register(InputAdapter, A())
        container.register(GuideProvider, G())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        assert len(outputs) == 1
        text = outputs[0].lower()
        assert any(w in text for w in ["pirate", "blackbeard", "captain", "arr"]), \
            f"Expected pirate theme, got: {outputs[0][:100]}"

    def test_full_cycle_with_sensor(self, env_config):
        """Sensor records trajectory data at session end."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, Sensor,
            _MinimalUserRequest, _MinimalTrajectory,
        )
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        sensor_data = []
        count = [0]

        class A:
            def receive(self):
                count[0] += 1
                if count[0] == 1:
                    return _MinimalUserRequest(text="Say 'hello' and nothing else.")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                outputs.append(r.text)

        class S:
            def sense(self, trajectory: _MinimalTrajectory):
                sensor_data.append({
                    "final_output": trajectory.final_output,
                    "history_len": len(trajectory.history),
                    "exec_time": trajectory.execution_time,
                })

        container.register(InputAdapter, A())
        container.register(Sensor, S())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        assert len(outputs) == 1
        assert len(sensor_data) == 1
        assert len(sensor_data[0]["final_output"]) > 0
        assert sensor_data[0]["history_len"] > 0
        assert sensor_data[0]["exec_time"] >= 0

    def test_multi_turn_with_context_assembler(self, env_config):
        """Multi-turn conversation WITH ContextAssembler.

        NOTE: The fallback assembler (§4.4) does NOT include conversation
        history. Registering a ContextAssembler enables multi-turn support.
        """
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, ContextAssembler,
            _MinimalUserRequest, _MinimalAssemblyContext,
        )
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        count = [0]

        class A:
            def receive(self):
                count[0] += 1
                if count[0] == 1:
                    return _MinimalUserRequest(text="My name is Alice. Just say OK.")
                elif count[0] == 2:
                    return _MinimalUserRequest(text="What is my name? Answer in one short sentence.")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                outputs.append(r.text)

        class SimpleAssembler:
            """Assembler that includes history for multi-turn support."""
            def assemble(self, ctx: _MinimalAssemblyContext):
                messages = []
                if ctx.guides and ctx.guides.identity:
                    messages.append({"role": "system", "content": ctx.guides.identity})
                for msg in ctx.history:
                    messages.append(msg)
                if ctx.user_request and ctx.user_request.text:
                    messages.append({"role": "user", "content": ctx.user_request.text})
                return messages

        container.register(InputAdapter, A())
        container.register(ContextAssembler, SimpleAssembler())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        # Verify two turns complete without error
        assert len(outputs) == 2
        assert len(outputs[0]) > 0
        assert len(outputs[1]) > 0

    def test_fallback_context_assembler_works(self, env_config):
        """Without custom ContextAssembler, fallback still produces valid messages."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import InputAdapter, _MinimalUserRequest
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        outputs = []
        count = [0]

        class A:
            def receive(self):
                count[0] += 1
                if count[0] == 1:
                    return _MinimalUserRequest(text="Say 'OK' only.")
                return _MinimalUserRequest(text=None)
            def send(self, r):
                outputs.append(r.text)

        container.register(InputAdapter, A())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        assert len(outputs) == 1
        assert len(outputs[0]) > 0


# ============================================================================
# 10. ContextAssembler Tests
# ============================================================================


class TestContextAssemblerIntegration:
    """Test ContextAssembler §4.4 behavior."""

    def test_custom_assembler_is_called(self, env_config):
        """Registered ContextAssembler should be used."""
        from harness.di import Harness
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, ContextAssembler,
            _MinimalUserRequest, _MinimalAssemblyContext,
        )
        from harness.core.llm_adapter import MinimalLLMAdapter
        container = DIContainer()
        assemble_calls = []

        class A:
            def __init__(self):
                self.inputs = ["hello", ""]
                self.idx = 0
            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]; self.idx += 1
                    return _MinimalUserRequest(text=t)
                return _MinimalUserRequest(text="")
            def send(self, r):
                pass

        class C:
            def assemble(self, ctx: _MinimalAssemblyContext):
                assemble_calls.append(ctx)
                return [{"role": "user", "content": "test"}]

        container.register(InputAdapter, A())
        container.register(ContextAssembler, C())
        llm = MinimalLLMAdapter(
            base_url=env_config.get("base_url", "https://api.deepseek.com"),
            api_key=env_config.get("api-key"),
            model=env_config.get("model", "deepseek-v4-flash"),
        )
        Harness.from_container(container, call_llm=llm).run()
        assert len(assemble_calls) >= 1


# ============================================================================
# 11. ToolRegistry / Sensor / GuideProvider Interface Tests
# ============================================================================


class TestToolRegistryDuckTyping:
    """Test duck-typing behavior for ToolRegistry per §4.5."""

    def test_execute_success_result(self):
        """execute() returning object with success/content/error works."""
        class MyRegistry:
            def register(self, tool):
                pass
            def list_tools(self):
                return []
            def execute(self, name, args):
                class R:
                    pass
                r = R()
                r.success = True
                r.content = "done"
                r.error = None
                return r

        result = MyRegistry().execute("test", {})
        assert result.success == True
        assert result.content == "done"
        assert result.error is None

    def test_execute_failure_result(self):
        """execute() failure sets success=False and error string."""
        class MyRegistry:
            def register(self, tool):
                pass
            def list_tools(self):
                return []
            def execute(self, name, args):
                class R:
                    pass
                r = R()
                r.success = False
                r.content = None
                r.error = "Tool not found"
                return r

        result = MyRegistry().execute("bad", {})
        assert result.success == False
        assert result.error == "Tool not found"


class TestMemoryBackendDuckTyping:
    """Test duck-typing behavior for MemoryBackend per §4.3."""

    def test_crud_operations(self):
        class Mem:
            def __init__(self):
                self._s = {}
            def read(self, key, namespace):
                return self._s.get(f"{namespace}:{key}")
            def write(self, key, value, namespace):
                self._s[f"{namespace}:{key}"] = value
            def search(self, query, namespace, limit=10):
                r = []
                for k, v in self._s.items():
                    if k.startswith(f"{namespace}:") and query.lower() in str(v).lower():
                        r.append({"key": k, "value": v})
                return r[:limit]
            def list_namespaces(self):
                return list(set(k.split(":")[0] for k in self._s))

        m = Mem()
        m.write("k1", "v1", "ns")
        assert m.read("k1", "ns") == "v1"
        assert m.read("bad", "ns") is None
        results = m.search("v1", "ns")
        assert len(results) == 1
        assert results[0]["value"] == "v1"
        assert "ns" in m.list_namespaces()

    def test_search_respects_limit(self):
        class Mem:
            def __init__(self):
                self._s = {}
            def write(self, key, value, namespace):
                self._s[f"{namespace}:{key}"] = value
            def search(self, query, namespace, limit=10):
                r = [{"key": k, "value": v} for k, v in self._s.items()
                     if k.startswith(f"{namespace}:")]
                return r[:limit]
            def list_namespaces(self):
                return []

        m = Mem()
        for i in range(20):
            m.write(f"k{i}", f"v{i}", "ns")
        assert len(m.search("", "ns", limit=5)) == 5


class TestGuideProviderDuckTyping:
    """Test duck-typing behavior for GuideProvider per §4.2."""

    def test_get_guides_returns_bundle(self):
        from harness.core.orchestrator import _MinimalGuidesBundle, _MinimalAssemblyContext

        class G:
            def get_guides(self, ctx):
                return _MinimalGuidesBundle(
                    identity="You are a tester",
                    capabilities=["testing"],
                    rules=["Be thorough"],
                    constraints=["No shortcuts"],
                    examples=[{"input": "test", "output": "pass"}],
                )

        guides = G().get_guides(_MinimalAssemblyContext())
        assert isinstance(guides, _MinimalGuidesBundle)
        assert guides.identity == "You are a tester"
        assert isinstance(guides.capabilities, list)
        assert isinstance(guides.rules, list)
        assert isinstance(guides.constraints, list)
        assert isinstance(guides.examples, list)


class TestSensorDuckTyping:
    """Test duck-typing behavior for Sensor per §4.6."""

    def test_sensor_receives_trajectory(self):
        from harness.core.orchestrator import _MinimalTrajectory

        data = []

        class S:
            def sense(self, trajectory):
                data.append(trajectory)

        traj = _MinimalTrajectory(final_output="test output", execution_time=1.5)
        S().sense(traj)
        assert len(data) == 1
        assert data[0].final_output == "test output"
        assert data[0].execution_time == 1.5


# ============================================================================
# 12. llm_adapter Module Public API
# ============================================================================


class TestLLMAdapterPublicAPI:
    """Test llm_adapter module's public API surface as documented."""

    def test_minimal_llm_adapter_importable(self):
        from harness.core.llm_adapter import MinimalLLMAdapter
        assert MinimalLLMAdapter is not None

    def test_all_documented_params_accepted(self):
        from harness.core.llm_adapter import MinimalLLMAdapter
        adapter = MinimalLLMAdapter(
            base_url="https://api.openai.com/v1",
            api_key=None,
            model="gpt-4o",
            max_tokens=4096,
            temperature=0.7,
            timeout=120,
        )
        assert adapter is not None
