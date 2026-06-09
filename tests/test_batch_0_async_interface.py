"""Tests for Batch 0: Async 接口层 + AsyncLifecycleOrchestrator.

覆盖：
- AsyncInputAdapter Protocol 满足性
- InternalMessage / __EXIT_SENTINEL__ / AgentOutput 行为
- AsyncLifecycleOrchestrator 三阶段
- KernelBridgeAdapter 消息转换/过滤/降级

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import time

import pytest

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.container import DIContainer
from harness.core.exceptions import ComponentNotRegisteredError
from harness.core.orchestrator import (
    ContextAssembler,
    GuideProvider,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.interfaces.async_input_adapter import AsyncInputAdapter
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    StopEvent,
    TextEvent,
    ThinkingEvent,
    ToolCall,
    ToolCallEvent,
    ToolCallFunction,
    ToolCallRecord,
    ToolDefinition,
    ToolResultEvent,
    Trajectory,
    UserRequest,
)
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.runtime.types import AgentOutput, InternalMessage, __EXIT_SENTINEL__


# ============================================================================
# Helpers
# ============================================================================


def async_test(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用。"""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# ============================================================================
# Mock / Stub 对象
# ============================================================================


class MockAgentRuntime:
    """AgentRuntime 的最小 stub（Batch 1 才定义真类）。"""

    def __init__(self, should_exit: bool = False):
        self.should_exit = should_exit


class MockConsole:
    """SystemConsole 的最小 stub（Batch 1 才定义 Protocol）。"""

    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


class MockKernel:
    """Kernel 的最小 stub（Batch 1 才定义真类）。"""

    def __init__(self):
        self.input_queues: dict[str, asyncio.Queue] = {}
        self._console = MockConsole()
        # Batch 3: 添加 message_bus mock，供 KBA.send() 使用
        self.message_bus = _MockMessageBus(self.input_queues, self._console)

    def add_queue(self, pid: str):
        self.input_queues[pid] = asyncio.Queue()


class _MockMessageBus:
    """MessageBus 的最小 mock，供 KBA.send() 测试使用。"""

    def __init__(self, input_queues, console):
        self._input_queues = input_queues
        self._console = console
        self._subscriptions: dict[str, set[str]] = {}

    async def publish(self, from_pid, event, on_no_subscriber=None):
        """降级路由：无订阅者时走降级路径（复现 Batch 0-2 行为）。"""
        from harness.interfaces.types import TextEvent
        from harness.runtime.types import AgentOutput

        subscribers = self._subscriptions.get(from_pid, set())
        active = {p for p in subscribers if p in self._input_queues}
        if not active and isinstance(event, TextEvent):
            if on_no_subscriber is not None:
                await on_no_subscriber(
                    AgentOutput(pid=from_pid, content=event.content)
                )

    def direct(self, target_pid, message):
        """定向投递 mock —— 直接入队。"""
        if target_pid in self._input_queues:
            self._input_queues[target_pid].put_nowait(message)


class MockAsyncAdapter:
    """AsyncInputAdapter 的最小 mock 实现。"""

    def __init__(self, inputs=None, capture_outputs=True):
        if inputs is None:
            inputs = ["hello", ""]
        self.inputs = list(inputs)
        self.outputs = []
        self.idx = 0
        self._capture = capture_outputs

    async def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]
            self.idx += 1
            return UserRequest(text=t)
        return UserRequest(text="")

    async def send(self, event, target=None):
        if self._capture:
            self.outputs.append((event, target))


# ============================================================================
# 1. AsyncInputAdapter Protocol 满足性
# ============================================================================


class TestAsyncInputAdapterProtocol:
    """AsyncInputAdapter Protocol 运行时检查。"""

    def test_protocol_is_runtime_checkable(self):
        """Protocol 标记为 @runtime_checkable。"""
        assert hasattr(AsyncInputAdapter, '_is_protocol')

    def test_valid_implementation_satisfies_protocol(self):
        """有 receive/send 方法的类满足 Protocol。"""
        class Valid:
            async def receive(self):
                return UserRequest(text="ok")

            async def send(self, event, target=None):
                pass

        assert isinstance(Valid(), AsyncInputAdapter)

    def test_missing_receive_fails_protocol(self):
        """缺少 receive 不满足 Protocol。"""
        class NoReceive:
            async def send(self, event, target=None):
                pass

        assert not isinstance(NoReceive(), AsyncInputAdapter)

    def test_missing_send_fails_protocol(self):
        """缺少 send 不满足 Protocol。"""
        class NoSend:
            async def receive(self):
                return UserRequest(text="ok")

        assert not isinstance(NoSend(), AsyncInputAdapter)

    def test_kba_satisfies_protocol(self):
        """KernelBridgeAdapter 满足 Protocol。"""
        kernel = MockKernel()
        kernel.add_queue("test")
        runtime = MockAgentRuntime()
        kba = KernelBridgeAdapter(pid="test", kernel=kernel, runtime=runtime)
        assert isinstance(kba, AsyncInputAdapter)


