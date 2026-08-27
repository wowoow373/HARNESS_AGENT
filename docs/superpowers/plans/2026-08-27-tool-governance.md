# 工具治理层（Tool Governance Layer）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在编排器与 ToolRouter 之间插入一个工具治理层，内置超时/重试弹性策略与 Gate 人工审批，接口不变（`Tool` / `ToolProvider` / `MCPAdapter` / `ToolRouter` 零改动）。

**Architecture:** 治理层 `ToolGovernanceLayer` 是每 agent 一个实例的 async 包装层，`await execute(name, args)` 内部依次做 Gate 检查（高风险工具经 `ApprovalBroker` 走前台 `/approve` `/deny` 审批）、重试退避、`asyncio.to_thread` + `wait_for` 超时，最终把所有故障收敛为 `ToolResult(success=False)`。策略由进程级 `PolicyRegistry`（代码注册，精确名 > fnmatch 通配 > 默认）提供。

**Tech Stack:** Python 3.10+（`asyncio`、`fnmatch`、`secrets`、`dataclasses`）；pytest（`asyncio.run()` 包装异步测试，不依赖 pytest-asyncio 插件）。

---

## 实现偏差说明（相对设计文档）

设计文档将 `ApprovalBroker` 列在 `harness/core/governance/approval.py`。但项目分层是 `interfaces → core → components → runtime`（core 不得 import runtime）。`ApprovalBroker` 需要：①发 `ApprovalRequested` 事件（SystemEvent，定义在 `runtime/types.py`）；②被 `Kernel._handle_system_input` 命令循环回调。它是 runtime 层概念（"挂 Kernel"本身即暗示）。因此：

- `ApprovalBroker` → `harness/runtime/approval.py`
- `ToolPolicy` / `RetryPolicy` / `PolicyRegistry` / `ToolGovernanceLayer` → `harness/core/governance/`
- `ToolGovernanceLayer` 只依赖 broker 的鸭子接口（`request` / `resolve` / `cancel`），不 import runtime 具体类

分层正确，行为不变。

## 文件结构

**新增：**

| 文件 | 职责 |
|---|---|
| `harness/core/governance/__init__.py` | 包导出 |
| `harness/core/governance/policy.py` | `ToolPolicy` / `RetryPolicy` dataclass + `PolicyRegistry` + 模块级单例 `policy_registry` + runtime 工具名常量 |
| `harness/core/governance/layer.py` | `ToolGovernanceLayer`：`async execute()`，编排 gate → retry → timeout |
| `harness/runtime/approval.py` | `ApprovalBroker`：pending future 表 + 事件推送 + 裁决 |

**修改：**

| 文件 | 修改 |
|---|---|
| `harness/runtime/types.py` | 新增 `ApprovalRequested` / `CommandApprove` / `CommandDeny`，更新 union |
| `harness/runtime/cli_console.py` | 解析 `/approve` `/deny`；渲染 `ApprovalRequested` |
| `harness/runtime/kernel.py` | 创建 `ApprovalBroker` + 引用 `policy_registry`；命令循环加两分支 |
| `harness/runtime/agent_runtime.py` | `_init_orchestrator` 传入 registry/broker |
| `harness/core/async_orchestrator.py` | `_phase_init` 建 governance layer；`_phase_loop` 改 `await governance.execute()` |

**测试新增：**

| 文件 | 覆盖点 |
|---|---|
| `tests/governance/test_policy_registry.py` | 三级匹配、覆盖顺序、lookup 兜底 |
| `tests/governance/test_layer.py` | 超时/重试/success=False 不重试/异常吸收/CancelledError 穿透/gate |
| `tests/runtime/test_approval.py` | ApprovalBroker request/resolve/cancel/pending 清理 |
| `tests/runtime/test_approval_commands.py` | console 解析 + kernel 命令分发 |

---

## Task 1: PolicyRegistry — 策略模型与匹配

**Files:**
- Create: `harness/core/governance/__init__.py`
- Create: `harness/core/governance/policy.py`
- Test: `tests/governance/test_policy_registry.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/governance/test_policy_registry.py
"""PolicyRegistry 匹配优先级测试。"""

from harness.core.governance.policy import (
    PolicyRegistry, ToolPolicy, RetryPolicy, RUNTIME_TOOL_NAMES,
)


def test_lookup_returns_default_when_no_rule():
    r = PolicyRegistry()
    p = r.lookup("anything")
    assert isinstance(p, ToolPolicy)
    assert p.timeout == 60.0
    assert p.gate is False


def test_exact_match_beats_wildcard():
    r = PolicyRegistry()
    r.register("mcp_*", ToolPolicy(timeout=30))
    r.register("mcp_fs_read", ToolPolicy(timeout=5))
    assert r.lookup("mcp_fs_read").timeout == 5
    assert r.lookup("mcp_other").timeout == 30


def test_later_wildcard_wins():
    r = PolicyRegistry()
    r.register("mcp_*", ToolPolicy(timeout=10))
    r.register("mcp_fs_*", ToolPolicy(timeout=20))
    assert r.lookup("mcp_fs_read").timeout == 20


def test_set_default_overrides_builtin():
    r = PolicyRegistry()
    r.set_default(ToolPolicy(timeout=1))
    assert r.lookup("unmatched").timeout == 1


def test_runtime_tool_names_are_direct():
    # 模块级单例已预注册 runtime tools 为 executor="direct"
    from harness.core.governance.policy import policy_registry
    assert set(RUNTIME_TOOL_NAMES) == {
        "spawn_workflow", "end_workflow", "finish_agent",
        "talk_to", "list_agents",
    }
    for name in RUNTIME_TOOL_NAMES:
        assert policy_registry.lookup(name).executor == "direct"


def test_retry_policy_defaults():
    rp = RetryPolicy()
    assert rp.max_attempts == 1
    assert rp.backoff == "exponential"
    assert rp.retry_on == ("timeout", "exception")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/governance/test_policy_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.governance'`

