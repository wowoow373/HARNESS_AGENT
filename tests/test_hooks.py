"""Tests for Hook system: HookManager unit tests + Orchestrator integration tests."""

import pytest

from harness.core.container import DIContainer
from harness.core.orchestrator import LifecycleOrchestrator
from harness.hooks import (
    EVENT_AFTER_ASSEMBLE,
    EVENT_AFTER_GUIDE_GENERATION,
    EVENT_AFTER_LLM_CALL,
    EVENT_AFTER_SENSOR,
    EVENT_AFTER_TOOL_EXECUTE,
    EVENT_BEFORE_ASSEMBLE,
    EVENT_BEFORE_GUIDE_GENERATION,
    EVENT_BEFORE_LLM_CALL,
    EVENT_BEFORE_TOOL_EXECUTE,
    EVENT_ON_ERROR,
    EVENT_ON_SESSION_END,
    HookManager,
)
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    Sensor,
    SystemToolProvider,
)
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    SystemState,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolResult,
    Trajectory,
    UserRequest,
)


# ============================================================================
# Part 1: HookManager Unit Tests
# ============================================================================


class TestHookManagerInit:
    """HookManager 初始化测试。"""

    def test_init_creates_empty_registry(self):
        """初始化后注册表为空。"""
        hm = HookManager()
        assert hm._hooks == {}


class TestHookManagerRegister:
    """HookManager.register() 测试。"""

    def test_register_single_hook(self):
        """注册一个 hook 到事件。"""
        hm = HookManager()

        def my_hook(ctx):
            ctx.data = "modified"

        hm.register(EVENT_BEFORE_LLM_CALL, my_hook)
        assert EVENT_BEFORE_LLM_CALL in hm._hooks
        assert len(hm._hooks[EVENT_BEFORE_LLM_CALL]) == 1
        assert hm._hooks[EVENT_BEFORE_LLM_CALL][0] is my_hook

    def test_register_multiple_hooks_same_event(self):
        """同一事件注册多个 hook。"""
        hm = HookManager()

        def hook1(ctx):
            pass

        def hook2(ctx):
            pass

        hm.register(EVENT_BEFORE_LLM_CALL, hook1)
        hm.register(EVENT_BEFORE_LLM_CALL, hook2)
        assert len(hm._hooks[EVENT_BEFORE_LLM_CALL]) == 2

    def test_register_different_events(self):
        """不同事件注册 hook。"""
        hm = HookManager()

        def hook1(ctx):
            pass

        def hook2(ctx):
            pass

        hm.register(EVENT_BEFORE_LLM_CALL, hook1)
        hm.register(EVENT_AFTER_LLM_CALL, hook2)
        assert len(hm._hooks) == 2

    def test_register_invalid_event_raises(self):
        """空字符串 event 抛 ValueError。"""
        hm = HookManager()

        def hook(ctx):
            pass

        with pytest.raises(ValueError, match="event must be a non-empty string"):
            hm.register("", hook)

    def test_register_invalid_hook_raises(self):
        """非 callable hook 抛 ValueError。"""
        hm = HookManager()

        with pytest.raises(ValueError, match="hook must be callable"):
            hm.register(EVENT_BEFORE_LLM_CALL, "not_callable")

    def test_register_same_hook_twice(self):
        """同一 hook 多次注册到同一事件。"""
        hm = HookManager()

        def hook(ctx):
            pass

        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        assert len(hm._hooks[EVENT_BEFORE_LLM_CALL]) == 2


