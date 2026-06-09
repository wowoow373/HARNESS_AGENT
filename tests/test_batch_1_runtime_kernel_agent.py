"""Tests for Batch 1: Runtime + Kernel + AgentRuntime 骨架。

覆盖：
- AgentState 枚举
- AgentRuntime（构造、run()、_idle_for_quiescence、_extract_last_output）
- Kernel（spawn_root、send_input、kill、end_workflow、list_agents、all_finished）
- CliConsole（send 格式化输出）
- SystemConsole Protocol 满足性
- Runtime（sync→async bridge、完整启动流程）
- SIGINT 两阶段处理器

使用 asyncio.run() 包装异步测试，不依赖 pytest-asyncio 插件。
"""

import asyncio
import time

import pytest

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.container import DIContainer
from harness.interfaces.async_input_adapter import AsyncInputAdapter
from harness.interfaces.system_console import SystemConsole
from harness.interfaces.types import (
    AssemblyContext,
    GuidesBundle,
    Message,
    Response,
    UserRequest,
)
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.runtime.cli_console import CliConsole
from harness.runtime.kernel import Kernel
from harness.runtime.runtime import Runtime
from harness.runtime.signals import create_sigint_handler
from harness.runtime.types import (
    AgentFinished,
    AgentOutput,
    AgentSpawned,
    AgentStateChanged,
    CommandTalk,
    InternalMessage,
    RuntimeStarted,
    RuntimeStopped,
    SystemCommand,
    SystemEvent,
    __EXIT_SENTINEL__,
)


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


class MockConsole:
    """SystemConsole 的 spy 实现。"""

    def __init__(self, inputs=None):
        if inputs is None:
            inputs = ["hello", ""]
        self.inputs = list(inputs)
        self.idx = 0
        self.events = []

    async def receive(self) -> SystemCommand:
        if self.idx < len(self.inputs):
            text = self.inputs[self.idx]
            self.idx += 1
            return CommandTalk(pid="root", text=text)
        return CommandTalk(pid="root", text="")

    async def send(self, event: SystemEvent) -> None:
        self.events.append(event)


class MockHarness:
    """Harness 的最小 stub。"""

    def __init__(self, container=None):
        self.container = container or DIContainer()
        self.call_llm = None


class MockAsyncAdapter:
    """AsyncInputAdapter 的最小 mock 实现。"""

    def __init__(self, inputs=None):
        if inputs is None:
            inputs = ["hello"]
        self.inputs = list(inputs)
        self.outputs = []
        self.idx = 0

    async def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]
            self.idx += 1
            return UserRequest(text=t)
        return UserRequest(text="")

    async def send(self, event, target=None):
        self.outputs.append((event, target))


# ============================================================================
# 1. AgentState 枚举
# ============================================================================


class TestAgentState:
    """AgentState 枚举测试。"""

    def test_all_five_members_exist(self):
        """5 个成员全部定义。"""
        assert AgentState.CREATED.value == "created"
        assert AgentState.INIT.value == "init"
        assert AgentState.RUNNING.value == "running"
        assert AgentState.TERMINATING.value == "terminating"
        assert AgentState.FINISHED.value == "finished"

    def test_is_enum(self):
        """是标准 enum.Enum。"""
        assert isinstance(AgentState.CREATED, AgentState)


# ============================================================================
# 2. AgentRuntime 构造
# ============================================================================


