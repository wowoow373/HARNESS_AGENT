# Batch 2: Workflow 脚本加载 + Runtime 管理 Tool 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现多 agent workflow 脚本加载（@agent / subscribe 装饰器 + Kernel.spawn_from_script）+ 5 个 Runtime 管理 Tool 注册到 SystemToolProvider

**Architecture:** 新增 `decorators.py`（模块级 registry + @agent/subscribe）+ `tools.py`（CompositeSystemToolProvider + 5 个 BaseTool 子类），修改 `kernel.py`（spawn_from_script + _inject_runtime_tools），前置修复 `Harness` 类缺少 `.container`/`.call_llm` 属性

**Tech Stack:** Python 3.10+, asyncio, pytest, importlib

---

### Task 0: 前置修复 — Harness 添加透传属性

**Files:**
- Modify: `harness/di.py`

**Why:** `AgentRuntime._init_orchestrator()` 和 `Runtime._run_async()` 已通过 `harness.container` / `harness.call_llm` 访问属性，但 `Harness` 类未暴露这两个属性。`LifecycleOrchestrator` 在 `__init__` 中将它们保存为 `self.container` 和 `self.call_llm`，`Harness` 只需透传。

- [ ] **Step 1: 添加 container 和 call_llm property**

```python
# harness/di.py — Harness 类，在 register_hook 方法之前插入:

    @property
    def container(self) -> DIContainer:
        """透传内部 LifecycleOrchestrator 的 DI 容器。"""
        return self._orchestrator.container

    @property
    def call_llm(self):
        """透传内部 LifecycleOrchestrator 的 call_llm。"""
        return self._orchestrator.call_llm
```

- [ ] **Step 2: 运行现有测试确认不退化**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add harness/di.py
git commit -m "fix: add Harness.container and Harness.call_llm passthrough properties

Batch 1 code in agent_runtime.py and runtime.py accesses harness.container
and harness.call_llm, but Harness never exposed these — it delegates
everything to LifecycleOrchestrator which stores them as self.container
and self.call_llm.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1: 创建 decorators.py — @agent + subscribe + registry

**Files:**
- Create: `harness/runtime/decorators.py`
- Create: `tests/runtime/test_decorators.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/runtime/test_decorators.py

"""Tests for harness.runtime.decorators — @agent / subscribe / registry."""

import sys
import tempfile
import os
import pytest
from harness.runtime.decorators import (
    _agent_registry,
    _subscription_registry,
    SubRecord,
    agent,
    subscribe,
)


class TestAgentDecorator:
    """@agent 装饰器测试。"""

    def test_registers_factory_in_registry(self):
        """@agent 将 factory 注册到 _agent_registry。"""
        _agent_registry.clear()

        @agent("test_agent", entry_prompt="do something")
        def make_harness():
            return "fake_harness"

        assert "test_agent" in _agent_registry
        bp = _agent_registry["test_agent"]
        assert bp["name"] == "test_agent"
        assert bp["entry_prompt"] == "do something"
        assert bp["metadata"] == {}
        assert callable(bp["factory"])
        assert bp["factory"]() == "fake_harness"

    def test_registers_metadata(self):
        """@agent 传递 metadata 时正确存储。"""
        _agent_registry.clear()

        @agent("worker", entry_prompt="work", metadata={"desc": "a worker"})
        def make_harness():
            return "h"

        assert _agent_registry["worker"]["metadata"] == {"desc": "a worker"}

    def test_duplicate_name_raises(self):
        """同一 name 重复注册 → ValueError。"""
        _agent_registry.clear()

        @agent("dup", entry_prompt="first")
        def factory1():
            return "h1"

        with pytest.raises(ValueError, match="already registered"):
            @agent("dup", entry_prompt="second")
            def factory2():
                return "h2"

    def test_factory_preserved_as_callable(self):
        """factory 保持为可调用对象，未被修改。"""
        _agent_registry.clear()

        @agent("a", entry_prompt="go")
        def my_factory():
            return object()

        assert _agent_registry["a"]["factory"] is my_factory


class TestSubscribe:
    """subscribe() 函数测试。"""

    def test_adds_sub_record(self):
        """subscribe("A").to("B") 追加 SubRecord 到 _subscription_registry。"""
        _subscription_registry.clear()

        subscribe("analyzer").to("collector")

        assert len(_subscription_registry) == 1
        rec = _subscription_registry[0]
        assert rec.subscriber == "analyzer"
        assert rec.publisher == "collector"

    def test_multiple_calls_accumulate(self):
        """多次 subscribe 累加，不覆盖。"""
        _subscription_registry.clear()

        subscribe("A").to("B")
        subscribe("A").to("C")

        assert len(_subscription_registry) == 2

    def test_self_subscription_raises(self):
        """自订阅 → ValueError。"""
        with pytest.raises(ValueError, match="Self-subscription"):
            subscribe("X").to("X")


class TestRegistryIsolation:
    """registry 清空 + importlib 加载隔离。"""

    def test_clear_empties_both_registries(self):
        """clear() 清空两个 registry。"""
        _agent_registry["x"] = {}
        _subscription_registry.append(SubRecord("a", "b"))

        _agent_registry.clear()
        _subscription_registry.clear()

        assert len(_agent_registry) == 0
        assert len(_subscription_registry) == 0

    def test_importlib_load_fills_registry(self):
        """importlib 加载含 @agent 的脚本后 registry 被填充。"""
        _agent_registry.clear()
        _subscription_registry.clear()

        script = '''
from harness.runtime.decorators import agent, subscribe

@agent("loader_test", entry_prompt="load")
def make_harness():
    return "loaded"
'''
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False
        ) as f:
            f.write(script)
            path = f.name

        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_test_script", path
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_test_script"] = module
            spec.loader.exec_module(module)

            assert "loader_test" in _agent_registry
            assert _agent_registry["loader_test"]["entry_prompt"] == "load"
        finally:
            os.unlink(path)
            sys.modules.pop("_test_script", None)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/runtime/test_decorators.py -v 2>&1 | tail -20
```

