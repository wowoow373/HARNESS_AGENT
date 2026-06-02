"""Hook 接口 — 生命周期拦截。

在框架生命周期的关键节点插入自定义逻辑。
Hook 是函数类型，不是类 Protocol。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from .types import SystemState


@dataclass
class HookContext:
    """Hook 触发时的上下文对象（所有 Hook 点统一使用此结构）。

    Hook 通过修改 HookContext.data 实现拦截效果。

    Attributes:
        event: 生命周期事件名（如 "before_llm_call"）。
        data: 该阶段的数据对象（可修改，类型依赖于事件名）。
        system_state: 系统当前状态（所有 Hook 均可访问）。
    """
    event: str = ""
    data: Any = None
    system_state: SystemState = field(default_factory=SystemState)


# Hook 是函数类型别名，不是类 Protocol
Hook = Callable[[HookContext], None]
"""Hook 函数类型。

签名: (context: HookContext) -> None

Hook 通过修改 HookContext.data 实现拦截效果。
Hook 点列表（事件名 → data 类型），共 11 个：

- before_guide_generation → GuideContext
- after_guide_generation → GuidesBundle
- before_assemble → AssemblyContext
- after_assemble → List[Message]
- before_llm_call → List[Message]
- after_llm_call → Response
- before_tool_execute → ToolCall
- after_tool_execute → ToolResult
- after_sensor → Trajectory（只读观察）
- on_session_end → Trajectory
- on_error → Exception
"""
