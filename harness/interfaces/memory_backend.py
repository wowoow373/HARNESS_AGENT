"""MemoryBackend 接口 — 跨会话持久化存储与检索。

提供键值读写、语义搜索和命名空间管理能力。
"""

from typing import Any, List, Optional, Protocol, runtime_checkable

from .types import MemoryItem


@runtime_checkable
class MemoryBackend(Protocol):
    """记忆后端接口。

    职责：跨会话的持久化存储与检索。

    调用时机：
    - 会话初始化阶段：框架从 MemoryBackend 检索相关记忆（search()），
      填入 AssemblyContext
    - 会话结束阶段：Sensor 调用 write() 写入知识

    命名空间约定（非强制，社区约定）：
    - episodic: 事件记忆（对话摘要）
    - semantic: 事实知识（用户偏好）
    - procedural: 技能/可复用模式
    - sensor_raw: Sensor 原始评估
    - system: 系统状态缓存

    实现示例：JsonlMemory — 追加式 JSONL 文件存储，启动时构建内存索引
    """

    def read(self, key: str, namespace: str) -> Optional[Any]:
        """按 key 读取记忆值。

        Args:
            key: 记忆键。
            namespace: 命名空间。

        Returns:
            Optional[Any]: 记忆值，不存在时为 None。
        """
        ...

    def write(self, key: str, value: Any, namespace: str) -> None:
        """写入记忆值。

        Args:
            key: 记忆键。
            value: 记忆值。
            namespace: 命名空间。
        """
        ...

    def search(self, query: str, namespace: str, limit: int = 10) -> List[MemoryItem]:
        """搜索相关记忆。

        Args:
            query: 搜索查询。
            namespace: 命名空间。
            limit: 最大返回条数，默认 10。

        Returns:
            List[MemoryItem]: 匹配的记忆项列表。
        """
        ...

    def list_namespaces(self) -> List[str]:
        """列出所有已知命名空间。

        Returns:
            List[str]: 命名空间名称列表。
        """
        ...
