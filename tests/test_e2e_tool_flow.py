"""E2E 全流程测试 — Tool/MCP 完整生命周期链。

覆盖验收标准：
- AC-TOOL-12: 完整 E2E — 本地工具从注册到 LLM 调用到 shutdown
- AC-TOOL-13: 完整 E2E — 多 Provider 并行（SystemToolProvider + Mock MCPAdapter）

全链路验证：
  1. DI 注册 SystemToolProvider / MCPAdapter
  2. _phase_init: ToolRouter 创建 → Provider 注册 → list_tools() → ctx.available_tools
  3. ContextAssembler 将工具拼入 LLM messages
  4. LLM tool_use → ToolRouter.execute() → 正确路由
  5. ToolResult → tool_result_message → LLM 再次调用
  6. _phase_end: ToolRouter.shutdown() → Provider 资源清理
"""

import json
import time

import pytest

from harness.core.container import DIContainer
from harness.core.exceptions import ComponentNotRegisteredError
from harness.core.orchestrator import (
    ContextAssembler,
    InputAdapter,
    LifecycleOrchestrator,
    Sensor,
    SystemToolProvider,
)
from harness.interfaces import MCPAdapter
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    ToolCall,
    ToolCallFunction,
    ToolCallRecord,
    ToolDefinition,
    ToolResult,
    Trajectory,
    UserRequest,
)


# ============================================================================
# AC-TOOL-12: E2E 本地工具完整链路
# ============================================================================


