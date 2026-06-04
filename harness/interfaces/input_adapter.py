"""InputAdapter 接口 — 输入输出适配。

接收用户原始输入转为标准化请求，将 Agent 事件推送给前端。
"""

from typing import Protocol, runtime_checkable

from .types import AdapterEvent, UserRequest


@runtime_checkable
class InputAdapter(Protocol):
    """输入输出适配器接口。

    职责：
    - receive()：接收用户原始输入，转为标准化的 UserRequest
    - send()：接收一个前端事件并呈现给用户

    调用时机：
    - receive()：会话初始化时由框架调用，以及后续每轮用户有新输入时
    - send()：编排器按 LLM 输出字段顺序逐一推送事件。
      前端自主决定每个事件类型的呈现方式（stdout/stderr/TUI面板/...）。

    事件类型（参见 harness.interfaces.types）：
    - ThinkingEvent：LLM 思考/推理过程（后台信息）
    - ToolCallEvent：工具调用开始（后台状态）
    - ToolResultEvent：工具执行结果（后台状态）
    - TextEvent：模型文本回复（前台对话）
    - StopEvent：本轮结束（会话控制）

    实现示例：CliAdapter — Thinking/Tool→stderr，Text→stdout
    """

    def receive(self) -> UserRequest:
        """接收用户输入并返回标准化请求。

        Returns:
            UserRequest: 标准化用户请求对象。
        """
        ...

    def send(self, event: AdapterEvent) -> None:
        """接收一个前端事件并呈现给用户。

        编排器按 LLM 输出字段顺序逐一推送事件。
        前端通过 isinstance / match-case 分发到不同输出通道。

        Args:
            event: 前端事件（ThinkingEvent | ToolCallEvent |
                   ToolResultEvent | TextEvent | StopEvent）。
        """
        ...