- [ ] **Step 3: 实现 policy.py**

```python
# harness/core/governance/policy.py
"""工具治理策略模型与注册表。

ToolPolicy 描述单个工具的治理策略（超时/重试/Gate/执行器）。
PolicyRegistry 提供代码注册式策略匹配：
精确名 > fnmatch 通配（注册顺序后者优先）> 默认策略。

匹配顺序实现说明：先扫一遍精确名（后者覆盖前者），再扫一遍通配
（后者覆盖前者），精确名整体优先于通配，最后兜底默认策略。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    """工具失败重试策略。

    Attributes:
        max_attempts: 最大尝试次数（1 = 不重试）。
        backoff: 退避策略。"fixed" | "exponential"。
        base_delay: 基础退避延迟（秒）。
        retry_on: 可重试的失败类别集合（"timeout" / "exception"）。
    """
    max_attempts: int = 1
    backoff: str = "exponential"
    base_delay: float = 0.5
    retry_on: tuple = ("timeout", "exception")


@dataclass
class ToolPolicy:
    """单个工具的治理策略。

    Attributes:
        timeout: 单次执行超时（秒）。
        retry: 重试策略。
        gate: True = 需人工审批。
        approval_timeout: 审批等待超时（秒）。
        executor: "thread"（to_thread 包装，支持超时）| "direct"（事件循环内直接调用）。
    """
    timeout: float = 60.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    gate: bool = False
    approval_timeout: float = 300.0
    executor: str = "thread"


# Runtime 管理工具名。它们操作 kernel 内存态，必须在事件循环内直接调用
# （放线程会引入竞态），且默认 gate=False。
RUNTIME_TOOL_NAMES: tuple = (
    "spawn_workflow", "end_workflow", "finish_agent", "talk_to", "list_agents",
)


class PolicyRegistry:
    """代码注册式策略表（进程级）。

    用法::

        registry.register("delete_file", ToolPolicy(gate=True, timeout=10))
        registry.register("mcp_*", ToolPolicy(timeout=30))
        registry.set_default(ToolPolicy(timeout=60))
        policy = registry.lookup(tool_name)
    """

    def __init__(self):
        self._rules: list[tuple[str, ToolPolicy]] = []
        self._default = ToolPolicy()

    def register(self, pattern: str, policy: ToolPolicy) -> None:
        """注册一条规则。pattern 支持 fnmatch 通配（"delete_*"、"*"）。"""
        self._rules.append((pattern, policy))

    def set_default(self, policy: ToolPolicy) -> None:
        """覆盖内置默认策略。"""
        self._default = policy

    def lookup(self, tool_name: str) -> ToolPolicy:
        """按优先级返回策略，永不返回 None。

        优先级：精确名（后者优先）> 通配（后者优先）> 默认策略。
        """
        exact: ToolPolicy | None = None
        for pattern, policy in self._rules:
            if pattern == tool_name:
                exact = policy
        if exact is not None:
            return exact

        wildcard: ToolPolicy | None = None
        for pattern, policy in self._rules:
            if fnmatch.fnmatch(tool_name, pattern):
                wildcard = policy
        if wildcard is not None:
            return wildcard

        return self._default


# 进程级单例。Kernel 引用它；用户可在装配代码/workflow 脚本中直接 import 注册。
policy_registry = PolicyRegistry()
for _name in RUNTIME_TOOL_NAMES:
    policy_registry.register(_name, ToolPolicy(executor="direct"))
```

```python
# harness/core/governance/__init__.py
"""工具治理层包。

统一 Tool 接入层，内置超时/重试/Gate 审批。
"""

from .policy import (
    PolicyRegistry,
    RetryPolicy,
    ToolPolicy,
    policy_registry,
)

__all__ = [
    "PolicyRegistry",
    "RetryPolicy",
    "ToolPolicy",
    "policy_registry",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/governance/test_policy_registry.py -v`
Expected: PASS（6 tests passed）

- [ ] **Step 5: Commit**

```bash
git add harness/core/governance/__init__.py harness/core/governance/policy.py tests/governance/test_policy_registry.py
git commit -m "feat(governance): add ToolPolicy/RetryPolicy/PolicyRegistry with 3-tier matching"
```

---

## Task 2: ApprovalBroker — 审批 pending 管理与裁决