class TestAgentRuntimeInit:
    """AgentRuntime 初始化测试。"""

    def test_init_created_state(self):
        """构造后状态为 CREATED。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        assert rt.state == AgentState.CREATED
        assert rt.should_exit is False
        assert rt.error is None
        assert rt._idle_since is None
        assert rt.round_count == 0
        assert rt._orchestrator is None

    def test_init_oneshot_mode(self):
        """oneshot 模式正确存储。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="oneshot",
            harness=harness,
            kernel=kernel,
        )
        assert rt.mode == "oneshot"

    def test_init_continuous_mode(self):
        """continuous 模式正确存储。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        assert rt.mode == "continuous"

    def test_init_orchestrator_creates_orchestrator(self):
        """_init_orchestrator 正确创建 AsyncLifecycleOrchestrator。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        adapter = MockAsyncAdapter()
        rt.adapter = adapter

        rt._init_orchestrator(call_llm=None)

        assert rt._orchestrator is not None
        assert isinstance(rt._orchestrator, AsyncLifecycleOrchestrator)
        assert rt._orchestrator._adapter is adapter

    def test_init_with_parent(self):
        """父 agent 引用正确存储。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        parent = AgentRuntime(
            pid="parent",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        child = AgentRuntime(
            pid="child",
            mode="oneshot",
            harness=harness,
            kernel=kernel,
            parent=parent,
        )
        assert child.parent is parent

    def test_max_rounds_default(self):
        """max_rounds 默认为 1000。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        assert rt.max_rounds == 1000

    def test_max_rounds_custom(self):
        """max_rounds 可自定义。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
            max_rounds=5,
        )
        assert rt.max_rounds == 5


# ============================================================================
# 3. AgentRuntime._idle_for_quiescence
# ============================================================================


class TestAgentRuntimeIdle:
    """_idle_for_quiescence 测试。"""

    def test_running_and_idle_returns_true(self):
        """RUNNING + _idle_since 非空 → True。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        rt.state = AgentState.RUNNING
        rt._idle_since = time.time()
        assert rt._idle_for_quiescence() is True

    def test_running_and_not_idle_returns_false(self):
        """RUNNING + _idle_since 为 None → False。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        rt.state = AgentState.RUNNING
        rt._idle_since = None
        assert rt._idle_for_quiescence() is False

    def test_not_running_returns_false(self):
        """非 RUNNING → False（即使 _idle_since 非空）。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        for state in [AgentState.CREATED, AgentState.INIT,
                       AgentState.TERMINATING, AgentState.FINISHED]:
            rt.state = state
            rt._idle_since = time.time()
            assert rt._idle_for_quiescence() is False, f"state={state}"


# ============================================================================
# 4. AgentRuntime._extract_last_output
# ============================================================================


