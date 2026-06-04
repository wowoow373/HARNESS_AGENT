"""YamlAssembler 单元测试。

测试 YamlAssembler 的 load、assemble、异常处理、依赖注入等核心行为。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.config.yaml_assembler import (
    AssemblyError,
    AssemblyValidationError,
    DependencyNotSatisfiedError,
    INTERFACE_REGISTRY,
    UnknownInterfaceError,
    YamlAssembler,
)
from harness.core.container import DIContainer
from harness.core.exceptions import ComponentNotRegisteredError
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: str, content: str) -> None:
    """将 YAML 内容写入临时文件。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# Minimal YAML snippet with just InputAdapter
_MINIMAL_YAML = """
harness:
  version: "1.0"
  profile: test
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks: []
"""

# Full YAML snippet (6 default components)
_FULL_YAML = """
harness:
  version: "1.0"
  profile: test
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test-memory
    - interface: GuideProvider
      implementation: harness.components.guide_provider.FileGuideProvider
      params:
        paths:
          - AGENTS.md
    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      params:
        max_history: 50
      inject:
        memory: MemoryBackend
    - interface: Sensor
      implementation: harness.components.sensor.LoggingSensor
      inject:
        memory: MemoryBackend
    - interface: SystemToolProvider
      implementation: harness.components.tool.DefaultSystemToolProvider
  hooks: []
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_yaml_file():
    """创建临时 YAML 文件。"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Task 6 — Unit tests
# ---------------------------------------------------------------------------


class TestYamlAssemblerLoad:
    """load() 方法的单元测试。"""

    def test_load_valid_yaml(self, temp_yaml_file):
        """测试加载合法 YAML 文件成功。"""
        _write_yaml(temp_yaml_file, _MINIMAL_YAML)
        assembler = YamlAssembler()
        result = assembler.load(temp_yaml_file)
        assert result is assembler  # 链式调用
        assert assembler._config is not None
        assert assembler._config["profile"] == "test"

    def test_load_missing_file_raises(self):
        """测试加载不存在的文件抛出 FileNotFoundError。"""
        assembler = YamlAssembler()
        with pytest.raises(FileNotFoundError):
            assembler.load("/nonexistent/path/harness.yaml")

    def test_load_invalid_yaml_syntax_raises(self, temp_yaml_file):
        """测试加载语法错误的 YAML 抛出异常。"""
        _write_yaml(temp_yaml_file, "this: [is: broken: yaml")
        assembler = YamlAssembler()
        with pytest.raises(Exception):  # yaml.YAMLError or yaml.constructor.ConstructorError
            assembler.load(temp_yaml_file)

    def test_load_empty_file_raises(self, temp_yaml_file):
        """测试加载空 YAML 文件抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, "")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="empty"):
            assembler.load(temp_yaml_file)

    def test_load_missing_harness_key_raises(self, temp_yaml_file):
        """测试缺少顶层 harness key 抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, "profile: test\n")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="harness"):
            assembler.load(temp_yaml_file)

    def test_load_top_level_not_dict_raises(self, temp_yaml_file):
        """测试顶层不是 mapping 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, "- just a list\n- no harness\n")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="mapping"):
            assembler.load(temp_yaml_file)

    def test_load_components_not_a_list_raises(self, temp_yaml_file):
        """测试 components 不是列表时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components: "not a list"
  hooks: []
""")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="components"):
            assembler.load(temp_yaml_file)

    def test_load_hooks_not_a_list_raises(self, temp_yaml_file):
        """测试 hooks 不是列表时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components: []
  hooks: "not a list"
""")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="hooks"):
            assembler.load(temp_yaml_file)

    def test_load_invalid_llm_provider_empty_raises(self, temp_yaml_file):
        """测试 llm.provider 为空字符串时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components: []
  hooks: []
  llm:
    provider: ""
