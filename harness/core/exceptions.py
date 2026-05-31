"""Harness Agent Template — 异常体系。

所有框架异常继承自 HarnessError，用户可单一 ``except HarnessError`` 捕获所有框架错误。
每层异常有明确的语义前缀（Config*、Container*、Orchestrator*）。
"""


class HarnessError(Exception):
    """Harness 框架所有异常的基类。

    所有框架异常都继承自该类，用户可通过 ``except HarnessError`` 统一捕获。
    """

    def __init__(self, message: str):
        super().__init__(message)


# --- 配置相关异常 ---


class ConfigError(HarnessError):
    """配置相关异常的基类。

    所有与配置文件加载、解析、校验相关的异常都继承自该类。
    """

    def __init__(self, message: str):
        super().__init__(f"[CONFIG] {message}")


class ConfigNotFoundError(ConfigError):
    """配置文件不存在或不可读时抛出。

    使用场景：指定的 profile.toml 文件路径不存在或权限不足。
    """

    def __init__(self, message: str):
        super().__init__(f"File not found: {message}")


class ConfigParseError(ConfigError):
    """配置文件解析失败时抛出（如 TOML 语法错误）。

    使用场景：TOML 文件存在但内容格式错误，无法被 tomllib 解析。
    """

    def __init__(self, message: str):
        super().__init__(f"Parse error: {message}")


class ConfigValidationError(ConfigError):
    """配置校验失败时抛出（如必需字段缺失或类型错误）。

    使用场景：TOML 解析成功但 [meta] 段缺失、name 为空等。
    """

    def __init__(self, message: str):
        super().__init__(f"Validation failed: {message}")


# --- DI 容器相关异常 ---


class ContainerError(HarnessError):
    """DI 容器相关异常的基类。

    所有与依赖注入容器操作相关的异常都继承自该类。
    """

    def __init__(self, message: str):
        super().__init__(f"[CONTAINER] {message}")


class DuplicateRegistrationError(ContainerError):
    """尝试重复注册同一接口类型时抛出。

    使用场景：对同一个 interface type 调用了多次 register()。
    """

    def __init__(self, message: str):
        super().__init__(f"Duplicate registration: {message}")


class ComponentNotRegisteredError(ContainerError):
    """请求的接口类型未注册时抛出。

    使用场景：resolve() 接口类型在容器中不存在。
    """

    def __init__(self, message: str):
        super().__init__(f"Component not registered: {message}")


# --- 编排相关异常 ---


class OrchestratorError(HarnessError):
    """编排流程中的错误。

    使用场景：生命周期编排过程中遇到的运行时错误，如 LLM API 调用失败。
    """

    def __init__(self, message: str):
        super().__init__(f"[ORCHESTRATOR] {message}")
