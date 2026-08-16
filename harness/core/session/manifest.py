"""manifest —— 装配清单的稳定哈希与分级比对。

当前为最小存根：仅 manifest_sha1（T5 的 SessionLog.begin 惰性依赖）。
compute_manifest / diff_manifest / ManifestDiff 由 T9 补全。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def manifest_sha1(manifest: Dict[str, Any]) -> str:
    """manifest 的稳定哈希（排序键 + 紧凑分隔，default=str 兜底）。"""
    blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