Expected: ImportError — `harness.runtime.decorators` 模块不存在。

- [ ] **Step 3: 写最小实现**

```python
# harness/runtime/decorators.py

"""@agent 装饰器 + subscribe() 函数 + 模块级 registry。

Kernel.spawn_from_script 在加载脚本前清空这两个 registry，
用 importlib 加载脚本后读取 registry 创建 AgentRuntime。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# ── 模块级 registry ──
# 由 Kernel.spawn_from_script 在加载脚本前清空。
# asyncio 单线程模型下模块级全局变量安全（无真正并发）。

_agent_registry: dict[str, dict] = {}
"""agent 注册表。key = agent name (pid), value = Blueprint:
    {"name": str, "entry_prompt": str, "metadata": dict, "factory": Callable}
"""

_subscription_registry: list[SubRecord] = []
"""订阅声明列表。"""


@dataclass
class SubRecord:
    """一条订阅声明。

    Attributes:
        subscriber: 订阅者 agent name。
        publisher: 发布者 agent name。
    """
    subscriber: str
    publisher: str


# ── @agent 装饰器 ──

def agent(name: str, entry_prompt: str, metadata: dict | None = None):
    """声明一个 agent 及其装配逻辑。

    Args:
        name: agent 的 pid，在本次 spawn 内唯一。
        entry_prompt: 必需。agent 的第一条 UserRequest.text。
        metadata: 可选。透传给父 agent LLM，出现在 spawn_workflow
                  返回值的 agents[].metadata 中。

    Returns:
        decorator: 接受 factory 函数的装饰器。

    Raises:
        ValueError: name 已在 _agent_registry 中。
    """
    def decorator(factory: Callable):
        if name in _agent_registry:
            raise ValueError(
                f"Agent '{name}' already registered. "
                f"Each @agent name must be unique within a workflow script."
            )
        _agent_registry[name] = {
            "name": name,
            "entry_prompt": entry_prompt,
            "metadata": metadata or {},
            "factory": factory,
        }
        return factory
    return decorator


# ── subscribe() ──

class _SubscribeBuilder:
    """subscribe("A").to("B") 的中间构建器。"""

    def __init__(self, subscriber: str):
        self._subscriber = subscriber

    def to(self, publisher: str) -> None:
        """完成订阅声明。

        Raises:
            ValueError: subscriber == publisher。
        """
        if self._subscriber == publisher:
            raise ValueError(
                f"Self-subscription not allowed: "
                f"'{self._subscriber}' cannot subscribe to itself."
            )
        _subscription_registry.append(
            SubRecord(subscriber=self._subscriber, publisher=publisher)
        )


def subscribe(subscriber: str) -> _SubscribeBuilder:
    """声明一个订阅关系。

    用法:
        subscribe("analyzer").to("collector")

    Args:
        subscriber: 订阅者 agent name。

    Returns:
        _SubscribeBuilder: 调用 .to(publisher) 完成声明。
    """
    return _SubscribeBuilder(subscriber)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/runtime/test_decorators.py -v 2>&1
```

Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/decorators.py tests/runtime/test_decorators.py
git commit -m "feat: add @agent / subscribe decorators with module-level registry

- @agent(name, entry_prompt, metadata?) registers factory in _agent_registry
- subscribe(subscriber).to(publisher) appends SubRecord to _subscription_registry
- Both registries cleared by Kernel.spawn_from_script before script loading
- Duplicate @agent name and self-subscription raise ValueError

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 创建 tools.py — CompositeSystemToolProvider + 5 个 Runtime Tool

