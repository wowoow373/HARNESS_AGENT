# Batch 2: Workflow 脚本加载 + Runtime 管理 Tool

> 版本: 0.1 | 日期: 2026-06-09 | 状态: 设计评审
> 依赖: Batch 0（Async 接口层 + AsyncLifecycleOrchestrator） + Batch 1（Runtime + Kernel + AgentRuntime 骨架）

---

## 一、目标

在 Batch 1 的单 agent 骨架之上，实现多 agent workflow 脚本加载和 Runtime 管理 tool 集。使 LLM 可以通过 `spawn_workflow` tool 加载 Python 脚本、创建子 agent、管理其生命周期。

本 batch 结束时：
- `@agent` 装饰器和 `subscribe()` 函数可用，workflow 脚本可声明 agent 装配逻辑和拓扑关系
- `Kernel.spawn_from_script()` 可用，支持从 Python 脚本文件加载多 agent 配置、创建 AgentRuntime、启动 asyncio Task
- 5 个 Runtime 管理 tool（`spawn_workflow` / `end_workflow` / `finish_agent` / `talk_to` / `list_agents`）可通过 `BaseTool` 子类注册到 agent 的 `SystemToolProvider`
- `CompositeSystemToolProvider` 包装器可用，在不修改 DI 容器注册机制的前提下向 agent 注入 Runtime tool
- 父 agent 可通过 `list_agents` tool 查询子 agent 状态，通过 `talk_to` tool 与子 agent 通信；`child_finished` 自动通知在 Batch 3 实现
- 全部现有测试继续通过

---

## 二、前置条件

以下接口/组件已存在且**不做任何修改**：

| 组件 | 位置 | 用途 |
|------|------|------|
| `AsyncInputAdapter` Protocol | `harness/interfaces/async_input_adapter.py` | AgentRuntime 的 I/O 通道 |
| `AsyncCallLLM` Protocol | `harness/interfaces/async_call_llm.py` | async LLM 调用契约 |
| `AsyncLifecycleOrchestrator` | `harness/core/async_orchestrator.py` | 三阶段编排（_phase_loop 仅执行一轮） |
| `KernelBridgeAdapter` | `harness/runtime/bridge_adapter.py` | 实现 AsyncInputAdapter，内联降级路由 + 定向投递 |
| `AgentRuntime` / `AgentState` | `harness/runtime/agent_runtime.py` | 状态机 + oneshot/continuous + parent/children + run() |
| `Kernel` | `harness/runtime/kernel.py` | 进程表 + spawn_root / send_input / kill / end_workflow / finish_agent / list_agents / all_finished |
| `SystemConsole` Protocol | `harness/interfaces/system_console.py` | 系统级交互接口 |
| `CliConsole` | `harness/runtime/cli_console.py` | SystemConsole 默认 CLI 实现 |
| `Runtime` | `harness/runtime/runtime.py` | 顶层入口 + sync→async LLM bridge |
| `InternalMessage` / `__EXIT_SENTINEL__` / `AgentOutput` 等 | `harness/runtime/types.py` | 共享类型 |
| `BaseTool` | `harness/components/tool/base.py` | Tool 抽象基类 |
| `DefaultSystemToolProvider` | `harness/components/tool/default_system_tool_provider.py` | 默认系统工具提供者 |
| `SystemToolProvider` Protocol | `harness/interfaces/system_tool_provider.py` | 系统工具提供者接口 |
| `DIContainer` | `harness/core/container.py` | DI 容器 |
| `ToolRouter` | `harness/core/tool_router.py` | Tool 执行分发 |

---

## 三、新增/修改的组件

| 组件 | 类型 | 文件 | 需展开内部设计 |
|------|------|------|--------------|
| `@agent` 装饰器 + `subscribe()` + registry | 新增 | `harness/runtime/decorators.py` | 是 |
| `CompositeSystemToolProvider` | 新增 | `harness/runtime/tools.py` | 是 |
| 5 个 Runtime `BaseTool` 子类 | 新增 | `harness/runtime/tools.py` | 是 |
| `Kernel.spawn_from_script()` | 修改 | `harness/runtime/kernel.py` | 是 |
| `Kernel._inject_runtime_tools()` | 修改 | `harness/runtime/kernel.py` | 是 |
| `harness/runtime/__init__.py` | 修改 | `harness/runtime/__init__.py` | 否（仅加 re-export） |

### 3.1 跨 Batch 接口契约

Batch 2 完成后，暴露给 Batch 3 的稳定 import：

```python
from harness.runtime.decorators import agent, subscribe
from harness.runtime.tools import (
    CompositeSystemToolProvider,
    SpawnWorkflowTool,
    EndWorkflowTool,
    FinishAgentTool,
    TalkToTool,
    ListAgentsTool,
    create_runtime_tools,
)
# Kernel 新增方法: spawn_from_script(script_path, parent=None) → dict
# Kernel 新增字段: _pending_subscriptions: list[tuple[str, str]]
#   （每个 tuple = (subscriber_pid, publisher_pid)，
#     Batch 3 在 MessageBus 创建后统一注册）
```

---

## 四、关键组件设计

### 4.1 `@agent` 装饰器 + `subscribe()` + Registry

#### 4.1.1 模块级全局变量

```python
# harness/runtime/decorators.py

from dataclasses import dataclass, field
from typing import Callable, Optional

# ── 模块级 registry ──
# 由 Kernel.spawn_from_script 在加载脚本前清空。
# 单线程 asyncio 模型下，模块级全局变量是安全的（无真正并发）。

_agent_registry: dict[str, dict] = {}
"""agent 注册表。key = agent name (pid), value = Blueprint dict:
    {
        "name": str,
        "entry_prompt": str,
        "metadata": dict,
        "factory": Callable[[], Harness],
    }
"""

_subscription_registry: list['SubRecord'] = []
"""订阅声明列表。每个元素是一个 SubRecord。"""


@dataclass
class SubRecord:
    """一条订阅声明。"""
    subscriber: str
    publisher: str
```

#### 4.1.2 `@agent` 装饰器