class TestAgentRuntimeExtractLastOutput:
    """_extract_last_output 测试。"""

    def test_extracts_last_assistant_content(self):
        """提取最后一条 assistant 消息的内容。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        adapter = MockAsyncAdapter()
        rt.adapter = adapter
        rt._init_orchestrator()
        rt._orchestrator._history = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        assert rt._extract_last_output() == "world"

    def test_empty_history_returns_empty_string(self):
        """空 history → ""。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        adapter = MockAsyncAdapter()
        rt.adapter = adapter
        rt._init_orchestrator()
        # 不设置 _history
        assert rt._extract_last_output() == ""

    def test_no_assistant_messages_returns_empty(self):
        """history 中无 assistant 消息 → ""。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        adapter = MockAsyncAdapter()
        rt.adapter = adapter
        rt._init_orchestrator()
        rt._orchestrator._history = [
            Message(role="user", content="hello"),
            Message(role="tool", content="result"),
        ]
        assert rt._extract_last_output() == ""

    def test_no_orchestrator_returns_empty(self):
        """_orchestrator 为 None → ""。"""
        harness = MockHarness()
        kernel = Kernel(MockConsole())
        rt = AgentRuntime(
            pid="test",
            mode="continuous",
            harness=harness,
            kernel=kernel,
        )
        assert rt._extract_last_output() == ""


# ============================================================================
# 5. AgentRuntime.run() — 完整生命周期
# ============================================================================


class TestAgentRuntimeRun:
    """AgentRuntime.run() 协程测试（mock orchestrator 组件）。"""

    def test_run_completes_three_phases_continuous(self):
        """continuous 模式：完整三阶段走通。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole(inputs=["hello", ""])
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            # 挂载 KBA
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            rt._init_orchestrator(call_llm=None)

            # 投递首轮输入
            await kernel.input_queues["test"].put(
                UserRequest(text="hello")
            )
            # 投递第二轮输入（空 text → 退出）
            await kernel.input_queues["test"].put(
                UserRequest(text="")
            )

            await rt.run()

            assert rt.state == AgentState.FINISHED
            assert rt.round_count >= 1

        asyncio.run(_test())

    def test_run_oneshot_auto_exits(self):
        """oneshot 模式：一轮后自动退出。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="oneshot",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            # 不需要 call_llm——没有 LLM 时 _phase_loop 发 StopEvent("no_llm")
            rt._init_orchestrator(call_llm=None)

            await kernel.input_queues["test"].put(
                UserRequest(text="do one thing")
            )

            await rt.run()

            assert rt.state == AgentState.FINISHED
            assert rt.round_count == 1  # oneshot 只跑一轮

        asyncio.run(_test())

    def test_run_should_exit_from_outside(self):
        """外部设 should_exit=True → agent 退出。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole(inputs=["hello"])
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            rt._init_orchestrator(call_llm=None)

            await kernel.input_queues["test"].put(
                UserRequest(text="hello")
            )

            # 在 agent 开始等待第二轮输入后，外部设 should_exit
            async def set_exit_later():
                await asyncio.sleep(0.05)
                rt.should_exit = True
                # 推送 sentinel 唤醒 receive()
                kernel.input_queues["test"].put_nowait(__EXIT_SENTINEL__)

            task_set = asyncio.create_task(set_exit_later())
            await rt.run()
            await task_set

            assert rt.state == AgentState.FINISHED

        asyncio.run(_test())

    def test_run_phase_init_exit(self):
        """_phase_init 收到 exit → 跳过 _phase_loop → FINISHED。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            rt._init_orchestrator(call_llm=None)

            # 直接投递 exit sentinel — _phase_init 中 receive() 拿到 sentinel
            await kernel.input_queues["test"].put(__EXIT_SENTINEL__)

            await rt.run()

            assert rt.state == AgentState.FINISHED
            assert rt.round_count == 0  # 没进过 _phase_loop

        asyncio.run(_test())

    def test_run_max_rounds_safety_net(self):
        """到达 max_rounds 后退出（不在 receive 处额外阻塞）。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
                max_rounds=2,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            rt._init_orchestrator(call_llm=None)

            # _phase_init 取第一项，receive() 在每轮后取下一项
            await kernel.input_queues["test"].put(
                UserRequest(text="round 1")
            )
            await kernel.input_queues["test"].put(
                UserRequest(text="round 2")
            )

            await rt.run()

            assert rt.state == AgentState.FINISHED
            assert rt.round_count == 2

        asyncio.run(_test())

    def test_run_idle_since_tracking(self):
        """_idle_since 在 receive() 前后正确设置和清除。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            async def spy_llm(msgs, tools):
                # 在 call_llm 中，_idle_since 应为 None
                return Response(text="ok", stop_reason="end_turn")

            rt._init_orchestrator(call_llm=spy_llm)

            await kernel.input_queues["test"].put(
                UserRequest(text="hello")
            )
            # 第二轮空输入 → exit
            await kernel.input_queues["test"].put(
                UserRequest(text="")
            )

            await rt.run()

            # 结束后 _idle_since 应为 None（在 finally 之前 receive 已返回）
            assert rt._idle_since is None

        asyncio.run(_test())

    def test_run_exception_recorded_in_error(self):
        """call_llm 异常时 error 被记录，_phase_end 仍执行。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            async def failing_llm(msgs, tools):
                raise RuntimeError("LLM API error")

            rt._init_orchestrator(call_llm=failing_llm)

            await kernel.input_queues["test"].put(
                UserRequest(text="hello")
            )

            await rt.run()

            assert rt.state == AgentState.FINISHED
            assert rt.error is not None
            assert "RuntimeError" in rt.error

        asyncio.run(_test())


# ============================================================================
# 6. Kernel — spawn_root
# ============================================================================


