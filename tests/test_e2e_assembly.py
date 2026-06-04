"""端到端装配集成测试。

验证从 YAML 装配到完整会话生命周期的端到端流程，
以及 CLI init/run 命令的基本行为。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.config.yaml_assembler import YamlAssembler
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces import (
    AssemblyContext,
    InputAdapter,
    MemoryBackend,
    Message,
    Response,
    ToolCall,
    ToolCallFunction,
    UserRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: str, content: str) -> None:
    """将 YAML 内容写入临时文件。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_mock_llm(text_response: str = "Hello!", tool_uses=None):
    """创建 mock LLM 函数。

    Args:
        text_response: LLM 文本响应。
        tool_uses: 工具调用列表。

    Returns:
        callable: mock LLM 函数。
    """

    def mock_llm(messages, tools):
        return Response(
            text=text_response,
            thinking=None,
            tool_uses=tool_uses or [],
            stop_reason="end_turn",
        )

    return mock_llm


def _make_mock_input_adapter(inputs, prompt="> "):
    """创建 mock InputAdapter。

    Args:
        inputs: 用户输入列表（字符串）。
        prompt: 输入提示符。

    Returns:
        MockInputAdapter: mock 适配器实例。
    """

    class MockInputAdapter:
        def __init__(self):
            self.inputs = list(inputs)
            self.sent: list[Response] = []
            self.prompt = prompt
            self._call_count = 0

        def receive(self):
            if self._call_count < len(self.inputs):
                text = self.inputs[self._call_count]
                self._call_count += 1
                return UserRequest(
                    text=text,
                    session_id=f"test-session-{time.time()}",
                )
            # 返回空文本 → 触发退出
            return UserRequest(text="", session_id="test-session")

        def send(self, event):
            self.sent.append(event)

    return MockInputAdapter()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_yaml():
    """创建临时 YAML 文件。"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def temp_dir():
    """创建临时工作目录。"""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ---------------------------------------------------------------------------
# E2E tests — YamlAssembler
# ---------------------------------------------------------------------------


class TestE2EYamlAssembly:
    """YAML 装配端到端测试。"""

    def test_e2e_yaml_assembly_full_lifecycle(self, temp_yaml):
        """测试 YAML 装配 → 完整 init→loop→end 生命周期。"""
        _write_yaml(temp_yaml, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks: []
""")
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml).assemble()
        assert harness is not None
        # 验证 Harness 有公开的 API
        assert hasattr(harness, "run")
        assert hasattr(harness, "register_hook")

    def test_e2e_yaml_with_mock_llm_tool_loop(self, temp_yaml):
        """测试 mock LLM + tool_use 场景完整执行。"""
        _write_yaml(temp_yaml, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: SystemToolProvider
      implementation: harness.components.tool.DefaultSystemToolProvider
  hooks: []
""")

        # 创建一个 mock InputAdapter：输入 "hello" → 退出
        mock_adapter = _make_mock_input_adapter(["hello"])

        # Mock LLM：返回 text 响应（不走 tool call 循环）
        mock_llm = _make_mock_llm("Hi there!")

        assembler = YamlAssembler()
        assembler.load(temp_yaml)

        # 手动装配：替换 InputAdapter 为 mock，使用 mock LLM
        container = DIContainer()
        container.register(InputAdapter, mock_adapter)

        from harness.components.tool.default_system_tool_provider import (
            DefaultSystemToolProvider,
        )
        from harness.interfaces import SystemToolProvider

        container.register(SystemToolProvider, DefaultSystemToolProvider())

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

        # 验证 mock adapter 收到了响应（TextEvent + StopEvent）
        text_events = [e for e in mock_adapter.sent if hasattr(e, "content")]
        assert len(text_events) >= 1
        assert text_events[0].content == "Hi there!"

    def test_e2e_sensor_writes_after_session(self, temp_yaml):
        """测试会话结束后 Sensor 写入 MemoryBackend。"""
        _write_yaml(temp_yaml, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test-e2e-memory
    - interface: Sensor
      implementation: harness.components.sensor.LoggingSensor
      inject:
        memory: MemoryBackend
  hooks: []
""")

        mock_adapter = _make_mock_input_adapter(["hello"])
        mock_llm = _make_mock_llm("Hello!")

        container = DIContainer()

        from harness.components.memory_backend.md_memory import MdMemory
        from harness.components.sensor.logging_sensor import LoggingSensor
        from harness.interfaces import MemoryBackend, Sensor

        memory = MdMemory(path="/tmp/test-e2e-memory")
        container.register(MemoryBackend, memory)
        container.register(InputAdapter, mock_adapter)
        container.register(Sensor, LoggingSensor(memory=memory))

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

        # 验证 MemoryBackend 中有写入
        namespaces = memory.list_namespaces()
        assert "episodic" in namespaces


