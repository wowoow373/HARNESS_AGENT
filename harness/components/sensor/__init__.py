"""Sensor 组件包 — 默认实现。

提供 LoggingSensor：将完整执行轨迹写入 MemoryBackend 的 episodic 命名空间。
"""

from .logging_sensor import LoggingSensor

__all__ = ["LoggingSensor"]
