# Batch 3: MessageBus + 消息订阅 + 并发 + 终止 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement MessageBus pub-sub routing, child_finished default subscription, cascade termination, quiescence detection, Mode B run_from_script entry, and upgrade KBA.send() from inline fallback routing to MessageBus.

**Architecture:** MessageBus is a pure mechanism layer (pub-sub routing table + message delivery) holding a reference to Kernel's input_queues dict. KBA.send() switches from direct input_queues manipulation to MessageBus.publish()/direct(). _on_agent_finished upgrades from stub to full impl with dedup logic (child_finished vs. cascading, parent exclusion from cascading). _monitor_quiescence upgrades from stub to actual idle detection. run_from_script is a new Runtime entry point.

**Tech Stack:** Python 3.12+ asyncio, dataclasses, pytest.

**Implementation order:** Bottom-up — MessageBus first (no deps), then types, then Kernel upgrades, then KBA upgrade, then Runtime.run_from_script, finally CliConsole formatting and __init__.py re-exports. Tests are interleaved TDD-style per component.

---

### Task 1: Add WorkflowFinished SystemEvent type and AgentRuntime.workflow_flag

**Files:**
- Modify: `harness/runtime/types.py` (add WorkflowFinished dataclass, update SystemEvent union)
- Modify: `harness/runtime/agent_runtime.py` (add workflow_flag attribute)

- [ ] **Step 1: Add WorkflowFinished dataclass to types.py**

Add after the `AgentOutput` class definition (after line 141 in current file):

```python
@dataclass
class WorkflowFinished:
    """Mode B: 所有 agent FINISHED，workflow 执行完成。

    run_from_script 在返回前推送此事件到 SystemConsole。

    Attributes:
        workflow_flag: workflow 标识（如 "wf_001"）。
        agents: agent 结果列表，每项含 pid/output/error/rounds/duration。
    """
    workflow_flag: str = ""
    agents: list = field(default_factory=list)
```

Update the `SystemEvent` union at the bottom of the file:

```python
# Union 类型别名，用于 SystemConsole.send() 签名
SystemEvent = (
    AgentSpawned | AgentStateChanged | AgentFinished
    | AgentOutput | RuntimeStarted | RuntimeStopped
    | WorkflowFinished  # Batch 3 新增
)
```

- [ ] **Step 2: Add workflow_flag attribute to AgentRuntime.__init__**

Add `self.workflow_flag` after `self.children: list[str] = []` (after current line 107):

```python
        # workflow 归属（Batch 3+，由 Kernel.spawn 设置）
        self.workflow_flag: Optional[str] = None
```

- [ ] **Step 3: Set workflow_flag in Kernel.spawn_root()**

Add after `self.workflow_table["wf_root"] = [pid]` (after current kernel.py line 134):

```python
        runtime.workflow_flag = "wf_root"
```

- [ ] **Step 4: Set workflow_flag in Kernel.spawn_from_script()**

Add inside the loop at step 5h, after `self.input_queues[name] = asyncio.Queue()` (after current kernel.py line 346):

```python
                runtime.workflow_flag = workflow_flag
```

- [ ] **Step 5: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runtime/types.py harness/runtime/agent_runtime.py harness/runtime/kernel.py
git commit -m "feat: add WorkflowFinished SystemEvent and AgentRuntime.workflow_flag

- New WorkflowFinished dataclass for Mode B workflow completion summary
- AgentRuntime.workflow_flag attribute set by Kernel during spawn
- Kernel.spawn_root() sets workflow_flag='wf_root'
- Kernel.spawn_from_script() sets workflow_flag on each spawned agent

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Create MessageBus class

**Files:**
- Create: `harness/runtime/message_bus.py`
- Test: `tests/runtime/test_message_bus.py`

- [ ] **Step 1: Write MessageBus class**

Create `harness/runtime/message_bus.py`:

```python
"""MessageBus — pub-sub 路由表 + 消息投递。

做机制不做策略。
- 维护 publisher → subscribers 映射
- publish() 时查表路由到 input_queues
- direct() 时跳过订阅表直接投递
- 提供 get_subscribers_of() 供 Kernel 级联终止查询
"""

from __future__ import annotations

import logging
from typing import Any

from ..interfaces.types import StopEvent, TextEvent

logger = logging.getLogger(__name__)


class MessageBus:
    """pub-sub 路由表 + 消息投递。

    职责边界：
    - 维护 publisher → subscribers 映射
    - publish() 时查表路由到 input_queues
    - direct() 时跳过订阅表直接投递
    - 提供 get_subscribers_of() 供 Kernel 级联终止查询
    - 不负责退出保护（在 KernelBridgeAdapter 层）
    - 不负责 sentinel 推送（在 Kernel 层）
    """

    def __init__(self, input_queues: dict[str, Any], console: Any = None):
        """初始化 MessageBus。

        Args:
            input_queues: Kernel 维护的 per-agent 输入队列
                          dict[str, asyncio.Queue]。
                          MessageBus 持有引用以投递消息。
            console: SystemConsole 引用，用于 publish() 内部 fallback 降级。
                     可选；为 None 时降级回调由调用方通过
                     on_no_subscriber 参数注入。
        """
        self._input_queues = input_queues
        self._console = console

        # publisher_pid → {subscriber_pid, ...}
        self._subscriptions: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def subscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """建立订阅：subscriber 接收 publisher 的每轮 TextEvent/StopEvent。

        纯内存操作（dict/set），同步方法。幂等——重复订阅同一对无副作用。

        Raises:
            ValueError: subscriber_pid == publisher_pid（不允许自订阅）
        """
        if subscriber_pid == publisher_pid:
            raise ValueError(
                f"Self-subscription not allowed: "
                f"'{subscriber_pid}' cannot subscribe to itself."
            )

        if publisher_pid not in self._subscriptions:
            self._subscriptions[publisher_pid] = set()

        self._subscriptions[publisher_pid].add(subscriber_pid)
        logger.debug(
            f"subscribe: '{subscriber_pid}' → '{publisher_pid}'"
        )

    async def publish(
        self,
        from_pid: str,
        event: Any,
        on_no_subscriber: Any = None,
    ) -> None:
        """向 from_pid 的所有订阅者广播 event。

        async 因为有降级路径需要 await on_no_subscriber。

        可路由事件类型：仅 TextEvent 和 StopEvent。调用方
        （KernelBridgeAdapter.send）在调用 publish 前过滤中间事件。

        无订阅者时：
        - event is TextEvent → 调用 on_no_subscriber；若为 None，
          尝试 self._console.send（内部 fallback）；都不可用则静默丢弃
        - event is StopEvent → 静默丢弃（不调用 on_no_subscriber）

        订阅者已 FINISHED（其 input_queue 已从 input_queues 中移除）→ 跳过
        """
        from .types import AgentOutput, InternalMessage

        subscribers = self._subscriptions.get(from_pid, set())

        # 过滤：只投递给 input_queues 中仍存在的订阅者
        active_subscribers = {
            pid for pid in subscribers
            if pid in self._input_queues
        }

        if not active_subscribers:
            # 无活跃订阅者
            if isinstance(event, TextEvent):
                # 降级优先级：on_no_subscriber > self._console > 静默丢弃
                if on_no_subscriber is not None:
                    await on_no_subscriber(
                        AgentOutput(pid=from_pid, content=event.content)
                    )
                elif self._console is not None:
                    await self._console.send(
                        AgentOutput(pid=from_pid, content=event.content)
                    )
                # else: 静默丢弃
            # StopEvent + 无订阅者 → 静默丢弃（无论 callbacks 如何）
            return

        # 构造内部消息
        msg = InternalMessage(
            from_pid=from_pid,
            content=event.content if isinstance(event, TextEvent) else "",
            metadata={"stop": True} if isinstance(event, StopEvent) else {},
        )

        # 广播到所有活跃订阅者
        for sub_pid in active_subscribers:
            self._input_queues[sub_pid].put_nowait(msg)
            logger.debug(f"publish: '{from_pid}' → '{sub_pid}'")

    def direct(self, target_pid: str, message: Any) -> None:
        """定向投递：跳过订阅表，直接投递到 target_pid 的队列。

        message 是由调用方（KernelBridgeAdapter）构造的 InternalMessage
        实例。direct() 直接入队，不做重新包装。

        纯 dict 查找 + asyncio.Queue.put_nowait，同步方法。

        Raises:
            KeyError: target_pid 不在 input_queues 中
        """
        if target_pid not in self._input_queues:
            raise KeyError(
                f"target_pid '{target_pid}' not found in input_queues"
            )

        self._input_queues[target_pid].put_nowait(message)
        logger.debug(f"direct: → '{target_pid}'")

    def get_subscribers_of(self, publisher_pid: str) -> list[str]:
        """返回订阅了 publisher_pid 的所有 pid 列表。

        无订阅者返回空列表。纯查询，无副作用。
        """
        return list(self._subscriptions.get(publisher_pid, set()))

    def unsubscribe(self, subscriber_pid: str, publisher_pid: str) -> None:
        """取消单个订阅关系。

        与 remove_publisher() 的区别：
        - unsubscribe("A", "B")：仅移除 A→B 这一条订阅关系
        - remove_publisher("B")：移除所有指向 B 的订阅关系

        如果订阅关系不存在，静默返回（幂等）。
        """
        if publisher_pid in self._subscriptions:
            self._subscriptions[publisher_pid].discard(subscriber_pid)
            if not self._subscriptions[publisher_pid]:
                del self._subscriptions[publisher_pid]
            logger.debug(
                f"unsubscribe: '{subscriber_pid}' × '{publisher_pid}'"
            )

    def remove_publisher(self, publisher_pid: str) -> None:
        """移除 publisher 的所有订阅关系。

        publisher FINISHED 时由 Kernel._on_agent_finished 调用。
        """
        removed = self._subscriptions.pop(publisher_pid, None)
        if removed:
            logger.debug(
                f"remove_publisher: '{publisher_pid}' "
                f"(removed {len(removed)} subscriber(s))"
            )
```