```python
def agent(name: str, entry_prompt: str, metadata: dict | None = None):
    """声明一个 agent 及其装配逻辑。

    被装饰的函数（factory）返回一个 Harness 实例。
    Kernel.spawn_from_script 在加载脚本后遍历 _agent_registry，
    调用每个 factory 获取 Harness，创建对应的 AgentRuntime。

    Args:
        name: agent 的 pid，在本次 spawn 内唯一。
        entry_prompt: 必需。agent 的第一条消息。
                      Kernel spawn 时作为 UserRequest.text 投递。
        metadata: 可选。透传给父 agent 的 LLM，
                  出现在 spawn_workflow 返回值的 agents[].metadata 中。

    Returns:
        decorator: 接受 factory 函数的装饰器。

    Raises:
        ValueError: name 已在 _agent_registry 中（重复注册）。
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
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 重复注册同一 name | raise ValueError | 即时反馈。spawn_from_script 在加载前清空 registry，所以重复只发生在同一次脚本加载中 |
| factory 返回值类型约束 | 文档约定返回 `Harness`；运行时不做类型检查 | 和现有 `@inline_tool` 等装饰器风格一致（duck typing） |
| `metadata` 默认值 | 空 dict `{}` | 避免 None check，下游代码直接 `.get()` |

#### 4.1.3 `subscribe()` 函数

```python
class _SubscribeBuilder:
    """subscribe("A").to("B") 的中间构建器。"""

    def __init__(self, subscriber: str):
        self._subscriber = subscriber

    def to(self, publisher: str) -> None:
        """声明订阅：subscriber 接收 publisher 的每轮 TextEvent/StopEvent。

        多次调用 subscribe("A").to("B") 会累加（不会覆盖）。

        Raises:
            ValueError: subscriber == publisher（不允许自订阅）
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

    含义：analyzer 订阅 collector 的每轮输出（TextEvent/StopEvent）。

    Batch 2 行为：订阅声明被 Kernel.spawn_from_script 收集到
    _pending_subscriptions 中，但 MessageBus 在 Batch 3 才创建。
    Batch 2 期间，subscribe 声明的唯一效果是影响 agent 的 mode：
    声明了 subscribe 的 agent 自动设为 "continuous"，否则为 "oneshot"。

    订阅的 agent name 不必在 @agent 中已注册——subscribe 在模块顶层被调用时
    @agent 装饰器尚未执行。Kernel.spawn_from_script 在加载完成后统一校验
    引用的 name 是否都存在于 _agent_registry 中。

    Args:
        subscriber: 订阅者 agent 的 name。

    Returns:
        _SubscribeBuilder: 调用 .to(publisher) 完成声明。
    """
    return _SubscribeBuilder(subscriber)
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 多次调用 `subscribe("A").to("B")` | 累加（不覆盖） | 简单直观；如需去重，由 MessageBus.subscribe 的 set 自动处理 |
| 引用的 name 不在 registry 中 | `spawn_from_script` 加载完成后统一校验并 raise ValueError | subscribe 调用时 @agent 可能尚未执行，所以不能在 subscribe 时校验 |
| 自订阅 | raise ValueError | 语义上无意义且可能导致死循环 |
| subscribe 影响 mode | 参与了 subscribe 声明的 agent（作为 subscriber 或 publisher）→ `continuous`；未参与 → `oneshot` | 发布者也需持续运行以产生输出供订阅者接收；顶层设计已明确此规则 |

#### 4.1.4 Registry 隔离

```python
# Kernel.spawn_from_script() 中:
from . import decorators

# 1. 清空 registry
decorators._agent_registry.clear()
decorators._subscription_registry.clear()

# 2. 用 importlib 加载脚本
import importlib.util
spec = importlib.util.spec_from_file_location("_workflow_script", script_path)
module = importlib.util.module_from_spec(spec)
sys.modules["_workflow_script"] = module
spec.loader.exec_module(module)
# 模块中 @agent / subscribe() 调用填充了 _agent_registry / _subscription_registry
```

**为什么安全**：
- 每次 spawn 前清空 → 每次获得干净的 registry
- 固定模块名 `"_workflow_script"` → 覆盖 `sys.modules` 中同名条目 → Python 的模块级代码不会在第二次 import 时重新执行（但 importlib 的 `exec_module` 强制重新执行）
- asyncio 单线程 → `clear()` 和 `exec_module()` 之间无并发

---

### 4.2 Runtime 管理 Tool 集

#### 4.2.1 整体架构

```
Kernel.spawn_root() / spawn_from_script()
  │
  └── _inject_runtime_tools(harness.container)
        │
        ├─ 1. 获取用户原有的 SystemToolProvider
        │     (从 DI 容器 resolve，如不存在则用 DefaultSystemToolProvider)
        │
        ├─ 2. 创建 5 个 Runtime BaseTool 实例
        │     (每个捕获 kernel 引用，用于 execute() 中调用 kernel 方法)
        │
        ├─ 3. 包装为 CompositeSystemToolProvider
        │     CompositeSystemToolProvider(user_provider, runtime_tools)
        │
        └─ 4. 覆盖注册到 DI 容器
              container.register(SystemToolProvider, composite)
```

#### 4.2.2 `CompositeSystemToolProvider`

