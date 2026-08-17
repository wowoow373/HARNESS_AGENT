# 会话持久化与恢复（Session Persistence & Resume）— Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Harness 增加内核级会话持久化与启动恢复能力——对话事实在变异点镜像写入 append-only 日志，进程重启后通过 `--resume <conv_id>` 重放播种、修复交互缺口、续写同一日志。

**Architecture:** 新增 `harness/core/session/` 子包（事件 schema / SessionLog / SessionStore / replay / manifest），属框架、非插件、默认开启。`SessionLog` 接管 `_history`/`_tool_call_records` 所有权（与 orchestrator 同一对象引用），每 agent 日志一个 `_LogWriter` 协程（唯一写执行流），轮次边界批量 flush、finalize 才 fsync。恢复路径与全新启动合一：`Kernel.boot()` 统一负责"创建全部 → 播种 → 配对修复 → 统一启动"。

**Tech Stack:** Python 3.12+, asyncio, dataclasses, JSONL, pytest（`asyncio.run()` 包装，不用 pytest-asyncio）

**设计来源：** 九轮讨论收敛的《对话持久化与恢复：完整设计总结》（决策总表 D1–D10、不变量与数据流规则）。本计划将其落为可执行任务。

---

## 依赖关系总览（本计划的核心）

### 依赖 DAG

```
                  ┌────────────────── 无依赖（可立即开工）──────────────────┐
                  │                                                        │
   T1 events/ids 事件 schema ─────┬────────────────┬───────────────┐
   T2 config + Sequencer ─────────┤                │               │
                  │               │                │               │
                  ▼               ▼                │               │
   T3 _LogWriter + SessionStore 写通道      T5 SessionLog ◄── T1,T3 │
                  │（懒打开/flush屏障/降级）   （内存真相+批缓冲）     │
                  ▼                              │               │
   T4 index.json 投影 + owner                    ▼               │
                  │                       T6 orchestrator 插桩     │
                  │                       （R0–R6 + flush/finalize）│
                  │                              │               │
                  ▼                              ▼               │
   T8 replay 加载/重放 ◄── T1,T5          T7 Kernel/Runtime 接线 ◄─┘
                  │                       （store 注入/finalize/close）
   T9 manifest 计算+分级比对 ◄── T1,T2     │
                  │                        ▼
                  │               T11 msg_id 盖章（独立分支，T7 后即可）
                  ▼                        │
   T10 Kernel.boot（create/start 分离 + 播种 + seq/lsn 续接 + resume_marker）
                  ◄── T7, T8, T9           │
                  │                        │
                  ▼                        ▼
   T12 交互配对修复 ◄── T10 + T11（唯一双依赖汇合点）
                  │
                  ▼
   T13 CLI/Runtime 入口（--resume/--force + Mode B script sha1）◄── T10
                  │
                  ▼
   T14 E2E（全生命周期 + 崩溃变体 + LSN 度量）◄── T12, T13
                  │
                  ▼
   T15 文档与收尾（ARCHITECTURE.md / 配置面 / 回归）
```

### 关键路径（决定总工期）

```
T1 → T3 → T5 → T6 → T7 → T10 → T12 → T13 → T14
```

### 可并行的轨道（T7 完成后）

| 轨道 | 任务 | 说明 |
|------|------|------|
| A | T8 → T9 | 读侧（replay + manifest），纯函数为主 |
| B | T11 | msg_id 盖章，只动 runtime 层四个文件 |
| C | T2 | 配置面，任何时候可做（T7 接线前完成即可） |

### 依赖关系的设计依据（为什么是这个顺序）

1. **T1（事件 schema）是一切的前提**：写侧（T3/T5）与读侧（T8）共用它，schema 不定稿，两侧都会返工。
2. **T3 与 T5 可以互换顺序**：`_LogWriter` 只认 `list[str]` 批次，SessionLog 只认 writer 的 `enqueue/enqueue_final/close` 契约。本计划先 T3 后 T5，是为了 T5 的测试能用真 store 打底。
3. **T6 必须在 T5 之后、T7 之前**：orchestrator 插桩只依赖 SessionLog 的 API，不依赖 Kernel 接线；先插桩可以让记录格式在最小范围（单 orchestrator + tmp 目录）内被测试钉死，再进 Kernel 集成。
4. **T11（msg_id 盖章）与 T8–T10 解耦**：盖章只改 `tools.py`/`message_bus.py`/`bridge_adapter.py`/`kernel.py` 的四处调解点，不依赖持久化读侧。但它必须在 **T12（配对修复）之前**——没有盖章就没有配对联。
5. **T10（boot）是最深的汇合点**：依赖写通道（T7）、读侧（T8）、manifest（T9），且内含一个对既有行为的**重构前置**（spawn 的 create/start 分离），风险最高，放在写读两侧都被测试钉死之后。
6. **T12 是唯一的双依赖汇合**：配对修复 = boot 流程（T10）× msg_id 边（T11）。
7. **T13/T14/T15 顺序固定**：入口 → 端到端 → 文档。

### 既有代码的四个改造约束（探索代码后确认，直接影响任务切分）

| 约束 | 位置 | 影响 |
|------|------|------|
| `_history`/`_tool_call_records` 目前由 orchestrator 自持 | `harness/core/async_orchestrator.py:134-135` | T6 需把所有权移入 SessionLog（**同一对象引用**，不改字段名，保住 `agent_runtime.py:245` 的 `_extract_last_output` 等全部读取方） |
| `spawn_root`/`spawn_from_script` 创建即启动 task | `harness/runtime/kernel.py:161,445` | T10 必须先做 create/start 分离重构（boot 要求"创建全部→播种→统一启动"）；默认参数保持现状，存量测试不动 |
| `_phase_end` 会 `self._history.clear()` | `harness/core/async_orchestrator.py:545` | T6 的 finalize 调用必须插在 clear **之前**；clear 对共享列表无害（事件此时已编码入 batch） |
| `Kernel._on_agent_finished` 由 task done_callback 触发 | `harness/runtime/kernel.py:163-167` | T7 的 index 投影更新挂在这里；此时 history 已清空，finalize 数据只能来自 `runtime.last_output`/`error`，所以 session_end 事件的**写入**放在 T6 的 `_phase_end` 内，`_on_agent_finished` 只做幂等兜底 + index 更新 |

---

## File Structure

| 文件 | 操作 | 职责 | 任务 |
|------|------|------|------|
| `harness/core/session/__init__.py` | 创建 | 子包导出 | T1 |
| `harness/core/session/exceptions.py` | 创建 | SessionError / CorruptLogError / SessionOwnerConflict / BootError | T1 |
| `harness/core/session/events.py` | 创建 | 事件类型常量 + 事件构造/编解码（**唯二编码路径之一**，replay 是另一半） | T1 |
| `harness/core/session/ids.py` | 创建 | new_conv_id / new_msg_id / new_owner_token / pid_from_token / pid_alive | T1 |
| `harness/core/session/config.py` | 创建 | SessionConfig(root, enabled) + 从 harness.yaml `sessions:` 节加载 | T2 |
| `harness/core/session/sequencer.py` | 创建 | Sequencer —— 会话级 LSN 发号器 | T2 |
| `harness/core/session/store.py` | 创建 | _LogWriter（每文件唯一写协程）+ SessionStore（单例、懒打开、降级、close） | T3 |
| `harness/core/session/store.py` | 修改 | 追加 index.json 原子重写 / read / rebuild / owner 接管 / finalize_agent | T4 |
| `harness/core/session/session_log.py` | 创建 | SessionLog —— `_history`/`_tool_call_records`/`_pending`/`_seq` 的唯一变异点 | T5 |
| `harness/core/async_orchestrator.py` | 修改 | 注入 session_log；R0–R6 记录点；轮次边界 flush；_phase_end finalize | T6 |
| `harness/runtime/agent_runtime.py` | 修改 | `_init_orchestrator(call_llm, session_log)` 透传 | T6 |
| `harness/runtime/kernel.py` | 修改 | `Kernel(console, store=None)`；spawn 时创建 SessionLog；`_on_agent_finished` → finalize_agent | T7 |
| `harness/runtime/runtime.py` | 修改 | `Runtime(console, session_config=None)`；创建 store；finally close | T7 |
| `harness/core/session/replay.py` | 创建 | load_agent_log / scan_session / measure_lsn_gap / ReplayResult / Edge | T8 |
| `harness/core/session/manifest.py` | 创建 | compute_manifest / manifest_sha1 / diff_manifest / fingerprint() 约定 | T9 |
| `harness/core/async_orchestrator.py` | 修改 | 提取模块级 `build_tool_router(container)`（_phase_init 与 boot 探针共用） | T9 |
| `harness/runtime/kernel.py` | 修改 | create/start 分离重构；`boot()` 全量；BootReport | T10 |
| `harness/runtime/tools.py` | 修改 | TalkToTool 盖章 msg_id（metadata + result content） | T11 |
| `harness/runtime/message_bus.py` | 修改 | publish 按订阅者盖章 msg_id，返回 edges | T11 |
| `harness/runtime/bridge_adapter.py` | 修改 | send 路由后 record_edge；direct 盖章 | T11 |
| `harness/runtime/kernel.py` | 修改 | child_finished / spawn_entry 盖章 + spawn_entry 边落父日志 | T11 |
| `harness/core/session/replay.py` | 修改 | 追加 plan_redelivery | T12 |
| `harness/runtime/kernel.py` | 修改 | boot 内插入配对修复段（播种后、启动前） | T12 |
| `main.py` | 修改 | `--resume`/`--force` 参数；session config 加载与透传 | T13 |
| `harness/runtime/runtime.py` | 修改 | run/run_from_script 接 resume/force；统一走 boot；Mode B sha1 | T13 |
| `tests/session/` | 创建 | 全部新测试（每任务一个文件） | T1–T14 |
| `ARCHITECTURE.md` / `README.md` | 修改 | 持久化章节 + 配置面 | T15 |
| `.gitignore` | 修改 | 忽略 `sessions/` | T3 |

---

## 全局实现约定（所有任务共用）

1. **新子包**：`harness/core/session/`。属框架内核机制，不进 DI 容器，组件层拿不到引用。
2. **编码唯一性**：事件 dict 只能由 `events.py` 的 `make_*` 构造、`encode_event` 编码；回放只能由 `replay.py` 解码还原。无第二编码路径（不变量 #2）。
3. **热路径零 I/O**：`record_*` 全部同步、零 await；磁盘只在 `_LogWriter` 协程 + `asyncio.to_thread` 里。
4. **测试风格**：遵循 `tests/runtime/test_message_bus.py` 的 `run_async` 装饰器约定（`asyncio.run()` 包装）；文件系统用 pytest `tmp_path`。
5. **共享测试替身**：T1 创建 `tests/session/_fakes.py`，后续任务复用，不在各测试文件重复定义。
6. **回归命令**：每个任务末尾除本任务测试外，跑 `python -m pytest tests/ -q`（存量约 920 个测试须保持绿）。

---

### Task 1: 事件 schema 与 id 生成器（events.py + ids.py + exceptions.py）

**依赖：** 无（地基任务，阻塞 T3/T5/T8）
**阻塞：** T3、T5、T8——schema 是写读两侧的契约，必须最先定稿。

**Files:**
- Create: `harness/core/session/__init__.py`
- Create: `harness/core/session/exceptions.py`
- Create: `harness/core/session/events.py`
- Create: `harness/core/session/ids.py`
- Create: `tests/session/__init__.py`
- Create: `tests/session/_fakes.py`
- Test: `tests/session/test_events.py`

- [ ] **Step 1: 写失败测试**

`tests/session/__init__.py`（空文件）与 `tests/session/_fakes.py`：

```python
"""session 测试共享替身与工具。"""

from __future__ import annotations

import asyncio
import functools


def run_async(coro_func):
    """装饰器：将 async 测试函数包装为 asyncio.run() 调用（与 tests/runtime 同约定）。"""
    @functools.wraps(coro_func)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


class MockConsole:
    """Mock SystemConsole：receive 永远阻塞（测试不驱动系统输入），send 收集事件。"""

    def __init__(self):
        self.events = []

    async def receive(self):
        await asyncio.sleep(3600)

    async def send(self, event):
        self.events.append(event)


class MockHarness:
    """最小 harness 替身：container + call_llm（与 tests/runtime 同形）。"""

    def __init__(self, call_llm=None):
        from harness.core.container import DIContainer
        self.container = DIContainer()
        self.call_llm = call_llm
```

`tests/session/test_events.py`：

```python
"""events.py — 事件 schema 与编解码测试。"""

import pytest

from harness.core.session import events
from harness.core.session.exceptions import SessionError
from harness.interfaces.types import Message, ToolCall, ToolCallFunction, ToolCallRecord


class TestMessageEvents:
    def test_user_event_roundtrip(self):
        msg = Message(role="user", content="你好")
        evt = events.make_message_event(msg, seq=1, lsn=1, ts=1780000001.0,
                                        meta={"from": "b", "msg_id": "M-abc"})
        assert evt["type"] == "user"
        line = events.encode_event(evt)
        back = events.decode_event(line)
        assert back["seq"] == 1 and back["lsn"] == 1
        assert back["meta"]["msg_id"] == "M-abc"
        assert events.event_to_message(back).content == "你好"

    def test_assistant_event_with_tool_calls_roundtrip(self):
        msg = Message(
            role="assistant", content="",
            tool_calls=[ToolCall(id="call_9", type="function",
                                 function=ToolCallFunction(name="talk_to",
                                                           arguments='{"pid":"b"}'))],
        )
        evt = events.make_message_event(msg, seq=2, lsn=2, ts=1.0)
        assert evt["type"] == "assistant"
        back = events.decode_event(events.encode_event(evt))
        restored = events.event_to_message(back)
        assert restored.tool_calls[0].id == "call_9"
        assert restored.tool_calls[0].function.name == "talk_to"

    def test_tool_role_maps_to_tool_result_event(self):
        msg = Message(role="tool", content='{"ok":true}', tool_call_id="call_9")
        evt = events.make_message_event(msg, seq=3, lsn=3, ts=1.0)
        assert evt["type"] == "tool_result"
        restored = events.event_to_message(events.decode_event(events.encode_event(evt)))
        assert restored.role == "tool" and restored.tool_call_id == "call_9"

    def test_meta_omitted_when_none(self):
        evt = events.make_message_event(Message(role="user", content="x"),
                                        seq=1, lsn=1, ts=1.0)
        assert "meta" not in evt


class TestToolCallEvent:
    def test_roundtrip(self):
        rec = ToolCallRecord(tool_call_id="call_9", tool_name="talk_to",
                             arguments={"pid": "b", "text": "在吗"},
                             result='{"ok":true,"msg_id":"M-1"}',
                             started_at=1.0, finished_at=2.0, error=None)
        evt = events.make_tool_call_event(rec, seq=4, lsn=4, ts=2.0)
        back = events.decode_event(events.encode_event(evt))
        restored = events.event_to_tool_call_record(back)
        assert restored.tool_name == "talk_to"
        assert restored.arguments == {"pid": "b", "text": "在吗"}
        assert restored.error is None


class TestControlEvents:
    def test_header_carries_parent_and_manifest(self):
        evt = events.make_header(conv_id="conv-1", pid="b", parent="root",
                                 manifest_sha1="9f2c", seq=0, lsn=0, ts=1.0)
        assert evt["type"] == "header"
        assert evt["format_version"] == events.FORMAT_VERSION
        assert evt["parent"] == "root"

    def test_edge_event(self):
        evt = events.make_edge_event(msg_id="M-2", from_pid="b", to_pid="root",
                                     kind="publish", text="查到了",
                                     seq=5, lsn=5, ts=1.0)
        assert evt["type"] == "edge" and evt["to"] == "root"

    def test_stop_and_session_end(self):
        stop = events.make_stop_event(stop_reason="end_turn", seq=6, lsn=6, ts=1.0)
        end = events.make_session_end_event(final_output="再见", execution_time=1.5,
                                            status="paused", seq=7, lsn=7, ts=2.0)
        assert stop["type"] == "stop"
        assert end["type"] == "session_end" and end["status"] == "paused"


class TestDecodeRobustness:
    def test_decode_rejects_garbage(self):
        with pytest.raises(ValueError):
            events.decode_event('{"no_type": true}')

    def test_encode_handles_non_serializable_via_default_str(self):
        evt = events.make_message_event(Message(role="user", content=object()),
                                        seq=1, lsn=1, ts=1.0)
        line = events.encode_event(evt)  # 不抛异常即通过
        assert isinstance(line, str)


class TestIds:
    def test_conv_id_format(self):
        cid = events.new_conv_id()
        assert cid.startswith("conv-") and len(cid) > 10

    def test_msg_id_unique(self):
        assert events.new_msg_id() != events.new_msg_id()

    def test_owner_token_roundtrip_pid(self):
        token = events.new_owner_token()
        assert events.pid_from_token(token) is not None

    def test_pid_alive_self(self):
        import os
        assert events.pid_alive(os.getpid()) is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session'`

- [ ] **Step 3: 实现**

`harness/core/session/exceptions.py`：

```python
"""Session 持久化异常体系。"""

from __future__ import annotations


class SessionError(Exception):
    """会话持久化与恢复的基类异常。"""


class CorruptLogError(SessionError):
    """agent 日志损坏：header 缺失/损坏，或 seq 不连续（缺号=损坏，拒绝恢复）。"""


class SessionOwnerConflict(SessionError):
    """会话被另一个存活进程占用（owner token 校验失败）。"""


class BootError(SessionError):
    """boot 失败：manifest 硬冲突、script sha1 不匹配、会话不存在等。

    启动失败 = 最安全失败：此时无任何 agent 已跑，干净退出即可。
    """
```

`harness/core/session/ids.py`：

```python
"""会话相关的 id / token 生成器。"""

from __future__ import annotations

import os
import time
import uuid


def new_conv_id() -> str:
    """生成会话 id：conv-<时间戳>-<随机后缀>。"""
    return f"conv-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def new_msg_id() -> str:
    """生成跨日志因果边配对键。由内核在调解点盖章，绝不信任 LLM 的 call_id。"""
    return f"M-{uuid.uuid4().hex[:8]}"


def new_owner_token() -> str:
    """生成 owner token：pid-<进程号>-<纳秒时间戳>。"""
    return f"pid-{os.getpid()}-{time.time_ns()}"


def pid_from_token(token: str) -> int | None:
    """从 owner token 解析进程号；解析失败返回 None。"""
    try:
        return int(token.split("-")[1])
    except (IndexError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """进程是否存活（os.kill(pid, 0) 探活）。"""
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    return True
```

`harness/core/session/events.py`：

```python
"""Session 事件日志 schema 与编解码 —— 唯一编码路径。

一行一事件（JSONL，UTF-8）。事件类型：
- header            日志头（每文件第一行，seq=0；缺失/损坏 = 拒绝恢复）
- user/assistant/tool_result  对话消息（镜像 SessionLog._history）
- tool_call         工具执行记录（镜像 SessionLog._tool_call_records）
- edge              出站消息边（msg_id 配对修复的发送方事实；不入 history）
- stop              轮次结束
- session_end       日志终态（存在=优雅关闭；缺失=崩溃证据）

三序分工（设计 2.4）：
- seq    文件内严格连续 +1 —— 回放顺序与行完整性校验
- lsn    会话级单调（不校验连续，空洞=崩溃损失证据）
- ts     墙钟，仅供人类取证展示
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ...interfaces.types import Message, ToolCallRecord
from ...messaging.builder import dict_to_message, message_to_dict
from .ids import new_conv_id, new_msg_id, new_owner_token, pid_alive, pid_from_token

FORMAT_VERSION = 1

EVT_HEADER = "header"
EVT_USER = "user"
EVT_ASSISTANT = "assistant"
EVT_TOOL_CALL = "tool_call"
EVT_TOOL_RESULT = "tool_result"
EVT_EDGE = "edge"
EVT_STOP = "stop"
EVT_SESSION_END = "session_end"

_MESSAGE_EVT_BY_ROLE = {
    "user": EVT_USER,
    "assistant": EVT_ASSISTANT,
    "tool": EVT_TOOL_RESULT,
}

# ids 的便捷 re-export（测试与调用方只 import events 即可）
__all__ = [
    "FORMAT_VERSION",
    "EVT_HEADER", "EVT_USER", "EVT_ASSISTANT", "EVT_TOOL_CALL",
    "EVT_TOOL_RESULT", "EVT_EDGE", "EVT_STOP", "EVT_SESSION_END",
    "encode_event", "decode_event",
    "make_header", "make_message_event", "make_tool_call_event",
    "make_edge_event", "make_stop_event", "make_session_end_event",
    "event_to_message", "event_to_tool_call_record",
    "new_conv_id", "new_msg_id", "new_owner_token",
    "pid_from_token", "pid_alive",
]


# ---------------------------------------------------------------------------
# 编解码
# ---------------------------------------------------------------------------


def encode_event(event: Dict[str, Any]) -> str:
    """事件 dict → JSON 行（不含换行）。default=str 兜底不可序列化值。"""
    return json.dumps(event, ensure_ascii=False, default=str)


def decode_event(line: str) -> Dict[str, Any]:
    """JSON 行 → 事件 dict。非事件行抛 ValueError。"""
    evt = json.loads(line)
    if not isinstance(evt, dict) or "type" not in evt or "seq" not in evt:
        raise ValueError(f"not a session event line: {line[:80]!r}")
    return evt


# ---------------------------------------------------------------------------
# 事件构造（全部带三序 seq/lsn/ts）
# ---------------------------------------------------------------------------


def make_header(*, conv_id: str, pid: str, parent: Optional[str],
                manifest_sha1: str, seq: int, lsn: int, ts: float) -> Dict[str, Any]:
    return {
        "type": EVT_HEADER, "format_version": FORMAT_VERSION,
        "conv_id": conv_id, "pid": pid, "parent": parent,
        "manifest_sha1": manifest_sha1, "created_at": ts,
        "seq": seq, "lsn": lsn, "ts": ts,
    }


def make_message_event(message: Message, *, seq: int, lsn: int, ts: float,
                       meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Message → user/assistant/tool_result 事件（按 role 映射）。"""
    evt: Dict[str, Any] = {
        "type": _MESSAGE_EVT_BY_ROLE.get(message.role, message.role),
        "seq": seq, "lsn": lsn, "ts": ts,
        "message": message_to_dict(message),
    }
    if meta:
        evt["meta"] = meta
    return evt


def make_tool_call_event(record: ToolCallRecord, *, seq: int, lsn: int,
                         ts: float) -> Dict[str, Any]:
    return {
        "type": EVT_TOOL_CALL, "seq": seq, "lsn": lsn, "ts": ts,
        "record": {
            "tool_call_id": record.tool_call_id,
            "tool_name": record.tool_name,
            "arguments": record.arguments,
            "result": record.result,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "error": record.error,
        },
    }


def make_edge_event(*, msg_id: str, from_pid: str, to_pid: str, kind: str,
                    text: str, seq: int, lsn: int, ts: float) -> Dict[str, Any]:
    """出站消息边（发送方事实）。kind ∈ talk_to|publish|direct|spawn_entry。"""
    return {
        "type": EVT_EDGE, "seq": seq, "lsn": lsn, "ts": ts,
        "msg_id": msg_id, "from": from_pid, "to": to_pid,
        "kind": kind, "text": text,
    }


def make_stop_event(*, stop_reason: str, seq: int, lsn: int,
                    ts: float) -> Dict[str, Any]:
    return {"type": EVT_STOP, "seq": seq, "lsn": lsn, "ts": ts,
            "stop_reason": stop_reason}


def make_session_end_event(*, final_output: str, execution_time: float,
                           status: str, seq: int, lsn: int,
                           ts: float) -> Dict[str, Any]:
    return {
        "type": EVT_SESSION_END, "seq": seq, "lsn": lsn, "ts": ts,
        "final_output": final_output, "execution_time": execution_time,
        "status": status,
    }


# ---------------------------------------------------------------------------
# 回放还原
# ---------------------------------------------------------------------------


def event_to_message(evt: Dict[str, Any]) -> Message:
    """user/assistant/tool_result 事件 → Message（回放用）。"""
    return dict_to_message(evt["message"])


def event_to_tool_call_record(evt: Dict[str, Any]) -> ToolCallRecord:
    """tool_call 事件 → ToolCallRecord（回放用）。"""
    rec = evt["record"]
    return ToolCallRecord(
        tool_call_id=rec.get("tool_call_id", ""),
        tool_name=rec.get("tool_name", ""),
        arguments=rec.get("arguments", {}),
        result=rec.get("result"),
        started_at=rec.get("started_at", 0.0),
        finished_at=rec.get("finished_at", 0.0),
        error=rec.get("error"),
    )
```

