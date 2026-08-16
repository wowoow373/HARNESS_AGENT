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

    @run_async
    async def test_close_idempotent(self, tmp_path):
        path = tmp_path / "c.jsonl"
        writer = _LogWriter(path)
        writer.start()
        await writer.close()
        await writer.close()  # 第二次不抛异常
        assert not path.exists()  # 从未 flush 数据 → 懒打开不建文件

    @run_async
    async def test_fsync_only_at_finalize_and_close(self, tmp_path, monkeypatch):
        """fsync 纪律：普通批次不 fsync；只在 finalize/close 触达持久介质。"""
        count = 0
        real_fsync = os.fsync

        def spy(fd):
            nonlocal count
            count += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", spy)
        writer = _LogWriter(tmp_path / "d.jsonl")
        writer.start()
        await writer.enqueue(['{"seq":1}']).wait()
        await writer.enqueue(['{"seq":2}']).wait()
        assert count == 0
        await writer.enqueue_final(['{"seq":3,"type":"session_end"}']).wait()
        assert count == 1
        await writer.close()
        assert count == 2


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