**Files:**
- Create: `harness/runtime/approval.py`
- Test: `tests/runtime/test_approval.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/runtime/test_approval.py
"""ApprovalBroker 单元测试。"""

import asyncio

from harness.runtime.approval import ApprovalBroker


class _MockConsole:
    def __init__(self):
        self.events = []
        self.send_called = 0

    async def send(self, event):
        self.send_called += 1
        self.events.append(event)


def run(coro):
    return asyncio.run(coro)


async def _test_request_creates_pending():
    console = _MockConsole()
    broker = ApprovalBroker(console=console)
    aid, fut = broker.request("root", "delete_file", {"path": "/x"})
    assert len(aid) == 4
    assert not fut.done()
    assert broker.pending_count == 1
    assert console.send_called == 1
    ev = console.events[0]
    assert ev.pid == "root"
    assert ev.tool_name == "delete_file"
    assert ev.approval_id == aid
    return aid, fut, broker


def test_request_creates_pending_and_event():
    aid, fut, broker = run(_test_request_creates_pending())
    assert fut.done() is False  # 未裁决


async def _test_resolve_approve():
    _, fut, broker = await _test_request_creates_pending()
    aid = broker.events[0]  # 占位，下面重取
    # 重新拿 aid
    console = _MockConsole()
    broker = ApprovalBroker(console=console)
    aid, fut = broker.request("root", "t", {})
    assert broker.resolve(aid, True) is True
    assert await fut is True
    assert broker.pending_count == 0
    return broker


def test_resolve_approve():
    broker = run(_test_resolve_approve())
    assert broker.pending_count == 0


def test_resolve_unknown_returns_false():
    broker = ApprovalBroker(console=_MockConsole())
    assert broker.resolve("ffff", True) is False


async def _test_cancel_cleans_pending():
    console = _MockConsole()
    broker = ApprovalBroker(console=console)
    aid, fut = broker.request("root", "t", {})
    broker.cancel(aid)
    assert broker.pending_count == 0
    assert fut.cancelled()
    return broker


def test_cancel_cleans_pending():
    broker = run(_test_cancel_cleans_pending())
    assert broker.pending_count == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/runtime/test_approval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.runtime.approval'`

- [ ] **Step 3: 实现 approval.py（先加类型，见 Task 3）**

> 注意：`ApprovalBroker` 依赖 `ApprovalRequested` 事件类型。该类型在 Task 3 加入 `runtime/types.py`。本步骤先写 broker，测试会因缺类型失败——因此在 Step 3 先一并实现 Task 3 的 types.py 类型（见下）。为让本 Task 独立可测，把类型定义放在本 Task 一并完成。

```python
# harness/runtime/approval.py
"""ApprovalBroker — 高风险工具审批的 pending 管理与裁决。

挂 Kernel（进程级单例）。管理 pending 审批请求（asyncio.Future 表），
request() 时推送 ApprovalRequested 事件到 SystemConsole，
resolve() 由 Kernel 命令循环（/approve /deny）回调。
"""

from __future__ import annotations

import asyncio
import secrets

from .types import ApprovalRequested


class ApprovalBroker:
    """审批请求的 pending 管理与裁决中枢。

    用法::

        broker = ApprovalBroker(console=console)
        approval_id, future = broker.request(pid, tool_name, args)
        approved = await asyncio.wait_for(future, timeout=300)
        # 命令循环里：broker.resolve(approval_id, approved=True)
    """

    def __init__(self, console):
        self._console = console
        self._pending: dict[str, asyncio.Future] = {}

    def request(self, pid: str, tool_name: str, args: dict) -> tuple[str, asyncio.Future]:
        """创建一个审批请求，推送事件，返回 (approval_id, future)。"""
        approval_id = self._new_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[approval_id] = future
        asyncio.create_task(self._console.send(ApprovalRequested(
            approval_id=approval_id,
            pid=pid,
            tool_name=tool_name,
            arguments=args,
        )))
        return approval_id, future

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """裁决一个 pending 请求。返回 False 表示无此请求或已裁决。"""
        future = self._pending.pop(approval_id, None)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def cancel(self, approval_id: str) -> None:
        """取消一个 pending 请求（agent 被 kill 等场景）。"""
        future = self._pending.pop(approval_id, None)
        if future is not None and not future.done():
            future.cancel()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def _new_id(self) -> str:
        """生成 4 位 hex 短 id，碰撞时重生成。"""
        while True:
            aid = secrets.token_hex(2)
            if aid not in self._pending:
                return aid
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/runtime/test_approval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/approval.py harness/runtime/types.py tests/runtime/test_approval.py
git commit -m "feat(governance): add ApprovalBroker with pending management and resolve/cancel"
```

---

## Task 3: runtime/types.py — 审批事件与命令类型

**Files:**
- Modify: `harness/runtime/types.py`

- [ ] **Step 1: 写失败测试（命令/事件类型构造）**

