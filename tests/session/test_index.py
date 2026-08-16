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
