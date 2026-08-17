"""Kernel.boot —— fresh/resume 统一入口、所有权、manifest 校验、种子恢复。"""

import json

import pytest

from harness.core.session.boot import BootReport
from harness.core.session.exceptions import BootError, SessionOwnerConflict
from harness.core.session.ids import new_msg_id
from harness.core.session.store import SessionStore
from harness.interfaces import ContextAssembler
from harness.interfaces.types import Message, Response, UserRequest
from harness.runtime.kernel import Kernel
from tests.session._fakes import MockConsole, MockHarness, run_async
from tests.session.test_kernel_wiring import ExitImmediatelyAdapter
from tests.session.test_orchestrator_recording import ScriptedAdapter


async def _async_llm(messages, tools):
    return Response(text="你好！")


class _RecordingAssembler:
    """ContextAssembler 探针：记录每轮 assemble 看到的 history 快照。

    注意：orchestrator._history 在 _phase_end 被清空（session_end 落盘之后），
    任务结束后再读恒为空——验证"种子进入 LLM 上下文"必须在组装点观测。
    """

    def __init__(self):
        self.seen_histories = []

    def assemble(self, ctx):
        self.seen_histories.append([m.content for m in ctx.history])
        return [Message(role="user", content=ctx.user_request.text)]


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
        await kernel._tasks["root"]
        assert kernel.runtime_table["root"].state.name == "FINISHED"
        await store.close()


class TestResumeBoot:
    @run_async
    async def test_resume_seeds_history_and_continues_seq(self, tmp_path):
        _ended_conv(tmp_path)
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        assembler = _RecordingAssembler()
        harness = _harness_with(ScriptedAdapter([UserRequest(text="新消息")]))
        harness.container.register(ContextAssembler, assembler)
        report = await kernel.boot(
            conv_id="conv-1",
            harness=harness,
            call_llm=_async_llm)
        assert report.mode == "resume"
        assert report.status_before == "paused"
        assert "root" in report.replayed
        # 种子即时进入 orchestrator（与 session_log 共享同一 list；
        # boot 返回后 task 尚未被调度，此时读取是确定性的）
        history = [m.content for m in
                   kernel.runtime_table["root"]._orchestrator._history]
        assert history[:2] == ["旧消息", "旧回复"]

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
        # 恢复的历史进入 LLM 上下文（组装点可见种子消息）
        assert assembler.seen_histories
        assert assembler.seen_histories[0][:2] == ["旧消息", "旧回复"]

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