```python
# tests/runtime/test_approval_commands.py 的解析部分在 Task 5 补全；此处先验证类型存在
"""审批相关 SystemCommand / SystemEvent 类型冒烟测试。"""

from harness.runtime.types import (
    ApprovalRequested, CommandApprove, CommandDeny,
)


def test_approval_requested_fields():
    ev = ApprovalRequested(approval_id="a3f9", pid="root",
                           tool_name="delete_file", arguments={"path": "/x"})
    assert ev.approval_id == "a3f9"
    assert ev.pid == "root"
    assert ev.tool_name == "delete_file"
    assert ev.arguments == {"path": "/x"}


def test_command_approve_deny_fields():
    assert CommandApprove(approval_id="a3f9").approval_id == "a3f9"
    assert CommandDeny(approval_id="a3f9").approval_id == "a3f9"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'ApprovalRequested'`

- [ ] **Step 3: 实现类型**

在 `harness/runtime/types.py` 的 SystemCommand 段（`CommandError` 之后）插入：

```python
@dataclass
class CommandApprove:
    """/approve <id> — 批准一个工具审批请求。"""
    approval_id: str


@dataclass
class CommandDeny:
    """/deny <id> — 拒绝一个工具审批请求。"""
    approval_id: str
```

更新 SystemCommand union（在 `CommandError` 之后加）：

```python
SystemCommand = (
    CommandTalk | CommandKill | CommandListAgents
    | CommandEndWorkflow | CommandExit | CommandTalkDirect
    | CommandError
    | CommandApprove | CommandDeny
)
```

在 SystemEvent 段（`SystemMessage` 之后）插入：

```python
@dataclass
class ApprovalRequested:
    """高风险工具触发了人工审批请求。

    Attributes:
        approval_id: 短审批 id，供 /approve <id> /deny <id> 使用。
        pid: 发起工具调用的 agent pid。
        tool_name: 待审批的工具名。
        arguments: 工具调用参数（供前台展示）。
    """
    approval_id: str = ""
    pid: str = ""
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
```

更新 SystemEvent union：

```python
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
    | WorkflowFinished
    | AgentsListed
    | CommandError
    | SystemMessage
    | ApprovalRequested
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: PASS（2 tests passed）

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/types.py tests/runtime/test_approval_commands.py
git commit -m "feat(governance): add ApprovalRequested event and CommandApprove/CommandDeny types"
```

---

## Task 4: ToolGovernanceLayer — 超时/重试/Gate 编排

**Files:**
- Create: `harness/core/governance/layer.py`
- Test: `tests/governance/test_layer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/governance/test_layer.py
"""ToolGovernanceLayer 弹性策略测试。"""

import asyncio
import time

from harness.core.governance.layer import ToolGovernanceLayer
from harness.core.governance.policy import PolicyRegistry, ToolPolicy, RetryPolicy
from harness.interfaces.types import ToolResult


class _Tool:
    def __init__(self, delay=0.0, raise_err=None, result=None):
        self.delay = delay
        self.raise_err = raise_err
        self.result = result
        self.calls = 0

    def execute(self, name, args):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raise_err:
            raise self.raise_err
        if self.result is not None:
            return self.result
        return ToolResult(success=True, content=f"ok:{name}")


class _FakeRouter:
    def __init__(self, tools):
        self._tools = tools

    def has_tool(self, name):
        return name in self._tools

    def execute(self, name, args):
        return self._tools[name].execute(name, args)


class _FakeBroker:
    def __init__(self):
        self._pending = {}
        self.requested = []

    def request(self, pid, name, args):
        aid = f"a{len(self.requested):03x}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[aid] = fut
        self.requested.append((pid, name, args, aid))
        return aid, fut

    def resolve(self, aid, approved):
        fut = self._pending.pop(aid, None)
        if fut is not None and not fut.done():
            fut.set_result(approved)
            return True
        return False

    def cancel(self, aid):
        fut = self._pending.pop(aid, None)
        if fut is not None and not fut.done():
            fut.cancel()


def run(coro):
    return asyncio.run(coro)


async def _timeout_absorbed():
    tool = _Tool(delay=0.3)
    router = _FakeRouter({"slow": tool})
    reg = PolicyRegistry()
    reg.register("slow", ToolPolicy(timeout=0.05))
    layer = ToolGovernanceLayer(router, reg, None, pid="root")
    result = await layer.execute("slow", {})
    return result, tool


def test_timeout_absorbed():
    result, tool = run(_timeout_absorbed())
    assert result.success is False
    assert "timeout" in result.error
    assert tool.calls == 1


async def _retry_then_succeed():
    calls = {"n": 0}

    class Flaky:
        def execute(self, name, args):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("flaky")
            return ToolResult(success=True, content="ok")

    router = _FakeRouter({"flaky": Flaky()})
    reg = PolicyRegistry()
    reg.register("flaky", ToolPolicy(
        retry=RetryPolicy(max_attempts=3, backoff="fixed", base_delay=0.0)))
    layer = ToolGovernanceLayer(router, reg, None, pid="root")
    result = await layer.execute("flaky", {})
    return result, calls


def test_retry_then_succeed():
    result, calls = run(_retry_then_succeed())
    assert result.success is True
    assert calls["n"] == 3


async def _no_retry_on_business_failure():
    tool = _Tool(result=ToolResult(success=False, content="", error="nope"))
    router = _FakeRouter({"t": tool})
    reg = PolicyRegistry()
    reg.register("t", ToolPolicy(retry=RetryPolicy(max_attempts=3, base_delay=0.0)))
    layer = ToolGovernanceLayer(router, reg, None, pid="root")
    result = await layer.execute("t", {})
    return result, tool


def test_no_retry_on_business_failure():
    result, tool = run(_no_retry_on_business_failure())
    assert result.success is False
    assert tool.calls == 1


async def _exception_absorbed():
    tool = _Tool(raise_err=ValueError("boom"))
    router = _FakeRouter({"t": tool})
    reg = PolicyRegistry()
    layer = ToolGovernanceLayer(router, reg, None, pid="root")
    result = await layer.execute("t", {})
    return result


def test_exception_absorbed():
    result = run(_exception_absorbed())
    assert result.success is False
    assert "boom" in result.error


async def _gate_approve():
    tool = _Tool()
    router = _FakeRouter({"gated": tool})
    reg = PolicyRegistry()
    reg.register("gated", ToolPolicy(gate=True))
    broker = _FakeBroker()
    layer = ToolGovernanceLayer(router, reg, broker, pid="root")
    task = asyncio.create_task(layer.execute("gated", {}))
    while not broker.requested:
        await asyncio.sleep(0)
    aid = broker.requested[0][3]
    broker.resolve(aid, True)
    result = await task
    return result, tool


def test_gate_approve_executes():
    result, tool = run(_gate_approve())
    assert result.success is True
    assert tool.calls == 1


async def _gate_deny():
    tool = _Tool()
    router = _FakeRouter({"gated": tool})
    reg = PolicyRegistry()
    reg.register("gated", ToolPolicy(gate=True))
    broker = _FakeBroker()
    layer = ToolGovernanceLayer(router, reg, broker, pid="root")
    task = asyncio.create_task(layer.execute("gated", {}))
    while not broker.requested:
        await asyncio.sleep(0)
    aid = broker.requested[0][3]
    broker.resolve(aid, False)
    result = await task
    return result, tool


def test_gate_deny_blocks():
    result, tool = run(_gate_deny())
    assert result.success is False
    assert "denied" in result.error
    assert tool.calls == 0


async def _gate_timeout():
    tool = _Tool()
    router = _FakeRouter({"gated": tool})
    reg = PolicyRegistry()
    reg.register("gated", ToolPolicy(gate=True, approval_timeout=0.05))
    broker = _FakeBroker()
    layer = ToolGovernanceLayer(router, reg, broker, pid="root")
    result = await layer.execute("gated", {})
    return result, tool


def test_gate_timeout_denies():
    result, tool = run(_gate_timeout())
    assert result.success is False
    assert "approval timeout" in result.error
    assert tool.calls == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/governance/test_layer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.governance.layer'`

