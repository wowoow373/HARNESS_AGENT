"""Test harness for DIContainer."""

import pytest

from harness.core.container import DIContainer
from harness.core.exceptions import (
    ComponentNotRegisteredError,
    DuplicateRegistrationError,
)


class TestDIContainerRegistration:
    """DIContainer 注册方法测试。"""

    def test_register_single_component(self):
        """正常注册单个组件。"""
        container = DIContainer()

        class IFoo:
            pass

        foo = object()
        container.register(IFoo, foo)
        assert container.is_registered(IFoo) is True

    def test_register_multiple_components(self):
        """正常注册多个组件。"""
        container = DIContainer()

        class IA:
            pass

        class IB:
            pass

        a = object()
        b = object()
        container.register(IA, a)
        container.register(IB, b)
        assert container.is_registered(IA) is True
        assert container.is_registered(IB) is True

    def test_register_duplicate_raises_error(self):
        """重复注册同一接口抛出 DuplicateRegistrationError。"""
        container = DIContainer()

        class IFoo:
            pass

        container.register(IFoo, object())
        with pytest.raises(DuplicateRegistrationError) as exc_info:
            container.register(IFoo, object())
        assert "IFoo" in str(exc_info.value)

    def test_register_none_instance_raises_error(self):
        """注册 None 实例抛出 ValueError。"""
        container = DIContainer()

        class IFoo:
            pass

        with pytest.raises(ValueError) as exc_info:
            container.register(IFoo, None)
        assert "None" in str(exc_info.value) or "null" in str(
            exc_info.value
        ).lower()

    def test_register_non_type_interface_raises_error(self):
        """非 type 的 interface 参数抛出 TypeError。"""
        container = DIContainer()
        with pytest.raises(TypeError):
            container.register("not_a_type", object())


class TestDIContainerResolution:
    """DIContainer 解析方法测试。"""

    def test_resolve_returns_same_instance(self):
        """resolve 返回与注册时相同的实例。"""
        container = DIContainer()

        class IFoo:
            pass

        foo = object()
        container.register(IFoo, foo)
        assert container.resolve(IFoo) is foo

    def test_resolve_unregistered_raises_error(self):
        """resolve 未注册类型抛出 ComponentNotRegisteredError。"""
        container = DIContainer()

        class IFoo:
            pass

        with pytest.raises(ComponentNotRegisteredError):
            container.resolve(IFoo)

    def test_resolve_unregistered_error_contains_name(self):
        """resolve 未注册类型错误消息包含接口名。"""
        container = DIContainer()

        class IBar:
            pass

        with pytest.raises(ComponentNotRegisteredError) as exc_info:
            container.resolve(IBar)
        assert "IBar" in str(exc_info.value)

    def test_resolve_non_type_raises_error(self):
        """resolve 非 type 参数抛出 TypeError。"""
        container = DIContainer()
        with pytest.raises(TypeError):
            container.resolve(123)


class TestDIContainerHelpers:
    """DIContainer 辅助方法测试。"""

    def test_is_registered_positive(self):
        """is_registered 对已注册组件返回 True。"""
        container = DIContainer()

        class IFoo:
            pass

        container.register(IFoo, object())
        assert container.is_registered(IFoo) is True

    def test_is_registered_negative(self):
        """is_registered 对未注册组件返回 False。"""
        container = DIContainer()

        class IFoo:
            pass

        assert container.is_registered(IFoo) is False

    def test_list_registered_empty(self):
        """空容器 list_registered 返回空字典。"""
        container = DIContainer()
        assert container.list_registered() == {}

    def test_list_registered_after_registration(self):
        """注册后 list_registered 包含正确条目。"""
        container = DIContainer()

        class IFoo:
            pass

        foo = object()
        container.register(IFoo, foo)
        reg = container.list_registered()
        assert IFoo in reg
        assert reg[IFoo] is foo

    def test_list_registered_returns_copy(self):
        """list_registered 返回副本，修改不影响内部状态。"""
        container = DIContainer()

        class IFoo:
            pass

        container.register(IFoo, object())
        reg = container.list_registered()
        reg.clear()
        assert container.is_registered(IFoo) is True


class TestDIContainerIntegration:
    """DIContainer 集成测试。"""

    def test_shared_instance_across_resolves(self):
        """同一实例被多处 resolve 时保持一致。"""
        container = DIContainer()

        class IMemory:
            pass

        class IAssembler:
            pass

        class ISensor:
            pass

        memory = object()
        container.register(IMemory, memory)
        container.register(IAssembler, memory)
        container.register(ISensor, memory)

        assert container.resolve(IMemory) is memory
        assert container.resolve(IAssembler) is memory
        assert container.resolve(ISensor) is memory

    def test_component_graph(self):
        """多个组件间的依赖图验证。"""
        container = DIContainer()

        class IA:
            pass

        class IB:
            pass

        class AImpl:
            pass

        class BImpl:
            def __init__(self, a):
                self.a = a

        a = AImpl()
        b = BImpl(a)

        container.register(IA, a)
        container.register(IB, b)

        assert container.resolve(IA) is a
        assert container.resolve(IB) is b
        assert container.resolve(IB).a is container.resolve(IA)

    def test_error_messages_are_descriptive(self):
        """错误消息包含足够上下文。"""
        container = DIContainer()

        class IFoo:
            pass

        class IBar:
            pass

        # 重复注册
        container.register(IFoo, object())
        with pytest.raises(DuplicateRegistrationError) as e:
            container.register(IFoo, object())
        assert "IFoo" in str(e.value)
        assert "already registered" in str(e.value).lower() or "duplicate" in str(
            e.value
        ).lower()

        # 未注册
        with pytest.raises(ComponentNotRegisteredError) as e:
            container.resolve(IBar)
        assert "IBar" in str(e.value)
