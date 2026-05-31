"""Test harness for ConfigLoader."""

import os
import tempfile

import pytest

from harness.core.config import ConfigLoader, ProfileConfig
from harness.core.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tmp_toml(content: str) -> str:
    """将内容写入临时 TOML 文件并返回路径。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False
    ) as f:
        f.write(content)
        return f.name


# ---------------------------------------------------------------------------
# ProfileConfig
# ---------------------------------------------------------------------------


class TestProfileConfig:
    """ProfileConfig 数据类测试。"""

    def test_create_with_required_fields(self):
        """使用必填字段创建实例。"""
        config = ProfileConfig(
            name="test",
            description="test config",
            template="coding",
            version="0.1.0",
        )
        assert config.name == "test"
        assert config.description == "test config"
        assert config.template == "coding"
        assert config.version == "0.1.0"

    def test_default_values(self):
        """默认字段值正确。"""
        config = ProfileConfig(
            name="test", description="", template="coding", version="0.1"
        )
        assert config.modules == {}
        assert config.raw == {}

    def test_field_types(self):
        """字段类型正确。"""
        config = ProfileConfig(
            name="test",
            description="desc",
            template="coding",
            version="0.1.0",
            modules={"input_adapter": True},
        )
        assert isinstance(config.name, str)
        assert isinstance(config.modules, dict)
        assert isinstance(config.modules["input_adapter"], bool)


# ---------------------------------------------------------------------------
# ConfigLoader.load()
# ---------------------------------------------------------------------------


class TestConfigLoaderLoad:
    """ConfigLoader.load() 测试。"""

    def test_load_valid_minimal_config(self):
        """加载最小合法 TOML。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("""
[meta]
name = "minimal"
template = "coding-assistant"
""")
        try:
            config = loader.load(path)
            assert config.name == "minimal"
            assert config.template == "coding-assistant"
            assert config.description == ""
            assert config.version == "0.1.0"
            assert config.modules == {}
        finally:
            os.unlink(path)

    def test_load_valid_full_config(self):
        """加载完整 TOML（含 modules）。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("""
[meta]
name = "full"
description = "Full config"
template = "coding-assistant"
version = "2.0.0"

[modules]
input_adapter = true
guide_provider = false
context_assembler = true
""")
        try:
            config = loader.load(path)
            assert config.name == "full"
            assert config.description == "Full config"
            assert config.version == "2.0.0"
            assert config.modules["input_adapter"] is True
            assert config.modules["guide_provider"] is False
            assert config.modules["context_assembler"] is True
        finally:
            os.unlink(path)

    def test_load_nonexistent_file_raises(self):
        """文件不存在抛出 ConfigNotFoundError。"""
        loader = ConfigLoader()
        with pytest.raises(ConfigNotFoundError) as exc_info:
            loader.load("/nonexistent/path/config.toml")
        assert "/nonexistent/path" in str(exc_info.value)

    def test_load_invalid_toml_raises(self):
        """TOML 语法错误抛出 ConfigParseError。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("[meta\nname = = invalid toml[[[")
        try:
            with pytest.raises(ConfigParseError):
                loader.load(path)
        finally:
            os.unlink(path)

    def test_load_preserves_raw(self):
        """load 保留原始 TOML 数据在 raw 字段中。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("""
[meta]
name = "test"
template = "coding"

[custom_section]
key = "value"
""")
        try:
            config = loader.load(path)
            assert "meta" in config.raw
            assert "custom_section" in config.raw
            assert config.raw["custom_section"]["key"] == "value"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# ConfigLoader.validate()
# ---------------------------------------------------------------------------


class TestConfigLoaderValidate:
    """ConfigLoader.validate() 测试。"""

    def test_validate_valid_config(self):
        """有效配置校验通过。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="test",
            description="",
            template="coding",
            version="0.1.0",
            modules={"input_adapter": True},
        )
        loader.validate(config)  # 不抛异常

    def test_validate_empty_name_raises(self):
        """空 name 抛出 ConfigValidationError。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="",
            description="",
            template="coding",
            version="0.1.0",
        )
        with pytest.raises(ConfigValidationError) as exc_info:
            loader.validate(config)
        assert "name" in str(exc_info.value).lower()

    def test_validate_empty_template_raises(self):
        """空 template 抛出 ConfigValidationError。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="test",
            description="",
            template="",
            version="0.1.0",
        )
        with pytest.raises(ConfigValidationError) as exc_info:
            loader.validate(config)
        assert "template" in str(exc_info.value).lower()

    def test_validate_non_bool_module_value_raises(self):
        """modules 中非 bool 值抛出 ConfigValidationError。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="test",
            description="",
            template="coding",
            version="0.1.0",
            modules={"input_adapter": "yes"},  # type: ignore
        )
        with pytest.raises(ConfigValidationError) as exc_info:
            loader.validate(config)
        assert "input_adapter" in str(exc_info.value)

    def test_validate_empty_modules_ok(self):
        """空 modules dict 不抛异常。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="test",
            description="",
            template="coding",
            version="0.1.0",
            modules={},
        )
        loader.validate(config)

    def test_validate_default_description_and_version(self):
        """默认 description 和 version 通过校验。"""
        loader = ConfigLoader()
        config = ProfileConfig(
            name="test",
            description="",
            template="coding",
            version="0.1.0",
        )
        loader.validate(config)  # 默认空值通过


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestConfigLoaderIntegration:
    """ConfigLoader 集成测试。"""

    def test_load_and_validate_roundtrip(self):
        """完整流程：写入 TOML → load → validate → 字段正确。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("""
[meta]
name = "my-agent"
description = "My coding agent"
template = "coding-assistant"
version = "0.1.0"

[modules]
input_adapter = true
guide_provider = false
""")
        try:
            config = loader.load(path)
            loader.validate(config)

            assert config.name == "my-agent"
            assert config.description == "My coding agent"
            assert config.template == "coding-assistant"
            assert config.version == "0.1.0"
            assert config.modules == {
                "input_adapter": True,
                "guide_provider": False,
            }
        finally:
            os.unlink(path)

    def test_missing_modules_section_is_ok(self):
        """modules 段缺失不报错，返回空 dict。"""
        loader = ConfigLoader()
        path = _write_tmp_toml("""
[meta]
name = "test"
template = "coding"
""")
        try:
            config = loader.load(path)
            loader.validate(config)
            assert config.modules == {}
        finally:
            os.unlink(path)

    def test_config_not_found_error_message_contains_path(self):
        """ConfigNotFoundError 消息包含具体路径。"""
        loader = ConfigLoader()
        with pytest.raises(ConfigNotFoundError) as exc_info:
            loader.load("/tmp/nonexistent_dir_xyz/profile.toml")
        assert "nonexistent_dir_xyz" in str(exc_info.value)
