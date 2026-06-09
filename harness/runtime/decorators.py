"""@agent decorator + subscribe() function + module-level registries.

Kernel.spawn_from_script clears these registries before loading a script,
then reads them after importlib loading to create AgentRuntime instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# ── Module-level registries ──
# Cleared by Kernel.spawn_from_script before script loading.
# Safe under asyncio single-threaded model (no real concurrency).

_agent_registry: dict[str, dict] = {}
"""agent registry. key = agent name (pid), value = Blueprint:
    {"name": str, "entry_prompt": str, "metadata": dict, "factory": Callable}
"""

_subscription_registry: list[SubRecord] = []
"""Subscription declaration list."""


@dataclass(frozen=True)
class SubRecord:
    """A single subscription declaration.

    Attributes:
        subscriber: Subscriber agent name.
        publisher: Publisher agent name.
    """
    subscriber: str
    publisher: str


# ── @agent decorator ──

def agent(name: str, entry_prompt: str, metadata: dict | None = None):
    """Declare an agent with its assembly logic.

    Args:
        name: Agent pid, must be unique within a single spawn.
        entry_prompt: Required. The agent's first UserRequest.text.
        metadata: Optional. Passed through to parent agent's LLM,
                  appears in spawn_workflow return value's agents[].metadata.

    Returns:
        decorator: Accepts a factory function and registers it.

    Raises:
        ValueError: If name is already in _agent_registry.
    """
    def decorator(factory: Callable):
        if name in _agent_registry:
            raise ValueError(
                f"Agent '{name}' already registered. "
                f"Each @agent name must be unique within a workflow script."
            )
        _agent_registry[name] = {
            "name": name,
            "entry_prompt": entry_prompt,
            "metadata": metadata or {},
            "factory": factory,
        }
        return factory
    return decorator


# ── subscribe() ──

class _SubscribeBuilder:
    """Intermediate builder for subscribe("A").to("B") syntax."""

    def __init__(self, subscriber: str):
        self._subscriber = subscriber

    def to(self, publisher: str) -> None:
        """Complete the subscription declaration.

        Raises:
            ValueError: If subscriber == publisher.
        """
        if self._subscriber == publisher:
            raise ValueError(
                f"Self-subscription not allowed: "
                f"'{self._subscriber}' cannot subscribe to itself."
            )
        _subscription_registry.append(
            SubRecord(subscriber=self._subscriber, publisher=publisher)
        )


def subscribe(subscriber: str) -> _SubscribeBuilder:
    """Declare a subscription relationship.

    Usage:
        subscribe("analyzer").to("collector")

    Args:
        subscriber: Subscriber agent name.

    Returns:
        _SubscribeBuilder: Call .to(publisher) to complete.
    """
    return _SubscribeBuilder(subscriber)