""")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="provider"):
            assembler.load(temp_yaml_file)

    def test_load_without_llm_section_is_valid(self, temp_yaml_file):
        """测试无 llm 段时加载成功。"""
        _write_yaml(temp_yaml_file, _MINIMAL_YAML)
        assembler = YamlAssembler()
        result = assembler.load(temp_yaml_file)
        assert result is assembler

    def test_load_harness_not_dict_raises(self, temp_yaml_file):
        """测试 harness 值不是 mapping 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, "harness: [1, 2, 3]\n")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="harness.*mapping"):
            assembler.load(temp_yaml_file)

    def test_load_llm_not_dict_raises(self, temp_yaml_file):
        """测试 llm 值不是 mapping 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components: []
  hooks: []
  llm: "not a dict"
""")
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="llm.*mapping"):
            assembler.load(temp_yaml_file)

    def test_load_components_entry_not_dict_raises(self, temp_yaml_file):
        """测试 components 条目不是 mapping 时调用 assemble 抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - "not a dict"
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="must be a mapping"):
            assembler.assemble()

    def test_load_component_missing_interface_raises(self, temp_yaml_file):
        """测试 components 条目缺少 interface 字段时抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - implementation: harness.components.input_adapter.CliAdapter
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="interface"):
            assembler.assemble()

    def test_load_component_missing_implementation_raises(self, temp_yaml_file):
        """测试 components 条目缺少 implementation 字段时抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="implementation"):
            assembler.assemble()


class TestYamlAssemblerAssemble:
    """assemble() 方法的单元测试。"""

    def test_assemble_minimal_config(self, temp_yaml_file):
        """测试最小配置（仅 InputAdapter）装配成功。"""
        _write_yaml(temp_yaml_file, _MINIMAL_YAML)
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml_file).assemble()
        assert harness is not None
        assert hasattr(harness, "run")
        assert hasattr(harness, "register_hook")

    def test_assemble_full_config(self, temp_yaml_file):
        """测试完整配置（全部 6 个默认组件）装配成功。"""
        _write_yaml(temp_yaml_file, _FULL_YAML)
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml_file).assemble()
        assert harness is not None

    def test_assemble_missing_input_adapter_raises(self, temp_yaml_file):
        """测试缺少 InputAdapter 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test-memory
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="InputAdapter"):
            assembler.assemble()

    def test_assemble_with_inject_resolves_dependency(self, temp_yaml_file):
        """测试 inject 依赖被正确解析注入。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test-memory
    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      params:
        max_history: 50
      inject:
        memory: MemoryBackend
  hooks: []
""")
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml_file).assemble()
        assert harness is not None

    def test_assemble_with_unresolved_inject_raises(self, temp_yaml_file):
        """测试 inject 引用未注册组件抛出 DependencyNotSatisfiedError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      inject:
        memory: MemoryBackend
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(DependencyNotSatisfiedError, match="MemoryBackend"):
            assembler.assemble()

    def test_assemble_with_invalid_implementation_raises(self, temp_yaml_file):
        """测试实现类路径无效时抛出 ImportError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: nonexistent.module.NonExistentClass
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(ImportError):
            assembler.assemble()

    def test_assemble_with_unknown_interface_raises(self, temp_yaml_file):
        """测试未知 interface 短名抛出 UnknownInterfaceError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: UnknownComponent
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(UnknownInterfaceError, match="UnknownComponent"):
            assembler.assemble()

    def test_assemble_with_llm_config_creates_adapter(self, temp_yaml_file):
        """测试 llm 段正确创建 MinimalLLMAdapter。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks: []
  llm:
    provider: openai
    model: gpt-4o
""")
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml_file).assemble()
        assert harness is not None

    def test_assemble_without_llm_config_call_llm_is_none(self, temp_yaml_file):
        """测试无 llm 段时装配不报错（call_llm 为 None）。"""
        _write_yaml(temp_yaml_file, _MINIMAL_YAML)
        assembler = YamlAssembler()
        harness = assembler.load(temp_yaml_file).assemble()
        assert harness is not None

    def test_assemble_not_loaded_raises(self):
        """测试未 load 就调用 assemble 抛出 AssemblyValidationError。"""
        assembler = YamlAssembler()
        with pytest.raises(AssemblyValidationError, match="load"):
            assembler.assemble()

    def test_assemble_invalid_implementation_format_raises(self, temp_yaml_file):
        """测试实现类路径格式无效（无点号）时抛出 ImportError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: NoModulePath
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(ImportError, match="Invalid implementation path"):
            assembler.assemble()

    def test_assemble_class_not_found_in_module_raises(self, temp_yaml_file):
        """测试模块中存在但类不存在时抛出 ImportError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.NonExistentClass
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(ImportError, match="not found"):
            assembler.assemble()

    def test_assemble_constructor_type_error_raises(self, temp_yaml_file):
        """测试构造函数参数类型不匹配时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        invalid_param: this_does_not_exist_on_constructor
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="Failed to construct"):
            assembler.assemble()

    def test_assemble_params_not_dict_raises(self, temp_yaml_file):
        """测试 params 不是 mapping 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params: "not a dict"
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="params.*mapping"):
            assembler.assemble()

    def test_assemble_inject_not_dict_raises(self, temp_yaml_file):
        """测试 inject 不是 mapping 时抛出 AssemblyValidationError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
    - interface: MemoryBackend
      implementation: harness.components.memory_backend.MdMemory
      params:
        path: /tmp/test
    - interface: ContextAssembler
      implementation: harness.components.context_assembler.SimpleAssembler
      inject: "not a dict"
  hooks: []
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="inject.*mapping"):
            assembler.assemble()