- [ ] **Step 2: Verify file parses correctly**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "from harness.runtime.message_bus import MessageBus; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/message_bus.py
git commit -m "feat: add MessageBus pub-sub routing table

- publisher → subscribers mapping (dict[str, set[str]])
- subscribe(subscriber, publisher): idempotent, rejects self-subscription
- publish(from_pid, event, on_no_subscriber=None): routes to active subscribers
  with TextEvent degradation path (on_no_subscriber > console > silent)
- direct(target_pid, message): bypasses subscription table
- get_subscribers_of(publisher_pid): query for cascade termination
- unsubscribe / remove_publisher: cleanup methods

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Write MessageBus unit tests

**Files:**
- Create: `tests/runtime/test_message_bus.py`

- [ ] **Step 1: Write MessageBus test file**

```python
# tests/runtime/test_message_bus.py

"""Tests for MessageBus pub-sub routing."""

import asyncio
import pytest
from harness.runtime.message_bus import MessageBus
from harness.interfaces.types import TextEvent, StopEvent


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole — records send calls with spy."""
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


def _make_bus(console=None):
    """Create a MessageBus with fresh input_queues."""
    return MessageBus(input_queues={}, console=console)


# ── subscribe ──


def test_subscribe_establishes_mapping():
    bus = _make_bus()
    bus.subscribe("analyzer", "collector")
    assert "analyzer" in bus._subscriptions["collector"]


def test_subscribe_idempotent():
    bus = _make_bus()
    bus.subscribe("analyzer", "collector")
    bus.subscribe("analyzer", "collector")
    assert bus._subscriptions["collector"] == {"analyzer"}


def test_subscribe_self_rejected():
    bus = _make_bus()
    with pytest.raises(ValueError, match="Self-subscription"):
        bus.subscribe("A", "A")


def test_subscribe_multiple_subscribers():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    assert bus._subscriptions["pub"] == {"A", "B"}


# ── publish ──


@pytest.mark.asyncio
async def test_publish_routes_to_active_subscriber():
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["analyzer"] = q
    bus.subscribe("analyzer", "collector")

    await bus.publish("collector", TextEvent(content="hello"))

    msg = q.get_nowait()
    assert msg.from_pid == "collector"
    assert msg.content == "hello"
    assert msg.metadata == {}


@pytest.mark.asyncio
async def test_publish_skips_finished_subscriber():
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["analyzer"] = q
    bus.subscribe("analyzer", "collector")

    # Remove queue to simulate agent FINISHED
    del bus._input_queues["analyzer"]

    await bus.publish("collector", TextEvent(content="hello"))
    # No crash, message silently skipped for finished subscriber


@pytest.mark.asyncio
async def test_publish_no_subscribers_text_event_calls_on_no_subscriber():
    bus = _make_bus()
    called_with = []

    async def _cb(event):
        called_with.append(event)

    await bus.publish("nobody", TextEvent(content="hi"),
                      on_no_subscriber=_cb)

    assert len(called_with) == 1
    assert called_with[0].pid == "nobody"
    assert called_with[0].content == "hi"


@pytest.mark.asyncio
async def test_publish_no_subscribers_text_event_fallback_console():
    console = _MockConsole()
    bus = _make_bus(console=console)

    await bus.publish("nobody", TextEvent(content="hi"))

    assert len(console.events) == 1
    assert console.events[0].pid == "nobody"
    assert console.events[0].content == "hi"


@pytest.mark.asyncio
async def test_publish_no_subscribers_text_event_silent_drop():
    """When no on_no_subscriber AND no console, silently drop."""
    bus = _make_bus()  # no console
    # Should not raise
    await bus.publish("nobody", TextEvent(content="hi"))


@pytest.mark.asyncio
async def test_publish_no_subscribers_stop_event_ignored():
    """StopEvent with no subscribers is silently dropped,
    even if callbacks exist."""
    called = []
    async def _cb(event):
        called.append(event)

    bus = _make_bus()
    await bus.publish("nobody", StopEvent(stop_reason="end"),
                      on_no_subscriber=_cb)
    assert len(called) == 0  # StopEvent ignores on_no_subscriber


@pytest.mark.asyncio
async def test_publish_no_subscribers_stop_event_ignored_with_console():
    console = _MockConsole()
    bus = _make_bus(console=console)
    await bus.publish("nobody", StopEvent(stop_reason="end"))
    assert len(console.events) == 0  # StopEvent ignores console fallback


@pytest.mark.asyncio
async def test_publish_on_no_subscriber_priority_over_console():
    """on_no_subscriber takes priority over internal console fallback."""
    console = _MockConsole()
    bus = _make_bus(console=console)
    called_with = []

    async def _cb(event):
        called_with.append(event)

    await bus.publish("nobody", TextEvent(content="hi"),
                      on_no_subscriber=_cb)

    assert len(called_with) == 1
    assert len(console.events) == 0  # console NOT called


# ── direct ──


def test_direct_delivers_to_target():
    bus = _make_bus()
    q = asyncio.Queue()
    bus._input_queues["target"] = q

    from harness.runtime.types import InternalMessage
    msg = InternalMessage(from_pid="sender", content="ping")
    bus.direct("target", msg)

    received = q.get_nowait()
    assert received.from_pid == "sender"
    assert received.content == "ping"


def test_direct_missing_target_raises_keyerror():
    bus = _make_bus()
    from harness.runtime.types import InternalMessage
    msg = InternalMessage(from_pid="sender", content="ping")

    with pytest.raises(KeyError, match="target_pid"):
        bus.direct("nonexistent", msg)


# ── get_subscribers_of ──


def test_get_subscribers_of_returns_list():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    result = bus.get_subscribers_of("pub")
    assert set(result) == {"A", "B"}


def test_get_subscribers_of_empty_for_unknown():
    bus = _make_bus()
    assert bus.get_subscribers_of("nobody") == []


# ── unsubscribe ──


def test_unsubscribe_removes_single_relationship():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    bus.unsubscribe("A", "pub")
    assert bus._subscriptions["pub"] == {"B"}


def test_unsubscribe_idempotent():
    bus = _make_bus()
    bus.unsubscribe("A", "pub")  # no crash
    bus.subscribe("A", "pub")
    bus.unsubscribe("A", "pub")
    bus.unsubscribe("A", "pub")  # second call, no crash


def test_unsubscribe_cleans_empty_set():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.unsubscribe("A", "pub")
    assert "pub" not in bus._subscriptions


# ── remove_publisher ──


def test_remove_publisher_clears_all():
    bus = _make_bus()
    bus.subscribe("A", "pub")
    bus.subscribe("B", "pub")
    bus.remove_publisher("pub")
    assert "pub" not in bus._subscriptions


def test_remove_publisher_idempotent():
    bus = _make_bus()
    bus.remove_publisher("nobody")  # no crash
```

