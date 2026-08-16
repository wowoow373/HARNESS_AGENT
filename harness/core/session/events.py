"""Session 事件日志 schema 与编解码 —— 唯一编码路径。

一行一事件（JSONL，UTF-8）。事件类型：
- header            日志头（每文件第一行，seq=0；缺失/损坏 = 拒绝恢复）
- user/assistant/tool_result  对话消息（镜像 SessionLog._history；
                              仅 user/assistant/tool 三种 role 可持久化，
                              system 消息按设计不入日志）
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
    if not isinstance(evt["type"], str) or not isinstance(evt["seq"], int):
        raise ValueError(f"malformed session event line: {line[:80]!r}")
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
    """Message → user/assistant/tool_result 事件（按 role 映射）。

    仅支持 user/assistant/tool 三种 role；其余 role（如 system）
    按设计不持久化，静默映射会铸造未声明事件类型，故直接抛 ValueError。
    """
    evt_type = _MESSAGE_EVT_BY_ROLE.get(message.role)
    if evt_type is None:
        raise ValueError(
            f"unsupported message role for session event: {message.role!r}"
        )
    evt: Dict[str, Any] = {
        "type": evt_type,
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
