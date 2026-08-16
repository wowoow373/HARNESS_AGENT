"""SessionConfig — 持久化唯一配置面（设计决策 D1）。

只有两个字段：sessions.root + sessions.enabled。
从 harness.yaml 的 sessions: 节加载；文件缺失/节缺失/解析失败均回退默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionConfig:
    """持久化配置。

    Attributes:
        root: 会话存储根目录（sessions/<conv_id>/ 的父目录）。
        enabled: 是否开启持久化。False 时 SessionLog 纯内存运行（零落盘）。
    """
    root: str = "./sessions"
    enabled: bool = True


def load_session_config(yaml_path: Optional[str] = None) -> SessionConfig:
    """从 harness.yaml 的 sessions: 节加载配置。

    Args:
        yaml_path: harness.yaml 路径；None 或文件不存在时返回默认配置。

    Returns:
        SessionConfig。任何解析失败都回退默认值（配置面永不阻断启动）。
    """
    cfg = SessionConfig()
    if not yaml_path or not os.path.isfile(yaml_path):
        return cfg
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    section = data.get("sessions") or {}
    if not isinstance(section, dict):
        return cfg
    if "root" in section:
        cfg.root = str(section["root"])
    if "enabled" in section:
        cfg.enabled = bool(section["enabled"])
    return cfg