**Files:**
- Create: `harness/runtime/tools.py`
- Create: `tests/runtime/test_tools.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/runtime/test_tools.py

"""Tests for harness.runtime.tools — CompositeSystemToolProvider + 5 Runtime Tools."""

import json
import pytest
from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool
from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider

from harness.runtime.tools import (
    CompositeSystemToolProvider,
    SpawnWorkflowTool,
    EndWorkflowTool,
    FinishAgentTool,
    TalkToTool,
    ListAgentsTool,
    create_runtime_tools,
)


# ── Helpers ──

class _DummyTool(BaseTool):
    """用于测试 CompositeSystemToolProvider 的用户 tool。"""
    def get_definition(self):
        return ToolDefinition(
            name="dummy",
            description="A dummy tool",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, args):
        return ToolResult(success=True, content="dummy_result")


class _FakeKernel:
    """Mock Kernel — 仅用于 Tool 单元测试，最小接口。"""
    def __init__(self):
        self.runtime_table = {}
        self.workflow_table = {}
        self._spawn_counter = 0
        self._called_spawn_from_script = None
        self._called_end_workflow = None
        self._called_finish_agent = None
        self._called_send_input = None
        self._called_list_agents = None

    def spawn_from_script(self, script_path, parent=None):
        self._called_spawn_from_script = (script_path, parent)
        self._spawn_counter += 1
        return {
            "workflow_flag": f"wf_{self._spawn_counter:03d}",
            "agents": [
                {"pid": "collector", "parent": None, "metadata": {}},
            ],
        }

    def end_workflow(self, flag):
        self._called_end_workflow = flag
        return ["collector"]

    def finish_agent(self, pid):
        self._called_finish_agent = pid

    def send_input(self, pid, request):
        self._called_send_input = (pid, request)

    def list_agents(self):
        self._called_list_agents = True
        return {"root": {"state": "running", "mode": "continuous"}}


# ── CompositeSystemToolProvider ──

class TestCompositeSystemToolProvider:
    """CompositeSystemToolProvider 单元测试。"""

    def test_get_tools_merges_user_and_runtime(self):
        """get_tools() 合并 user tools + runtime tools。"""
        rt = _DummyTool()  # name="dummy"
        rt_def = rt.get_definition()
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[_DummyTool()],
        )

        tools = composite.get_tools()

        # DefaultSystemToolProvider 自带 read_file/write_file/shell
        assert len(tools) >= 4  # 3 builtins + runtime tool
        # Runtime tool 应在列表末尾
        assert tools[-1].name == "dummy"

    def test_execute_runtime_tool_first(self):
        """execute() 先查 runtime tool。"""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[_DummyTool()],
        )

        result = composite.execute("dummy", {})

        assert result.success is True
        assert result.content == "dummy_result"

    def test_execute_falls_back_to_user_provider(self):
        """不在 runtime 中的 tool 从 user provider 执行。"""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[],
        )

        result = composite.execute("read_file", {"file_path": "/tmp/test"})

        # read_file 是 DefaultSystemToolProvider 的内置工具
        assert result.success is False  # 文件不存在
        assert "File not found" in result.error

    def test_execute_raises_keyerror_when_not_found(self):
        """Runtime 和 user provider 都没有 → KeyError。"""
        composite = CompositeSystemToolProvider(
            user_provider=DefaultSystemToolProvider(),
            runtime_tools=[],
        )

        with pytest.raises(KeyError, match="nonexistent"):
            composite.execute("nonexistent", {})

    def test_no_user_provider_creates_default(self):
        """不传 user_provider → 自动使用 DefaultSystemToolProvider。"""
        composite = CompositeSystemToolProvider()

        tools = composite.get_tools()

        assert len(tools) >= 3  # DefaultSystemToolProvider builtins

    def test_empty_runtime_tools(self):
        """空 runtime_tools → 仅 user tools。"""
        composite = CompositeSystemToolProvider(
            user_provider=_DummyTool(),
            runtime_tools=[],
        )

        tools = composite.get_tools()

        assert len(tools) == 1  # only _DummyTool (as user_provider)
        assert tools[0].name == "dummy"

    def test_runtime_tool_priority_over_user_tool(self):
        """同名时 Runtime tool 覆盖 user tool 的 execute。"""
        user_tool = _DummyTool()
        rt_tool = _DummyTool()  # same name "dummy"

        class _OverrideTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(
                    name="dummy",
                    description="overrides",
                    parameters={"type": "object", "properties": {}, "required": []},
                )
            def execute(self, args):
                return ToolResult(success=True, content="runtime_wins")

        composite = CompositeSystemToolProvider(
            user_provider=user_tool,
            runtime_tools=[_OverrideTool()],
        )

        result = composite.execute("dummy", {})
        assert result.content == "runtime_wins"


# ── Runtime Tools ──

class TestSpawnWorkflowTool:
    """SpawnWorkflowTool 测试。"""

    def test_get_definition(self):
        kernel = _FakeKernel()
        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")

        d = tool.get_definition()
        assert d.name == "spawn_workflow"
        assert "script_path" in str(d.parameters)

    def test_execute_success(self):
        kernel = _FakeKernel()
        kernel.runtime_table["root"] = object()  # dummy parent
        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")

        result = tool.execute({"script_path": "wf.py"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["workflow_flag"] == "wf_001"
        assert len(data["agents"]) == 1
        assert kernel._called_spawn_from_script is not None

    def test_execute_failure(self):
        kernel = _FakeKernel()
        kernel.runtime_table["root"] = object()

        def failing_spawn(path, parent=None):
            raise FileNotFoundError("no such file")
        kernel.spawn_from_script = failing_spawn

        tool = SpawnWorkflowTool(kernel=kernel, parent_pid="root")
        result = tool.execute({"script_path": "nonexistent.py"})

        assert result.success is False
        assert "FileNotFoundError" in result.error


class TestEndWorkflowTool:
    """EndWorkflowTool 测试。"""

    def test_get_definition(self):
        tool = EndWorkflowTool(kernel=_FakeKernel())
        d = tool.get_definition()
        assert d.name == "end_workflow"
        assert "flag" in d.parameters["required"]

    def test_execute_success(self):
        kernel = _FakeKernel()
        tool = EndWorkflowTool(kernel=kernel)

        result = tool.execute({"flag": "wf_001"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["ok"] is True
        assert "collector" in data["killed"]
        assert kernel._called_end_workflow == "wf_001"


class TestFinishAgentTool:
    """FinishAgentTool 测试。"""

    def test_get_definition(self):
        tool = FinishAgentTool(kernel=_FakeKernel(), pid="worker")
        d = tool.get_definition()
        assert d.name == "finish_agent"

    def test_execute_calls_finish_agent_with_pid(self):
        kernel = _FakeKernel()
        tool = FinishAgentTool(kernel=kernel, pid="worker")

        result = tool.execute({})

        assert result.success is True
        assert kernel._called_finish_agent == "worker"


class TestTalkToTool:
    """TalkToTool 测试。"""

    def test_get_definition(self):
        tool = TalkToTool(kernel=_FakeKernel(), from_pid="root")
        d = tool.get_definition()
        assert d.name == "talk_to"
        assert "pid" in d.parameters["required"]
        assert "text" in d.parameters["required"]

    def test_execute_sends_input(self):
        kernel = _FakeKernel()
        tool = TalkToTool(kernel=kernel, from_pid="root")

        result = tool.execute({"pid": "collector", "text": "hello"})

        assert result.success is True
        data = json.loads(result.content)
        assert data["target"] == "collector"
        assert kernel._called_send_input is not None
        pid, req = kernel._called_send_input
        assert pid == "collector"
        assert req.text == "hello"
        assert req.metadata["from"] == "root"
        assert req.metadata["type"] == "talk_to"


class TestListAgentsTool:
    """ListAgentsTool 测试。"""

    def test_get_definition(self):
        tool = ListAgentsTool(kernel=_FakeKernel())
        d = tool.get_definition()
        assert d.name == "list_agents"

    def test_execute_returns_agent_list(self):
        kernel = _FakeKernel()
        tool = ListAgentsTool(kernel=kernel)

        result = tool.execute({})

        assert result.success is True
        data = json.loads(result.content)
        assert "root" in data["agents"]
        assert kernel._called_list_agents is True


class TestCreateRuntimeTools:
    """create_runtime_tools 工厂函数测试。"""

    def test_returns_five_tools(self):
        kernel = _FakeKernel()
        tools = create_runtime_tools(kernel=kernel, pid="root")

        assert len(tools) == 5
        names = [t.get_definition().name for t in tools]
        assert names == [
            "spawn_workflow",
            "end_workflow",
            "finish_agent",
            "talk_to",
            "list_agents",
        ]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/runtime/test_tools.py -v 2>&1 | tail -20
```