```python
# harness/runtime/tools.py

from typing import Any, Dict, List, Optional

from ..components.tool.base import BaseTool
from ..components.tool.default_system_tool_provider import DefaultSystemToolProvider
from ..interfaces.types import ToolDefinition, ToolResult


class CompositeSystemToolProvider:
    """组合式 SystemToolProvider。

    将用户原有的 SystemToolProvider 与 Runtime 管理 Tool 合并。
    Runtime tool 优先级高于用户 tool——同名时 Runtime tool 覆盖用户 tool。

    实现 SystemToolProvider Protocol（duck typing，不显式继承）。
    """

    def __init__(
        self,
        user_provider: Optional[object] = None,
        runtime_tools: Optional[List[BaseTool]] = None,
    ):
        """初始化组合 Provider。

        Args:
            user_provider: 用户原有的 SystemToolProvider 实例。
                           为 None 时使用 DefaultSystemToolProvider。
            runtime_tools: Runtime 管理 Tool 实例列表。
                           为 None 时空列表。
        """
        # 用户 provider（确保非空）
        if user_provider is None:
            user_provider = DefaultSystemToolProvider()
        self._user = user_provider

        # Runtime tools（以 name 索引）
        self._runtime_tools: Dict[str, BaseTool] = {}
        if runtime_tools:
            for tool in runtime_tools:
                self._register_runtime_tool(tool)

    # ------------------------------------------------------------------
    # SystemToolProvider 协议方法
    # ------------------------------------------------------------------

    def get_tools(self) -> List[ToolDefinition]:
        """合并用户 tools 和 Runtime tools。

        Runtime tools 排在用户 tools 之后（在 LLM 的 function list 中靠后）。
        """
        user_defs = self._user.get_tools() if self._user else []
        runtime_defs = [
            t.get_definition() for t in self._runtime_tools.values()
        ]
        return user_defs + runtime_defs

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """按名称执行工具。

        先查 Runtime tools，再查用户 provider。
        Runtime tool 优先级高于用户 tool。

        Raises:
            KeyError: 工具名称在两者中都未找到。
        """
        # Runtime tool 优先
        if name in self._runtime_tools:
            return self._runtime_tools[name].execute(args)

        # 回退到用户 provider
        if self._user:
            try:
                return self._user.execute(name, args)
            except KeyError:
                pass

        raise KeyError(
            f"Tool '{name}' not found in CompositeSystemToolProvider"
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _register_runtime_tool(self, tool: BaseTool) -> None:
        """注册一个 Runtime tool。"""
        name = tool.get_definition().name
        if name in self._runtime_tools:
            import logging
            logging.getLogger(__name__).warning(
                f"Runtime tool '{name}' already registered, overwriting"
            )
        self._runtime_tools[name] = tool

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def tool_count(self) -> int:
        """返回已注册的 Runtime tool 数量。"""
        return len(self._runtime_tools)

    def has_runtime_tool(self, name: str) -> bool:
        """检查 Runtime tool 是否存在。"""
        return name in self._runtime_tools
```

#### 4.2.3 5 个 Runtime Tool

所有 Runtime tool 实现 `BaseTool`，通过构造函数捕获 `kernel` 引用。

**Tool 1: `spawn_workflow`**

```python
class SpawnWorkflowTool(BaseTool):
    """加载 workflow 脚本，创建子 agent 并启动。

    LLM 调用此 tool 后得到 workflow_flag 和 agent 列表。
    后续通过 end_workflow / talk_to 管理这些子 agent。
    """

    def __init__(self, kernel: 'Kernel', parent_pid: str):
        """初始化 tool。

        Args:
            kernel: Kernel 全局单例。
            parent_pid: 调用此 tool 的 agent 的 pid。
                        子 workflow 的 agent 将以该 agent 为 parent。
        """
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
                        "description": "workflow 脚本的绝对路径（.py 文件）。"
                                       "脚本中通过 @agent 装饰器声明 agent 装配逻辑。",
                    },
                },
                "required": ["script_path"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        script_path = args["script_path"]
        try:
            # 查找 parent AgentRuntime，传递给 spawn_from_script
            parent = self._kernel.runtime_table.get(self._parent_pid)
            result = self._kernel.spawn_from_script(
                script_path, parent=parent
            )
            import json
            return ToolResult(
                success=True,
                content=json.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"spawn_workflow failed: {e}"
            )
            return ToolResult(
                success=False,
                content="",
                error=f"{type(e).__name__}: {e}",
            )
```

**Tool 2: `end_workflow`**

```python
class EndWorkflowTool(BaseTool):
    """终止整个 workflow 的所有 agent。"""

    def __init__(self, kernel: 'Kernel'):
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
                        "description": "workflow 标识。"
                                       "spawn_workflow 返回的 workflow_flag。",
                    },
                },
                "required": ["flag"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        flag = args["flag"]
        killed = self._kernel.end_workflow(flag)
        import json
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True, "killed": killed}),
        )
```

**Tool 3: `finish_agent`**

```python
class FinishAgentTool(BaseTool):
    """当前 agent 自我终止。

    调用后 agent 进入 TERMINATING → FINISHED。
    通常在 agent 完成自身任务后调用。
    """

    def __init__(self, kernel: 'Kernel', pid: str):
        self._kernel = kernel
        self._pid = pid

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="finish_agent",
            description=(
                "标记当前 agent 的任务完成，触发正常退出流程。"
                "仅在当前 agent 确定所有工作已完成时调用。"
                "调用后 agent 立即进入退出流程。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        self._kernel.finish_agent(self._pid)
        import json
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True}),
        )
```

**Tool 4: `talk_to`**

```python
class TalkToTool(BaseTool):
    """向指定 agent 发送定向消息。

    不走订阅路由，直接投递到目标 agent 的 input_queue。
    用于父 agent 向子 agent 发送指令，或 peer agent 间通信。
    """

    def __init__(self, kernel: 'Kernel', from_pid: str):
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
                        "description": "目标 agent 的 pid。"
                                       "spawn_workflow 返回的 agents 列表中获取。",
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
        from ..interfaces.types import UserRequest
        self._kernel.send_input(
            target_pid,
            UserRequest(
                text=text,
                metadata={"from": self._from_pid, "type": "talk_to"},
            ),
        )
        import json
        return ToolResult(
            success=True,
            content=json.dumps({"ok": True, "target": target_pid}),
        )
```

**Tool 5: `list_agents`**

```python
class ListAgentsTool(BaseTool):
    """列出 Kernel 中所有 agent 的当前状态。"""

    def __init__(self, kernel: 'Kernel'):
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
        import json
        return ToolResult(
            success=True,
            content=json.dumps({"agents": agents}, ensure_ascii=False),
        )
```

#### 4.2.4 工厂函数

```python
def create_runtime_tools(kernel: 'Kernel', pid: str) -> list[BaseTool]:
    """创建当前 agent 需要的 Runtime 管理 Tool 列表。

    Args:
        kernel: Kernel 全局单例引用。
        pid: 当前 agent 的 pid（用于 finish_agent / talk_to 知道"自己是谁"）。

    Returns:
        list: 5 个 Runtime tool 实例。
    """
    return [
        SpawnWorkflowTool(kernel=kernel),
        EndWorkflowTool(kernel=kernel),
        FinishAgentTool(kernel=kernel, pid=pid),
        TalkToTool(kernel=kernel, from_pid=pid),
        ListAgentsTool(kernel=kernel),
    ]
```

---

