"""Harness Agent Template — 依赖注入容器。

采用预构造实例注册模式：用户创建组件实例并手动注入依赖，然后注册到容器。
容器仅负责存储和按接口类型解析，不管理对象生命周期。
"""

from typing import Any, Dict

from .exceptions import (
    ComponentNotRegisteredError,
    DuplicateRegistrationError,
)


class DIContainer:
    """依赖注入容器 — 预构造实例注册模式。

    职责：存储已创建的组件实例，按接口类型解析。
    不管理对象生命周期，不创建实例。

    用法::

        container = DIContainer()
        memory = JsonlMemory(path="./memory.jsonl")
        container.register(MemoryBackend, memory)
        # ...
        resolved = container.resolve(MemoryBackend)  # → memory
    """

    def __init__(self):
        """初始化空的注册表。"""
        self._registry: Dict[type, Any] = {}

    def register(self, interface: type, instance: Any) -> None:
        """注册一个组件实例。

        Args:
            interface: 组件的抽象接口类型（用于 resolve 时的 key）。
            instance: 已创建的组件实例。

        Raises:
            TypeError: interface 不是 type 类型。
            ValueError: instance 为 None。
            DuplicateRegistrationError: 同一接口类型已注册过。
        """
        if not isinstance(interface, type):
            raise TypeError(
                f"interface must be a type, got {type(interface).__name__}"
            )
        if instance is None:
            raise ValueError(
                f"Cannot register None instance for interface '{interface.__name__}'"
            )
        if interface in self._registry:
            raise DuplicateRegistrationError(
                f"Interface '{interface.__name__}' is already registered"
            )
        self._registry[interface] = instance

    def resolve(self, interface: type) -> Any:
        """按接口类型解析并返回已注册的实例。

        Args:
            interface: 组件的抽象接口类型。

        Returns:
            已注册的组件实例。

        Raises:
            TypeError: interface 不是 type 类型。
            ComponentNotRegisteredError: 接口类型未注册。
        """
        if not isinstance(interface, type):
            raise TypeError(
                f"interface must be a type, got {type(interface).__name__}"
            )
        if interface not in self._registry:
            raise ComponentNotRegisteredError(
                f"Interface '{interface.__name__}' has not been registered"
            )
        return self._registry[interface]

    def is_registered(self, interface: type) -> bool:
        """检查接口类型是否已注册。

        Args:
            interface: 组件的抽象接口类型。

        Returns:
            True 如果已注册，False 否则。
        """
        return interface in self._registry

    def list_registered(self) -> Dict[type, Any]:
        """返回所有已注册的 (接口类型 → 实例) 映射。

        Returns:
            Dict[type, Any]: 注册表副本。修改返回值不影响容器内部状态。
        """
        return dict(self._registry)