Expected: ImportError — `harness.runtime.tools` 模块不存在。

- [ ] **Step 3: 写最小实现**

```python
# harness/runtime/tools.py

"""Runtime 管理 Tool 集。

提供 CompositeSystemToolProvider（包装用户 SystemToolProvider）和
5 个 Runtime 管理 Tool: spawn_workflow / end_workflow / finish_agent /
talk_to / list_agents。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..components.tool.base import BaseTool
from ..components.tool.default_system_tool_provider import DefaultSystemToolProvider
from ..interfaces.types import ToolDefinition, ToolResult, UserRequest

if TYPE_CHECKING:
    from .kernel import Kernel

logger = logging.getLogger(__name__)


# ── CompositeSystemToolProvider ──────────────────────────────────────────


class CompositeSystemToolProvider:
    """组合式 SystemToolProvider。

    将用户原有的 SystemToolProvider 与 Runtime 管理 Tool 合并。
    Runtime tool 优先级高于用户 tool。
    """

    def __init__(
        self,
        user_provider: Optional[object] = None,
        runtime_tools: Optional[List[BaseTool]] = None,
    ):
        if user_provider is None:
            user_provider = DefaultSystemToolProvider()
        self._user = user_provider

        self._runtime_tools: Dict[str, BaseTool] = {}
        if runtime_tools:
            for tool in runtime_tools:
                self._register_runtime_tool(tool)

    # ── SystemToolProvider 协议 ──

    def get_tools(self) -> List[ToolDefinition]:
        user_defs = self._user.get_tools() if self._user else []
        runtime_defs = [t.get_definition() for t in self._runtime_tools.values()]
        return user_defs + runtime_defs

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        if name in self._runtime_tools:
            return self._runtime_tools[name].execute(args)

        if self._user:
            try:
                return self._user.execute(name, args)
            except KeyError:
                pass

        raise KeyError(
            f"Tool '{name}' not found in CompositeSystemToolProvider"
        )

    # ── 内部 ──

    def _register_runtime_tool(self, tool: BaseTool) -> None:
        name = tool.get_definition().name
        if name in self._runtime_tools:
            logger.warning(
                f"Runtime tool '{name}' already registered, overwriting"
            )
        self._runtime_tools[name] = tool

    @property
    def tool_count(self) -> int:
        return len(self._runtime_tools)

    def has_runtime_tool(self, name: str) -> bool:
        return name in self._runtime_tools


# ── Runtime Tools ────────────────────────────────────────────────────────


class SpawnWorkflowTool(BaseTool):
    """加载 workflow 脚本，创建子 agent 并启动。"""

    def __init__(self, kernel: Kernel, parent_pid: str):
        self._kernel = kernel
        self._parent_pid = parent_pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="spawn_workflow",
            description=(
                "从 Python 脚本文件加载一个 workflow，创建其中声明的所有 agent 并启动执行。"
                "返回 workflow_flag（用于后续 end_workflow）和 agent 列表（含 pid 和 metadata）。"
                "子 agent 创建后立即开始执行 entry_prompt，无需手动启动。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": (
                            "workflow 脚本的绝对路径（.py 文件）。"
                            "脚本中通过 @agent 装饰器声明 agent 装配逻辑。"
                        ),
                    },
                },
                "required": ["script_path"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        script_path = args["script_path"]
        try:
            parent = self._kernel.runtime_table.get(self._parent_pid)
            result = self._kernel.spawn_from_script(script_path, parent=parent)
            return ToolResult(
                success=True,
                content=json.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            logger.error(f"spawn_workflow failed: {e}")
            return ToolResult(
                success=False,
                content="",
                error=f"{type(e).__name__}: {e}",
            )


class EndWorkflowTool(BaseTool):
    """终止整个 workflow 的所有 agent。"""

    def __init__(self, kernel: Kernel):
        self._kernel = kernel

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="end_workflow",
            description=(
                "终止指定 workflow 中的所有 agent。"
                "所有 agent 收到退出信号后会走正常清理流程（_phase_end）后结束。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "flag": {
                        "type": "string",
                        "description": "workflow 标识。spawn_workflow 返回的 workflow_flag。",
                    },
                },
                "required": ["flag"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        flag = args["flag"]
        killed = self._kernel.end_workflow(flag)
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True, "killed": killed}),
        )


class FinishAgentTool(BaseTool):
    """当前 agent 自我终止。"""

    def __init__(self, kernel: Kernel, pid: str):
        self._kernel = kernel
        self._pid = pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="finish_agent",
            description=(
                "标记当前 agent 的任务完成，触发正常退出流程。"
                "仅在当前 agent 确定所有工作已完成时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        self._kernel.finish_agent(self._pid)
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True}),
        )


class TalkToTool(BaseTool):
    """向指定 agent 发送定向消息。"""

    def __init__(self, kernel: Kernel, from_pid: str):
        self._kernel = kernel
        self._from_pid = from_pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="talk_to",
            description=(
                "向指定 agent 发送一条定向消息。"
                "消息直接投递到目标 agent 的输入队列，不走订阅路由。"
                "目标 agent 在下一轮对话中可以看到这条消息。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "目标 agent 的 pid。spawn_workflow 返回的 agents 列表中获取。",
                    },
                    "text": {
                        "type": "string",
                        "description": "要发送的消息内容。",
                    },
                },
                "required": ["pid", "text"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        target_pid = args["pid"]
        text = args["text"]
        self._kernel.send_input(
            target_pid,
            UserRequest(
                text=text,
                metadata={"from": self._from_pid, "type": "talk_to"},
            ),
        )
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True, "target": target_pid}),
        )


class ListAgentsTool(BaseTool):
    """列出 Kernel 中所有 agent 的当前状态。"""

    def __init__(self, kernel: Kernel):
        self._kernel = kernel

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_agents",
            description=(
                "列出当前 Runtime 中所有 agent 的状态信息，"
                "包括 pid、state、mode、parent、已执行轮数、error。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        agents = self._kernel.list_agents()
        return ToolResult(
            success=True,
            content=json.dumps({"agents": agents}, ensure_ascii=False),
        )


# ── 工厂函数 ─────────────────────────────────────────────────────────────


def create_runtime_tools(kernel: Kernel, pid: str) -> list[BaseTool]:
    """创建当前 agent 需要的 Runtime 管理 Tool 列表。

    Args:
        kernel: Kernel 全局单例引用。
        pid: 当前 agent 的 pid。

    Returns:
        5 个 Runtime tool 实例。
    """
    return [
        SpawnWorkflowTool(kernel=kernel, parent_pid=pid),
        EndWorkflowTool(kernel=kernel),
        FinishAgentTool(kernel=kernel, pid=pid),
        TalkToTool(kernel=kernel, from_pid=pid),
        ListAgentsTool(kernel=kernel),
    ]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/runtime/test_tools.py -v 2>&1
```

