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


def plan_redelivery(replays, restarted, *, script_entry_prompts=None):
    """配对修复重投计划（T12 填充；当前恒为空）。"""
    return []


def _edge_from_talk_to(record: ToolCallRecord, *, source_pid: str) -> Optional[Edge]:
    """talk_to 的发送方事实：arguments 含目标与文本，result JSON 含 msg_id。"""
    if record.tool_name != "talk_to" or not record.result:
        return None
    try:
        payload = json.loads(record.result) if isinstance(record.result, str) else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
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
