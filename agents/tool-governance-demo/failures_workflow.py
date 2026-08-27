"""Failure handling demo workflow — 工具抛异常 + 工具一直未响应。

面试演示：工具治理层的故障兜底能力。

三个演示工具：
- slow_query         → time.sleep(10) 模拟「一直未响应」，配 timeout=3 → 3s 超时
- unreliable_divide  → raise RuntimeError 模拟「工具自己抛异常」
- safe_echo          → 正常工具，agent 在两次失败后换用它完成

真实 LLM 驱动 agent 依次调用这三个工具，观察：
  1. 第一个工具超时 → agent 没被卡住，收到超时错误
  2. 第二个工具抛异常 → agent 没崩溃，收到异常错误
  3. agent 换第三个正常工具 → 完成

运行:
    python agents/tool-governance-demo/demo_failures.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 将项目根目录加入 sys.path（使得脚本可以在任意位置被加载）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    InputAdapter,
    MemoryBackend,
    ContextAssembler,
    Sensor,
    SystemToolProvider,
)
from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.runtime.decorators import agent

# 工具治理策略
from harness.core.governance.policy import policy_registry, ToolPolicy


# ── 演示工具 ──────────────────────────────────────────────────────


class SlowQueryTool(BaseTool):
    """一直未响应的工具（sleep 10s，模拟慢服务卡死）。"""

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
    """会抛异常的工具（模拟内部有 bug）。"""

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
    """正常可靠的工具。"""

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


# ── 治理策略注册 ──────────────────────────────────────────────────

# slow_query：3 秒超时（工具 sleep 10s，必然超时）
policy_registry.register("slow_query", ToolPolicy(timeout=3))

# unreliable_divide：异常会被兜底；给 5s 超时兜底（防御性）
policy_registry.register("unreliable_divide", ToolPolicy(timeout=5))

# safe_echo 用内置默认策略（timeout=60，不重试，不 gate）


def _assemble_agent(name: str) -> Harness:
    """装配一个只暴露三个演示工具的 agent。"""
    container = DIContainer()

    memory = MdMemory(path=f"./memory/failures_demo/{name}")
    container.register(MemoryBackend, memory)
    container.register(InputAdapter, object())  # 由 KernelBridgeAdapter 覆盖
    container.register(
        ContextAssembler,
        SimpleAssembler(max_history=100, memory=memory),
    )
    container.register(Sensor, LoggingSensor(memory=memory))
    # 只暴露三个演示工具（use_builtins=False，避免 read_file/shell 干扰）
    container.register(
        SystemToolProvider,
        DefaultSystemToolProvider(
            tools=[SlowQueryTool(), UnreliableDivideTool(), SafeEchoTool()],
            use_builtins=False,
        ),
    )

    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


@agent(
    "failure_demo",
    entry_prompt=(
        "你是数据分析助手。请完成以下任务：\n"
        "1. 用 slow_query 工具查询销售数据（参数 query=\"sales\"）\n"
        "2. 用 unreliable_divide 工具计算转化率（参数 a=100, b=0）\n"
        "3. 用 safe_echo 工具把最终结论发送给用户（参数 text 写你的结论）\n\n"
        "如果某个步骤失败，继续尝试后续步骤即可。全部完成后，告诉我整个过程发生了什么。"
    ),
    metadata={"role": "failure_demo", "task": "演示超时/异常兜底"},
)
def assemble_failure_demo():
    return _assemble_agent("failure_demo")