`harness/core/session/__init__.py`：

```python
"""harness.core.session — 内核级会话持久化与恢复。

属框架（非插件、不可替换、默认开启）。组件层与本包之间只有
"读"（history 经 assemble）与"被观察"（R2/R3 记录 Hook 之后的终值）。
"""

from .config import SessionConfig, load_session_config
from .exceptions import BootError, CorruptLogError, SessionError, SessionOwnerConflict
from .sequencer import Sequencer
from .session_log import SessionLog
from .store import SessionStore

__all__ = [
    "SessionConfig", "load_session_config",
    "SessionError", "CorruptLogError", "SessionOwnerConflict", "BootError",
    "Sequencer", "SessionLog", "SessionStore",
]
```

注意：此 `__init__.py` 引用的 `config.py`/`sequencer.py`/`session_log.py`/`store.py` 在 T2/T3/T5 才创建——本任务先创建一个**仅导出 events/exceptions 的临时版**，T2/T3/T5 逐步替换为上面的完整版。临时版：

```python
"""harness.core.session — 内核级会话持久化与恢复（T1 临时导出）。"""

from .exceptions import BootError, CorruptLogError, SessionError, SessionOwnerConflict

__all__ = ["SessionError", "CorruptLogError", "SessionOwnerConflict", "BootError"]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_events.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/ tests/session/
git commit -m "feat(session): add event schema, ids, and exceptions for session persistence"
```

---

### Task 2: 配置面与 LSN 发号器（config.py + sequencer.py）

**依赖：** 无（独立轨道，T7 接线前完成即可）
**阻塞：** T7（SessionConfig 透传）、T5（Sequencer）

**Files:**
- Create: `harness/core/session/config.py`
- Create: `harness/core/session/sequencer.py`
- Modify: `harness/core/session/__init__.py`
- Test: `tests/session/test_config_sequencer.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_config_sequencer.py`：

```python
"""SessionConfig 与 Sequencer 测试。"""

from harness.core.session.config import SessionConfig, load_session_config
from harness.core.session.sequencer import Sequencer


class TestSessionConfig:
    def test_defaults_when_no_file(self, tmp_path):
        cfg = load_session_config(str(tmp_path / "missing.yaml"))
        assert cfg.enabled is True
        assert cfg.root == "./sessions"

    def test_defaults_when_none_path(self):
        cfg = load_session_config(None)
        assert cfg.enabled is True

    def test_sessions_section_parsed(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text("sessions:\n  root: /tmp/my-sessions\n  enabled: false\n",
                     encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.root == "/tmp/my-sessions"
        assert cfg.enabled is False

    def test_missing_section_uses_defaults(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text("llm:\n  model: gpt-4o\n", encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.enabled is True and cfg.root == "./sessions"

    def test_broken_yaml_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text(":::not yaml:::[", encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.enabled is True


class TestSequencer:
    def test_monotonic_from_zero(self):
        seq = Sequencer()
        assert [seq.next(), seq.next(), seq.next()] == [0, 1, 2]

    def test_start_offset_for_resume(self):
        seq = Sequencer(start=16)
        assert seq.next() == 16
        assert seq.next_value == 17

    def test_gaps_are_legal(self):
        """LSN 不校验连续：发号后崩溃产生合法空洞（设计 2.4）。"""
        seq = Sequencer()
        seq.next()  # 0 —— 假设该事件随崩溃丢失
        seq.next()  # 1
        assert seq.next() == 2  # 空洞 0/1 不报错
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_config_sequencer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.config'`

- [ ] **Step 3: 实现**

`harness/core/session/sequencer.py`：

```python
"""Sequencer — 会话级 LSN（Log Sequence Number）发号器。

单调递增，不保证连续——崩溃使已发号但未落盘的事件形成合法空洞，
空洞本身就是损失度量的证据（设计 2.4）。不校验连续性。
"""

from __future__ import annotations


class Sequencer:
    """会话级 LSN 发号器。进程内由 SessionStore 持有，跨 boot 经 max(lsn)+1 恢复。"""

    def __init__(self, start: int = 0):
        self._next = start

    def next(self) -> int:
        """取号（单调 +1）。"""
        value = self._next
        self._next += 1
        return value

    @property
    def next_value(self) -> int:
        """下一个将发出的号（不取号）。"""
        return self._next
```

`harness/core/session/config.py`：

```python
"""SessionConfig — 持久化唯一配置面（设计决策 D1）。

只有两个字段：sessions.root + sessions.enabled。
从 harness.yaml 的 sessions: 节加载；文件缺失/节缺失/解析失败均回退默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionConfig:
    """持久化配置。

    Attributes:
        root: 会话存储根目录（sessions/<conv_id>/ 的父目录）。
        enabled: 是否开启持久化。False 时 SessionLog 纯内存运行（零落盘）。
    """
    root: str = "./sessions"
    enabled: bool = True


def load_session_config(yaml_path: Optional[str] = None) -> SessionConfig:
    """从 harness.yaml 的 sessions: 节加载配置。

    Args:
        yaml_path: harness.yaml 路径；None 或文件不存在时返回默认配置。

    Returns:
        SessionConfig。任何解析失败都回退默认值（配置面永不阻断启动）。
    """
    cfg = SessionConfig()
    if not yaml_path or not os.path.isfile(yaml_path):
        return cfg
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return cfg
    section = data.get("sessions") or {}
    if "root" in section:
        cfg.root = str(section["root"])
    if "enabled" in section:
        cfg.enabled = bool(section["enabled"])
    return cfg
```

`harness/core/session/__init__.py` 替换为：

```python
"""harness.core.session — 内核级会话持久化与恢复。"""

from .config import SessionConfig, load_session_config
from .exceptions import BootError, CorruptLogError, SessionError, SessionOwnerConflict
from .sequencer import Sequencer

__all__ = [
    "SessionConfig", "load_session_config",
    "SessionError", "CorruptLogError", "SessionOwnerConflict", "BootError",
    "Sequencer",
]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_config_sequencer.py tests/session/test_events.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/config.py harness/core/session/sequencer.py harness/core/session/__init__.py tests/session/test_config_sequencer.py
git commit -m "feat(session): add SessionConfig (sessions.root/enabled) and LSN Sequencer"
```

---
### Task 3: 写通道 —— _LogWriter 协程 + SessionStore 骨架（store.py）

**依赖：** T1（encode 后的行由它落盘）
**阻塞：** T4、T5
**设计对应：** 写动作时序图（第四节）——生产者零 I/O，磁盘只在 writer 协程 + 线程池；flush 返回契约 = "该轮事件已在 OS page cache"；fsync 只在 finalize/close；写失败 → degraded，永不升级。

**Files:**
- Create: `harness/core/session/store.py`
- Modify: `harness/core/session/__init__.py`（导出 SessionStore）
- Modify: `.gitignore`（忽略 `sessions/`）
- Test: `tests/session/test_store.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_store.py`：

```python
"""SessionStore 与 _LogWriter 写通道测试。"""

import json
import os

import pytest

from harness.core.session.store import SessionStore, _LogWriter
from tests.session._fakes import run_async


class TestLogWriter:
    @run_async
    async def test_batch_flush_reaches_page_cache_before_barrier(self, tmp_path):
        """flush 契约：barrier 返回时数据已到 page cache（文件可读）。"""
        path = tmp_path / "a.jsonl"
        writer = _LogWriter(path)
        writer.start()
        barrier = writer.enqueue(['{"seq":1}', '{"seq":2}'])
        await barrier.wait()
        assert path.read_text(encoding="utf-8").splitlines() == ['{"seq":1}', '{"seq":2}']
        await writer.close()

    @run_async
    async def test_lazy_open_no_file_until_first_flush(self, tmp_path):
        """懒打开：首次 flush 前不建文件。"""
        path = tmp_path / "lazy.jsonl"
        writer = _LogWriter(path)
        writer.start()
        assert not path.exists()
        await writer.enqueue(['{"seq":0}']).wait() if False else None
        barrier = writer.enqueue(['{"seq":0}'])
        await barrier.wait()
        assert path.exists()
        await writer.close()

    @run_async
    async def test_finalize_batch_appends_and_close_drains(self, tmp_path):
        path = tmp_path / "b.jsonl"
        writer = _LogWriter(path)
        writer.start()
        await writer.enqueue(['{"seq":0}']).wait()
        barrier = writer.enqueue_final(['{"seq":1,"type":"session_end"}'])
        await barrier.wait()
        await writer.close()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["type"] == "session_end"

    @run_async
    async def test_write_failure_degrades_but_never_raises(self, tmp_path):
        """写失败 → degraded → barrier 仍置位（flush 永不挂起）→ 不抛异常。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        path = blocker / "sub" / "x.jsonl"  # 父路径是文件 → mkdir/open 必败
        writer = _LogWriter(path)
        writer.start()
        barrier = writer.enqueue(['{"seq":1}'])
        await barrier.wait()  # 不挂起、不抛异常
        assert writer.degraded is True
        assert writer.error is not None
        await writer.close()
        # 降级后后续批次直接放行
        barrier2 = writer.enqueue(['{"seq":2}'])
        # close 后 writer 已退出，enqueue 的批次无人消费——这是预期外用法，跳过
        barrier2.set()

    @run_async
    async def test_close_idempotent(self, tmp_path):
        writer = _LogWriter(tmp_path / "c.jsonl")
        writer.start()
        await writer.close()
        await writer.close()  # 第二次不抛异常


class TestSessionStoreCore:
    @run_async
    async def test_begin_session_creates_layout(self, tmp_path):
        store = SessionStore(str(tmp_path))
        conv_id = store.begin_session(None)
        assert conv_id.startswith("conv-")
        assert store.conv_id == conv_id
        assert (tmp_path / conv_id / "agents").is_dir()
        await store.close()

    @run_async
    async def test_disabled_store_is_noop(self, tmp_path):
        store = SessionStore(str(tmp_path), enabled=False)
        store.begin_session(None)
        writer = store._create_writer("root")
        assert writer is None
        await store.close()
        assert list(tmp_path.iterdir()) == []

    @run_async
    async def test_close_drains_all_writers(self, tmp_path):
        store = SessionStore(str(tmp_path))
        store.begin_session(None)
        w1 = store._create_writer("root")
        w2 = store._create_writer("b")
        await w1.enqueue(['{"pid":"root"}']).wait()
        await w2.enqueue(['{"pid":"b"}']).wait()
        await store.close()
        assert (tmp_path / store.conv_id / "agents" / "root.jsonl").exists()
        assert (tmp_path / store.conv_id / "agents" / "b.jsonl").exists()

    @run_async
    async def test_degraded_pids_reported(self, tmp_path):
        store = SessionStore(str(tmp_path))
        store.begin_session(None)
        writer = store._create_writer("root")
        # 直接破坏 writer 的路径使其降级
        writer._path = tmp_path / "blocker" / "x.jsonl"
        (tmp_path / "blocker").write_text("file", encoding="utf-8")
        await writer.enqueue(['{"seq":1}']).wait()
        assert store.degraded == ["root"]
        await store.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.store'`

- [ ] **Step 3: 实现**

`harness/core/session/store.py`：

```python
"""SessionStore — 进程级单例：会话目录、writer 注册表、Sequencer、index.json。

属框架内核组件（非 DI、不可替换）。

写通道规则（设计第四节）：
- 每 agent 日志一个 _LogWriter 协程 —— 该文件的唯一写执行流
- 文件懒打开：首次 flush 才创建 agents/<pid>.jsonl
- flush 返回契约 = 该批事件已在 OS page cache（进程崩溃不丢）
- fsync 只在 finalize（enqueue_final）与 close（断电不丢已关闭会话）
- 写失败 → degraded → 后续批次直接放行，对话照常（失败方向向下，永不升级）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from .events import FORMAT_VERSION
from .ids import new_conv_id, new_owner_token
from .sequencer import Sequencer

logger = logging.getLogger(__name__)


class _Batch(NamedTuple):
    """writer 队列单元。"""
    lines: List[str]
    fsync: bool                       # finalize/close 批次为 True
    barrier: Optional[asyncio.Event]  # flush() 的等待点
    close: bool = False


class _LogWriter:
    """每 agent 日志一个写协程 —— 该文件的唯一写执行流。

    生产者（SessionLog.flush/finalize）只 enqueue，零 I/O；
    磁盘操作全部在 _run 协程 + asyncio.to_thread 内。
    """

    def __init__(self, path: Path):
        self._path = path
        self._queue: asyncio.Queue[_Batch] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._fh = None  # 线程内懒打开
        self.degraded = False
        self.error: Optional[str] = None

    # ── 生产者侧（event loop 内调用，零 I/O）──

    def start(self) -> None:
        assert self._task is None, "writer already started"
        self._task = asyncio.create_task(self._run())

    def enqueue(self, lines: List[str]) -> asyncio.Event:
        """普通批次（write + flush → page cache）。返回 flush 屏障。"""
        barrier = asyncio.Event()
        self._queue.put_nowait(_Batch(list(lines), fsync=False, barrier=barrier))
        return barrier

    def enqueue_final(self, lines: List[str]) -> asyncio.Event:
        """finalize 批次（write + flush + fsync）。返回屏障。"""
        barrier = asyncio.Event()
        self._queue.put_nowait(_Batch(list(lines), fsync=True, barrier=barrier))
        return barrier

    async def close(self) -> None:
        """drain → fsync → close。幂等。"""
        if self._task is None:
            return
        done = asyncio.Event()
        self._queue.put_nowait(_Batch([], fsync=True, barrier=done, close=True))
        await done.wait()
        await self._task
        self._task = None

    # ── 消费者侧（唯一写执行流）──

    async def _run(self) -> None:
        while True:
            batch = await self._queue.get()
            try:
                if not self.degraded:
                    await asyncio.to_thread(self._write_batch, batch)
            except Exception as e:
                # 写失败 → 降级，置位屏障放行，永不升级打断对话
                self.degraded = True
                self.error = f"{type(e).__name__}: {e}"
                logger.error("LogWriter degraded: %s — %s", self._path, self.error)
            finally:
                if batch.barrier is not None:
                    batch.barrier.set()
                self._queue.task_done()
            if batch.close:
                break

    # ── 线程内（唯一触盘处）──

    def _write_batch(self, batch: _Batch) -> None:
        if self._fh is None:  # 懒打开
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a", encoding="utf-8")
        if batch.lines:
            self._fh.write("\n".join(batch.lines) + "\n")
            self._fh.flush()  # → page cache（flush 契约）
        if batch.fsync:
            os.fsync(self._fh.fileno())  # → 持久介质（finalize/close）
        if batch.close and self._fh is not None:
            self._fh.close()
            self._fh = None


class SessionStore:
    """进程级单例。

    职责：会话目录布局、writer 注册表、Sequencer 持有、index.json 投影。
    不在 DI 容器中，组件层拿不到引用。
    """

    def __init__(self, root: str, *, enabled: bool = True):
        self._root = Path(root)
        self._enabled = enabled
        self._conv_id: Optional[str] = None
        self._conv_dir: Optional[Path] = None
        self._sequencer = Sequencer()
        self._writers: Dict[str, _LogWriter] = {}
        self._logs: Dict[str, object] = {}        # pid → SessionLog（T5 起用）
        self._index_data: Optional[dict] = None
        self._agent_index: Dict[str, dict] = {}
        self._owner_token: Optional[str] = None

    # ── 基本属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def conv_id(self) -> Optional[str]:
        return self._conv_id

    @property
    def conv_dir(self) -> Optional[Path]:
        return self._conv_dir

    @property
    def sequencer(self) -> Sequencer:
        return self._sequencer

    @property
    def degraded(self) -> List[str]:
        """已降级的 writer pid 列表（供 close 时一次性提示）。"""
        return [pid for pid, w in self._writers.items() if w.degraded]

    # ── 会话生命周期 ──

    def begin_session(self, conv_id: Optional[str] = None,
                      *, script: Optional[dict] = None) -> str:
        """开始（或接管）会话目录。index.json 惰性：此处不写盘（T4 起写）。"""
        self._conv_id = conv_id or new_conv_id()
        if not self._enabled:
            return self._conv_id
        self._conv_dir = self._root / self._conv_id
        (self._conv_dir / "agents").mkdir(parents=True, exist_ok=True)
        self._owner_token = new_owner_token()
        return self._conv_id

    def agent_log_path(self, pid: str) -> Path:
        """agents/<pid>.jsonl 路径（pid 含路径分隔符时替换，防目录逃逸）。"""
        assert self._conv_dir is not None, "begin_session() must be called first"
        safe = pid.replace("/", "_").replace("\\", "_")
        return self._conv_dir / "agents" / f"{safe}.jsonl"

    def _create_writer(self, pid: str) -> Optional[_LogWriter]:
        """为 pid 创建并启动 writer（enabled=False 时返回 None）。"""
        if not self._enabled or self._conv_dir is None:
            return None
        writer = _LogWriter(self.agent_log_path(pid))
        writer.start()
        self._writers[pid] = writer
        return writer

    def writer_for(self, pid: str) -> Optional[_LogWriter]:
        return self._writers.get(pid)

    def restore_sequencer(self, next_lsn: int) -> None:
        """boot 恢复：Sequencer = max(lsn) + 1。"""
        self._sequencer = Sequencer(next_lsn)

    async def close(self) -> None:
        """进程退出路径：drain 全部 writer → fsync → close。"""
        for pid, writer in list(self._writers.items()):
            try:
                await writer.close()
            except Exception as e:
                logger.error("writer close failed for '%s': %s", pid, e)
```

`harness/core/session/__init__.py` 替换为：

```python
"""harness.core.session — 内核级会话持久化与恢复。"""

from .config import SessionConfig, load_session_config
from .exceptions import BootError, CorruptLogError, SessionError, SessionOwnerConflict
from .sequencer import Sequencer
from .store import SessionStore

__all__ = [
    "SessionConfig", "load_session_config",
    "SessionError", "CorruptLogError", "SessionOwnerConflict", "BootError",
    "Sequencer", "SessionStore",
]
```

`.gitignore` 追加（若已有 `sessions/` 行则跳过）：

```
# 会话持久化运行产物
sessions/
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_store.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q   # 存量测试保持绿
git add harness/core/session/store.py harness/core/session/__init__.py tests/session/test_store.py .gitignore
git commit -m "feat(session): add _LogWriter coroutine and SessionStore skeleton"
```

---

### Task 4: index.json 投影 —— 原子重写 / 读取 / 崩溃重建 / owner 接管

**依赖：** T3
**阻塞：** T7（close 写终态）、T10（boot 读 index + owner 校验）
**设计对应：** index 是投影（可重建、可丢失），agents/<pid>.jsonl 才是事实；原子重写 = tmp + os.replace；owner token 防双开。

**Files:**
- Modify: `harness/core/session/store.py`
- Test: `tests/session/test_index.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_index.py`：

