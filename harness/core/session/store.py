"""SessionStore — 进程级单例：会话目录、writer 注册表、Sequencer、index.json。

属框架内核组件（非 DI、不可替换）。

写通道规则（设计第四节）：
- 每 agent 日志一个 _LogWriter 协程 —— 该文件的唯一写执行流
- 文件懒打开：首次 flush 才创建 agents/<pid>.jsonl
- flush 返回契约 = 该批事件已在 OS page cache（进程崩溃不丢）
- fsync 只在 finalize（enqueue_final）与 close（断电不丢已关闭会话）
- 写失败 → degraded → 后续批次直接放行，对话照常（失败方向向下，永不升级）
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from .ids import new_conv_id, new_owner_token
from .sequencer import Sequencer

logger = logging.getLogger(__name__)


class _Batch(NamedTuple):
    """writer 队列单元。"""
    lines: List[str]
    fsync: bool                       # finalize/close 批次为 True
    barrier: Optional[asyncio.Event]  # flush() 的等待点
    close: bool = False


class _LogWriter:
    """每 agent 日志一个写协程 —— 该文件的唯一写执行流。

    生产者（SessionLog.flush/finalize）只 enqueue，零 I/O；
    磁盘操作全部在 _run 协程 + asyncio.to_thread 内。
    """

    def __init__(self, path: Path):
        self._path = path
        self._queue: asyncio.Queue[_Batch] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._fh = None  # 线程内懒打开
        self.degraded = False
        self.error: Optional[str] = None

    # ── 生产者侧（event loop 内调用，零 I/O）──

    def start(self) -> None:
        assert self._task is None, "writer already started"
        self._task = asyncio.create_task(self._run())

    def enqueue(self, lines: List[str]) -> asyncio.Event:
        """普通批次（write + flush → page cache）。返回 flush 屏障。"""
        barrier = asyncio.Event()
        self._queue.put_nowait(_Batch(list(lines), fsync=False, barrier=barrier))
        return barrier

    def enqueue_final(self, lines: List[str]) -> asyncio.Event:
        """finalize 批次（write + flush + fsync）。返回屏障。"""
        barrier = asyncio.Event()
        self._queue.put_nowait(_Batch(list(lines), fsync=True, barrier=barrier))
        return barrier

    async def close(self) -> None:
        """drain → fsync → close。幂等。"""
        if self._task is None:
            return
        if self._task.done():
            # 任务已死（如被外部取消）：队列无人 drain，入队只会挂起，直接放弃
            self._task = None
            return
        done = asyncio.Event()
        self._queue.put_nowait(_Batch([], fsync=True, barrier=done, close=True))
        await done.wait()
        await self._task
        self._task = None

    # ── 消费者侧（唯一写执行流）──

    async def _run(self) -> None:
        while True:
            batch = await self._queue.get()
            try:
                if not self.degraded:
                    await asyncio.to_thread(self._write_batch, batch)
            except Exception as e:
                # 写失败 → 降级，置位屏障放行，永不升级打断对话
                self.degraded = True
                self.error = f"{type(e).__name__}: {e}"
                logger.error("LogWriter degraded: %s — %s", self._path, self.error)
            finally:
                if batch.barrier is not None:
                    batch.barrier.set()
                self._queue.task_done()
            if batch.close:
                break

    # ── 线程内（唯一触盘处）──

    def _write_batch(self, batch: _Batch) -> None:
        if self._fh is None and not batch.lines:
            return  # 无数据批次不建文件；屏障由 _run 的 finally 置位
        if self._fh is None:  # 懒打开
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a", encoding="utf-8")
        if batch.lines:
            self._fh.write("\n".join(batch.lines) + "\n")
            self._fh.flush()  # → page cache（flush 契约）
        if batch.fsync:
            os.fsync(self._fh.fileno())  # → 持久介质（finalize/close）
        if batch.close and self._fh is not None:
            self._fh.close()
            self._fh = None


class SessionStore:
    """进程级单例。

    职责：会话目录布局、writer 注册表、Sequencer 持有、index.json 投影。
    不在 DI 容器中，组件层拿不到引用。
    """

    def __init__(self, root: str, *, enabled: bool = True):
        self._root = Path(root)
        self._enabled = enabled
        self._conv_id: Optional[str] = None
        self._conv_dir: Optional[Path] = None
        self._sequencer = Sequencer()
        self._writers: Dict[str, _LogWriter] = {}
        self._logs: Dict[str, object] = {}        # pid → SessionLog（T5 起用）
        self._index_data: Optional[dict] = None
        self._agent_index: Dict[str, dict] = {}
        self._owner_token: Optional[str] = None

    # ── 基本属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def conv_id(self) -> Optional[str]:
        return self._conv_id

    @property
    def conv_dir(self) -> Optional[Path]:
        return self._conv_dir

    @property
    def sequencer(self) -> Sequencer:
        return self._sequencer

    @property
    def degraded(self) -> List[str]:
        """已降级的 writer pid 列表（供 close 时一次性提示）。"""
        return [pid for pid, w in self._writers.items() if w.degraded]

    # ── 会话生命周期 ──

    def begin_session(self, conv_id: Optional[str] = None,
                      *, script: Optional[dict] = None) -> str:
        """开始（或接管）会话目录。index.json 惰性：此处不写盘（T4 起写）。"""
        self._conv_id = conv_id or new_conv_id()
        if not self._enabled:
            return self._conv_id
        self._conv_dir = self._root / self._conv_id
        (self._conv_dir / "agents").mkdir(parents=True, exist_ok=True)
        self._owner_token = new_owner_token()
        return self._conv_id

    def agent_log_path(self, pid: str) -> Path:
        """agents/<pid>.jsonl 路径（pid 含路径分隔符时替换，防目录逃逸）。"""
        assert self._conv_dir is not None, "begin_session() must be called first"
        safe = pid.replace("/", "_").replace("\\", "_")
        return self._conv_dir / "agents" / f"{safe}.jsonl"

    def _create_writer(self, pid: str) -> Optional[_LogWriter]:
        """为 pid 创建并启动 writer（enabled=False 时返回 None）。"""
        if not self._enabled or self._conv_dir is None:
            return None
        writer = _LogWriter(self.agent_log_path(pid))
        writer.start()
        self._writers[pid] = writer
        return writer

    def writer_for(self, pid: str) -> Optional[_LogWriter]:
        return self._writers.get(pid)

    def restore_sequencer(self, next_lsn: int) -> None:
        """boot 恢复：Sequencer = max(lsn) + 1。"""
        self._sequencer = Sequencer(next_lsn)

    async def close(self) -> None:
        """进程退出路径：drain 全部 writer → fsync → close。"""
        for pid, writer in list(self._writers.items()):
            try:
                await writer.close()
            except Exception as e:
                logger.error("writer close failed for '%s': %s", pid, e)
