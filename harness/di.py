"""Harness Agent Template — 装配入口。

提供 Harness 类，封装从 DI 容器解析组件到启动编排的完整流程。
"""

from typing import Callable, Optional

from .core.container import DIContainer
from .core.exceptions import ComponentNotRegisteredError
from .core.orchestrator import InputAdapter, LifecycleOrchestrator


class Harness:
    """Harness Agent 框架的顶层入口。

    封装 DI 容器解析 → 编排器创建 → 生命周期启动的完整流程。

    用法::

        from harness.di import Harness
        from harness.core.container import DIContainer

        container = DIContainer()
        container.register(InputAdapter, CliAdapter())
        # ... 注册更多组件

        harness = Harness.from_container(container, call_llm=my_llm)
        harness.run()
    """

    def __init__(self, orchestrator: LifecycleOrchestrator):
        """初始化 Harness 实例。

        Args:
            orchestrator: 生命周期编排器实例。
        """
        self._orchestrator = orchestrator

    @staticmethod
    def from_container(
        container: DIContainer,
        call_llm: Optional[Callable] = None,
    ) -> "Harness":
        """从 DI 容器构造 Harness 实例。

        Args:
            container: 已装配好组件的 DI 容器。
            call_llm: LLM 调用函数（可选，测试时可用 mock）。

        Returns:
            Harness: 可运行的框架实例。

        Raises:
            ComponentNotRegisteredError: InputAdapter 未注册（必需组件）。
        """
        # 验证必需组件 InputAdapter 已注册
        if not container.is_registered(InputAdapter):
            raise ComponentNotRegisteredError(
                "InputAdapter is required but not registered"
            )
        orchestrator = LifecycleOrchestrator(container, call_llm=call_llm)
        return Harness(orchestrator)

    def run(self) -> None:
        """启动完整的会话生命周期。

        等价于 LifecycleOrchestrator.run()。
        """
        self._orchestrator.run()