- [ ] **Step 3: 实现 layer.py**

```python
# harness/core/governance/layer.py
"""ToolGovernanceLayer — 统一工具接入层的弹性策略与 Gate 编排。

每 agent 一个实例，包裹 ToolRouter。execute() 顺序：
  1. lookup 策略
  2. Gate：policy.gate 为 True 时经 ApprovalBroker 等待人工审批
  3. 执行：executor="thread" 用 asyncio.to_thread + wait_for 超时；
     executor="direct" 在事件循环内同步调用（runtime tools）
  4. 重试：retry_on 匹配的失败类别按 backoff 重试
  5. 收敛：所有故障（超时/异常/拒绝/审批超时/重试耗尽）→ ToolResult(success=False)

唯一向上传播的异常是 asyncio.CancelledError（kill 语义必须能打断工具执行）。
"""

from __future__ import annotations

import asyncio
import logging

from ..interfaces.types import ToolResult

logger = logging.getLogger(__name__)


class _ToolTimeout(Exception):
    """内部异常：单次执行超时（用于 retry_on="timeout" 判定）。"""

    def __init__(self, tool_name: str, timeout: float):
        super().__init__(f"tool '{tool_name}' timeout after {timeout}s")
        self.tool_name = tool_name
        self.timeout = timeout


class ToolGovernanceLayer:
    """工具治理层：Gate + 超时 + 重试。

    Args:
        tool_router: 已装配的 ToolRouter。
        policy_registry: PolicyRegistry 实例。
        approval_broker: ApprovalBroker 实例或 None（None 时 gate 工具拒绝执行）。
        pid: 当前 agent pid（审批事件展示用）。
    """

    def __init__(self, tool_router, policy_registry, approval_broker, *, pid: str = ""):
        self._router = tool_router
        self._registry = policy_registry
        self._broker = approval_broker
        self._pid = pid

    def has_tool(self, name: str) -> bool:
        return self._router.has_tool(name)

    async def execute(self, name: str, args: dict) -> ToolResult:
        policy = self._registry.lookup(name)

        gate_result = await self._gate(name, args, policy)
        if gate_result is not None:
            return gate_result

        return await self._execute_with_resilience(name, args, policy)

    # ------------------------------------------------------------------
    # Gate
    # ------------------------------------------------------------------

    async def _gate(self, name, args, policy) -> ToolResult | None:
        if not policy.gate:
            return None
        if self._broker is None:
            return ToolResult(
                success=False,
                error=f"tool '{name}' requires approval but no broker configured",
            )
        approval_id, future = self._broker.request(self._pid, name, args)
        try:
            approved = await asyncio.wait_for(future, timeout=policy.approval_timeout)
        except asyncio.TimeoutError:
            self._broker.cancel(approval_id)
            return ToolResult(
                success=False,
                error=f"approval timeout after {policy.approval_timeout}s",
            )
        except asyncio.CancelledError:
            self._broker.cancel(approval_id)
            raise
        if approved:
            return None
        return ToolResult(success=False, error="denied by operator")

    # ------------------------------------------------------------------
    # 执行 + 重试
    # ------------------------------------------------------------------

    async def _execute_with_resilience(self, name, args, policy) -> ToolResult:
        attempts = max(1, policy.retry.max_attempts)
        last_error: str | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await self._run_once(name, args, policy)
                return result  # success 或 success=False（业务失败不重试）
            except _ToolTimeout as e:
                last_error = str(e)
                if "timeout" not in policy.retry.retry_on or attempt == attempts:
                    break
                await asyncio.sleep(self._delay(policy.retry, attempt))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if "exception" not in policy.retry.retry_on or attempt == attempts:
                    break
                await asyncio.sleep(self._delay(policy.retry, attempt))

        return ToolResult(success=False, error=last_error)

    async def _run_once(self, name, args, policy) -> ToolResult:
        if policy.executor == "direct":
            return self._router.execute(name, args)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._router.execute, name, args),
                timeout=policy.timeout,
            )
        except asyncio.TimeoutError:
            raise _ToolTimeout(name, policy.timeout)

    @staticmethod
    def _delay(retry, attempt: int) -> float:
        if retry.backoff == "fixed":
            return retry.base_delay
        # exponential: base_delay * 2^(attempt-1)
        return retry.base_delay * (2 ** (attempt - 1))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/governance/test_layer.py -v`
