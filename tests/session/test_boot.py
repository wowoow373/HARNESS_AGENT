"""Kernel.boot —— fresh/resume 统一入口、所有权、manifest 校验、种子恢复。"""

import asyncio
import json
import os
import sys

import pytest

from harness.core.session.boot import BootReport
from harness.core.session.exceptions import BootError, SessionOwnerConflict
from harness.core.session.ids import new_msg_id
from harness.core.session.store import SessionStore
from harness.interfaces import ContextAssembler, SystemToolProvider
from harness.interfaces.types import (
    Message, Response, ToolDefinition, UserRequest,
)
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


class _SearchProvider:
    """SystemToolProvider 替身：只提供 search 工具。"""

    def get_tools(self):
        return [ToolDefinition(name="search")]

    def execute(self, name, args):
        return None


def _runtime_tool_names():
    """_inject_runtime_tools 注入的 runtime 工具名集合（动态取，防硬编码漂移）。"""
    from harness.runtime.tools import create_runtime_tools
    probe_kernel = Kernel(MockConsole())
    return {t.get_definition().name
            for t in create_runtime_tools(kernel=probe_kernel, pid="root")}


def _tool_conv(tmp_path, conv_id="conv-tools", tool_names=()):
    """构造一个用过 search 工具的已结束会话（tool_call 已闭合）。

    index manifest 记录 SystemToolProvider.tool_names=tool_names
    （调用方负责与本次 boot 探针将看到的工具集对齐/错开）。
    """
    _write_log(tmp_path, conv_id, "root", [
        {"type": "header", "format_version": 1, "conv_id": conv_id, "pid": "root",
         "parent": None, "manifest_sha1": "m1", "created_at": 1.0,
         "seq": 0, "lsn": 0, "ts": 1.0},
        {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
         "message": {"role": "user", "content": "查一下"}},
        {"type": "assistant", "seq": 2, "lsn": 2, "ts": 1.0,
         "message": {"role": "assistant", "content": "",
                     "tool_calls": [{"id": "call_1", "type": "function",
                                     "function": {"name": "search",
                                                  "arguments": "{}"}}]}},
        {"type": "tool_call", "seq": 3, "lsn": 3, "ts": 1.0,
         "record": {"tool_call_id": "call_1", "tool_name": "search",
                    "arguments": {}, "result": "结果",
                    "started_at": 1.0, "finished_at": 1.0, "error": None}},
        {"type": "tool_result", "seq": 4, "lsn": 4, "ts": 1.0,
         "message": {"role": "tool", "content": "结果",
                     "tool_call_id": "call_1"}},
        {"type": "assistant", "seq": 5, "lsn": 5, "ts": 1.0,
         "message": {"role": "assistant", "content": "答案"}},
        {"type": "stop", "seq": 6, "lsn": 6, "ts": 1.0, "stop_reason": "end_turn"},
        {"type": "session_end", "seq": 7, "lsn": 7, "ts": 1.0,
         "final_output": "答案", "execution_time": 1.0, "status": "paused"},
    ])
    (tmp_path / conv_id / "index.json").write_text(json.dumps({
        "conv_id": conv_id, "created_at": 1.0, "owner": None,
        "status": "paused", "manifest_sha1": "m1",
        "manifest": {"SystemToolProvider": {"id": "x",
                                            "tool_names": list(tool_names)},
                     "llm": {"model": None}},
        "script": None,
        "agents": {"root": {"parent": None, "last_seq": 7, "last_lsn": 7,
                            "status": "paused", "final_output": "答案",
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
    @pytest.mark.skipif(sys.platform == "win32", reason="pid_alive POSIX-only")
    async def test_owner_conflict_refused_and_forced(self, tmp_path):
        _ended_conv(tmp_path)
        idx_path = tmp_path / "conv-1" / "index.json"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))

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
    @pytest.mark.skipif(sys.platform == "win32", reason="pid_alive POSIX-only")
    async def test_dead_owner_takeover_succeeds(self, tmp_path):
        """owner 为死进程 → 无 force 直接接管；index owner 换成本进程 token。"""
        import os
        _ended_conv(tmp_path)
        idx_path = tmp_path / "conv-1" / "index.json"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        idx["owner"] = "pid-999999-1"          # 死进程持有
        idx["status"] = "active"
        idx_path.write_text(json.dumps(idx), encoding="utf-8")

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            conv_id="conv-1",
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "resume"

        owner = json.loads(idx_path.read_text(encoding="utf-8"))["owner"]
        assert isinstance(owner, dict)                       # begin_session 形状
        assert f"pid-{os.getpid()}-" in owner["token"]       # 已换成本进程
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
        # 续跑结束后全量重解析：每行合法 JSON 且 seq 连续（截断未留隐患）
        evts = [json.loads(l) for l in
                p.read_text(encoding="utf-8").splitlines()]
        assert [e["seq"] for e in evts] == list(range(len(evts)))


class TestBootGuards:
    """boot 入口守卫：store 缺失 / Mode 组合模糊 / 接管前廉价校验。"""

    @run_async
    async def test_resume_without_store_is_boot_error(self, tmp_path):
        """I1：store 缺失时 conv_id 不得被静默丢弃为 fresh。"""
        kernel = Kernel(MockConsole())           # 无 store
        with pytest.raises(BootError, match="持久化未启用"):
            await kernel.boot(conv_id="conv-1",
                              harness=_harness_with(ExitImmediatelyAdapter()),
                              call_llm=_async_llm)

    @run_async
    async def test_script_conv_without_script_path_is_boot_error(self, tmp_path):
        """I2：会话由脚本创建但未提供 script_path → 接管前 BootError。"""
        _ended_conv(tmp_path)
        idx_path = tmp_path / "conv-1" / "index.json"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        idx["script"] = {"path": "/old/wf.py", "sha1": "abc"}
        idx_path.write_text(json.dumps(idx), encoding="utf-8")

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(BootError, match="script_path"):
            await kernel.boot(conv_id="conv-1",
                              harness=_harness_with(ExitImmediatelyAdapter()),
                              call_llm=_async_llm)
        # 未接管：index owner 仍是 None
        assert json.loads(idx_path.read_text(encoding="utf-8"))["owner"] is None

    @run_async
    async def test_script_path_without_script_meta_warns_mode_a(self, tmp_path):
        """I2：提供了 script_path 但会话无脚本记录 → 告警 + 按 Mode A 恢复。"""
        _ended_conv(tmp_path)
        script = tmp_path / "wf.py"
        script.write_text("# 内容无所谓\n", encoding="utf-8")

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            conv_id="conv-1", script_path=str(script),
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "resume"
        assert "root" in report.replayed
        assert any("无脚本记录" in w for w in report.warnings)
        await kernel._tasks["root"]
        await store.close()

    @run_async
    async def test_rebuild_index_recovers_mode_b_with_script(self, tmp_path):
        """I1：index 丢失/重建后无 script 记录，但日志无 root 且脚本在手
        → 推断 Mode B 恢复（而非 BootError 缺少 root 日志）。"""
        from tests.runtime.test_e2e_workflow import _write_workflow_script

        conv_id = "conv-wf"
        # 无 root 日志、多 agent（w1）的 Mode B 会话；不写 index.json（模拟丢失）
        _write_log(tmp_path, conv_id, "w1", [
            {"type": "header", "format_version": 1, "conv_id": conv_id,
             "pid": "w1", "parent": "root", "manifest_sha1": "m0",
             "created_at": 1.0, "seq": 0, "lsn": 0, "ts": 1.0},
            {"type": "user", "seq": 1, "lsn": 1, "ts": 1.0,
             "message": {"role": "user", "content": "去干活"}},
        ])

        script = _write_workflow_script([{"name": "w1",
                                          "entry_prompt": "去干活"}])
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        try:
            report = await kernel.boot(conv_id=conv_id, script_path=script,
                                       harness=None)
            assert report.mode == "resume"
            assert "w1" in report.replayed
            assert any("按提供的脚本恢复" in w for w in report.warnings)
        finally:
            await asyncio.gather(*kernel._tasks.values())
            await store.close()
            os.unlink(script)

    @run_async
    async def test_mode_a_without_harness_is_boot_error(self, tmp_path):
        """I3：Mode A 缺 harness → 接管前 BootError（不留活主残留）。"""
        _ended_conv(tmp_path)
        idx_path = tmp_path / "conv-1" / "index.json"

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(BootError, match="harness"):
            await kernel.boot(conv_id="conv-1", harness=None,
                              call_llm=_async_llm)
        # 未接管：index 保持 paused / owner None
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        assert idx["owner"] is None and idx["status"] == "paused"

    @run_async
    async def test_resume_rejects_path_traversal_conv_id(self, tmp_path):
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(BootError, match="非法会话"):
            await kernel.boot(conv_id="../evil",
                              harness=_harness_with(ExitImmediatelyAdapter()),
                              call_llm=_async_llm)

    @run_async
    async def test_fresh_mode_b_records_script_meta(self, tmp_path):
        script = tmp_path / "wf.py"
        script.write_text(
            "from harness.core.container import DIContainer\n"
            "from harness.di import Harness\n"
            "from harness.interfaces.input_adapter import InputAdapter\n"
            "from harness.runtime.decorators import agent\n"
            "@agent('worker', entry_prompt='do it')\n"
            "def assemble_worker():\n"
            "    c = DIContainer()\n"
            "    c.register(InputAdapter, object())\n"
            "    return Harness.from_container(c, call_llm=None)\n",
            encoding="utf-8",
        )
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(script_path=str(script), call_llm=_async_llm)
        assert report.mode == "fresh"
        # 等待 oneshot agent 跑完并关闭 store，避免泄漏 task
        # （先 gather/close 再断言，断言失败也不会挂起 asyncio.run 收尾）
        await asyncio.gather(*kernel._tasks.values())
        await store.close()
        idx = json.loads((tmp_path / store.conv_id / "index.json")
                         .read_text(encoding="utf-8"))
        assert idx["script"]["path"] == str(script)
        assert idx["script"]["sha1"]


class TestManifestGate:
    """C1：manifest 分级校验在创建之后执行——探针能看到完整工具集。"""

    @run_async
    async def test_used_tool_present_succeeds_without_warnings(self, tmp_path):
        expected = sorted(_runtime_tool_names() | {"search"})
        _tool_conv(tmp_path, tool_names=expected)
        harness = _harness_with(ExitImmediatelyAdapter())
        harness.container.register(SystemToolProvider, _SearchProvider())

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(conv_id="conv-tools", harness=harness,
                                   call_llm=_async_llm)
        assert report.mode == "resume"
        assert report.replayed == ["root"]
        assert report.warnings == []             # 无硬失败、无假软告警
        await kernel._tasks["root"]
        await store.close()

    @run_async
    async def test_missing_used_tool_hard_fails_and_force_degrades(self, tmp_path):
        expected = sorted(_runtime_tool_names() | {"search"})
        _tool_conv(tmp_path, tool_names=expected)
        # 容器不提供 search（composite 只带 runtime 工具）→ 硬失败
        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        with pytest.raises(BootError, match="search"):
            await kernel.boot(
                conv_id="conv-tools",
                harness=_harness_with(ExitImmediatelyAdapter()),
                call_llm=_async_llm)

        # force 降级为告警（同 store 二次接管：create_log 允许替换未 begun 的 log）
        kernel2 = Kernel(MockConsole(), store=store)
        report = await kernel2.boot(
            conv_id="conv-tools", force=True,
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "resume"
        assert any("[force 降级]" in w and "search" in w
                   for w in report.warnings)
        await kernel2._tasks["root"]
        await store.close()

    @run_async
    async def test_probe_unavailable_skips_check_with_warning(
            self, tmp_path, monkeypatch):
        """探针计算失败（{}）→ 跳过分级校验 + warning，绝不硬阻断。"""
        expected = sorted(_runtime_tool_names() | {"search"})
        _tool_conv(tmp_path, tool_names=expected)
        monkeypatch.setattr(
            Kernel, "_probe_manifest", lambda self, harness, runtime: {})

        store = SessionStore(str(tmp_path))
        kernel = Kernel(MockConsole(), store=store)
        report = await kernel.boot(
            conv_id="conv-tools",
            harness=_harness_with(ExitImmediatelyAdapter()),
            call_llm=_async_llm)
        assert report.mode == "resume"
        assert any("探针不可用" in w for w in report.warnings)
        await kernel._tasks["root"]
        await store.close()