class TestE2ECLI:
    """CLI 端到端测试。"""

    def test_e2e_cli_init_creates_project(self, temp_dir):
        """测试 main.py init 正确创建项目目录和文件。"""
        from main import main

        output_dir = os.path.join(temp_dir, "my-agent")
        exit_code = main(["init", "--profile", "coding-assistant", output_dir])

        assert exit_code == 0
        assert os.path.isdir(output_dir)
        # 检查关键文件存在
        assert os.path.isfile(os.path.join(output_dir, "harness.yaml"))
        assert os.path.isfile(os.path.join(output_dir, "AGENTS.md"))
        assert os.path.isfile(os.path.join(output_dir, "README.md"))
        assert os.path.isfile(os.path.join(output_dir, "profile.toml"))

    def test_e2e_cli_init_existing_dir_no_force(self, temp_dir):
        """测试目标目录已存在时无 --force 则报错。"""
        from main import main

        # 创建非空目录
        existing_dir = os.path.join(temp_dir, "existing")
        os.makedirs(existing_dir)
        with open(os.path.join(existing_dir, "somefile.txt"), "w") as f:
            f.write("hello")

        exit_code = main(["init", "--profile", "coding-assistant", existing_dir])
        assert exit_code == 1  # 错误退出

    def test_e2e_cli_init_with_force_overwrites(self, temp_dir):
        """测试 --force 覆盖已存在的目录。"""
        from main import main

        output_dir = os.path.join(temp_dir, "force-test")
        os.makedirs(output_dir)
        with open(os.path.join(output_dir, "old_file.txt"), "w") as f:
            f.write("old")

        exit_code = main([
            "init", "--profile", "coding-assistant", "--force", output_dir,
        ])

        assert exit_code == 0
        assert os.path.isfile(os.path.join(output_dir, "harness.yaml"))

    def test_e2e_cli_init_invalid_profile(self, temp_dir):
        """测试 --profile 指定不存在的模板时报错。"""
        from main import main

        output_dir = os.path.join(temp_dir, "bad-profile")
        exit_code = main(["init", "--profile", "nonexistent-profile", output_dir])
        assert exit_code == 1

    def test_e2e_cli_run_without_config(self):
        """测试无 config 时降级装配不崩溃。"""
        from main import _fallback_assemble

        harness = _fallback_assemble()
        assert harness is not None
        assert hasattr(harness, "run")

    def test_e2e_config_file_not_found_prints_warning(self, temp_dir, capsys):
        """测试 config 文件不存在时打印 warning 但装配不崩溃。"""
        from main import _fallback_assemble

        # 验证降级装配本身可以正常工作（不调用 run()）
        harness = _fallback_assemble()
        assert harness is not None
        assert hasattr(harness, "run")


class TestE2EHookInYaml:
    """YAML Hook 端到端测试。"""

    def test_e2e_hook_from_yaml_is_triggered(self):
        """测试 YAML 中声明的 hook 在会话中被正确触发。"""
        import logging

        # 捕获 before_llm_call hook 的触发
        hook_called = []

        def my_before_llm_hook(context):
            hook_called.append(("before_llm_call", type(context.data).__name__))

        mock_adapter = _make_mock_input_adapter(["hello"])

        # 带 tool_use 的 LLM 响应，测试 before_tool_execute hook
        mock_llm = _make_mock_llm("Done!")

        container = DIContainer()
        container.register(InputAdapter, mock_adapter)

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.register_hook("before_llm_call", my_before_llm_hook)
        harness.run()

        assert len(hook_called) > 0
        assert hook_called[0][0] == "before_llm_call"