- [ ] **Step 2: Run MessageBus tests**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/runtime/test_message_bus.py -v 2>&1
```

Expected: all 20 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_message_bus.py
git commit -m "test: add MessageBus unit tests (20 tests)

Coverage: subscribe (4), publish (9), direct (2), get_subscribers_of (2),
unsubscribe (3), remove_publisher (2).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Upgrade Kernel.__init__ to create MessageBus

**Files:**
- Modify: `harness/runtime/kernel.py:69,73`

- [ ] **Step 1: Replace message_bus = None with MessageBus creation**

Replace this at kernel.py line 69:
```python
        self.message_bus = None  # Batch 3 替换为 MessageBus
```

With:
```python
        # Batch 3: 创建 MessageBus。
        # input_queues 开始时为空，后续 spawn 中增量添加。
        # MessageBus 持有引用——新 agent queue 自动可见。
        from .message_bus import MessageBus
        self.message_bus = MessageBus(
            input_queues=self.input_queues,
            console=console,
        )
```

- [ ] **Step 2: Update _pending_subscriptions comment and add warning log**

Replace this at kernel.py line 73:
```python
        # Batch 2-3 预留
        self._pending_subscriptions: list[tuple[str, str]] = []
```

With:
```python
        # Batch 2 遗留清理：如有 Batch 2 期间暂存的订阅关系，
        # 记录 WARNING 后清空（MessageBus 已接管）。
        if self._pending_subscriptions:
            logger.warning(
                f"Clearing {len(self._pending_subscriptions)} stale pending "
                f"subscriptions from Batch 2 — they will NOT be registered "
                f"with MessageBus."
            )
        self._pending_subscriptions: list[tuple[str, str]] = []
```

Wait—this logic is wrong. `_pending_subscriptions` is initialized to `[]` on line 73, so the warning would never trigger. The fix is: don't init `_pending_subscriptions` before the check. Move the check after init, OR just add a comment that this is intentionally emptied with MessageBus creation:

Replace lines 69-73 with:
```python
        # Batch 3: 创建 MessageBus（替代 Batch 1-2 的 message_bus = None）。
        # input_queues 开始时为空，后续 spawn 中增量添加。
        # MessageBus 持有引用——新 agent queue 自动可见。
        from .message_bus import MessageBus
        self.message_bus = MessageBus(
            input_queues=self.input_queues,
            console=console,
        )

        # Batch 2 遗留：_pending_subscriptions 不再使用。
        # 订阅关系现在直接注册到 MessageBus（见 spawn_from_script）。
        # 任何在此 Kernel 实例创建前残留的 pending 订阅都不会被注册。
        self._pending_subscriptions: list[tuple[str, str]] = []
```

- [ ] **Step 3: Verify import and construction work**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "
from harness.runtime.kernel import Kernel
from harness.runtime.message_bus import MessageBus

class MC:
    async def receive(self): pass
    async def send(self, e): pass

k = Kernel(MC())
assert isinstance(k.message_bus, MessageBus)
assert k.message_bus._input_queues is k.input_queues  # same dict, not a copy
print('OK')
"
```

Expected: `OK`

- [ ] **Step 4: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass (MessageBus is created but none of the existing tests rely on it being None).

- [ ] **Step 5: Commit**

```bash
git add harness/runtime/kernel.py
git commit -m "feat: create MessageBus in Kernel.__init__

Replace message_bus=None with actual MessageBus instance.
MessageBus holds reference to input_queues dict — new agent
queues added by spawn_root/spawn_from_script are auto-visible.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Upgrade Kernel._on_agent_finished to full implementation

**Files:**
- Modify: `harness/runtime/kernel.py:_on_agent_finished`

- [ ] **Step 1: Replace _on_agent_finished stub with full implementation**

Replace the stub method (current kernel.py lines 465-486) with:

```python
    async def _on_agent_finished(self, runtime: 'AgentRuntime') -> None:
        """agent FINISHED 时的回调（由 Task.done_callback 触发）。

        执行顺序：
        1. 推送 AgentFinished 到 SystemConsole
        2. 默认订阅：通知父 agent（child_finished），含去重逻辑
        3. 级联终止：通过 MessageBus 查询订阅者，推送 __EXIT_SENTINEL__。
           父 agent 被显式排除（不受级联影响，顶层设计 Section 四.5）。
        4. 清理订阅表：remove_publisher
        """
        from .agent_runtime import AgentState

        duration = time.time() - runtime.started_at

        # ── 1. 推送 SystemConsole ──
        await self._console.send(AgentFinished(
            pid=runtime.pid,
            result=runtime.last_output,
            duration=duration,
            error=runtime.error,
        ))

        logger.info(
            f"_on_agent_finished: pid='{runtime.pid}' "
            f"duration={duration:.1f}s error={runtime.error}"
        )

        # ── 2. 默认订阅：通知父 agent ──
        # 去重：如果父 agent 显式 subscribe 了本 agent，跳过 child_finished。
        # 父通过 subscribe 流已经收到了子 agent 的输出，无需重复通知。
        if runtime.parent and runtime.parent.state != AgentState.FINISHED:
            parent_subscribed = (
                runtime.parent.pid
                in self.message_bus.get_subscribers_of(runtime.pid)
            )
            if not parent_subscribed:
                self.send_input(runtime.parent.pid, UserRequest(
                    text=(
                        f"[{runtime.pid}] "
                        f"{'异常退出' if runtime.error else '已完成'}。\n"
                        f"{runtime.last_output}"
                    ),
                    metadata={
                        "type": "child_finished",
                        "pid": runtime.pid,
                        "workflow_flag": runtime.workflow_flag,
                        "duration": duration,
                        "error": runtime.error,
                    },
                ))
                logger.debug(
                    f"_on_agent_finished: child_finished sent to "
                    f"parent='{runtime.parent.pid}'"
                )
            else:
                logger.debug(
                    f"_on_agent_finished: parent '{runtime.parent.pid}' "
                    f"already subscribed to '{runtime.pid}', "
                    f"skipping child_finished (dedup)"
                )

        # ── 3. 级联终止：通知显式订阅者 ──
        # 关键：父 agent 不受级联影响。
        # 即使父显式 subscribe 了子 agent，子 FINISHED 时父也不应被强制退出。
        # 顶层设计 Section 四.5 明确约定。
        parent_pid = runtime.parent.pid if runtime.parent else None
        subscribers = self.message_bus.get_subscribers_of(runtime.pid)
        for sub_pid in subscribers:
            # 跳过父 agent —— 父不受级联影响
            if sub_pid == parent_pid:
                logger.debug(
                    f"_on_agent_finished: skipping parent '{sub_pid}' "
                    f"in cascade (parent不受级联影响)"
                )
                continue

            sub_runtime = self.runtime_table.get(sub_pid)
            if sub_runtime and sub_runtime.state not in (
                AgentState.FINISHED, AgentState.TERMINATING
            ):
                sub_runtime.should_exit = True
                self.input_queues[sub_pid].put_nowait(__EXIT_SENTINEL__)
                logger.info(
                    f"_on_agent_finished: cascade sentinel sent "
                    f"to '{sub_pid}' (subscribed to '{runtime.pid}')"
                )

        # ── 4. 清理订阅表 ──
        self.message_bus.remove_publisher(runtime.pid)
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/kernel.py
git commit -m "feat: full _on_agent_finished with child_finished + cascade termination