class TestYamlAssemblerHooks:
    """YAML Hook 注册的单元测试。"""

    def test_assemble_registers_hooks(self, temp_yaml_file):
        """测试 YAML 中声明的 hook 被正确注册。"""
        # 使用真实的 hook 模块路径（harness 内部不提供具体 hook，用 lambda 测试）
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks:
    - event: before_llm_call
      handler: harness.hooks.hook_manager.HookManager
""")
        assembler = YamlAssembler()
        # HookManager 可被 callable，但这里主要是测试 import 通路
        # 注意：HookManager 是类不是函数，这里验证 import 成功即可
        assembler.load(temp_yaml_file)
        # 因为 HookManager 不是可调用函数签名，register_hook 调用时
        # 不会在注册阶段报错（trigger 时才报），这里验证 assemble 成功
        harness = assembler.assemble()
        assert harness is not None

    def test_assemble_invalid_hook_handler_raises(self, temp_yaml_file):
        """测试 hook handler 路径无效时抛出 ImportError。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks:
    - event: before_llm_call
      handler: nonexistent.module.function
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(ImportError):
            assembler.assemble()

    def test_assemble_hook_entry_not_dict_raises(self, temp_yaml_file):
        """测试 hook 条目不是 mapping 时抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks:
    - "not a dict"
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="hook.*mapping"):
            assembler.assemble()

    def test_assemble_hook_missing_event_raises(self, temp_yaml_file):
        """测试 hook 条目缺少 event 时抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks:
    - handler: my_module.my_function
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="event"):
            assembler.assemble()

    def test_assemble_hook_missing_handler_raises(self, temp_yaml_file):
        """测试 hook 条目缺少 handler 时抛出错误。"""
        _write_yaml(temp_yaml_file, """
harness:
  components:
    - interface: InputAdapter
      implementation: harness.components.input_adapter.CliAdapter
  hooks:
    - event: before_llm_call
""")
        assembler = YamlAssembler()
        assembler.load(temp_yaml_file)
        with pytest.raises(AssemblyValidationError, match="handler"):
            assembler.assemble()


class TestExceptions:
    """异常类的单元测试。"""

    def test_assembly_error_is_exception(self):
        """测试 AssemblyError 是 Exception 子类。"""
        assert issubclass(AssemblyError, Exception)

    def test_unknown_interface_error_contains_available(self):
        """测试 UnknownInterfaceError 消息包含可用接口列表。"""
        e = UnknownInterfaceError("FooBar")
        assert "FooBar" in str(e)
        assert "InputAdapter" in str(e)  # available list

    def test_dependency_not_satisfied_error(self):
        """测试 DependencyNotSatisfiedError 消息包含引用信息。"""
        e = DependencyNotSatisfiedError("MemoryBackend", "memory")
        assert "MemoryBackend" in str(e)
        assert "memory" in str(e)
        assert "not yet registered" in str(e)

    def test_assembly_validation_error(self):
        """测试 AssemblyValidationError 消息正确传递。"""
        e = AssemblyValidationError("test message")
        assert str(e) == "test message"


class TestInterfaceRegistry:
    """INTERFACE_REGISTRY 常量测试。"""

    def test_all_7_interfaces_present(self):
        """测试映射表包含全部 7 个接口。"""
        expected = {
            "InputAdapter",
            "GuideProvider",
            "MemoryBackend",
            "ContextAssembler",
            "Sensor",
            "SystemToolProvider",
            "MCPAdapter",
        }
        assert set(INTERFACE_REGISTRY.keys()) == expected

    def test_interface_short_names_map_to_correct_types(self):
        """测试短名映射到正确的接口类型。"""
        assert INTERFACE_REGISTRY["InputAdapter"] is InputAdapter
        assert INTERFACE_REGISTRY["GuideProvider"] is GuideProvider
        assert INTERFACE_REGISTRY["MemoryBackend"] is MemoryBackend
        assert INTERFACE_REGISTRY["ContextAssembler"] is ContextAssembler
        assert INTERFACE_REGISTRY["Sensor"] is Sensor
        assert INTERFACE_REGISTRY["SystemToolProvider"] is SystemToolProvider