Expected: all 18 tests pass.

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/tools.py tests/runtime/test_tools.py
git commit -m "feat: add CompositeSystemToolProvider + 5 Runtime management tools

- CompositeSystemToolProvider wraps user's SystemToolProvider + runtime tools
- SpawnWorkflowTool: loads workflow script, creates child agents
- EndWorkflowTool: terminates all agents in a workflow
- FinishAgentTool: agent self-termination
- TalkToTool: direct message to target agent via send_input
- ListAgentsTool: lists all agents' state snapshot
- create_runtime_tools(kernel, pid) factory function

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 更新 kernel.py — spawn_from_script + _inject_runtime_tools + end_workflow 返回值

**Files:**
- Modify: `harness/runtime/kernel.py`
- Create: `tests/runtime/test_spawn_from_script.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/runtime/test_spawn_from_script.py

"""Tests for Kernel.spawn_from_script() and _inject_runtime_tools()."""

import asyncio
import os
import sys
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.system_tool_provider import SystemToolProvider
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentState
from harness.runtime.decorators import _agent_registry, _subscription_registry
from harness.runtime.tools import CompositeSystemToolProvider


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole — 记录 send 调用。"""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


def _make_minimal_harness(call_llm=None):
    """创建一个最小 Harness 实例（用于 @agent factory）。"""
    container = DIContainer()
    return Harness.from_container(container, call_llm=call_llm)


def _write_workflow_script(agent_count=2, with_subscribe=False):
    """创建临时 workflow 脚本，返回路径。"""
    if agent_count >= 1:
        agents_block = """
@agent("collector", entry_prompt="采集数据")
def assemble_collector():
    container = DIContainer()
    return Harness.from_container(container, call_llm=None)
"""
    else:
        agents_block = ""

    if agent_count >= 2:
        agents_block += """
@agent("analyzer", entry_prompt="分析数据", metadata={"desc": "analyzer"})
def assemble_analyzer():
    container = DIContainer()
    return Harness.from_container(container, call_llm=None)
"""

    subscribe_block = ""
    if with_subscribe and agent_count >= 2:
        subscribe_block = '\nsubscribe("analyzer").to("collector")\n'

    content = f"""from harness.core.container import DIContainer
from harness.di import Harness
from harness.runtime.decorators import agent, subscribe
{agents_block}
{subscribe_block}
"""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(content)
        path = f.name
    return path


# ── _inject_runtime_tools ──

class TestInjectRuntimeTools:
    """Kernel._inject_runtime_tools 测试。"""

    def test_injects_composite_provider(self):
        """注入后 SystemToolProvider 为 CompositeSystemToolProvider。"""
        kernel = Kernel(_MockConsole())
        container = DIContainer()

        kernel._inject_runtime_tools(container, pid="test")

        provider = container.resolve(SystemToolProvider)
        assert isinstance(provider, CompositeSystemToolProvider)

    def test_preserves_user_tools(self):
        """用户原有工具保留在 Composite 中。"""
        from harness.components.tool.base import BaseTool
        from harness.interfaces.types import ToolDefinition, ToolResult

        class _UserTool(BaseTool):
            def get_definition(self):
                return ToolDefinition(
                    name="user_tool",
                    description="u",
                    parameters={"type": "object", "properties": {}, "required": []},
                )
            def execute(self, args):
                return ToolResult(success=True, content="user")

        from harness.components.tool.default_system_tool_provider import DefaultSystemToolProvider
        user_provider = DefaultSystemToolProvider(extra_tools=[_UserTool()])
        container = DIContainer()
        container.register(SystemToolProvider, user_provider)

        kernel = Kernel(_MockConsole())
        kernel._inject_runtime_tools(container, pid="test")

        provider = container.resolve(SystemToolProvider)
        tools = provider.get_tools()
        tool_names = [t.name for t in tools]
        assert "user_tool" in tool_names
        assert "spawn_workflow" in tool_names

    def test_runtime_tools_are_executable(self):
        """注入的 Runtime tool 可执行。"""
        kernel = Kernel(_MockConsole())
        container = DIContainer()

        kernel._inject_runtime_tools(container, pid="test")
        provider = container.resolve(SystemToolProvider)

        result = provider.execute("list_agents", {})
        assert result.success is True


# ── spawn_from_script ──

class TestSpawnFromScript:
    """Kernel.spawn_from_script 集成测试。"""

    def test_creates_agents_in_runtime_table(self):
        """spawn_from_script 将 agent 注册到 runtime_table。"""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            assert "collector" in kernel.runtime_table
            assert "analyzer" in kernel.runtime_table
            assert result["workflow_flag"].startswith("wf_")
            assert len(result["agents"]) == 2
        finally:
            os.unlink(path)

    def test_returns_correct_agent_metadata(self):
        """返回值中每个 agent 含 pid / parent / metadata。"""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            agent_map = {a["pid"]: a for a in result["agents"]}
            assert agent_map["collector"]["parent"] is None
            assert agent_map["analyzer"]["metadata"]["desc"] == "analyzer"
        finally:
            os.unlink(path)

    def test_oneshot_mode_when_no_subscribe(self):
        """无 subscribe → oneshot。"""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            rt = kernel.runtime_table["collector"]
            assert rt.mode == "oneshot"
        finally:
            os.unlink(path)

    def test_continuous_mode_when_subscriber(self):
        """有 subscribe → continuous。"""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert kernel.runtime_table["analyzer"].mode == "continuous"
        finally:
            os.unlink(path)

    def test_continuous_mode_when_publisher(self):
        """作为 publisher 被订阅 → continuous。"""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert kernel.runtime_table["collector"].mode == "continuous"
        finally:
            os.unlink(path)

    def test_entry_prompt_delivered(self):
        """entry_prompt 投递到 agent 的 input_queue。"""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            # 从 input_queue 取出 entry_prompt
            msg = kernel.input_queues["collector"].get_nowait()
            assert msg.text == "采集数据"
            assert msg.metadata["workflow_flag"].startswith("wf_")
        finally:
            os.unlink(path)

    def test_workflow_table_recorded(self):
        """workflow_table 记录 workflow_flag → pid 列表。"""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            result = kernel.spawn_from_script(path)

            flag = result["workflow_flag"]
            assert flag in kernel.workflow_table
            assert set(kernel.workflow_table[flag]) == {"collector", "analyzer"}
        finally:
            os.unlink(path)

    def test_subscriptions_stashed(self):
        """subscribe 关系暂存到 _pending_subscriptions。"""
        path = _write_workflow_script(agent_count=2, with_subscribe=True)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert ("analyzer", "collector") in kernel._pending_subscriptions
        finally:
            os.unlink(path)

    def test_input_queues_created(self):
        """每个 agent 都有 input_queue。"""
        path = _write_workflow_script(agent_count=2)
        try:
            kernel = Kernel(_MockConsole())
            kernel.spawn_from_script(path)

            assert "collector" in kernel.input_queues
            assert isinstance(kernel.input_queues["collector"], asyncio.Queue)
        finally:
            os.unlink(path)

    def test_no_agent_declarations_raises(self):
        """无 @agent 声明 → ValueError。"""
        path = _write_workflow_script(agent_count=0)
        try:
            kernel = Kernel(_MockConsole())
            with pytest.raises(ValueError, match="No @agent declarations"):
                kernel.spawn_from_script(path)
        finally:
            os.unlink(path)

    def test_invalid_subscribe_reference_raises(self):
        """subscribe 引用未知 agent → ValueError。"""
        content = """from harness.runtime.decorators import agent, subscribe
from harness.core.container import DIContainer
from harness.di import Harness

@agent("only_one", entry_prompt="go")
def make():
    return Harness.from_container(DIContainer(), call_llm=None)

subscribe("unknown").to("only_one")
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False
        ) as f:
            f.write(content)
            path = f.name

        try:
            kernel = Kernel(_MockConsole())
            with pytest.raises(ValueError, match="subscribe.*unknown"):
                kernel.spawn_from_script(path)
        finally:
            os.unlink(path)

    def test_spawn_counter_increments(self):
        """每次 spawn 后 _spawn_counter 递增。"""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            assert kernel._spawn_counter == 0

            kernel.spawn_from_script(path)
            assert kernel._spawn_counter == 1
            assert kernel.workflow_table["wf_001"] == ["collector"]

            kernel.spawn_from_script(path)
            assert kernel._spawn_counter == 2
            assert kernel.workflow_table["wf_002"] == ["collector"]
        finally:
            os.unlink(path)

    def test_parent_children_recorded(self):
        """spawn_from_script with parent → parent.children 含子 pid。"""
        path = _write_workflow_script(agent_count=1)
        try:
            kernel = Kernel(_MockConsole())
            # 先创建一个 parent agent
            from harness.runtime.agent_runtime import AgentRuntime
            parent = AgentRuntime(
                pid="parent_agent", mode="continuous",
                harness=_make_minimal_harness(), kernel=kernel,
            )

            kernel.spawn_from_script(path, parent=parent)

            assert "collector" in parent.children
            assert kernel.runtime_table["collector"].parent is parent
        finally:
            os.unlink(path)


# ── end_workflow 返回值 ──

class TestEndWorkflowReturns:
    """Batch 2: end_workflow 返回 killed pids 列表。"""

    def test_end_workflow_returns_killed_pids(self):
        """end_workflow() 返回被 kill 的 pid 列表。"""
        kernel = Kernel(_MockConsole())
        kernel.workflow_table["wf_test"] = ["a", "b"]

        # 注册假 agent（避免 kill 中 WARNING）
        from harness.runtime.agent_runtime import AgentRuntime
        for pid in ["a", "b"]:
            rt = AgentRuntime(
                pid=pid, mode="oneshot",
                harness=_make_minimal_harness(), kernel=kernel,
            )
            kernel.runtime_table[pid] = rt
            kernel.input_queues[pid] = asyncio.Queue()

        killed = kernel.end_workflow("wf_test")
        assert set(killed) == {"a", "b"}

    def test_end_workflow_unknown_flag_returns_empty(self):
        """不存在的 flag → 返回空列表。"""
        kernel = Kernel(_MockConsole())
        killed = kernel.end_workflow("nonexistent")
        assert killed == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/runtime/test_spawn_from_script.py -v 2>&1 | tail -30
```