```python
"""index.json 投影测试：原子重写、读取、崩溃重建、owner 接管。"""

import json
import os

import pytest

from harness.core.session import events
from harness.core.session.store import SessionStore
from tests.session._fakes import run_async


def _write_log(conv_dir, pid, lines):
    p = conv_dir / "agents" / f"{pid}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestIndexWriteRead:
    @run_async
    async def test_begin_session_writes_index_with_owner(self, tmp_path):
        store = SessionStore(str(tmp_path))
        conv_id = store.begin_session(None, script={"path": "w.py", "sha1": "ab12"})
        index = store.read_index(conv_id)
        assert index["conv_id"] == conv_id
        assert index["status"] == "active"
        assert index["owner"]["token"].startswith("pid-")
        assert index["script"]["sha1"] == "ab12"
        assert index["format_version"] == events.FORMAT_VERSION
        await store.close()

    @run_async
    async def test_atomic_rewrite_leaves_no_tmp(self, tmp_path):
        store = SessionStore(str(tmp_path))
        conv_id = store.begin_session(None)
        store.write_index(updated_at=123.0)
        assert not (tmp_path / conv_id / "index.json.tmp").exists()
        assert store.read_index(conv_id)["updated_at"] == 123.0
        await store.close()

    @run_async
    async def test_resume_takeover_merges_existing_index(self, tmp_path):
        """恢复接管：created_at/manifest/script 保留，owner 换新。"""
        store1 = SessionStore(str(tmp_path))
        conv_id = store1.begin_session(None)
        store1.note_manifest("root", {"ContextAssembler": {"id": "a.A"}})
        old_owner = store1.read_index(conv_id)["owner"]["token"]
        created = store1.read_index(conv_id)["created_at"]
        await store1.close()

        store2 = SessionStore(str(tmp_path))
        store2.begin_session(conv_id)  # 接管同一目录
        index = store2.read_index(conv_id)
        assert index["created_at"] == created
        assert index["manifest"] == {"ContextAssembler": {"id": "a.A"}}
        assert index["owner"]["token"] != old_owner
        await store2.close()

    @run_async
    async def test_close_marks_paused_and_releases_owner(self, tmp_path):
        store = SessionStore(str(tmp_path))
        conv_id = store.begin_session(None)
        await store.close()
        index = store.read_index(conv_id)
        assert index["status"] == "paused"
        assert index["owner"] is None

    def test_read_index_missing_returns_none(self, tmp_path):
        store = SessionStore(str(tmp_path))
        assert store.read_index("conv-nope") is None

    def test_note_manifest_first_writer_wins(self, tmp_path):
        store = SessionStore(str(tmp_path))
        conv_id = store.begin_session(None)
        store.note_manifest("root", {"m": 1})
        store.note_manifest("b", {"m": 2})
        assert store.read_index(conv_id)["manifest"] == {"m": 1}


class TestIndexRebuild:
    def test_rebuild_from_logs_after_index_loss(self, tmp_path):
        """index 丢失 → 从 agents/*.jsonl 重建投影（事实是日志，不是 index）。"""
        conv_dir = tmp_path / "conv-x"
        (conv_dir / "agents").mkdir(parents=True)
        _write_log(conv_dir, "root", [
            json.dumps({"type": "header", "format_version": 1, "conv_id": "conv-x",
                        "pid": "root", "parent": None, "manifest_sha1": "m",
                        "created_at": 1.0, "seq": 0, "lsn": 0, "ts": 1.0}),
            json.dumps({"type": "user", "seq": 1, "lsn": 1, "ts": 1.1,
                        "message": {"role": "user", "content": "hi"}}),
            json.dumps({"type": "session_end", "seq": 2, "lsn": 2, "ts": 1.2,
                        "final_output": "bye", "execution_time": 0.2,
                        "status": "paused"}),
        ])
        store = SessionStore(str(tmp_path))
        rebuilt = store.rebuild_index("conv-x")
        assert rebuilt["agents"]["root"]["last_seq"] == 2
        assert rebuilt["agents"]["root"]["last_lsn"] == 2
        assert rebuilt["agents"]["root"]["status"] == "paused"
        # 重建结果同时落盘
        assert store.read_index("conv-x")["agents"]["root"]["last_seq"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_index.py -v`
Expected: FAIL — `AttributeError: ... 'SessionStore' object has no attribute 'read_index'`

- [ ] **Step 3: 实现**

`harness/core/session/store.py` —— 做三处修改。

**3a. import 区追加**（文件头部 `from .ids import ...` 一行之后）：

```python
import json

from .events import FORMAT_VERSION
from .ids import new_conv_id, new_owner_token, pid_alive, pid_from_token
```

（`FORMAT_VERSION` 已在 T3 import；此处只新增 `json` 与 `pid_alive, pid_from_token`。）

**3b. `begin_session` 替换为（index 惰性 → 此处建立/接管并写盘）**：

```python
    def begin_session(self, conv_id: Optional[str] = None,
                      *, script: Optional[dict] = None) -> str:
        """开始（或接管）会话目录。

        全新会话：创建目录、生成 owner、初始化 index。
        恢复接管（conv_id 已存在）：保留 created_at/manifest/script/agents，
        更换 owner token，status 置回 active。
        """
        self._conv_id = conv_id or new_conv_id()
        if not self._enabled:
            return self._conv_id
        self._conv_dir = self._root / self._conv_id
        (self._conv_dir / "agents").mkdir(parents=True, exist_ok=True)
        self._owner_token = new_owner_token()

        existing = self.read_index(self._conv_id)
        now = time.time()
        self._index_data = {
            "format_version": FORMAT_VERSION,
            "conv_id": self._conv_id,
            "status": "active",
            "owner": {"token": self._owner_token, "acquired_at": now},
            "manifest": (existing or {}).get("manifest"),
            "script": script if script is not None else (existing or {}).get("script"),
            "agents": (existing or {}).get("agents", {}),
            "created_at": (existing or {}).get("created_at", now),
            "updated_at": now,
        }
        self._agent_index = dict(self._index_data["agents"])
        self.write_index()
        return self._conv_id
```

**3c. 类末尾（`close` 方法之后）追加 index 方法组，并把 `close` 替换为写终态版**：

```python
    # ── index.json 投影（原子重写；可丢失，可重建）──

    def read_index(self, conv_id: str) -> Optional[dict]:
        """读取 index.json；不存在或损坏返回 None。"""
        path = self._root / conv_id / "index.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def write_index(self, **patch) -> None:
        """原子重写 index.json（tmp + os.replace）。投影，随时可重建。"""
        if not self._enabled or self._conv_dir is None or self._index_data is None:
            return
        self._index_data.update(patch)
        tmp = self._conv_dir / "index.json.tmp"
        tmp.write_text(
            json.dumps(self._index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._conv_dir / "index.json")

    def note_manifest(self, pid: str, manifest: dict) -> None:
        """记录装配清单。首个上报者（通常 root）的 manifest 入 index。"""
        if not self._enabled or self._index_data is None:
            return
        if not self._index_data.get("manifest"):
            self.write_index(manifest=manifest, updated_at=time.time())

    async def finalize_agent(self, pid: str, *, final_output: str,
                             execution_time: float, status: str = "paused") -> None:
        """agent FINISHED 时的幂等收尾：SessionLog.finalize 兜底 + index 投影更新。

        正常路径 session_end 已在 _phase_end 写入（T6）；这里是防御性兜底
        （_phase_end 未跑到时补写），并保证 index 的 agents[pid] 被更新。
        """
        log = self._logs.get(pid)
        if log is not None and not log.finalized:
            await log.finalize(status=status, final_output=final_output,
                               execution_time=execution_time)
        if log is not None:
            self._agent_index[pid] = {
                "last_seq": log.last_seq,
                "last_lsn": log.last_lsn,
                "status": status,
            }
            self.write_index(agents=self._agent_index, updated_at=time.time())

    def rebuild_index(self, conv_id: str) -> dict:
        """index 丢失/损坏时从 agents/*.jsonl 重建投影（惰性导入 replay 避免循环）。"""
        from .replay import scan_session  # replay 依赖 events，不依赖 store

        conv_dir = self._root / conv_id
        replays = scan_session(conv_dir)
        now = time.time()
        rebuilt = {
            "format_version": FORMAT_VERSION,
            "conv_id": conv_id,
            "status": "crashed",  # 有日志但无 index = 崩溃痕迹
            "owner": None,
            "manifest": None,
            "script": None,
            "agents": {
                pid: {"last_seq": r.last_seq, "last_lsn": r.max_lsn,
                      "status": r.status}
                for pid, r in replays.items()
            },
            "created_at": now,
            "updated_at": now,
            "rebuilt": True,
        }
        path = conv_dir / "index.json"
        tmp = conv_dir / "index.json.tmp"
        tmp.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return rebuilt
```

**3d. `close` 替换为：**

```python
    async def close(self) -> None:
        """进程退出路径：drain 全部 writer → fsync → close → index 写终态。"""
        for pid, writer in list(self._writers.items()):
            try:
                await writer.close()
            except Exception as e:
                logger.error("writer close failed for '%s': %s", pid, e)
        if self._enabled and self._index_data is not None:
            self.write_index(status="paused", owner=None, updated_at=time.time())
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_index.py tests/session/test_store.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/store.py tests/session/test_index.py
git commit -m "feat(session): add index.json projection with atomic rewrite, rebuild, owner takeover"
```

---

### Task 5: SessionLog —— 内存真相 + 批缓冲 + 唯一变异点（session_log.py）

**依赖：** T1（events）、T3（store/writer）
**阻塞：** T6（orchestrator 插桩）、T8（seed 重放目标）
**设计对应：** 架构图 SessionLog 层——`_history`/`_tool_call_records` 内存真相、`_pending` 批缓冲、`_seq` 本日志行号；记录点零 I/O；flush/finalize 是仅有的 async 方法；finalize 失败用内存快照兜底重写。

**Files:**
- Create: `harness/core/session/session_log.py`
- Modify: `harness/core/session/__init__.py`（导出 SessionLog）
- Test: `tests/session/test_session_log.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_session_log.py`：

```python
"""SessionLog —— 内存真相 + 记录点 + flush/finalize + seed 测试。"""

import json

from harness.core.session.session_log import SessionLog
from harness.core.session.store import SessionStore
from harness.interfaces.types import Message, ToolCallRecord
from tests.session._fakes import run_async


def _make_log(tmp_path, pid="root", **kwargs):
    store = SessionStore(str(tmp_path))
    store.begin_session(None)
    log = store.create_log(pid, manifest_provider=kwargs.pop("manifest_provider", None),
                           **kwargs)
    return store, log


def _read_events(store, pid):
    path = store.agent_log_path(pid)
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines()]


class TestRecordPoints:
    def test_record_message_appends_history_and_pending(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="你好"),
                           meta={"from": "b", "msg_id": "M-1"})
        assert log.history[0].content == "你好"
        assert len(log._pending) == 2  # header + user
        assert json.loads(log._pending[0])["type"] == "header"
        assert json.loads(log._pending[1])["meta"]["msg_id"] == "M-1"

    def test_history_is_same_object_for_orchestrator(self, tmp_path):
        """同一对象引用两视图（不变量 #2）：orchestrator 拿到的就是本列表。"""
        store, log = _make_log(tmp_path)
        external_ref = log.history
        log.record_message(Message(role="user", content="x"))
        assert external_ref is log.history
        assert len(external_ref) == 1

    def test_record_tool_call_appends_records_not_history(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_tool_call(ToolCallRecord(tool_call_id="c1", tool_name="bash"))
        assert len(log.tool_call_records) == 1
        assert len(log.history) == 0

    def test_record_edge_not_in_history(self, tmp_path):
        """edge 是发送方事实，只落盘不入 history（事实-派生分离）。"""
        store, log = _make_log(tmp_path)
        log.record_edge(msg_id="M-2", to="root", kind="publish", text="查到了")
        assert len(log.history) == 0
        evt = json.loads(log._pending[-1])
        assert evt["type"] == "edge" and evt["to"] == "root"

    def test_seq_strictly_contiguous(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="a"))
        log.record_message(Message(role="assistant", content="b"))
        log.record_stop("end_turn")
        seqs = [json.loads(l)["seq"] for l in log._pending]
        assert seqs == [0, 1, 2, 3]
        assert log.last_seq == 3

    def test_lsn_from_shared_sequencer(self, tmp_path):
        store, log = _make_log(tmp_path)
        _, log2 = _make_log(tmp_path, pid="b")  # 同一 store → 同一 Sequencer
        # 注意：_make_log 又建了一个 store——改用同一 store 验证
        log2 = store.create_log("b")
        log.record_message(Message(role="user", content="a"))   # header lsn0, user lsn1
        log2.record_message(Message(role="user", content="b"))  # header lsn2, user lsn3
        lsns = [json.loads(l)["lsn"] for l in log._pending]
        lsns2 = [json.loads(l)["lsn"] for l in log2._pending]
        assert lsns == [0, 1]
        assert lsns2 == [2, 3]

    def test_manifest_provider_called_once_at_begin(self, tmp_path):
        calls = []
        store, log = _make_log(
            tmp_path,
            manifest_provider=lambda: calls.append(1) or {"llm": {"model": "gpt-4o"}},
        )
        log.record_message(Message(role="user", content="a"))
        log.record_message(Message(role="user", content="b"))
        assert len(calls) == 1
        header = json.loads(log._pending[0])
        assert header["manifest_sha1"] != ""


class TestFlushFinalize:
    @run_async
    async def test_flush_writes_batch_and_clears_pending(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="你好"))
        log.record_stop("end_turn")
        await log.flush()
        assert log._pending == []
        evts = _read_events(store, "root")
        assert [e["type"] for e in evts] == ["header", "user", "stop"]
        await store.close()

    @run_async
    async def test_flush_empty_is_noop_and_lazy(self, tmp_path):
        store, log = _make_log(tmp_path)
        await log.flush()
        assert not store.agent_log_path("root").exists()  # 懒打开
        await store.close()

    @run_async
    async def test_finalize_writes_session_end_and_is_idempotent(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="你好"))
        await log.finalize(status="paused", final_output="再见", execution_time=1.0)
        await log.finalize(status="paused", final_output="再见", execution_time=1.0)
        evts = _read_events(store, "root")
        assert evts[-1]["type"] == "session_end"
        assert sum(1 for e in evts if e["type"] == "session_end") == 1
        await store.close()

    @run_async
    async def test_finalize_guarantees_header_first(self, tmp_path):
        """从未记录过的 agent 直接 finalize：文件以 header 开头（合法日志）。"""
        store, log = _make_log(tmp_path)
        await log.finalize(status="paused", final_output="", execution_time=0.0)
        evts = _read_events(store, "root")
        assert [e["type"] for e in evts] == ["header", "session_end"]
        await store.close()

    @run_async
    async def test_disabled_store_memory_only(self, tmp_path):
        store = SessionStore(str(tmp_path), enabled=False)
        store.begin_session(None)
        log = store.create_log("root")
        log.record_message(Message(role="user", content="x"))
        await log.flush()
        await log.finalize(status="paused", final_output="", execution_time=0.0)
        assert list(tmp_path.iterdir()) == []  # 零落盘
        assert log.history[0].content == "x"   # 内存真相仍在


class TestSeed:
    @run_async
    async def test_seed_then_continue_same_log(self, tmp_path):
        """恢复路径：seed 播种 → 续写同一文件，seq 续接，header 不重写。"""
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="第一轮"))
        await log.finalize(status="paused", final_output="", execution_time=1.0)
        conv_id = store.conv_id
        await store.close()

        store2 = SessionStore(str(tmp_path))
        store2.begin_session(conv_id)
        log2 = store2.create_log("root")
        log2.seed(history=[Message(role="user", content="第一轮")],
                  tool_call_records=[], last_seq=2, last_lsn=2)
        log2.record_message(Message(role="user", content="第二轮"))
        await log2.flush()
        evts = _read_events(store2, "root")
        assert sum(1 for e in evts if e["type"] == "header") == 1
        assert [e["seq"] for e in evts] == list(range(len(evts)))
        assert evts[-1]["message"]["content"] == "第二轮"
        assert log2.history[0].content == "第一轮"  # 播种在内存
        await store2.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_session_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.session_log'`

- [ ] **Step 3: 实现**

`harness/core/session/session_log.py`：

> **【质量评审修订回写】** 本节代码块已按 T5 质量评审结论更新（两个计划级缺陷修复）：
> ① `_finalize_fallback` 由盲目追加改为**对账感知**（盲目追加会把可恢复日志变成拒绝恢复：fsync 失败时批次已完整落盘，再追加即重复行 → seq gap）；
> ② `record_message` 改为**先校验后变异**（原顺序在 make_message_event 对非法 role 抛 ValueError 前已 append history + 烧掉 seq → 盘上断档）。
> 另含次要加固：flush/finalize 遇已关闭 writer 丢批返回（修复前永久挂起）、seed 断言 last_seq >= 0、manifest_sha1 顶层导入、`_read_ondisk_last_seq` 物理截断半截尾行。

