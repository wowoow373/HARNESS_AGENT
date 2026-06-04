"""Harness Agent Template — 配置模块。

提供 TOML 配置文件的加载、解析与校验，以及 YAML 装配声明的加载与 DI 容器构建。
"""

from .loader import ConfigLoader, ProfileConfig
from .yaml_assembler import (
    AssemblyError,
    AssemblyValidationError,
    DependencyNotSatisfiedError,
    INTERFACE_REGISTRY,
    UnknownInterfaceError,
    YamlAssembler,
)

__all__ = [
    "ConfigLoader",
    "ProfileConfig",
    "YamlAssembler",
    "INTERFACE_REGISTRY",
    "AssemblyError",
    "AssemblyValidationError",
    "DependencyNotSatisfiedError",
    "UnknownInterfaceError",
]