Expected: AttributeError — `Kernel` 没有 `spawn_from_script` 方法。

- [ ] **Step 3: 修改 kernel.py**

三个修改：
1. `end_workflow()` 改为返回 killed pids 列表
2. 添加 `spawn_from_script()` 方法
3. 添加 `_inject_runtime_tools()` 方法
4. `spawn_root()` 中调用 `_inject_runtime_tools()`

**修改 A — end_workflow 返回 killed pids:**

```python
# kernel.py — 替换现有的 end_workflow 方法:

    def end_workflow(self, flag: str) -> list[str]:
        """终止整个 workflow，返回被 kill 的 pid 列表。

        Args:
            flag: workflow 标识（如 "wf_root" 或 "wf_001"）。

        Returns:
            list[str]: 被 kill 的 agent pid 列表。
        """
        pids = self.workflow_table.get(flag, [])
        logger.info(f"end_workflow: flag='{flag}' killing {len(pids)} agents")
        for pid in pids:
            self.kill(pid)
        return list(pids)
```

**修改 B — 在 spawn_root 中添加 _inject_runtime_tools 调用:**

```python
# kernel.py — spawn_root() 方法中，_init_orchestrator() 之前添加:

        # 步骤 2a（新增）: 注入 Runtime tools
        self._inject_runtime_tools(harness.container, pid=pid)

        # 3. 初始化 orchestrator
        runtime._init_orchestrator(call_llm=call_llm)
```

