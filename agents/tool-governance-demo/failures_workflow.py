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
            description="查询远端数据（模拟慢服务，会长时间无响应）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                },
                "required": ["query"],
            },
        )

    def execute(self, args) -> ToolResult:
        print("    [工具] slow_query 被调用，开始执行（将卡住 10s，治理层 3s 超时）...",
              flush=True)
        time.sleep(10)  # 一直未响应：既不返回也不抛异常
        return ToolResult(success=True, content="这段永远不会返回给 LLM")


class UnreliableDivideTool(BaseTool):
    """会抛异常的工具（模拟内部有 bug）。"""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="unreliable_divide",
            description="执行除法运算（模拟内部有 bug、会抛异常的工具）",
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
        print("    [工具] unreliable_divide 被调用，抛出异常...", flush=True)
        raise RuntimeError("工具内部错误：除数为零导致崩溃")


class SafeEchoTool(BaseTool):
    """正常可靠的工具。"""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="safe_echo",
            description="安全地回显文本（可靠工具）",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要回显的文本"},
                },
                "required": ["text"],
            },
        )

    def execute(self, args) -> ToolResult:
        print("    [工具] safe_echo 执行成功", flush=True)
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
        "你是面试演示 agent「failure_demo」。本次任务是展示工具治理层如何兜底工具故障。\n\n"
        "请严格按以下顺序依次调用工具，每个工具只调用一次，无论返回什么结果都继续下一步：\n"
        "1. slow_query，参数 query=\"test\"\n"
        "2. unreliable_divide，参数 a=1, b=0\n"
        "3. safe_echo，参数 text=\"面试演示成功：超时和异常都被框架兜底，agent 恢复了\"\n\n"
        "完成后，用一句话总结你经历的三个工具调用的结果，然后调用 finish_agent 结束自己。\n\n"
        "注意：前两个工具会失败（第一个超时、第二个抛异常），这是预期的，请务必继续，"
        "不要因为失败而停下。"
    ),
    metadata={"role": "failure_demo", "task": "演示超时/异常兜底"},
)
def assemble_failure_demo():
    return _assemble_agent("failure_demo")