Expected: PASS（9 tests passed）

- [ ] **Step 5: Commit**

```bash
git add harness/core/governance/layer.py tests/governance/test_layer.py
git commit -m "feat(governance): add ToolGovernanceLayer with gate/timeout/retry resilience"
```

---

## Task 5: CliConsole — 解析 /approve /deny + 渲染事件

**Files:**
- Modify: `harness/runtime/cli_console.py`
- Test: `tests/runtime/test_approval_commands.py`（扩展）

- [ ] **Step 1: 写失败测试（解析）**

在 `tests/runtime/test_approval_commands.py` 追加：

```python
import asyncio

from harness.runtime.cli_console import CliConsole
from harness.runtime.types import (
    ApprovalRequested, CommandApprove, CommandDeny, CommandError,
)


def test_parse_approve():
    console = CliConsole()
    cmd = console._parse_command("/approve a3f9")
    assert isinstance(cmd, CommandApprove)
    assert cmd.approval_id == "a3f9"


def test_parse_deny():
    console = CliConsole()
    cmd = console._parse_command("/deny a3f9")
    assert isinstance(cmd, CommandDeny)
    assert cmd.approval_id == "a3f9"


def test_parse_approve_missing_id():
    console = CliConsole()
    cmd = console._parse_command("/approve")
    assert isinstance(cmd, CommandError)


def test_parse_deny_missing_id():
    console = CliConsole()
    cmd = console._parse_command("/deny")
    assert isinstance(cmd, CommandError)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: FAIL — `_parse_command` 对 `/approve` 返回 `CommandError(未知命令)`

- [ ] **Step 3: 实现解析 + 渲染**

在 `harness/runtime/cli_console.py` 顶部 import 列表补入 `ApprovalRequested` / `CommandApprove` / `CommandDeny`（现有 `from .types import (...)` 块）。

在 `_parse_command` 的 `/talk` 分支之后、`未知命令` 之前插入：

```python
        # /approve <id>
        if parts[0] == "/approve":
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /approve <id>"
                )
            return CommandApprove(approval_id=parts[1])

        # /deny <id>
        if parts[0] == "/deny":
            if len(parts) < 2:
                return CommandError(
                    command=text, error="用法: /deny <id>"
                )
            return CommandDeny(approval_id=parts[1])
```

在 `send()` 方法内、`CommandError` 分支之前插入渲染：

```python
        elif isinstance(event, ApprovalRequested):
            import json
            print(f"[审批] {event.pid} 请求执行工具 {event.tool_name}")
            print(f"  参数: {json.dumps(event.arguments, ensure_ascii=False)}")
            print(f"  /approve {event.approval_id} 批准    "
                  f"/deny {event.approval_id} 拒绝")
```

（`json` 已在文件顶部 import，若未 import 则需补 `import json`；以文件实际为准。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: PASS（6 tests passed）

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/cli_console.py tests/runtime/test_approval_commands.py
git commit -m "feat(governance): parse /approve /deny and render ApprovalRequested in CliConsole"
```

---

## Task 6: Kernel — 创建 broker/registry 引用 + 命令分发

**Files:**
- Modify: `harness/runtime/kernel.py`
- Test: `tests/runtime/test_approval_commands.py`（扩展 kernel 分发测试）