### 4.3 `Kernel.spawn_from_script()`

> ⚠️ **前置依赖说明**：`spawn_from_script` 从 `@agent` factory 返回的 `Harness` 实例中提取 `harness.container` 和 `harness.call_llm` 属性。当前 `Harness` 类（`harness/di.py`）将内部逻辑委托给 `LifecycleOrchestrator`，不直接暴露 `.container` / `.call_llm` 属性。**Batch 1 已在 `agent_runtime.py` 和 `runtime.py` 中依赖这两个属性**——如果尚未修复，需要在 `Harness` 类中添加透传 property：
> ```python
> @property
> def container(self): return self._orchestrator.container
> @property
> def call_llm(self): return self._call_llm
> ```
> 此修复属于 Batch 1 遗留问题，不计入 Batch 2 工作量。


#### 4.3.1 方法签名

```python
def spawn_from_script(
    self,
    script_path: str,
    parent: Optional['AgentRuntime'] = None,
) -> dict:
    """加载 workflow 脚本，创建多 agent 并启动。

    适用于两种场景：
    - Mode A 子 agent spawn：parent 为调用 spawn_workflow tool 的 agent
    - Mode B entry agent spawn：parent 为 None（Batch 3 run_from_script）

    Returns:
        {
            "workflow_flag": "wf_001",
            "agents": [
                {"pid": "collector", "parent": "root", "metadata": {}},
                {"pid": "analyzer", "parent": "root", "metadata": {"description": "..."}},
            ]
        }
    """
```

#### 4.3.2 完整伪代码

```
def spawn_from_script(self, script_path, parent=None):
    import sys
    import importlib.util
    from . import decorators
    from .agent_runtime import AgentRuntime
    from .bridge_adapter import KernelBridgeAdapter
    from ..interfaces.types import UserRequest

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
        # 加载失败：清空 registry（部分填充的），透传异常
        decorators._agent_registry.clear()
        decorators._subscription_registry.clear()
        raise

    # ── 步骤 3: 校验 registry ──
    if not decorators._agent_registry:
        raise ValueError(
            f"No @agent declarations found in '{script_path}'"
        )

    # 校验 subscribe 中引用的 name 都存在于 _agent_registry
    for sub in decorators._subscription_registry:
        if sub.subscriber not in decorators._agent_registry:
            raise ValueError(
                f"subscribe('{sub.subscriber}') references unknown agent. "
                f"Known agents: {list(decorators._agent_registry.keys())}"
            )
        if sub.publisher not in decorators._agent_registry:
            raise ValueError(
                f"subscribe(...).to('{sub.publisher}') references unknown agent. "
                f"Known agents: {list(decorators._agent_registry.keys())}"
            )

    # ── 步骤 4: 暂存订阅关系 ──
    # Batch 2 中 MessageBus 尚未创建，订阅关系暂存到 _pending_subscriptions。
    # Batch 3 在 MessageBus 创建后统一注册。
    # 注意：多次 spawn_from_script 会累加。Batch 3 注册前需按 (subscriber,
    # publisher) 去重（set），避免同一订阅关系被注册多次。
    for sub in decorators._subscription_registry:
        self._pending_subscriptions.append((sub.subscriber, sub.publisher))

    # ── 步骤 5: 为每个 @agent 创建 AgentRuntime ──
    created_pids: list[str] = []  # 用于回滚
    agent_results: list[dict] = []  # 返回值中 agents 列表

    for name, blueprint in decorators._agent_registry.items():
        try:
            # 5a. 调用 factory 获取 Harness
            harness = blueprint["factory"]()

            # 5b. 确定 mode
            # agent 声明了 subscribe（作为订阅者或发布者）→ continuous
            # 因为发布者也需要持续运行以产生输出供订阅者接收
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
            call_llm = getattr(harness, 'call_llm', None)
            if call_llm and not asyncio.iscoroutinefunction(call_llm):
                original = call_llm
                async def _async_wrapper(msgs, tools):
                    return await asyncio.to_thread(original, msgs, tools)
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
                        f"Agent name '{name}' already exists in runtime_table "
                        f"(state={existing.state.value}). "
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
            # 清理刚创建的 parent.children
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
                AgentSpawned(pid=name, parent=parent.pid if parent else None)
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
    # 重要：Task 已经启动（步骤 7），agent 在 adapter.receive() 处阻塞。
    # 现在投递 entry_prompt 唤醒它们。
    for name, blueprint in decorators._agent_registry.items():
        self.send_input(
            name,
            UserRequest(
                text=blueprint["entry_prompt"],
                metadata={"workflow_flag": workflow_flag},
            ),
        )

    # ── 步骤 10: 返回 ──
    return {
        "workflow_flag": workflow_flag,
        "agents": agent_results,
    }
```

#### 4.3.3 关键时序：Task 启动 vs entry_prompt 投递

```
1. asyncio.create_task(runtime.run())
   → agent 进入 INIT → _phase_init() → await adapter.receive()
   → input_queues[name].get() [阻塞]
                                    ↓
2. send_input(name, UserRequest(entry_prompt))
   → input_queues[name].put_nowait(UserRequest)
   → 唤醒 agent
```

Task 启动在 entry_prompt 投递之前，确保 agent 已经在监听队列后才投递消息。这是安全的：`asyncio.create_task` 创建协程但不会立即执行到 `receive()`——它在下一次 `await` 点才执行。由于 `spawn_from_script` 是同步函数（在 tool execute 中同步调用），所有 Task 创建完后才投递 entry_prompt，agent 在下次 event loop 迭代中才开始执行。

#### 4.3.4 回滚策略

| 失败步骤 | 回滚行为 |
|---------|---------|
| 步骤 2（脚本加载失败） | `_agent_registry` / `_subscription_registry` 已清空但无新内容。异常透传 |
| 步骤 3（校验失败） | 尚未创建任何 AgentRuntime。异常透传 |
| 步骤 5（创建 AgentRuntime 失败） | 清理 `created_pids` 中已创建的 Runtime（从 runtime_table/input_queues 移除），清理 parent.children。异常透传 |
| 步骤 6-9（SystemConsole/Task/entry_prompt 失败） | 已创建的 AgentRuntime 仍存在。对于步骤 7 失败：已启动的 Task 仍然运行，但 `_tasks` 中无记录 → agent 自然完成后不会触发 `_on_agent_finished`。对于步骤 9 失败：已启动的 agent 在 `receive()` 中阻塞，永远不会收到 entry_prompt → 需要超时机制（后续 batch 解决） |