**修改 C — 添加两个新方法（在 all_finished 之后，_on_agent_finished 之前插入）:**

```python
    def spawn_from_script(
        self, script_path: str, parent=None
    ) -> dict:
        """加载 workflow 脚本，创建多 agent 并启动。

        Args:
            script_path: workflow 脚本的绝对路径。
            parent: 父 AgentRuntime，None 表示顶层 agent。

        Returns:
            {"workflow_flag": str, "agents": [{"pid": str, "parent": str|None,
                                               "metadata": dict}]}

        Raises:
            FileNotFoundError: 脚本文件不存在或无法加载。
            ValueError: 无 @agent 声明，或 subscribe 引用未知 agent。
        """
        import sys
        import importlib.util
        from . import decorators
        from .agent_runtime import AgentRuntime
        from .bridge_adapter import KernelBridgeAdapter

        # ── 步骤 1: 生成 workflow_flag ──
        self._spawn_counter += 1
        workflow_flag = f"wf_{self._spawn_counter:03d}"

        # ── 步骤 2: 清空 registry + 加载脚本 ──
        decorators._agent_registry.clear()
        decorators._subscription_registry.clear()

        try:
            spec = importlib.util.spec_from_file_location(
                "_workflow_script", script_path
            )
            if spec is None:
                raise FileNotFoundError(
                    f"Cannot load workflow script: {script_path}"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_workflow_script"] = module
            spec.loader.exec_module(module)
        except Exception:
            decorators._agent_registry.clear()
            decorators._subscription_registry.clear()
            raise

        # ── 步骤 3: 校验 registry ──
        if not decorators._agent_registry:
            raise ValueError(
                f"No @agent declarations found in '{script_path}'"
            )

        for sub in decorators._subscription_registry:
            if sub.subscriber not in decorators._agent_registry:
                raise ValueError(
                    f"subscribe('{sub.subscriber}') references unknown agent. "
                    f"Known agents: {list(decorators._agent_registry.keys())}"
                )
            if sub.publisher not in decorators._agent_registry:
                raise ValueError(
                    f"subscribe(...).to('{sub.publisher}') references unknown "
                    f"agent. Known agents: {list(decorators._agent_registry.keys())}"
                )

        # ── 步骤 4: 暂存订阅关系 ──
        for sub in decorators._subscription_registry:
            self._pending_subscriptions.append(
                (sub.subscriber, sub.publisher)
            )

        # ── 步骤 5: 为每个 @agent 创建 AgentRuntime ──
        created_pids: list[str] = []
        agent_results: list[dict] = []

        for name, blueprint in decorators._agent_registry.items():
            try:
                # 5a. 调用 factory 获取 Harness
                harness = blueprint["factory"]()

                # 5b. 确定 mode
                has_subscriptions = any(
                    sub.subscriber == name or sub.publisher == name
                    for sub in decorators._subscription_registry
                )
                mode = "continuous" if has_subscriptions else "oneshot"

                # 5c. 创建 AgentRuntime
                runtime = AgentRuntime(
                    pid=name,
                    mode=mode,
                    harness=harness,
                    kernel=self,
                    parent=parent,
                )

                # 5d. 挂载 KernelBridgeAdapter
                runtime.adapter = KernelBridgeAdapter(
                    pid=name, kernel=self, runtime=runtime
                )

                # 5e. 提取并桥接 call_llm
                import asyncio as _asyncio
                call_llm = getattr(harness, 'call_llm', None)
                if call_llm and not _asyncio.iscoroutinefunction(call_llm):
                    original = call_llm

                    async def _async_wrapper(msgs, tools,
                                             _orig=original):
                        return await _asyncio.to_thread(_orig, msgs, tools)

                    call_llm = _async_wrapper

                # 5f. 注入 Runtime tools
                self._inject_runtime_tools(harness.container, pid=name)

                # 5g. 初始化 orchestrator
                runtime._init_orchestrator(call_llm=call_llm)

                # 5h. 注册到 Kernel（先检查重名）
                if name in self.runtime_table:
                    existing = self.runtime_table[name]
                    if existing.state != AgentState.FINISHED:
                        raise ValueError(
                            f"Agent name '{name}' already exists in "
                            f"runtime_table (state={existing.state.value}). "
                            f"Each spawn must use unique @agent names."
                        )
                self.runtime_table[name] = runtime
                self.input_queues[name] = asyncio.Queue()

                # 5i. 记录父子关系
                if parent is not None:
                    parent.children.append(name)

                created_pids.append(name)
                agent_results.append({
                    "pid": name,
                    "parent": parent.pid if parent else None,
                    "metadata": blueprint.get("metadata", {}),
                })

            except Exception:
                # 回滚：清理已创建的 AgentRuntime
                for created_pid in created_pids:
                    self.runtime_table.pop(created_pid, None)
                    self.input_queues.pop(created_pid, None)
                if parent is not None:
                    for created_pid in created_pids:
                        if created_pid in parent.children:
                            parent.children.remove(created_pid)
                raise

        # ── 步骤 6: 推送 SystemConsole 事件 ──
        for name in created_pids:
            from .types import AgentSpawned
            asyncio.create_task(
                self._console.send(
                    AgentSpawned(
                        pid=name, parent=parent.pid if parent else None
                    )
                )
            )

        # ── 步骤 7: 启动 asyncio Task ──
        for name in created_pids:
            runtime = self.runtime_table[name]
            task = asyncio.create_task(runtime.run())
            self._tasks[name] = task
            task.add_done_callback(
                lambda t, r=runtime: asyncio.create_task(
                    self._on_agent_finished(r)
                )
            )

        # ── 步骤 8: 记录 workflow 映射 ──
        self.workflow_table[workflow_flag] = created_pids.copy()

        # ── 步骤 9: 投递 entry_prompt ──
        for name, blueprint in decorators._agent_registry.items():
            self.send_input(
                name,
                UserRequest(
                    text=blueprint["entry_prompt"],
                    metadata={"workflow_flag": workflow_flag},
                ),
            )

        logger.info(
            f"spawn_from_script: workflow_flag='{workflow_flag}' "
            f"created {len(created_pids)} agent(s): {created_pids}"
        )

        # ── 步骤 10: 返回 ──
        return {
            "workflow_flag": workflow_flag,
            "agents": agent_results,
        }

    def _inject_runtime_tools(self, container, pid: str) -> None:
        """向 agent 的 DI 容器注入 Runtime 管理 Tool。

        包装为 CompositeSystemToolProvider，保留用户原有的 SystemToolProvider。

        Args:
            container: agent 的 DIContainer 实例。
            pid: 当前 agent 的 pid。
        """
        from .tools import create_runtime_tools, CompositeSystemToolProvider
        from ..interfaces.system_tool_provider import SystemToolProvider

        try:
            user_provider = container.resolve(SystemToolProvider)
        except Exception:
            user_provider = None

        runtime_tools = create_runtime_tools(kernel=self, pid=pid)

        composite = CompositeSystemToolProvider(
            user_provider=user_provider,
            runtime_tools=runtime_tools,
        )

        # 如果已注册则替换（composite 包装了原 provider，不会丢失用户工具）
        if container.is_registered(SystemToolProvider):
            container._registry[SystemToolProvider] = composite
        else:
            container.register(SystemToolProvider, composite)

        logger.debug(
            f"_inject_runtime_tools: pid='{pid}' "
            f"injected {composite.tool_count} runtime tool(s)"
        )
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/runtime/test_spawn_from_script.py -v 2>&1
```