class TestHookManagerTrigger:
    """HookManager.trigger() 测试。"""

    def test_trigger_returns_original_when_no_hooks(self):
        """无注册 hook 时返回原始 data。"""
        hm = HookManager()
        state = SystemState()
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, "original", state)
        assert result == "original"

    def test_trigger_single_hook_modifies_data(self):
        """单个 hook 修改 data。"""
        hm = HookManager()

        def my_hook(ctx):
            ctx.data = "modified"

        hm.register(EVENT_BEFORE_LLM_CALL, my_hook)
        state = SystemState()
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, "original", state)
        assert result == "modified"

    def test_trigger_multiple_hooks_same_event(self):
        """多个 hook 按注册顺序执行。"""
        hm = HookManager()
        order = []

        def hook1(ctx):
            order.append(1)
            ctx.data += "_1"

        def hook2(ctx):
            order.append(2)
            ctx.data += "_2"

        hm.register(EVENT_BEFORE_LLM_CALL, hook1)
        hm.register(EVENT_BEFORE_LLM_CALL, hook2)
        state = SystemState()
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, "start", state)
        assert order == [1, 2]
        assert result == "start_1_2"

    def test_hook_modification_visible_to_next_hook(self):
        """前序 hook 的修改对后续 hook 可见。"""
        hm = HookManager()

        def hook1(ctx):
            ctx.data["key1"] = "value1"

        def hook2(ctx):
            ctx.data["key2"] = ctx.data.get("key1", "") + "_extended"

        hm.register(EVENT_BEFORE_LLM_CALL, hook1)
        hm.register(EVENT_BEFORE_LLM_CALL, hook2)
        state = SystemState()
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, {}, state)
        assert result == {"key1": "value1", "key2": "value1_extended"}

    def test_hook_exception_does_not_block_others(self):
        """单个 hook 异常不阻塞其他 hook。"""
        hm = HookManager()
        order = []

        def bad_hook(ctx):
            order.append("bad")
            raise RuntimeError("Hook error")

        def good_hook(ctx):
            order.append("good")
            ctx.data = "recovered"

        hm.register(EVENT_BEFORE_LLM_CALL, bad_hook)
        hm.register(EVENT_BEFORE_LLM_CALL, good_hook)
        state = SystemState()
        result = hm.trigger(EVENT_BEFORE_LLM_CALL, "original", state)
        assert order == ["bad", "good"]
        assert result == "recovered"

    def test_trigger_different_events_independent(self):
        """不同事件的 hook 互不影响。"""
        hm = HookManager()
        calls = []

        def hook_before(ctx):
            calls.append("before")

        def hook_after(ctx):
            calls.append("after")

        hm.register(EVENT_BEFORE_LLM_CALL, hook_before)
        hm.register(EVENT_AFTER_LLM_CALL, hook_after)
        state = SystemState()

        hm.trigger(EVENT_BEFORE_LLM_CALL, "data", state)
        assert calls == ["before"]

        hm.trigger(EVENT_AFTER_LLM_CALL, "data", state)
        assert calls == ["before", "after"]

    def test_system_state_passed_to_hook(self):
        """system_state 正确传递给 hook。"""
        hm = HookManager()
        received_state = None

        def hook(ctx):
            nonlocal received_state
            received_state = ctx.system_state

        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        state = SystemState(phase="loop", session_id="test_123")
        hm.trigger(EVENT_BEFORE_LLM_CALL, "data", state)
        assert received_state is state
        assert received_state.phase == "loop"
        assert received_state.session_id == "test_123"

    def test_trigger_event_name_in_context(self):
        """context.event 正确设置为事件名。"""
        hm = HookManager()
        received_event = None

        def hook(ctx):
            nonlocal received_event
            received_event = ctx.event

        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        state = SystemState()
        hm.trigger(EVENT_BEFORE_LLM_CALL, "data", state)
        assert received_event == EVENT_BEFORE_LLM_CALL


class TestHookManagerUnregister:
    """HookManager.unregister() 测试。"""

    def test_unregister_removes_hook(self):
        """注销后 hook 不再被触发。"""
        hm = HookManager()
        called = []

        def hook(ctx):
            called.append(True)

        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        hm.unregister(EVENT_BEFORE_LLM_CALL, hook)
        state = SystemState()
        hm.trigger(EVENT_BEFORE_LLM_CALL, "data", state)
        assert len(called) == 0

    def test_unregister_nonexistent_event_no_error(self):
        """注销不存在的事件不抛异常。"""
        hm = HookManager()

        def hook(ctx):
            pass

        hm.unregister("nonexistent_event", hook)

    def test_unregister_nonexistent_hook_no_error(self):
        """注销不存在的 hook 不抛异常。"""
        hm = HookManager()

        def hook1(ctx):
            pass

        def hook2(ctx):
            pass

        hm.register(EVENT_BEFORE_LLM_CALL, hook1)
        hm.unregister(EVENT_BEFORE_LLM_CALL, hook2)
        assert len(hm._hooks[EVENT_BEFORE_LLM_CALL]) == 1

    def test_unregister_only_first_match(self):
        """同一 hook 多次注册时只移除第一次匹配。"""
        hm = HookManager()
        call_count = [0]

        def hook(ctx):
            call_count[0] += 1

        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        hm.register(EVENT_BEFORE_LLM_CALL, hook)
        hm.unregister(EVENT_BEFORE_LLM_CALL, hook)
        state = SystemState()
        hm.trigger(EVENT_BEFORE_LLM_CALL, "data", state)
        assert call_count[0] == 1