#### 4.3.5 `_inject_runtime_tools()`

```python
def _inject_runtime_tools(self, container, pid: str) -> None:
    """向 agent 的 DI 容器注入 Runtime 管理 Tool。

    包装为 CompositeSystemToolProvider，保留用户原有的 SystemToolProvider。

    Args:
        container: agent 的 DIContainer 实例。
        pid: 当前 agent 的 pid。
    """
    from .tools import create_runtime_tools, CompositeSystemToolProvider
    from ..interfaces.system_tool_provider import SystemToolProvider

    # 获取用户原有 provider（可能不存在）
    try:
        user_provider = container.resolve(SystemToolProvider)
    except Exception:
        user_provider = None

    # 创建 Runtime tools
    runtime_tools = create_runtime_tools(kernel=self, pid=pid)

    # 包装为 Composite
    composite = CompositeSystemToolProvider(
        user_provider=user_provider,
        runtime_tools=runtime_tools,
    )

    # 注册到 DI 容器。
    # 如果 SystemToolProvider 已注册（通常如此），需要替换为 composite。
    # DIContainer.register() 不允许重复注册（会抛 DuplicateRegistrationError），
    # 因此用直接赋值覆盖——这是有意的替换，composite 内部包装了原始 provider，
    # 不会丢失用户工具。
    if container.is_registered(SystemToolProvider):
        container._registry[SystemToolProvider] = composite
    else:
        container.register(SystemToolProvider, composite)
```

**设计要点**：

| 问题 | 决策 | 理由 |
|------|------|------|
| 覆盖注册是否会丢失用户 tools | 否 — Composite 包装了 user_provider | `get_tools()` 返回 user tools + runtime tools；`execute()` 先查 runtime 再查 user |
| 用户未注册 SystemToolProvider | 使用 DefaultSystemToolProvider 作为 _user | 确保基本文件读写工具仍然可用 |
| `_inject_runtime_tools` 调用时机 | `_init_orchestrator()` 之前 | orchestrator 在 `_phase_init()` 中 resolve SystemToolProvider，必须在此之前注册完成 |
| 覆盖注册方式 | 直接赋值 `_registry[key]` 绕过 DuplicateRegistrationError | 不修改 DIContainer 类（保持 Batch 0/1 的"不碰现有组件"原则），且替换是安全的（composite 包装了原 provider） |

---

### 4.4 `spawn_root` 的同步更新

```python
# Kernel.spawn_root() 中，更新后的步骤顺序：
# (原步骤 1-2 不变: 创建 AgentRuntime + 挂载 KBA)

# 步骤 2a（新增）: 注入 Runtime tools
self._inject_runtime_tools(harness.container, pid=pid)

# 步骤 3（原步骤）: 初始化 orchestrator
runtime._init_orchestrator(call_llm=call_llm)

# (后续步骤不变: 注册 → 推送 → 启动 Task → 记录 workflow)
```

---

## 五、数据流

### 5.1 spawn_workflow 完整流程

```
root agent (Mode A, continuous)
  │
  ├─ LLM 决定 spawn workflow
  ├─ LLM 调用 tool: spawn_workflow(script_path="workflow.py")
  │
  ▼
SpawnWorkflowTool.execute(args={script_path: "workflow.py"})
  │
  └─ kernel.spawn_from_script("workflow.py", parent=root_runtime)
       │
       ├─ 1. 生成 workflow_flag="wf_001"
       ├─ 2. 清空 decorators registry
       ├─ 3. importlib 加载脚本 → exec_module
       │     → @agent("collector", ...) 填充 _agent_registry
       │     → @agent("analyzer", ...)  填充 _agent_registry
       │     → subscribe("analyzer").to("collector") 填充 _subscription_registry
       ├─ 4. 校验 references
       ├─ 5. 暂存订阅到 _pending_subscriptions (Batch 3 使用)
       ├─ 6. 创建 AgentRuntime × 2
       │     ├─ collector: mode="oneshot", adapter=KBA("collector", ...)
       │     │   _inject_runtime_tools(collector_container, pid="collector")
       │     └─ analyzer:  mode="continuous", adapter=KBA("analyzer", ...)
       │         _inject_runtime_tools(analyzer_container, pid="analyzer")
       ├─ 7. 注册到 runtime_table / input_queues
       ├─ 8. root.children = ["collector", "analyzer"]
       ├─ 9. console.send(AgentSpawned) × 2
       ├─ 10. asyncio.create_task(runtime.run()) × 2
       │      → collector + analyzer 都进入 INIT → adapter.receive() [阻塞]
       ├─ 11. workflow_table["wf_001"] = ["collector", "analyzer"]
       ├─ 12. send_input("collector", UserRequest(text="采集代码库...", metadata={workflow_flag:"wf_001"}))
       │      send_input("analyzer", UserRequest(text="分析 collector 数据...", metadata={workflow_flag:"wf_001"}))
       │      → 唤醒 agent
       └─ 13. return {workflow_flag: "wf_001", agents: [{pid:"collector",...}, {pid:"analyzer",...}]}
            │
            ▼
ToolRouter → ToolResult(success=True, content=json)
  │
  ▼
AsyncLifecycleOrchestrator._phase_loop()
  → ToolResult 追加到 messages
  → LLM 下一轮看到 spawn_workflow 的返回值
  → LLM 从返回值中记住 workflow_flag 和 pid
```

### 5.2 子 agent oneshot 自动完成 + child_finished 通知

```
collector (oneshot):
  │  adapter.receive() → UserRequest(text="采集代码库...", metadata={workflow_flag:"wf_001"})
  │  _phase_loop: call_llm → TextEvent("采集到 28 个文件...") → StopEvent
  │  mode="oneshot" → should_exit = True
  │  finally: _phase_end → state=FINISHED → _finished.set()
  │
  ▼
Kernel._on_agent_finished(collector_runtime):  [Batch 1 stub — 仅 console.send]
  │  → console.send(AgentFinished(pid="collector", result="采集到 28 个文件...", ...))
  │  注意：Batch 1 stub 中还没有 child_finished 推送 —— 这是 Batch 3 的内容。
  │  因此 Batch 2 期间父 agent 不会自动收到子 agent 的完成通知。
  └─ 父 agent 需要轮询 list_agents() 或等待 talk_to 来获知子 agent 状态。
```