class TestE2ELocalToolFlow:
    """AC-TOOL-12: 本地工具端到端全流程。

    验证从 DI 注册到 shutdown 的完整链路。
    """

    def _build_container_with_system_tools(self):
        """构建含 SystemToolProvider 的 DI 容器。"""
        container = DIContainer()

        # 实际文件系统操作 — 使用真实的 DefaultSystemToolProvider
        from harness.components.tool import DefaultSystemToolProvider

        class SingleTurnAdapter:
            def __init__(self):
                self.inputs = ["Read /tmp/test.txt", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(
                    event.content if hasattr(event, "content") else str(event)
                )

        class SpyAssembler:
            def __init__(self):
                self.assemble_calls = []
                self.tools_seen = []

            def assemble(self, ctx):
                self.assemble_calls.append(ctx)
                self.tools_seen.append(list(ctx.available_tools))
                msgs = []
                if ctx.guides and ctx.guides.identity:
                    msgs.append(Message(role="system", content=ctx.guides.identity))
                if ctx.user_request and ctx.user_request.text:
                    msgs.append(Message(role="user", content=ctx.user_request.text))
                return msgs

        class TraceSensor:
            def __init__(self):
                self.called = False
                self.traj = None

            def sense(self, trajectory):
                self.called = True
                self.traj = trajectory

        container.register(InputAdapter, SingleTurnAdapter())
        container.register(SystemToolProvider, DefaultSystemToolProvider())
        container.register(ContextAssembler, SpyAssembler())
        container.register(Sensor, TraceSensor())

        return container

    def test_e2e_tool_flow_to_llm_messages(self):
        """AC-TOOL-12 Step 1-2: 工具从注册到拼入 Context。

        _phase_init() 后：
        - ToolRouter 创建并注册 SystemToolProvider
        - list_tools() 返回默认 3 个工具
        - available_tools 进入 AssemblyContext
        - ContextAssembler 收到含工具的 ctx
        """
        container = self._build_container_with_system_tools()

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        # Step 1: 工具发现
        assert orch._cached_tool_router is not None
        assert orch._cached_tool_router.tool_count >= 3
        tool_names = {t.name for t in orch._cached_tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "shell" in tool_names

        # Step 2: 工具拼入 Context
        assert len(ctx.available_tools) >= 3
        # 每个 ToolDefinition 有有效字段
        for td in ctx.available_tools:
            assert td.name
            assert td.description
            assert isinstance(td.parameters, dict)

    def test_e2e_tool_use_routing_and_result_loop(self):
        """AC-TOOL-12 Step 3-5: LLM tool_use → 路由执行 → 结果回传。

        模拟 LLM 两轮调用：
        1. 第一轮返回 tool_use "read_file"
        2. ToolRouter 路由到 SystemToolProvider 执行
        3. ToolResult → tool_result_message → 追加到 messages
        4. 第二轮 LLM 收到含工具结果的 messages → 返回 text
        """
        import tempfile, os

        from harness.components.tool import DefaultSystemToolProvider

        container = DIContainer()

        class MA:
            def __init__(self):
                self.inputs = ["test", ""]
                self.outputs = []
                self.idx = 0
            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]; self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")
            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        class SA:
            def __init__(self):
                self.assembled = []
            def assemble(self, ctx):
                self.assembled.append(ctx)
                return [Message(role="user", content=ctx.user_request.text or "")]

        container.register(InputAdapter, MA())
        container.register(SystemToolProvider, DefaultSystemToolProvider())
        container.register(ContextAssembler, SA())

        # 写入测试文件
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        tmp.write("hello from e2e test")
        tmp.close()
        tmp_path = tmp.name

        call_count = [0]

        def tool_then_text_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="call_e2e_1",
                            function=ToolCallFunction(
                                name="read_file",
                                arguments=json.dumps({"path": tmp_path}),
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            else:
                tool_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") == "tool"]
                assert len(tool_msgs) >= 1, "Expected tool result message in second LLM call"
                return Response(
                    text="File contents: hello from e2e test",
                    stop_reason="end_turn",
                )

        orch = LifecycleOrchestrator(container, call_llm=tool_then_text_llm)
        orch._cached_guides = GuidesBundle(identity="You are a helpful assistant")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        os.unlink(tmp_path)

        assert len(orch._tool_call_records) == 1, (
            f"Expected 1 tool call record, got {len(orch._tool_call_records)}. "
            f"LLM calls: {call_count[0]}"
        )
        record = orch._tool_call_records[0]
        assert record.tool_name == "read_file"
        assert record.arguments == {"path": tmp_path}
        assert record.error is None
        assert "hello from e2e test" in str(record.result)

        # Step 4: LLM 第二轮被调用并收到工具结果
        assert call_count[0] == 2

        # Step 5: 最终响应发送给用户 (TextEvent + StopEvent per turn,
        #          ToolCallEvent + ToolResultEvent per tool call)
        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) >= 1
        text_outputs = [o for o in adapter.outputs if "hello from e2e test" in str(o)]
        assert len(text_outputs) >= 1

    def test_e2e_shutdown_cleanup(self):
        """AC-TOOL-12 Step 6-7: _phase_end → shutdown → 状态清理。"""
        container = self._build_container_with_system_tools()

        def text_llm(msgs, tools):
            return Response(text="ok", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=text_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        # Step 6: shutdown 后状态清理
        assert len(orch._history) == 0
        assert len(orch._tool_call_records) == 0
        assert orch._should_exit_flag is False

        # Step 7: Sensor 收到 Trajectory
        sensor = container.resolve(Sensor)
        assert sensor.called is True
        assert sensor.traj is not None
        assert isinstance(sensor.traj, Trajectory)
        assert sensor.traj.execution_time >= 0


# ============================================================================
# AC-TOOL-13: E2E 多 Provider 并行
# ============================================================================


class TestE2EMultiProviderFlow:
    """AC-TOOL-13: 多 Provider 并行端到端测试。

    验证 SystemToolProvider + MCPAdapter 同时注册时：
    - 工具合并
    - 正确路由
    - shutdown 分发
    """

    def _build_multi_provider_container(self):
        """构建含 SystemToolProvider + Mock MCPAdapter 的容器。"""
        container = DIContainer()

        class SingleTurnAdapter:
            def __init__(self):
                self.inputs = ["do something"]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(
                    event.content if hasattr(event, "content") else str(event)
                )

        class MockMCPAdapter:
            """Mock MCPAdapter — 暴露 mcp_search 工具。"""
            def __init__(self):
                self.executed = []
                self._shutdown_called = False

            def get_tools(self):
                return [
                    ToolDefinition(
                        name="mcp_search",
                        description="Search files via MCP",
                        parameters={
                            "type": "object",
                            "properties": {
                                "pattern": {"type": "string"}
                            },
                        },
                    )
                ]

            def execute(self, name, args):
                self.executed.append((name, args))
                return ToolResult(
                    success=True,
                    content=f"mcp result for {args.get('pattern', '')}",
                )

            def shutdown(self):
                self._shutdown_called = True

        class TraceSensor:
            def __init__(self):
                self.called = False
                self.traj = None

            def sense(self, trajectory):
                self.called = True
                self.traj = trajectory

        from harness.components.tool import DefaultSystemToolProvider

        container.register(InputAdapter, SingleTurnAdapter())
        container.register(SystemToolProvider, DefaultSystemToolProvider())
        container.register(MCPAdapter, MockMCPAdapter())
        container.register(Sensor, TraceSensor())

        return container

    def test_multi_provider_tools_merged(self):
        """AC-TOOL-13: 多 Provider 工具合并。

        list_tools() 返回 System + MCP 工具的总和。
        """
        container = self._build_multi_provider_container()

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        # 工具合并
        tool_names = {t.name for t in orch._cached_tools}
        assert "read_file" in tool_names  # from SystemToolProvider
        assert "write_file" in tool_names
        assert "shell" in tool_names
        assert "mcp_search" in tool_names  # from MCPAdapter
        assert len(tool_names) == 4

    def test_multi_provider_correct_routing(self):
        """AC-TOOL-13: 执行时正确路由到各自 Provider。

        LLM 调用 read_file → SystemToolProvider
        LLM 调用 mcp_search → MCPAdapter
        """
        container = self._build_multi_provider_container()

        call_count = [0]

        def multi_tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="c1",
                            function=ToolCallFunction(
                                name="mcp_search",
                                arguments=json.dumps({"pattern": "*.py"}),
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            else:
                return Response(text="done", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=multi_tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # MCPAdapter 收到了调用
        mcp = container.resolve(MCPAdapter)
        assert len(mcp.executed) == 1
        assert mcp.executed[0][0] == "mcp_search"
        assert mcp.executed[0][1] == {"pattern": "*.py"}

        # SystemToolProvider 没收到
        stp = container.resolve(SystemToolProvider)
        # DefaultSystemToolProvider 没有 executed list，
        # 但我们可以验证 tool_call_records
        assert len(orch._tool_call_records) == 1
        assert orch._tool_call_records[0].tool_name == "mcp_search"

    def test_multi_provider_shutdown(self):
        """AC-TOOL-13: shutdown 分发到 MCPAdapter。"""
        container = self._build_multi_provider_container()

        def text_llm(msgs, tools):
            return Response(text="ok", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=text_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        # MCPAdapter.shutdown() 被调用
        mcp = container.resolve(MCPAdapter)
        assert mcp._shutdown_called is True


# ============================================================================
# AC-TOOL-04: ToolCallRecord 完整性
# ============================================================================


class TestToolCallRecordIntegrity:
    """AC-TOOL-04 + AC-TOOL-14: ToolCallRecord 完整记录验证。"""

    def test_success_record(self):
        """成功执行的 ToolCallRecord 字段完整。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["use tool", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        class MockSystemProvider:
            def get_tools(self):
                return [
                    ToolDefinition(
                        name="echo",
                        description="Echo back",
                        parameters={"type": "object", "properties": {}},
                    )
                ]

            def execute(self, name, args):
                return ToolResult(success=True, content=f"echo: {args}")

        container.register(InputAdapter, MockAdapter())
        container.register(SystemToolProvider, MockSystemProvider())

        call_count = [0]

        def tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="rec_test_1",
                            function=ToolCallFunction(
                                name="echo",
                                arguments=json.dumps({"msg": "hello"}),
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            return Response(text="got it", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert len(orch._tool_call_records) == 1
        r = orch._tool_call_records[0]
        assert r.tool_name == "echo"
        assert r.arguments == {"msg": "hello"}
        assert "echo" in str(r.result)
        assert r.started_at > 0
        assert r.finished_at >= r.started_at
        assert r.error is None

    def test_failure_record(self):
        """AC-TOOL-14: 执行失败的 ToolCallRecord。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["use tool", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        class FailingProvider:
            def get_tools(self):
                return [
                    ToolDefinition(
                        name="risky_tool",
                        description="Might fail",
                        parameters={"type": "object", "properties": {}},
                    )
                ]

            def execute(self, name, args):
                return ToolResult(
                    success=False,
                    content=None,
                    error="Something went wrong",
                )

        container.register(InputAdapter, MockAdapter())
        container.register(SystemToolProvider, FailingProvider())

        call_count = [0]

        def tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="fail_1",
                            function=ToolCallFunction(
                                name="risky_tool",
                                arguments=json.dumps({}),
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            return Response(text="handled error", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert len(orch._tool_call_records) == 1
        r = orch._tool_call_records[0]
        assert r.tool_name == "risky_tool"
        assert r.result is None
        assert r.error == "Something went wrong"


# ============================================================================
# AC-TOOL-05: MCPAdapter 运行时裁切
# ============================================================================


class TestMCPAdapterOptionality:
    """AC-TOOL-05: MCPAdapter 未注册时编排器正常运行。"""

    def test_no_mcp_adapter_no_crash(self):
        """不注册 MCPAdapter → 编排器正常运行。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["hello", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        container.register(InputAdapter, MockAdapter())

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        assert len(orch._history) == 0  # _phase_end 清理了
        # 不崩溃即通过

    def test_mcp_adapter_registered_and_used(self):
        """注册 MCPAdapter 后被正确集成。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["hello", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        class MockMCP:
            def __init__(self):
                self.executed = []
                self._shutdown_called = False

            def get_tools(self):
                return [
                    ToolDefinition(
                        name="mcp_echo",
                        description="MCP echo tool",
                        parameters={"type": "object", "properties": {}},
                    )
                ]

            def execute(self, name, args):
                self.executed.append((name, args))
                return ToolResult(success=True, content=f"mcp: {args}")

            def shutdown(self):
                self._shutdown_called = True

        container.register(InputAdapter, MockAdapter())
        container.register(MCPAdapter, MockMCP())

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        # 验证 MCP 工具出现在 available_tools 中
        tool_names = {t.name for t in orch._cached_tools}
        assert "mcp_echo" in tool_names

        orch._phase_loop(ctx)
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        # 验证 shutdown 被调用
        mcp = container.resolve(MCPAdapter)
        assert mcp._shutdown_called is True


# ============================================================================
# AC-TOOL-15: ContextAssembler 内层循环调用次数
# ============================================================================


class TestContextAssemblerCallCountE2E:
    """AC-TOOL-15: 内层循环不调用 ContextAssembler。"""

    def test_inner_loop_no_reassemble(self):
        """内层循环（tool_use 连续多轮）不重新调用 assemble()。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["use tools", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, event):
                self.outputs.append(event.content if hasattr(event, "content") else str(event))

        class SpyAssembler:
            def __init__(self):
                self.call_count = 0

            def assemble(self, ctx):
                self.call_count += 1
                return [
                    {"role": "user", "content": ctx.user_request.text or ""}
                ]

        class MockProvider:
            def get_tools(self):
                return [
                    ToolDefinition(name="t1", description="Tool 1"),
                    ToolDefinition(name="t2", description="Tool 2"),
                ]

            def execute(self, name, args):
                return ToolResult(success=True, content=f"result of {name}")

        container.register(InputAdapter, MockAdapter())
        container.register(SystemToolProvider, MockProvider())
        spy = SpyAssembler()
        container.register(ContextAssembler, spy)

        # LLM: 3 次连续 tool_use → text
        call_count = [0]

        def multi_tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] <= 3:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id=f"c{call_count[0]}",
                            function=ToolCallFunction(
                                name=f"t{call_count[0]}",
                                arguments=json.dumps({}),
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            return Response(text="all done", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=multi_tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # 内层循环 3 次 tool_use → assemble() 额外调用次数为 0
        # 总共只在外层循环开始时调用了 1 次
        assert spy.call_count == 1, (
            f"Expected assemble() called exactly once, got {spy.call_count}"
        )
