"""Sensor 接口 — 反馈控制。

读取完整执行轨迹，按自定义规则评估，将沉淀的知识写入 MemoryBackend。
"""

from typing import Protocol, runtime_checkable

from .types import Trajectory


@runtime_checkable
class Sensor(Protocol):
    """反馈传感器接口。

    职责：读取完整执行轨迹，按自定义规则评估，将沉淀的知识写入 MemoryBackend。

    调用时机：会话结束阶段，在 on_session_end Hook 触发之后、
    after_sensor Hook 触发之前，由框架调用。

    设计要点：
    - Sensor 是副作用组件，不显式返回值给框架
    - Sensor 通过构造函数注入获得 MemoryBackend 引用
    - Sensor 在会话结束时统一评估完整的多轮 Trajectory
    - 用户可在 Sensor 内部接入另一个 Agent 做复杂评估

    实现示例：LoggingSensor — 将轨迹记录到 MemoryBackend 的 episodic 命名空间
    """

    def sense(self, trajectory: Trajectory) -> None:
        """评估完整执行轨迹并将知识写入 MemoryBackend。

        Args:
            trajectory: 完整的会话执行轨迹。
        """
        ...
