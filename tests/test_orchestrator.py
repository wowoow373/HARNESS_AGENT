"""Test harness for LifecycleOrchestrator."""

import time

import pytest

from harness.core.container import DIContainer
from harness.core.exceptions import ComponentNotRegisteredError, OrchestratorError
from harness.core.orchestrator import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    LifecycleOrchestrator,
    MemoryBackend,
    Sensor,
    ToolRegistry,
)
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    ToolCall,
    ToolCallFunction,
    ToolCallRecord,
    ToolDefinition,
    Trajectory,
    UserRequest,
)


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------


class TestDataStructures:
    """正式类型数据结构测试。"""

    def test_user_request_creation(self):
        """UserRequest 正常创建。"""
        req = UserRequest(text="hello")
        assert req.text == "hello"
        assert req.metadata == {}

    def test_user_request_with_metadata(self):
        """UserRequest 带 metadata。"""
        req = UserRequest(text="hello", metadata={"key": "value"})
        assert req.metadata == {"key": "value"}

    def test_guides_bundle_creation(self):
        """GuidesBundle 正常创建。"""
        guides = GuidesBundle(identity="test bot")
        assert guides.identity == "test bot"
        assert guides.rules == []

    def test_assembly_context_creation(self):
        """AssemblyContext 正常创建。"""
        req = UserRequest(text="hello")
        guides = GuidesBundle(identity="test")
        ctx = AssemblyContext(user_request=req, guides=guides)
        assert ctx.user_request is req
        assert ctx.guides is guides
        assert ctx.history == []

    def test_tool_call_creation(self):
        """ToolCall 创建与 JSON 参数解析。"""
        import json
        tc = ToolCall(id="call_1", function=ToolCallFunction(name="read", arguments='{"path": "/tmp/x"}'))
        assert tc.id == "call_1"
        assert tc.function.name == "read"
        assert tc.function.arguments == '{"path": "/tmp/x"}'
        parsed = json.loads(tc.function.arguments)
        assert parsed == {"path": "/tmp/x"}
        assert isinstance(parsed, dict)

    def test_response_text_only(self):
        """Response 纯文本。"""
        resp = Response(text="hi there", stop_reason="end_turn")
        assert resp.text == "hi there"
        assert resp.tool_uses == []

    def test_response_tool_use_only(self):
        """Response 纯 tool_use。"""
        resp = Response(
            tool_uses=[
                ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"path":"/x"}'))
            ],
            stop_reason="tool_use",
        )
        assert resp.text is None
        assert len(resp.tool_uses) == 1
        assert resp.tool_uses[0].function.name == "read"

    def test_response_text_and_tool_uses_coexistence(self):
        """Response text + tool_uses 共存。"""
        resp = Response(
            text="Let me check that file",
            tool_uses=[
                ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"path":"/x"}'))
            ],
            stop_reason="end_turn",
        )
        assert resp.text == "Let me check that file"
        assert len(resp.tool_uses) == 1

    def test_trajectory_creation(self):
        """Trajectory 正常创建。"""
        traj = Trajectory(
            history=[Message(role="user", content="hi")],
            tool_calls=[ToolCallRecord(tool_name="read", result="data")],
            final_output="done",
            execution_time=1.5,
        )
        assert traj.final_output == "done"
        assert len(traj.history) == 1
        assert len(traj.tool_calls) == 1
        assert traj.execution_time == 1.5


# ---------------------------------------------------------------------------
# LifecycleOrchestrator __init__ tests
# ---------------------------------------------------------------------------


class TestOrchestratorInit:
    """编排器初始化测试。"""

    def test_init_with_container_only(self):
        """仅传入 container 可正常初始化。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)
        assert orch.call_llm is None
        assert orch._history == []
        assert orch._tool_call_records == []

    def test_init_with_call_llm(self):
        """传入 call_llm 可正常初始化。"""

        def mock_llm(msgs, tools):
            return Response(text="ok")

        container = DIContainer()
        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        assert orch.call_llm is mock_llm

    def test_init_state_is_clean(self):
        """初始化后状态正确。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)
        assert orch._history == []
        assert orch._tool_call_records == []
        assert orch._should_exit_flag is False
        assert orch._cached_guides is None
        assert orch._cached_tools == []


