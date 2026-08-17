"""manifest —— 装配清单计算与分级校验（设计决策 D2）。

分级规则：
- 硬失败（语义关键）：ContextAssembler id 变化；
  历史中实际用过的工具在当前工具集中缺失
- 告警（其余）：GuideProvider/MCPAdapter 指纹变化、llm model 变化、
  未用工具增删
- --force：硬失败降级为告警（在 Kernel.boot 执行，不在此）

组件可选约定：实现 ``fingerprint() -> dict`` 参与 manifest 计算；
未实现时默认 {"id": 类全限定名}。

确定性契约：``fingerprint()`` 实现必须只返回 JSON 原生值
（str/int/float/bool/None/list/dict）。``manifest_sha1`` 的
``default=str`` 只是尽力兜底——对非 JSON 原生值（如 set）其序列化
结果（repr）不保证跨进程稳定，不能当作稳定性保证。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def manifest_sha1(manifest: Dict[str, Any]) -> str:
    """manifest 的稳定哈希（排序键 + 紧凑分隔，default=str 兜底）。"""
    blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def component_id(obj: Any) -> str:
    """组件类全限定名。"""
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def fingerprint_of(obj: Any) -> Dict[str, Any]:
    """fingerprint() 约定：组件实现了就用，否则默认 {"id": 类全限定名}。

    契约：``fingerprint()`` 必须只返回 JSON 原生值
    （str/int/float/bool/None/list/dict）；否则 manifest_sha1 的
    ``default=str`` 兜底不保证跨进程确定性。
    """
    fp = getattr(obj, "fingerprint", None)
    if callable(fp):
        try:
            result = fp()
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning("fingerprint() of %s failed: %s", component_id(obj), e)
    return {"id": component_id(obj)}


def compute_manifest(container, *, cached_tools: list,
                     call_llm=None) -> Dict[str, Any]:
    """从 DI 容器 + 装配缓存计算 manifest。

    Args:
        container: DIContainer。
        cached_tools: ToolRouter.list_tools() 的结果（_phase_init 缓存，
                      或 boot 探针用 build_tool_router 现算）。
        call_llm: LLM callable（best-effort 取 .model）。
    """
    from ...interfaces import (
        ContextAssembler, GuideProvider, MCPAdapter, SystemToolProvider,
    )

    manifest: Dict[str, Any] = {}
    for interface, key in ((ContextAssembler, "ContextAssembler"),
                           (GuideProvider, "GuideProvider"),
                           (SystemToolProvider, "SystemToolProvider"),
                           (MCPAdapter, "MCPAdapter")):
        try:
            component = container.resolve(interface)
        except Exception:
            continue
        fp = fingerprint_of(component)
        if key in ("SystemToolProvider", "MCPAdapter"):
            fp = {**fp, "tool_names": sorted(t.name for t in cached_tools)}
        manifest[key] = fp

    manifest["llm"] = {"model": getattr(call_llm, "model", None)}
    return manifest


@dataclass
class ManifestDiff:
    """分级比对结果。hard 非空 = 语义关键不一致（boot 硬失败，--force 降级）。"""
    hard: List[str] = field(default_factory=list)
    soft: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard


def diff_manifest(old: Optional[Dict[str, Any]], new: Dict[str, Any], *,
                  used_tool_names: Set[str]) -> ManifestDiff:
    """分级比对。old 为 None（无历史 manifest）时全部跳过。"""
    diff = ManifestDiff()
    if not old:
        return diff

    # 硬：ContextAssembler id
    old_asm = (old.get("ContextAssembler") or {}).get("id")
    new_asm = (new.get("ContextAssembler") or {}).get("id")
    if old_asm and new_asm and old_asm != new_asm:
        diff.hard.append(
            f"ContextAssembler 变化: {old_asm} → {new_asm}")

    # 硬：历史中实际用过的工具在当前工具集中缺失
    current_tools: Set[str] = set(
        (new.get("SystemToolProvider") or {}).get("tool_names", []))
    for missing in sorted(used_tool_names - current_tools):
        diff.hard.append(f"历史使用过的工具 '{missing}' 当前不可用")

    # 软：工具集增删（未被历史使用的）
    old_tools = set((old.get("SystemToolProvider") or {}).get("tool_names", []))
    if old_tools != current_tools and not diff.hard:
        diff.soft.append(
            f"工具集变化: {sorted(old_tools)} → {sorted(current_tools)}")

    # 软：GuideProvider 指纹
    if old.get("GuideProvider") != new.get("GuideProvider"):
        diff.soft.append("GuideProvider 内容变化（如 AGENTS.md 已修改）")

    # 软：llm model
    old_model = (old.get("llm") or {}).get("model")
    new_model = (new.get("llm") or {}).get("model")
    if old_model != new_model:
        diff.soft.append(f"LLM model 变化: {old_model} → {new_model}")

    return diff