- child_finished default subscription: parent receives completion notification
- Dedup: skip child_finished if parent also explicitly subscribes to child
- Cascade termination: subscribers receive __EXIT_SENTINEL__,
  parent explicitly excluded (top-level design Section 四.5)
- remove_publisher cleanup after cascade

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Upgrade Kernel._monitor_quiescence to full implementation

**Files:**
- Modify: `harness/runtime/kernel.py:_monitor_quiescence`

- [ ] **Step 1: Replace _monitor_quiescence stub with full implementation**

Replace the stub method (current kernel.py lines 488-499) with:

```python
    async def _monitor_quiescence(self) -> None:
        """静默检测监控协程。

        每秒检查一次：如果所有非 FINISHED agent 都处于 idle 状态
        （在 RUNNING 状态且在 adapter.receive() 中等待），
        则向全体推送 __EXIT_SENTINEL__ 触发优雅退出。

        Mode B 的核心结束机制：当所有 agent 完成工作进入 WAITING_INPUT，
        无人会再产生输出时，静默检测自动终止所有 agent。
        """
        from .agent_runtime import AgentState

        logger.info("_monitor_quiescence: started")
        while not self._shutdown:
            await asyncio.sleep(1)

            non_finished = [
                r for r in self.runtime_table.values()
                if r.state != AgentState.FINISHED
            ]

            if not non_finished:
                logger.info(
                    "_monitor_quiescence: all agents FINISHED, exiting"
                )
                return

            # 所有非 FINISHED agent 都在等待输入？
            all_idle = all(
                r._idle_for_quiescence() for r in non_finished
            )
            if all_idle:
                logger.info(
                    f"_monitor_quiescence: all {len(non_finished)} "
                    f"non-finished agent(s) idle, pushing sentinel"
                )
                for r in non_finished:
                    r.should_exit = True
                    if r.pid in self.input_queues:
                        self.input_queues[r.pid].put_nowait(
                            __EXIT_SENTINEL__
                        )
                return
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/kernel.py
git commit -m "feat: full _monitor_quiescence with idle detection

Replaces Batch 1 stub (poll all_finished) with actual idle detection:
1s polling loop checks all non-FINISHED agents via _idle_for_quiescence().
When all are idle (WAITING_INPUT), pushes __EXIT_SENTINEL__ to all.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Upgrade Kernel.spawn_from_script to register subscriptions to MessageBus

**Files:**
- Modify: `harness/runtime/kernel.py:spawn_from_script` (Step 4)

- [ ] **Step 1: Replace Step 4 (stash to _pending_subscriptions) with MessageBus registration**

Replace lines 287-291 (current kernel.py):
```python
        # ── Step 4: Stash subscription relationships ──
        for sub in decorators._subscription_registry:
            self._pending_subscriptions.append(
                (sub.subscriber, sub.publisher)
            )
```

With:
```python
        # ── Step 4: 注册订阅关系到 MessageBus ──
        for sub in decorators._subscription_registry:
            self.message_bus.subscribe(sub.subscriber, sub.publisher)
            logger.debug(
                f"spawn_from_script: subscribed "
                f"'{sub.subscriber}' → '{sub.publisher}'"
            )
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass. Batch 2 tests that use subscribe should still work — subscriptions are now registered to MessageBus instead of stashed, but since subscribe routing wasn't active in Batch 2, no behavioral change.

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/kernel.py
git commit -m "feat: register subscriptions to MessageBus in spawn_from_script

Instead of stashing subscription relationships in _pending_subscriptions,
now directly registers them to MessageBus.subscribe().

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Upgrade KernelBridgeAdapter.send() to use MessageBus

**Files:**
- Modify: `harness/runtime/bridge_adapter.py:send()` (lines 84-127)

- [ ] **Step 1: Replace KBA.send() inline fallback routing with MessageBus**

Replace the send method (current bridge_adapter.py lines 84-127) with:

