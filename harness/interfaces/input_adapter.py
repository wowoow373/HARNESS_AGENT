"""InputAdapter 接口 — 输入输出适配。

接收用户原始输入转为标准化请求，将 Agent 响应返回给用户。
"""

from typing import Protocol, runtime_checkable

from .types import Response, UserRequest


@runtime_checkable
class InputAdapter(Protocol):
    """输入输出适配器接口。

    职责：
    - receive()：接收用户原始输入，转为标准化的 UserRequest
    - send()：将 LLM 的 Response 返回给用户

    调用时机：
    - receive()：会话初始化时由框架调用，以及后续每轮用户有新输入时
    - send()：每次 LLM 返回包含 text 的 Response 时由框架调用

    实现示例：CliAdapter — stdin 读取输入，stdout 打印响应
    """

    def receive(self) -> UserRequest:
        """接收用户输入并返回标准化请求。

        Returns:
            UserRequest: 标准化用户请求对象。
        """
        ...

    def send(self, response: Response) -> None:
        """将 Agent 响应返回给用户。

        Args:
            response: LLM 返回的 Response 对象。
        """
        ...
