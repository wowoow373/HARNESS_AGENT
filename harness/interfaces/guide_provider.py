"""GuideProvider 接口 — 前馈控制。

在 Agent 行动前提供所有指导性输入（身份定义、能力清单、行为规则、硬约束等）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .types import EnvState, GuidesBundle, SystemState, UserRequest


@dataclass
class GuideContext:
    """传入 GuideProvider.get_guides() 的上下文参数。

    Attributes:
        user_request: 用户当前请求。
        system_state: 系统当前状态（含 phase）。
        env_state: 环境状态。
        metadata: 扩展元数据。
    """
    user_request: Optional[UserRequest] = None
    system_state: SystemState = field(default_factory=SystemState)
    env_state: Optional[EnvState] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GuideProvider(Protocol):
    """前馈指导提供者接口。

    职责：在 Agent 行动前提供所有指导性输入。

    调用时机：会话初始化时由框架调用，只调用一次。
    产出 GuidesBundle 后被框架缓存复用。

    实现示例：FileGuideProvider — 从文件系统读取静态配置（如 AGENTS.md）
    """

    def get_guides(self, context: GuideContext) -> GuidesBundle:
        """根据上下文生成指导集。

        Args:
            context: 包含用户请求、系统状态、环境状态的上下文。

        Returns:
            GuidesBundle: 完整的指导集（身份、能力、规则、约束、示例）。
        """
        ...
