"""session 测试共享替身与工具。"""

from __future__ import annotations

import asyncio
import functools


def run_async(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用（与 tests/runtime 同约定）。"""
    @functools.wraps(coro_func)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


class MockConsole:
    """Mock SystemConsole：receive 永远阻塞（测试不驱动系统输入），send 收集事件。"""

    def __init__(self):
        self.events = []

    async def receive(self):
        await asyncio.sleep(3600)

    async def send(self, event):
        self.events.append(event)


class MockHarness:
    """最小 harness 替身：container + call_llm（与 tests/runtime 同形）。"""

    def __init__(self, call_llm=None):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = call_llm
