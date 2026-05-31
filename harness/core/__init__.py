"""Harness Agent Template — 内核模块。

包含 DI 容器、配置加载器、生命周期编排器、LLM 适配器和异常体系。
"""

from .config import ConfigLoader, ProfileConfig
from .container import DIContainer
from .exceptions import (
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
from .llm_adapter import MinimalLLMAdapter
from .orchestrator import LifecycleOrchestrator

__all__ = [
    # Container
    "DIContainer",
    # Config
    "ConfigLoader",
    "ProfileConfig",
    # Orchestrator
    "LifecycleOrchestrator",
    # LLM
    "MinimalLLMAdapter",
    # Exceptions
    "HarnessError",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "ContainerError",
    "DuplicateRegistrationError",
    "ComponentNotRegisteredError",
    "OrchestratorError",
]