class TestKernelSpawnRoot:
    """Kernel.spawn_root 测试。"""

    def test_creates_agent_with_pid_root(self):
        """创建 pid="root" 的 agent。"""
        console = MockConsole()
        kernel = Kernel(console)
        harness = MockHarness()

        # spawn_root 内部有 asyncio.create_task，必须在 event loop 中调用。
        # 投递 sentinel 让 agent 立即退出，避免 asyncio.run() 阻塞。
        async def _test():
            pid = kernel.spawn_root(harness)
            # 让 agent 立即退出
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            # 短暂等待让 task 处理 sentinel
            await asyncio.sleep(0.1)
            return pid

        pid = asyncio.run(_test())
        assert pid == "root"
        assert "root" in kernel.runtime_table

    def test_registers_input_queue(self):
        """为 root 注册 asyncio.Queue。"""
        console = MockConsole()
        kernel = Kernel(console)
        harness = MockHarness()

        async def _test():
            kernel.spawn_root(harness)
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            await asyncio.sleep(0.1)

        asyncio.run(_test())
        assert "root" in kernel.input_queues
        assert isinstance(kernel.input_queues["root"], asyncio.Queue)

    def test_creates_asyncio_task(self):
        """启动 asyncio Task。"""
        console = MockConsole()
        kernel = Kernel(console)
        harness = MockHarness()

        async def _test():
            kernel.spawn_root(harness)
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            await asyncio.sleep(0.1)

        asyncio.run(_test())
        assert "root" in kernel._tasks
        assert isinstance(kernel._tasks["root"], asyncio.Task)

    def test_records_workflow(self):
        """workflow_table 中记录 wf_root。"""
        console = MockConsole()
        kernel = Kernel(console)
        harness = MockHarness()

        async def _test():
            kernel.spawn_root(harness)
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            await asyncio.sleep(0.1)

        asyncio.run(_test())
        assert "wf_root" in kernel.workflow_table
        assert kernel.workflow_table["wf_root"] == ["root"]

    def test_pushes_agent_spawned_event(self):
        """推送 AgentSpawned 事件。"""
        console = MockConsole()

        async def _test():
            kernel = Kernel(console)
            harness = MockHarness()
            kernel.spawn_root(harness)
            # 等 console.send task + agent init 执行
            await asyncio.sleep(0.05)

            spawned_events = [
                e for e in console.events
                if isinstance(e, AgentSpawned)
            ]
            assert len(spawned_events) >= 1
            assert spawned_events[0].pid == "root"

            # 让 agent 退出，避免 asyncio.run() block
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            await asyncio.sleep(0.05)

        asyncio.run(_test())

    def test_mounts_kernel_bridge_adapter(self):
        """挂载 KernelBridgeAdapter 到 agent。"""
        console = MockConsole()
        kernel = Kernel(console)
        harness = MockHarness()

        async def _test():
            kernel.spawn_root(harness)
            kernel.input_queues["root"].put_nowait(__EXIT_SENTINEL__)
            await asyncio.sleep(0.1)

        asyncio.run(_test())
        rt = kernel.runtime_table["root"]
        assert rt.adapter is not None
        assert isinstance(rt.adapter, KernelBridgeAdapter)


# ============================================================================
# 7. Kernel — send_input / kill / end_workflow / finish_agent
# ============================================================================