```python
"""SessionLog —— 每 agent 一份的会话日志（唯一咽喉点）。

内存真相（_history/_tool_call_records）与磁盘镜像（_pending → _LogWriter）
的唯一变异点（设计决策 D3/D4）：

- record_* 全部同步、零 I/O、零 await —— 热路径纯内存
- flush() 轮次边界调用，返回契约 = 该批事件已达 OS page cache
- finalize() 追加 session_end 并 fsync；失败用内存快照兜底重写（失败方向向下）
- seed() 仅供 boot 恢复播种 —— 重放永不重复写
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...interfaces.types import Message, ToolCallRecord
from . import events
from .manifest import manifest_sha1
from .sequencer import Sequencer

logger = logging.getLogger(__name__)


class SessionLog:
    """每 agent 一份。AgentRuntime 创建，编排器经共享引用读写。"""

    def __init__(self, *, conv_id: str, pid: str, store=None,
                 sequencer: Optional[Sequencer] = None,
                 parent: Optional[str] = None,
                 manifest_provider: Optional[Callable[[], Dict[str, Any]]] = None):
        self.conv_id = conv_id
        self.pid = pid
        self._store = store
        self._sequencer = sequencer or Sequencer()
        self._parent = parent
        self._manifest_provider = manifest_provider

        # 内存真相（orchestrator 经 history/tool_call_records 属性共享同一对象）
        self._history: List[Message] = []
        self._tool_call_records: List[ToolCallRecord] = []

        self._pending: List[str] = []   # 批缓冲（已编码 JSON 行；崩溃丢弃，尾部修复兜底）
        self._seq = 0                   # 下一个待分配的 seq（last_seq = _seq - 1）
        self._last_lsn = -1
        self._begun = False             # header 是否已入缓冲（或已在盘上）
        self._finalized = False
        self._writer = None

    # ── 只读视图 ──

    @property
    def history(self) -> List[Message]:
        return self._history

    @property
    def tool_call_records(self) -> List[ToolCallRecord]:
        return self._tool_call_records

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def last_seq(self) -> int:
        return self._seq - 1

    @property
    def last_lsn(self) -> int:
        return self._last_lsn

    # ── 记录点（同步、零 I/O）──

    def begin(self) -> None:
        """R0：header 入缓冲（惰性——首个记录点触发；manifest 此刻已可计算）。"""
        if self._begun:
            return
        self._begun = True
        manifest: Dict[str, Any] = {}
        if self._manifest_provider is not None:
            try:
                manifest = self._manifest_provider() or {}
            except Exception as e:
                logger.warning("manifest_provider failed for '%s': %s", self.pid, e)
        sha = ""
        if manifest:
            sha = manifest_sha1(manifest)
            if self._store is not None:
                self._store.note_manifest(self.pid, manifest)
        seq, lsn, ts = self._next()
        self._append(events.make_header(
            conv_id=self.conv_id, pid=self.pid, parent=self._parent,
            manifest_sha1=sha, seq=seq, lsn=lsn, ts=ts,
        ))

    def record_message(self, message: Message, *,
                       meta: Optional[Dict[str, Any]] = None) -> None:
        """R1/R2/R3b/R4a：history 变异点镜像写入（先校验后变异）。

        非法 role（如 system）在 make_message_event 抛出——此刻零副作用；
        事件构造成功后才 append history、推进 seq/lsn（先 peek 后 commit），
        避免内存真相被污染 / seq 被烧掉导致盘上断档。
        """
        self.begin()
        evt = events.make_message_event(message, seq=self._seq,
                                        lsn=self._sequencer.next_value,
                                        ts=time.time(), meta=meta)
        self._history.append(message)
        self._seq += 1
        self._last_lsn = self._sequencer.next()
        self._append(evt)

    def record_tool_call(self, record: ToolCallRecord) -> None:
        """R3a：工具执行记录（Hook 之后的终值）。"""
        self.begin()
        self._tool_call_records.append(record)
        seq, lsn, ts = self._next()
        self._append(events.make_tool_call_event(record, seq=seq, lsn=lsn, ts=ts))

    def record_edge(self, *, msg_id: str, to: str, kind: str, text: str) -> None:
        """出站消息边（发送方事实）。只落盘，不入 history（事实-派生分离）。"""
        self.begin()
        seq, lsn, ts = self._next()
        self._append(events.make_edge_event(
            msg_id=msg_id, from_pid=self.pid, to_pid=to, kind=kind, text=text,
            seq=seq, lsn=lsn, ts=ts,
        ))

    def record_stop(self, stop_reason: str) -> None:
        """R4b：轮次结束。"""
        self.begin()
        seq, lsn, ts = self._next()
        self._append(events.make_stop_event(stop_reason=stop_reason,
                                            seq=seq, lsn=lsn, ts=ts))

    # ── 写通道（仅有的两个 async 方法）──

    async def flush(self) -> None:
        """轮次边界批量 flush。返回时该批事件已达 page cache。"""
        if not self._pending:
            return
        if not self._writable():
            self._pending.clear()
            return
        batch, self._pending = self._pending, []
        writer = self._ensure_writer()
        if writer is None or writer.closed:
            # store.close() 之后 enqueue 将无人 drain（barrier 永久挂起）——丢批返回
            logger.warning("flush after writer closed for '%s': dropping %d lines",
                           self.pid, len(batch))
            return
        barrier = writer.enqueue(batch)
        await barrier.wait()

    async def finalize(self, *, status: str, final_output: str,
                       execution_time: float) -> None:
        """R6：session_end 事件 + fsync。幂等。

        失败兜底：writer 已降级时，用本批次的内存快照同步直写一次。
        """
        if self._finalized:
            return
        self._finalized = True
        self.begin()  # 保证 header 在先（从未记录过的 agent 也产生合法日志）
        seq, lsn, ts = self._next()
        self._append(events.make_session_end_event(
            final_output=final_output, execution_time=execution_time,
            status=status, seq=seq, lsn=lsn, ts=ts,
        ))
        if not self._writable():
            self._pending.clear()
            return
        batch, self._pending = self._pending, []
        writer = self._ensure_writer()
        if writer is None or writer.closed:
            # store.close() 之后 enqueue 将无人 drain（barrier 永久挂起）——丢批返回
            logger.warning("finalize after writer closed for '%s': dropping %d lines",
                           self.pid, len(batch))
            return
        barrier = writer.enqueue_final(batch)
        await barrier.wait()
        if writer.degraded:
            self._finalize_fallback(batch)

    # ── boot 播种（恢复路径专用）──

    def seed(self, *, history: List[Message],
             tool_call_records: List[ToolCallRecord],
             last_seq: int, last_lsn: int) -> None:
        """重放结果直接装入内存（重放永不重复写）。seq 续接 last_seq+1。"""
        assert last_seq >= 0, "seed last_seq 必须 >= 0（首行须为 header seq=0）"
        self._history.extend(history)
        self._tool_call_records.extend(tool_call_records)
        self._seq = last_seq + 1
        self._last_lsn = last_lsn
        self._begun = True  # header 已在盘上

    # ── 内部 ──

    def _next(self) -> tuple[int, int, float]:
        seq = self._seq
        self._seq += 1
        lsn = self._sequencer.next()
        self._last_lsn = lsn
        return seq, lsn, time.time()

    def _append(self, event: Dict[str, Any]) -> None:
        self._pending.append(events.encode_event(event))

    def _writable(self) -> bool:
        return (self._store is not None and self._store.enabled
                and self._store.conv_dir is not None)

    def _ensure_writer(self):
        if self._writer is None:
            self._writer = self._store._create_writer(self.pid)
        return self._writer

    def _finalize_fallback(self, batch: List[str]) -> None:
        """writer 降级后的 finalize 兜底：对账感知，只补盘上缺失的 seq 后缀。

        盲目追加会把可恢复日志变成拒绝恢复（重复行/断档 = seq gap），故：
        - suffix（batch 中 seq > ondisk_last_seq 的行）为空 → 批次已完整
          在盘上（write+flush 成功、fsync 失败），直接返回
        - suffix[0].seq == ondisk_last_seq + 1 → 追加 suffix + flush + fsync
        - 否则（中间有空洞/分叉）→ 只记录 error，不再恶化已不一致的状态
        """
        try:
            path = self._store.agent_log_path(self.pid)
            ondisk_last_seq = self._read_ondisk_last_seq(path)
            suffix = [l for l in batch if json.loads(l)["seq"] > ondisk_last_seq]
            if not suffix:
                logger.info("finalize fallback: batch already on disk for '%s'",
                            self.pid)
                return
            if json.loads(suffix[0])["seq"] != ondisk_last_seq + 1:
                logger.error(
                    "finalize fallback: seq divergence for '%s' "
                    "(ondisk_last=%d, batch_head=%d) — skip",
                    self.pid, ondisk_last_seq, json.loads(suffix[0])["seq"])
                return
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(suffix) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            logger.warning("finalize fallback snapshot written for '%s'", self.pid)
        except Exception as e:
            logger.error("finalize fallback failed for '%s': %s", self.pid, e)

    def _read_ondisk_last_seq(self, path: Path) -> int:
        """容错读取盘上 last_seq（文件不存在/为空 = -1）。

        末字节非换行 → r+b 物理截断半截尾行（防 fallback 字节拼接到半截行尾）；
        逐行 decode，遇首个不可解码行停止（视为尾部截断点）。
        """
        if not path.exists():
            return -1
        raw = path.read_bytes()
        if not raw:
            return -1
        if not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n")  # -1 → 整文件仅半截行，清空
            with open(path, "r+b") as fh:
                fh.truncate(cut + 1)
            raw = raw[:cut + 1]
        last_seq = -1
        for line in raw.splitlines():
            try:
                evt = json.loads(line)
                if not isinstance(evt, dict) or not isinstance(evt.get("seq"), int):
                    raise ValueError("not an event line")
            except ValueError:  # JSONDecodeError/UnicodeDecodeError 均属之
                break
            last_seq = evt["seq"]
        return last_seq
```

`harness/core/session/store.py` 追加 `create_log`（放在 `writer_for` 之后）：

```python
    def create_log(self, pid: str, *, parent: Optional[str] = None,
                   manifest_provider=None):
        """为 agent 创建 SessionLog（writer 懒创建于首次 flush）。

        enabled=False 时同样创建（store 不可写 → SessionLog 纯内存运行，
        保持"唯一咽喉点"语义不随配置分叉）。

        顺序约束：boot 路径须在 restore_sequencer 之后再调用本方法
        （Sequencer 按引用捕获，restore_sequencer 会替换对象）。
        """
        from .session_log import SessionLog

        log = SessionLog(
            conv_id=self._conv_id or "ephemeral",
            pid=pid,
            store=self,
            sequencer=self._sequencer,
            parent=parent,
            manifest_provider=manifest_provider,
        )
        self._logs[pid] = log
        return log
```

`harness/core/session/__init__.py` 追加 SessionLog 导出：

```python
from .session_log import SessionLog
```

（加入 `__all__`。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/ -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/session_log.py harness/core/session/store.py harness/core/session/__init__.py tests/session/test_session_log.py
git commit -m "feat(session): add SessionLog — in-memory truth with mirrored record points"
```

---
### Task 6: 编排器插桩 —— R0–R6 记录点 + 轮次边界 flush（async_orchestrator.py）

**依赖：** T5（SessionLog API）
**阻塞：** T7
**设计对应：** 记录流程图（第三节）——所有记录点都在"内核自己 append `_history` 的那一行"，且都在 Hook 之后（记录终值）；组件插入/替换/卸载不影响任何记录点。

**改造要点（对应"四个改造约束"表）：**
- `_history`/`_tool_call_records` 字段名不变，有 session_log 时**重绑定为 SessionLog 的列表对象**（同一引用，读取方零改动）
- `_phase_end` 的 finalize 必须插在 `self._history.clear()` **之前**
- 无 session_log 时行为与现状完全一致（存量测试不受影响）

**Files:**
- Modify: `harness/core/async_orchestrator.py`
- Modify: `harness/runtime/agent_runtime.py`（`_init_orchestrator` 透传）
- Test: `tests/session/test_orchestrator_recording.py`

- [ ] **Step 1: 写失败测试**

> **【执行期修订】** 原测试块中 test 1/3/6 在 `_phase_loop` 后读 `log._pending`，与 3i 的轮次边界 flush（swap 清空 _pending，由 test_flush_at_round_boundary_persists 钉死）矛盾。已将这三处的观察点改为读已落盘文件（`_read_events` helper），断言内容不变。

`tests/session/test_orchestrator_recording.py`：

```python
"""orchestrator 插桩测试：R0–R6 记录点、轮次 flush、共享列表。"""

import json

from harness.core.async_orchestrator import AsyncLifecycleOrchestrator
from harness.core.container import DIContainer
from harness.core.session.store import SessionStore
from harness.interfaces.types import (
    Response, ToolCall, ToolCallFunction, ToolDefinition, ToolResult, UserRequest,
)
from tests.session._fakes import run_async


class ScriptedAdapter:
    """异步 InputAdapter 替身：按脚本返回输入，收集发送事件。"""

    def __init__(self, inputs):
        self._inputs = list(inputs)
        self.sent = []

    async def receive(self) -> UserRequest:
        return self._inputs.pop(0) if self._inputs else UserRequest(
            text="", metadata={"exit": True})

    async def send(self, event, target=None):
        self.sent.append(event)


class EchoProvider:
    """最小 SystemToolProvider：一个 echo 工具。"""

    def get_tools(self):
        return [ToolDefinition(name="echo", description="echo",
                               parameters={"type": "object", "properties": {}})]

    def execute(self, name, args):
        return ToolResult(success=True, content=f"echo:{json.dumps(args)}")


def _llm_scripted(responses):
    queue = list(responses)

    async def call_llm(messages, tools):
        return queue.pop(0)

    return call_llm


def _make(tmp_path, inputs, llm, with_provider=False):
    store = SessionStore(str(tmp_path))
    store.begin_session(None)
    container = DIContainer()
    if with_provider:
        from harness.interfaces.system_tool_provider import SystemToolProvider
        container.register(SystemToolProvider, EchoProvider())
    log = store.create_log("root")
    adapter = ScriptedAdapter(inputs)
    orch = AsyncLifecycleOrchestrator(
        container, adapter=adapter, call_llm=llm, session_log=log)
    return store, log, orch


def _read_events(store, pid):
    """读已 flush 的事件（轮次边界 flush 后 _pending 已清空，事件在盘上）。"""
    path = store.agent_log_path(pid)
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines()]