**Batch 2 的务实限制**：`_on_agent_finished` 仍为 Batch 1 stub（仅推送 `AgentFinished` 到 SystemConsole）。`child_finished` 默认订阅推送在 Batch 3 实现。Batch 2 期间，父 agent 可以通过 `list_agents` tool 主动查询子 agent 状态，或等待子 agent 通过 `talk_to` 主动汇报。

### 5.3 talk_to 定向投递路径

```
root agent:
  LLM 调用 tool: talk_to(pid="collector", text="请用中文汇报")
  │
  ▼
TalkToTool.execute(args={pid: "collector", text: "请用中文汇报"})
  → kernel.send_input("collector", UserRequest(text="请用中文汇报", metadata={from:"root", type:"talk_to"}))
  → input_queues["collector"].put_nowait(UserRequest)
  │
  ▼
collector: adapter.receive() → UserRequest(text="请用中文汇报", metadata={from:"root", type:"talk_to"})
  → 下一轮 _phase_loop 中 LLM 看到此消息
```

**注意**：`talk_to` 走 `Kernel.send_input()` 直接入队，不经过 KBA.send() 的定向投递路径。这是因为 tool 在 ToolRouter 中同步执行，无法调用 KBA 的 async send()。两条路径互相独立：
- **Tool 路径**（本 batch）：`TalkToTool.execute()` → `Kernel.send_input()` → 直接入队
- **KBA 路径**（Batch 3）：`adapter.send(event, target=pid)` → `MessageBus.direct()` → 入队

---

## 六、错误处理

### 6.1 异常分类与策略

| 异常来源 | 处理位置 | 策略 |
|---------|---------|------|
| 脚本文件不存在 | `spawn_from_script` 步骤 2 | `importlib.util.spec_from_file_location` 返回 None → raise `FileNotFoundError` → ToolResult(success=False, error=...) |
| 脚本语法错误 / import 错误 | `spawn_from_script` 步骤 2 | `exec_module` 抛出 → clear registry → ToolResult(success=False, error=...) |
| 无 `@agent` 声明 | `spawn_from_script` 步骤 3 | raise `ValueError` → ToolResult(success=False, error=...) |
| `subscribe` 引用未知 agent | `spawn_from_script` 步骤 3 | raise `ValueError` → ToolResult(success=False, error=...) |
| `@agent` 重复注册同一 name | `@agent` 装饰器内部 | raise `ValueError` → 脚本加载失败 → catch 在步骤 2 |
| `subscribe` 自订阅 | `subscribe().to()` 内部 | raise `ValueError` → 脚本加载失败 |
| agent name 重复（两次 spawn 产生同名 agent）| `spawn_from_script` 步骤 5h | raise `ValueError`（如已有同名非 FINISHED agent）→ 回滚 |
| factory 调用失败 | `spawn_from_script` 步骤 5a | 回滚已创建的 Runtime → ToolResult(success=False, error=...) |
| AgentRuntime 创建失败 | `spawn_from_script` 步骤 5c | 同 factory 失败 |
| `_inject_runtime_tools` 失败 | `spawn_from_script` 步骤 5f | 回滚已创建的 Runtime → ToolResult(success=False, error=...)；**注意**：已成功注入的 agent 的 DI 容器修改无法回滚——实践中 `_inject_runtime_tools` 失败只可能发生在内存耗尽等灾难性场景 |
| `asyncio.create_task` 失败 | `spawn_from_script` 步骤 7 | 已启动的 task 无法回滚（asyncio 无 kill API）。后续 batch 可考虑 Task 取消机制 |
| entry_prompt 投递失败 | `spawn_from_script` 步骤 9 | agent 在 receive() 中永久阻塞。后续 batch 引入超时机制 |
| `talk_to` 目标 pid 不存在 | `Kernel.send_input()` | WARNING 日志 → ToolResult(success=True)（幂等：消息丢失但不崩溃） |
| `end_workflow` flag 不存在 | `Kernel.end_workflow()` | `workflow_table.get(flag, [])` 返回空列表 → ToolResult(success=True, killed=[]) |
| `finish_agent` 自身已 FINISHED | `Kernel.kill()` | kill 检查 state != FINISHED → 跳过 → ToolResult(success=True) |

### 6.2 spawn_from_script 回滚边界

```
已创建的 AgentRuntime:
  ├─ runtime_table[pid] 已存在
  ├─ input_queues[pid] 已存在
  ├─ asyncio Task 可能已启动（步骤 7 在步骤 6-9 之间）
  └─ 回滚时需要清理:
       ├─ runtime_table.pop(pid)
       ├─ input_queues.pop(pid)
       └─ parent.children.remove(pid)（如有 parent）

无法回滚的:
  └─ 已启动的 asyncio Task（Python asyncio 不支持强制 kill Task）
     但 Task 中 agent 在 adapter.receive() 处阻塞 —— 其 input_queue
     已被移除，所以 receive() 永远不会返回。Task 成为僵尸协程。
     后续 batch 可通过 Task.cancel() + CancelledError catch 解决。
```

---

## 七、测试策略

### 7.1 测试框架与模式

与现有测试保持一致：pytest + 手动 mock（不使用 mock 库）。

### 7.2 单元测试场景

#### `decorators.py`

| 测试 | 验证点 |
|------|--------|
| `@agent` 注册到 `_agent_registry` | name / entry_prompt / metadata / factory 正确存储 |
| `@agent` 重复注册同一 name → ValueError | 异常消息正确 |
| `@agent` metadata 默认值 | 不传 metadata → metadata={} |
| factory 被正确保存 | 调用 factory → 返回 Harness |
| `subscribe("A").to("B")` 追加到 `_subscription_registry` | SubRecord(subscriber="A", publisher="B") |
| `subscribe("A").to("B")` 多次调用 | 累加，两次 SubRecord |
| `subscribe` 自订阅 → ValueError | `subscribe("A").to("A")` raise |
| `clear()` 清空 registries | 两个 registry 被清空 |
| importlib 加载后 registry 被填充 | 模拟完整加载流程 |

