"""工具治理层演示共享的工具集 + 治理策略注册。

三个工具演示两种故障：
- slow_query         → time.sleep(10) 卡住，配 timeout=3 → 超时兜底
- unreliable_divide  → a/b，b=0 时抛真实的 ZeroDivisionError → 异常兜底
- safe_echo          → 正常工具

被 failures_workflow.py（Mode B）和 interactive_demo.py（Mode A）共享。
"""

from __future__ import annotations

import time

from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool
from harness.core.governance.policy import policy_registry, ToolPolicy


class SlowQueryTool(BaseTool):
    """查询销售数据（连接数据库，可能卡住）。"""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="slow_query",
            description="查询销售数据（连接数据库）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                },
                "required": ["query"],
            },
        )

    def execute(self, args) -> ToolResult:
        time.sleep(10)  # 数据库连接卡住，一直未返回
        return ToolResult(success=True, content="这段永远不会返回给 LLM")


class UnreliableDivideTool(BaseTool):
    """计算两个数的比值。"""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="unreliable_divide",
            description="计算两个数的比值",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "被除数"},
                    "b": {"type": "number", "description": "除数"},
                },
                "required": ["a", "b"],
            },
        )

    def execute(self, args) -> ToolResult:
        # b=0 时抛真实的 ZeroDivisionError
        return ToolResult(success=True, content=args["a"] / args["b"])


class SafeEchoTool(BaseTool):
    """向用户发送一条消息。"""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="safe_echo",
            description="向用户发送一条消息",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要发送的消息内容"},
                },
                "required": ["text"],
            },
        )

    def execute(self, args) -> ToolResult:
        return ToolResult(success=True, content=args["text"])


def demo_tools() -> list:
    """三个演示工具实例。"""
    return [SlowQueryTool(), UnreliableDivideTool(), SafeEchoTool()]


def register_policies() -> None:
    """注册治理策略（进程级单例，注册一次全局生效）。"""
    policy_registry.register("slow_query", ToolPolicy(timeout=3))
    policy_registry.register("unreliable_divide", ToolPolicy(timeout=5))
    # safe_echo 用内置默认策略（timeout=60）