```python
    async def send(self, event, target=None):
        """推送事件。

        路径分流：
        1. 退出保护：should_exit 为 True → 静默丢弃
        2. 事件类型过滤：非 TextEvent/StopEvent → 降级到 SystemConsole
        3. 定向投递：target 非空 → MessageBus.direct()
        4. 广播：target=None → MessageBus.publish()
        """
        if self._runtime.should_exit:
            return  # 退出保护：丢弃"最后一轮污染"

        # ── 事件类型分流 ──
        # 仅 TextEvent/StopEvent 参与 MessageBus 路由；
        # 中间事件（Thinking/ToolCall/ToolResult）直接降级到 SystemConsole
        if not isinstance(event, (TextEvent, StopEvent)):
            if isinstance(event, (ThinkingEvent, ToolCallEvent, ToolResultEvent)):
                await self._kernel._console.send(
                    AgentOutput(
                        pid=self._pid,
                        content=f"[{type(event).__name__}] {event}",
                    )
                )
            return

        if target is not None:
            # 定向投递：走 MessageBus.direct()
            msg = InternalMessage(
                from_pid=self._pid,
                content=event.content if isinstance(event, TextEvent) else "",
                metadata={"stop": True} if isinstance(event, StopEvent) else {},
            )
            self._kernel.message_bus.direct(target, msg)
        else:
            # pub-sub 路由：走 MessageBus.publish()
            await self._kernel.message_bus.publish(
                from_pid=self._pid,
                event=event,
                on_no_subscriber=(
                    self._kernel._console.send
                    if isinstance(event, TextEvent) else None
                ),
            )
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass. The behavioral change is that `target=None, TextEvent` now routes through MessageBus.publish() instead of directly to console.send(). For agents with no subscribers (the common case in existing tests), MessageBus.publish() calls on_no_subscriber → console.send, preserving the same end behavior.

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/bridge_adapter.py
git commit -m "feat: switch KBA.send() from inline fallback to MessageBus

- target=None → MessageBus.publish() with on_no_subscriber=console.send
  (preserves TextEvent→console degradation for agents with no subscribers)
- target=pid → MessageBus.direct()
- Exit guard and intermediate event filtering unchanged

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Add Runtime.run_from_script() Mode B entry

**Files:**
- Modify: `harness/runtime/runtime.py` (add run_from_script + _run_from_script_async)

- [ ] **Step 1: Update module docstring and add imports**

Update the docstring at the top of runtime.py and add `time` import:

```python
"""Runtime — 顶层入口。

创建 Kernel + 启动事件循环 + 注册信号处理。

用法:
    # Mode A（交互式单 agent）
    console = CliConsole()
    root_harness = Harness.from_container(container, call_llm=my_llm)
    Runtime(console).run(root_harness)

    # Mode B（直接启动 workflow 脚本）
    Runtime(console).run_from_script("workflow.py")
"""
```

Add `time` to the imports at the top:
```python
import time
```

Add `WorkflowFinished` to the imports from `.types`:
```python
from .types import RuntimeStarted, RuntimeStopped, WorkflowFinished
```

- [ ] **Step 2: Add run_from_script methods after run()**

Add after the `run()` method (after current line 64):

```python
    def run_from_script(self, script_path: str) -> None:
        """Mode B 入口 — 直接启动 workflow 脚本。

        不创建 root agent。从脚本加载 agent 并启动，等待所有 agent
        FINISHED（通过静默检测或自然结束），汇总结果后返回。

        用法:
            console = CliConsole()
            Runtime(console).run_from_script("workflow.py")
        """
        try:
            asyncio.run(self._run_from_script_async(script_path))
        except KeyboardInterrupt:
            # SIGINT 已在 _on_sigint 中处理
            pass

    async def _run_from_script_async(self, script_path: str) -> None:
        """Mode B 异步主流程。"""
        from .kernel import Kernel

        # 1. 创建 Kernel
        self._kernel = Kernel(self._console)

        # 2. 从脚本 spawn agent（无 parent）
        result = self._kernel.spawn_from_script(script_path, parent=None)

        logger.info(
            f"run_from_script: workflow_flag='{result['workflow_flag']}' "
            f"with {len(result['agents'])} agent(s)"
        )

        # 3. 启动系统输入处理
        #    (Mode B 下 Batch 4 将支持 /talk, /agents 等命令)
        task_sys = asyncio.create_task(
            self._kernel._handle_system_input()
        )

        # 4. 启动静默检测
        task_mon = asyncio.create_task(
            self._kernel._monitor_quiescence()
        )

        # 5. 注册信号处理
        handler = create_sigint_handler(self)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, handler)
            logger.debug("SIGINT handler registered (Mode B)")
        except NotImplementedError:
            logger.debug("SIGINT handler not available on this platform")

        # 6. 推送启动事件
        await self._console.send(RuntimeStarted())

        # 7. 等待全部完成
        try:
            await asyncio.gather(
                *self._kernel._tasks.values(),
                task_sys,
                task_mon,
                return_exceptions=True,
            )
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass

        # 8. 收集最终输出
        agents_results = []
        for pid, r in self._kernel.runtime_table.items():
            agents_results.append({
                "pid": pid,
                "output": r.last_output,
                "error": r.error,
                "rounds": r.round_count,
                "duration": (
                    time.time() - r.started_at if r.started_at else 0.0
                ),
            })

        # 9. 推送 WorkflowFinished
        await self._console.send(WorkflowFinished(
            workflow_flag=result["workflow_flag"],
            agents=agents_results,
        ))

        # 10. 推送停止事件
        await self._console.send(RuntimeStopped())
```

- [ ] **Step 3: Verify import**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "from harness.runtime.runtime import Runtime; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add harness/runtime/runtime.py
git commit -m "feat: add Runtime.run_from_script() Mode B entry

- No root agent; loads workflow script directly via spawn_from_script
- Ends via quiescence detection (all agents idle)
- Collects and emits WorkflowFinished with per-agent results
- Shares SIGINT handling with Mode A

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Add WorkflowFinished formatting to CliConsole

**Files:**
- Modify: `harness/runtime/cli_console.py`

- [ ] **Step 1: Add WorkflowFinished import**

Add `WorkflowFinished` to the import from `.types` (line 12-22):

```python
from .types import (
    AgentFinished,
    AgentOutput,
    AgentSpawned,
    AgentStateChanged,
    CommandTalk,
    RuntimeStarted,
    RuntimeStopped,
    WorkflowFinished,
    SystemCommand,
    SystemEvent,
)
```

- [ ] **Step 2: Add WorkflowFinished handler in send()**

Add before the final line of `send()` (before line 76, after `RuntimeStopped` handler):

```python
        elif isinstance(event, WorkflowFinished):
            print(f"[系统] Workflow {event.workflow_flag} 完成:")
            for agent in event.agents:
                status = "异常" if agent.get("error") else "正常"
                print(
                    f"  {agent['pid']:12} {status}  "
                    f"{agent['rounds']}轮  {agent['duration']:.1f}s"
                )
                output = agent.get("output", "")
                if output:
                    # 截断长输出，只显示前 200 字符
                    truncated = output[:200]
                    if len(output) > 200:
                        truncated += "..."
                    print(f"    → {truncated}")
```

- [ ] **Step 3: Verify no import errors**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "from harness.runtime.cli_console import CliConsole; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add harness/runtime/cli_console.py
git commit -m "feat: add WorkflowFinished formatting to CliConsole.send()

Displays per-agent pid, status, rounds, duration, and truncated
output for each agent in the finished workflow.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Update __init__.py with Batch 3 re-exports

**Files:**
- Modify: `harness/runtime/__init__.py`

- [ ] **Step 1: Add MessageBus and WorkflowFinished to re-exports**

Add `MessageBus` to imports from message_bus:
```python
from .message_bus import MessageBus
```

Add `WorkflowFinished` to imports from types:
```python
from .types import (
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
    WorkflowFinished,
    __EXIT_SENTINEL__,
)
```

Add `"MessageBus"` and `"WorkflowFinished"` to `__all__`:
```python
    "MessageBus",
    "WorkflowFinished",
```

- [ ] **Step 2: Verify all public API accessible**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "
from harness.runtime import (
    MessageBus, WorkflowFinished, Runtime, Kernel, AgentRuntime, AgentState,
    KernelBridgeAdapter, CliConsole, create_sigint_handler,
    InternalMessage, __EXIT_SENTINEL__,
    AgentOutput, AgentSpawned, AgentStateChanged, AgentFinished,
    RuntimeStarted, RuntimeStopped,
    CommandTalk, SystemCommand, SystemEvent,
    agent, subscribe, SubRecord,
    CompositeSystemToolProvider, SpawnWorkflowTool, EndWorkflowTool,
    FinishAgentTool, TalkToTool, ListAgentsTool, create_runtime_tools,
)
print('MessageBus:', MessageBus)
print('WorkflowFinished:', WorkflowFinished)
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 3: Commit**

```bash
git add harness/runtime/__init__.py
git commit -m "feat: re-export MessageBus and WorkflowFinished from harness.runtime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: Write _on_agent_finished unit tests

**Files:**
- Create: `tests/runtime/test_on_agent_finished.py`

- [ ] **Step 1: Write _on_agent_finished test file**