class TestKernelMethods:
    """Kernel 公开方法测试。"""

    def test_send_input_puts_request_in_queue(self):
        """send_input 正确入队 UserRequest。"""
        console = MockConsole()
        kernel = Kernel(console)
        kernel.runtime_table["test"] = _stub_runtime("test")
        kernel.input_queues["test"] = asyncio.Queue()

        req = UserRequest(text="hello")
        kernel.send_input("test", req)

        assert not kernel.input_queues["test"].empty()
        assert kernel.input_queues["test"].get_nowait() is req

    def test_send_input_unknown_pid_logs_warning(self, caplog):
        """不存在的 pid → WARNING 日志。"""
        console = MockConsole()
        kernel = Kernel(console)

        import logging
        with caplog.at_level(logging.WARNING):
            kernel.send_input("nonexistent", UserRequest(text="hi"))

        assert "nonexistent" in caplog.text

    def test_kill_sets_should_exit(self):
        """kill 设 should_exit=True。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("test")
        kernel.runtime_table["test"] = rt
        kernel.input_queues["test"] = asyncio.Queue()

        assert rt.should_exit is False
        kernel.kill("test")
        assert rt.should_exit is True

    def test_kill_puts_sentinel(self):
        """kill 推送 __EXIT_SENTINEL__。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("test")
        kernel.runtime_table["test"] = rt
        kernel.input_queues["test"] = asyncio.Queue()

        kernel.kill("test")

        assert not kernel.input_queues["test"].empty()
        assert kernel.input_queues["test"].get_nowait() is __EXIT_SENTINEL__

    def test_kill_finished_agent_skips(self):
        """已 FINISHED agent 不操作。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("test")
        rt.state = AgentState.FINISHED
        kernel.runtime_table["test"] = rt
        kernel.input_queues["test"] = asyncio.Queue()

        kernel.kill("test")
        # should_exit 不应改变
        assert rt.should_exit is False

    def test_end_workflow_kills_all_agents(self):
        """end_workflow 对 workflow 中所有 agent 调 kill。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt1 = _stub_runtime("agent1")
        rt2 = _stub_runtime("agent2")
        kernel.runtime_table["agent1"] = rt1
        kernel.runtime_table["agent2"] = rt2
        kernel.input_queues["agent1"] = asyncio.Queue()
        kernel.input_queues["agent2"] = asyncio.Queue()
        kernel.workflow_table["wf_001"] = ["agent1", "agent2"]

        kernel.end_workflow("wf_001")

        assert rt1.should_exit is True
        assert rt2.should_exit is True

    def test_end_workflow_unknown_flag_no_error(self):
        """不存在 flag 不崩溃。"""
        console = MockConsole()
        kernel = Kernel(console)
        kernel.end_workflow("nonexistent")  # 不抛异常

    def test_finish_agent_equals_kill(self):
        """finish_agent 等同于 kill。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("test")
        kernel.runtime_table["test"] = rt
        kernel.input_queues["test"] = asyncio.Queue()

        kernel.finish_agent("test")

        assert rt.should_exit is True
        assert not kernel.input_queues["test"].empty()

    def test_list_agents_snapshot(self):
        """list_agents 返回正确快照。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("agent1")
        rt.mode = "continuous"
        rt.round_count = 3
        rt.error = "some error"
        kernel.runtime_table["agent1"] = rt

        info = kernel.list_agents()

        assert "agent1" in info
        assert info["agent1"]["state"] == "running"
        assert info["agent1"]["mode"] == "continuous"
        assert info["agent1"]["parent"] is None
        assert info["agent1"]["rounds"] == 3
        assert info["agent1"]["error"] == "some error"

    def test_list_agents_shows_parent(self):
        """list_agents 正确显示父 agent pid。"""
        console = MockConsole()
        kernel = Kernel(console)
        parent = _stub_runtime("parent")
        child = _stub_runtime("child")
        child.parent = parent
        kernel.runtime_table["parent"] = parent
        kernel.runtime_table["child"] = child

        info = kernel.list_agents()

        assert info["child"]["parent"] == "parent"

    def test_all_finished_true(self):
        """全部 FINISHED → True。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt1 = _stub_runtime("a")
        rt2 = _stub_runtime("b")
        rt1.state = AgentState.FINISHED
        rt2.state = AgentState.FINISHED
        kernel.runtime_table["a"] = rt1
        kernel.runtime_table["b"] = rt2

        assert kernel.all_finished() is True

    def test_all_finished_false(self):
        """有非 FINISHED → False。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt1 = _stub_runtime("a")
        rt2 = _stub_runtime("b")
        rt1.state = AgentState.FINISHED
        rt2.state = AgentState.RUNNING
        kernel.runtime_table["a"] = rt1
        kernel.runtime_table["b"] = rt2

        assert kernel.all_finished() is False


# ============================================================================
# 8. Kernel — _on_agent_finished (stub)
# ============================================================================


