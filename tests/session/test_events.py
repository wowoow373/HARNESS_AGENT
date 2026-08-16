"""events.py — 事件 schema 与编解码测试。"""

import pytest

from harness.core.session import events
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

    def test_pid_alive_posix_path(self):
        """POSIX 路径：自身 pid 探活为 True（win32 上跳过，门禁行为见下）。"""
        import os
        import sys
        if sys.platform == "win32":
            pytest.skip("pid_alive is POSIX-only")
        assert events.pid_alive(os.getpid()) is True

    def test_pid_alive_windows_gate(self, monkeypatch):
        """win32 上门禁生效：抛 NotImplementedError 而非误杀被探测进程。"""
        import os
        import sys
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(NotImplementedError):
            events.pid_alive(os.getpid())


class TestContractPins:
    def test_make_message_event_rejects_unknown_role(self):
        import pytest as _pytest
        from harness.interfaces.types import Message as _Msg
        with _pytest.raises(ValueError, match="unsupported message role"):
            events.make_message_event(_Msg(role="system", content="x"),
                                      seq=1, lsn=1, ts=1.0)

    def test_decode_rejects_wrong_value_types(self):
        import pytest as _pytest
        with _pytest.raises(ValueError):
            events.decode_event('{"type": "user", "seq": "abc"}')
        with _pytest.raises(ValueError):
            events.decode_event('{"type": 1, "seq": 0}')