```python
# tests/runtime/test_on_agent_finished.py

"""Tests for Kernel._on_agent_finished full implementation."""

import asyncio
import time
import pytest
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.message_bus import MessageBus
from harness.runtime.types import (
    AgentFinished,  __EXIT_SENTINEL__,
)
from harness.interfaces.types import UserRequest


# ── Stubs / Helpers ──

class _MockConsole:
    """Mock SystemConsole with spy."""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


class _MockHarness:
    """Minimal harness stub."""
    def __init__(self):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = None


def _make_runtime(kernel, pid, mode="continuous", parent=None):
    """Create a minimal AgentRuntime for testing."""
    harness = _MockHarness()
    rt = AgentRuntime(
        pid=pid, mode=mode, harness=harness,
        kernel=kernel, parent=parent,
    )
    rt.last_output = f"output from {pid}"
    rt.error = None
    rt.started_at = time.time() - 5.0
    rt.workflow_flag = "wf_test"
    return rt


@pytest.fixture
def kernel():
    """Fixture: Kernel with mock console and empty agent tables."""
    console = _MockConsole()
    k = Kernel(console)
    return k


# ── Tests ──


@pytest.mark.asyncio
async def test_on_agent_finished_pushes_agent_finished_event(kernel):
    runtime = _make_runtime(kernel, "worker")
    kernel.runtime_table["worker"] = runtime
    kernel.input_queues["worker"] = asyncio.Queue()

    await kernel._on_agent_finished(runtime)

    finished_events = [
        e for e in kernel._console.events
        if isinstance(e, AgentFinished)
    ]
    assert len(finished_events) == 1
    assert finished_events[0].pid == "worker"
    assert finished_events[0].result == "output from worker"


@pytest.mark.asyncio
async def test_child_finished_sent_to_parent(kernel):
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    await kernel._on_agent_finished(child)

    # Parent should receive child_finished UserRequest
    msg = kernel.input_queues["parent"].get_nowait()
    assert isinstance(msg, UserRequest)
    assert msg.metadata["type"] == "child_finished"
    assert msg.metadata["pid"] == "child"
    assert msg.metadata["workflow_flag"] == "wf_test"


@pytest.mark.asyncio
async def test_child_finished_skipped_when_parent_subscribes_child(kernel):
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    # Parent explicitly subscribes to child
    kernel.message_bus.subscribe("parent", "child")

    await kernel._on_agent_finished(child)

    # Parent should NOT receive child_finished (dedup)
    assert kernel.input_queues["parent"].empty()


@pytest.mark.asyncio
async def test_child_finished_skipped_when_parent_finished(kernel):
    parent = _make_runtime(kernel, "parent")
    parent.state = AgentState.FINISHED
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    await kernel._on_agent_finished(child)

    # Parent is already FINISHED → no child_finished
    assert kernel.input_queues["parent"].empty()


@pytest.mark.asyncio
async def test_cascade_sends_sentinel_to_subscribers(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber should receive __EXIT_SENTINEL__
    item = kernel.input_queues["subscriber"].get_nowait()
    assert item is __EXIT_SENTINEL__
    assert sub.should_exit is True


@pytest.mark.asyncio
async def test_parent_excluded_from_cascade(kernel):
    """Top-level design Section 四.5: parent不受级联影响."""
    parent = _make_runtime(kernel, "parent")
    child = _make_runtime(kernel, "child", parent=parent)
    kernel.runtime_table["parent"] = parent
    kernel.runtime_table["child"] = child
    kernel.input_queues["parent"] = asyncio.Queue()
    kernel.input_queues["child"] = asyncio.Queue()

    # Parent also explicitly subscribes to child
    kernel.message_bus.subscribe("parent", "child")

    await kernel._on_agent_finished(child)

    # Parent should NOT receive sentinel (excluded from cascade)
    # AND should NOT receive child_finished (dedup — already subscribed)
    assert kernel.input_queues["parent"].empty()
    assert parent.should_exit is False  # Parent unaffected


@pytest.mark.asyncio
async def test_cascade_skips_terminating_subscriber(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    sub.state = AgentState.TERMINATING
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber is already TERMINATING → no sentinel sent
    assert kernel.input_queues["subscriber"].empty()
    # should_exit not changed (might already be True from prior exit signal)
    # Key: no duplicate sentinel in queue


@pytest.mark.asyncio
async def test_cascade_skips_finished_subscriber(kernel):
    pub = _make_runtime(kernel, "publisher")
    sub = _make_runtime(kernel, "subscriber")
    sub.state = AgentState.FINISHED
    kernel.runtime_table["publisher"] = pub
    kernel.runtime_table["subscriber"] = sub
    kernel.input_queues["publisher"] = asyncio.Queue()
    kernel.input_queues["subscriber"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Subscriber already FINISHED → no sentinel sent
    assert kernel.input_queues["subscriber"].empty()


@pytest.mark.asyncio
async def test_remove_publisher_called(kernel):
    pub = _make_runtime(kernel, "publisher")
    kernel.runtime_table["publisher"] = pub
    kernel.input_queues["publisher"] = asyncio.Queue()

    kernel.message_bus.subscribe("subscriber", "publisher")

    await kernel._on_agent_finished(pub)

    # Publisher's subscriptions should be cleaned up
    assert kernel.message_bus.get_subscribers_of("publisher") == []
```

- [ ] **Step 2: Run _on_agent_finished tests**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/runtime/test_on_agent_finished.py -v 2>&1
```

Expected: 9 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_on_agent_finished.py
git commit -m "test: add _on_agent_finished full implementation tests (9 tests)

Coverage: AgentFinished push, child_finished to parent, dedup
(parent subscribes child), parent FINISHED skip, cascade sentinel,
parent exclusion from cascade, TERMINATING/FINISHED subscriber skip,
remove_publisher cleanup.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 13: Write _monitor_quiescence unit tests

**Files:**
- Create: `tests/runtime/test_monitor_quiescence.py`

- [ ] **Step 1: Write _monitor_quiescence test file**

```python
# tests/runtime/test_monitor_quiescence.py

"""Tests for Kernel._monitor_quiescence full implementation."""

import asyncio
import pytest
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentRuntime, AgentState
from harness.runtime.types import __EXIT_SENTINEL__


# ── Stubs / Helpers ──

class _MockConsole:
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


class _MockHarness:
    def __init__(self):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = None


def _make_runtime(kernel, pid, mode="continuous"):
    harness = _MockHarness()
    rt = AgentRuntime(
        pid=pid, mode=mode, harness=harness,
        kernel=kernel,
    )
    rt.started_at = 10.0
    return rt


# ── Tests ──


@pytest.mark.asyncio
async def test_quiescence_returns_when_all_finished():
    """Monitors exits immediately when all agents are FINISHED."""
    console = _MockConsole()
    k = Kernel(console)

    rt = _make_runtime(k, "a")
    rt.state = AgentState.FINISHED
    k.runtime_table["a"] = rt
    k.input_queues["a"] = asyncio.Queue()

    # Should return immediately, no sleep needed
    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)


@pytest.mark.asyncio
async def test_quiescence_pushes_sentinel_when_all_idle():
    """When all non-FINISHED agents are idle, pushes sentinel to all."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_b = _make_runtime(k, "b")
    rt_a.state = AgentState.RUNNING
    rt_b.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle
    rt_b._idle_since = 100.0  # idle

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    # Should detect idle and push sentinels within 3 seconds
    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)

    # Both should have received sentinel
    assert rt_a.should_exit is True
    assert rt_b.should_exit is True

    qa = k.input_queues["a"].get_nowait()
    qb = k.input_queues["b"].get_nowait()
    assert qa is __EXIT_SENTINEL__
    assert qb is __EXIT_SENTINEL__


@pytest.mark.asyncio
async def test_quiescence_does_not_push_when_not_all_idle():
    """If any agent is still active (not idle), no sentinel is pushed."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_b = _make_runtime(k, "b")
    rt_a.state = AgentState.RUNNING
    rt_b.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle
    rt_b._idle_since = None   # NOT idle (e.g., in call_llm)

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    # The monitor polls every 1s. We'll check after 2 polls that
    # nothing was pushed, then set _shutdown to stop.
    async def _check_and_stop():
        await asyncio.sleep(2.5)
        # After 2+ polling cycles, a should still not have sentinel
        assert rt_a.should_exit is False
        assert rt_b.should_exit is False
        k._shutdown = True  # stop the monitor

    await asyncio.gather(
        k._monitor_quiescence(),
        _check_and_stop(),
    )