class TestKernelOnAgentFinished:
    """_on_agent_finished stub 测试。"""

    def test_pushes_agent_finished_event(self):
        """推送 AgentFinished 事件到 SystemConsole。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)

            rt = _stub_runtime("test")
            rt.started_at = time.time() - 3.0
            rt.last_output = "done"
            rt.error = None

            await kernel._on_agent_finished(rt)

            finished = [
                e for e in console.events
                if isinstance(e, AgentFinished)
            ]
            assert len(finished) == 1
            assert finished[0].pid == "test"
            assert finished[0].result == "done"
            assert finished[0].duration >= 2.9
            assert finished[0].error is None

        asyncio.run(_test())

    def test_pushes_error_info(self):
        """异常退出时 error 字段非空。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)

            rt = _stub_runtime("test")
            rt.started_at = time.time() - 1.0
            rt.last_output = ""
            rt.error = "RuntimeError: LLM API error"

            await kernel._on_agent_finished(rt)

            finished = [
                e for e in console.events
                if isinstance(e, AgentFinished)
            ]
            assert len(finished) == 1
            assert finished[0].error == "RuntimeError: LLM API error"

        asyncio.run(_test())


# ============================================================================
# 9. Kernel — _monitor_quiescence (stub)
# ============================================================================


class TestKernelMonitorQuiescence:
    """_monitor_quiescence stub 测试。"""

    def test_returns_when_all_finished(self):
        """所有 agent FINISHED 后返回。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)
            # shutdown 标志让循环在一次检查后退出
            kernel._shutdown = True

            await kernel._monitor_quiescence()
            # 不抛异常，正常返回

        asyncio.run(_test())


# ============================================================================
# 10. Kernel — _handle_system_input (stub)
# ============================================================================


class TestKernelHandleSystemInput:
    """_handle_system_input stub 测试。"""

    def test_routes_text_to_root(self):
        """CommandTalk → send_input to root。"""

        async def _test():
            console = MockConsole(inputs=["hello"])
            kernel = Kernel(console)
            kernel._shutdown = False
            kernel.runtime_table["root"] = _stub_runtime("root")
            kernel.input_queues["root"] = asyncio.Queue()

            # 用 asyncio.wait_for 防止无限循环
            async def _handle_one():
                # 重写：只处理一条命令后返回
                command = await console.receive()
                if isinstance(command, CommandTalk):
                    kernel.send_input(
                        command.pid,
                        UserRequest(text=command.text),
                    )

            await _handle_one()

            assert not kernel.input_queues["root"].empty()
            req = kernel.input_queues["root"].get_nowait()
            assert req.text == "hello"

        asyncio.run(_test())


# ============================================================================
# 11. CliConsole — send
# ============================================================================


class TestCliConsoleSend:
    """CliConsole.send() 格式化输出测试。"""

    def test_send_agent_spawned(self, capsys):
        """AgentSpawned 格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentSpawned(pid="root"))
            captured = capsys.readouterr()
            assert "Agent spawned: root" in captured.out

        asyncio.run(_test())

    def test_send_agent_spawned_with_parent(self, capsys):
        """AgentSpawned 含 parent 信息。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentSpawned(pid="child", parent="root"))
            captured = capsys.readouterr()
            assert "parent=root" in captured.out

        asyncio.run(_test())

    def test_send_agent_finished_normal(self, capsys):
        """AgentFinished 正常退出格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentFinished(
                pid="root", result="done", duration=3.5, error=None
            ))
            captured = capsys.readouterr()
            assert "正常完成" in captured.out
            assert "3.5s" in captured.out

        asyncio.run(_test())

    def test_send_agent_finished_error(self, capsys):
        """AgentFinished 异常退出格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentFinished(
                pid="root", result="", duration=1.0,
                error="RuntimeError: boom"
            ))
            captured = capsys.readouterr()
            assert "异常退出" in captured.out

        asyncio.run(_test())

    def test_send_agent_output(self, capsys):
        """AgentOutput 显示 [pid] 标签。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentOutput(pid="root", content="Hello world"))
            captured = capsys.readouterr()
            assert "[root] Hello world" in captured.out

        asyncio.run(_test())

    def test_send_runtime_started(self, capsys):
        """RuntimeStarted 格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(RuntimeStarted())
            captured = capsys.readouterr()
            assert "Runtime 启动" in captured.out

        asyncio.run(_test())

    def test_send_runtime_stopped(self, capsys):
        """RuntimeStopped 格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(RuntimeStopped())
            captured = capsys.readouterr()
            assert "Runtime 停止" in captured.out

        asyncio.run(_test())

    def test_send_agent_state_changed(self, capsys):
        """AgentStateChanged 格式化。"""

        async def _test():
            console = CliConsole()
            await console.send(AgentStateChanged(
                pid="root", old="running", new="terminating"
            ))
            captured = capsys.readouterr()
            assert "running → terminating" in captured.out

        asyncio.run(_test())


