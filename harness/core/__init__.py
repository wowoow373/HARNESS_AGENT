"""Harness Agent Template — 内核模块。

包含 DI 容器、生命周期编排器和异常体系。
"""

from ..adapters import MinimalLLMAdapter  # re-export for backward compatibility
from ..config import ConfigLoader, ProfileConfig  # re-export for backward compatibility
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