@pytest.mark.asyncio
async def test_quiescence_ignores_terminating_agent():
    """An agent in TERMINATING state is not counted as idle."""
    console = _MockConsole()
    k = Kernel(console)

    rt_a = _make_runtime(k, "a")
    rt_a.state = AgentState.RUNNING
    rt_a._idle_since = 100.0  # idle

    rt_b = _make_runtime(k, "b")
    rt_b.state = AgentState.TERMINATING  # not idle (not RUNNING)

    k.runtime_table["a"] = rt_a
    k.runtime_table["b"] = rt_b
    k.input_queues["a"] = asyncio.Queue()
    k.input_queues["b"] = asyncio.Queue()

    # rt_a is the only non-FINISHED RUNNING agent → is idle → sentinel pushed
    await asyncio.wait_for(k._monitor_quiescence(), timeout=3.0)

    assert rt_a.should_exit is True
    qa = k.input_queues["a"].get_nowait()
    assert qa is __EXIT_SENTINEL__
```

- [ ] **Step 2: Run _monitor_quiescence tests**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/runtime/test_monitor_quiescence.py -v 2>&1
```

Expected: 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_monitor_quiescence.py
git commit -m "test: add _monitor_quiescence full implementation tests (4 tests)

Coverage: all FINISHED immediate return, all idle triggers sentinel,
not-all-idle waits, TERMINATING agent excluded from idle check.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: Write KBA.send() upgrade tests

**Files:**
- Create: `tests/runtime/test_kba_send_upgrade.py`

- [ ] **Step 1: Write KBA.send() MessageBus routing test file**

```python
# tests/runtime/test_kba_send_upgrade.py

"""Tests for KBA.send() MessageBus routing (Batch 3 upgrade)."""

import asyncio
import pytest
from harness.runtime.bridge_adapter import KernelBridgeAdapter
from harness.interfaces.types import (
    TextEvent, StopEvent, ThinkingEvent, ToolCallEvent, ToolResultEvent,
)
from harness.runtime.types import InternalMessage, AgentOutput


# ── Stubs ──

class _MockRuntime:
    should_exit = False


class _MockConsole:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


class _MockMessageBus:
    """MessageBus spy — records method calls."""
    def __init__(self):
        self.publish_calls = []
        self.direct_calls = []

    async def publish(self, from_pid, event, on_no_subscriber=None):
        self.publish_calls.append({
            "from_pid": from_pid,
            "event": event,
            "on_no_subscriber": on_no_subscriber,
        })

    def direct(self, target_pid, message):
        self.direct_calls.append({
            "target_pid": target_pid,
            "message": message,
        })


class _MockKernel:
    def __init__(self):
        self._console = _MockConsole()
        self.message_bus = _MockMessageBus()


def _make_kba():
    kernel = _MockKernel()
    runtime = _MockRuntime()
    return KernelBridgeAdapter(pid="test", kernel=kernel, runtime=runtime), kernel


# ── Tests ──


@pytest.mark.asyncio
async def test_text_event_target_none_routes_to_publish():
    kba, kernel = _make_kba()
    await kba.send(TextEvent(content="hello"), target=None)

    assert len(kernel.message_bus.publish_calls) == 1
    call = kernel.message_bus.publish_calls[0]
    assert call["from_pid"] == "test"
    assert isinstance(call["event"], TextEvent)
    assert call["event"].content == "hello"
    # on_no_subscriber should be console.send for TextEvent
    assert call["on_no_subscriber"] is not None


@pytest.mark.asyncio
async def test_stop_event_target_none_routes_to_publish():
    kba, kernel = _make_kba()
    await kba.send(StopEvent(stop_reason="end"), target=None)

    assert len(kernel.message_bus.publish_calls) == 1
    call = kernel.message_bus.publish_calls[0]
    assert call["from_pid"] == "test"
    assert isinstance(call["event"], StopEvent)
    # on_no_subscriber should be None for StopEvent
    assert call["on_no_subscriber"] is None


@pytest.mark.asyncio
async def test_target_pid_routes_to_direct():
    kba, kernel = _make_kba()
    await kba.send(TextEvent(content="ping"), target="other")

    assert len(kernel.message_bus.direct_calls) == 1
    call = kernel.message_bus.direct_calls[0]
    assert call["target_pid"] == "other"
    assert isinstance(call["message"], InternalMessage)
    assert call["message"].from_pid == "test"
    assert call["message"].content == "ping"
    assert call["message"].metadata == {}


@pytest.mark.asyncio
async def test_stop_event_target_pid_has_stop_metadata():
    kba, kernel = _make_kba()
    await kba.send(StopEvent(stop_reason="end"), target="other")

    assert len(kernel.message_bus.direct_calls) == 1
    msg = kernel.message_bus.direct_calls[0]["message"]
    assert msg.content == ""
    assert msg.metadata == {"stop": True}


@pytest.mark.asyncio
async def test_should_exit_drops_all():
    kba, kernel = _make_kba()
    kba._runtime.should_exit = True

    await kba.send(TextEvent(content="should not appear"))
    await kba.send(StopEvent(stop_reason="end"), target="other")

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel.message_bus.direct_calls) == 0


@pytest.mark.asyncio
async def test_thinking_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ThinkingEvent(content="hmm..."), target=None)

    # Should not touch MessageBus
    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel.message_bus.direct_calls) == 0

    # Should degrade to console
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)


@pytest.mark.asyncio
async def test_tool_call_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ToolCallEvent(content="call x"), target=None)

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)


@pytest.mark.asyncio
async def test_tool_result_event_degraded_to_console():
    kba, kernel = _make_kba()
    await kba.send(ToolResultEvent(content="result"), target=None)

    assert len(kernel.message_bus.publish_calls) == 0
    assert len(kernel._console.events) == 1
    assert isinstance(kernel._console.events[0], AgentOutput)
```

- [ ] **Step 2: Run KBA.send() upgrade tests**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/runtime/test_kba_send_upgrade.py -v 2>&1
```

Expected: 8 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_kba_send_upgrade.py
git commit -m "test: add KBA.send() MessageBus routing tests (8 tests)

Coverage: TextEvent/StopEvent → publish, target=pid → direct,
should_exit drops all, Thinking/ToolCall/ToolResult → console.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 15: Write integration test for Mode B end-to-end

**Files:**
- Create: `tests/runtime/test_mode_b_e2e.py`

- [ ] **Step 1: Write Mode B integration test**

```python
# tests/runtime/test_mode_b_e2e.py

"""Integration tests for Batch 3: Mode B + MessageBus + cascade."""

import asyncio
import os
import tempfile
import pytest
from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.kernel import Kernel
from harness.runtime.agent_runtime import AgentState
from harness.runtime.types import WorkflowFinished


# ── Helpers ──

class _MockConsole:
    """Mock SystemConsole with spy."""
    def __init__(self):
        self.events = []

    async def receive(self):
        from harness.runtime.types import CommandTalk
        return CommandTalk(pid="root", text="")

    async def send(self, event):
        self.events.append(event)


def _write_two_agent_script():
    """Write a minimal 2-agent workflow script, return path."""
    content = '''from harness.core.container import DIContainer
from harness.di import Harness
from harness.interfaces.input_adapter import InputAdapter
from harness.runtime.decorators import agent, subscribe

@agent("collector", entry_prompt="collect data and report")
def assemble_collector():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

@agent("analyzer", entry_prompt="analyze the collected data")
def assemble_analyzer():
    container = DIContainer()
    container.register(InputAdapter, object())
    return Harness.from_container(container, call_llm=None)

subscribe("analyzer").to("collector")
'''
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False
    ) as f:
        f.write(content)
        path = f.name
    return path