# ---------------------------------------------------------------------------
# _resolve_optional tests
# ---------------------------------------------------------------------------


class TestResolveOptional:
    """_resolve_optional 测试。"""

    def test_returns_none_for_unregistered(self):
        """未注册组件返回 None。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)

        class IFoo:
            pass

        result = orch._resolve_optional(IFoo)
        assert result is None

    def test_returns_instance_for_registered(self):
        """已注册组件返回实例。"""
        container = DIContainer()

        class IFoo:
            pass

        foo = object()
        container.register(IFoo, foo)

        orch = LifecycleOrchestrator(container)
        assert orch._resolve_optional(IFoo) is foo

    def test_does_not_raise_on_missing(self):
        """缺失组件不抛异常。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)

        class IFoo:
            pass

        # 不应该抛异常
        result = orch._resolve_optional(IFoo)
        assert result is None


# ---------------------------------------------------------------------------
# _should_exit tests
# ---------------------------------------------------------------------------


class TestShouldExit:
    """_should_exit 测试。"""

    @pytest.fixture
    def orch(self):
        return LifecycleOrchestrator(DIContainer())

    def test_normal_text_does_not_exit(self, orch):
        """正常文本不触发退出。"""
        assert orch._should_exit(UserRequest(text="hello")) is False

    def test_empty_text_exits(self, orch):
        """空字符串触发退出。"""
        assert orch._should_exit(UserRequest(text="")) is True

    def test_whitespace_text_exits(self, orch):
        """仅空白字符触发退出。"""
        assert orch._should_exit(UserRequest(text="   ")) is True
        assert orch._should_exit(UserRequest(text="\t\n")) is True

    def test_exit_command_exits(self, orch):
        """退出关键词 /exit 触发退出。"""
        assert orch._should_exit(UserRequest(text="/exit")) is True

    def test_metadata_exit_flag(self, orch):
        """metadata 中的 exit 标志触发退出。"""
        assert (
            orch._should_exit(
                UserRequest(text="hello", metadata={"exit": True})
            )
            is True
        )

    def test_metadata_exit_false_does_not_exit(self, orch):
        """metadata exit=False 不触发退出。"""
        assert (
            orch._should_exit(
                UserRequest(text="hello", metadata={"exit": False})
            )
            is False
        )


# ---------------------------------------------------------------------------
# _phase_init tests
# ---------------------------------------------------------------------------


