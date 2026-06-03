"""Harness Agent Template — 组件接口类型。

该模块提供框架的所有公开接口契约和共享数据类型：

- 17 个大包对象（dataclass）：跨组件传递的数据结构
- 10 个组件接口（9 个 Protocol + 1 个类型别名）：组件间解耦的抽象契约
- 2 个辅助类型 + 1 个类型别名：GuideContext、HookContext、Hook

所有实现组件通过满足这些 Protocol 即可接入框架。
框架内核（core/）通过此模块的 import 路径获取接口类型作为 DI 容器的注册 key。

模块边界：此包不依赖任何实现模块（core/、adapters/、components/）。
"""

# ── 大包对象（17 个 dataclass） ──────────────────────────────────────────
from .types import (
    AssemblyContext,
    Attachment,
    EnvState,
    Example,
    GuidesBundle,
    MemoryItem,
    Message,
    Response,
    SystemState,
    ToolCall,
    ToolCallFunction,
    ToolCallRecord,
    ToolDefinition,
    ToolResult,
    ToolTransform,
    Trajectory,
    UserRequest,
)

# ── 组件接口（9 个 Protocol + 1 个类型别名） ─────────────────────────────
from .input_adapter import InputAdapter
from .guide_provider import GuideContext, GuideProvider
from .context_assembler import ContextAssembler
from .memory_backend import MemoryBackend
from .sensor import Sensor
from .tool import Tool
from .system_tool_provider import SystemToolProvider
from .mcp_adapter import MCPAdapter
from .mcp_handler import MCPHandler
from .hook import Hook, HookContext

__all__ = [
    # 17 个大包对象
    "UserRequest",
    "SystemState",
    "Attachment",
    "EnvState",
    "GuidesBundle",
    "Example",
    "AssemblyContext",
    "Trajectory",
    "Message",
    "Response",
    "ToolCall",
    "ToolCallFunction",
    "ToolDefinition",
    "ToolCallRecord",
    "ToolResult",
    "ToolTransform",
    "MemoryItem",
    # 10 个组件接口（9 个 Protocol + 1 个函数类型别名）
    "InputAdapter",
    "GuideProvider",
    "ContextAssembler",
    "MemoryBackend",
    "Sensor",
    "Tool",
    "SystemToolProvider",
    "MCPAdapter",
    "MCPHandler",
    "Hook",
    # 辅助类型（GuideProvider 的上下文 + Hook 的上下文）
    "GuideContext",
    "HookContext",
]