# ── Tests ──


@pytest.mark.asyncio
async def test_mode_b_two_agents_complete_with_workflow_finished():
    """Mode B: collector (oneshot) + analyzer (continuous with subscribe)
    both run to completion and produce WorkflowFinished."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        result = k.spawn_from_script(script_path, parent=None)

        # Verify result structure
        assert result["workflow_flag"].startswith("wf_")
        assert len(result["agents"]) == 2
        pids = {a["pid"] for a in result["agents"]}
        assert pids == {"collector", "analyzer"}

        # Verify subscribe registered to MessageBus
        subscribers = k.message_bus.get_subscribers_of("collector")
        assert "analyzer" in subscribers

        # Verify both agents were created
        assert "collector" in k.runtime_table
        assert "analyzer" in k.runtime_table

        # Verify modes
        assert k.runtime_table["collector"].mode == "oneshot"
        assert k.runtime_table["analyzer"].mode == "continuous"

        # Verify workflow_flag set on agents
        assert k.runtime_table["collector"].workflow_flag == result["workflow_flag"]
        assert k.runtime_table["analyzer"].workflow_flag == result["workflow_flag"]

        # Verify tasks were started
        assert "collector" in k._tasks
        assert "analyzer" in k._tasks

        # Verify entry_prompts were delivered
        qc = k.input_queues["collector"].get_nowait()
        qa = k.input_queues["analyzer"].get_nowait()
        assert qc.text == "collect data and report"
        assert qa.text == "analyze the collected data"

        # Both should become FINISHED (oneshot auto-exits,
        # continuous receives sentinel from cascade after collector finishes)
        # Wait for both tasks to complete
        await asyncio.wait_for(
            asyncio.gather(*k._tasks.values(), return_exceptions=True),
            timeout=10.0,
        )

        assert k.runtime_table["collector"].state == AgentState.FINISHED
        assert k.runtime_table["analyzer"].state == AgentState.FINISHED

        # Verify WorkflowFinished-like summary can be collected
        agents_results = []
        for pid, r in k.runtime_table.items():
            agents_results.append({
                "pid": pid,
                "output": r.last_output,
                "error": r.error,
                "rounds": r.round_count,
            })

        assert len(agents_results) == 2

    finally:
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_subscribe_message_routing_end_to_end():
    """TextEvent from publisher routes through MessageBus to subscriber."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        k.spawn_from_script(script_path, parent=None)

        # Simulate collector producing a TextEvent via KBA.send()
        collector = k.runtime_table["collector"]
        analyzer = k.runtime_table["analyzer"]

        # Start both tasks so they're waiting in INIT
        # Consume entry prompts
        k.input_queues["collector"].get_nowait()
        k.input_queues["analyzer"].get_nowait()

        # Manually send a TextEvent through collector's KBA
        from harness.interfaces.types import TextEvent
        await collector.adapter.send(TextEvent(content="28 files found"))

        # analyzer should have received the message via MessageBus
        from harness.interfaces.types import UserRequest
        import asyncio
        msg = await asyncio.wait_for(
            k.input_queues["analyzer"].get(), timeout=2.0
        )
        assert msg.content == "28 files found"
        assert msg.metadata["from"] == "collector"

    finally:
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_cascade_termination_subscriber_exits():
    """When publisher finishes, subscriber receives __EXIT_SENTINEL__."""
    console = _MockConsole()
    k = Kernel(console)
    script_path = _write_two_agent_script()

    try:
        k.spawn_from_script(script_path, parent=None)

        collector = k.runtime_table["collector"]
        analyzer = k.runtime_table["analyzer"]

        # Consume entry prompts
        k.input_queues["collector"].get_nowait()
        k.input_queues["analyzer"].get_nowait()

        # Manually finish collector (simulate oneshot completion)
        collector.should_exit = True
        k.input_queues["collector"].put_nowait(
            __import__('harness.runtime.types', fromlist=['__EXIT_SENTINEL__']).__EXIT_SENTINEL__
        )

        # Wait a bit for cascade to propagate
        await asyncio.sleep(0.5)

        # analyzer should have sentinel in queue from cascade
        from harness.runtime.types import __EXIT_SENTINEL__
        sentinel = k.input_queues["analyzer"].get_nowait()
        assert sentinel is __EXIT_SENTINEL__
        assert analyzer.should_exit is True

    finally:
        os.unlink(script_path)
```

- [ ] **Step 2: Run Mode B integration tests**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/runtime/test_mode_b_e2e.py -v 2>&1
```

Expected: 3 tests pass.

Note: The test `test_mode_b_two_agents_complete_with_workflow_finished` waits for real agent tasks to FINISH. Since call_llm is None (test mode), agents will quickly produce StopEvent("no_llm") and exit as oneshot, or wait on receive() for continuous agents.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_mode_b_e2e.py
git commit -m "test: add Mode B + MessageBus integration tests (3 tests)

Coverage: 2-agent workflow creation + FINISHED, subscribe message
routing end-to-end, cascade termination subscriber exits.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 16: Final regression — run all tests

**Files:**
- (none — verification only)

- [ ] **Step 1: Run full test suite**

```bash
cd /home/wowoow/open-source/harness_agent && python -m pytest tests/ -v 2>&1 | tail -50
```

Expected: all tests pass. No regressions in existing Batch 0/1/2 tests.

- [ ] **Step 2: Verify import coverage**

```bash
cd /home/wowoow/open-source/harness_agent && python -c "
from harness.runtime import MessageBus, WorkflowFinished
from harness.runtime.message_bus import MessageBus
from harness.runtime.kernel import Kernel
from harness.runtime.runtime import Runtime
# Verify run_from_script exists
assert hasattr(Runtime, 'run_from_script')
# Verify Kernel creates MessageBus
class MC:
    async def receive(self): pass
    async def send(self, e): pass
k = Kernel(MC())
assert k.message_bus is not None
print('All checks passed')
"
```

Expected: `All checks passed`

- [ ] **Step 3: Commit (if any test fixes needed)**

```bash
git add -A
git commit -m "chore: final Batch 3 test adjustments and fixes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 17: Update Batch 3 status in module docstrings

**Files:**
- Modify: `harness/runtime/__init__.py` (batch status comment)
- Modify: `harness/runtime/kernel.py` (batch status comment)
- Modify: `harness/runtime/bridge_adapter.py` (batch status comment)
- Modify: `harness/runtime/runtime.py` (remove "Batch 3 才支持" comment)

- [ ] **Step 1: Update runtime docstrings to mark Batch 3 as done**

In `harness/runtime/__init__.py`, update the batch plan comment (line 7):
```python
Batch 3: MessageBus + 订阅 + 并发 + 终止（完整多 agent）。✅
```

In `harness/runtime/kernel.py`, update the batch plan comment (line 8):
```python
Batch 3: MessageBus + 订阅 + 级联 + 静默检测完整实现。✅
```

In `harness/runtime/bridge_adapter.py`, update the batch plan comment (lines 13-16), replace the future-tense description with past tense:
```python
Batch 3（已实现）：
- target=None → message_bus.publish(from_pid, event, on_no_subscriber=...)
- target=pid → message_bus.direct(target, InternalMessage(...))
```

In `harness/runtime/runtime.py`, update the Mode B comment (line 11):
```python
    # Mode B（Batch 3 实现）
    Runtime(console).run_from_script("workflow.py")
```

- [ ] **Step 2: Commit**

```bash
git add harness/runtime/__init__.py harness/runtime/kernel.py harness/runtime/bridge_adapter.py harness/runtime/runtime.py
git commit -m "docs: mark Batch 3 as complete in module docstrings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