# ============================================================================
# 12. SystemConsole Protocol
# ============================================================================


class TestSystemConsoleProtocol:
    """SystemConsole Protocol 满足性检查。"""

    def test_protocol_is_runtime_checkable(self):
        """标记为 @runtime_checkable。"""
        assert hasattr(SystemConsole, '_is_protocol')

    def test_cli_console_satisfies_protocol(self):
        """CliConsole 满足 SystemConsole Protocol。"""
        assert isinstance(CliConsole(), SystemConsole)

    def test_mock_console_satisfies_protocol(self):
        """MockConsole（有 receive/send）满足 Protocol。"""
        assert isinstance(MockConsole(), SystemConsole)

    def test_missing_receive_fails_protocol(self):
        """缺少 receive 不满足 Protocol。"""
        class NoReceive:
            async def send(self, event):
                pass
        assert not isinstance(NoReceive(), SystemConsole)

    def test_missing_send_fails_protocol(self):
        """缺少 send 不满足 Protocol。"""
        class NoSend:
            async def receive(self):
                return CommandTalk(pid="root", text="")
        assert not isinstance(NoSend(), SystemConsole)


# ============================================================================
# 13. Runtime — sync→async bridge + run
# ============================================================================


class TestRuntimeBridge:
    """sync→async LLM bridge 测试。"""

    def test_sync_call_llm_is_wrapped(self):
        """同步 call_llm 被包装为 async。"""

        def sync_llm(msgs, tools):
            return Response(text="sync reply")

        # 模拟 Runtime._run_async 中的桥接逻辑
        call_llm = sync_llm
        if call_llm and not asyncio.iscoroutinefunction(call_llm):
            original = call_llm

            async def _async_wrapper(msgs, tools):
                return await asyncio.to_thread(original, msgs, tools)

            call_llm = _async_wrapper

        assert asyncio.iscoroutinefunction(call_llm)

    def test_async_call_llm_is_not_rewrapped(self):
        """已是 async 的 call_llm 不被再次包装。"""

        async def async_llm(msgs, tools):
            return Response(text="async reply")

        call_llm = async_llm
        assert asyncio.iscoroutinefunction(call_llm)
        # 不会被桥接（asyncio.iscoroutinefunction 返回 True）


# ============================================================================
# 14. SIGINT handler
# ============================================================================


