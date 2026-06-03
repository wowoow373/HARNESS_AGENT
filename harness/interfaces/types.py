"""Harness Agent Template — 统一大包对象（Data Transfer Objects）。

跨组件传递的数据结构集中定义在此模块。所有 dataclass 仅包含字段声明与默认值，
不包含任何业务逻辑或实现代码。

用途：作为组件接口（Protocol）的方法参数与返回类型，约束组件间数据交换格式。

模块边界：此模块是纯类型层，不 import 项目的任何其他模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 基础类型（无前向引用依赖）
# ---------------------------------------------------------------------------


@dataclass
class SystemState:
    """系统当前状态，由框架维护，贯穿整个生命周期。

    Attributes:
        phase: 当前阶段（"init" | "loop" | "end"）。
        session_id: 会话标识。
        run_mode: 运行模式（"normal" | "debug" | "dry_run"）。
        metadata: 扩展桶。
    """
    phase: str = "init"
    session_id: str = ""
    run_mode: str = "normal"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Attachment:
    """用户请求中的附件单元。

    Attributes:
        type: 附件类型（"file" | "image" | "url"）。
        content: 附件内容，根据 type 不同使用方式。
        meta: 附件元数据。
    """
    type: str = "file"
    content: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EnvState:
    """环境状态，在 GuideContext 中传递给 GuideProvider。

    Attributes:
        work_dir: 工作目录。
        git_status: Git 状态摘要。
        timestamp: 当前时间戳。
        platform: 操作系统平台（"linux" | "macos" | "windows"）。
    """
    work_dir: str = ""
    git_status: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    platform: str = ""


@dataclass
class Example:
    """少样本示例条目。

    Attributes:
        input: 示例输入。
        output: 示例预期输出。
    """
    input: str = ""
    output: str = ""


@dataclass
class ToolCallFunction:
    """工具调用函数描述。

    Attributes:
        name: 函数名。
        arguments: JSON 编码的参数字符串。
    """
    name: str = ""
    arguments: str = "{}"


@dataclass
class ToolDefinition:
    """Tool 的元信息，用于 LLM 的 tool schema 生成和 ToolRouter 发现。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        parameters: JSON Schema 格式的参数定义。
    """
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具执行返回结果。

    Attributes:
        success: 是否执行成功。
        content: 成功时的工具返回结果。
        error: 失败时的错误信息。
    """
    success: bool = True
    content: Any = None
    error: Optional[str] = None


@dataclass
class ToolTransform:
    """单个 MCP 工具的转换声明。

    用于声明式配置 MCP 工具的转换行为：重命名、隐藏、注入默认参数等。
    当声明式不够用时，可通过 MCPHandler 实现程序化转换。

    Attributes:
        expose_as: 对外暴露的名称（None 表示使用原始名称）。
        description_override: 覆盖工具描述（None 表示使用原始描述）。
        hidden: 是否对 LLM 隐藏（True 时工具不可见但内部可执行）。
        arg_defaults: 注入的默认参数（合并到 LLM 传入的参数中，LLM 值优先）。
        arg_transform: 高级程序化参数转换（可选 Callable）。
        result_transform: 高级程序化结果转换（可选 Callable）。
    """

    expose_as: Optional[str] = None
    description_override: Optional[str] = None
    hidden: bool = False
    arg_defaults: Dict[str, Any] = field(default_factory=dict)
    arg_transform: Optional[Any] = None  # Callable[[Dict], Dict]
    result_transform: Optional[Any] = None  # Callable[[Any], Any]


@dataclass
class MemoryItem:
    """从 MemoryBackend 检索出的记忆项。

    Attributes:
        key: 记忆键。
        value: 记忆值。
        namespace: 命名空间。
        timestamp: 写入时间戳。
        metadata: 扩展元数据。
    """
    key: str = ""
    value: Any = None
    namespace: str = ""
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """对话消息单元。

    Attributes:
        role: 消息角色（"system" | "user" | "assistant" | "tool"）。
        content: 消息文本内容。
        tool_call_id: 当 role="tool" 时，关联的 tool_use 标识。
    """
    role: str = "user"
    content: str = ""
    tool_call_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 组合类型（依赖上面已创建的类型）