- [ ] **Step 1: 写失败测试（kernel 分发）**

在 `tests/runtime/test_approval_commands.py` 追加：

```python
from harness.runtime.kernel import Kernel


class _ConsoleSpy:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)

    async def receive(self):
        return None  # 不会被调用到


def test_kernel_has_broker_and_registry():
    k = Kernel(_ConsoleSpy())
    assert k.approval_broker is not None
    assert k.policy_registry is not None
    assert k.approval_broker.pending_count == 0


async def _test_kernel_dispatch_approve():
    k = Kernel(_ConsoleSpy())
    # 手动塞一个 pending 审批
    aid, fut = k.approval_broker.request("root", "t", {})
    # 构造 CommandApprove 直接走命令循环是异步的——改为直接调 resolve 验证桥接
    # （_handle_system_input 的 while 循环不便在单元测试驱动，此处验证 resolve 桥接）
    assert k.approval_broker.resolve(aid, True) is True
    assert await fut is True
    return k


def test_kernel_approval_bridge():
    k = asyncio.run(_test_kernel_dispatch_approve())
    assert k.approval_broker.pending_count == 0
```

> 说明：`_handle_system_input` 是 `while` 循环驱动的完整命令循环，单元测试不直接驱动它；本 Task 通过 `Kernel` 构造验证 broker/registry 已挂载，并通过 `resolve` 桥接验证裁决链路。命令循环内的两个分发分支由 Task 7 的集成/E2E 覆盖。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: FAIL — `AttributeError: 'Kernel' object has no attribute 'approval_broker'`

- [ ] **Step 3: 实现 Kernel 接线**

在 `Kernel.__init__` 中，`self._store = store` 之后插入：

```python
        # 工具治理层基础设施（进程级）：
        # - policy_registry：进程级策略单例（代码注册式）
        # - approval_broker：审批 pending 管理 + 事件推送 + 裁决
        from ..core.governance.policy import policy_registry
        from .approval import ApprovalBroker
        self.policy_registry = policy_registry
        self.approval_broker = ApprovalBroker(console=console)
```

在 `_handle_system_input` 中，`CommandError` 分支之前插入两个命令分发分支：

```python
            # ── CommandApprove / CommandDeny: 工具审批裁决 ──
            elif isinstance(command, (CommandApprove, CommandDeny)):
                approved = isinstance(command, CommandApprove)
                if self.approval_broker.resolve(command.approval_id, approved):
                    verdict = "批准" if approved else "拒绝"
                    await self._console.send(SystemMessage(
                        message=f"审批 {command.approval_id} 已{verdict}"
                    ))
                else:
                    await self._console.send(CommandError(
                        command=f"/{'approve' if approved else 'deny'} "
                                f"{command.approval_id}",
                        error="无此审批请求或已裁决",
                    ))
```

并更新 `_handle_system_input` 顶部 `from .types import (...)` 的 import 列表，加入 `CommandApprove` / `CommandDeny`（`SystemMessage` 需已在列表中，若未则补）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/runtime/test_approval_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/kernel.py tests/runtime/test_approval_commands.py
git commit -m "feat(governance): mount ApprovalBroker and PolicyRegistry on Kernel, dispatch approve/deny"
```

---

## Task 7: 编排器接线 — governance 包裹 ToolRouter

**Files:**
- Modify: `harness/core/async_orchestrator.py`
- Modify: `harness/runtime/agent_runtime.py`
- Test: `tests/governance/test_layer_integration.py`

- [ ] **Step 1: 写失败测试（编排器集成）**

```python
# tests/governance/test_layer_integration.py
"""编排器与治理层的集成接线测试。"""

import asyncio

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.governance.layer import ToolGovernanceLayer
from harness.core.governance.policy import PolicyRegistry
from harness.interfaces.types import ToolResult


class _Tool:
    def execute(self, name, args):
        return ToolResult(success=True, content=f"ok:{name}")


class _Provider:
    def __init__(self):
        self.tool = _Tool()

    def get_tools(self):
        from harness.interfaces.types import ToolDefinition
        return [ToolDefinition(name="t", description="d", parameters={})]

    def execute(self, name, args):
        return self.tool.execute(name, args)


class _Container:
    def __init__(self):
        self._provider = _Provider()

    def resolve(self, interface):
        if interface.__name__ == "SystemToolProvider":
            return self._provider
        raise ComponentNotRegisteredError(interface)


class ComponentNotRegisteredError(Exception):
    pass


class _Adapter:
    pid = "root"

    async def receive(self):
        from harness.interfaces.types import UserRequest
        return UserRequest(text="hi", session_id="s")

    async def send(self, event):
        pass


def run(coro):
    return asyncio.run(coro)


async def _orchestrator_builds_governance():
    orch = AsyncLifecycleOrchestrator(
        _Container(), adapter=_Adapter(), call_llm=None,
        policy_registry=PolicyRegistry(), approval_broker=None,
    )
    # 触发 _phase_init 以构建 governance（含 tool_router）
    await orch._phase_init()
    gov = orch._governance
    assert isinstance(gov, ToolGovernanceLayer)
    assert gov.has_tool("t")
    # 默认策略透传：正常执行返回成功
    result = await gov.execute("t", {})
    assert result.success is True
    assert result.content == "ok:t"
    return orch