class TestSigintHandler:
    """SIGINT 两阶段处理器测试。"""

    def test_first_stage_sets_should_exit(self):
        """第一阶段：所有 agent 的 should_exit=True + 推 sentinel。"""
        console = MockConsole()
        kernel = Kernel(console)
        rt = _stub_runtime("test")
        kernel.runtime_table["test"] = rt
        kernel.input_queues["test"] = asyncio.Queue()

        # 创建一个 mock Runtime
        class MockRuntime:
            _kernel = kernel
            _sigint_count = 0

        mock_runtime = MockRuntime()
        handler = create_sigint_handler(mock_runtime)

        handler()  # 第一阶段

        assert rt.should_exit is True
        assert not kernel.input_queues["test"].empty()
        assert kernel.input_queues["test"].get_nowait() is __EXIT_SENTINEL__
        assert mock_runtime._sigint_count == 1

    def test_second_stage_cancels_tasks(self):
        """第二阶段：task.cancel()。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)
            rt = _stub_runtime("test")
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            # 创建一个真实的 asyncio Task
            async def dummy_task():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass

            task = asyncio.create_task(dummy_task())
            kernel._tasks["test"] = task

            class MockRuntime:
                _kernel = kernel
                _sigint_count = 1  # 已经是第二阶段

            handler = create_sigint_handler(MockRuntime())
            handler()  # 第二阶段

            # asyncio cancel 需要 event loop 轮次来传播 CancelledError
            await asyncio.sleep(0)

            assert task.cancelled() or task.done()

        asyncio.run(_test())

    def test_kernel_none_is_safe(self):
        """kernel 为 None 时不崩溃。"""
        class MockRuntime:
            _kernel = None
            _sigint_count = 0

        handler = create_sigint_handler(MockRuntime())
        handler()  # 不应崩溃


# ============================================================================
# 15. 集成测试：AgentRuntime + Kernel + KBA 完整流程
# ============================================================================


class TestIntegrationAgentKernelKBA:
    """AgentRuntime + Kernel + KBA 集成测试。"""

    def test_full_flow_with_mock_llm_oneshot(self):
        """oneshot 模式：完整三阶段走通（mock LLM）。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            async def mock_llm(msgs, tools):
                return Response(
                    text="Hello! How can I help?",
                    stop_reason="end_turn",
                )

            # 手动创建 AgentRuntime（不通过 spawn_root 避免 agent 阻塞）
            rt = AgentRuntime(
                pid="test",
                mode="oneshot",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()
            rt._init_orchestrator(call_llm=mock_llm)

            # 投递输入
            await kernel.input_queues["test"].put(
                UserRequest(text="hello world")
            )

            # oneshot 一轮后自动 FINISHED
            await asyncio.wait_for(rt.run(), timeout=5.0)

            assert rt.state == AgentState.FINISHED
            assert rt.round_count == 1
            assert "Hello" in rt.last_output

        asyncio.run(_test())

    def test_continuous_agent_exits_on_empty_input(self):
        """continuous 模式：收到空输入 → exit。"""

        async def _test():
            container = DIContainer()
            harness = MockHarness(container=container)
            console = MockConsole()
            kernel = Kernel(console)

            async def mock_llm(msgs, tools):
                return Response(text="reply", stop_reason="end_turn")

            rt = AgentRuntime(
                pid="test",
                mode="continuous",
                harness=harness,
                kernel=kernel,
            )
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )
            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()
            rt._init_orchestrator(call_llm=mock_llm)

            # 第一轮正常输入
            await kernel.input_queues["test"].put(
                UserRequest(text="hello")
            )
            # 第二轮空输入 → _should_exit 触发退出
            await kernel.input_queues["test"].put(
                UserRequest(text="")
            )

            await asyncio.wait_for(rt.run(), timeout=5.0)

            assert rt.state == AgentState.FINISHED
            assert rt.round_count == 1  # 第二轮空输入不进 _phase_loop

        asyncio.run(_test())

    def test_kba_receives_from_kernel_queue(self):
        """KBA.receive() 从真实 Kernel input_queue 取消息。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)
            rt = _stub_runtime("test")

            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()

            # 挂载 KBA
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )

            # 投递 UserRequest
            kernel.send_input("test", UserRequest(text="test_input"))

            result = await rt.adapter.receive()
            assert result.text == "test_input"

        asyncio.run(_test())

    def test_kba_send_fallback_to_console(self):
        """KBA.send() TextEvent 降级到 console。"""

        async def _test():
            console = MockConsole()
            kernel = Kernel(console)
            rt = _stub_runtime("test")

            kernel.runtime_table["test"] = rt
            kernel.input_queues["test"] = asyncio.Queue()
            rt.adapter = KernelBridgeAdapter(
                pid="test", kernel=kernel, runtime=rt
            )

            from harness.interfaces.types import TextEvent
            await rt.adapter.send(TextEvent(content="hello from agent"))

            # console 应该收到 AgentOutput
            outputs = [
                e for e in console.events
                if isinstance(e, AgentOutput)
            ]
            assert len(outputs) == 1
            assert outputs[0].pid == "test"
            assert outputs[0].content == "hello from agent"

        asyncio.run(_test())


# ============================================================================
# Helpers
# ============================================================================


def _stub_runtime(pid: str) -> AgentRuntime:
    """创建一个最小 stub AgentRuntime 用于 Kernel 测试。"""
    harness = MockHarness()
    kernel = Kernel(MockConsole())
    runtime = AgentRuntime(
        pid=pid,
        mode="continuous",
        harness=harness,
        kernel=kernel,
    )
    # 绕过 _init_orchestrator 检查直接用原有 kernel
    # 先手动设置状态
    runtime.state = AgentState.RUNNING
    return runtime
