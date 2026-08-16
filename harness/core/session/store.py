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
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from .events import FORMAT_VERSION
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
        """开始（或接管）会话目录。

        全新会话：创建目录、生成 owner、初始化 index。
        恢复接管（conv_id 已存在）：保留 created_at/manifest/script/agents，
        更换 owner token，status 置回 active。
        """
        self._conv_id = conv_id or new_conv_id()
        if not self._enabled:
            return self._conv_id
        self._conv_dir = self._root / self._conv_id
        (self._conv_dir / "agents").mkdir(parents=True, exist_ok=True)
        self._owner_token = new_owner_token()

        existing = self.read_index(self._conv_id)
        now = time.time()
        self._index_data = {
            "format_version": FORMAT_VERSION,
            "conv_id": self._conv_id,
            "status": "active",
            "owner": {"token": self._owner_token, "acquired_at": now},
            "manifest": (existing or {}).get("manifest"),
            "script": script if script is not None else (existing or {}).get("script"),
            "agents": (existing or {}).get("agents", {}),
            "created_at": (existing or {}).get("created_at", now),
            "updated_at": now,
        }
        # _agent_index 与 _index_data["agents"] 同一引用：
        # finalize_agent 改它后经 write_index(agents=...) 落盘
        self._agent_index = self._index_data["agents"]
        self.write_index()
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

    def create_log(self, pid: str, *, parent: Optional[str] = None,
                   manifest_provider=None):
        """为 agent 创建 SessionLog（writer 懒创建于首次 flush）。

        enabled=False 时同样创建（store 不可写 → SessionLog 纯内存运行，
        保持"唯一咽喉点"语义不随配置分叉）。
        """
        from .session_log import SessionLog

        log = SessionLog(
            conv_id=self._conv_id or "ephemeral",
            pid=pid,
            store=self,
            sequencer=self._sequencer,
            parent=parent,
            manifest_provider=manifest_provider,
        )
        self._logs[pid] = log
        return log

    def restore_sequencer(self, next_lsn: int) -> None:
        """boot 恢复：Sequencer = max(lsn) + 1。"""
        self._sequencer = Sequencer(next_lsn)

    async def close(self) -> None:
        """进程退出路径：drain 全部 writer → fsync → close → index 写终态。"""
        for pid, writer in list(self._writers.items()):
            try:
                await writer.close()
            except Exception as e:
                logger.error("writer close failed for '%s': %s", pid, e)
        if self._enabled and self._index_data is not None:
            self.write_index(status="paused", owner=None, updated_at=time.time())

    # ── index.json 投影（原子重写；可丢失，可重建）──

    def read_index(self, conv_id: str) -> Optional[dict]:
        """读取 index.json；不存在、损坏（含非 dict 的合法 JSON）返回 None。"""
        path = self._root / conv_id / "index.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):  # ValueError 覆盖 JSONDecodeError 与 UnicodeDecodeError
            return None
        return data if isinstance(data, dict) else None

    def write_index(self, **patch) -> None:
        """原子重写 index.json（tmp + os.replace）。投影，随时可重建。

        失败方向向下：OSError 只记录不升级——为可重建的投影崩对话路径是本末倒置。
        """
        if not self._enabled or self._conv_dir is None or self._index_data is None:
            return
        self._index_data.update(patch)
        tmp = self._conv_dir / "index.json.tmp"
        try:
            tmp.write_text(
                json.dumps(self._index_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._conv_dir / "index.json")
        except OSError as e:
            logger.error("index.json write failed (rebuildable projection): %s", e)

    def note_manifest(self, pid: str, manifest: dict) -> None:
        """记录装配清单。首个上报者（通常 root）的 manifest 入 index。"""
        if not self._enabled or self._index_data is None:
            return
        if not self._index_data.get("manifest"):
            self.write_index(manifest=manifest, updated_at=time.time())

    async def finalize_agent(self, pid: str, *, final_output: str,
                             execution_time: float, status: str = "paused") -> None:
        """agent FINISHED 时的幂等收尾：SessionLog.finalize 兜底 + index 投影更新。

        正常路径 session_end 已在 _phase_end 写入（T6）；这里是防御性兜底
        （_phase_end 未跑到时补写），并保证 index 的 agents[pid] 被更新。
        """
        log = self._logs.get(pid)
        if log is not None and not log.finalized:
            await log.finalize(status=status, final_output=final_output,
                               execution_time=execution_time)
        if log is not None:
            self._agent_index[pid] = {
                "last_seq": log.last_seq,
                "last_lsn": log.last_lsn,
                "status": status,
            }
            self.write_index(agents=self._agent_index, updated_at=time.time())

    def rebuild_index(self, conv_id: str) -> dict:
        """index 丢失/损坏时从 agents/*.jsonl 重建投影（惰性导入 replay 避免循环）。

        调用顺序约束：仅可在 begin_session 接管前调用；
        对已打开的会话调用，重建结果会被后续 close() 的终态写覆盖。
        """
        from .replay import scan_session  # replay 依赖 events，不依赖 store

        conv_dir = self._root / conv_id
        replays = scan_session(conv_dir)
        now = time.time()
        rebuilt = {
            "format_version": FORMAT_VERSION,
            "conv_id": conv_id,
            "status": "crashed",  # 有日志但无 index = 崩溃痕迹
            "owner": None,
            "manifest": None,
            "script": None,
            "agents": {
                pid: {"last_seq": r.last_seq, "last_lsn": r.max_lsn,
                      "status": r.status}
                for pid, r in replays.items()
            },
            "created_at": now,
            "updated_at": now,
            "rebuilt": True,
        }
        path = conv_dir / "index.json"
        tmp = conv_dir / "index.json.tmp"
        tmp.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return rebuilt