# ============================================================================
# 2. InternalMessage / __EXIT_SENTINEL__ / AgentOutput
# ============================================================================


class TestInternalMessageAndSentinel:
    """共享类型行为测试。"""

    def test_internal_message_default_construction(self):
        """InternalMessage 默认构造。"""
        msg = InternalMessage()
        assert msg.from_pid == ""
        assert msg.content == ""
        assert msg.metadata == {}
        assert msg.created_at > 0

    def test_internal_message_custom_construction(self):
        """InternalMessage 自定义字段。"""
        msg = InternalMessage(
            from_pid="collector",
            content="采集到 28 个文件",
            metadata={"stop": False},
        )
        assert msg.from_pid == "collector"
        assert msg.content == "采集到 28 个文件"
        assert msg.metadata == {"stop": False}

    def test_internal_message_stop_metadata(self):
        """StopEvent 转换的 InternalMessage 含 stop metadata。"""
        msg = InternalMessage(
            from_pid="agent1",
            content="",
            metadata={"stop": True},
        )
        assert msg.metadata["stop"] is True
        assert msg.content == ""

    def test_exit_sentinel_is_singleton_like(self):
        """__EXIT_SENTINEL__ 使用 is 身份比较。"""
        sentinel = __EXIT_SENTINEL__
        assert sentinel is __EXIT_SENTINEL__

    def test_other_object_is_not_sentinel(self):
        """另一个 object() 不是 sentinel。"""
        other = object()
        assert other is not __EXIT_SENTINEL__

    def test_agent_output_default_construction(self):
        """AgentOutput 默认构造。"""
        ao = AgentOutput()
        assert ao.pid == ""
        assert ao.content == ""

    def test_agent_output_custom_construction(self):
        """AgentOutput 自定义字段。"""
        ao = AgentOutput(pid="root", content="Hello world")
        assert ao.pid == "root"
        assert ao.content == "Hello world"


# ============================================================================
# 3. AsyncLifecycleOrchestrator __init__
# ============================================================================


class TestAsyncLifecycleOrchestratorInit:
    """异步编排器初始化测试。"""

    def test_init_with_minimal_args(self):
        """仅传入 container + adapter 可正常初始化。"""
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
        assert orch.call_llm is None
        assert orch._history == []
        assert orch._tool_call_records == []
        assert orch._adapter is adapter

    def test_init_with_call_llm(self):
        """传入 async call_llm 可正常初始化。"""

        async def mock_llm(msgs, tools):
            return Response(text="ok")

        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(
            container, adapter=adapter, call_llm=mock_llm
        )
        assert orch.call_llm is mock_llm

    def test_init_state_is_clean(self):
        """初始化后状态正确。"""
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
        assert orch._history == []
        assert orch._tool_call_records == []
        assert orch._should_exit_flag is False
        assert orch._cached_guides is None
        assert orch._cached_tools == []
        assert orch._cached_tool_router is None

    def test_init_without_adapter_raises(self):
        """缺少 adapter 参数时 TypeError。"""
        container = DIContainer()
        with pytest.raises(TypeError):
            # adapter 是 keyword-only 参数，缺少时抛 TypeError
            AsyncLifecycleOrchestrator(container)  # noqa


# ============================================================================
# 4. _resolve_optional
# ============================================================================


