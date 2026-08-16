"""SessionLog —— 每 agent 一份的会话日志（唯一咽喉点）。

内存真相（_history/_tool_call_records）与磁盘镜像（_pending → _LogWriter）
的唯一变异点（设计决策 D3/D4）：

- record_* 全部同步、零 I/O、零 await —— 热路径纯内存
- flush() 轮次边界调用，返回契约 = 该批事件已达 OS page cache
- finalize() 追加 session_end 并 fsync；失败用内存快照兜底重写（失败方向向下）
- seed() 仅供 boot 恢复播种 —— 重放永不重复写
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...interfaces.types import Message, ToolCallRecord
from . import events
from .manifest import manifest_sha1
from .sequencer import Sequencer

logger = logging.getLogger(__name__)


class SessionLog:
    """每 agent 一份。AgentRuntime 创建，编排器经共享引用读写。"""

    def __init__(self, *, conv_id: str, pid: str, store=None,
                 sequencer: Optional[Sequencer] = None,
                 parent: Optional[str] = None,
                 manifest_provider: Optional[Callable[[], Dict[str, Any]]] = None):
        self.conv_id = conv_id
        self.pid = pid
        self._store = store
        self._sequencer = sequencer or Sequencer()
        self._parent = parent
        self._manifest_provider = manifest_provider

        # 内存真相（orchestrator 经 history/tool_call_records 属性共享同一对象）
        self._history: List[Message] = []
        self._tool_call_records: List[ToolCallRecord] = []

        self._pending: List[str] = []   # 批缓冲（已编码 JSON 行；崩溃丢弃，尾部修复兜底）
        self._seq = 0                   # 下一个待分配的 seq（last_seq = _seq - 1）
        self._last_lsn = -1
        self._begun = False             # header 是否已入缓冲（或已在盘上）
        self._finalized = False
        self._writer = None

    # ── 只读视图 ──

    @property
    def history(self) -> List[Message]:
        return self._history

    @property
    def tool_call_records(self) -> List[ToolCallRecord]:
        return self._tool_call_records

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def last_seq(self) -> int:
        return self._seq - 1

    @property
    def last_lsn(self) -> int:
        return self._last_lsn

    # ── 记录点（同步、零 I/O）──

    def begin(self) -> None:
        """R0：header 入缓冲（惰性——首个记录点触发；manifest 此刻已可计算）。"""
        if self._begun:
            return
        self._begun = True
        manifest: Dict[str, Any] = {}
        if self._manifest_provider is not None:
            try:
                manifest = self._manifest_provider() or {}
            except Exception as e:
                logger.warning("manifest_provider failed for '%s': %s", self.pid, e)
        sha = ""
        if manifest:
            sha = manifest_sha1(manifest)
            if self._store is not None:
                self._store.note_manifest(self.pid, manifest)
        seq, lsn, ts = self._next()
        self._append(events.make_header(
            conv_id=self.conv_id, pid=self.pid, parent=self._parent,
            manifest_sha1=sha, seq=seq, lsn=lsn, ts=ts,
        ))

    def record_message(self, message: Message, *,
                       meta: Optional[Dict[str, Any]] = None) -> None:
        """R1/R2/R3b/R4a：history 变异点镜像写入（先校验后变异）。

        非法 role（如 system）在 make_message_event 抛出——此刻零副作用；
        事件构造成功后才 append history、推进 seq/lsn（先 peek 后 commit），
        避免内存真相被污染 / seq 被烧掉导致盘上断档。
        """
        self.begin()
        evt = events.make_message_event(message, seq=self._seq,
                                        lsn=self._sequencer.next_value,
                                        ts=time.time(), meta=meta)
        self._history.append(message)
        self._seq += 1
        self._last_lsn = self._sequencer.next()
        self._append(evt)

    def record_tool_call(self, record: ToolCallRecord) -> None:
        """R3a：工具执行记录（Hook 之后的终值）。"""
        self.begin()
        self._tool_call_records.append(record)
        seq, lsn, ts = self._next()
        self._append(events.make_tool_call_event(record, seq=seq, lsn=lsn, ts=ts))

    def record_edge(self, *, msg_id: str, to: str, kind: str, text: str) -> None:
        """出站消息边（发送方事实）。只落盘，不入 history（事实-派生分离）。"""
        self.begin()
        seq, lsn, ts = self._next()
        self._append(events.make_edge_event(
            msg_id=msg_id, from_pid=self.pid, to_pid=to, kind=kind, text=text,
            seq=seq, lsn=lsn, ts=ts,
        ))

    def record_stop(self, stop_reason: str) -> None:
        """R4b：轮次结束。"""
        self.begin()
        seq, lsn, ts = self._next()
        self._append(events.make_stop_event(stop_reason=stop_reason,
                                            seq=seq, lsn=lsn, ts=ts))

    # ── 写通道（仅有的两个 async 方法）──

    async def flush(self) -> None:
        """轮次边界批量 flush。返回时该批事件已达 page cache。"""
        if not self._pending:
            return
        if not self._writable():
            self._pending.clear()
            return
        batch, self._pending = self._pending, []
        writer = self._ensure_writer()
        if writer is None or writer.closed:
            # store.close() 之后 enqueue 将无人 drain（barrier 永久挂起）——丢批返回
            logger.warning("flush after writer closed for '%s': dropping %d lines",
                           self.pid, len(batch))
            return
        barrier = writer.enqueue(batch)
        await barrier.wait()

    async def finalize(self, *, status: str, final_output: str,
                       execution_time: float) -> None:
        """R6：session_end 事件 + fsync。幂等。

        失败兜底：writer 已降级时，用本批次的内存快照同步直写一次。
        """
        if self._finalized:
            return
        self._finalized = True
        self.begin()  # 保证 header 在先（从未记录过的 agent 也产生合法日志）
        seq, lsn, ts = self._next()
        self._append(events.make_session_end_event(
            final_output=final_output, execution_time=execution_time,
            status=status, seq=seq, lsn=lsn, ts=ts,
        ))
        if not self._writable():
            self._pending.clear()
            return
        batch, self._pending = self._pending, []
        writer = self._ensure_writer()
        if writer is None or writer.closed:
            # store.close() 之后 enqueue 将无人 drain（barrier 永久挂起）——丢批返回
            logger.warning("finalize after writer closed for '%s': dropping %d lines",
                           self.pid, len(batch))
            return
        barrier = writer.enqueue_final(batch)
        await barrier.wait()
        if writer.degraded:
            self._finalize_fallback(batch)

    # ── boot 播种（恢复路径专用）──

    def seed(self, *, history: List[Message],
             tool_call_records: List[ToolCallRecord],
             last_seq: int, last_lsn: int) -> None:
        """重放结果直接装入内存（重放永不重复写）。seq 续接 last_seq+1。"""
        assert last_seq >= 0, "seed last_seq 必须 >= 0（首行须为 header seq=0）"
        self._history.extend(history)
        self._tool_call_records.extend(tool_call_records)
        self._seq = last_seq + 1
        self._last_lsn = last_lsn
        self._begun = True  # header 已在盘上

    # ── 内部 ──

    def _next(self) -> tuple[int, int, float]:
        seq = self._seq
        self._seq += 1
        lsn = self._sequencer.next()
        self._last_lsn = lsn
        return seq, lsn, time.time()

    def _append(self, event: Dict[str, Any]) -> None:
        self._pending.append(events.encode_event(event))

    def _writable(self) -> bool:
        return (self._store is not None and self._store.enabled
                and self._store.conv_dir is not None)

    def _ensure_writer(self):
        if self._writer is None:
            self._writer = self._store._create_writer(self.pid)
        return self._writer

    def _finalize_fallback(self, batch: List[str]) -> None:
        """writer 降级后的 finalize 兜底：对账感知，只补盘上缺失的 seq 后缀。

        盲目追加会把可恢复日志变成拒绝恢复（重复行/断档 = seq gap），故：
        - suffix（batch 中 seq > ondisk_last_seq 的行）为空 → 批次已完整
          在盘上（write+flush 成功、fsync 失败），直接返回
        - suffix[0].seq == ondisk_last_seq + 1 → 追加 suffix + flush + fsync
        - 否则（中间有空洞/分叉）→ 只记录 error，不再恶化已不一致的状态
        """
        try:
            path = self._store.agent_log_path(self.pid)
            ondisk_last_seq = self._read_ondisk_last_seq(path)
            suffix = [l for l in batch if json.loads(l)["seq"] > ondisk_last_seq]
            if not suffix:
                logger.info("finalize fallback: batch already on disk for '%s'",
                            self.pid)
                return
            if json.loads(suffix[0])["seq"] != ondisk_last_seq + 1:
                logger.error(
                    "finalize fallback: seq divergence for '%s' "
                    "(ondisk_last=%d, batch_head=%d) — skip",
                    self.pid, ondisk_last_seq, json.loads(suffix[0])["seq"])
                return
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(suffix) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            logger.warning("finalize fallback snapshot written for '%s'", self.pid)
        except Exception as e:
            logger.error("finalize fallback failed for '%s': %s", self.pid, e)

    def _read_ondisk_last_seq(self, path: Path) -> int:
        """容错读取盘上 last_seq（文件不存在/为空 = -1）。

        末字节非换行 → r+b 物理截断半截尾行（防 fallback 字节拼接到半截行尾）；
        逐行 decode，遇首个不可解码行停止（视为尾部截断点）。
        """
        if not path.exists():
            return -1
        raw = path.read_bytes()
        if not raw:
            return -1
        if not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n")  # -1 → 整文件仅半截行，清空
            with open(path, "r+b") as fh:
                fh.truncate(cut + 1)
            raw = raw[:cut + 1]
        last_seq = -1
        for line in raw.splitlines():
            try:
                evt = json.loads(line)
                if not isinstance(evt, dict) or not isinstance(evt.get("seq"), int):
                    raise ValueError("not an event line")
            except ValueError:  # JSONDecodeError/UnicodeDecodeError 均属之
                break
            last_seq = evt["seq"]
        return last_seq
