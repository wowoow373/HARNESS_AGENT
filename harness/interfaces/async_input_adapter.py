"""AsyncInputAdapter 接口 — 异步输入输出适配。

Runtime 路径下所有 agent 的 I/O 通道接口。
与同步 InputAdapter 的区别：receive()/send() 均为 async，
send() 新增 target 参数支持定向投递。
"""

from typing import Protocol, runtime_checkable

from .types import AdapterEvent, UserRequest


@runtime_checkable
class AsyncInputAdapter(Protocol):
    """异步输入输出适配器接口。

    职责：
    - receive()：异步接收输入，返回标准化的 UserRequest
    - send()：异步推送事件，可选定向投递到指定 agent

    与同步 InputAdapter 的区别：
    - receive() / send() 均为 async def，兼容 asyncio 队列和 MessageBus
    - send() 新增 target 参数：None 走 pub-sub 路由，指定 pid 走定向投递

    实现类：KernelBridgeAdapter（harness.runtime.bridge_adapter）
    """

    async def receive(self) -> UserRequest:
        """异步接收输入并返回标准化请求。

        Returns:
            UserRequest: 标准化用户请求对象。
        """
        ...

    async def send(self, event: AdapterEvent, target: str | None = None) -> None:
        """异步推送事件。

        编排器按 LLM 输出字段顺序逐一推送事件。

        Args:
            event: 前端事件（ThinkingEvent | ToolCallEvent |
                   ToolResultEvent | TextEvent | StopEvent）。
            target: 定向投递目标 pid。None 表示走 pub-sub 路由。
        """
        ...
