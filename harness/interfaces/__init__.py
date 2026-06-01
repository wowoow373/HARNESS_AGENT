"""Harness Agent Template — 组件接口类型。

batch-01 中的占位类型，仅作为 DI 容器的注册 key 使用。
后续版本会替换为正式的 Protocol/ABC 定义。
"""


class InputAdapter:
    """输入输出适配器接口。"""
    pass


class GuideProvider:
    """前馈指导提供者接口。"""
    pass


class ContextAssembler:
    """上下文组装器接口。"""
    pass


class MemoryBackend:
    """记忆后端接口。"""
    pass


class Sensor:
    """反馈传感器接口。"""
    pass


class ToolRegistry:
    """工具注册表接口。"""
    pass


class Tool:
    """工具接口。"""
    pass


class MCPManager:
    """MCP 管理器接口。"""
    pass
