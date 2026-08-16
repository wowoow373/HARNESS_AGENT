"""SessionLog —— 内存真相 + 记录点 + flush/finalize + seed 测试。"""

import json
import os

import pytest

from harness.core.session import events
from harness.core.session.replay import load_agent_log
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


def _batch_lines(*seqs):
    """构造带指定 seq 的编码事件行（fallback 对账只读 seq）。"""
    return [events.encode_event(
        events.make_stop_event(stop_reason="end_turn", seq=s, lsn=s, ts=1.0)
    ) for s in seqs]


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
        log2 = store.create_log("b")  # 同一 store → 同一 Sequencer
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

    def test_record_message_invalid_role_does_not_mutate(self, tmp_path):
        """Critical 2：先校验后变异——非法 role 抛出，history/_seq/_pending/sequencer 全不变。"""
        store, log = _make_log(tmp_path)
        log.begin()  # header 入缓冲，建立基线
        seq0, lsn0 = log._seq, log.last_lsn
        pending0 = list(log._pending)
        next_lsn0 = log._sequencer.next_value
        with pytest.raises(ValueError):
            log.record_message(Message(role="system", content="x"))
        assert len(log.history) == 0
        assert log._seq == seq0
        assert log.last_lsn == lsn0
        assert log._pending == pending0
        assert log._sequencer.next_value == next_lsn0


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

    @run_async
    async def test_flush_finalize_after_store_close_drop_batch(self, tmp_path):
        """Minor 4：writer 已关闭（store.close 后）→ 丢批返回，不 enqueue（修复前永久挂起）。"""
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="a"))
        await log.flush()
        await store.close()          # writer 已关闭
        log.record_message(Message(role="user", content="b"))
        await log.flush()            # 丢批返回
        await log.finalize(status="paused", final_output="", execution_time=0.0)
        assert log.finalized
        assert log._pending == []


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

    def test_seed_rejects_negative_last_seq(self, tmp_path):
        """Minor 5：last_seq < 0 会让首行不是 header（seq=0 被占），直接拒绝误用。"""
        store, log = _make_log(tmp_path)
        with pytest.raises(AssertionError):
            log.seed(history=[], tool_call_records=[], last_seq=-1, last_lsn=-1)


class TestFinalizeFallback:
    """Important 3：降级路径 —— 对账感知 fallback 的端到端与对账矩阵。"""

    @staticmethod
    def _preset(store, pid, lines, *, half_line=None):
        """预置 agents/<pid>.jsonl 内容（可选末尾半截行、无尾换行）。"""
        path = store.agent_log_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(l + "\n" for l in lines)
        if half_line is not None:
            text += half_line
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _seqs_on_disk(path):
        return [json.loads(l)["seq"]
                for l in path.read_text(encoding="utf-8").splitlines()]

    @run_async
    async def test_finalize_fsync_failure_recovers_via_reconciliation(self,
                                                                      tmp_path,
                                                                      monkeypatch):
        """端到端：write+flush 成功、fsync 抛 OSError → writer 降级 →
        fallback 对账发现批次已完整落盘 → 不重复追加，日志可恢复。"""
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="你好"))

        def boom(fd):
            raise OSError("fsync boom")
        monkeypatch.setattr(os, "fsync", boom)

        await log.finalize(status="paused", final_output="再见", execution_time=1.0)
        assert store.writer_for("root").degraded

        result = load_agent_log(store.agent_log_path("root"))
        assert result.status == "paused"
        assert result.last_seq == 2
        lines = store.agent_log_path("root").read_text(encoding="utf-8").splitlines()
        assert sum(1 for l in lines if json.loads(l)["type"] == "session_end") == 1
        assert [json.loads(l)["seq"] for l in lines] == [0, 1, 2]  # 无重复行
        await store.close()

    def test_fallback_skips_when_batch_already_on_disk(self, tmp_path):
        """a) 盘上 seq 0-4、batch seq 3-4 → 批次已完整在盘上，跳过无追加。"""
        store, log = _make_log(tmp_path)
        path = self._preset(store, "root", _batch_lines(0, 1, 2, 3, 4))
        before = path.read_bytes()
        log._finalize_fallback(_batch_lines(3, 4))
        assert path.read_bytes() == before

    def test_fallback_appends_only_missing_suffix(self, tmp_path):
        """b) 盘上 seq 0-2、batch seq 2-4 → 只追加缺失的 3-4。"""
        store, log = _make_log(tmp_path)
        path = self._preset(store, "root", _batch_lines(0, 1, 2))
        log._finalize_fallback(_batch_lines(2, 3, 4))
        assert self._seqs_on_disk(path) == [0, 1, 2, 3, 4]

    def test_fallback_skips_on_seq_gap(self, tmp_path):
        """c) 盘上 seq 0-1、batch seq 3-4（断档）→ 跳过且文件不变。"""
        store, log = _make_log(tmp_path)
        path = self._preset(store, "root", _batch_lines(0, 1))
        before = path.read_bytes()
        log._finalize_fallback(_batch_lines(3, 4))
        assert path.read_bytes() == before

    def test_fallback_truncates_half_line_then_appends(self, tmp_path):
        """d) 文件末尾半截行 → 先物理截断，再按对账规则追加缺失后缀。"""
        store, log = _make_log(tmp_path)
        path = self._preset(store, "root", _batch_lines(0, 1, 2),
                            half_line='{"type": "stop", "seq": 3')
        log._finalize_fallback(_batch_lines(3, 4))
        lines = path.read_text(encoding="utf-8").splitlines()
        assert [json.loads(l)["seq"] for l in lines] == [0, 1, 2, 3, 4]
        for l in lines:
            events.decode_event(l)  # 全部可解码（无字节拼接）


class TestFinalizeAgentIntegration:
    """额外（T4 质量评审建议）：用真实 SessionLog 验证 store.finalize_agent 的 duck-typed 契约。"""

    @run_async
    async def test_finalize_agent_with_real_session_log(self, tmp_path):
        store, log = _make_log(tmp_path)
        log.record_message(Message(role="user", content="hi"))
        await store.finalize_agent("root", final_output="done", execution_time=0.5)
        evts = _read_events(store, "root")
        assert sum(1 for e in evts if e["type"] == "session_end") == 1
        index = store.read_index(store.conv_id)
        assert index["agents"]["root"]["last_seq"] == log.last_seq
        assert index["agents"]["root"]["status"] == "paused"
        await store.close()
