"""Harness Agent Template — 内部数据结构。

batch-01 MVP 的最小化数据类型。batch-02-1 会替换为
``harness/interfaces/`` 中的正式类型定义。此模块届时标记为废弃。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class _MinimalUserRequest:
    """最小化的用户请求表示。

    Attributes:
        text: 用户主输入文本。
        metadata: 附加元数据。
    """
    text: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _MinimalGuidesBundle:
    """最小化的 GuidesBundle 表示。

    Attributes:
        identity: 核心身份定义。
        capabilities: 能力清单。
        rules: 行为规则列表。
        constraints: 硬约束列表。
        examples: 少样本示例列表。
    """
    identity: str = ""
    capabilities: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    examples: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class _MinimalAssemblyContext:
    """最小化的 AssemblyContext 表示。

    Attributes:
        user_request: 当前用户请求。
        guides: 来自 GuideProvider 的 GuidesBundle。
        available_tools: 可用工具定义列表。
        history: 当前会话的对话历史。
        memories: 从 MemoryBackend 检索的记忆。
        system_state: 系统当前状态。
        metadata: 领域扩展桶，框架不解释。
    """
    user_request: Optional[_MinimalUserRequest] = None
    guides: Optional[_MinimalGuidesBundle] = None
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    memories: List[Dict[str, Any]] = field(default_factory=list)
    system_state: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _MinimalToolCallFunction:
    """工具调用函数描述。

    Attributes:
        name: 函数名。
        arguments: JSON 编码的参数字符串。
    """
    name: str = ""
    arguments: str = "{}"


@dataclass
class _MinimalToolCall:
    """最小化的 ToolCall 表示。遵循 OpenAI tool call 格式。

    Attributes:
        id: tool call 唯一标识（如 "call_abc123"）。
        type: 固定为 "function"。
        function: 函数名与参数。
    """
    id: str
    type: str = "function"
    function: _MinimalToolCallFunction = field(default_factory=_MinimalToolCallFunction)

    def parse_arguments(self) -> Dict[str, Any]:
        """将 function.arguments JSON string 解析为 dict。

        Returns:
            解析后的参数字典。
        """
        return json.loads(self.function.arguments)


@dataclass
class _MinimalResponse:
    """最小化的 LLM Response 表示。

    设计要求：
    - text 和 tool_uses 可同时非空。
    - tool_uses 为空列表时表示纯文本响应。
    - text 为 None 且 tool_uses 非空时表示纯工具调用响应。

    Attributes:
        text: LLM 文本输出（可为 None）。
        thinking: LLM 思考/推理过程（可为 None）。
        tool_uses: 工具调用列表（可为空）。
        stop_reason: 停止原因。
    """
    text: Optional[str] = None
    thinking: Optional[str] = None
    tool_uses: List[_MinimalToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


@dataclass
class _MinimalTrajectory:
    """最小化的 Trajectory 表示。

    Attributes:
        user_request: 用户原始请求。
        history: 完整对话历史。
        tool_calls: 所有工具调用记录与执行结果。
        final_output: Agent 最终输出。
        execution_time: 执行耗时（秒）。
        system_state: 系统当前状态。
        metadata: 扩展元数据。
    """
    user_request: Optional[_MinimalUserRequest] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    final_output: str = ""
    execution_time: float = 0.0
    system_state: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