# ============================================================================
# Part 2: Orchestrator Integration Tests
# ============================================================================


class TestOrchestratorHookIntegration:
    """验证 Orchestrator 中 11 个 Hook 点被正确触发。"""

    def _make_container(self, inputs=None):
        """快速搭建含 Mock InputAdapter 的测试容器。"""
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

    # --- Phase 1 hooks ---

    def test_before_and_after_guide_generation(self):
        """Phase 1 中 before/after_guide_generation 被触发。"""
        container = self._make_container()

        class MockGuideProvider:
            def get_guides(self, ctx):
                return GuidesBundle(identity="test_identity")

        container.register(GuideProvider, MockGuideProvider())

        events_triggered = []

        def before_hook(ctx):
            events_triggered.append(ctx.event)
            assert isinstance(ctx.data, AssemblyContext)

        def after_hook(ctx):
            events_triggered.append(ctx.event)
            assert isinstance(ctx.data, GuidesBundle)
            assert ctx.data.identity == "test_identity"

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_BEFORE_GUIDE_GENERATION, before_hook)
        orch.register_hook(EVENT_AFTER_GUIDE_GENERATION, after_hook)
        orch._phase_init()

        assert EVENT_BEFORE_GUIDE_GENERATION in events_triggered
        assert EVENT_AFTER_GUIDE_GENERATION in events_triggered

    def test_guide_generation_hooks_not_triggered_on_early_exit(self):
        """Phase 1 提前退出时 guide_generation hooks 不触发。"""
        container = DIContainer()

        class ExitAdapter:
            def receive(self):
                return UserRequest(text="/exit")

            def send(self, r):
                pass

        container.register(InputAdapter, ExitAdapter())

        events_triggered = []

        def before_hook(ctx):
            events_triggered.append(ctx.event)

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_BEFORE_GUIDE_GENERATION, before_hook)
        orch._phase_init()

        assert EVENT_BEFORE_GUIDE_GENERATION not in events_triggered

    # --- Phase 2 outer loop hooks ---

    def test_before_and_after_assemble(self):
        """Phase 2 外层循环中 before/after_assemble 被触发。"""
        container = self._make_container()

        class MockAssembler:
            def assemble(self, ctx):
                return [Message(role="user", content="test")]

        container.register(ContextAssembler, MockAssembler())

        events_triggered = []
        received_before_data = None
        received_after_data = None

        def before_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_before_data
            received_before_data = ctx.data

        def after_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_after_data
            received_after_data = ctx.data

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_ASSEMBLE, before_hook)
        orch.register_hook(EVENT_AFTER_ASSEMBLE, after_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert EVENT_BEFORE_ASSEMBLE in events_triggered
        assert EVENT_AFTER_ASSEMBLE in events_triggered
        assert isinstance(received_before_data, AssemblyContext)
        assert isinstance(received_after_data, list)
        assert received_after_data[0].role == "user"

    def test_before_and_after_assemble_without_assembler(self):
        """无 ContextAssembler 时 before/after_assemble 仍然触发。"""
        container = self._make_container()

        events_triggered = []

        def before_hook(ctx):
            events_triggered.append(ctx.event)

        def after_hook(ctx):
            events_triggered.append(ctx.event)

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_ASSEMBLE, before_hook)
        orch.register_hook(EVENT_AFTER_ASSEMBLE, after_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert EVENT_BEFORE_ASSEMBLE in events_triggered
        assert EVENT_AFTER_ASSEMBLE in events_triggered

    # --- Phase 2 inner loop hooks ---

    def test_before_and_after_llm_call(self):
        """Phase 2 内层循环中 before/after_llm_call 被触发。"""
        container = self._make_container()

        events_triggered = []
        received_messages = None
        received_response = None

        def before_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_messages
            received_messages = ctx.data

        def after_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_response
            received_response = ctx.data

        def mock_llm(msgs, tools):
            return Response(text="llm_reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_LLM_CALL, before_hook)
        orch.register_hook(EVENT_AFTER_LLM_CALL, after_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert EVENT_BEFORE_LLM_CALL in events_triggered
        assert EVENT_AFTER_LLM_CALL in events_triggered
        assert isinstance(received_messages, list)
        assert isinstance(received_response, Response)
        assert received_response.text == "llm_reply"

    def test_before_and_after_tool_execute(self):
        """tool_use 场景中 before/after_tool_execute 被触发。"""
        container = self._make_container()

        class MockToolProvider:
            def get_tools(self):
                return [
                    ToolDefinition(
                        name="echo",
                        description="Echo",
                        parameters={},
                    )
                ]

            def execute(self, name, args):
                class TR:
                    success = True
                    content = f"echoed: {args.get('msg', '')}"
                    error = None

                return TR()

        container.register(SystemToolProvider, MockToolProvider())

        events_triggered = []
        received_tool_call = None
        received_tool_result = None

        def before_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_tool_call
            received_tool_call = ctx.data

        def after_hook(ctx):
            events_triggered.append(ctx.event)
            nonlocal received_tool_result
            received_tool_result = ctx.data

        call_count = [0]

        def tool_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="tc1",
                            function=ToolCallFunction(
                                name="echo",
                                arguments='{"msg":"hello"}',
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            return Response(text="done", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=tool_llm)
        orch.register_hook(EVENT_BEFORE_TOOL_EXECUTE, before_hook)
        orch.register_hook(EVENT_AFTER_TOOL_EXECUTE, after_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        assert EVENT_BEFORE_TOOL_EXECUTE in events_triggered
        assert EVENT_AFTER_TOOL_EXECUTE in events_triggered
        assert isinstance(received_tool_call, ToolCall)
        assert received_tool_call.function.name == "echo"
        assert isinstance(received_tool_result, ToolResult)
        assert received_tool_result.success is True

    # --- Phase 3 hooks ---

    def test_on_session_end(self):
        """Phase 3 中 on_session_end 被触发，接收 Trajectory。"""
        container = self._make_container()

        received_trajectory = None

        def hook(ctx):
            nonlocal received_trajectory
            received_trajectory = ctx.data

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_ON_SESSION_END, hook)
        orch._history = [Message(role="assistant", content="final")]
        orch._start_time = __import__("time").time() - 1.0

        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        assert isinstance(received_trajectory, Trajectory)
        assert received_trajectory.final_output == "final"

    def test_after_sensor(self):
        """Sensor.sense() 之后 after_sensor 被触发。"""
        container = self._make_container()

        class MockSensor:
            def __init__(self):
                self.sensed = False

            def sense(self, trajectory):
                self.sensed = True

        container.register(Sensor, MockSensor())

        received_trajectory = None

        def hook(ctx):
            nonlocal received_trajectory
            received_trajectory = ctx.data

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_AFTER_SENSOR, hook)
        orch._history = [Message(role="assistant", content="final")]
        orch._start_time = __import__("time").time() - 1.0

        trajectory = orch._build_trajectory()
        orch._phase_end(trajectory)

        assert isinstance(received_trajectory, Trajectory)
        sensor = container.resolve(Sensor)
        assert sensor.sensed is True

    # --- on_error hook ---

    def test_on_error_hook_triggered(self):
        """异常时 on_error hook 被触发，异常对象正确传递。"""
        container = self._make_container(inputs=["hello"])

        class BadAdapter:
            def receive(self):
                raise RuntimeError("Adapter failure")

            def send(self, r):
                pass

        container = DIContainer()
        container.register(InputAdapter, BadAdapter())

        received_exception = None

        def hook(ctx):
            nonlocal received_exception
            received_exception = ctx.data

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_ON_ERROR, hook)

        with pytest.raises(Exception):
            orch.run()

        assert isinstance(received_exception, RuntimeError)
        assert str(received_exception) == "Adapter failure"

    # --- Hook data modification affects flow ---

    def test_hook_modifies_messages_before_llm_call(self):
        """before_llm_call hook 修改 messages，mock LLM 收到修改后的内容。"""
        container = self._make_container()

        received_messages = None

        def before_llm_hook(ctx):
            # 在 messages 末尾添加一个 system 消息
            ctx.data.append(Message(role="system", content="injected"))

        def mock_llm(msgs, tools):
            nonlocal received_messages
            received_messages = msgs
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_LLM_CALL, before_llm_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # msgs 是 dict 列表（因为 messages_to_dicts 已转换）
        assert any(m.get("role") == "system" and m.get("content") == "injected" for m in received_messages)

    def test_hook_modifies_response_after_llm_call(self):
        """after_llm_call hook 修改 Response，后续流程使用修改后的 Response。"""
        container = self._make_container()

        def after_llm_hook(ctx):
            ctx.data.text = "modified_reply"

        def mock_llm(msgs, tools):
            return Response(text="original", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_AFTER_LLM_CALL, after_llm_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        adapter = container.resolve(InputAdapter)
        assert adapter.outputs[0] == "modified_reply"

    # --- system_state.phase tracking ---

    def test_system_state_phase_updated(self):
        """system_state.phase 在 init → loop → end 间正确切换。"""
        container = self._make_container()

        class MockGuideProvider:
            def get_guides(self, ctx):
                return GuidesBundle(identity="test")

        container.register(GuideProvider, MockGuideProvider())

        phases = []

        def init_hook(ctx):
            phases.append(ctx.system_state.phase)

        def loop_hook(ctx):
            if ctx.system_state.phase not in phases:
                phases.append(ctx.system_state.phase)

        def end_hook(ctx):
            phases.append(ctx.system_state.phase)

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_GUIDE_GENERATION, init_hook)
        orch.register_hook(EVENT_BEFORE_ASSEMBLE, loop_hook)
        orch.register_hook(EVENT_ON_SESSION_END, end_hook)

        orch.run()

        assert "init" in phases
        assert "loop" in phases
        assert "end" in phases

    # --- register_hook via orchestrator ---

    def test_register_hook_via_orchestrator(self):
        """orchestrator.register_hook() 方法工作正常。"""
        container = self._make_container()
        called = []

        def hook(ctx):
            called.append(True)

        orch = LifecycleOrchestrator(container)
        orch.register_hook(EVENT_BEFORE_LLM_CALL, hook)

        # 通过内部 HookManager 验证注册成功
        assert EVENT_BEFORE_LLM_CALL in orch._hook_manager._hooks
        assert len(orch._hook_manager._hooks[EVENT_BEFORE_LLM_CALL]) == 1


# ============================================================================
# Part 3: End-to-End Tests
# ============================================================================


class TestHookEndToEnd:
    """Hook 完整生命周期的端到端测试。"""

    def _make_container(self, inputs=None):
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

    def test_e2e_hook_modifies_messages_before_llm_call(self):
        """before_llm_call hook 修改 messages，LLM 收到修改后的内容。"""
        container = self._make_container()

        captured_messages = None

        def inject_hook(ctx):
            """在 messages 中注入一个额外的 system 消息。"""
            ctx.data.append(Message(role="system", content="hook_injected"))

        def capturing_llm(msgs, tools):
            nonlocal captured_messages
            captured_messages = msgs
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=capturing_llm)
        orch.register_hook(EVENT_BEFORE_LLM_CALL, inject_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        orch.run()

        assert captured_messages is not None
        injected = [m for m in captured_messages if m.get("role") == "system" and m.get("content") == "hook_injected"]
        assert len(injected) == 1

    def test_e2e_hook_observes_full_session(self):
        """多个 hook 注册到不同事件，完整会话中每个都被触发一次。"""
        container = self._make_container()

        class MockGuideProvider:
            def get_guides(self, ctx):
                return GuidesBundle(identity="test")

        container.register(GuideProvider, MockGuideProvider())

        event_counts = {}

        def make_hook(event_name):
            def hook(ctx):
                event_counts[event_name] = event_counts.get(event_name, 0) + 1

            return hook

        events_to_test = [
            EVENT_BEFORE_GUIDE_GENERATION,
            EVENT_AFTER_GUIDE_GENERATION,
            EVENT_BEFORE_ASSEMBLE,
            EVENT_AFTER_ASSEMBLE,
            EVENT_BEFORE_LLM_CALL,
            EVENT_AFTER_LLM_CALL,
            EVENT_ON_SESSION_END,
            EVENT_AFTER_SENSOR,
        ]

        def mock_llm(msgs, tools):
            return Response(text="reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        for event in events_to_test:
            orch.register_hook(event, make_hook(event))

        orch.run()

        for event in events_to_test:
            assert event_counts.get(event, 0) >= 1, f"Event {event} was not triggered"

    def test_e2e_no_hooks_session_still_works(self):
        """无 hook 注册时，完整会话正常执行（无回归）。"""
        container = self._make_container(inputs=["hello", ""])

        def mock_llm(msgs, tools):
            return Response(text="no_hook_reply", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.run()

        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) == 1
        assert adapter.outputs[0] == "no_hook_reply"

    def test_e2e_hook_exception_does_not_crash_session(self):
        """hook 异常时会话继续正常执行。"""
        container = self._make_container(inputs=["hello", ""])

        def bad_hook(ctx):
            raise RuntimeError("Hook explosion")

        def mock_llm(msgs, tools):
            return Response(text="recovered", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_BEFORE_LLM_CALL, bad_hook)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        # 不应抛异常
        orch.run()

        adapter = container.resolve(InputAdapter)
        assert len(adapter.outputs) == 1
        assert adapter.outputs[0] == "recovered"

    def test_e2e_multiple_hooks_modify_chain(self):
        """多个 hook 依次修改数据，最终效果叠加。"""
        container = self._make_container(inputs=["hello", ""])

        def add_prefix(ctx):
            ctx.data.text = "[PREFIX] " + (ctx.data.text or "")

        def add_suffix(ctx):
            ctx.data.text = (ctx.data.text or "") + " [SUFFIX]"

        def mock_llm(msgs, tools):
            return Response(text="base", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=mock_llm)
        orch.register_hook(EVENT_AFTER_LLM_CALL, add_prefix)
        orch.register_hook(EVENT_AFTER_LLM_CALL, add_suffix)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        orch.run()

        adapter = container.resolve(InputAdapter)
        assert adapter.outputs[0] == "[PREFIX] base [SUFFIX]"

    def test_e2e_tool_result_hook_modifies_result(self):
        """after_tool_execute hook 修改 ToolResult，影响后续流程。"""
        container = self._make_container()

        class MockToolProvider:
            def get_tools(self):
                return [
                    ToolDefinition(
                        name="calc",
                        description="Calc",
                        parameters={},
                    )
                ]

            def execute(self, name, args):
                class TR:
                    success = True
                    content = "42"
                    error = None

                return TR()

        container.register(SystemToolProvider, MockToolProvider())

        def modify_result(ctx):
            ctx.data.content = "modified: " + str(ctx.data.content)

        call_count = [0]

        def tool_then_text_llm(msgs, tools):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="tc1",
                            function=ToolCallFunction(
                                name="calc", arguments='{}'
                            ),
                        )
                    ],
                    stop_reason="tool_use",
                )
            return Response(text="done", stop_reason="end_turn")

        orch = LifecycleOrchestrator(container, call_llm=tool_then_text_llm)
        orch.register_hook(EVENT_AFTER_TOOL_EXECUTE, modify_result)
        orch._cached_guides = GuidesBundle(identity="test")
        orch._cached_tools = []

        ctx = orch._phase_init()
        orch._phase_loop(ctx)

        # tool result message 中应包含被修改后的内容
        tool_msgs = [m for m in orch._history if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "modified: 42" in tool_msgs[0].content
