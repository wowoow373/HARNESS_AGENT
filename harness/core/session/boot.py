"""boot —— fresh/resume 统一启动编排（设计第五节 Boot-Resume 时序图）。

"创建所有 → 种子 → 配对修复 → 启动所有"四步序由 Kernel.boot 驱动；
本模块提供数据结构与纯函数（恢复计划、恢复警告），与 Kernel 解耦以便测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BootReport:
    """boot 结果报告（打印给用户 + 供 T13 CLI 汇总）。"""
    conv_id: str
    mode: str                       # "fresh" | "resume"
    status_before: str = ""         # resume 前的 index status
    replayed: List[str] = field(default_factory=list)     # 已种子的 pid
    lsn_gap: int = 0                # 崩溃损失度量（0 = 无损失）
    redelivered: List[str] = field(default_factory=list)  # 配对修复重投的 msg_id
    warnings: List[str] = field(default_factory=list)


@dataclass
class ResumePlan:
    """单个 agent 的恢复计划。"""
    pid: str
    restart: bool                   # Mode A: 仅 root；Mode B: 全体
    needs_marker: bool              # interrupted → 注入 resume_marker
    truncated_tail: bool            # 需要物理截断半行


def used_tool_names(replays: Dict[str, object]) -> set:
    """历史中实际用过的工具名集合（manifest 硬校验输入）。

    duck-type：replay 的 tool_call_records 元素只需有 ``tool_name`` 属性。
    """
    names = set()
    for r in replays.values():
        for rec in r.tool_call_records:
            names.add(rec.tool_name)
    return names