Expected: all tests pass (≥ 14 tests).

- [ ] **Step 5: 确认全部已有测试仍通过**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runtime/kernel.py tests/runtime/test_spawn_from_script.py
git commit -m "feat: add Kernel.spawn_from_script() + _inject_runtime_tools()

- spawn_from_script(script_path, parent): 10-step workflow loading
  - Clears decorator registry, loads via importlib
  - Creates AgentRuntime for each @agent with mode detection
  - Injects runtime tools via CompositeSystemToolProvider
  - Starts asyncio Tasks and delivers entry_prompts
  - Rollback on failure (cleanup created Runtime entries)
  - Duplicate name check (raises ValueError)
- _inject_runtime_tools(container, pid): wraps user's SystemToolProvider
  with CompositeSystemToolProvider containing 5 runtime management tools
- end_workflow() now returns list of killed pids
- spawn_root() updated to call _inject_runtime_tools

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 更新 __init__.py — re-export Batch 2 组件

**Files:**
- Modify: `harness/runtime/__init__.py`

- [ ] **Step 1: 添加 re-export**

```python
# harness/runtime/__init__.py — 在 "Batch 2" 注释后、现有 imports 之后添加:

from .decorators import agent, subscribe, SubRecord
from .tools import (
    CompositeSystemToolProvider,
    SpawnWorkflowTool,
    EndWorkflowTool,
    FinishAgentTool,
    TalkToTool,
    ListAgentsTool,
    create_runtime_tools,
)
```

同时更新 `__all__` 列表添加新导出名。

- [ ] **Step 2: 验证 import 可用**

```bash
python -c "
from harness.runtime import agent, subscribe
from harness.runtime import CompositeSystemToolProvider, create_runtime_tools
from harness.runtime import SpawnWorkflowTool, EndWorkflowTool, FinishAgentTool, TalkToTool, ListAgentsTool
print('All imports OK')
"
```

Expected: "All imports OK"。

- [ ] **Step 3: 确认全部测试仍通过**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add harness/runtime/__init__.py
git commit -m "feat: re-export Batch 2 components from harness.runtime

Add agent, subscribe, CompositeSystemToolProvider, 5 tool classes,
and create_runtime_tools to harness.runtime public API.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
