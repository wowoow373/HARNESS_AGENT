"""E2E：两次运行(seq 连续/单 header)、崩溃变体、中断标记、LSN 空洞。"""

import json

from harness.core.session.store import SessionStore
from harness.interfaces import ContextAssembler, SystemToolProvider
from harness.interfaces.types import Response, UserRequest
from harness.runtime.kernel import Kernel
from tests.session._fakes import MockConsole, run_async
from tests.session.test_boot import _harness_with, _write_log
from tests.session.test_orchestrator_recording import (
    EchoProvider, ScriptedAdapter,
)


def _read(conv_dir, pid="root"):
    return [json.loads(l) for l in
            (conv_dir / "agents" / f"{pid}.jsonl")
            .read_text(encoding="utf-8").splitlines()]


class _HistoryAssembler:
    """把 history + 当前 user 请求原样组装进 LLM 入参（E2E 校验种子可见）。"""

    def assemble(self, ctx):
        msgs = [{"role": m.role, "content": m.content} for m in ctx.history]
        msgs.append({"role": "user", "content": ctx.user_request.text})
        return msgs


class TestFullLifecycle:
    @run_async
    async def test_two_runs_share_one_contiguous_log(self, tmp_path):
        """T0–T6 全链路：run1 正常结束 → run2 resume 继续，单 header、seq 连续。"""

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
            contents = [m.get("content") for m in messages]
            assert "问题一" in contents and "第一轮回复" in contents
            return Response(text="第二轮回复")

        h2 = _harness_with(ScriptedAdapter([UserRequest(text="问题二")]))
        h2.container.register(ContextAssembler, _HistoryAssembler())
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
        """崩溃变体：无 session_end → status=crashed、index 重建、仍可 resume。"""
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
        h.container.register(SystemToolProvider, EchoProvider())
        report = await k.boot(conv_id="conv-i", harness=h, call_llm=None)
        assert any("call_z" in w for w in report.warnings)

        history = k.runtime_table["root"]._orchestrator._history
        assert any("call_z" in (m.content or "") and "中断" in m.content
                   for m in history)
        await k._tasks["root"]
        await store.close()
        disk = (tmp_path / "conv-i" / "agents" / "root.jsonl") \
            .read_text(encoding="utf-8")
        assert "中断" not in disk

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
