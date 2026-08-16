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

    def test_talk_to_non_dict_result_yields_no_edge(self, tmp_path):
        """talk_to 结果为合法 JSON 非对象（如 '"ok"'）→ 不产出边，且不崩溃。"""
        _conv(tmp_path, "root", [
            _header("root"),
            _user(1, 1, "呼叫 b"),
            {"type": "tool_call", "seq": 2, "lsn": 2, "ts": 1.0,
             "record": {"tool_call_id": "c10", "tool_name": "talk_to",
                        "arguments": {"pid": "b", "text": "在吗"},
                        "result": '"ok"',
                        "started_at": 1.0, "finished_at": 1.1, "error": None}},
        ])
        r = load_agent_log(tmp_path / "conv-1" / "agents" / "root.jsonl")
        assert r.edges == []


class TestScanAndGap:
    def test_scan_session_multi_agents(self, tmp_path):
        _conv(tmp_path, "root", [_header("root"), _user(1, 1, "a")])
        _conv(tmp_path, "b", [_header("b", parent="root", lsn=2),
                              _user(1, 3, "b")])   # seq 文件内连续；lsn 全局单调
        replays = scan_session(tmp_path / "conv-1")
        assert set(replays) == {"root", "b"}

    def test_lsn_gap_measures_crash_loss(self, tmp_path):
        """LSN 空洞 = 崩溃损失度量：lsn 4 缺失（已发号未落盘）。"""
        _conv(tmp_path, "root", [_header("root"), _user(1, 1, "a")])
        _conv(tmp_path, "b", [_header("b", parent="root", lsn=2),
                              _user(1, 3, "b"),   # seq 文件内连续
                              _user(2, 5, "c")])  # lsn 4 空洞
        replays = scan_session(tmp_path / "conv-1")
        assert measure_lsn_gap(replays) == 1
