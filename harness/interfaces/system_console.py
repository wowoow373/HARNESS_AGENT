"""SystemConsole 接口 — Runtime 系统级交互。

Runtime 路径下的用户交互入口，与 AsyncInputAdapter 完全独立：
- AsyncInputAdapter：一个 agent 的 stdin/stdout
- SystemConsole：整个 Runtime 的"控制台"，处理系统命令和事件
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SystemConsole(Protocol):
    """Runtime 系统级交互接口。

    职责：
    - receive()：接收用户输入，返回解析后的系统命令
    - send()：推送系统级事件（agent 状态变更、agent 输出降级等）

    与 AsyncInputAdapter 的区别：
    - AsyncInputAdapter 的 receive() 返回 UserRequest（LLM 看到的消息）
    - SystemConsole 的 receive() 返回 SystemCommand（Kernel 处理的命令）
    - AsyncInputAdapter 的 send() 推送 AdapterEvent（前端事件流）
    - SystemConsole 的 send() 推送 SystemEvent（系统通知）

    实现类：CliConsole（harness.runtime.cli_console）
    """

    async def receive(self) -> 'SystemCommand':
        """接收用户输入，返回解析后的系统命令。

        Returns:
            SystemCommand: 解析后的系统命令。
                          Batch 1 仅返回 CommandTalk。
        """
        ...

    async def send(self, event: 'SystemEvent') -> None:
        """推送系统级事件。

        实现方负责格式化输出到对应前端（CLI stdout、WebSocket 等）。

        Args:
            event: 系统事件（AgentSpawned | AgentStateChanged |
                   AgentFinished | AgentOutput | RuntimeStarted |
                   RuntimeStopped）。
        """
        ...
