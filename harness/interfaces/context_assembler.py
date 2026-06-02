"""ContextAssembler 接口 — 上下文工程。

将 Harness 所有信息源组装成发给 LLM 的最终消息列表。
"""

from typing import List, Protocol, runtime_checkable

from .types import AssemblyContext, Message


@runtime_checkable
class ContextAssembler(Protocol):
    """上下文组装器接口。

    职责：接收 AssemblyContext 大包，将其中的用户请求、指导、工具定义、
    对话历史、检索记忆等信息拼接为 List[Message]，作为 LLM 的输入。

    调用时机：每轮外层循环开始时（用户有新输入），框架调用一次。

    设计说明：
    - 框架基线：框架在每轮外层循环开始前自动执行
      memory.search(user_request.text, namespace="episodic")，
      结果填入 AssemblyContext.memories。
      ContextAssembler 的最低实现只需消费 AssemblyContext.memories，
      无需持有 MemoryBackend 引用。
    - 组件增强：当 ContextAssembler 需要超越框架基线的检索策略时
      （如跨 namespace 检索、使用不同 query 策略），可通过构造函数
      注入 MemoryBackend 并在 assemble() 内执行额外检索。

    实现示例：SimpleAssembler — 滑动窗口截断 + 直接拼接
    """

    def assemble(self, inputs: AssemblyContext) -> List[Message]:
        """将 AssemblyContext 组装为 LLM 可消费的消息列表。

        Args:
            inputs: 包含所有上下文信息的 AssemblyContext。

        Returns:
            List[Message]: 按顺序排列的消息列表。
        """
        ...