#### `CompositeSystemToolProvider`

| 测试 | 验证点 |
|------|--------|
| `get_tools()` 合并 user + runtime | 返回列表包含两者，runtime 在后 |
| `execute()` 先查 runtime tool | runtime tool 被调用 |
| `execute()` 回退到 user provider | 不在 runtime 中的 tool 从 user 执行 |
| `execute()` 两者都无 → KeyError | 抛出 KeyError |
| 无 user_provider → 自动创建 DefaultSystemToolProvider | get_tools 含默认工具 |
| 空 runtime_tools → 仅 user tools | 正确返回 |
| Runtime tool 同名覆盖 | 第二个同名注册 skip with warning |

#### 5 个 Runtime Tool

| 测试 | 验证点 |
|------|--------|
| `SpawnWorkflowTool.get_definition()` | name / description / parameters 正确 |
| `SpawnWorkflowTool.execute()` 成功 | kernel.spawn_from_script 被调用，返回 JSON 含 workflow_flag + agents |
| `SpawnWorkflowTool.execute()` 脚本不存在 | ToolResult(success=False, error=...) |
| `EndWorkflowTool.execute()` 成功 | kernel.end_workflow 被调用，返回 {ok, killed: [...]} |
| `EndWorkflowTool.execute()` flag 不存在 | killed=[] |
| `FinishAgentTool.execute()` 成功 | kernel.finish_agent 被调用（传入自身 pid） |
| `TalkToTool.execute()` 成功 | kernel.send_input 被调用，返 {ok, target} |
| `TalkToTool.execute()` target 不存在 | kernel.send_input warning 日志 |
| `ListAgentsTool.execute()` 成功 | kernel.list_agents 被调用 |

#### `Kernel.spawn_from_script()`

| 测试 | 验证点 |
|------|--------|
| 加载含 2 agent 的脚本 | 创建 2 个 AgentRuntime, runtime_table 有 2 条记录 |
| 返回值的 workflow_flag | wf_001 格式正确 |
| 返回值的 agents 列表 | 每个 agent 含 pid / parent / metadata |
| oneshot agent（无 subscribe）| mode="oneshot" |
| continuous agent（有 subscribe）| mode="continuous" |
| `_pending_subscriptions` 暂存 | subscribe 关系正确存入 |
| workflow_table 记录 | workflow_flag → pid 列表 |
| entry_prompt 投递 | agent 的 receive() 返回 entry_prompt UserRequest |
| parent.children 记录 | spawn_from_script(parent=root) → root.children 含子 pid |
| 脚本无 @agent → ValueError | 异常消息含 script_path |
| subscribe 引用未知 agent → ValueError | 异常消息指出未知 name |
| factory 失败 → 回滚 | 已创建的 Runtime 被清理 |
| `entry_prompt` 为空字符串 | agent 收到 `UserRequest(text="")` → `_should_exit()` 检测空 text → 立即退出。需要验证此行为或拒绝空 entry_prompt |
| 两次 spawn 同一脚本 | 两次都成功（registry 隔离）；第二次 spawn 的 agent 使用不同 workflow_flag，不会覆盖第一次的 runtime_table 条目 |
| 两次 spawn 产生同名 agent | raise ValueError（步骤 5h 重复检查）|
| `_inject_runtime_tools` 被调用 | container.resolve(SystemToolProvider) 返回 Composite |

#### `Kernel._inject_runtime_tools()`

| 测试 | 验证点 |
|------|--------|
| 注入后 SystemToolProvider 为 Composite | resolve 返回 CompositeSystemToolProvider |
| 用户原有工具保留 | Composite.get_tools() 包含用户工具 |
| Runtime tools 可用 | Composite.get_tools() 包含 5 个 Runtime tool |
| 执行 spawn_workflow 成功 | Composite.execute("spawn_workflow", ...) 成功 |

#### `spawn_root` 同步更新

| 测试 | 验证点 |
|------|--------|
| root agent 获得 Runtime tools | root 的 SystemToolProvider 包含 spawn_workflow 等 |

### 7.3 集成测试场景

| 测试 | 验证点 |
|------|--------|
| 完整 spawn_from_script 流程 | 加载脚本 → 创建 agents → 运行到 FINISHED |
| 子 agent oneshot 自动退出 | oneshot agent 一轮后 FINISHED |
| 子 agent continuous 等待输入 | continuous agent 一轮后在 receive() 阻塞 |
| talk_to 发送消息 | 父 agent 通过 send_input 发送 → 子 agent receive 收到 |
| end_workflow 终止所有子 agent | 所有子 agent 收到 sentinel → FINISHED |
| parent.children 正确跟踪 | spawn 后 children 列表正确，agent FINISHED 后子记录保留 |

### 7.4 需要的 Mock/Stub

| Mock 对象 | 最小接口 | 用途 |
|----------|---------|------|
| `MockKernel` | runtime_table / input_queues / spawn_from_script / send_input / kill / ... | Tool 单元测试 |
| `MockHarness` | container 属性 + call_llm | spawn_from_script 中 factory 返回值 |
| `MockConsole` | async send(event) | Kernel 测试 |
| `MockAsyncLLM` | async __call__(msgs, tools) → Response | AgentRuntime run() 测试 |

### 7.5 临时测试文件

测试需要创建临时 `.py` 文件作为 workflow 脚本：

```python
# conftest.py 或测试文件中的 fixture
import tempfile
import os

@pytest.fixture
def workflow_script():
    """创建最小 workflow 脚本，返回其路径。"""
    content = '''
from harness.di import Harness
from harness.core.container import DIContainer
from harness.runtime.decorators import agent, subscribe

@agent("collector", entry_prompt="采集数据")
def assemble_collector():
    container = DIContainer()
    return Harness.from_container(container, call_llm=None)

@agent("analyzer", entry_prompt="分析数据")
def assemble_analyzer():
    container = DIContainer()
    return Harness.from_container(container, call_llm=None)

subscribe("analyzer").to("collector")
'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)
```

### 7.6 现有测试回归

Batch 2 **不改动任何 Batch 0/1 已动过的文件**（除 `kernel.py` 新增方法和 `__init__.py` 追加 re-export）。