class TestAsyncOrchResolveOptional:
    """_resolve_optional 测试。"""

    @pytest.fixture
    def orch(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        return AsyncLifecycleOrchestrator(container, adapter=adapter)

    def test_returns_none_for_unregistered(self, orch):
        """未注册组件返回 None。"""
        class IFoo:
            pass
        result = orch._resolve_optional(IFoo)
        assert result is None

    def test_returns_instance_for_registered(self, orch):
        """已注册组件返回实例。"""
        class IFoo:
            pass
        foo = object()
        orch.container.register(IFoo, foo)
        assert orch._resolve_optional(IFoo) is foo

    def test_does_not_raise_on_missing(self, orch):
        """缺失组件不抛异常。"""
        class IFoo:
            pass
        result = orch._resolve_optional(IFoo)
        assert result is None


# ============================================================================
# 5. _should_exit
# ============================================================================


class TestAsyncOrchShouldExit:
    """_should_exit 测试。"""

    @pytest.fixture
    def orch(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        return AsyncLifecycleOrchestrator(container, adapter=adapter)

    def test_normal_text_does_not_exit(self, orch):
        assert orch._should_exit(UserRequest(text="hello")) is False

    def test_empty_text_exits(self, orch):
        assert orch._should_exit(UserRequest(text="")) is True

    def test_whitespace_text_exits(self, orch):
        assert orch._should_exit(UserRequest(text="   ")) is True
        assert orch._should_exit(UserRequest(text="\t\n")) is True

    def test_exit_command_exits(self, orch):
        assert orch._should_exit(UserRequest(text="/exit")) is True

    def test_metadata_exit_flag(self, orch):
        assert (
            orch._should_exit(
                UserRequest(text="hello", metadata={"exit": True})
            )
            is True
        )

    def test_metadata_exit_false_does_not_exit(self, orch):
        assert (
            orch._should_exit(
                UserRequest(text="hello", metadata={"exit": False})
            )
            is False
        )


# ============================================================================
# 6. _phase_init
# ============================================================================


class TestAsyncOrchPhaseInit:
    """_phase_init 异步测试。"""

    def test_init_with_minimal_components(self):
        """仅有 adapter 时 _phase_init 正常完成。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()

            assert ctx is not None
            assert ctx.user_request.text == "hello"
            assert ctx.guides is not None
            assert ctx.guides.identity == ""
        asyncio.run(_test())

    def test_init_with_guide_provider(self):
        """有 GuideProvider 时正确获取 guides。"""
        async def _test():
            container = DIContainer()

            class MockGuide:
                def get_guides(self, ctx):
                    return GuidesBundle(
                        identity="You are a helpful assistant",
                        rules=["be nice"],
                    )

            container.register(GuideProvider, MockGuide())
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()

            assert ctx.guides.identity == "You are a helpful assistant"
            assert orch._cached_guides.identity == "You are a helpful assistant"
        asyncio.run(_test())

    def test_init_with_memory_backend(self):
        """有 MemoryBackend 时正确检索记忆。"""
        async def _test():
            container = DIContainer()

            class MockMemory:
                def search(self, query, namespace, limit=10):
                    return [{"key": "mem1", "value": "past", "namespace": namespace}]

                def read(self, key, namespace):
                    pass

                def write(self, key, value, namespace):
                    pass

                def list_namespaces(self):
                    return []

            container.register(MemoryBackend, MockMemory())
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()

            assert len(ctx.memories) == 1
            assert ctx.memories[0]["key"] == "mem1"
        asyncio.run(_test())

    def test_init_returns_assembly_context(self):
        """返回值是 AssemblyContext 结构。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()

            assert isinstance(ctx, AssemblyContext)
            assert ctx.user_request is not None
            assert ctx.guides is not None
        asyncio.run(_test())

    def test_init_missing_input_adapter_does_not_raise(self):
        """adapter 不在 DI 容器中 — 不依赖 DI，直接使用构造时传入的 adapter。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
            ctx = await orch._phase_init()
            assert ctx.user_request.text == "hello"
        asyncio.run(_test())

    def test_init_first_round_exit(self):
        """第一轮输入 /exit 时设置 should_exit_flag 并返回最小 ctx。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter(inputs=["/exit", ""])
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()

            assert orch._should_exit_flag is True
            assert ctx.user_request is not None
            assert ctx.user_request.text == "/exit"
        asyncio.run(_test())

    def test_init_exit_from_metadata(self):
        """metadata exit=True 触发退出标志。"""
        async def _test():
            container = DIContainer()

            class ExitMetaAdapter:
                async def receive(self):
                    return UserRequest(text="", metadata={"exit": True})

                async def send(self, event, target=None):
                    pass

            orch = AsyncLifecycleOrchestrator(container, adapter=ExitMetaAdapter())

            ctx = await orch._phase_init()

            assert orch._should_exit_flag is True
        asyncio.run(_test())


# ============================================================================
# 7. _phase_loop — 单轮执行
# ============================================================================


class TestAsyncOrchPhaseLoop:
    """_phase_loop 单轮执行测试。

    关键验证：_phase_loop 仅执行一轮，不调用 adapter.receive()。
    """

    def test_single_turn_with_text_response(self):
        """单轮纯文本对话。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])

            async def text_llm(msgs, tools):
                return Response(text="Hello! How can I help?", stop_reason="end_turn")

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=text_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            # TextEvent + StopEvent = 2 events
            assert len(adapter.outputs) == 2
            ev0, _ = adapter.outputs[0]
            ev1, _ = adapter.outputs[1]
            assert isinstance(ev0, TextEvent)
            assert "Hello" in ev0.content
            assert isinstance(ev1, StopEvent)
            # history: user → assistant
            assert len(orch._history) == 2
            assert orch._history[0].role == "user"
            assert orch._history[1].role == "assistant"
        asyncio.run(_test())

    def test_does_not_call_receive_in_loop(self):
        """_phase_loop 不调用 adapter.receive()。"""
        async def _test():
            class SpyAdapter:
                def __init__(self):
                    self.receive_calls = 0
                    self.outputs = []
                    self._receive_inputs = ["hello"]

                async def receive(self):
                    self.receive_calls += 1
                    if self.receive_calls <= len(self._receive_inputs):
                        return UserRequest(
                            text=self._receive_inputs[self.receive_calls - 1]
                        )
                    return UserRequest(text="")

                async def send(self, event, target=None):
                    self.outputs.append(event)

            adapter = SpyAdapter()

            async def text_llm(msgs, tools):
                return Response(text="reply", stop_reason="end_turn")

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=text_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            receive_count_after_init = adapter.receive_calls  # 应为 1

            await orch._phase_loop(ctx)
            receive_count_after_loop = adapter.receive_calls  # 应仍为 1

            # _phase_loop 不应调用 receive()
            assert receive_count_after_init == 1
            assert receive_count_after_loop == 1, (
                f"_phase_loop should NOT call adapter.receive(), "
                f"but receive_calls went from {receive_count_after_init} "
                f"to {receive_count_after_loop}"
            )
        asyncio.run(_test())

    def test_tool_use_loop(self):
        """tool_use 循环：纯 tool_use → LLM again → text。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["read file", ""])

            class MockSTP:
                def __init__(self):
                    self.executed = []

                def get_tools(self):
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

            container = DIContainer()
            container.register(SystemToolProvider, MockSTP())

            call_count = [0]

            async def tool_then_text_llm(msgs, tools):
                call_count[0] += 1
                if call_count[0] == 1:
                    return Response(
                        tool_uses=[
                            ToolCall(
                                id="c1",
                                function=ToolCallFunction(
                                    name="read", arguments='{"path": "/tmp/x"}'
                                ),
                            )
                        ],
                        stop_reason="tool_use",
                    )
                else:
                    return Response(
                        text="File contents: hello world", stop_reason="end_turn"
                    )

            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=tool_then_text_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            stp = container.resolve(SystemToolProvider)
            assert len(stp.executed) == 1
            assert stp.executed[0] == ("read", {"path": "/tmp/x"})
            assert call_count[0] == 2
            assert len(orch._tool_call_records) == 1
            assert orch._tool_call_records[0].tool_name == "read"
        asyncio.run(_test())

    def test_text_and_tool_uses_coexistence(self):
        """text + tool_uses 共存：中间文本不发用户，继续内层循环。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["check file", ""])

            class MockSTP:
                def __init__(self):
                    self.executed = []

                def get_tools(self):
                    return [ToolDefinition(name="read", description="Read")]

                def execute(self, name, args):
                    self.executed.append((name, args))

                    class TR:
                        success = True
                        content = "data from file"
                        error = None

                    return TR()

            container = DIContainer()
            container.register(SystemToolProvider, MockSTP())

            call_count = [0]

            async def coexistence_llm(msgs, tools):
                call_count[0] += 1
                if call_count[0] == 1:
                    return Response(
                        text="Let me check that file for you",
                        tool_uses=[
                            ToolCall(
                                id="c1",
                                function=ToolCallFunction(
                                    name="read", arguments='{"path": "/tmp/x"}'
                                ),
                            )
                        ],
                        stop_reason="end_turn",
                    )
                else:
                    return Response(
                        text="I checked: it contains 'data from file'",
                        stop_reason="end_turn",
                    )

            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=coexistence_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            # 中间文本 "Let me check..." 不应发送给用户
            text_events = [
                ev for ev, _ in adapter.outputs if isinstance(ev, TextEvent)
            ]
            assert len(text_events) == 1
            assert "contains 'data from file'" in text_events[0].content
        asyncio.run(_test())

    def test_tool_execution_failure_recorded(self):
        """Tool 执行失败时错误被完整记录。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["write file", ""])

            class FailingSTP:
                def get_tools(self):
                    return [ToolDefinition(name="write", description="Write")]

                def execute(self, name, args):
                    class TR:
                        success = False
                        content = None
                        error = "Permission denied"

                    return TR()

            container = DIContainer()
            container.register(SystemToolProvider, FailingSTP())

            call_count = [0]

            async def error_llm(msgs, tools):
                call_count[0] += 1
                if call_count[0] == 1:
                    return Response(
                        tool_uses=[
                            ToolCall(
                                id="c1",
                                function=ToolCallFunction(
                                    name="write",
                                    arguments='{"path":"/protected/file"}',
                                ),
                            )
                        ],
                        stop_reason="tool_use",
                    )
                else:
                    return Response(text="I couldn't write", stop_reason="end_turn")

            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=error_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            assert len(orch._tool_call_records) == 1
            assert orch._tool_call_records[0].tool_name == "write"
            assert orch._tool_call_records[0].error == "Permission denied"
        asyncio.run(_test())

    def test_call_llm_none_handling(self):
        """call_llm 为 None 时不崩溃。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])
            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=None
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            # 不应崩溃
            await orch._phase_loop(ctx)
        asyncio.run(_test())

    def test_exit_flag_skips_loop(self):
        """should_exit_flag 为 True 时 _phase_loop 立即返回。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["/exit", ""])
            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            ctx = await orch._phase_init()
            assert orch._should_exit_flag is True

            output_count_before = len(adapter.outputs)

            await orch._phase_loop(ctx)

            # 不应产生新输出
            assert len(adapter.outputs) == output_count_before
        asyncio.run(_test())

    def test_history_integrity_user_assistant(self):
        """history 中 user/assistant 按事件流正确记录。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])

            async def text_llm(msgs, tools):
                return Response(text="reply", stop_reason="end_turn")

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=text_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            assert len(orch._history) == 2
            assert orch._history[0].role == "user"
            assert orch._history[0].content == "hello"
            assert orch._history[1].role == "assistant"
            assert orch._history[1].content == "reply"
        asyncio.run(_test())

    def test_llm_call_exception_propagates(self):
        """LLM 调用异常传播到调用方。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])

            async def failing_llm(msgs, tools):
                raise RuntimeError("LLM API error")

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=failing_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()

            with pytest.raises(RuntimeError, match="LLM API error"):
                await orch._phase_loop(ctx)
        asyncio.run(_test())

    def test_context_assembler_fallback_on_error(self):
        """ContextAssembler 异常时降级到 _fallback_assemble。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])

            class BrokenAssembler:
                def assemble(self, ctx):
                    raise RuntimeError("assembler broken")

            async def text_llm(msgs, tools):
                return Response(text="still works", stop_reason="end_turn")

            container = DIContainer()
            container.register(ContextAssembler, BrokenAssembler())
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=text_llm
            )
            orch._cached_guides = GuidesBundle(identity="system prompt")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            # 不应崩溃——降级到 fallback_assemble
            await orch._phase_loop(ctx)

            assert len(adapter.outputs) >= 1
        asyncio.run(_test())

    def test_tool_router_empty_handled(self):
        """ToolRouter 无工具时 tool_use 不崩溃（记录错误）。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["hello", ""])

            call_count = [0]

            async def tool_llm(msgs, tools):
                call_count[0] += 1
                if call_count[0] == 1:
                    return Response(
                        tool_uses=[
                            ToolCall(
                                id="c1",
                                function=ToolCallFunction(
                                    name="nonexistent", arguments='{"key":"value"}'
                                ),
                            )
                        ],
                        stop_reason="tool_use",
                    )
                else:
                    return Response(
                        text="I see the tool failed", stop_reason="end_turn"
                    )

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=tool_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            # 不应崩溃
            await orch._phase_loop(ctx)

            if orch._tool_call_records:
                assert orch._tool_call_records[0].error is not None
        asyncio.run(_test())

    def test_max_tool_iterations_guard(self):
        """超出最大 tool 迭代次数时强制停止。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["loop test", ""])

            class MockSTP:
                def get_tools(self):
                    return [ToolDefinition(name="loop", description="Loops")]

                def execute(self, name, args):
                    class TR:
                        success = True
                        content = "looped"
                        error = None

                    return TR()

            container = DIContainer()
            container.register(SystemToolProvider, MockSTP())

            # 永远返回 tool_use 的 LLM（模拟无限循环）
            async def looping_llm(msgs, tools):
                return Response(
                    tool_uses=[
                        ToolCall(
                            id="c1",
                            function=ToolCallFunction(name="loop", arguments='{}'),
                        )
                    ],
                    stop_reason="tool_use",
                )

            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=looping_llm
            )
            orch._MAX_TOOL_ITERATIONS = 3  # 加速测试
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            # 应该以 max_iterations StopEvent 结束
            stop_events = [
                ev for ev, _ in adapter.outputs if isinstance(ev, StopEvent)
            ]
            assert any(
                ev.stop_reason == "max_iterations" for ev in stop_events
            )
        asyncio.run(_test())

    def test_thinking_event_sent(self):
        """LLM 返回 thinking 时推送 ThinkingEvent。"""
        async def _test():
            adapter = MockAsyncAdapter(inputs=["think", ""])

            async def thinking_llm(msgs, tools):
                return Response(
                    text="answer",
                    thinking="Let me think about this...",
                    stop_reason="end_turn",
                )

            container = DIContainer()
            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=thinking_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            ctx = await orch._phase_init()
            await orch._phase_loop(ctx)

            thinking_events = [
                ev for ev, _ in adapter.outputs if isinstance(ev, ThinkingEvent)
            ]
            assert len(thinking_events) == 1
            assert "think" in thinking_events[0].content
        asyncio.run(_test())


# ============================================================================
# 8. _phase_end
# ============================================================================


class TestAsyncOrchPhaseEnd:
    """_phase_end 异步测试。"""

    def test_sensor_called_with_trajectory(self):
        """Sensor 被调用并收到正确的 Trajectory。"""
        async def _test():
            container = DIContainer()

            class SpySensor:
                def __init__(self):
                    self.received = None

                def sense(self, trajectory):
                    self.received = trajectory

            container.register(Sensor, SpySensor())
            adapter = MockAsyncAdapter()
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
            orch._history = [Message(role="assistant", content="final answer")]
            orch._tool_call_records = [
                ToolCallRecord(tool_name="read", result="data")
            ]
            orch._start_time = time.time() - 5.0

            trajectory = orch._build_trajectory()
            await orch._phase_end(trajectory)

            sensor = container.resolve(Sensor)
            assert sensor.received is not None
            assert len(sensor.received.history) == 1
            assert sensor.received.final_output == "final answer"
            assert sensor.received.execution_time > 0
        asyncio.run(_test())

    def test_sensor_not_called_when_not_registered(self):
        """Sensor 未注册时不崩溃。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter()
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
            orch._history = [Message(role="assistant", content="done")]

            trajectory = orch._build_trajectory()
            await orch._phase_end(trajectory)
        asyncio.run(_test())

    def test_state_cleaned_after_end(self):
        """_phase_end 后内部状态被清理。"""
        async def _test():
            container = DIContainer()
            adapter = MockAsyncAdapter()
            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
            orch._history = [Message(role="assistant", content="test")]
            orch._tool_call_records = [ToolCallRecord(tool_name="read")]
            orch._should_exit_flag = True

            trajectory = orch._build_trajectory()
            await orch._phase_end(trajectory)

            assert len(orch._history) == 0
            assert len(orch._tool_call_records) == 0
            assert orch._should_exit_flag is False
        asyncio.run(_test())


