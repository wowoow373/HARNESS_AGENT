"""Harness Agent Template — Hook 注册与执行管理器。

职责：维护事件名到 Hook 函数列表的映射，按注册顺序依次触发。
不依赖 DI 容器，由 LifecycleOrchestrator 在 ``__init__`` 中实例化。
"""

import logging
from typing import Any, Dict, List

from ..interfaces.hook import Hook, HookContext
from ..interfaces.types import SystemState

logger = logging.getLogger(__name__)


class HookManager:
    """Hook 注册与执行管理器。

    维护事件名到 Hook 函数列表的映射，按注册顺序依次触发。
    单个 Hook 异常不影响同事件的其他 Hook 和框架主流程。

    用法::

        hm = HookManager()
        hm.register(EVENT_BEFORE_LLM_CALL, my_hook)
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, data, system_state)
    """

    def __init__(self):
        """初始化空的 Hook 注册表。"""
        self._hooks: Dict[str, List[Hook]] = {}

    def register(self, event: str, hook: Hook) -> None:
        """注册一个 Hook 到指定事件。

        同一 Hook 可被多次注册到同一事件（每次注册独立，
        ``unregister`` 只移除第一次匹配）。

        Args:
            event: 生命周期事件名（非空字符串）。
            hook: Hook 函数，签名 ``(context: HookContext) -> None``。

        Raises:
            ValueError: event 为空字符串或 hook 不可调用。
        """
        if not event:
            raise ValueError("event must be a non-empty string")
        if not callable(hook):
            raise ValueError("hook must be callable")

        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append(hook)
        logger.debug(f"Hook registered for event '{event}'")

    def unregister(self, event: str, hook: Hook) -> None:
        """从指定事件注销一个 Hook。

        使用 ``is`` 身份比较移除第一次匹配的 hook。
        未找到时不抛异常，记录 DEBUG 日志。

        Args:
            event: 生命周期事件名。
            hook: 要注销的 Hook 函数。
        """
        if event not in self._hooks:
            logger.debug(f"Cannot unregister: event '{event}' has no hooks")
            return

        hooks = self._hooks[event]
        for i, h in enumerate(hooks):
            if h is hook:
                hooks.pop(i)
                logger.debug(f"Hook unregistered from event '{event}'")
                return

        logger.debug(f"Hook not found in event '{event}' for unregister")

    def trigger(self, event: str, data: Any, system_state: SystemState) -> Any:
        """触发事件，执行所有注册的 Hook。

        行为：

        - 按注册顺序依次调用 Hook
        - 每个 Hook 可修改 ``context.data``，修改对后续 Hook 可见
        - 单个 Hook 抛异常时：记录 WARNING，跳过该 Hook，继续执行后续 Hook
        - 无 Hook 注册时：直接返回原始 data

        Args:
            event: 生命周期事件名。
            data: 传递给 Hook 的数据对象（可被修改）。
            system_state: 系统当前状态。

        Returns:
            经过所有 Hook 处理后的 data（可能已被修改）。
        """
        hooks = self._hooks.get(event)
        if not hooks:
            return data

        context = HookContext(event=event, data=data, system_state=system_state)

        for hook in hooks:
            try:
                hook(context)
            except Exception as exc:
                logger.warning(
                    f"Hook for event '{event}' raised {type(exc).__name__}: {exc}"
                )

        return context.data