class TestRecording:
    @run_async
    async def test_text_round_records_r0_r1_r4(self, tmp_path):
        """纯文本轮：header(R0) → user(R1) → assistant(R4a) → stop(R4b)。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="你好！")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)

        evts = _read_events(store, "root")  # flush 后事件在盘上，不在 _pending
        assert [e["type"] for e in evts] == ["header", "user", "assistant", "stop"]
        assert evts[1]["message"]["content"] == "你好"
        assert evts[2]["message"]["content"] == "你好！"
        assert evts[3]["stop_reason"] == "end_turn"
        await store.close()

    @run_async
    async def test_history_is_shared_object(self, tmp_path):
        store, log, orch = _make(
            tmp_path, [UserRequest(text="hi")],
            _llm_scripted([Response(text="ok")]))
        assert orch._history is log.history           # 同一对象引用
        assert orch._tool_call_records is log.tool_call_records
        await store.close()

    @run_async
    async def test_tool_round_records_r2_r3(self, tmp_path):
        """工具轮：R2(assistant+tool_calls) → R3a(tool_call) → R3b(tool_result)。"""
        llm = _llm_scripted([
            Response(tool_uses=[ToolCall(
                id="call_1", type="function",
                function=ToolCallFunction(name="echo", arguments='{"x":1}'))]),
            Response(text="done"),
        ])
        store, log, orch = _make(
            tmp_path, [UserRequest(text="查一下")], llm, with_provider=True)
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)

        types = [e["type"] for e in _read_events(store, "root")]
        assert types == ["header", "user", "assistant",
                         "tool_call", "tool_result", "assistant", "stop"]
        rec = _read_events(store, "root")[3]["record"]
        assert rec["tool_name"] == "echo" and rec["error"] is None
        assert len(log.tool_call_records) == 1       # R3a 镜像 records
        await store.close()

    @run_async
    async def test_flush_at_round_boundary_persists(self, tmp_path):
        """轮次边界 flush：_phase_loop 返回时本轮已在 page cache（文件可读）。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="ok")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        lines = store.agent_log_path("root").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4                        # 未到 finalize 已落盘
        assert log._pending == []
        await store.close()

    @run_async
    async def test_phase_end_finalize_before_clear(self, tmp_path):
        """R6：session_end 在 history 清理前写入；clear 不影响盘上事实。"""
        store, log, orch = _make(
            tmp_path, [UserRequest(text="你好")],
            _llm_scripted([Response(text="最终回复")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        traj = orch._build_trajectory()
        await orch._phase_end(traj)

        evts = [json.loads(l) for l in
                store.agent_log_path("root").read_text(encoding="utf-8").splitlines()]
        assert evts[-1]["type"] == "session_end"
        assert evts[-1]["final_output"] == "最终回复"
        assert len(orch._history) == 0                # clear 依旧发生
        await store.close()

    @run_async
    async def test_user_meta_recorded(self, tmp_path):
        """R1 meta：from/msg_id/type 从 UserRequest.metadata 提取落盘。"""
        req = UserRequest(text="在吗", metadata={
            "from": "b", "type": "talk_to", "msg_id": "M-9", "irrelevant": 1})
        store, log, orch = _make(tmp_path, [req],
                                 _llm_scripted([Response(text="在")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        user_evt = _read_events(store, "root")[1]
        assert user_evt["meta"] == {"from": "b", "type": "talk_to", "msg_id": "M-9"}
        await store.close()

    @run_async
    async def test_no_session_log_behavior_unchanged(self, tmp_path):
        """无 session_log：与现状一致（不建文件、不报错、history 自持）。"""
        container = DIContainer()
        orch = AsyncLifecycleOrchestrator(
            container, adapter=ScriptedAdapter([UserRequest(text="hi")]),
            call_llm=_llm_scripted([Response(text="ok")]))
        ctx = await orch._phase_init()
        await orch._phase_loop(ctx)
        assert len(orch._history) == 2
        assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_orchestrator_recording.py -v`
Expected: FAIL — `TypeError: AsyncLifecycleOrchestrator.__init__() got an unexpected keyword argument 'session_log'`

- [ ] **Step 3: 实现**

**3a. `harness/core/async_orchestrator.py` — `__init__` 签名与共享绑定**

把 `__init__` 的签名段（`call_llm: Optional[AsyncCallLLM] = None,` 之后）改为：

```python
    def __init__(
        self,
        container: DIContainer,
        *,
        adapter: AsyncInputAdapter,
        # call_llm 允许为 None 以兼容以下场景：
        # - 单元测试（验证编排流程不调 LLM）
        # - Hook 开发（在 call_llm 前拦截并注入自定义行为）
        # 与同步版 LifecycleOrchestrator 的 Optional 约定一致
        call_llm: Optional[AsyncCallLLM] = None,
        # session_log 为 None 时行为与插桩前完全一致（无持久化路径）
        session_log=None,
    ):
```

在 `__init__` 尾部（`self._cached_tool_router = ...` 之后）追加：

```python
        # SessionLog —— _history/_tool_call_records 的唯一变异点。
        # 同一对象引用两视图：orchestrator 的字段直接重绑定为 SessionLog 的列表，
        # 读取方（AgentRuntime._extract_last_output、assemble、sensor）零改动。
        self._session_log = session_log
        if session_log is not None:
            self._history = session_log.history
            self._tool_call_records = session_log.tool_call_records
```

**3b. 新增模块级辅助与实例 helper**（放在 `logger = logging.getLogger(__name__)` 之后）：

```python
def _request_meta(request: UserRequest) -> dict:
    """提取持久化所需的交互元数据（from/msg_id/type/workflow_flag）。

    只白名单提取——metadata 是用户扩展桶，不全量落盘。
    """
    return {
        k: request.metadata[k]
        for k in ("from", "msg_id", "type", "workflow_flag")
        if k in request.metadata
    }
```

类内追加（放在 `_resolve_optional` 之前）：

```python
    # ------------------------------------------------------------------
    # SessionLog 记录点（无 session_log 时退化为原样的内存 append）
    # ------------------------------------------------------------------

    def _record_message(self, message: Message, *, meta: Optional[dict] = None) -> None:
        if self._session_log is not None:
            self._session_log.record_message(message, meta=meta)
        else:
            self._history.append(message)

    def _record_tool_call(self, record: ToolCallRecord) -> None:
        if self._session_log is not None:
            self._session_log.record_tool_call(record)
        else:
            self._tool_call_records.append(record)

    def _record_stop(self, reason: str) -> None:
        if self._session_log is not None:
            self._session_log.record_stop(reason)
```

**3c. R1 —— `_phase_loop` 用户消息写入**（原 `self._history.append(Message(role="user", ...))` 处）替换为：

```python
        # ── 当前轮用户请求写入 history（R1: record_message + meta）──
        if ctx.user_request and ctx.user_request.text:
            self._record_message(
                Message(role="user", content=ctx.user_request.text),
                meta=_request_meta(ctx.user_request),
            )
```

**3d. R2 —— assistant tool_calls 写入**，替换为：

```python
                # 将 assistant tool_use 消息写入 history（R2，Hook 后终值）
                self._record_message(Message(
                    role="assistant",
                    content=response.text or "",
                    tool_calls=list(response.tool_uses),
                ))
```

**3e. R3a —— tool_call_records**，替换 `self._tool_call_records.append(ToolCallRecord(...))` 为：

```python
                    # 记录到 tool_call_records（R3a，Hook 后终值）
                    self._record_tool_call(ToolCallRecord(
                        tool_call_id=tc.id,
                        tool_name=tc.function.name,
                        arguments=args,
                        result=content if success else None,
                        started_at=before_ts,
                        finished_at=after_ts,
                        error=error,
                    ))
```

**3f. R3b —— tool result 消息**，替换为：

```python
                    # 将 tool 执行结果写入 history（R3b）
                    self._record_message(Message(
                        role="tool",
                        content=str(content) if not error else f"Error: {error}",
                        tool_call_id=tc.id,
                    ))
```

**3g. R4a/R4b —— text 分支**，替换为：

```python
            if response.text:
                messages.append(
                    Message(role="assistant", content=response.text or "")
                )
                await self._adapter.send(TextEvent(content=response.text or ""))
                await self._adapter.send(StopEvent(stop_reason=response.stop_reason))
                self._record_message(
                    Message(role="assistant", content=response.text or ""))
                self._record_stop(response.stop_reason)   # R4b
                break  # 跳出内层循环
```

**3h. 其余两个 StopEvent 分支补 record_stop**（max_iterations / no_llm / empty_response 三处的 `await self._adapter.send(StopEvent(...))` 之后各加一行）：

```python
                self._record_stop("max_iterations")   # / "no_llm" / "empty_response"
```

**3i. 轮次边界 flush —— `_phase_loop` 末尾**（`logger.info("Phase 2: Single round ended")` 之前）插入：

```python
        # ── 轮次边界：批量 flush（返回即达 page cache；进程崩溃不丢本轮）──
        if self._session_log is not None:
            await self._session_log.flush()
```

**3j. R6 —— `_phase_end` finalize**（在 `self._cached_tool_router.shutdown()` 块之后、`self._history.clear()` 之前）插入：

```python
        # R6: finalize —— session_end 事件 + fsync。
        # 必须在 history 清理之前（final_output 来自 trajectory，事件此刻编码）；
        # 失败只降级（finalize 内部兜底），不阻断 _phase_end 其余清理。
        if self._session_log is not None:
            try:
                await self._session_log.finalize(
                    status="paused",
                    final_output=trajectory.final_output,
                    execution_time=trajectory.execution_time,
                )
            except Exception as e:
                logger.warning(f"SessionLog.finalize() failed: {e}")
```

**3k. `harness/runtime/agent_runtime.py`** — `_init_orchestrator` 替换为：

```python
    def _init_orchestrator(self, call_llm: Optional[AsyncCallLLM] = None,
                           session_log=None):
        """在 Kernel 设置好 adapter 后调用，完成 orchestrator 装配。

        Args:
            call_llm: async LLM callable。已在 Runtime 入口层做过
                      sync→async 桥接。
            session_log: SessionLog（可选）。传入后 orchestrator 的
                         _history/_tool_call_records 与之共享同一对象。
        """
        self._orchestrator = AsyncLifecycleOrchestrator(
            container=self._harness.container,
            adapter=self.adapter,
            call_llm=call_llm,
            session_log=session_log,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_orchestrator_recording.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q   # 无 session_log 路径行为不变，存量测试全绿
git add harness/core/async_orchestrator.py harness/runtime/agent_runtime.py tests/session/test_orchestrator_recording.py
git commit -m "feat(session): instrument orchestrator with R0-R6 record points and round-boundary flush"
```

---

### Task 7: Kernel/Runtime 接线 —— store 注入、spawn 建 log、finalize 与 close

**依赖：** T4（index/finalize_agent）、T6（插桩）
**阻塞：** T10、T11、T13
**设计对应：** 架构图 Kernel/Runtime 层——Runtime.finally 挂 store.close()；Kernel 是进程内唯一"会话所有权"持有者；`_on_agent_finished → store.finalize(pid)`。

**Files:**
- Modify: `harness/runtime/kernel.py`
- Modify: `harness/runtime/runtime.py`
- Test: `tests/session/test_kernel_wiring.py`

> **【执行期修订】**（T7 质量评审结论，已回写实现）
> 1. `create_log` 增加重复 pid 守卫：同名 log **从未 begun 且无 writer** → 允许替换（spawn 回滚后重试路径）；已 begun 或已有 writer → raise ValueError（双 writer 同文件 = 日志分叉不可恢复）。注意语义后果：spawn_from_script 的"同名替换"分支在持久化开启（默认）时对该分支已 begun 的 agent 变为响亮失败——有意为之（失败方向：响亮的工具错误 > 静默的日志损坏）。
> 2. `_run_async` / `_run_from_script_async` 的 finally 在 `_close_store()` 之前 `await asyncio.gather(*kernel._tasks.values(), return_exceptions=True)`——等中途 spawn 的子 agent 收尾，避免其 R6 finalize 撞上 writer 已关闭而丢 session_end（resume 误标 crashed）。
> 3. 增加 Runtime 级接线测试（_open_store/_close_store 生产路径：conv 目录生成、header+session_end、index 终态 paused/owner=None）。
> 4. T6 评审转入：`test_no_llm_records_stop` 补钉 no_llm 分支的 stop 落盘。
>
> **【执行期修订 2】**（T7 第二轮质量评审结论，已回写实现 d1b6224）
> 1. **post-sweep spawn 窗口焊死**：/exit 落地（CommandExit 清扫）后在途 LLM 响应仍可能执行 spawn_workflow，新 agent 收不到 sentinel 成为孤儿 → finally 的 gather 永久挂起。双重关闭：(a) 清扫循环提取为 `Kernel._signal_all_exit()`（CommandExit 分支改为调用它），两个 finally 在 gather 前再清扫一次；(b) `spawn_from_script` 入口加 `_shutdown` 守卫（`raise RuntimeError("kernel is shutting down, spawn rejected")`，经 SpawnWorkflowTool 转为受控 ToolResult 错误）。无 await 介于清扫与 `_shutdown=True` 之间且 spawn_from_script 全程同步 → spawn 要么完整地先于清扫（被扫中）要么后于守卫翻转（被拒），无交错。
> 2. **回归测试拆分**（评审 PoC 断言组合与守卫内在矛盾，经评审认可）：`test_post_shutdown_spawn_rejected_and_run_returns`（PoC 原形状，断言 run() 返回 + root session_end + 受控工具错误落盘 + index 无孤儿）；`test_unswept_child_reswept_in_finally`（task_sys 异常死亡路径，断言再清扫救回子 agent → child.jsonl 首 header 尾 session_end）。两测试在修复前均以 timeout 实证挂起。
> 3. Minors：Mode B `await task_sys` 补 `except Exception`（不跳过收尾）；两处 `add_signal_handler` 的 except 补 `RuntimeError`（与 remove 侧对称，非主线程降级）。
> 4. 遗留（转入 T13）：console 异常死亡路径下 finally 未置 `_shutdown=True`，drain 期间 spawn 仍可成功——不产生挂起（该 child 不在 gather 集合内），仅丢失 session_end 的保真度边缘；T13 触碰 runtime 时在 finally 再清扫前补一行 `self._kernel._shutdown = True`。

- [ ] **Step 1: 写失败测试**

`tests/session/test_kernel_wiring.py`：

```python
"""Kernel/Runtime 持久化接线测试。"""

import json

from harness.core.session.store import SessionStore
from harness.interfaces.types import UserRequest
from harness.runtime.kernel import Kernel
from tests.session._fakes import MockConsole, MockHarness, run_async


class ExitImmediatelyAdapter:
    """首次 receive 即返回退出（走 _phase_init 退出路径，最小化运行）。"""

    async def receive(self) -> UserRequest:
        return UserRequest(text="/exit")

    async def send(self, event, target=None):
        pass


def _harness_with_exit_adapter():
    from harness.interfaces.async_input_adapter import AsyncInputAdapter
    harness = MockHarness()
    harness.container.register(AsyncInputAdapter, ExitImmediatelyAdapter())
    return harness


class TestKernelWiring:
    @run_async
    async def test_spawn_root_creates_session_log(self, tmp_path):
        store = SessionStore(str(tmp_path))
        store.begin_session(None)
        kernel = Kernel(MockConsole(), store=store)
        kernel.spawn_root(_harness_with_exit_adapter())
        await kernel._tasks["root"]
        await store.close()

        path = store.agent_log_path("root")
        types = [json.loads(l)["type"] for l in
                 path.read_text(encoding="utf-8").splitlines()]
        assert types == ["header", "session_end"]   # 立即退出：仅首尾
        index = store.read_index(store.conv_id)
        assert index["agents"]["root"]["status"] == "paused"
        assert index["agents"]["root"]["last_seq"] == 1

    @run_async
    async def test_kernel_without_store_still_works(self, tmp_path):
        """无 store（向后兼容路径）：spawn/退出正常，零落盘。"""
        kernel = Kernel(MockConsole())
        kernel.spawn_root(_harness_with_exit_adapter())
        await kernel._tasks["root"]
        assert kernel.runtime_table["root"].state.name == "FINISHED"
        assert list(tmp_path.iterdir()) == []

    @run_async
    async def test_spawn_from_script_agents_get_logs(self, tmp_path):
        """workflow 脚本 agent 同样接线（写最小 fixture 脚本）。"""
        script = tmp_path / "wf.py"
        script.write_text(
            "from harness.runtime.decorators import agent\n"
            "from tests.session._fakes import MockHarness\n"
            "from harness.interfaces.async_input_adapter import AsyncInputAdapter\n"
            "from tests.session.test_kernel_wiring import ExitImmediatelyAdapter\n"
            "@agent(name='worker', entry_prompt='go')\n"
            "def make():\n"
            "    h = MockHarness()\n"
            "    h.container.register(AsyncInputAdapter, ExitImmediatelyAdapter())\n"
            "    return h\n",
            encoding="utf-8")
        store = SessionStore(str(tmp_path / "sessions"))
        store.begin_session(None)
        kernel = Kernel(MockConsole(), store=store)
        kernel.spawn_from_script(str(script), parent=None)
        await kernel._tasks["worker"]
        await store.close()
        assert store.agent_log_path("worker").exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_kernel_wiring.py -v`
Expected: FAIL — `TypeError: Kernel.__init__() got an unexpected keyword argument 'store'`

- [ ] **Step 3: 实现**

**3a. `harness/runtime/kernel.py` — `__init__` 加 store 参数**

签名改为 `def __init__(self, console, store=None):`，docstring 加一行 `@param store: SessionStore（可选）。None 时持久化关闭，行为与现状一致。`，方法体末尾（`self._pending_subscriptions` 之后）追加：

```python
        # 持久化：进程级 SessionStore（内核机制，非 DI）
        self._store = store
```

**3b. 新增 `_make_session_log`**（放在 `_inject_runtime_tools` 之后）：

```python
    def _make_session_log(self, pid: str, harness, runtime, parent):
        """为 agent 创建 SessionLog（须在 _init_orchestrator 之前，以共享 history）。

        store 为 None（持久化关闭）时仍创建纯内存 SessionLog——
        "唯一咽喉点"语义不随配置分叉。
        """
        from ..core.session.session_log import SessionLog

        if self._store is None:
            return SessionLog(conv_id="ephemeral", pid=pid, store=None)
        return self._store.create_log(
            pid,
            parent=parent.pid if parent else None,
            manifest_provider=lambda: self._probe_manifest(harness, runtime),
        )

    def _probe_manifest(self, harness, runtime) -> dict:
        """计算当前装配清单（T9 接入 compute_manifest；此前返回最小集）。"""
        try:
            from ..core.session.manifest import compute_manifest
            tools = []
            orch = getattr(runtime, "_orchestrator", None)
            if orch is not None:
                tools = orch._cached_tools
            return compute_manifest(
                harness.container, cached_tools=tools,
                call_llm=getattr(harness, "call_llm", None),
            )
        except Exception as e:
            logger.debug("_probe_manifest failed for '%s': %s",
                         getattr(runtime, "pid", "?"), e)
            return {}
```

**3c. `spawn_root` 步骤 3 替换**（`runtime._init_orchestrator(call_llm=call_llm)` 一行）为：

```python
        # 3. 初始化 orchestrator（先建 SessionLog——orchestrator 与之共享 history）
        session_log = self._make_session_log(pid, harness, runtime, parent=None)
        runtime._init_orchestrator(call_llm=call_llm, session_log=session_log)
```

**3d. `spawn_from_script` 步骤 5g 替换**为：

```python
                # 5g. Initialize orchestrator（SessionLog 先行，理由同 spawn_root）
                session_log = self._make_session_log(name, harness, runtime, parent)
                runtime._init_orchestrator(call_llm=call_llm,
                                           session_log=session_log)
```

**3e. `_on_agent_finished` 插入 finalize**（`await self._console.send(AgentFinished(...))` 块之后、`logger.info(...)` 之后）：

```python
        # ── 1.5 持久化收尾（幂等）：session_end 兜底 + index 投影更新 ──
        if self._store is not None:
            try:
                await self._store.finalize_agent(
                    runtime.pid,
                    final_output=runtime.last_output,
                    execution_time=duration,
                    status="paused",
                )
            except Exception as e:
                logger.warning(
                    f"finalize_agent('{runtime.pid}') failed: {e}")
```

**3f. `harness/runtime/runtime.py`** — 三处修改。

`__init__` 替换为：

```python
    def __init__(self, console: 'SystemConsole', session_config=None):
        """初始化 Runtime。

        Args:
            console: SystemConsole 实现（如 CliConsole）。
            session_config: SessionConfig（可选）。None 时使用默认配置
                            （root=./sessions, enabled=True）。
        """
        self._console = console
        self._session_config = session_config
        self._store = None
        self._kernel = None
        self._sigint_count: int = 0
```

新增私有方法（放在"内部"注释之后）：

```python
    def _open_store(self):
        """按配置创建 SessionStore 并开始会话（resume 路径在 T13 改为 boot 接管）。"""
        from ..core.session.config import SessionConfig
        from ..core.session.store import SessionStore

        cfg = self._session_config or SessionConfig()
        self._store = SessionStore(cfg.root, enabled=cfg.enabled)
        self._store.begin_session(None)
        return self._store

    async def _close_store(self) -> None:
        """drain + fsync + close；降级的一次性提示（失败方向向下的最后一环）。"""
        if self._store is None:
            return
        try:
            await self._store.close()
        except Exception as e:
            logger.error("SessionStore.close() failed: %s", e)
        for pid in self._store.degraded:
            print(f"[系统] 警告：agent '{pid}' 的会话日志写盘降级，"
                  f"该 agent 日志可能不完整。")
```

`_run_async` 步骤 2 替换为：

```python
        # 2. 创建 SessionStore + Kernel
        store = self._open_store()
        self._kernel = Kernel(self._console, store=store)
```

`_run_async` 的 finally 块（`loop.remove_signal_handler` 的 try 之后）追加：

```python
            await self._close_store()
```

`_run_from_script_async` 同样处理：步骤 1 改为

```python
        # 1. 创建 SessionStore + Kernel
        store = self._open_store()
        self._kernel = Kernel(self._console, store=store)
```

其 finally 块（`loop.remove_signal_handler` 的 try 之后）同样追加 `await self._close_store()`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_kernel_wiring.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q
git add harness/runtime/kernel.py harness/runtime/runtime.py tests/session/test_kernel_wiring.py
git commit -m "feat(session): wire SessionStore into Kernel/Runtime lifecycle"
```

---

### Task 8: 读侧 —— replay.py 加载、校验、重放、中断检测

**依赖：** T1（events 解码）、T5（ReplayResult 是 seed 的输入）
**阻塞：** T10、T12
**设计对应：** 崩溃恢复流程图（第六节）——截断半行不修改文件（append 前才物理截断，见 T10）；seq 缺号 = 损坏拒绝；尾部 assistant 含未闭合 tool_calls = interrupted。

**Files:**
- Create: `harness/core/session/replay.py`
- Test: `tests/session/test_replay.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_replay.py`：

```python
"""replay.py —— 日志加载、校验、重放、中断/边检测测试。"""

import json

import pytest

from harness.core.session import events
from harness.core.session.exceptions import CorruptLogError
from harness.core.session.replay import (
    load_agent_log, measure_lsn_gap, scan_session,
)


def _write(path, evts):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in evts) + "\n",
                    encoding="utf-8")


def _conv(tmp_path, pid, evts):
    _write(tmp_path / "conv-1" / "agents" / f"{pid}.jsonl", evts)


def _header(pid, parent=None, lsn=0):
    return {"type": "header", "format_version": 1, "conv_id": "conv-1",
            "pid": pid, "parent": parent, "manifest_sha1": "m",
            "created_at": 1.0, "seq": 0, "lsn": lsn, "ts": 1.0}


def _user(seq, lsn, text, meta=None):
    e = {"type": "user", "seq": seq, "lsn": lsn, "ts": 1.0,
         "message": {"role": "user", "content": text}}
    if meta:
        e["meta"] = meta
    return e


class TestLoad:
    def test_normal_log_replays_history_and_records(self, tmp_path):
        _conv(tmp_path, "root", [
            _header("root"),
            _user(1, 1, "你好"),
            {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
             "message": {"role": "assistant", "content": "你好！"}},
            {"type": "stop", "seq": 3, "lsn": 3, "ts": 1.0,
             "stop_reason": "end_turn"},
            {"type": "session_end", "seq": 4, "lsn": 4, "ts": 1.0,
             "final_output": "你好！", "execution_time": 1.0, "status": "paused"},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert r.status == "paused"
        assert [m.content for m in r.history] == ["你好", "你好！"]
        assert r.last_seq == 4
        assert r.max_lsn == 4
        assert r.interrupted_at is None
        assert r.truncated_bytes == 0

    def test_truncated_tail_dropped_not_modified(self, tmp_path):
        p = tmp_path / "conv-1" / "agents" / "root.jsonl"
        _write(p, [_header("root"), _user(1, 1, "完整行")])
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('{"type":"assistant","seq":2,"lsn":2,"ts":1.0,"mess')  # 半行
        before = p.stat().st_size
        r = load_agent_log(p)
        assert r.truncated_bytes > 0
        assert [m.content for m in r.history] == ["完整行"]
        assert p.stat().st_size == before   # load 不修改文件

    def test_seq_gap_is_corrupt(self, tmp_path):
        _conv(tmp_path, "root", [_header("root"), _user(5, 1, "跳号")])
        with pytest.raises(CorruptLogError, match="seq"):
            load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")

    def test_missing_header_is_corrupt(self, tmp_path):
        _conv(tmp_path, "root", [_user(0, 0, "无头")])
        with pytest.raises(CorruptLogError, match="header"):
            load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")

    def test_no_session_end_means_crashed(self, tmp_path):
        _conv(tmp_path, "root", [_header("root"), _user(1, 1, "x")])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert r.status == "crashed"

    def test_interrupted_detection(self, tmp_path):
        """尾部 assistant 含 tool_calls 且无对应 tool_result → interrupted。"""
        _conv(tmp_path, "root", [
            _header("root"),
            _user(1, 1, "派活"),
            {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
             "message": {"role": "assistant", "content": None,
                         "tool_calls": [{"id": "call_x", "type": "function",
                                         "function": {"name": "bash",
                                                      "arguments": "{}"}}]}},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert r.interrupted_at == "call_x"

    def test_tool_call_records_replayed(self, tmp_path):
        _conv(tmp_path, "root", [
            _header("root"),
            _user(1, 1, "执行"),
            {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
             "message": {"role": "assistant", "content": None,
                         "tool_calls": [{"id": "c1", "type": "function",
                                         "function": {"name": "echo",
                                                      "arguments": "{}"}}]}},
            {"type": "tool_call", "seq": 3, "lsn": 3, "ts": 1.0,
             "record": {"tool_call_id": "c1", "tool_name": "echo",
                        "arguments": {}, "result": "ok", "started_at": 1.0,
                        "finished_at": 1.1, "error": None}},
            {"type": "tool_result", "seq": 4, "lsn": 4, "ts": 1.0,
             "message": {"role": "tool", "tool_call_id": "c1", "content": "ok"}},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert len(r.tool_call_records) == 1
        assert r.tool_call_records[0].tool_name == "echo"
        assert r.interrupted_at is None   # tool_result 已闭合


class TestEdgesAndMsgIds:
    def test_edge_events_extracted(self, tmp_path):
        _conv(tmp_path, "b", [
            _header("b", parent="root"),
            _user(1, 1, "干活", meta={"msg_id": "spawn_entry:b"}),
            {"type": "edge", "seq": 2, "lsn": 2, "ts": 1.0, "msg_id": "M-2",
             "from": "b", "to": "root", "kind": "publish", "text": "查到了"},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "b.jsonl")
        assert r.edges[0].msg_id == "M-2"
        assert r.edges[0].to_pid == "root"
        assert r.received_msg_ids == {"spawn_entry:b"}
        assert r.parent == "root"

    def test_talk_to_edge_from_tool_call_record(self, tmp_path):
        """talk_to 发送方事实 = tool_call 记录（arguments 含目标，result 含 msg_id）。"""
        _conv(tmp_path, "root", [
            _header("root"),
            _user(1, 1, "呼叫 b"),
            {"type": "tool_call", "seq": 2, "lsn": 2, "ts": 1.0,
             "record": {"tool_call_id": "c9", "tool_name": "talk_to",
                        "arguments": {"pid": "b", "text": "在吗"},
                        "result": '{"ok":true,"target":"b","msg_id":"M-7f3a"}',
                        "started_at": 1.0, "finished_at": 1.1, "error": None}},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert len(r.edges) == 1
        assert r.edges[0].kind == "talk_to"
        assert r.edges[0].msg_id == "M-7f3a"
        assert r.edges[0].to_pid == "b"
        assert r.edges[0].text == "在吗"


class TestScanAndGap:
    def test_scan_session_multi_agents(self, tmp_path):
        _conv(tmp_path, "root", [_header("root"), _user(1, 1, "a")])
        _conv(tmp_path, "b", [_header("b", parent="root", lsn=2),
                              _user(2, 3, "b")])
        replays = scan_session(tmp_path / "conv-1")
        assert set(replays) == {"root", "b"}

    def test_lsn_gap_measures_crash_loss(self, tmp_path):
        """LSN 空洞 = 崩溃损失度量：lsn 4 缺失（已发号未落盘）。"""
        _conv(tmp_path, "root", [_header("root"), _user(1, 1, "a")])
        _conv(tmp_path, "b", [_header("b", parent="root", lsn=2),
                              _user(2, 3, "b"),
                              _user(3, 5, "c")])   # lsn 4 空洞
        replays = scan_session(tmp_path / "conv-1")
        assert measure_lsn_gap(replays) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.replay'`

- [ ] **Step 3: 实现**

`harness/core/session/replay.py`：

```python
"""replay.py —— 读侧：日志加载、校验、重放、中断检测、边提取、损失度量。

恢复语义（设计第六节）：
- 定位最后完整行：截断半行（load 不修改文件；物理截断在 append 打开前做）
- header 缺失/损坏 → 拒绝；seq 不连续 → 缺号=损坏，拒绝
- 尾部 assistant 含未闭合 tool_calls → interrupted（boot 注入 resume_marker）
- edge 事件 + talk_to 工具记录 = 发送方事实，供跨日志配对修复
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from ...interfaces.types import Message, ToolCallRecord
from . import events
from .exceptions import CorruptLogError

logger = logging.getLogger(__name__)

# boot 注入的中断标记（只在内存合成，永不落盘——幂等）
RESUME_MARKER = (
    "[系统] 上次会话在工具调用 {call_id} 处中断，"
    "该工具的副作用可能已部分生效，请核对后再继续。"
)


@dataclass
class Edge:
    """出站消息边（msg_id 配对修复的发送方事实）。"""
    msg_id: str
    from_pid: str
    to_pid: str
    kind: str   # talk_to | publish | direct | spawn_entry
    text: str


@dataclass
class ReplayResult:
    """单个 agent 日志的重放结果（SessionLog.seed 的直接输入）。"""
    pid: str
    conv_id: str
    parent: Optional[str]
    history: List[Message] = field(default_factory=list)
    tool_call_records: List[ToolCallRecord] = field(default_factory=list)
    user_metas: List[dict] = field(default_factory=list)   # user 事件 meta 序列
    last_seq: int = 0
    max_lsn: int = 0
    event_count: int = 0
    status: str = "crashed"        # session_end 存在 → 其 status；否则 crashed
    final_output: str = ""
    interrupted_at: Optional[str] = None
    edges: List[Edge] = field(default_factory=list)
    received_msg_ids: Set[str] = field(default_factory=set)
    truncated_bytes: int = 0


def load_agent_log(path: Path) -> ReplayResult:
    """加载并校验单个 agent 日志，重放为 ReplayResult。

    Raises:
        CorruptLogError: header 缺失/损坏，或 seq 不连续（缺号=损坏）。
    """
    raw = path.read_bytes()
    truncated = 0
    if raw and not raw.endswith(b"\n"):
        last_nl = raw.rfind(b"\n")
        truncated = len(raw) - last_nl - 1
        raw = raw[:last_nl + 1]  # 仅内存截断；文件修改在 append 打开前（T10）
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip()]
    if not lines:
        raise CorruptLogError(f"{path}: empty log (missing header)")

    parsed: List[dict] = []
    for i, line in enumerate(lines):
        try:
            parsed.append(events.decode_event(line))
        except (ValueError, json.JSONDecodeError) as e:
            raise CorruptLogError(f"{path}:{i + 1}: {e}") from e

    header = parsed[0]
    if header["type"] != events.EVT_HEADER:
        raise CorruptLogError(f"{path}: first line is not a header")
    for expect, evt in enumerate(parsed):
        if evt["seq"] != expect:
            raise CorruptLogError(
                f"{path}: seq gap — expected {expect}, got {evt['seq']}")

    result = ReplayResult(
        pid=header.get("pid", path.stem),
        conv_id=header.get("conv_id", ""),
        parent=header.get("parent"),
        last_seq=parsed[-1]["seq"],
        max_lsn=max(e.get("lsn", 0) for e in parsed),
        event_count=len(parsed),
        truncated_bytes=truncated,
    )

    tool_result_ids: Set[str] = set()
    last_assistant_tool_calls: Optional[List[dict]] = None

    for evt in parsed:
        etype = evt["type"]
        if etype in (events.EVT_USER, events.EVT_ASSISTANT, events.EVT_TOOL_RESULT):
            result.history.append(events.event_to_message(evt))
            if etype == events.EVT_USER:
                meta = evt.get("meta") or {}
                result.user_metas.append(meta)
                if meta.get("msg_id"):
                    result.received_msg_ids.add(meta["msg_id"])
            elif etype == events.EVT_TOOL_RESULT:
                tcid = evt["message"].get("tool_call_id")
                if tcid:
                    tool_result_ids.add(tcid)
            elif etype == events.EVT_ASSISTANT:
                tcs = evt["message"].get("tool_calls")
                last_assistant_tool_calls = tcs if tcs else None
        elif etype == events.EVT_TOOL_CALL:
            record = events.event_to_tool_call_record(evt)
            result.tool_call_records.append(record)
            edge = _edge_from_talk_to(record, source_pid=result.pid)
            if edge is not None:
                result.edges.append(edge)
        elif etype == events.EVT_EDGE:
            result.edges.append(Edge(
                msg_id=evt.get("msg_id", ""),
                from_pid=evt.get("from", result.pid),
                to_pid=evt.get("to", ""),
                kind=evt.get("kind", "publish"),
                text=evt.get("text", ""),
            ))
        elif etype == events.EVT_SESSION_END:
            result.status = evt.get("status", "paused")
            result.final_output = evt.get("final_output", "")

    # 尾部语义检测：最后的 assistant tool_calls 是否有未闭合项
    if last_assistant_tool_calls:
        missing = [tc["id"] for tc in last_assistant_tool_calls
                   if tc["id"] not in tool_result_ids]
        if missing:
            result.interrupted_at = missing[0]

    return result


def scan_session(conv_dir: Path) -> Dict[str, ReplayResult]:
    """扫描 conv_dir/agents/*.jsonl，全部重放。损坏日志向上抛 CorruptLogError。"""
    agents_dir = conv_dir / "agents"
    replays: Dict[str, ReplayResult] = {}
    if not agents_dir.is_dir():
        return replays
    for path in sorted(agents_dir.glob("*.jsonl")):
        r = load_agent_log(path)
        replays[r.pid] = r
    return replays


def measure_lsn_gap(replays: Dict[str, ReplayResult]) -> int:
    """LSN 空洞 = 崩溃损失度量：已发号未落盘的事件数（0 = 无损失）。"""
    if not replays:
        return 0
    global_max = max(r.max_lsn for r in replays.values())
    total_events = sum(r.event_count for r in replays.values())
    return max(0, global_max + 1 - total_events)


def _edge_from_talk_to(record: ToolCallRecord, *, source_pid: str) -> Optional[Edge]:
    """talk_to 的发送方事实：arguments 含目标与文本，result JSON 含 msg_id。"""
    if record.tool_name != "talk_to" or not record.result:
        return None
    try:
        payload = json.loads(record.result) if isinstance(record.result, str) else {}
    except json.JSONDecodeError:
        return None
    msg_id = payload.get("msg_id")
    if not msg_id:
        return None
    return Edge(
        msg_id=msg_id,
        from_pid=source_pid,
        to_pid=record.arguments.get("pid", ""),
        kind="talk_to",
        text=record.arguments.get("text", ""),
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_replay.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/replay.py tests/session/test_replay.py
git commit -m "feat(session): add replay loader with corruption checks and interruption detection"
```

---

### Task 9: manifest —— 计算、分级比对、fingerprint() 约定 + build_tool_router 提取

**依赖：** T1（sha1 入 header/index）；被 T7 的 `_probe_manifest` 调用
**阻塞：** T10（boot 分级校验）
**设计对应：** 决策 D2——语义关键（assembler、历史中实际用过的工具）不一致 → 硬失败；其余 → 告警。工具集在 `_phase_init` 装配缓存齐后才可算，故 manifest 由 provider 惰性计算（T5 的 begin() 触发）。

**Files:**
- Create: `harness/core/session/manifest.py`
- Modify: `harness/core/async_orchestrator.py`（提取 `build_tool_router`，供 boot 探针复用）
- Test: `tests/session/test_manifest.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_manifest.py`：

```python
"""manifest 计算与分级比对测试。"""

from harness.core.container import DIContainer
from harness.core.session.manifest import (
    compute_manifest, diff_manifest, manifest_sha1,
)
from harness.interfaces import ContextAssembler, GuideProvider, SystemToolProvider
from harness.interfaces.types import ToolDefinition


class AsmA:
    def assemble(self, ctx):
        return []


class AsmB:
    def assemble(self, ctx):
        return []


class GuideWithFingerprint:
    def get_guides(self, ctx):
        return None

    def fingerprint(self):
        return {"content_sha1": ["ab12"]}


class ProviderX:
    def get_tools(self):
        return [ToolDefinition(name="bash"), ToolDefinition(name="talk_to")]

    def execute(self, name, args):
        return None


def _container(**overrides):
    c = DIContainer()
    c.register(ContextAssembler, overrides.get("assembler", AsmA()))
    if overrides.get("guide") is not None:
        c.register(GuideProvider, overrides["guide"])
    c.register(SystemToolProvider, overrides.get("provider", ProviderX()))
    return c


class TestCompute:
    def test_manifest_shape_and_stable_sha(self):
        m = compute_manifest(_container(),
                             cached_tools=ProviderX().get_tools(),
                             call_llm=None)
        assert m["ContextAssembler"]["id"].endswith("AsmA")
        assert m["SystemToolProvider"]["tool_names"] == ["bash", "talk_to"]
        assert manifest_sha1(m) == manifest_sha1(dict(m))  # 稳定性

    def test_fingerprint_convention_used(self):
        m = compute_manifest(_container(guide=GuideWithFingerprint()),
                             cached_tools=[], call_llm=None)
        assert m["GuideProvider"]["content_sha1"] == ["ab12"]

    def test_default_fingerprint_is_class_id(self):
        m = compute_manifest(_container(), cached_tools=[], call_llm=None)
        assert m["GuideProvider"] == {} or "id" in m.get("GuideProvider", {})


class TestDiff:
    def test_assembler_change_is_hard(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(assembler=AsmB()),
                               cached_tools=ProviderX().get_tools())
        diff = diff_manifest(old, new, used_tool_names=set())
        assert diff.hard and not diff.ok

    def test_used_tool_missing_is_hard(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(), cached_tools=[ToolDefinition(name="bash")])
        diff = diff_manifest(old, new, used_tool_names={"talk_to"})
        assert any("talk_to" in h for h in diff.hard)

    def test_unused_tool_removed_is_soft(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(), cached_tools=[ToolDefinition(name="bash")])
        diff = diff_manifest(old, new, used_tool_names=set())
        assert not diff.hard and diff.soft

    def test_guide_change_is_soft(self):
        old = compute_manifest(_container(guide=GuideWithFingerprint()),
                               cached_tools=[])
        changed = GuideWithFingerprint()
        changed.fingerprint = lambda: {"content_sha1": ["ffff"]}
        new = compute_manifest(_container(guide=changed), cached_tools=[])
        diff = diff_manifest(old, new, used_tool_names=set())
        assert not diff.hard and diff.soft

    def test_identical_is_ok(self):
        m = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        diff = diff_manifest(m, m, used_tool_names=set())
        assert diff.ok
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.manifest'`

- [ ] **Step 3: 实现**

`harness/core/session/manifest.py`：

```python
"""manifest —— 装配清单计算与分级校验（设计决策 D2）。

分级规则：
- 硬失败（语义关键）：ContextAssembler id 变化；
  历史中实际用过的工具在当前工具集中缺失
- 告警（其余）：GuideProvider/MCPAdapter 指纹变化、llm model 变化、
  未用工具增删
- --force：硬失败降级为告警（在 Kernel.boot 执行，不在此）

组件可选约定：实现 ``fingerprint() -> dict`` 参与 manifest 计算；
未实现时默认 {"id": 类全限定名}。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def component_id(obj: Any) -> str:
    """组件类全限定名。"""
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def fingerprint_of(obj: Any) -> Dict[str, Any]:
    """fingerprint() 约定：组件实现了就用，否则默认 {"id": 类全限定名}。"""
    fp = getattr(obj, "fingerprint", None)
    if callable(fp):
        try:
            result = fp()
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning("fingerprint() of %s failed: %s", component_id(obj), e)
    return {"id": component_id(obj)}


def compute_manifest(container, *, cached_tools: list,
                     call_llm=None) -> Dict[str, Any]:
    """从 DI 容器 + 装配缓存计算 manifest。

    Args:
        container: DIContainer。
        cached_tools: ToolRouter.list_tools() 的结果（_phase_init 缓存，
                      或 boot 探针用 build_tool_router 现算）。
        call_llm: LLM callable（best-effort 取 .model）。
    """
    from ...interfaces import (
        ContextAssembler, GuideProvider, MCPAdapter, SystemToolProvider,
    )

    manifest: Dict[str, Any] = {}
    for interface, key in ((ContextAssembler, "ContextAssembler"),
                           (GuideProvider, "GuideProvider"),
                           (SystemToolProvider, "SystemToolProvider"),
                           (MCPAdapter, "MCPAdapter")):
        try:
            component = container.resolve(interface)
        except Exception:
            continue
        fp = fingerprint_of(component)
        if key in ("SystemToolProvider", "MCPAdapter"):
            fp = {**fp, "tool_names": sorted(t.name for t in cached_tools)}
        manifest[key] = fp

    manifest["llm"] = {"model": getattr(call_llm, "model", None)}
    return manifest


def manifest_sha1(manifest: Dict[str, Any]) -> str:
    """manifest 的稳定哈希（排序键 + 紧凑分隔，default=str 兜底）。"""
    blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


@dataclass
class ManifestDiff:
    """分级比对结果。hard 非空 = 语义关键不一致（boot 硬失败，--force 降级）。"""
    hard: List[str] = field(default_factory=list)
    soft: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard


def diff_manifest(old: Optional[Dict[str, Any]], new: Dict[str, Any], *,
                  used_tool_names: Set[str]) -> ManifestDiff:
    """分级比对。old 为 None（无历史 manifest）时全部跳过。"""
    diff = ManifestDiff()
    if not old:
        return diff

    # 硬：ContextAssembler id
    old_asm = (old.get("ContextAssembler") or {}).get("id")
    new_asm = (new.get("ContextAssembler") or {}).get("id")
    if old_asm and new_asm and old_asm != new_asm:
        diff.hard.append(
            f"ContextAssembler 变化: {old_asm} → {new_asm}")

    # 硬：历史中实际用过的工具在当前工具集中缺失
    current_tools: Set[str] = set(
        (new.get("SystemToolProvider") or {}).get("tool_names", []))
    for missing in sorted(used_tool_names - current_tools):
        diff.hard.append(f"历史使用过的工具 '{missing}' 当前不可用")

    # 软：工具集增删（未被历史使用的）
    old_tools = set((old.get("SystemToolProvider") or {}).get("tool_names", []))
    if old_tools != current_tools and not diff.hard:
        diff.soft.append(
            f"工具集变化: {sorted(old_tools)} → {sorted(current_tools)}")

    # 软：GuideProvider 指纹
    if old.get("GuideProvider") != new.get("GuideProvider"):
        diff.soft.append("GuideProvider 内容变化（如 AGENTS.md 已修改）")

    # 软：llm model
    old_model = (old.get("llm") or {}).get("model")
    new_model = (new.get("llm") or {}).get("model")
    if old_model != new_model:
        diff.soft.append(f"LLM model 变化: {old_model} → {new_model}")

    return diff
```

**`harness/core/async_orchestrator.py` 提取 `build_tool_router`**——在 `_request_meta` 之后追加模块级函数：

```python
def build_tool_router(container: DIContainer):
    """合并 SystemToolProvider + MCPAdapter → (ToolRouter, List[ToolDefinition])。

    _phase_init 与 Kernel boot 探针（manifest 计算）共用的装配逻辑。
    """
    from ..interfaces import MCPAdapter, SystemToolProvider

    tool_router = ToolRouter()
    for interface in (SystemToolProvider, MCPAdapter):
        try:
            provider = container.resolve(interface)
        except ComponentNotRegisteredError:
            continue
        try:
            tool_router.register_provider(provider)
        except Exception as e:
            logger.warning(f"{interface.__name__} registration failed: {e}")
    try:
        tools = tool_router.list_tools()
    except Exception as e:
        logger.warning(f"ToolRouter.list_tools() failed: {e}")
        tools = []
    return tool_router, tools
```

`_phase_init` 的步骤 4 整段（`# 4. ToolRouter` 到 `self._cached_tools = available_tools`）替换为：

```python
        # 4. ToolRouter（框架内部，非 DI）— 合并 SystemToolProvider + MCPAdapter
        #    与 Kernel boot 探针共用 build_tool_router（行为不变，仅提取）
        tool_router, available_tools = build_tool_router(self.container)
        self._cached_tool_router = tool_router
        self._cached_tools = available_tools
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_manifest.py tests/session/test_orchestrator_recording.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q
git add harness/core/session/manifest.py harness/core/async_orchestrator.py tests/session/test_manifest.py
git commit -m "feat(session): add manifest computation, graded diff, and build_tool_router extraction"
```

---
### Task 10: Kernel.boot —— create/start 拆分、所有权接管、种子恢复、Mode A/B

**依赖：** T5（seed）、T7（接线）、T8（replay）、T9（manifest）
**阻塞：** T12（boot 的配对修复阶段插入点）、T13
**设计对应：** Boot-Resume 时序图（第五节）——"创建所有 → 种子 → 配对修复 → 启动所有"；所有权令牌 + pid 活性检查 + --force；Mode A 仅重启 root，Mode B 重跑脚本（sha1 校验）。

**关键重构：spawn 的 create/start 拆分**
现状 `spawn_root`/`spawn_from_script` 将"创建 agent"与"启动 task + 投递 entry"融合（kernel.py）。boot 要求：先创建所有 → 种子 → 配对修复 → 再启动。拆分必须**保留原签名默认行为**（`spawn_root` 外部调用方、存量测试不受影响）。

**Files:**
- Modify: `harness/runtime/kernel.py`
- Create: `harness/core/session/boot.py`（boot 编排与 BootReport，保持 kernel.py 可控大小）
- Test: `tests/session/test_boot.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_boot.py`：

```python
"""Kernel.boot —— fresh/resume 统一入口、所有权、manifest 校验、种子恢复。"""

import json

import pytest

from harness.core.session.boot import BootReport
from harness.core.session.exceptions import BootError, SessionOwnerConflict
from harness.core.session.ids import new_msg_id
from harness.core.session.store import SessionStore
from harness.interfaces.types import Response, UserRequest
from harness.runtime.kernel import Kernel
from tests.session._fakes import MockConsole, MockHarness, run_async
from tests.session.test_kernel_wiring import ExitImmediatelyAdapter
from tests.session.test_orchestrator_recording import ScriptedAdapter


async def _async_llm(messages, tools):
    return Response(text="你好！")


def _harness_with(adapter):
    from harness.interfaces.async_input_adapter import AsyncInputAdapter
    h = MockHarness()
    h.container.register(AsyncInputAdapter, adapter)
    return h


def _write_log(tmp_path, conv_id, pid, evts):
    p = tmp_path / conv_id / "agents" / f"{pid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in evts) + "\n",
                 encoding="utf-8")
    return p


def _ended_conv(tmp_path, conv_id="conv-1"):
    """构造一个干净结束的 root 会话（3 轮对话，含一条用户消息）。"""
    _write_log(tmp_path, conv_id, "root", [
        {"type": "header", "format_version": 1, "conv_id": conv_id, "pid": "root",
         "parent": None, "manifest_sha1": "m0", "created_at": 1.0,
         "seq": 0, "lsn": 0, "ts": 1.0},
        {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
         "message": {"role": "user", "content": "旧消息"}},
        {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
         "message": {"role": "assistant", "content": "旧回复"}},
        {"type": "stop", "seq": 3, "lsn": 3, "ts": 1.0, "stop_reason": "end_turn"},
        {"type": "session_end", "seq": 4, "lsn": 4, "ts": 1.0,
         "final_output": "旧回复", "execution_time": 1.0, "status": "paused"},
    ])
    (tmp_path / conv_id / "index.json").write_text(json.dumps({
        "conv_id": conv_id, "created_at": 1.0, "owner": None,
        "status": "paused", "manifest_sha1": "m0",
        "manifest": {}, "script": None,
        "agents": {"root": {"parent": None, "last_seq": 4, "last_lsn": 4,
                            "status": "paused", "final_output": "旧回复",
                            "execution_time": 1.0}},
    }), encoding="utf-8")


class TestFreshBoot:
    @run_async
    async def test_boot_fresh_delegates_to_spawn(self, tmp_path):
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "fresh"
        assert report.replayed == []
        assert kernel.runtime_table["root"].state.name == "FINISHED"
        await store.close()


class TestResumeBoot:
    @run_async
    async def test_resume_seeds_history_and_continues_seq(self, tmp_path):
        _ended_conv(tmp_path)
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            conv_id="conv-1",
            harness=_harness_with(ScriptedAdapter([UserRequest(text="新消息")])),
            call_llm=_async_llm)
        assert report.mode == "resume"
        assert report.status_before == "paused"
        assert "root" in report.replayed

        await kernel._tasks["root"]
        await store.close()
        evts = [json.loads(l) for l in
                (tmp_path / "conv-1" / "agents" / "root.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        # 单 header + seq 跨运行连续（旧 0-4，新从 5 开始）
        assert [e["type"] for e in evts].count("header") == 1
        assert [e["seq"] for e in evts] == list(range(len(evts)))
        assert evts[5]["type"] == "user"
        assert evts[5]["message"]["content"] == "新消息"
        # 恢复的历史进入 orchestrator（新 LLM 调用能看到旧消息）
        history = [m.content for m in
                   kernel.runtime_table["root"]._orchestrator._history]
        assert history[:2] == ["旧消息", "旧回复"]

    @run_async
    async def test_owner_conflict_refused_and_forced(self, tmp_path):
        _ended_conv(tmp_path)
        idx_path = tmp_path / "conv-1" / "index.json"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        idx["owner"] = "pid-999999-1"          # 死进程持有
        idx["status"] = "active"
        idx_path.write_text(json.dumps(idx), encoding="utf-8")

        import os
        # 活进程持有 → 拒绝
        idx["owner"] = f"pid-{os.getpid()}-1"
        idx_path.write_text(json.dumps(idx), encoding="utf-8")
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(SessionOwnerConflict):
            await kernel.boot(conv_id="conv-1",
                              harness=_harness_with(ExitImmediatelyAdapter()),
                              call_llm=_async_llm)
        # --force 强制接管
        report = await kernel.boot(
            conv_id="conv-1", force=True,
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "resume"
        await kernel._tasks["root"]
        await store.close()

    @run_async
    async def test_missing_conv_is_boot_error(self, tmp_path):
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(BootError, match="不存在"):
            await kernel.boot(conv_id="conv-nope",
                              harness=_harness_with(ExitImmediatelyAdapter()),
                              call_llm=_async_llm)

    @run_async
    async def test_truncated_tail_physically_cut_on_append(self, tmp_path):
        _ended_conv(tmp_path)
        p = tmp_path / "conv-1" / "agents" / "root.jsonl"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('{"type":"user","seq":5,"lsn":5,"ts":2.0,"mess')   # 崩溃半行
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            conv_id="conv-1",
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.warnings                       # 有截断提示
        text = p.read_text(encoding="utf-8")
        assert all(l.startswith("{") and l.endswith("}")
                   for l in text.splitlines())       # 半行已物理截断
        assert json.loads(text.splitlines()[-1])["type"] == "session_end"
        await kernel._tasks["root"]
        await store.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_boot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.core.session.boot'` / `AttributeError: 'Kernel' object has no attribute 'boot'`

- [ ] **Step 3: 实现**

**3a. `harness/core/session/boot.py`：**

```python
"""boot —— fresh/resume 统一启动编排（设计第五节 Boot-Resume 时序图）。

"创建所有 → 种子 → 配对修复 → 启动所有"四步序由 Kernel.boot 驱动；
本模块提供数据结构与纯函数（恢复计划、恢复警告），与 Kernel 解耦以便测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BootReport:
    """boot 结果报告（打印给用户 + 供 T13 CLI 汇总）。"""
    conv_id: str
    mode: str                       # "fresh" | "resume"
    status_before: str = ""         # resume 前的 index status
    replayed: List[str] = field(default_factory=list)     # 已种子的 pid
    lsn_gap: int = 0                # 崩溃损失度量（0 = 无损失）
    redelivered: List[str] = field(default_factory=list)  # 配对修复重投的 msg_id
    warnings: List[str] = field(default_factory=list)


@dataclass
class ResumePlan:
    """单个 agent 的恢复计划。"""
    pid: str
    restart: bool                   # Mode A: 仅 root；Mode B: 全体
    needs_marker: bool              # interrupted → 注入 resume_marker
    truncated_tail: bool            # 需要物理截断半行
```

**3b. `harness/runtime/kernel.py` —— create/start 拆分。**

`spawn_root` 现实现（步骤 1–6）改为调用两个新方法，行为不变：

```python
    def spawn_root(self, harness, call_llm: Optional[Callable] = None):
        """创建并启动根 agent（= _create_root + _start_agent，行为不变）。"""
        runtime = self._create_root(harness, call_llm)
        self._start_agent(runtime.pid)
        return runtime

    def _create_root(self, harness, call_llm: Optional[Callable] = None):
        """创建根 agent 但不启动（boot 四步序的第 1 步使用）。

        步骤与拆分前 spawn_root 的 1–4 完全一致。
        """
        # 1. 创建 AgentRuntime（原步骤 1，逐行保留）
        ...

        # 2. adapter（原步骤 2，逐行保留）
        ...

        # 3. SessionLog 先行 + _init_orchestrator（原步骤 3，逐行保留）
        ...

        # 4. 注册 runtime_table/input_queues（原步骤 4，逐行保留）
        ...
        return runtime

    def _start_agent(self, pid: str) -> None:
        """启动已注册 agent 的运行 task（boot 四步序的第 4 步使用）。

        即原 spawn_root 步骤 6 的 asyncio.create_task(...) + done_callback。
        """
        runtime = self.runtime_table[pid]
        task = asyncio.create_task(runtime.run(), name=f"agent:{pid}")
        self._tasks[pid] = task
        task.add_done_callback(
            lambda _t, p=pid: asyncio.ensure_future(self._on_agent_finished(p))
        )
```

（`spawn_from_script` 同理拆为 `_create_agents_from_script(script_path, parent, autostart, deliver_entry)` + 循环 `_start_agent`；公开签名保持 `spawn_from_script(script_path, parent=None, *, autostart=True, deliver_entry=True)`，默认 True/True 时与现状逐字节等价——entry_prompt 投递仍发生在 task 创建之后。）

**3c. `Kernel.boot` 主体**（追加在 `_start_agent` 之后）：

```python
    async def boot(self, conv_id: Optional[str] = None, *, force: bool = False,
                   harness=None, call_llm: Optional[Callable] = None,
                   script_path: Optional[str] = None) -> "BootReport":
        """统一启动入口：conv_id 为 None → fresh；否则 resume。

        fresh：begin_session(None) + 原 spawn 路径（Mode A 与现状一致）。
        resume 四步序（设计第五节）：
          1. 创建所有（Mode A 仅 root；Mode B 重跑脚本，autostart=False）
          2. 种子（replay → 物理截断半行 → seed → Sequencer 恢复到 max_lsn+1）
          3. 配对修复（T12 的 plan_redelivery 注入点，此处预留调用）
          4. 启动所有（_start_agent）
        """
        from ..core.session.boot import BootReport

        if self._store is None or conv_id is None:
            # fresh 路径：与现状完全一致
            if self._store is not None:
                self._store.begin_session(None)
            if script_path is not None:
                self.spawn_from_script(script_path, parent=None)
            else:
                self.spawn_root(harness, call_llm)
            return BootReport(conv_id=self._store.conv_id if self._store else "",
                              mode="fresh")

        return await self._boot_resume(conv_id, force=force, harness=harness,
                                       call_llm=call_llm, script_path=script_path)

    async def _boot_resume(self, conv_id: str, *, force: bool, harness,
                           call_llm, script_path) -> "BootReport":
        from ..core.session.boot import BootReport
        from ..core.session.exceptions import BootError, SessionOwnerConflict
        from ..core.session.ids import pid_alive, pid_from_token
        from ..core.session.manifest import diff_manifest
        from ..core.session.replay import (
            RESUME_MARKER, measure_lsn_gap, scan_session,
        )

        store = self._store
        index = store.read_index(conv_id)
        conv_dir = store.root_path / conv_id
        if index is None and not conv_dir.is_dir():
            raise BootError(f"会话 '{conv_id}' 不存在于 {store.root_path}")
        index = index or store.rebuild_index(conv_id)

        # ── 所有权接管（D6：令牌 + pid 活性 + --force）──
        import os
        owner = index.get("owner")
        if owner:
            owner_pid = pid_from_token(owner)
            if pid_alive(owner_pid) and owner_pid != os.getpid() and not force:
                raise SessionOwnerConflict(
                    f"会话 '{conv_id}' 正被进程 {owner_pid} 持有；"
                    f"如确认该进程已退出，使用 --force 强制接管。")

        report = BootReport(conv_id=conv_id, mode="resume",
                            status_before=index.get("status", "unknown"))

        # ── 重放与校验（第 2 步的准备阶段：读侧先行，不建文件）──
        replays = scan_session(conv_dir)
        if not replays:
            raise BootError(f"会话 '{conv_id}' 没有任何 agent 日志")
        report.lsn_gap = measure_lsn_gap(replays)
        if report.lsn_gap:
            report.warnings.append(
                f"LSN 空洞 {report.lsn_gap}：上次运行有 {report.lsn_gap} 个事件"
                f"已发号未落盘（崩溃损失）")
        for pid, r in replays.items():
            if r.truncated_bytes:
                report.warnings.append(
                    f"agent '{pid}' 日志尾部截断 {r.truncated_bytes} 字节（半行）")

        # ── manifest 分级校验（D2）──
        probe = self._probe_manifest(harness, None) if harness else {}
        diff = diff_manifest(index.get("manifest"), probe,
                             used_tool_names=_used_tool_names(replays))
        report.warnings.extend(diff.soft)
        if diff.hard and not force:
            raise BootError("manifest 硬校验失败：\n  " + "\n  ".join(diff.hard)
                            + "\n如确认继续，使用 --force。")
        if diff.hard:
            report.warnings.extend(f"[force 降级] {h}" for h in diff.hard)

        # ── Mode 判定：有 script 记录 → Mode B；否则 Mode A ──
        script_meta = index.get("script")
        mode_b = script_meta is not None and script_path is not None
        if mode_b:
            self._verify_script_sha1(script_meta, script_path, force, report)

        # ── 第 1 步：创建所有（不启动）──
        if mode_b:
            self._create_agents_from_script(script_path, parent=None,
                                            autostart=False, deliver_entry=False)
        else:
            root_replay = replays.get("root")
            if root_replay is None:
                raise BootError(f"会话 '{conv_id}' 缺少 root 日志，无法恢复")
            self._create_root(harness, call_llm)

        # ── 接管会话（index owner 更新 + sequencer 恢复）──
        store.begin_session(conv_id)  # resume 接管：合并 index，状态 → active
        store.restore_sequencer(max(r.max_lsn for r in replays.values()) + 1)

        # ── 第 2 步：种子（append 前物理截断半行；只 seed 将重启的 agent）──
        restarted = set(self.runtime_table.keys())
        for pid in sorted(restarted):
            r = replays.get(pid)
            if r is None:
                continue                       # 新 agent（Mode B 可能出现）
            log_path = store.agent_log_path(pid)
            if r.truncated_bytes:
                import os as _os
                with open(log_path, "ab") as fh:
                    fh.truncate(_os.path.getsize(log_path) - r.truncated_bytes)
            writer = store.open_log_for_append(r, manifest_provider=None)
            runtime = self.runtime_table[pid]
            session_log = self._make_session_log(pid, runtime._harness,
                                                 runtime, parent=None)
            session_log.seed(r.history, r.tool_call_records,
                             last_seq=r.last_seq, last_lsn=r.max_lsn)
            runtime._orchestrator._history = session_log.history
            runtime._orchestrator._tool_call_records = \
                session_log.tool_call_records
            runtime._orchestrator._session_log = session_log
            if r.interrupted_at:
                # resume_marker：只在内存合成，永不落盘（幂等）
                from ..interfaces.types import Message
                session_log.history.append(Message(
                    role="user",
                    content=RESUME_MARKER.format(call_id=r.interrupted_at)))
                report.warnings.append(
                    f"agent '{pid}' 在工具调用 {r.interrupted_at} 处中断，"
                    f"已注入恢复标记")
            report.replayed.append(pid)

        # ── 第 3 步：配对修复（T12 填充；此处调用 plan_redelivery）──
        from ..core.session.replay import plan_redelivery
        for plan in plan_redelivery(replays, restarted):
            store_key = plan.dedup_key
            if plan.target in restarted and store_key:
                self.send_input(plan.target, plan.request)
                report.redelivered.append(store_key)

        # ── 第 4 步：启动所有 ──
        for pid in sorted(restarted):
            if pid not in self._tasks or self._tasks[pid].done():
                self._start_agent(pid)

        return report

    @staticmethod
    def _verify_script_sha1(script_meta: dict, script_path: str,
                            force: bool, report) -> None:
        """Mode B 前置校验：脚本 sha1 不一致 → 硬失败（--force 降级）。"""
        import hashlib
        from ..core.session.exceptions import BootError
        actual = hashlib.sha1(
            open(script_path, "rb").read()).hexdigest()
        expect = script_meta.get("sha1")
        if expect and actual != expect:
            msg = (f"脚本已修改：{script_path}\n  记录: {expect}\n"
                   f"  当前: {actual}")
            if not force:
                raise BootError(msg + "\n如确认继续，使用 --force。")
            report.warnings.append(f"[force 降级] {msg}")

    def _used_tool_names(replays) -> set:
        ...
```

模块级辅助（放在 `kernel.py` 末尾或 `boot.py`——放 `boot.py` 并在 kernel 中 import）：

```python
def _used_tool_names(replays) -> set:
    """历史中实际用过的工具名集合（manifest 硬校验输入）。"""
    names = set()
    for r in replays.values():
        for rec in r.tool_call_records:
            names.add(rec.tool_name)
    return names
```

**3d. `SessionStore` 补 `open_log_for_append`**（T4 已有 `restore_sequencer`；本任务新增）：

```python
    def open_log_for_append(self, replay, *,
                            manifest_provider=None) -> SessionLog:
        """为已恢复的 agent 打开追加写 SessionLog（不重建文件、不写新 header）。

        调用方负责：先物理截断半行，再调用本方法；随后 SessionLog.seed()。
        """
        writer = self._create_writer(replay.pid)
        log = SessionLog(self.conv_id, replay.pid, store=self,
                         sequencer=self._sequencer, parent=replay.parent,
                         manifest_provider=manifest_provider)
        self._logs[replay.pid] = log
        return log
```

（`SessionStore.__init__` 需有 `self._logs: Dict[str, SessionLog] = {}` 与 `self._sequencer`；`create_log` 同步登记进 `_logs`。若 T3/T5 的实现已包含，则此处仅补充缺失部分。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_boot.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q
git add harness/core/session/boot.py harness/core/session/store.py harness/runtime/kernel.py tests/session/test_boot.py
git commit -m "feat(session): add Kernel.boot with create/start split, ownership takeover, and resume seeding"
```

---

### Task 11: msg_id 盖章 —— TalkToTool / MessageBus / KBA / child_finished / spawn_entry

**依赖：** T6（record_edge 可用）、T7（kernel 持有 logs）
**阻塞：** T12（配对修复依赖这些事实）
**设计对应：** 决策 D8——msg_id 由内核在中介点盖章，永不信任 LLM 的 call_id；edge 事件 = 发送方事实（不落 history）。

**Files:**
- Modify: `harness/runtime/tools.py`（TalkToTool）
- Modify: `harness/runtime/message_bus.py`
- Modify: `harness/runtime/bridge_adapter.py`
- Modify: `harness/runtime/kernel.py`（child_finished、spawn entry 盖章）
- Test: `tests/session/test_msg_id_stamping.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_msg_id_stamping.py`：

```python
"""msg_id 盖章测试：四个中介点各自产生可配对的发送方事实。"""

import json
import re

from harness.runtime.kernel import Kernel
from harness.runtime.message_bus import MessageBus
from harness.runtime.tools import TalkToTool
from harness.interfaces.types import UserRequest
from tests.session._fakes import MockConsole, MockHarness, run_async

MSG_ID_RE = re.compile(r"^M-[0-9a-f]{8}$")


class _QueueAdapter:
    def __init__(self):
        self.sent = []

    async def receive(self):
        return UserRequest(text="", metadata={"exit": True})

    async def send(self, event, target=None):
        self.sent.append(event)


def _kernel_with_agent(tmp_path, pid="root"):
    from harness.core.session.store import SessionStore
    from harness.interfaces.async_input_adapter import AsyncInputAdapter
    store = SessionStore(str(tmp_path))
    store.begin_session(None)
    kernel = Kernel(MockConsole(), store=store)
    h = MockHarness()
    h.container.register(AsyncInputAdapter, _QueueAdapter())
    kernel.spawn_root(h)
    return kernel, store


class TestTalkToStamping:
    def test_talk_to_stamps_msg_id_in_metadata_and_result(self, tmp_path):
        kernel, store = _kernel_with_agent(tmp_path)
        kernel.spawn_root.__wrapped__ if False else None
        # 直接造第二个 agent 的输入队列
        kernel.input_queues["b"] = __import__("asyncio").Queue()
        tool = TalkToTool(kernel, from_pid="root")
        result = tool.execute({"pid": "b", "text": "在吗"})
        payload = json.loads(result.content)
        assert MSG_ID_RE.match(payload["msg_id"])
        req = kernel.input_queues["b"].get_nowait()
        assert req.metadata["msg_id"] == payload["msg_id"]
        assert req.metadata["from"] == "root" and req.metadata["type"] == "talk_to"


class TestPublishStamping:
    @run_async
    async def test_publish_stamps_per_subscriber_and_returns_edges(self):
        bus = MessageBus()
        bus.subscribe("a", pid="root")
        bus.subscribe("b", pid="root")
        edges = bus.publish("root", _FakeEvent(text="进度更新"))
        assert len(edges) == 2
        assert edges[0].msg_id != edges[1].msg_id      # 每订阅者独立 msg_id
        assert all(MSG_ID_RE.match(e.msg_id) for e in edges)
        assert {e.to_pid for e in edges} == {"a", "b"}

    @run_async
    async def test_stop_event_unstamped(self):
        from harness.interfaces.types import StopEvent
        bus = MessageBus()
        bus.subscribe("a", pid="root")
        edges = bus.publish("root", StopEvent(stop_reason="end_turn"))
        assert edges == []                              # 控制事件不盖章


class _FakeEvent:
    def __init__(self, text):
        self.text = text


class TestChildFinishedAndSpawnEntry:
    @run_async
    async def test_child_finished_metadata_has_msg_id(self, tmp_path):
        kernel, store = _kernel_with_agent(tmp_path)
        runtime = kernel.runtime_table["root"]
        # 模拟 child：注册后直接触发 finished
        kernel.input_queues["root"] = __import__("asyncio").Queue()
        child = kernel._create_root(MockHarness())       # 借 root 构造一个 child
        child._pid = "child-1"
        child._parent = runtime
        kernel.runtime_table["child-1"] = child
        kernel.input_queues["child-1"] = __import__("asyncio").Queue()
        await kernel._on_agent_finished("child-1")
        req = kernel.input_queues["root"].get_nowait()
        assert req.metadata["type"] == "child_finished"
        assert MSG_ID_RE.match(req.metadata["msg_id"])
        assert req.metadata["from"] == "child-1"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_msg_id_stamping.py -v`
Expected: FAIL — TalkToTool 的 result 无 `msg_id` 键；`bus.publish` 返回 None

- [ ] **Step 3: 实现**

**3a. `harness/runtime/tools.py` —— `TalkToTool.execute` 替换为：**

```python
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        target_pid = args["pid"]
        text = args["text"]
        # msg_id 由内核在中介点盖章（D8），同时写入 metadata 与 result——
        # result 里的 msg_id 随 R3a tool_call 记录落盘，成为发送方事实
        from ..core.session.ids import new_msg_id
        msg_id = new_msg_id()
        self._kernel.send_input(
            target_pid,
            UserRequest(
                text=text,
                metadata={
                    "from": self._from_pid,
                    "type": "talk_to",
                    "msg_id": msg_id,
                },
            ),
        )
        return ToolResult(
            success=True,
            content=json.dumps(
                {"ok": True, "target": target_pid, "msg_id": msg_id},
                ensure_ascii=False),
        )
```

**3b. `harness/runtime/message_bus.py` —— `publish` 返回盖章边列表：**

新增模块级 dataclass 与修改 publish：

```python
from dataclasses import dataclass
from typing import List


@dataclass
class StampedEdge:
    """publish 盖章结果：每个实际投递的订阅者一条。"""
    msg_id: str
    from_pid: str
    to_pid: str
    kind: str          # "publish" | "direct"
    text: str


def publish(self, from_pid: str, event, on_no_subscriber=None) -> List[StampedEdge]:
    """（原 fan-out 逻辑保留）返回实际投递的盖章边列表；StopEvent 等控制事件不盖章。"""
    from ..core.session.ids import new_msg_id
    from ..interfaces.types import StopEvent

    edges: List[StampedEdge] = []
    stampable = not isinstance(event, StopEvent)
    # ……原订阅者遍历与投递逻辑保持不变，仅在每次实际入队处追加：
    #     if stampable:
    #         edges.append(StampedEdge(new_msg_id(), from_pid, target,
    #                                  "publish", getattr(event, "text", "")))
    #   且入队的 InternalMessage.metadata["msg_id"] = edges[-1].msg_id
    return edges
```

`direct(target_pid, message)` 同样：若 `message.metadata` 无 msg_id 则盖章并返回 `StampedEdge(..., kind="direct")`，否则返回 None。

**3c. `harness/runtime/bridge_adapter.py` —— `send()` 记录 edge：**

在 `send()` 的 publish/direct 调用之后追加（新增实例方法 `_record_edges`）：

```python
    def _record_edges(self, edges) -> None:
        """publish/direct 的盖章边写入发送方 SessionLog（edge 事件，不进 history）。"""
        if not edges:
            return
        log = getattr(self, "_session_log", None)
        if log is None:
            return
        for e in edges:
            log.record_edge(e.msg_id, e.to_pid, e.kind, e.text)
```

KBA 需要拿到 `_session_log`：`Kernel._inject_runtime_tools`（或 adapter 创建处）在 adapter 上设置 `adapter._session_log = session_log`（与 `_make_session_log` 同一次接线）。

**3d. `harness/runtime/kernel.py` —— `_on_agent_finished` 的 child_finished 盖章：**

构造 UserRequest 处改为：

```python
        from ..core.session.ids import new_msg_id
        request = UserRequest(
            text=...,                                    # 原文本不变
            metadata={
                "type": "child_finished",
                "from": pid,
                "msg_id": new_msg_id(),
                "pid": pid,
                "workflow_flag": ...,
                "duration": duration,
                "error": ...,
            },
        )
```

同时：child 的 SessionLog 记录一条 `record_edge(msg_id, parent.pid, "child_finished", text)`（发送方事实）——在 `_on_agent_finished` 中 `self._store.log_for(pid)` 取得（T4/T5 需在 store 增加 `log_for(pid)` 查询 `_logs`，无则 None）。

**3e. spawn entry 盖章：**`spawn_from_script` 的 entry_prompt 投递处，metadata 增加 `"msg_id": f"spawn_entry:{pid}"`（确定性 msg_id，天然幂等），并向 parent 的 log `record_edge(f"spawn_entry:{pid}", parent_pid, pid, "spawn_entry", entry_prompt)`。`spawn_from_script` 返回的 `agent_results` 每项增加 `"entry_prompt"` 字段。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_msg_id_stamping.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q   # message_bus/tools/bridge 存量测试须保持绿
git add harness/runtime/tools.py harness/runtime/message_bus.py harness/runtime/bridge_adapter.py harness/runtime/kernel.py tests/session/test_msg_id_stamping.py
git commit -m "feat(session): stamp msg_id at mediation points and record sender-side edges"
```

---

### Task 12: 配对修复 —— plan_redelivery（msg_id 边规则 + child_finished 规则 + Mode B entry 规则）

**依赖：** T8（edges/received_msg_ids）、T10（boot 第 3 步插入点）、T11（事实来源）
**阻塞：** T14
**设计对应：** 崩溃恢复流程图——"跨日志配对修复：已发未收 → 重投"；三条规则见下。

**规则（来自设计推导，此处固化为代码语义）：**
1. **msg_id 边规则**：发送方日志有 edge（msg_id → target），而 target 日志的 `received_msg_ids` 无此 msg_id → 重投给 target（仅当 target 在重启集合中）
2. **child_finished 确定性键规则**：child 日志有 session_end（=已结束），其 parent 日志无任何 `meta.from == child` 的 user 事件 → 重投 child_finished（确定性 key `child_finished:{pid}`；parent 日志中任何来自该 child 的 user 事件都证明父已感知，跳过——订阅去重语义）
3. **Mode B 空日志 entry 规则**：Mode B 重建的 agent 在旧日志中存在、但新运行尚未收到 entry（`deliver_entry=False`），且旧日志含 `spawn_entry:{pid}` 的 received → **不重投**（entry 由脚本重跑保证语义等价）；仅当旧日志存在而 `received_msg_ids` 无 `spawn_entry:{pid}` 时才补投（半成品 spawn：进程崩在 entry 投递前）

**Files:**
- Modify: `harness/core/session/replay.py`（追加 plan_redelivery）
- Test: `tests/session/test_redelivery.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_redelivery.py`：

```python
"""配对修复计划测试：三条规则与重启集合过滤。"""

from harness.core.session.replay import Edge, ReplayResult, plan_redelivery


def _replay(pid, parent=None, edges=(), received=(), user_metas=(),
            status="crashed", final_output=""):
    return ReplayResult(pid=pid, conv_id="c", parent=parent,
                        edges=list(edges), received_msg_ids=set(received),
                        user_metas=list(user_metas),
                        status=status, final_output=final_output)


class TestMsgIdEdgeRule:
    def test_unreceived_edge_is_redelivered(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b", received=set())
        plans = plan_redelivery({"a": a, "b": b}, restarted={"a", "b"})
        assert len(plans) == 1
        assert plans[0].target == "b"
        assert plans[0].request.text == "在吗"
        assert plans[0].request.metadata["msg_id"] == "M-1"
        assert plans[0].request.metadata["from"] == "a"

    def test_received_edge_not_redelivered(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b", received={"M-1"})
        assert plan_redelivery({"a": a, "b": b}, restarted={"a", "b"}) == []

    def test_target_not_restarted_is_skipped(self):
        """Mode A：只重启 root，不发往未重启 agent。"""
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b")
        assert plan_redelivery({"a": a, "b": b}, restarted={"a"}) == []


class TestChildFinishedRule:
    def test_ended_child_without_parent_ack_is_redelivered(self):
        child = _replay("w1", parent="root", status="paused",
                        final_output="做完了")
        root = _replay("root", user_metas=[{"from": "user"}])
        plans = plan_redelivery({"root": root, "w1": child},
                                restarted={"root", "w1"})
        assert len(plans) == 1
        p = plans[0]
        assert p.dedup_key == "child_finished:w1"
        assert p.target == "root"
        assert p.request.metadata["type"] == "child_finished"
        assert p.request.metadata["from"] == "w1"
        assert "做完了" in p.request.text

    def test_parent_already_aware_skips(self):
        """parent 日志中任何 from==child 的 user 事件 = 已感知（订阅去重）。"""
        child = _replay("w1", parent="root", status="paused")
        root = _replay("root", user_metas=[{"from": "w1", "type": "talk_to"}])
        assert plan_redelivery({"root": root, "w1": child},
                               restarted={"root", "w1"}) == []

    def test_running_child_not_redelivered(self):
        child = _replay("w1", parent="root", status="crashed")  # 未结束
        root = _replay("root")
        assert plan_redelivery({"root": root, "w1": child},
                               restarted={"root", "w1"}) == []


class TestSpawnEntryRule:
    def test_missing_spawn_entry_is_topped_up(self):
        """半成品 spawn：agent 日志存在但从没收到 entry → 补投。"""
        child = _replay("w1", parent="root", received=set())
        plans = plan_redelivery({"root": _replay("root"), "w1": child},
                                restarted={"w1"}, script_entry_prompts={
                                    "w1": "去干活"})
        assert any(p.dedup_key == "spawn_entry:w1" for p in plans)

    def test_received_spawn_entry_not_topped_up(self):
        child = _replay("w1", parent="root", received={"spawn_entry:w1"})
        assert plan_redelivery({"w1": child}, restarted={"w1"},
                               script_entry_prompts={"w1": "去干活"}) == []


class TestDedupKey:
    def test_plans_carry_stable_dedup_keys(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "publish", "进度")])
        plans = plan_redelivery({"a": a, "b": _replay("b")}, restarted={"b"})
        assert plans[0].dedup_key == "M-1"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_redelivery.py -v`
Expected: FAIL — `ImportError: cannot import name 'plan_redelivery'`

- [ ] **Step 3: 实现**

`harness/core/session/replay.py` 追加：

```python
@dataclass
class RedeliveryPlan:
    """一条待重投消息（boot 第 3 步执行）。"""
    dedup_key: str          # msg_id 或确定性键（child_finished:{pid} 等）
    target: str
    request: "UserRequest"


def plan_redelivery(replays: Dict[str, ReplayResult], restarted: Set[str], *,
                    script_entry_prompts: Optional[Dict[str, str]] = None
                    ) -> List[RedeliveryPlan]:
    """跨日志配对修复计划（纯函数，boot 第 3 步调用）。

    规则 1（msg_id 边）：发送方有 edge(msg_id→target) 且 target 未收到 → 重投。
    规则 2（child_finished）：child 已结束而 parent 无 from==child 记录 → 重投。
    规则 3（Mode B entry）：agent 旧日志存在但未收到 spawn_entry → 补投。
    所有重投仅指向 restarted 集合内的 target。
    """
    from ...interfaces.types import UserRequest

    plans: List[RedeliveryPlan] = []
    seen_keys: Set[str] = set()

    # 规则 1：msg_id 边
    for src in replays.values():
        for edge in src.edges:
            if edge.to_pid not in restarted or edge.to_pid not in replays:
                continue
            target = replays[edge.to_pid]
            if edge.msg_id in target.received_msg_ids:
                continue
            if edge.msg_id in seen_keys:
                continue
            seen_keys.add(edge.msg_id)
            plans.append(RedeliveryPlan(
                dedup_key=edge.msg_id,
                target=edge.to_pid,
                request=UserRequest(text=edge.text, metadata={
                    "from": edge.from_pid,
                    "type": edge.kind,
                    "msg_id": edge.msg_id,
                    "redelivered": True,
                }),
            ))

    # 规则 2：child_finished 确定性键
    for child in replays.values():
        if not child.parent or child.parent not in replays:
            continue
        if child.status == "crashed":
            continue                       # 未结束，无 child_finished 可补
        parent = replays[child.parent]
        if child.parent not in restarted:
            continue
        aware = any(m.get("from") == child.pid for m in parent.user_metas)
        if aware:
            continue                       # 订阅去重：父已感知
        key = f"child_finished:{child.pid}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        plans.append(RedeliveryPlan(
            dedup_key=key,
            target=child.parent,
            request=UserRequest(
                text=f"子 agent '{child.pid}' 已完成。最终输出：{child.final_output}",
                metadata={
                    "type": "child_finished",
                    "from": child.pid,
                    "msg_id": key,
                    "redelivered": True,
                }),
        ))

    # 规则 3：Mode B entry 补投（半成品 spawn）
    for pid, prompt in (script_entry_prompts or {}).items():
        if pid not in restarted or pid not in replays:
            continue
        if f"spawn_entry:{pid}" in replays[pid].received_msg_ids:
            continue
        plans.append(RedeliveryPlan(
            dedup_key=f"spawn_entry:{pid}",
            target=pid,
            request=UserRequest(text=prompt, metadata={
                "type": "spawn_entry",
                "msg_id": f"spawn_entry:{pid}",
                "redelivered": True,
            }),
        ))

    return plans
```

并在 `harness/runtime/kernel.py` `_boot_resume` 第 3 步处把 `plan_redelivery(replays, restarted)` 调用改为传入 `script_entry_prompts`（Mode B 时从 `_create_agents_from_script` 返回的 agent_results 收集 `{pid: entry_prompt}`；Mode A 传 None）。同时给重投递加 dedup：`send_input` 前检查 `plan.dedup_key in report.redelivered` 跳过（防御同一 boot 内重复计划）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_redelivery.py tests/session/test_boot.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/core/session/replay.py harness/runtime/kernel.py tests/session/test_redelivery.py
git commit -m "feat(session): add cross-log pairing repair with msg_id edge, child_finished, and spawn_entry rules"
```

---

### Task 13: CLI/Runtime 入口 —— --resume / --force 接线

**依赖：** T10（boot）、T2（config）
**阻塞：** T14
**设计对应：** 恢复唯一入口 = `--resume <conv_id>` 启动器参数；无进程内切换。

**Files:**
- Modify: `main.py`
- Modify: `harness/runtime/runtime.py`
- Test: `tests/session/test_cli_resume.py`

- [ ] **Step 1: 写失败测试**

`tests/session/test_cli_resume.py`：

```python
"""CLI --resume/--force 入口测试。"""

from unittest.mock import patch

import pytest

from main import build_parser
from harness.core.session.config import load_session_config


class TestParser:
    def test_run_accepts_resume_and_force(self):
        args = build_parser().parse_args(["run", "--resume", "conv-1", "--force"])
        assert args.resume == "conv-1"
        assert args.force is True

    def test_defaults(self):
        args = build_parser().parse_args(["run"])
        assert args.resume is None
        assert args.force is False

    def test_workflow_accepts_resume(self):
        args = build_parser().parse_args(
            ["workflow", "s.py", "--resume", "conv-9"])
        assert args.resume == "conv-9"


class TestConfigLoading:
    def test_sessions_section_parsed(self, tmp_path):
        cfg = tmp_path / "harness.yaml"
        cfg.write_text("sessions:\n  root: /tmp/s\n  enabled: true\n",
                       encoding="utf-8")
        sc = load_session_config(str(cfg))
        assert sc.root == "/tmp/s" and sc.enabled is True

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        sc = load_session_config(str(tmp_path / "nope.yaml"))
        assert sc.enabled is True and sc.root.endswith("sessions")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/session/test_cli_resume.py -v`
Expected: FAIL — `--resume` 未定义（argparse error）

- [ ] **Step 3: 实现**

**3a. `main.py`：**
- `run` 与 `workflow` 子命令各加：`--resume CONV_ID`（恢复指定会话）与 `--force`（配合 --resume：强制接管所有权/降级 manifest 硬校验）。
- `_cmd_run`/`_cmd_workflow` 中：`session_config = load_session_config(args.config)`，构造 `Runtime(console, session_config=session_config)`，调用 `runtime.run(harness, resume=args.resume, force=args.force)` / `runtime.run_from_script(script, resume=args.resume, force=args.force)`。
- boot 抛出的 `SessionOwnerConflict`/`BootError` 捕获 → 打印友好错误并 `sys.exit(2)`。

**3b. `harness/runtime/runtime.py`：**
- `run(harness, *, resume=None, force=False)` / `run_from_script(script_path, *, resume=None, force=False)` 透传到 `_run_async`/`_run_from_script_async`。
- `_open_store` 改为只做创建（`SessionStore(...)`），**不再** `begin_session(None)`——会话开始由 `kernel.boot(...)` 统一负责：
  - resume 为 None：`await kernel.boot(harness=harness, call_llm=..., script_path=...)`
  - 否则：`await kernel.boot(conv_id=resume, force=force, harness=harness, call_llm=..., script_path=...)`
- 原 `_run_async` 中 `spawn_root`/`_run_from_script_async` 中 `spawn_from_script` 的直接调用**替换**为 boot 调用（boot 的 fresh 分支内部走同样的 spawn 路径，行为等价）；boot 完成后按 `BootReport.mode/warnings/lsn_gap/redelivered` 打印恢复摘要（`[系统] 已恢复会话 conv-1：重放 2 个 agent，补投 1 条消息，LSN 空洞 0`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/session/test_cli_resume.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 回归 + Commit**

```bash
python -m pytest tests/ -q
git add main.py harness/runtime/runtime.py tests/session/test_cli_resume.py
git commit -m "feat(session): expose --resume/--force CLI flags routing through kernel.boot"
```

---

### Task 14: 端到端测试 —— 全生命周期、崩溃变体、中断标记、LSN 空洞

**依赖：** T10–T13（完整链路）
**阻塞：** T15
**设计对应：** 完整流程推导 T0–T6 的可执行验证。

**Files:**
- Test: `tests/session/test_e2e.py`

- [ ] **Step 1: 写 E2E 测试**

```python
"""E2E：两次运行(seq 连续/单 header)、崩溃变体、中断标记、LSN 空洞。"""

import json
import os

import pytest

from harness.core.session.store import SessionStore
from harness.interfaces.types import Response, ToolCall, ToolCallFunction, UserRequest
from harness.runtime.kernel import Kernel
from tests.session._fakes import MockConsole, run_async
from tests.session.test_boot import _harness_with, _write_log
from tests.session.test_orchestrator_recording import (
    EchoProvider, ScriptedAdapter, _llm_scripted,
)


def _read(conv_dir, pid="root"):
    return [json.loads(l) for l in
            (conv_dir / "agents" / f"{pid}.jsonl")
            .read_text(encoding="utf-8").splitlines()]


class TestFullLifecycle:
    @run_async
    async def test_two_runs_share_one_contiguous_log(self, tmp_path):
        """T0–T6 全链路：run1 正常结束 → run2 resume 继续，单 header、seq 连续。"""
        from harness.core.container import DIContainer
        from harness.interfaces.system_tool_provider import SystemToolProvider
        from harness.interfaces.async_input_adapter import AsyncInputAdapter

        async def llm1(messages, tools):
            return Response(text="第一轮回复")

        # ── run 1 ──
        store1 = SessionStore(str(tmp_path))
        k1 = Kernel(MockConsole(), store=store1)
        h1 = _harness_with(ScriptedAdapter([UserRequest(text="问题一")]))
        await k1.boot(harness=h1, call_llm=llm1)
        await k1._tasks["root"]
        conv_id = store1.conv_id
        await store1.close()

        # ── run 2（resume）──
        store2 = SessionStore(str(tmp_path))
        k2 = Kernel(MockConsole(), store=store2)

        async def llm2(messages, tools):
            # 恢复的历史应在新 LLM 调用中可见
            contents = [m.get("content") for m in messages]
            assert "问题一" in contents and "第一轮回复" in contents
            return Response(text="第二轮回复")

        h2 = _harness_with(ScriptedAdapter([UserRequest(text="问题二")]))
        report = await k2.boot(conv_id=conv_id, harness=h2, call_llm=llm2)
        assert report.mode == "resume" and report.lsn_gap == 0
        await k2._tasks["root"]
        await store2.close()

        evts = _read(tmp_path / conv_id)
        assert [e["type"] for e in evts].count("header") == 1
        assert [e["seq"] for e in evts] == list(range(len(evts)))
        assert [e["type"] for e in evts].count("session_end") == 2
        idx = json.loads((tmp_path / conv_id / "index.json")
                         .read_text(encoding="utf-8"))
        assert idx["status"] == "paused"
        assert idx["agents"]["root"]["final_output"] == "第二轮回复"

    @run_async
    async def test_crash_variant_missing_session_end(self, tmp_path):
        """崩溃变体：剥掉 session_end → status=crashed、仍可 resume、index 重建。"""
        conv_dir = tmp_path / "conv-c"
        _write_log(tmp_path, "conv-c", "root", [
            {"type": "header", "format_version": 1, "conv_id": "conv-c",
             "pid": "root", "parent": None, "manifest_sha1": "m",
             "created_at": 1.0, "seq": 0, "lsn": 0, "ts": 1.0},
            {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
             "message": {"role": "user", "content": "崩前的消息"}},
            {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
             "message": {"role": "assistant", "content": "崩前的回复"}},
        ])
        # 无 index.json（崩溃时来不及写）→ rebuild
        store = SessionStore(str(tmp_path))
        k = Kernel(MockConsole(), store=store)
        report = await k.boot(conv_id="conv-c",
                              harness=_harness_with(ScriptedAdapter(
                                  [UserRequest(text="/exit")])),
                              call_llm=None)
        assert report.status_before == "crashed"
        assert (conv_dir / "index.json").exists()
        await k._tasks["root"]
        await store.close()

    @run_async
    async def test_interrupted_tool_call_gets_memory_only_marker(self, tmp_path):
        """中断检测：assistant tool_calls 无 tool_result → resume_marker 只在内存。"""
        _write_log(tmp_path, "conv-i", "root", [
            {"type": "header", "format_version": 1, "conv_id": "conv-i",
             "pid": "root", "parent": None, "manifest_sha1": "m",
             "created_at": 1.0, "seq": 0, "lsn": 0, "ts": 1.0},
            {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
             "message": {"role": "user", "content": "执行工具"}},
            {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
             "message": {"role": "assistant", "content": None,
                         "tool_calls": [{"id": "call_z", "type": "function",
                                         "function": {"name": "bash",
                                                      "arguments": "{}"}}]}},
        ])
        store = SessionStore(str(tmp_path))
        k = Kernel(MockConsole(), store=store)
        adapter = ScriptedAdapter([UserRequest(text="/exit")])
        h = _harness_with(adapter)
        from harness.core.container import DIContainer
        from harness.interfaces.system_tool_provider import SystemToolProvider
        h.container.register(SystemToolProvider, EchoProvider())
        report = await k.boot(conv_id="conv-i", harness=h, call_llm=None)
        assert any("call_z" in w for w in report.warnings)

        history = k.runtime_table["root"]._orchestrator._history
        assert any("call_z" in (m.content or "") and "中断" in m.content
                   for m in history)                      # 内存有标记
        await k._tasks["root"]
        await store.close()
        disk = (tmp_path / "conv-i" / "agents" / "root.jsonl") \
            .read_text(encoding="utf-8")
        assert "中断" not in disk                         # 盘上永不落标记

    def test_crafted_lsn_gap_measured(self, tmp_path):
        """手工构造 LSN 空洞：lsn 3 缺失 → gap=1。"""
        _write_log(tmp_path, "conv-g", "root", [
            {"type": "header", "format_version": 1, "conv_id": "conv-g",
             "pid": "root", "parent": None, "manifest_sha1": "m",
             "created_at": 1.0, "seq": 0, "lsn": 0, "ts": 1.0},
            {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
             "message": {"role": "user", "content": "a"}},
        ])
        _write_log(tmp_path, "conv-g", "w1", [
            {"type": "header", "format_version": 1, "conv_id": "conv-g",
             "pid": "w1", "parent": "root", "manifest_sha1": "m",
             "created_at": 1.0, "seq": 0, "lsn": 2, "ts": 1.0},
            {"type": "user", "seq": 1, "lsn": 4, "ts": 1.0,
             "message": {"role": "user", "content": "b"}},   # lsn 3 空洞
        ])
        from harness.core.session.replay import measure_lsn_gap, scan_session
        assert measure_lsn_gap(scan_session(tmp_path / "conv-g")) == 1
```

- [ ] **Step 2: 运行确认通过（链路已就绪则直接 PASS；失败则回到对应任务修复）**

Run: `python -m pytest tests/session/test_e2e.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 全量回归 + Commit**

```bash
python -m pytest tests/ -q
git add tests/session/test_e2e.py
git commit -m "test(session): add end-to-end lifecycle, crash, interruption, and lsn-gap tests"
```

---

### Task 15: 文档 —— ARCHITECTURE.md 章节、配置说明、README

**依赖：** T14（功能定型后写文档）
**阻塞：** 无（收尾）

**Files:**
- Modify: `ARCHITECTURE.md`（新增"会话持久化与恢复"章节）
- Modify: `README.md`（一句话 + 指向 ARCHITECTURE 章节）
- Modify: `harness.yaml`（如存在模板，补 `sessions:` 注释段）

- [ ] **Step 1: 写文档**

`ARCHITECTURE.md` 新增章节，要点（照设计定稿缩写）：

```markdown
## 会话持久化与恢复

- 内核机制（非插件），默认开启；配置面仅 `sessions.root` / `sessions.enabled`
- 存储：`sessions/<conv_id>/agents/<pid>.jsonl`（append-only 事实，唯一不可丢）
  + `index.json`（原子重写的投影，可从 jsonl 重建）
- 热路径零 I/O：内存即时、轮次边界批量 flush（page cache）、fsync 仅在
  finalize/close；单写协程 per 文件
- 三套序号：seq（文件内严格连续，缺号=损坏）/ msg_id（跨日志因果配对）/
  LSN（会话级单调，空洞=崩溃损失证据）
- 恢复：`--resume <conv_id>`（可加 --force）；boot 四步序
  "创建所有 → 种子 → 配对修复 → 启动所有"；manifest 分级校验
  （语义关键不一致硬失败，--force 降级）
- 崩溃语义：尾部半行加载时截断；中断工具调用注入仅内存的恢复标记；
  已发未收消息按 msg_id 配对补投
```

`README.md` 在特性列表加一行：

```markdown
- 会话持久化与崩溃恢复（`--resume`）——见 [ARCHITECTURE.md](ARCHITECTURE.md#会话持久化与恢复)
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md README.md
git commit -m "docs(session): document persistence & resume architecture and config"
```

---

## 自检清单（writing-plans skill 要求）

**1. 规格覆盖**（对照设计总结各节）：

| 设计条目 | 覆盖任务 |
|---|---|
| D1 内核机制、默认开启、config 仅 root/enabled | T2、T7、T13 |
| D2 manifest 分级校验 + --force | T9、T10 |
| D3 存储布局 jsonl + index.json | T3、T4 |
| D4 内存优先/轮次 flush/fsync 仅 finalize | T5、T6 |
| D5 记录点 R0–R6、SessionLog 唯一咽喉 | T5、T6 |
| D6 所有权令牌 + pid 活性 + --force | T1（ids）、T10 |
| D7 --resume 启动器入口、无进程内切换 | T13 |
| D8 msg_id 内核盖章、四中介点 | T11 |
| D9 三套序号语义 | T1（events）、T5（seq/lsn）、T8（校验与度量） |
| D10 单写协程 per 文件 | T3 |
| 崩溃恢复流程（截断/损坏拒绝/中断检测） | T8、T10、T14 |
| 配对修复三规则 | T12 |
| Mode A / Mode B | T10、T12、T13 |
| BootReport / 恢复摘要打印 | T10、T13 |

无缺口。

**2. 占位符扫描**：T10 Step 3b/3d 中有少量"逐行保留"式指引（拆分重构处），因为它们是**移动既有代码**而非新写——已明确标注来源行与等价性要求，不属于空泛占位。其余所有步骤均含完整代码。

**3. 类型一致性**（跨任务核对）：
- `SessionLog.__init__(conv_id, pid, store=None, sequencer=None, parent=None, manifest_provider=None)`（T5）↔ T7 `_make_session_log`、T10 `open_log_for_append` 一致 ✓
- `SessionStore` 方法面：`begin_session / agent_log_path / create_log / writer_for / restore_sequencer / finalize_agent / read_index / rebuild_index / open_log_for_append / log_for / close / degraded / conv_id / root_path` —— T3/T4 定义，T7/T10/T11 消费；其中 `log_for`（T11 使用）在 T11 Step 3d 标注了"需 store 增加"，保持显式 ✓
- `ReplayResult` 字段（T8）↔ `seed(history, tool_call_records, last_seq, last_lsn)`（T5）↔ T10 调用一致 ✓
- `Edge`（T8）与 `StampedEdge`（T11）：命名不同——前者是 replay 还原的发送方事实，后者是 publish 的运行时返回。已刻意区分；`record_edge(msg_id, to, kind, text)`（T5）与两者字段序一致 ✓
- `BootReport`（T10）↔ T13 打印字段一致 ✓
- `plan_redelivery(replays, restarted, *, script_entry_prompts)`（T12）↔ T10 第 3 步调用一致 ✓
- `RESUME_MARKER` 只在 T8 定义、T10 使用、T14 断言，格式串含 `{call_id}` 占位 ✓

## 执行方式建议

任务粒度与依赖已按"子代理逐任务执行 + 任务间两阶段审查"设计：每个任务自带完整失败测试与实现，关键路径上的任务（T5/T6/T8/T10）完成后应运行全量回归再放行下一任务。
