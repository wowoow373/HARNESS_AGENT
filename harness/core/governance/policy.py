"""工具治理策略模型与注册表。

ToolPolicy 描述单个工具的治理策略（超时/重试/Gate/执行器）。
PolicyRegistry 提供代码注册式策略匹配：
精确名 > fnmatch 通配（注册顺序后者优先）> 默认策略。

匹配顺序实现说明：先扫一遍精确名（后者覆盖前者），再扫一遍通配
（后者覆盖前者），精确名整体优先于通配，最后兜底默认策略。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    """工具失败重试策略。

    Attributes:
        max_attempts: 最大尝试次数（1 = 不重试）。
        backoff: 退避策略。"fixed" | "exponential"。
        base_delay: 基础退避延迟（秒）。
        retry_on: 可重试的失败类别集合（"timeout" / "exception"）。
    """
    max_attempts: int = 1
    backoff: str = "exponential"
    base_delay: float = 0.5
    retry_on: tuple = ("timeout", "exception")


@dataclass
class ToolPolicy:
    """单个工具的治理策略。

    Attributes:
        timeout: 单次执行超时（秒）。
        retry: 重试策略。
        gate: True = 需人工审批。
        approval_timeout: 审批等待超时（秒）。
        executor: "thread"（to_thread 包装，支持超时）| "direct"（事件循环内直接调用）。
    """
    timeout: float = 60.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    gate: bool = False
    approval_timeout: float = 300.0
    executor: str = "thread"


# Runtime 管理工具名。它们操作 kernel 内存态，必须在事件循环内直接调用
# （放线程会引入竞态），且默认 gate=False。
RUNTIME_TOOL_NAMES: tuple = (
    "spawn_workflow", "end_workflow", "finish_agent", "talk_to", "list_agents",
)


class PolicyRegistry:
    """代码注册式策略表（进程级）。

    用法::

        registry.register("delete_file", ToolPolicy(gate=True, timeout=10))
        registry.register("mcp_*", ToolPolicy(timeout=30))
        registry.set_default(ToolPolicy(timeout=60))
        policy = registry.lookup(tool_name)
    """

    def __init__(self):
        self._rules: list[tuple[str, ToolPolicy]] = []
        self._default = ToolPolicy()

    def register(self, pattern: str, policy: ToolPolicy) -> None:
        """注册一条规则。pattern 支持 fnmatch 通配（"delete_*"、"*"）。"""
        self._rules.append((pattern, policy))

    def set_default(self, policy: ToolPolicy) -> None:
        """覆盖内置默认策略。"""
        self._default = policy

    def lookup(self, tool_name: str) -> ToolPolicy:
        """按优先级返回策略，永不返回 None。

        优先级：精确名（后者优先）> 通配（后者优先）> 默认策略。
        """
        exact: ToolPolicy | None = None
        for pattern, policy in self._rules:
            if pattern == tool_name:
                exact = policy
        if exact is not None:
            return exact

        wildcard: ToolPolicy | None = None
        for pattern, policy in self._rules:
            if fnmatch.fnmatch(tool_name, pattern):
                wildcard = policy
        if wildcard is not None:
            return wildcard

        return self._default


# 进程级单例。Kernel 引用它；用户可在装配代码/workflow 脚本中直接 import 注册。
policy_registry = PolicyRegistry()
for _name in RUNTIME_TOOL_NAMES:
    policy_registry.register(_name, ToolPolicy(executor="direct"))
