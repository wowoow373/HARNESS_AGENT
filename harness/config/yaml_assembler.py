"""Harness Agent Template — YAML 装配加载器。

从 ``harness.yaml`` 装配声明文件构建 DIContainer 和 Harness 实例。
YAML 装配是 Python API 的上层封装：覆盖 80% 的简单装配场景，
复杂场景仍可使用 Python API 直接编程。

用法::

    from harness.config.yaml_assembler import YamlAssembler

    assembler = YamlAssembler()
    harness = assembler.load("harness.yaml").assemble()
    harness.run()
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, List, Optional

import yaml

from ..core.container import DIContainer
from ..core.exceptions import ComponentNotRegisteredError
from ..interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MCPAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 接口短名 → 完整类型映射表
# ---------------------------------------------------------------------------

INTERFACE_REGISTRY: Dict[str, type] = {
    "InputAdapter": InputAdapter,
    "GuideProvider": GuideProvider,
    "MemoryBackend": MemoryBackend,
    "ContextAssembler": ContextAssembler,
    "Sensor": Sensor,
    "SystemToolProvider": SystemToolProvider,
    "MCPAdapter": MCPAdapter,
}

# 必须注册到 DI 容器的接口
REQUIRED_INTERFACES = frozenset({"InputAdapter"})


# ---------------------------------------------------------------------------
# 异常类
# ---------------------------------------------------------------------------


class AssemblyError(Exception):
    """YAML 装配过程中的错误基类。

    所有 YAML 装配相关的异常都继承自此。
    """


class UnknownInterfaceError(AssemblyError):
    """YAML 中引用了未知的 interface 短名。

    Args:
        short_name: 出现问题的短名。
        available: 可用的短名列表。
    """

    def __init__(self, short_name: str):
        available = sorted(INTERFACE_REGISTRY.keys())
        super().__init__(
            f"Unknown interface '{short_name}'. "
            f"Available: {available}"
        )
        self.short_name = short_name


class DependencyNotSatisfiedError(AssemblyError):
    """YAML ``inject`` 中引用的组件尚未注册。

    Args:
        interface_name: 被引用的接口短名。
        param_name: 注入的目标参数名。
    """

    def __init__(self, interface_name: str, param_name: str):
        super().__init__(
            f"Cannot inject '{interface_name}' for parameter "
            f"'{param_name}': component not yet registered. "
            f"Ensure the referenced component is declared before "
            f"the component that depends on it."
        )
        self.interface_name = interface_name
        self.param_name = param_name


class AssemblyValidationError(AssemblyError):
    """YAML 结构校验失败。

    Args:
        message: 描述校验失败的原因。
    """

    def __init__(self, message: str):
        super().__init__(message)


# ---------------------------------------------------------------------------
# YamlAssembler
# ---------------------------------------------------------------------------


class YamlAssembler:
    """从 YAML 装配声明构建 DIContainer 和 Harness。

    职责：

    1. 解析 ``harness.yaml`` → 结构化配置对象
    2. 按组件声明顺序动态 import 实现类
    3. 解析 ``inject`` 依赖（确保被依赖组件先注册）
    4. 构造组件实例并注册到 DIContainer
    5. 构建并返回 Harness 实例

    用法::

        assembler = YamlAssembler()
        harness = assembler.load("harness.yaml").assemble()
        harness.run()
    """

    def __init__(self):
        """初始化空的装配器。"""
        self._config: Optional[Dict[str, Any]] = None
        self._container: Optional[DIContainer] = None

    # ------------------------------------------------------------------
    # load — 加载并校验 YAML
    # ------------------------------------------------------------------

    def load(self, path: str) -> "YamlAssembler":
        """加载并校验 YAML 装配文件。

        Args:
            path: ``harness.yaml`` 文件路径。

        Returns:
            self，支持链式调用 ``.load().assemble()``。

        Raises:
            FileNotFoundError: 文件不存在或不可读。
            yaml.YAMLError: YAML 语法错误。
            AssemblyValidationError: 顶层结构校验失败。
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Assembly config not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw: Any = yaml.safe_load(f)
        except yaml.YAMLError:
            raise

        # 校验顶层结构
        if raw is None:
            raise AssemblyValidationError(
                "YAML file is empty. Expected a top-level 'harness' key."
            )
        if not isinstance(raw, dict):
            raise AssemblyValidationError(
                f"Top-level must be a mapping, got {type(raw).__name__}"
            )
        if "harness" not in raw:
            raise AssemblyValidationError(
                "Missing top-level 'harness' key in YAML assembly config"
            )

        harness_cfg = raw["harness"]
        if not isinstance(harness_cfg, dict):
            raise AssemblyValidationError(
                f"'harness' must be a mapping, got {type(harness_cfg).__name__}"
            )

        # 校验 components 段
        components = harness_cfg.get("components", [])
        if not isinstance(components, list):
            raise AssemblyValidationError(
                f"'harness.components' must be a list, got {type(components).__name__}"
            )

        # 校验 hooks 段
        hooks = harness_cfg.get("hooks", [])
        if not isinstance(hooks, list):
            raise AssemblyValidationError(
                f"'harness.hooks' must be a list, got {type(hooks).__name__}"
            )

        # 校验 llm 段（可选）
        llm_cfg = harness_cfg.get("llm")
        if llm_cfg is not None:
            if not isinstance(llm_cfg, dict):
                raise AssemblyValidationError(
                    f"'harness.llm' must be a mapping, got {type(llm_cfg).__name__}"
                )
            provider = llm_cfg.get("provider")
            if not provider or not isinstance(provider, str):
                raise AssemblyValidationError(
                    "'harness.llm.provider' must be a non-empty string"
                )

        self._config = harness_cfg
        logger.debug(f"Loaded assembly config from {path}")
        return self

    # ------------------------------------------------------------------
    # assemble — 构建 DI 容器和 Harness
    # ------------------------------------------------------------------

    def assemble(self):
        """按 YAML 声明构建 DIContainer 和 Harness 实例。

        Returns:
            Harness: 可运行的框架实例。

        Raises:
            AssemblyValidationError: 配置未加载或缺少必需组件。
            UnknownInterfaceError: YAML 中引用了未知接口。
            DependencyNotSatisfiedError: inject 引用的组件尚未注册。
            ImportError: 实现类 import 失败。
        """
        # 延迟导入避免循环依赖（harness.di → core → config → yaml_assembler → di）
        from ..di import Harness

        if self._config is None:
            raise AssemblyValidationError(
                "No configuration loaded. Call load() before assemble()."
            )

        container = DIContainer()
        self._container = container

        # 1. 解析 LLM 配置
        call_llm = self._build_llm_adapter()

        # 2. 按顺序注册组件
        components = self._config.get("components", [])
        has_input_adapter = False
        for entry in components:
            if not isinstance(entry, dict):
                raise AssemblyValidationError(
                    f"Each component entry must be a mapping, "
                    f"got {type(entry).__name__}"
                )
            interface_short = entry.get("interface")
            if not interface_short or not isinstance(interface_short, str):
                raise AssemblyValidationError(
                    "Each component entry must have a non-empty 'interface' field"
                )

            if interface_short not in INTERFACE_REGISTRY:
                raise UnknownInterfaceError(interface_short)

            impl_path = entry.get("implementation")
            if not impl_path or not isinstance(impl_path, str):
                raise AssemblyValidationError(
                    f"Component '{interface_short}' must have a "
                    f"non-empty 'implementation' field"
                )

            # 动态导入实现类
            impl_class = self._import_class(impl_path)

            # 解析构造函数参数
            params = entry.get("params")
            if params is not None and not isinstance(params, dict):
                raise AssemblyValidationError(
                    f"'params' for '{interface_short}' must be a mapping"
                )
            params = params or {}

            # 解析 inject 依赖
            inject = entry.get("inject")
            if inject is not None and not isinstance(inject, dict):
                raise AssemblyValidationError(
                    f"'inject' for '{interface_short}' must be a mapping"
                )
            inject = inject or {}
            injected_deps = self._resolve_inject(inject)

            # 构造实例
            try:
                instance = impl_class(**params, **injected_deps)
            except TypeError as e:
                raise AssemblyValidationError(
                    f"Failed to construct '{impl_path}' for interface "
                    f"'{interface_short}': {e}"
                ) from e

            # 注册到容器
            interface_type = INTERFACE_REGISTRY[interface_short]
            container.register(interface_type, instance)
            logger.debug(
                f"Registered {impl_path} as '{interface_short}'"
            )

            if interface_short == "InputAdapter":
                has_input_adapter = True

        # 校验必需组件
        if not has_input_adapter:
            raise AssemblyValidationError(
                f"Component 'InputAdapter' is required but was not "
                f"declared in the YAML assembly"
            )

        # 3. 构建 Harness
        harness = Harness.from_container(container, call_llm=call_llm)

        # 4. 注册 Hook
        hooks = self._config.get("hooks", [])
        for hook_entry in hooks:
            if not isinstance(hook_entry, dict):
                raise AssemblyValidationError(
                    f"Each hook entry must be a mapping, "
                    f"got {type(hook_entry).__name__}"
                )
            event = hook_entry.get("event")
            if not event or not isinstance(event, str):
                raise AssemblyValidationError(
                    "Each hook entry must have a non-empty 'event' field"
                )
            handler_path = hook_entry.get("handler")
            if not handler_path or not isinstance(handler_path, str):
                raise AssemblyValidationError(
                    "Each hook entry must have a non-empty 'handler' field"
                )

            handler = self._import_function(handler_path)
            harness.register_hook(event, handler)
            logger.debug(f"Registered hook '{handler_path}' for event '{event}'")

        self._container = None  # 释放引用
        return harness

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _import_class(full_path: str) -> type:
        """从完整模块路径动态加载类。

        Args:
            full_path: 格式 ``"module.path.ClassName"``。

        Returns:
            加载的类对象。

        Raises:
            ImportError: 模块路径无效或类不存在。
        """
        try:
            module_path, class_name = full_path.rsplit(".", 1)
        except ValueError:
            raise ImportError(
                f"Invalid implementation path '{full_path}': "
                f"expected format 'module.path.ClassName'"
            )
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            raise ImportError(
                f"Failed to import module '{module_path}' specified in "
                f"implementation path '{full_path}': {e}"
            ) from e
        try:
            return getattr(module, class_name)
        except AttributeError:
            raise ImportError(
                f"Class '{class_name}' not found in module '{module_path}'"
            )

    @staticmethod
    def _import_function(full_path: str):
        """从完整模块路径动态加载函数。

        Args:
            full_path: 格式 ``"module.path.function_name"``。

        Returns:
            加载的函数对象。

        Raises:
            ImportError: 模块路径无效或函数不存在。
        """
        try:
            module_path, func_name = full_path.rsplit(".", 1)
        except ValueError:
            raise ImportError(
                f"Invalid handler path '{full_path}': "
                f"expected format 'module.path.function_name'"
            )
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            raise ImportError(
                f"Failed to import module '{module_path}' specified in "
                f"handler path '{full_path}': {e}"
            ) from e
        try:
            return getattr(module, func_name)
        except AttributeError:
            raise ImportError(
                f"Function '{func_name}' not found in module '{module_path}'"
            )

    def _resolve_inject(self, inject: Dict[str, str]) -> Dict[str, Any]:
        """将 inject 短名映射解析为实际组件实例。

        Args:
            inject: ``{param_name: InterfaceShortName}`` 映射。

        Returns:
            ``{param_name: registered_instance}`` 映射。

        Raises:
            UnknownInterfaceError: inject 中引用了未知的接口短名。
            DependencyNotSatisfiedError: 引用的组件尚未注册。
        """
        resolved: Dict[str, Any] = {}
        for param_name, interface_short_name in inject.items():
            if interface_short_name not in INTERFACE_REGISTRY:
                raise UnknownInterfaceError(interface_short_name)
            interface_type = INTERFACE_REGISTRY[interface_short_name]
            try:
                resolved[param_name] = self._container.resolve(interface_type)
            except ComponentNotRegisteredError as e:
                raise DependencyNotSatisfiedError(
                    interface_short_name, param_name
                ) from e
        return resolved

    def _build_llm_adapter(self):
        """根据 ``harness.llm`` 配置段创建 LLM 适配器。

        行为：
        - 如果 ``llm`` 段不存在 → 返回 ``None``（LLM 调用被跳过）
        - 否则创建 ``MinimalLLMAdapter`` 实例

        环境变量（``LLM_BASE_URL``、``OPENAI_API_KEY``、``LLM_MODEL``）
        作为默认值，YAML 中的显式配置可以覆盖。

        Returns:
            MinimalLLMAdapter 实例，或 None。

        Raises:
            AssemblyValidationError: provider 字段无效。
        """
        llm_cfg = self._config.get("llm")
        if llm_cfg is None:
            logger.info(
                "No 'harness.llm' config — LLM calls will be skipped. "
                "This mode is intended for testing/debugging only."
            )
            return None

        # 延迟导入，避免未安装依赖时的 import 错误
        from ..adapters.llm_adapter import MinimalLLMAdapter

        provider = llm_cfg.get("provider", "openai")

        # 从环境变量读取默认值
        kwargs: Dict[str, Any] = {}
        env_base_url = os.environ.get("LLM_BASE_URL")
        env_api_key = os.environ.get("OPENAI_API_KEY")
        env_model = os.environ.get("LLM_MODEL")

        if env_base_url:
            kwargs["base_url"] = env_base_url
        if env_api_key:
            kwargs["api_key"] = env_api_key
        if env_model:
            kwargs["model"] = env_model

        # YAML 显式配置覆盖环境变量
        if "base_url" in llm_cfg:
            kwargs["base_url"] = llm_cfg["base_url"]
        if "api_key" in llm_cfg:
            # 支持 ${ENV_VAR} 语法
            api_key = llm_cfg["api_key"]
            if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
                env_var = api_key[2:-1]
                api_key = os.environ.get(env_var, "")
            kwargs["api_key"] = api_key
        if "model" in llm_cfg:
            kwargs["model"] = llm_cfg["model"]

        logger.info(
            f"Creating LLM adapter: provider={provider}, model={kwargs.get('model', 'default')}"
        )
        return MinimalLLMAdapter(**kwargs)