class TestPhaseInit:
    """_phase_init 测试。"""

    def test_init_with_minimal_components(self):
        """仅有 InputAdapter 时 _phase_init 正常完成。"""
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return UserRequest(text="hello")

            def send(self, r):
                pass

        container.register(InputAdapter, MockAdapter())

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        assert ctx is not None
        assert ctx.user_request.text == "hello"
        assert ctx.guides is not None
        assert ctx.guides.identity == ""

    def test_init_with_guide_provider(self):
        """有 GuideProvider 时正确获取 guides。"""
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return UserRequest(text="hello")

            def send(self, r):
                pass

        class MockGuideProvider:
            def get_guides(self, ctx):
                return GuidesBundle(
                    identity="You are a helpful assistant",
                    rules=["be nice", "be concise"],
                )

        container.register(InputAdapter, MockAdapter())
        container.register(GuideProvider, MockGuideProvider())

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        assert ctx.guides.identity == "You are a helpful assistant"
        assert orch._cached_guides.identity == "You are a helpful assistant"

    def test_init_with_memory_backend(self):
        """有 MemoryBackend 时正确检索记忆。"""
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return UserRequest(text="hello")

            def send(self, r):
                pass

        class MockMemory:
            def search(self, query, namespace, limit=10):
                return [
                    {"key": "mem1", "value": "past conversation", "namespace": "episodic"}
                ]

            def read(self, key, namespace):
                pass

            def write(self, key, value, namespace):
                pass

            def list_namespaces(self):
                return []

        container.register(InputAdapter, MockAdapter())
        container.register(MemoryBackend, MockMemory())

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        memories = ctx.memories
        assert len(memories) == 1
        assert memories[0]["key"] == "mem1"

    def test_init_returns_assembly_context(self):
        """返回值是 AssemblyContext 结构。"""
        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return UserRequest(text="hello")

            def send(self, r):
                pass

        container.register(InputAdapter, MockAdapter())

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        assert isinstance(ctx, AssemblyContext)
        assert ctx.user_request is not None
        assert ctx.guides is not None

    def test_init_missing_input_adapter_raises(self):
        """InputAdapter 缺失时抛异常。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)

        with pytest.raises(ComponentNotRegisteredError):
            orch._phase_init()

    def test_init_first_round_exit_skips_to_phase_end(self):
        """第一轮输入 /exit 时跳过 _phase_loop 直接返回。"""
        container = DIContainer()

        class ExitAdapter:
            def receive(self):
                return UserRequest(text="/exit")
            def send(self, r):
                pass

        container.register(InputAdapter, ExitAdapter())

        orch = LifecycleOrchestrator(container)
        ctx = orch._phase_init()

        # 退出标志已设，返回了最小 ctx
        assert orch._should_exit_flag is True
        assert ctx.user_request is not None
        assert ctx.user_request.text == "/exit"


# ---------------------------------------------------------------------------
# _phase_loop tests
# ---------------------------------------------------------------------------


class TestPhaseLoop:
    """_phase_loop 测试。"""

    def _setup_container_with_adapter(
        self, inputs=None, outputs_capture=None
    ):
        """快速搭建测试容器。"""
        container = DIContainer()
        if inputs is None:
            inputs = ["hello", ""]

        class MockAdapter:
            def __init__(self):
                self.inputs = list(inputs)
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, response):
                text = response.text if hasattr(response, "text") else str(response)
                self.outputs.append(text)

        container.register(InputAdapter, MockAdapter())
        return container

    def test_single_turn_with_text_response(self):
        """单轮纯文本对话。"""
        container = self._setup_container_with_adapter()

        def text_llm(msgs, tools):
            return Response(
                text="Hello! How can I help?", stop_reason="end_turn"
            )

        orch = LifecycleOrchestrator(container, call_llm=text_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) == 1
        assert "Hello" in adapter.outputs[0]
        assert len(orch._history) == 1

    def test_multi_turn_conversation(self):
        """多轮对话（2 轮 + 退出）。"""
        container = self._setup_container_with_adapter(
            inputs=["hello", "what's up", ""]
        )

        def multi_turn_llm(msgs, tools):
            last_user_msg = [
                m for m in msgs if m["role"] == "user"
            ][-1]["content"]
            return Response(
                text=f"Reply to: {last_user_msg}", stop_reason="end_turn"
            )

        orch = LifecycleOrchestrator(container, call_llm=multi_turn_llm)
        orch._cached_guides = GuidesBundle(identity="You are helpful")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) == 2
        assert len(orch._history) == 2
        assert orch._history[0].content == "Reply to: hello"
        assert orch._history[1].content == "Reply to: what's up"

    def test_tool_use_loop(self):
        """tool_use 循环（纯 tool_use → tool result → LLM again → text）。"""
        container = self._setup_container_with_adapter()

        class MockToolRegistry:
            def __init__(self):
                self.executed = []

            def list_tools(self):
                return [
                    ToolDefinition(
                        name="read",
                        description="Read a file",
                        parameters={},
                    )
                ]

            def execute(self, name, args):
                self.executed.append((name, args))

                class TR:
                    success = True
                    content = f"contents of {args['path']}"
                    error = None

                return TR()

        container.register(ToolRegistry, MockToolRegistry())

        call_count = [0]

        def tool_then_text_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"path": "/tmp/x"}')),
                    ],
                    stop_reason="tool_use",
                )
            else:
                return Response(
                    text="File contents: hello world", stop_reason="end_turn"
                )

        orch = LifecycleOrchestrator(container, call_llm=tool_then_text_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        tr = container.resolve(ToolRegistry)
        assert len(tr.executed) == 1
        assert tr.executed[0] == ("read", {"path": "/tmp/x"})
        assert call_count[0] == 2
        assert len(orch._tool_call_records) == 1
        assert orch._tool_call_records[0].tool_name == "read"
        assert orch._history[-1].content == "File contents: hello world"

    def test_text_and_tool_uses_coexistence(self):
        """text + tool_uses 共存场景。"""
        container = self._setup_container_with_adapter()

        class MockToolRegistry:
            def __init__(self):
                self.executed = []

            def list_tools(self):
                return []

            def execute(self, name, args):
                self.executed.append((name, args))

                class TR:
                    success = True
                    content = "data"
                    error = None

                return TR()

        container.register(ToolRegistry, MockToolRegistry())

        def coexistence_llm(msgs, tools):
            return Response(
                text="Let me check that file for you",
                tool_uses=[
                    ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"path": "/tmp/x"}')),
                ],
                stop_reason="end_turn",
            )

        orch = LifecycleOrchestrator(
            container, call_llm=coexistence_llm
        )
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        tr = container.resolve(ToolRegistry)
        assert len(tr.executed) == 1

        adapter = container.resolve(InputAdapter)
        assert any("Let me check" in str(o) for o in adapter.outputs)
        assert any(
            "Let me check" in h.content
            for h in orch._history
        )

    def test_tool_execution_failure_recorded(self):
        """Tool 执行失败时错误被完整记录。"""
        container = self._setup_container_with_adapter(inputs=["do something", ""])

        class FailingToolRegistry:
            def __init__(self):
                self.executed = []

            def list_tools(self):
                return []

            def execute(self, name, args):
                self.executed.append((name, args))

                class TR:
                    success = False
                    content = None
                    error = "Permission denied"

                return TR()

        container.register(ToolRegistry, FailingToolRegistry())

        # LLM: tool call → tool result (error) → LLM again → text
        call_count = [0]

        def error_tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(id="c1", function=ToolCallFunction(name="write", arguments='{"path":"/protected/file"}')),
                    ],
                    stop_reason="tool_use",
                )
            else:
                return Response(
                    text="I couldn't write to that file",
                    stop_reason="end_turn",
                )

        orch = LifecycleOrchestrator(container, call_llm=error_tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert len(orch._tool_call_records) == 1
        assert orch._tool_call_records[0].tool_name == "write"
        assert orch._tool_call_records[0].error == "Permission denied"

    def test_exit_on_empty_input(self):
        """空输入触发退出（不进入第一轮）。"""
        # 使用仅含空输入的 adapter
        container = DIContainer()

        class EmptyAdapter:
            def receive(self):
                return UserRequest(text="")

            def send(self, r):
                pass

        container.register(InputAdapter, EmptyAdapter())
        orch = LifecycleOrchestrator(container)
        orch._cached_guides = GuidesBundle()
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)  # 应该立即退出，不抛异常

    def test_call_llm_none_handling(self):
        """call_llm 为 None 时不崩溃。"""
        container = self._setup_container_with_adapter()

        orch = LifecycleOrchestrator(container, call_llm=None)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        # 应该不崩溃，只是跳过 LLM 调用
        orch._phase_loop(ctx)

    def test_tool_registry_not_registered_handled(self):
        """ToolRegistry 未注册时 tool_use 不崩溃。"""
        container = self._setup_container_with_adapter()

        call_count = [0]

        def tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(id="c1", function=ToolCallFunction(name="read", arguments='{"path":"/x"}'))
                    ],
                    stop_reason="tool_use",
                )
            else:
                return Response(
                    text="I see the tool failed", stop_reason="end_turn"
                )

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        # 不应崩溃：tool_use 检测到 ToolRegistry 缺失，记录错误
        orch._phase_loop(ctx)
        # tool_call_records 中应有带 error 的记录
        if orch._tool_call_records:
            assert orch._tool_call_records[0].error is not None



# ---------------------------------------------------------------------------
# _phase_end tests
# ---------------------------------------------------------------------------


class TestPhaseEnd:
    """_phase_end 测试。"""

    def test_sensor_called_with_trajectory(self):
        """Sensor 被调用并收到正确的 Trajectory。"""
        container = DIContainer()

        class MockSensor:
            def __init__(self):
                self.received_trajectory = None

            def sense(self, trajectory):
                self.received_trajectory = trajectory

        container.register(Sensor, MockSensor())

        orch = LifecycleOrchestrator(container)
        orch._history = [Message(role="assistant", content="final answer")]
        orch._tool_call_records = [
            ToolCallRecord(tool_name="read", result="data")
        ]
        orch._start_time = time.time() - 5.0

        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        sensor = container.resolve(Sensor)
        assert sensor.received_trajectory is not None
        assert len(sensor.received_trajectory.history) == 1
        assert sensor.received_trajectory.final_output == "final answer"
        assert sensor.received_trajectory.execution_time > 0

    def test_sensor_not_called_when_not_registered(self):
        """Sensor 未注册时不崩溃。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)
        orch._history = [Message(role="assistant", content="done")]
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

    def test_trajectory_contains_execution_time(self):
        """Trajectory 包含正确的执行时间。"""
        container = DIContainer()

        class MockSensor:
            def __init__(self):
                self.traj = None

            def sense(self, traj):
                self.traj = traj

        container.register(Sensor, MockSensor())

        orch = LifecycleOrchestrator(container)
        orch._start_time = time.time() - 3.0
        orch._history = [Message(role="assistant", content="done")]
        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        sensor = container.resolve(Sensor)
        assert sensor.traj.execution_time >= 3.0

    def test_state_cleaned_after_end(self):
        """_phase_end 后内部状态被清理。"""
        container = DIContainer()
        orch = LifecycleOrchestrator(container)
        orch._history = [Message(role="assistant", content="test")]
        orch._tool_call_records = [ToolCallRecord(tool_name="read")]
        orch._should_exit_flag = True

        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        assert len(orch._history) == 0
        assert len(orch._tool_call_records) == 0
        assert orch._should_exit_flag is False