- `pytest` 全部现有测试继续通过
- 无 import 错误（新增模块不影响已有 import 路径）
- `kernel.py` 新增的 `_inject_runtime_tools()` 在 `spawn_root()` 中调用——验证 root agent 的行为不变

---

## 八、验收标准

### AC-2.1 decorators.py
- [ ] `_agent_registry` 和 `_subscription_registry` 模块级全局变量定义
- [ ] `@agent(name, entry_prompt, metadata?)` 装饰器可用，注册到 `_agent_registry`
- [ ] `@agent` 重复注册同一 name → raise ValueError
- [ ] `subscribe(subscriber).to(publisher)` 函数可用，追加到 `_subscription_registry`
- [ ] `subscribe` 自订阅 → raise ValueError
- [ ] `SubRecord` dataclass 定义（subscriber / publisher）

### AC-2.2 tools.py
- [ ] `CompositeSystemToolProvider` 实现 SystemToolProvider Protocol（duck typing）
- [ ] `CompositeSystemToolProvider.get_tools()` 合并 user + runtime tools
- [ ] `CompositeSystemToolProvider.execute()` runtime tool 优先，回退 user provider
- [ ] `SpawnWorkflowTool(BaseTool)` — get_definition / execute 完整
- [ ] `EndWorkflowTool(BaseTool)` — get_definition / execute 完整
- [ ] `FinishAgentTool(BaseTool)` — get_definition / execute 完整（捕获 pid）
- [ ] `TalkToTool(BaseTool)` — get_definition / execute 完整（捕获 from_pid）
- [ ] `ListAgentsTool(BaseTool)` — get_definition / execute 完整
- [ ] `create_runtime_tools(kernel, pid)` 工厂函数返回 5 个 tool 实例

### AC-2.3 Kernel.spawn_from_script()
- [ ] `spawn_from_script(script_path, parent=None)` 方法定义
- [ ] importlib 脚本加载 + registry 清空 + 固定模块名覆盖
- [ ] `subscribe` 引用校验（引用的 name 必须在 `_agent_registry` 中）
- [ ] 订阅关系暂存到 `_pending_subscriptions`
- [ ] 每个 @agent 创建 AgentRuntime（含 mode 判定、KBA 挂载、call_llm 提取+桥接）
- [ ] `_inject_runtime_tools()` 在 `_init_orchestrator()` 之前调用
- [ ] 父子关系记录（parent.children.append）
- [ ] asyncio Task 启动 + done_callback 注册
- [ ] workflow_table 记录
- [ ] entry_prompt 投递（Task 启动之后）
- [ ] 返回值 {workflow_flag, agents: [{pid, parent, metadata}]}
- [ ] 回滚：factory 失败 → 清理已创建 Runtime
- [ ] 空 @agent 注册表 → raise ValueError

### AC-2.4 Kernel._inject_runtime_tools()
- [ ] `_inject_runtime_tools(container, pid)` 方法定义
- [ ] 获取用户原有 SystemToolProvider（不存在则 None）
- [ ] 创建 CompositeSystemToolProvider 并注册到 DI 容器
- [ ] spawn_root 中调用 `_inject_runtime_tools`

### AC-2.5 包结构
- [ ] `harness/runtime/decorators.py` 创建
- [ ] `harness/runtime/tools.py` 创建
- [ ] `harness/runtime/__init__.py` 新增 `agent`, `subscribe`, `CompositeSystemToolProvider`, `create_runtime_tools` 及 5 个 Tool 类 re-export

### AC-2.6 现有测试不退化
- [ ] `pytest` 全部现有测试通过
- [ ] 无 import 错误

### AC-2.7 新测试
- [ ] decorators 测试 ≥ 8 条
- [ ] CompositeSystemToolProvider 测试 ≥ 7 条
- [ ] Runtime Tool 测试 ≥ 10 条（5 个 tool × ~2 条）
- [ ] Kernel.spawn_from_script 测试 ≥ 10 条
- [ ] Kernel._inject_runtime_tools 测试 ≥ 3 条
- [ ] 集成测试 ≥ 4 条

---

## 九、与后续 Batch 的接口约定

### 9.1 Batch 2 → Batch 3

```
暴露:
  from harness.runtime.decorators import agent, subscribe
  from harness.runtime.tools import (
      CompositeSystemToolProvider,
      SpawnWorkflowTool,
      EndWorkflowTool,
      FinishAgentTool,
      TalkToTool,
      ListAgentsTool,
      create_runtime_tools,
  )
  # Kernel 新增: spawn_from_script(script_path, parent=None)
  # Kernel 新增字段: _pending_subscriptions: list[tuple[str, str]]

Batch 3 依赖:
  - _pending_subscriptions 在 MessageBus 创建后注册为正式 subscribe 关系
  - spawn_from_script 中订阅暂存逻辑在 Batch 3 升级为直接调用
    message_bus.subscribe()
  - _on_agent_finished 从 stub 升级为完整实现（child_finished + 级联终止）
  - KBA.send() 从内联降级路由切换到 MessageBus.publish()
  - Runtime.run_from_script() Mode B 入口（复用 spawn_from_script）
```

### 9.2 职责边界（本 Batch 不做的内容）

以下内容**不属于 Batch 2**，在后续 batch 中实现：

- ❌ `MessageBus` 类及 pub-sub 路由（Batch 3）
- ❌ 默认订阅（child_finished 推送）—— `_on_agent_finished` 仍为 Batch 1 stub（Batch 3）
- ❌ 流式订阅的实际消息路由—— subscribe 声明的效果在 Batch 3 才生效（Batch 3）
- ❌ 级联终止（Batch 3）
- ❌ 静默检测完整实现（Batch 3）
- ❌ `Runtime.run_from_script()` Mode B 入口（Batch 3）
- ❌ KBA 降级路由 → MessageBus 切换（Batch 3）
- ❌ `/agents` `/kill` `/end` `/exit` `/talk` 命令解析（Batch 4）
- ❌ `_handle_system_input` 完整实现（Batch 4）
- ❌ SIGINT 信号处理改进（Batch 4）
- ❌ ContextAssembler system prompt 注入 Runtime tool 说明（Batch 5）
- ❌ 子 agent 中间输出可观测（read_log tool）（Batch 5）