# ============================================================================
# 9. _build_trajectory
# ============================================================================


class TestAsyncOrchBuildTrajectory:
    """_build_trajectory 测试。"""

    def test_execution_time_is_correct(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
        orch._start_time = time.time() - 3.0
        orch._history = [Message(role="assistant", content="done")]

        traj = orch._build_trajectory()

        assert traj.execution_time >= 3.0

    def test_final_output_from_last_history_entry(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)
        orch._history = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]

        traj = orch._build_trajectory()

        assert traj.final_output == "world"

    def test_empty_history_final_output_is_empty(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

        traj = orch._build_trajectory()

        assert traj.final_output == ""
        assert len(traj.history) == 0


# ============================================================================
# 10. _fallback_assemble
# ============================================================================


class TestAsyncOrchFallbackAssemble:
    """_fallback_assemble 测试。"""

    def test_fallback_with_guides_and_user_request(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

        ctx = AssemblyContext(
            user_request=UserRequest(text="hello"),
            guides=GuidesBundle(identity="system prompt"),
        )

        msgs = orch._fallback_assemble(ctx)

        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[0].content == "system prompt"
        assert msgs[1].role == "user"
        assert msgs[1].content == "hello"

    def test_fallback_without_guides(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

        ctx = AssemblyContext(
            user_request=UserRequest(text="hello"),
        )

        msgs = orch._fallback_assemble(ctx)

        assert len(msgs) == 1
        assert msgs[0].role == "user"

    def test_fallback_with_none_user_request(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

        ctx = AssemblyContext(
            guides=GuidesBundle(identity="system prompt"),
        )

        msgs = orch._fallback_assemble(ctx)

        assert len(msgs) == 1
        assert msgs[0].role == "system"

    def test_fallback_empty_ctx(self):
        container = DIContainer()
        adapter = MockAsyncAdapter()
        orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

        ctx = AssemblyContext()
        msgs = orch._fallback_assemble(ctx)

        assert msgs == []


# ============================================================================
# 11. KernelBridgeAdapter.receive()
# ============================================================================


class TestKernelBridgeAdapterReceive:
    """KBA.receive() 消息转换测试。"""

    @pytest.fixture
    def setup(self):
        kernel = MockKernel()
        kernel.add_queue("test_agent")
        runtime = MockAgentRuntime()
        kba = KernelBridgeAdapter(
            pid="test_agent", kernel=kernel, runtime=runtime
        )
        return kernel, runtime, kba

    def test_receive_user_request_returns_as_is(self, setup):
        """入队 UserRequest → 原样返回。"""
        async def _test():
            kernel, _, kba = setup
            req = UserRequest(text="hello", session_id="s1")
            await kernel.input_queues["test_agent"].put(req)

            result = await kba.receive()

            assert result is req
            assert result.text == "hello"
        asyncio.run(_test())

    def test_receive_internal_message_converts_to_user_request(self, setup):
        """入队 InternalMessage → 转换为 UserRequest。"""
        async def _test():
            kernel, _, kba = setup
            msg = InternalMessage(
                from_pid="collector",
                content="采集到 28 个文件",
            )
            await kernel.input_queues["test_agent"].put(msg)

            result = await kba.receive()

            assert isinstance(result, UserRequest)
            assert result.text == "采集到 28 个文件"
            assert result.metadata["from"] == "collector"
        asyncio.run(_test())

    def test_receive_internal_message_with_stop_metadata(self, setup):
        """StopEvent 转换的 InternalMessage 保留 stop metadata。"""
        async def _test():
            kernel, _, kba = setup
            msg = InternalMessage(
                from_pid="collector",
                content="",
                metadata={"stop": True},
            )
            await kernel.input_queues["test_agent"].put(msg)

            result = await kba.receive()

            assert result.text == ""
            assert result.metadata["stop"] is True
            assert result.metadata["from"] == "collector"
        asyncio.run(_test())

    def test_receive_exit_sentinel_converts_to_exit_request(self, setup):
        """入队 __EXIT_SENTINEL__ → 返回 exit UserRequest。"""
        async def _test():
            kernel, _, kba = setup
            await kernel.input_queues["test_agent"].put(__EXIT_SENTINEL__)

            result = await kba.receive()

            assert isinstance(result, UserRequest)
            assert result.text == ""
            assert result.metadata["exit"] is True
        asyncio.run(_test())

    def test_receive_unknown_type_converts_to_string(self, setup):
        """未知类型降级为 str。"""
        async def _test():
            kernel, _, kba = setup
            await kernel.input_queues["test_agent"].put(42)

            result = await kba.receive()

            assert isinstance(result, UserRequest)
            assert result.text == "42"
        asyncio.run(_test())


# ============================================================================
# 12. KernelBridgeAdapter.send()
# ============================================================================


class TestKernelBridgeAdapterSend:
    """KBA.send() 事件路由/过滤/降级测试。"""

    @pytest.fixture
    def setup(self):
        kernel = MockKernel()
        kernel.add_queue("sender")
        kernel.add_queue("target")
        runtime = MockAgentRuntime()
        kba = KernelBridgeAdapter(
            pid="sender", kernel=kernel, runtime=runtime
        )
        return kernel, runtime, kba

    def test_exit_protection_should_exit(self, setup):
        """should_exit=True 时静默丢弃所有输出。"""
        async def _test():
            kernel, runtime, kba = setup
            runtime.should_exit = True

            await kba.send(TextEvent(content="should be dropped"))

            # console 不应收到消息
            assert len(kernel._console.events) == 0
            # target queue 应为空
            assert kernel.input_queues["target"].empty()
        asyncio.run(_test())

    def test_text_event_fallback_to_console(self, setup):
        """TextEvent, target=None → 降级到 console。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(TextEvent(content="Hello world"))

            assert len(kernel._console.events) == 1
            ev = kernel._console.events[0]
            assert isinstance(ev, AgentOutput)
            assert ev.pid == "sender"
            assert ev.content == "Hello world"
        asyncio.run(_test())

    def test_stop_event_discarded(self, setup):
        """StopEvent, target=None → 静默丢弃。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(StopEvent(stop_reason="end_turn"))

            # console 不应收到消息
            assert len(kernel._console.events) == 0
        asyncio.run(_test())

    def test_thinking_event_fallback_to_console(self, setup):
        """ThinkingEvent → 降级到 console（中间事件）。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(ThinkingEvent(content="Hmm, let me think..."))

            assert len(kernel._console.events) == 1
            ev = kernel._console.events[0]
            assert isinstance(ev, AgentOutput)
            assert "ThinkingEvent" in ev.content
            assert "Hmm" in ev.content
        asyncio.run(_test())

    def test_tool_call_event_fallback_to_console(self, setup):
        """ToolCallEvent → 降级到 console（中间事件）。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(ToolCallEvent(
                call_id="c1", tool_name="read", arguments={"path": "/x"}
            ))

            assert len(kernel._console.events) == 1
            ev = kernel._console.events[0]
            assert isinstance(ev, AgentOutput)
            assert "ToolCallEvent" in ev.content
        asyncio.run(_test())

    def test_tool_result_event_fallback_to_console(self, setup):
        """ToolResultEvent → 降级到 console（中间事件）。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(ToolResultEvent(
                call_id="c1", tool_name="read", success=True, result="data"
            ))

            assert len(kernel._console.events) == 1
            ev = kernel._console.events[0]
            assert isinstance(ev, AgentOutput)
            assert "ToolResultEvent" in ev.content
        asyncio.run(_test())

    def test_direct_target_text_event(self, setup):
        """TextEvent, target=pid → AgentOutput 到 console + 入队 target queue。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(TextEvent(content="定向消息"), target="target")

            # console 同步收到 AgentOutput（终端始终可见）
            assert len(kernel._console.events) == 1
            assert isinstance(kernel._console.events[0], AgentOutput)
            assert kernel._console.events[0].content == "定向消息"
            # target queue 应有消息
            assert not kernel.input_queues["target"].empty()
            msg = kernel.input_queues["target"].get_nowait()
            assert isinstance(msg, InternalMessage)
            assert msg.from_pid == "sender"
            assert msg.content == "定向消息"
            assert msg.metadata.get("stop") is None  # TextEvent 不含 stop
        asyncio.run(_test())

    def test_direct_target_stop_event(self, setup):
        """StopEvent, target=pid → InternalMessage 含 stop metadata。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(StopEvent(stop_reason="end_turn"), target="target")

            msg = kernel.input_queues["target"].get_nowait()
            assert isinstance(msg, InternalMessage)
            assert msg.from_pid == "sender"
            assert msg.content == ""
            assert msg.metadata["stop"] is True
        asyncio.run(_test())

    def test_direct_target_intermediate_event_degraded_to_console(self, setup):
        """Batch 3: 中间事件（ThinkingEvent等）始终降级到 SystemConsole，
        即使指定了 target——事件类型过滤优先于路由分派。"""
        async def _test():
            kernel, _, kba = setup

            # 定向投递 ThinkingEvent 应降级到 console（不经过 target）
            await kba.send(ThinkingEvent(content="thinking..."), target="target")

            # console 应收到降级输出
            assert len(kernel._console.events) >= 1
            assert any(
                "ThinkingEvent" in str(e.content)
                for e in kernel._console.events
            )
            # target queue 不应收到（中间事件不参与路由）
            assert kernel.input_queues["target"].empty()
        asyncio.run(_test())

    def test_sender_queue_not_affected(self, setup):
        """消息只入队目标 queue，不影响发送者 queue。"""
        async def _test():
            kernel, _, kba = setup

            await kba.send(TextEvent(content="msg"), target="target")

            # sender queue 应仍为空
            assert kernel.input_queues["sender"].empty()
        asyncio.run(_test())


# ============================================================================
# 13. 集成测试：完整三阶段
# ============================================================================


class TestAsyncOrchFullLifecycle:
    """完整三阶段端到端测试。"""

    def test_full_three_phase_with_mocks(self):
        """完整三阶段走通（最小组件）。"""
        async def _test():
            container = DIContainer()

            class MockSensor:
                def __init__(self):
                    self.called = False
                    self.traj = None

                def sense(self, traj):
                    self.called = True
                    self.traj = traj

            container.register(Sensor, MockSensor())

            adapter = MockAsyncAdapter(inputs=["hello", ""])

            async def mock_llm(msgs, tools):
                return Response(text="mock reply", stop_reason="end_turn")

            orch = AsyncLifecycleOrchestrator(
                container, adapter=adapter, call_llm=mock_llm
            )
            orch._cached_guides = GuidesBundle(identity="test")
            orch._cached_tools = []

            # 阶段一
            ctx = await orch._phase_init()
            assert ctx is not None
            assert ctx.user_request.text == "hello"

            # 阶段二
            await orch._phase_loop(ctx)
            assert len(adapter.outputs) >= 2  # TextEvent + StopEvent

            # 阶段三
            traj = orch._build_trajectory()
            await orch._phase_end(traj)

            sensor = container.resolve(Sensor)
            assert sensor.called is True
            assert sensor.traj is not None
            assert sensor.traj.final_output == "mock reply"
        asyncio.run(_test())

    def test_init_exit_skips_loop(self):
        """_phase_init 检测到退出后，_phase_loop 跳过，_phase_end 仍执行。"""
        async def _test():
            container = DIContainer()

            class SpySensor:
                def __init__(self):
                    self.called = False

                def sense(self, traj):
                    self.called = True

            container.register(Sensor, SpySensor())
            adapter = MockAsyncAdapter(inputs=["/exit", ""])

            orch = AsyncLifecycleOrchestrator(container, adapter=adapter)

            # 阶段一：检测到退出
            ctx = await orch._phase_init()
            assert orch._should_exit_flag is True

            # 阶段二：跳过
            await orch._phase_loop(ctx)
            # （不抛异常，无输出）

            # 阶段三：仍执行
            traj = orch._build_trajectory()
            await orch._phase_end(traj)

            sensor = container.resolve(Sensor)
            assert sensor.called is True
        asyncio.run(_test())
