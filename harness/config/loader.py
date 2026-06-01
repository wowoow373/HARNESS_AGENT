"""Harness Agent Template — 配置加载器。

读取 TOML 配置文件（profile.toml），解析为结构化配置对象。
框架内核不解释配置语义，仅做格式校验和字段提取。
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict

from ..core.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)

# Python 3.11+ 使用 tomllib，低版本回退到 tomli
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None  # type: ignore


@dataclass
class ProfileConfig:
    """从 profile.toml 解析出的结构化配置。

    Attributes:
        name: Agent 名称，必须为非空字符串。
        description: Agent 描述。
        template: 领域模板名称，必须为非空字符串。
        version: 版本号。
        modules: 模块启用/禁用标志，key 为模块名，value 为 bool。
        raw: 原始 TOML 数据（完整保留）。
    """
    name: str
    description: str
    template: str
    version: str
    modules: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class ConfigLoader:
    """TOML 配置文件加载器。

    职责：读取、解析、校验 profile.toml，返回 ProfileConfig。

    用法::

        loader = ConfigLoader()
        config = loader.load("./profile.toml")
        loader.validate(config)
    """

    def load(self, path: str) -> ProfileConfig:
        """加载并解析 TOML 配置文件。

        Args:
            path: profile.toml 文件路径。

        Returns:
            ProfileConfig: 结构化配置对象。

        Raises:
            ConfigNotFoundError: 文件不存在或不可读。
            ConfigParseError: TOML 语法错误或缺少 tomllib/tomli。
        """
        if tomllib is None:
            raise ConfigParseError(
                "tomllib (Python 3.11+) or tomli package is required to parse TOML files"
            )

        if not os.path.isfile(path):
            raise ConfigNotFoundError(path)

        try:
            with open(path, "rb") as f:
                raw: Dict[str, Any] = tomllib.load(f)
        except PermissionError as e:
            raise ConfigNotFoundError(f"{path}: {e}")
        except Exception as e:
            raise ConfigParseError(str(e))

        # 提取 [meta] 段
        meta = raw.get("meta", {})
        if not isinstance(meta, dict):
            raise ConfigValidationError("[meta] section must be a TOML table")

        # 提取 [modules] 段
        modules_raw = raw.get("modules", {})
        if modules_raw and isinstance(modules_raw, dict):
            modules: Dict[str, bool] = {}
            for key, value in modules_raw.items():
                if not isinstance(value, bool):
                    raise ConfigValidationError(
                        f"modules.{key} must be boolean, got {type(value).__name__}"
                    )
                modules[key] = value
        else:
            modules = {}

        return ProfileConfig(
            name=meta.get("name", ""),
            description=meta.get("description", ""),
            template=meta.get("template", ""),
            version=meta.get("version", "0.1.0"),
            modules=modules,
            raw=raw,
        )

    def validate(self, config: ProfileConfig) -> None:
        """校验配置完整性。

        校验规则：
        - meta.name 必须是非空字符串
        - meta.template 必须是非空字符串
        - modules 中的值必须是布尔类型（load 阶段已校验，此处补充检查）

        Args:
            config: 待校验的 ProfileConfig 实例。

        Raises:
            ConfigValidationError: 校验失败。
        """
        if not config.name or not isinstance(config.name, str):
            raise ConfigValidationError(
                "meta.name must be a non-empty string"
            )

        if not config.template or not isinstance(config.template, str):
            raise ConfigValidationError(
                "meta.template must be a non-empty string"
            )

        # modules 值类型校验
        for key, value in config.modules.items():
            if not isinstance(value, bool):
                raise ConfigValidationError(
                    f"modules.{key} must be boolean, got {type(value).__name__}"
                )