# ---------------------------------------------------------------------------


@dataclass
class UserRequest:
    """标准化用户请求，由 InputAdapter.receive() 产出。

    Attributes:
        text: 用户主输入文本。
        attachments: 附件列表（文件、图片、链接等）。
        context: 附加上下文（地理位置、当前文件等）。
        system_state: 系统当前状态。
        session_id: 会话标识。
        metadata: 领域扩展桶，框架不解释。
    """
    text: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    system_state: SystemState = field(default_factory=SystemState)
    session_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """单次工具调用请求（执行前），遵循 OpenAI 原生 tool call 格式。

    Attributes:
        id: 工具调用唯一标识。
        type: 固定为 "function"。
        function: 函数名与参数。
    """
    id: str = ""
    type: str = "function"
    function: ToolCallFunction = field(default_factory=ToolCallFunction)


@dataclass
class ToolCallRecord:
    """单次工具调用的完整执行记录（执行后）。

    Attributes:
        tool_name: 工具名称。
        arguments: 工具调用参数。
        result: 工具执行结果。
        started_at: 执行开始时间戳。
        finished_at: 执行完成时间戳。
        error: 如果执行失败，记录错误信息。
    """
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    started_at: float = 0.0
    finished_at: float = 0.0
    error: Optional[str] = None


@dataclass
class GuidesBundle:
    """由 GuideProvider.get_guides() 产出的完整指导集。

    Attributes:
        identity: 核心身份定义（如 "You are a coding assistant..."）。
        capabilities: 能力清单。
        rules: 行为规则列表。
        constraints: 硬约束列表。
        examples: 少样本示例（可选）。
    """
    identity: str = ""
    capabilities: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    examples: List[Example] = field(default_factory=list)


@dataclass
class Response:
    """单轮 LLM 调用返回，可同时包含文本和工具调用。

    Attributes:
        text: LLM 文本输出。
        thinking: 思考/推理过程（如有）。
        tool_uses: 工具调用列表（可为空）。
        stop_reason: 停止原因（"end_turn" | "tool_use" | "max_tokens" 等）。
    """
    text: Optional[str] = None
    thinking: Optional[str] = None
    tool_uses: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


@dataclass
class AssemblyContext:
    """框架构建的上下文大包，传入 ContextAssembler.assemble()。

    Attributes:
        user_request: 当前用户请求。
        guides: 来自 GuideProvider 的指导集。
        available_tools: 来自 ToolRouter 的可用工具定义列表。
        history: 当前会话的对话历史。
        memories: 从 MemoryBackend 检索的记忆。
        system_state: 系统当前状态。
        metadata: 领域扩展桶，框架不解释。
    """
    user_request: Optional[UserRequest] = None
    guides: Optional[GuidesBundle] = None
    available_tools: List[ToolDefinition] = field(default_factory=list)
    history: List[Message] = field(default_factory=list)
    memories: List[MemoryItem] = field(default_factory=list)
    system_state: SystemState = field(default_factory=SystemState)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trajectory:
    """会话结束后由框架组装，传入 Sensor.sense() 的完整执行轨迹。

    Attributes:
        user_request: 用户原始请求。
        history: 完整对话历史（含思考过程、工具调用）。
        tool_calls: 所有工具调用记录与执行结果。
        final_output: Agent 最终输出。
        execution_time: 执行耗时（秒）。
        system_state: 系统当前状态。
        metadata: 扩展元数据。
    """
    user_request: Optional[UserRequest] = None
    history: List[Message] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    final_output: str = ""
    execution_time: float = 0.0
    system_state: SystemState = field(default_factory=SystemState)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 导出列表
# ---------------------------------------------------------------------------

__all__ = [
    "SystemState",
    "Attachment",
    "EnvState",
    "Example",
    "ToolCallFunction",
    "ToolDefinition",
    "ToolResult",
    "ToolTransform",
    "MemoryItem",
    "Message",
    "UserRequest",
    "ToolCall",
    "ToolCallRecord",
    "GuidesBundle",
    "Response",
    "AssemblyContext",
    "Trajectory",
]