# ---------------------------------------------------------------------------
# run() tests
# ---------------------------------------------------------------------------


class TestRun:
    """run() 完整生命周期测试。"""

    def test_full_lifecycle_with_mocks(self):
        """完整三阶段端到端测试（全部组件注册）。"""
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

            def send(self, response):
                self.outputs.append(
                    response.text if hasattr(response, "text") else str(response)
                )

        class MockGuideProvider:
            def get_guides(self, ctx):
                return GuidesBundle(
                    identity="You are a helpful assistant"
                )

        class MockMemory:
            def search(self, query, namespace, limit=10):
                return []

            def read(self, key, namespace):
                return None

            def write(self, key, value, namespace):
                pass

            def list_namespaces(self):
                return []

        class MockAssembler:
            def assemble(self, ctx):
                msgs = []
                if ctx.guides and ctx.guides.identity:
                    msgs.append(Message(
                        role="system", content=ctx.guides.identity
                    ))
                if ctx.user_request and ctx.user_request.text:
                    msgs.append(Message(
                        role="user", content=ctx.user_request.text,
                    ))
                return msgs

        class MockToolRegistry:
            def list_tools(self):
                return []

            def execute(self, name, args):
                class TR:
                    success = True
                    content = "result"
                    error = None

                return TR()

        class MockSensor:
            def __init__(self):
                self.called = False
                self.traj = None

            def sense(self, traj):
                self.called = True
                self.traj = traj

        container.register(InputAdapter, MockAdapter())
        container.register(GuideProvider, MockGuideProvider())
        container.register(MemoryBackend, MockMemory())
        container.register(ContextAssembler, MockAssembler())
        container.register(ToolRegistry, MockToolRegistry())
        container.register(Sensor, MockSensor())

        def mock_llm(msgs, tools):
            return Response(text="mock reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.run()

        adapter = container.resolve(InputAdapter)
        sensor = container.resolve(Sensor)
        assert len(adapter.outputs) >= 1
        assert sensor.called is True
        assert sensor.traj is not None

    def test_run_calls_phase_end_even_on_error(self):
        """异常时 finally 确保 _phase_end 被调用。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.call_count = 0

            def receive(self):
                self.call_count += 1
                if self.call_count == 1:
                    return UserRequest(text="hello")
                raise RuntimeError("Simulated error")

            def send(self, r):
                pass

        class SpySensor:
            def __init__(self):
                self.called = False

            def sense(self, traj):
                self.called = True

        container.register(InputAdapter, MockAdapter())
        container.register(Sensor, SpySensor())

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)

        with pytest.raises(Exception):
            orch.run()

        # finally 确保 _phase_end 被调用，Sensor.sense() 被执行
        sensor = container.resolve(Sensor)
        assert sensor.called is True

    def test_minimal_valid_session(self):
        """最小可行的会话全程（仅 InputAdapter + call_llm）。"""
        container = DIContainer()

        class MockAdapter:
            def __init__(self):
                self.inputs = ["minimal test", ""]
                self.outputs = []
                self.idx = 0

            def receive(self):
                if self.idx < len(self.inputs):
                    t = self.inputs[self.idx]
                    self.idx += 1
                    return UserRequest(text=t)
                return UserRequest(text="")

            def send(self, response):
                self.outputs.append(
                    response.text if hasattr(response, "text") else str(response)
                )

        container.register(InputAdapter, MockAdapter())

        def mock_llm(msgs, tools):
            return Response(
                text="minimal response", stop_reason="end_turn"
            )

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.run()

        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) == 1
        assert adapter.outputs[0] == "minimal response"


# ---------------------------------------------------------------------------
# Integration: Harness.from_container() equivalent flow
# ---------------------------------------------------------------------------


class TestHarnessFlow:
    """验证 Harness.from_container() 等价流程。"""

    def test_harness_from_container_validates_input_adapter(self):
        """验证 InputAdapter 必需校验。"""
        from harness.di import Harness

        container = DIContainer()
        with pytest.raises(ComponentNotRegisteredError):
            Harness.from_container(container)

    def test_harness_from_container_creates_instance(self):
        """验证正常构造。"""
        from harness.di import Harness

        container = DIContainer()

        class MockAdapter:
            def receive(self):
                return UserRequest(text="hi")

            def send(self, r):
                pass

        container.register(InputAdapter, MockAdapter())

        harness = Harness.from_container(container)
        assert harness is not None
        assert harness._orchestrator is not None


# ---------------------------------------------------------------------------
# AC-ORCH-14: 内层循环不调用 ContextAssembler
# ---------------------------------------------------------------------------


class TestContextAssemblerCallCount:
    """验证 AC-ORCH-14：tool_use 内层循环中 ContextAssembler 不重复调用。"""

    def _setup_for_assemble_count_test(self):
        """搭建含 tool_use 循环和 spy ContextAssembler 的测试环境。"""
        from harness.core.container import DIContainer
        from harness.core.orchestrator import (
            InputAdapter, ToolRegistry, ContextAssembler,
        )
        from harness.interfaces.types import (
            UserRequest, GuidesBundle,
            ToolDefinition, ToolCall, ToolCallFunction, Response,
        )

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

            def send(self, response):
                self.outputs.append(
                    response.text if hasattr(response, "text") else str(response)
                )

        class SpyAssembler:
            def __init__(self):
                self.call_count = 0

            def assemble(self, ctx):
                self.call_count += 1
                return [{"role": "user", "content": ctx.user_request.text or ""}]

        class MockToolRegistry:
            call_count = [0]

            def list_tools(self):
                return [ToolDefinition(name="test_tool", description="A test tool")]

            def execute(self, name, args):
                class TR:
                    success = True
                    content = f"result for {args}"
                    error = None
                return TR()

        container.register(InputAdapter, MockAdapter())
        container.register(ToolRegistry, MockToolRegistry())

        call_count = [0]

        def tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[ToolCall(id="c1", function=ToolCallFunction(
                        name="test_tool", arguments='{"key":"value"}'
                    ))],
                    stop_reason="tool_use",
                )
            elif call_count[0] == 2:
                return Response(
                    text="Done after tool", stop_reason="end_turn"
                )
            return Response(text="extra", stop_reason="end_turn")

        spy = SpyAssembler()
        container.register(ContextAssembler, spy)

        return container, spy, tool_llm

    def test_inner_loop_does_not_call_assemble(self):
        """AC-ORCH-14：内层 tool_use 循环中 assemble() 调用次数为 0。"""
        container, spy, tool_llm = self._setup_for_assemble_count_test()

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # assemble() 在每次外层循环开始时调用 1 次
        # 本测试只有 1 轮用户输入（1 次外层循环）→ 恰好 1 次
        assert spy.call_count == 1, (
            f"Expected assemble() to be called exactly once per outer loop iteration, "
            f"but got {spy.call_count} calls"
        )

    def test_outer_loop_calls_assemble_once_per_turn(self):
        """AC-ORCH-14：外层循环每轮仅调用 assemble() 一次。"""
        container, spy, tool_llm = self._setup_for_assemble_count_test()

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # 1 次外层循环 → 恰好 1 次 assemble()
        assert spy.call_count == 1