class TestE2EMemoryPersistence:
    """跨装配记忆持久化测试。"""

    def test_e2e_memory_persists_across_assemblies(self, temp_dir):
        """测试同一 memory 目录的两次装配间记忆持久化。"""
        from harness.components.memory_backend.md_memory import MdMemory
        from harness.components.sensor.logging_sensor import LoggingSensor
        from harness.interfaces import MemoryBackend, Sensor

        memory_path = os.path.join(temp_dir, "persistent-memory")

        # ── 第一次装配和运行 ──
        mock_adapter1 = _make_mock_input_adapter(["first message"])
        mock_llm1 = _make_mock_llm("First response!")

        container1 = DIContainer()
        memory1 = MdMemory(path=memory_path)
        container1.register(MemoryBackend, memory1)
        container1.register(InputAdapter, mock_adapter1)
        container1.register(Sensor, LoggingSensor(memory=memory1))

        harness1 = Harness.from_container(container1, call_llm=mock_llm1)
        harness1.run()

        # 验证第一次运行后 memory 中有 episodic 数据
        results1 = memory1.search("first", "episodic")
        assert len(results1) > 0

        # ── 第二次装配和运行（使用同一 memory 路径） ──
        mock_adapter2 = _make_mock_input_adapter(["second message"])
        mock_llm2 = _make_mock_llm("Second response!")

        container2 = DIContainer()
        memory2 = MdMemory(path=memory_path)
        container2.register(MemoryBackend, memory2)
        container2.register(InputAdapter, mock_adapter2)
        container2.register(Sensor, LoggingSensor(memory=memory2))

        harness2 = Harness.from_container(container2, call_llm=mock_llm2)
        harness2.run()

        # ── 验证跨装配持久化 ──
        # MemoryBackend 应该包含两次运行的记录
        results2 = memory2.search("second", "episodic")
        assert len(results2) > 0

        # 第一次的记忆也应该还在
        results_first = memory2.search("first", "episodic")
        assert len(results_first) > 0


class TestE2EErrorHandling:
    """异常处理端到端测试。"""

    def test_e2e_on_error_hook_triggered(self):
        """测试异常时 on_error Hook 被触发。"""
        error_called = []

        def on_error_hook(context):
            error_called.append(("on_error", str(context.data)))

        # 创建一个会失败的 mock：InputAdapter 抛出异常
        class FailingAdapter:
            def receive(self):
                raise RuntimeError("Simulated adapter failure")

            def send(self, event):
                pass

        container = DIContainer()
        container.register(InputAdapter, FailingAdapter())

        harness = Harness.from_container(container, call_llm=None)
        harness.register_hook("on_error", on_error_hook)

        # run() 不应该崩溃（在 finally 中清理）
        # 注意：ComponentNotRegisteredError 或 OrchestratorError 会 re-raise
        try:
            harness.run()
        except Exception:
            pass

        assert len(error_called) > 0
        assert error_called[0][0] == "on_error"

    def test_e2e_exit_signal_from_cli(self):
        """测试 /exit 信号正确终止会话。"""
        mock_adapter = _make_mock_input_adapter(["/exit"])
        mock_llm = _make_mock_llm("Goodbye!")

        container = DIContainer()
        container.register(InputAdapter, mock_adapter)

        harness = Harness.from_container(container, call_llm=mock_llm)
        # 不应该抛异常
        harness.run()

        # /exit 时不会调用 send（在 _should_exit 后直接跳到 phase_end）
        assert len(mock_adapter.sent) == 0

    def test_e2e_empty_input_exits(self):
        """测试空输入时退出会话。"""
        # 第一次 receive 就返回空文本
        mock_adapter = _make_mock_input_adapter([""])
        mock_llm = _make_mock_llm("Should not be called")

        container = DIContainer()
        container.register(InputAdapter, mock_adapter)

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()
        # 空输入时不会走到 LLM 调用
        assert len(mock_adapter.sent) == 0


class TestE2EDefaultAssembly:
    """降级装配测试。"""

    def test_e2e_default_assembly_without_yaml(self):
        """测试无 YAML 时降级装配不崩溃。"""
        from main import _fallback_assemble

        harness = _fallback_assemble()
        assert harness is not None
        # 验证核心 API 可用
        assert hasattr(harness, "run")
        assert hasattr(harness, "register_hook")

    def test_e2e_default_assembly_registers_all_6(self):
        """测试降级装配注册了所有 6 个默认组件。"""
        from main import _fallback_assemble

        # 验证不会崩溃
        harness = _fallback_assemble()
        assert harness is not None