def test_orchestrator_builds_governance():
    orch = run(_orchestrator_builds_governance())
    assert isinstance(orch._governance, ToolGovernanceLayer)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/governance/test_layer_integration.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'policy_registry'`

- [ ] **Step 3: 实现编排器接线**

在 `AsyncLifecycleOrchestrator.__init__` 签名增加两个参数（`session_log=None` 之后）：

```python
        # 工具治理层（可选注入；None 时用模块级单例 registry + None broker）
        policy_registry=None,
        approval_broker=None,
```

在 `__init__` 末尾（`_tool_call_records = session_log.tool_call_records` 之后）加入：

```python
        # 工具治理层：_phase_init 里包裹 tool_router 后设置
        self._governance: Optional[ToolGovernanceLayer] = None
        self._policy_registry = policy_registry
        self._approval_broker = approval_broker
```

在 `_phase_init` 的步骤 4（`build_tool_router` 之后、`self._cached_tools = available_tools` 之后）加入：

```python
        # 4a. 治理层包裹 tool_router（Gate + 超时 + 重试）
        from ..core.governance.policy import policy_registry as _default_reg
        from ..core.governance.layer import ToolGovernanceLayer
        self._governance = ToolGovernanceLayer(
            tool_router=tool_router,
            policy_registry=self._policy_registry or _default_reg,
            approval_broker=self._approval_broker,
            pid=getattr(self._adapter, "pid", ""),
        )
```

在 `_phase_loop` 的工具执行处（`if tool_router and tool_router.has_tool(...)` 块），把：

```python
                        try:
                            if tool_router and tool_router.has_tool(tc.function.name):
                                result = tool_router.execute(
                                    tc.function.name, args
                                )
                            else:
                                error = (
                                    f"ToolRouter has no tool '{tc.function.name}'. "
                                    f"Available: {sorted(tool_router._routes.keys()) if tool_router else 'none'}"
                                )
                        except Exception as e:
                            error = str(e)
```

改为：

```python
                        try:
                            if (self._governance is not None
                                    and self._governance.has_tool(tc.function.name)):
                                result = await self._governance.execute(
                                    tc.function.name, args
                                )
                            else:
                                error = (
                                    f"ToolRouter has no tool '{tc.function.name}'. "
                                    f"Available: {sorted(tool_router._routes.keys()) if tool_router else 'none'}"
                                )
                        except Exception as e:
                            error = str(e)
```

（文件顶部 `from ..interfaces.async_input_adapter import AsyncInputAdapter` 之后补 `from ..core.governance.layer import ToolGovernanceLayer` 供类型注解，或直接用字符串注解 `Optional["ToolGovernanceLayer"]`。本计划用字符串注解避免 import 重。）

- [ ] **Step 3b: 实现 agent_runtime 传参**

在 `harness/runtime/agent_runtime.py` 的 `_init_orchestrator` 里，构造 `AsyncLifecycleOrchestrator` 时追加两个参数：

```python
        self._orchestrator = AsyncLifecycleOrchestrator(
            container=self._harness.container,
            adapter=self.adapter,
            call_llm=call_llm,
            session_log=session_log,
            policy_registry=self._kernel.policy_registry,
            approval_broker=self._kernel.approval_broker,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/governance/test_layer_integration.py -v`
Expected: PASS

- [ ] **Step 5: 回归测试**

Run: `pytest -q`
Expected: 全量通过（重点确认 `tests/runtime/`、`tests/test_e2e_tool_flow.py`、`tests/test_tool_router.py`、MCP 相关测试无回归——治理层默认透传）

- [ ] **Step 6: Commit**

```bash
git add harness/core/async_orchestrator.py harness/runtime/agent_runtime.py tests/governance/test_layer_integration.py
git commit -m "feat(governance): wire ToolGovernanceLayer into orchestrator and AgentRuntime"
```

---

## Self-Review 结果

**Spec 覆盖检查：**
- 超时/重试弹性策略 → Task 4 ✓
- Gate 机制 + 人工审批 → Task 2/3/5/6 ✓
- 接口不变（Tool/ToolProvider/MCPAdapter/ToolRouter 零改动）→ 全计划仅改编排器调用点与新增文件 ✓
- 代码注册式策略 → Task 1 ✓
- 异步化包装 → Task 4/7 ✓
- 故障全部吸收 → Task 4 ✓
- 审批超时视为拒绝 → Task 4 ✓
- Console 命令式审批 → Task 5/6 ✓

**一致性检查：**
- `ApprovalRequested` 字段（approval_id/pid/tool_name/arguments）在 Task 2（构造）与 Task 3（定义）一致 ✓
- `ApprovalBroker.request()` 返回 `(approval_id, future)` 在 Task 2/4 一致 ✓
- `ToolGovernanceLayer` 构造签名（tool_router, policy_registry, approval_broker, pid=）在 Task 4/7 一致 ✓
- `CommandApprove`/`CommandDeny` 字段 `approval_id` 在 Task 3/5/6 一致 ✓
