"""Test harness for exception hierarchy."""

import pytest

from harness.core.exceptions import (
    ComponentNotRegisteredError,
    ConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    ContainerError,
    DuplicateRegistrationError,
    HarnessError,
    OrchestratorError,
)


class TestHarnessError:
    """HarnessError 基类测试。"""

    def test_base_exception_is_exception(self):
        """HarnessError 是 Exception 的子类。"""
        assert issubclass(HarnessError, Exception)

    def test_can_raise_and_catch(self):
        """可以正常抛出和捕获。"""
        with pytest.raises(HarnessError):
            raise HarnessError("test")

    def test_message_preserved(self):
        """异常消息被正确保留。"""
        try:
            raise HarnessError("hello world")
        except HarnessError as e:
            assert "hello world" in str(e)


class TestExceptionHierarchy:
    """验证完整的继承层次。"""

    def test_config_error_hierarchy(self):
        """Config 分支继承正确。"""
        assert issubclass(ConfigError, HarnessError)
        assert issubclass(ConfigNotFoundError, ConfigError)
        assert issubclass(ConfigParseError, ConfigError)
        assert issubclass(ConfigValidationError, ConfigError)

    def test_container_error_hierarchy(self):
        """Container 分支继承正确。"""
        assert issubclass(ContainerError, HarnessError)
        assert issubclass(DuplicateRegistrationError, ContainerError)
        assert issubclass(ComponentNotRegisteredError, ContainerError)

    def test_orchestrator_error_hierarchy(self):
        """Orchestrator 分支继承正确。"""
        assert issubclass(OrchestratorError, HarnessError)

    def test_catch_all_with_harness_error(self):
        """所有子类异常都可被 HarnessError 统一捕获。"""
        all_exc = [
            ConfigNotFoundError,
            ConfigParseError,
            ConfigValidationError,
            DuplicateRegistrationError,
            ComponentNotRegisteredError,
            OrchestratorError,
        ]
        for exc_cls in all_exc:
            try:
                raise exc_cls("test")
            except HarnessError:
                pass
            else:
                pytest.fail(
                    f"{exc_cls.__name__} should be caught by HarnessError"
                )

    def test_catch_with_intermediate_parent(self):
        """中间层父类可以捕获子类。"""
        config_exc = [
            ConfigNotFoundError,
            ConfigParseError,
            ConfigValidationError,
        ]
        for exc_cls in config_exc:
            try:
                raise exc_cls("test")
            except ConfigError:
                pass
            else:
                pytest.fail(
                    f"{exc_cls.__name__} should be caught by ConfigError"
                )

    def test_catch_container_with_intermediate_parent(self):
        """ContainerError 可以捕获子类。"""
        for exc_cls in [DuplicateRegistrationError, ComponentNotRegisteredError]:
            try:
                raise exc_cls("test")
            except ContainerError:
                pass
            else:
                pytest.fail(
                    f"{exc_cls.__name__} should be caught by ContainerError"
                )


class TestExceptionMessages:
    """异常消息测试。"""

    def test_config_error_message_prefix(self):
        """ConfigError 消息包含 [CONFIG] 前缀。"""
        e = ConfigError("something wrong")
        assert "[CONFIG]" in str(e)

    def test_container_error_message_prefix(self):
        """ContainerError 消息包含 [CONTAINER] 前缀。"""
        e = ContainerError("something wrong")
        assert "[CONTAINER]" in str(e)

    def test_orchestrator_error_message_prefix(self):
        """OrchestratorError 消息包含 [ORCHESTRATOR] 前缀。"""
        e = OrchestratorError("something wrong")
        assert "[ORCHESTRATOR]" in str(e)

    def test_config_not_found_contains_path(self):
        """ConfigNotFoundError 消息包含文件路径。"""
        e = ConfigNotFoundError("/tmp/missing.toml")
        assert "missing.toml" in str(e)

    def test_duplicate_registration_contains_name(self):
        """DuplicateRegistrationError 消息包含接口名。"""
        e = DuplicateRegistrationError("InputAdapter")
        assert "InputAdapter" in str(e)

    def test_component_not_registered_contains_name(self):
        """ComponentNotRegisteredError 消息包含接口名。"""
        e = ComponentNotRegisteredError("InputAdapter")
        assert "InputAdapter" in str(e)
