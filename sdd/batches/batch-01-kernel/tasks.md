# batch-01 — MVP 任务分解

> **执行顺序**：从上到下依次执行。每个 Task 完成后通过验证检查点再进入下一个。
>
> **约定**：每个步骤标注 `[V]` = 可验证步骤，含具体的验证方法。

---

## Task 1：项目骨架搭建

**目标**：创建 batch-01 所需的最小目录结构和空文件。

**预估**：1 个步骤，5 分钟。

---

### 步骤 1.1：创建目录与占位文件 `[V]`

**操作**：

```bash
# 在仓库根目录下创建
mkdir -p harness/core
touch harness/__init__.py
touch harness/core/__init__.py
touch harness/core/exceptions.py
touch harness/core/container.py
touch harness/config/loader.py
touch harness/core/orchestrator.py
touch harness/adapters/llm_adapter.py
touch harness/di.py
mkdir -p tests
touch tests/__init__.py
touch tests/test_container.py
touch tests/test_config.py
touch tests/test_orchestrator.py
touch tests/test_llm_adapter.py
touch tests/test_exceptions.py
```

**验证方法**：

```bash
# 验证目录结构
ls -la harness/
ls -la harness/core/
ls -la tests/
# 预期：所有文件存在，包括 harness/__init__.py 和 harness/di.py
```

**检查点**：`find harness -type f | sort` 输出包含所有预期文件。

---

## Task 2：异常体系实现

**目标**：实现 `harness/core/exceptions.py` — 框架所有异常的基类和核心异常类。

**依赖**：Task 1

**预估**：2 个步骤，15 分钟。

---

### 步骤 2.1：定义异常层次结构 `[V]`

**操作**：在 `harness/core/exceptions.py` 中实现以下异常类：

```
HarnessError(Exception)                    # 框架所有异常的基类
├── ConfigError(HarnessError)              # 配置相关异常
│   ├── ConfigNotFoundError(ConfigError)   # 配置文件不存在
│   ├── ConfigParseError(ConfigError)      # TOML 语法错误
│   └── ConfigValidationError(ConfigError) # 配置字段校验失败
├── ContainerError(HarnessError)           # DI 容器异常
│   ├── DuplicateRegistrationError(ContainerError)   # 重复注册
│   └── ComponentNotRegisteredError(ContainerError)  # 组件未注册
└── OrchestratorError(HarnessError)        # 编排异常
```

**要求**：
- 每个异常类有 docstring 说明使用场景
- 构造函数接受 `message: str` 参数并传递给父类
- 异常消息格式为 `"[PREFIX] description"`，如 `"[CONFIG] File not found: /path/to/file"`

**验证方法**：

```python
# 交互式验证（python -c 或 pytest）
from harness.core.exceptions import (
    HarnessError, ConfigError, ConfigNotFoundError,
    DuplicateRegistrationError, ComponentNotRegisteredError,
    OrchestratorError,
)

# 1. 验证继承关系
assert issubclass(ConfigNotFoundError, ConfigError)
assert issubclass(ConfigNotFoundError, HarnessError)
assert issubclass(DuplicateRegistrationError, ContainerError)
assert issubclass(OrchestratorError, HarnessError)

# 2. 验证异常消息
e = ComponentNotRegisteredError("InputAdapter")
assert "InputAdapter" in str(e)

# 3. 验证可被 HarnessError 统一捕获
try:
    raise ConfigNotFoundError("/tmp/missing.toml")
except HarnessError:
    pass  # 应该被捕获
else:
    assert False, "Should have been caught"

# 4. 验证异常实例属性
e = DuplicateRegistrationError("InputAdapter")
assert isinstance(e, Exception)
```

**检查点**：所有继承关系和消息传递正确，`except HarnessError` 可捕获所有子类异常。

---

### 步骤 2.2：更新 harness/core/__init__.py 导出 `[V]`

**操作**：在 `harness/core/__init__.py` 中导出所有异常类。

```python
from .exceptions import (
    HarnessError,
    ConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    ContainerError,
    DuplicateRegistrationError,
    ComponentNotRegisteredError,
    OrchestratorError,
)
```

**验证方法**：

```python
from harness.core import (
    HarnessError, ConfigNotFoundError,
    DuplicateRegistrationError, OrchestratorError,
)
# 所有 import 不报错即通过
```

**检查点**：所有异常类可从 `harness.core` 直接 import。

---

## Task 3：DIContainer 实现

**目标**：实现 `harness/core/container.py` — 预构造实例注册模式的 DI 容器。

**依赖**：Task 2

**预估**：4 个步骤，30 分钟。

---

### 步骤 3.1：实现 `register()` 方法 `[V]`

**操作**：实现 `DIContainer.register(interface, instance)` 方法。

核心逻辑：
1. 校验 `interface` 是否为 `type`（非 type 则 raise `TypeError`）
2. 校验 `instance` 是否为 `None`（None 则 raise `ValueError`，消息包含接口名）
3. 检查 `interface` 是否已在 `_registry` 中（已存在则 raise `DuplicateRegistrationError`）
4. 将 `(interface, instance)` 存入 `_registry`

**验证方法**：

```python
from harness.core.container import DIContainer
from harness.core.exceptions import DuplicateRegistrationError

container = DIContainer()

# 创建测试用的"接口"和"实例"
class IFoo: pass
class FooImpl: pass

foo = FooImpl()

# 1. 正常注册
container.register(IFoo, foo)
assert container.is_registered(IFoo) == True

# 2. 重复注册抛异常
try:
    container.register(IFoo, FooImpl())
    assert False, "Should raise DuplicateRegistrationError"
except DuplicateRegistrationError as e:
    assert "IFoo" in str(e)

# 3. 注册 None 抛 ValueError
try:
    container.register(IFoo, None)
    assert False, "Should raise ValueError"
except ValueError as e:
    assert "None" in str(e) or "null" in str(e).lower()

# 4. 注册非 type 抛 TypeError
try:
    container.register("not_a_type", foo)
    assert False, "Should raise TypeError"
except TypeError:
    pass

# 5. 不同接口可独立注册
class IBar: pass
class BarImpl: pass
bar = BarImpl()
container.register(IBar, bar)
assert container.is_registered(IBar) == True
assert container.is_registered(IFoo) == True
```

**检查点**：`register()` 满足全部 5 个验证场景。

---

### 步骤 3.2：实现 `resolve()` 方法 `[V]`

**操作**：实现 `DIContainer.resolve(interface)` 方法。

核心逻辑：
1. 校验 `interface` 是否为 `type`（非 type 则 raise `TypeError`）
2. 检查 `interface` 是否在 `_registry` 中（不存在则 raise `ComponentNotRegisteredError`）
3. 返回已注册的实例

**验证方法**：

```python
container = DIContainer()

class IFoo: pass
class IFooImpl: pass

foo = IFooImpl()
container.register(IFoo, foo)

# 1. 正常解析
resolved = container.resolve(IFoo)
assert resolved is foo  # 返回的是同一个实例

# 2. 未注册类型抛异常
from harness.core.exceptions import ComponentNotRegisteredError
class IBar: pass
try:
    container.resolve(IBar)
    assert False, "Should raise ComponentNotRegisteredError"
except ComponentNotRegisteredError as e:
    assert "IBar" in str(e)

# 3. 非 type 参数抛 TypeError
try:
    container.resolve("not_a_type")
    assert False, "Should raise TypeError"
except TypeError:
    pass
```

**检查点**：`resolve()` 返回正确实例，缺失组件时异常消息包含接口名。

---

### 步骤 3.3：实现辅助方法 `[V]`

**操作**：实现 `is_registered()` 和 `list_registered()` 方法。

- `is_registered(interface)` → bool
- `list_registered()` → Dict[type, Any]（返回副本，不是内部引用）

**验证方法**：

```python
container = DIContainer()

# 1. 初始状态为空
assert container.is_registered(object) == False
assert container.list_registered() == {}

# 2. 注册后状态更新
class IFoo: pass
foo = object()
container.register(IFoo, foo)
assert container.is_registered(IFoo) == True
assert IFoo in container.list_registered()
assert container.list_registered()[IFoo] is foo

# 3. list_registered 返回副本（修改不影响内部状态）
reg = container.list_registered()
reg.clear()
assert container.is_registered(IFoo) == True  # 内部状态未被修改
```

**检查点**：辅助方法行为正确，`list_registered()` 返回副本。

---

### 步骤 3.4：DIContainer 完整集成验证 `[V]`

**操作**：确保所有方法协同工作。

**验证方法**：

```python
container = DIContainer()

# 完整场景：注册多个接口 → 解析 → 检查
class IA: pass
class IB: pass
class AImpl: pass
class BImpl:
    def __init__(self, a):
        self.a = a

a = AImpl()
b = BImpl(a)

container.register(IA, a)
container.register(IB, b)

# 验证共享实例
assert container.resolve(IA) is a
assert container.resolve(IB) is b
assert container.resolve(IB).a is container.resolve(IA)

# 验证注册表完整性
reg = container.list_registered()
assert len(reg) == 2
assert IA in reg and IB in reg

# 验证错误路径
from harness.core.exceptions import (
    DuplicateRegistrationError,
    ComponentNotRegisteredError
)
import pytest
with pytest.raises(DuplicateRegistrationError):
    container.register(IA, AImpl())
with pytest.raises(ComponentNotRegisteredError):
    container.resolve(str)
```

**检查点**：多组件注册/解析正确，错误路径覆盖完整。

---

## Task 4：ConfigLoader 实现

**目标**：实现 `harness/config/loader.py` — TOML 配置文件加载、解析与校验。

**依赖**：Task 2

**预估**：4 个步骤，30 分钟。

---

### 步骤 4.1：实现 ProfileConfig 数据类 `[V]`

**操作**：在 `harness/config/loader.py` 中定义 `ProfileConfig` dataclass。

```python
from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class ProfileConfig:
    name: str
    description: str
    template: str
    version: str
    modules: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
```

**要求**：
- 所有字段有类型标注
- 类有 docstring 描述每个字段含义

**验证方法**：

```python
from harness.config.loader import ProfileConfig

# 1. 创建实例
config = ProfileConfig(
    name="test",
    description="test config",
    template="coding",
    version="0.1.0",
    modules={"input_adapter": True},
)

# 2. 默认字段
assert config.raw == {}

# 3. 字段访问
assert config.name == "test"
assert config.template == "coding"
assert config.modules["input_adapter"] == True
```

**检查点**：`ProfileConfig` dataclass 创建和字段访问正常。

---

### 步骤 4.2：实现 `ConfigLoader.load()` — 文件读取与 TOML 解析 `[V]`

**操作**：实现 `ConfigLoader.load(path)` 方法。

核心逻辑：
1. 检查文件是否存在且可读（不存在则 raise `ConfigNotFoundError`，消息包含路径）
2. 使用 `tomllib`（Python 3.11+）或 `tomli`（降级兼容）解析 TOML
3. 解析失败则 raise `ConfigParseError`，包装原始异常
4. 从解析结果中提取字段并构造 `ProfileConfig`

**Python 版本兼容策略**：
```python
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # 需要 pip install tomli
```

**验证方法**：

```python
import tempfile, os
from harness.config.loader import ConfigLoader, ProfileConfig

loader = ConfigLoader()

# 1. 正常加载
toml_content = """
[meta]
name = "my-agent"
description = "My coding agent"
template = "coding-assistant"
version = "0.1.0"

[modules]
input_adapter = true
guide_provider = false
"""
with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
    f.write(toml_content)
    tmp_path = f.name

try:
    config = loader.load(tmp_path)
    assert isinstance(config, ProfileConfig)
    assert config.name == "my-agent"
    assert config.template == "coding-assistant"
    assert config.modules["input_adapter"] == True
    assert config.modules["guide_provider"] == False
finally:
    os.unlink(tmp_path)

# 2. 文件不存在抛 ConfigNotFoundError
from harness.core.exceptions import ConfigNotFoundError
try:
    loader.load("/nonexistent/path/config.toml")
    assert False, "Should raise ConfigNotFoundError"
except ConfigNotFoundError as e:
    assert "nonexistent" in str(e)

# 3. TOML 语法错误抛 ConfigParseError
from harness.core.exceptions import ConfigParseError
with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
    f.write("[meta\nname = invalid toml[[")
    tmp_path2 = f.name
try:
    loader.load(tmp_path2)
    assert False, "Should raise ConfigParseError"
except ConfigParseError:
    pass
finally:
    os.unlink(tmp_path2)
```

**检查点**：正常加载返回正确的 `ProfileConfig`，三种错误路径均有正确的异常。

---

### 步骤 4.3：实现 `ConfigLoader.validate()` — 配置校验 `[V]`

**操作**：实现 `ConfigLoader.validate(config)` 方法。

校验规则（按优先级）：
1. `[meta]` 段必须存在 → `ConfigValidationError("Missing [meta] section")`
2. `meta.name` 必须是非空字符串 → `ConfigValidationError("meta.name must be a non-empty string")`
3. `meta.template` 必须是非空字符串 → `ConfigValidationError("meta.template must be a non-empty string")`
4. `meta.version` 缺失时默认 `"0.1.0"`，不报错
5. `meta.description` 缺失时默认 `""`，不报错
6. `[modules]` 段缺失时 `modules` 字段返回空 dict，不报错
7. `modules` 中任何值非 bool 时 → `ConfigValidationError("modules.{key} must be boolean")`

**验证方法**：

```python
from harness.config.loader import ConfigLoader, ProfileConfig
from harness.core.exceptions import ConfigValidationError

loader = ConfigLoader()

# 1. 正常配置校验通过
config = ProfileConfig(
    name="test", description="", template="coding", version="0.1.0",
    modules={"input_adapter": True}
)
loader.validate(config)  # 不抛异常

# 2. name 为空字符串
try:
    loader.validate(ProfileConfig(
        name="", description="", template="coding", version="0.1.0"
    ))
    assert False
except ConfigValidationError as e:
    assert "name" in str(e).lower()

# 3. template 为空字符串
try:
    loader.validate(ProfileConfig(
        name="test", description="", template="", version="0.1.0"
    ))
    assert False
except ConfigValidationError as e:
    assert "template" in str(e).lower()

# 4. modules 值非 bool
try:
    loader.validate(ProfileConfig(
        name="test", description="", template="coding", version="0.1.0",
        modules={"input_adapter": "yes"}  # 应为 bool
    ))
    assert False
except ConfigValidationError as e:
    assert "input_adapter" in str(e)

# 5. 空 modules 不报错
loader.validate(ProfileConfig(
    name="test", description="", template="coding", version="0.1.0",
    modules={}
))
```

**检查点**：5 个校验场景全部通过。

---

### 步骤 4.4：ConfigLoader 完整集成验证 `[V]`

**操作**：验证 `load()` + `validate()` 的完整工作流。

**验证方法**：

```python
import tempfile, os
from harness.config.loader import ConfigLoader

loader = ConfigLoader()

# 最小合法 TOML
minimal_toml = """
[meta]
name = "minimal"
template = "coding-assistant"
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
    f.write(minimal_toml)
    tmp_path = f.name

try:
    config = loader.load(tmp_path)
    loader.validate(config)
    assert config.name == "minimal"
    assert config.description == ""  # 默认值
    assert config.version == "0.1.0"  # 默认值
    assert config.modules == {}
finally:
    os.unlink(tmp_path)

# 正常完整 TOML
full_toml = """
[meta]
name = "full"
description = "Full config"
template = "coding-assistant"
version = "2.0.0"

[modules]
input_adapter = true
guide_provider = false
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
    f.write(full_toml)
    tmp_path2 = f.name

try:
    config = loader.load(tmp_path2)
    loader.validate(config)
    assert config.version == "2.0.0"
    assert config.modules["input_adapter"] == True
    assert config.modules["guide_provider"] == False
finally:
    os.unlink(tmp_path2)
```

**检查点**：最小和完整 TOML 都能正确加载和校验。

---

## Task 5：LifecycleOrchestrator 实现

**目标**：实现 `harness/core/orchestrator.py` — 三阶段生命周期编排器。

**依赖**：Task 3 (DIContainer)

**预估**：6 个步骤，60 分钟。

---

### 步骤 5.1：定义编排器所需的最小数据结构 `[V]`

**操作**：在 `harness/core/types.py` 中定义编排器内部使用的轻量数据结构。

由于 batch-02 才定义正式的大包对象，batch-01 编排器使用**最小化的内部数据结构**来表示各阶段的数据流。这些是临时结构，batch-02-1 实现后会被正式类型替换。

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json

@dataclass
class _MinimalUserRequest:
    """最小化的用户请求表示（batch-02-1 替换为 interfaces/types.py 中的正式类型）"""
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class _MinimalGuidesBundle:
    """最小化的 GuidesBundle 表示"""
    identity: str = ""
    rules: List[str] = field(default_factory=list)

@dataclass
class _MinimalAssemblyContext:
    """最小化的 AssemblyContext 表示"""
    user_request: Optional[_MinimalUserRequest] = None
    guides: Optional[_MinimalGuidesBundle] = None
    history: List[Dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class _MinimalToolCall:
    """最小化的 ToolCall 表示。遵循 OpenAI tool call 格式。

    Attributes:
        id: tool call 唯一标识（如 "call_abc123"）
        name: 函数名
        arguments: JSON 编码的参数字符串
    """
    id: str
    name: str
    arguments: str  # JSON string, 执行时由编排器 parse 为 dict

    def parse_arguments(self) -> Dict[str, Any]:
        """将 arguments JSON string 解析为 dict。"""
        return json.loads(self.arguments)

@dataclass
class _MinimalResponse:
    """最小化的 LLM Response 表示。

    设计要求：
    - text 和 tool_uses 可同时非空（遵循架构"LLM 单次响应可同时包含两者"）
    - tool_uses 为空列表时仅表示纯文本响应
    - text 为 None 且 tool_uses 非空时表示纯工具调用响应
    """
    text: Optional[str] = None
    tool_uses: List[_MinimalToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"

@dataclass
class _MinimalTrajectory:
    """最小化的 Trajectory 表示"""
    history: List[Dict] = field(default_factory=list)
    tool_call_records: List[Dict] = field(default_factory=list)
    final_output: str = ""
    execution_time: float = 0.0
```

**验证方法**：

```python
from harness.core.orchestrator import (
    _MinimalUserRequest,
    _MinimalGuidesBundle,
    _MinimalAssemblyContext,
    _MinimalToolCall,
    _MinimalResponse,
    _MinimalTrajectory,
)

# 1. 验证所有 dataclass 可正常创建
req = _MinimalUserRequest(text="hello")
assert req.text == "hello"

guides = _MinimalGuidesBundle(identity="test bot")
assert guides.identity == "test bot"

ctx = _MinimalAssemblyContext(user_request=req, guides=guides)
assert ctx.user_request is req

# 2. _MinimalToolCall 创建与 JSON 解析
tc = _MinimalToolCall(id="call_1", name="read", arguments='{"path": "/tmp/x"}')
assert tc.id == "call_1"
assert tc.name == "read"
assert tc.arguments == '{"path": "/tmp/x"}'
parsed = tc.parse_arguments()
assert parsed == {"path": "/tmp/x"}
assert isinstance(parsed, dict)

# 3. _MinimalResponse 纯文本
resp = _MinimalResponse(text="hi there", stop_reason="end_turn")
assert resp.text == "hi there"
assert resp.tool_uses == []

# 4. _MinimalResponse 纯 tool_use
resp2 = _MinimalResponse(
    tool_uses=[_MinimalToolCall(id="c1", name="read", arguments='{"path":"/x"}')],
    stop_reason="tool_use"
)
assert resp2.text is None
assert len(resp2.tool_uses) == 1
assert resp2.tool_uses[0].name == "read"

# 5. _MinimalResponse text + tool_uses 共存（关键场景）
resp3 = _MinimalResponse(
    text="Let me check that file",
    tool_uses=[_MinimalToolCall(id="c1", name="read", arguments='{"path":"/x"}')],
    stop_reason="end_turn"
)
assert resp3.text == "Let me check that file"
assert len(resp3.tool_uses) == 1
# text 和 tool_uses 同时非空 — 架构要求的共存场景

# 6. _MinimalTrajectory
traj = _MinimalTrajectory(
    history=[{"role": "user", "content": "hi"}],
    tool_call_records=[{"tool_name": "read", "result": "data"}],
    final_output="done",
    execution_time=1.5
)
assert traj.final_output == "done"
assert len(traj.history) == 1
assert len(traj.tool_call_records) == 1
```

**检查点**：所有最小数据结构可正常创建和访问，`_MinimalToolCall` JSON 解析正确，text + tool_uses 共存场景已覆盖。

---

### 步骤 5.2：实现 `__init__()` 和辅助方法 `[V]`

**操作**：实现编排器的构造函数和 `_resolve_optional()` 辅助方法。

```python
class LifecycleOrchestrator:
    def __init__(self, container: DIContainer, call_llm: Optional[Callable] = None):
        self.container = container
        self.call_llm = call_llm
        self._history: List[Any] = []          # 对话历史
        self._tool_call_records: List[Any] = [] # 工具调用记录
        self._start_time: float = 0.0           # 会话开始时间
        self._should_exit_flag: bool = False    # 退出标志

    def _resolve_optional(self, interface: type) -> Optional[Any]:
        """尝试解析组件，不存在时返回 None 并记录 WARNING。"""
        if self.container.is_registered(interface):
            return self.container.resolve(interface)
        else:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Component '{interface.__name__}' not registered, skipping"
            )
            return None
```

**验证方法**：

```python
from harness.core.container import DIContainer
from harness.core.orchestrator import LifecycleOrchestrator

container = DIContainer()
orch = LifecycleOrchestrator(container)

# 1. call_llm 可选
assert orch.call_llm is None

# 2. _resolve_optional 对未注册组件返回 None（不抛异常）
class IFoo: pass
result = orch._resolve_optional(IFoo)
assert result is None  # 返回 None 而非抛异常

# 3. _resolve_optional 对已注册组件返回实例
class IFooImpl: pass
foo = IFooImpl()
container.register(IFoo, foo)
assert orch._resolve_optional(IFoo) is foo

# 4. 组件注册后 call_llm 可被设置（text 响应）
def mock_llm(msgs, tools):
    return _MinimalResponse(text="ok", stop_reason="end_turn")
orch2 = LifecycleOrchestrator(container, call_llm=mock_llm)
assert orch2.call_llm is mock_llm

# 5. call_llm 可返回 tool_use 响应
def tool_llm(msgs, tools):
    return _MinimalResponse(
        tool_uses=[_MinimalToolCall(id="c1", name="bash", arguments='{"cmd":"ls"}')],
        stop_reason="tool_use"
    )
orch3 = LifecycleOrchestrator(container, call_llm=tool_llm)
resp = orch3.call_llm([], [])
assert len(resp.tool_uses) == 1
assert resp.tool_uses[0].parse_arguments() == {"cmd": "ls"}
```

**检查点**：`_resolve_optional` 正确区分已注册/未注册组件，不会因缺失组件而崩溃。

---

### 步骤 5.3：实现 `_phase_init()` — 会话初始化 `[V]`

**操作**：实现阶段一的编排逻辑。

核心流程（每步都有 `_resolve_optional` 保护）：
1. 解析 `InputAdapter` → 调用 `receive()` → 获取 `UserRequest`
2. 解析 `GuideProvider` → 构建 `GuideContext` → 调用 `get_guides()` → 缓存 `GuidesBundle`
3. 解析 `MemoryBackend` → 调用 `search()` → 获取相关记忆
4. 解析 `ToolRegistry` → 调用 `list_tools()` → 缓存工具列表
5. 构建并返回 `_MinimalAssemblyContext`

```python
def _phase_init(self) -> _MinimalAssemblyContext:
    self._start_time = time.time()

    # 1. InputAdapter（必需组件）
    adapter = self.container.resolve(InputAdapter)  # 直接 resolve，缺失则抛异常
    user_request = adapter.receive()

    # 2. GuideProvider（可选）
    guides = _MinimalGuidesBundle()
    guide_provider = self._resolve_optional(GuideProvider)
    if guide_provider:
        raw_guides = guide_provider.get_guides(...)
        # 转换为最小表示
        guides = ...
    self._cached_guides = guides

    # 3. MemoryBackend（可选）
    memories = []
    memory = self._resolve_optional(MemoryBackend)
    if memory:
        memories = memory.search(user_request.text, "episodic")

    # 4. ToolRegistry（可选）
    available_tools = []
    tool_registry = self._resolve_optional(ToolRegistry)
    if tool_registry:
        available_tools = tool_registry.list_tools()
    self._cached_tools = available_tools

    # 5. 构建 AssemblyContext
    ctx = _MinimalAssemblyContext(
        user_request=user_request,
        guides=guides,
        history=self._history,
        metadata={"memories": memories},
    )
    return ctx
```

**验证方法**：

```python
# 使用 mock 组件验证初始化流程
from harness.core.container import DIContainer
from harness.core.orchestrator import LifecycleOrchestrator

container = DIContainer()

# 设计 mock InputAdapter
class MockAdapter:
    def __init__(self):
        self.receive_count = 0
    def receive(self):
        self.receive_count += 1
        return _MinimalUserRequest(text="hello")
    def send(self, response):
        pass

# 注册必需组件
container.register(InputAdapter, MockAdapter())

orch = LifecycleOrchestrator(container)
ctx = orch._phase_init()

# 验证：
# 1. InputAdapter.receive() 被调用了
assert ctx.user_request.text == "hello"

# 2. 返回的 AssemblyContext 结构完整
assert ctx.guides is not None
assert isinstance(ctx.history, list)
assert isinstance(ctx.metadata, dict)
```

**检查点**：`_phase_init()` 在有必需组件时正常完成，缺失可选组件时不阻塞。

---

### 步骤 5.4：实现 `_phase_loop()` — 多轮对话循环（含 LLM 内层循环） `[V]`

**操作**：实现阶段二的编排逻辑。这是编排器最核心的方法，实现了完整的 LLM 对话循环。

#### 5.4.1 核心流程伪代码

```
_phase_loop(initial_ctx):
    ctx = initial_ctx
    assembler = _resolve_optional(ContextAssembler)
    adapter = container.resolve(InputAdapter)
    tool_registry = _resolve_optional(ToolRegistry)

    while not _should_exit_flag:
        # === 外层：组装上下文 ===
        messages = []
        if assembler:
            messages = assembler.assemble(ctx)
        elif ctx.user_request:
            # 降级：无 ContextAssembler 时直接用 user_request.text
            messages = [{"role": "user", "content": ctx.user_request.text}]

        # === 内层：LLM + Tool call 循环 ===
        while True:
            if not self.call_llm:
                logger.warning("call_llm not set, cannot call LLM")
                break

            response = self.call_llm(messages, self._cached_tools)

            # --- 处理 tool_uses ---
            if response.tool_uses:
                # 1. 构造 assistant message（含 tool_calls 块）
                assistant_msg = self._build_assistant_message(response)
                messages.append(assistant_msg)

                # 2. 串行执行每个 tool
                for tc in response.tool_uses:
                    before_ts = time.time()
                    try:
                        args = tc.parse_arguments()  # JSON string → dict
                        result = tool_registry.execute(tc.name, args)
                        after_ts = time.time()
                    except Exception as e:
                        result = ToolResult(success=False, error=str(e))
                        after_ts = time.time()

                    # 3. 记录到 tool_call_records
                    self._tool_call_records.append({
                        "tool_name": tc.name,
                        "arguments": args,
                        "result": result,
                        "started_at": before_ts,
                        "finished_at": after_ts,
                        "error": result.error if not result.success else None,
                    })

                    # 4. 构造 tool result message 追加到 messages
                    tool_msg = self._build_tool_result_message(tc, result)
                    messages.append(tool_msg)

                # 5. 如果本次 response 有 text → 发给用户 + 跳出内层循环
                if response.text:
                    adapter.send(response)
                    self._history.append(
                        {"role": "assistant", "content": response.text}
                    )
                    break
                # 6. 如果仅有 tool_uses 无 text → 继续内层循环（回到 LLM）
                continue

            # --- 处理纯 text 响应（无 tool_uses） ---
            if response.text:
                messages.append(
                    {"role": "assistant", "content": response.text}
                )
                adapter.send(response)
                self._history.append(
                    {"role": "assistant", "content": response.text}
                )
                break  # 跳出内层循环

            # --- 防御：空响应 ---
            logger.warning("LLM returned empty response (no text, no tool_uses)")
            break

        # === 外层：等待下一轮用户输入 ===
        new_request = adapter.receive()
        if self._should_exit(new_request):
            break

        # 更新 ctx 用于下一轮
        ctx = _MinimalAssemblyContext(
            user_request=new_request,
            guides=self._cached_guides,
            history=self._history,
            metadata={"memories": getattr(ctx, 'metadata', {}).get('memories', [])},
        )
```

#### 5.4.2 辅助方法：`_build_assistant_message()`

将 LLM 响应中的 text 和 tool_uses 转为 OpenAI 兼容的 assistant message dict：

```python
def build_assistant_message(, response: _MinimalResponse) -> Dict:
    """将 Response 转换为含 tool_calls 的 assistant message dict。

    OpenAI 格式:
    {
        "role": "assistant",
        "content": "<text or None>",
        "tool_calls": [
            {
                "id": "call_xxx",
                "type": "function",
                "function": {"name": "xxx", "arguments": "<json string>"}
            }
        ]
    }
    """
    msg = {"role": "assistant"}
    if response.text:
        msg["content"] = response.text
    else:
        msg["content"] = None

    if response.tool_uses:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
            }
            for tc in response.tool_uses
        ]
    return msg
```

#### 5.4.3 辅助方法：`_build_tool_result_message()`

将 ToolResult 转为 OpenAI 兼容的 tool result message dict：

```python
def _build_tool_result_message(
    self, tool_call: _MinimalToolCall, result: Any
) -> Dict:
    """将 tool 执行结果转换为 tool result message dict。

    OpenAI 格式:
    {
        "role": "tool",
        "tool_call_id": "call_xxx",
        "content": "<result as string or serialized>"
    }
    """
    content = result.content if hasattr(result, 'content') else str(result)
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": content,
    }
```

#### 5.4.4 关键设计决策

| 决策 | 理由 |
|------|------|
| tool_uses 中 arguments 保持 JSON string，执行时 parse | 与 OpenAI 原生格式一致；编排器不提前解析 |
| 工具执行失败时 `result.error` 不为 None，仍追加到 messages | LLM 需要看到错误来修正行为 |
| tool_uses 存在但 ToolRegistry 未注册时仍追加 assistant_msg | 保留完整轨迹，tool result 使用 error 信息 |
| text + tool_uses 共存时先执行 tools，再发 text | tools 结果保留在 messages 中供后续轮次；text 立即告知用户 |
| 内层循环不重新走 ContextAssembler | 架构要求：tool_use 链中上下文不重新组装 |

#### 5.4.5 验证方法

**验证 A — 纯文本单轮对话**：

```python
container = DIContainer()

class MockAdapter:
    def __init__(self):
        self.inputs = ["hello", ""]
        self.outputs = []
        self.idx = 0
    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]; self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")
    def send(self, response):
        self.outputs.append(response.text)

container.register(InputAdapter, MockAdapter())

# 纯文本 LLM
def text_llm(msgs, tools):
    return _MinimalResponse(text="Hello! How can I help?", stop_reason="end_turn")

orch = LifecycleOrchestrator(container, call_llm=text_llm)
orch._cached_guides = _MinimalGuidesBundle(identity="test")
orch._cached_tools = []

ctx = orch._phase_init()
orch._phase_loop(ctx)

assert len(orch._history) == 1
assert orch._history[0]["content"] == "Hello! How can I help?"
```

**验证 B — tool_use 循环（无 text）**：

```python
container2 = DIContainer()

class MockAdapter2:
    def __init__(self):
        self.inputs = ["read /tmp/x", ""]
        self.outputs = []
        self.idx = 0
    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]; self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")
    def send(self, r): self.outputs.append(r.text)

class MockToolRegistry:
    def __init__(self):
        self.executed = []
    def list_tools(self):
        return [{"name": "read", "description": "Read a file", "parameters": {...}}]
    def execute(self, name, args):
        self.executed.append((name, args))
        # 返回一个 mock ToolResult
        class TR:
            def __init__(self):
                self.success = True
                self.content = f"contents of {args['path']}"
                self.error = None
        return TR()

container2.register(InputAdapter, MockAdapter2())
container2.register(ToolRegistry, MockToolRegistry())

call_count = [0]
def tool_then_text_llm(msgs, tools):
    call_count[0] += 1
    if call_count[0] == 1:
        # 第一次调用：返回 tool_use
        return _MinimalResponse(
            tool_uses=[_MinimalToolCall(id="c1", name="read",
                        arguments='{"path": "/tmp/x"}')],
            stop_reason="tool_use"
        )
    else:
        # 第二次调用（收到 tool result 后）：返回 text
        return _MinimalResponse(text="File contents: hello world", stop_reason="end_turn")

orch2 = LifecycleOrchestrator(container2, call_llm=tool_then_text_llm)
orch2._cached_guides = _MinimalGuidesBundle(identity="test")
orch2._cached_tools = []

ctx2 = orch2._phase_init()
orch2._phase_loop(ctx2)

# 验证 tool 被执行
tr = container2.resolve(ToolRegistry)
assert len(tr.executed) == 1
assert tr.executed[0] == ("read", {"path": "/tmp/x"})

# 验证 LLM 被调用了 2 次
assert call_count[0] == 2

# 验证 tool_call_records 被记录
assert len(orch2._tool_call_records) == 1
assert orch2._tool_call_records[0]["tool_name"] == "read"

# 验证最终输出
assert orch2._history[-1]["content"] == "File contents: hello world"
```

**验证 C — text + tool_uses 共存（关键场景）**：

```python
container3 = DIContainer()

class MockAdapter3:
    def __init__(self):
        self.inputs = ["analyze /tmp/x", ""]
        self.outputs = []
        self.idx = 0
    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]; self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")
    def send(self, r): self.outputs.append(r.text)

class MockTR3:
    def __init__(self):
        self.executed = []
    def list_tools(self): return []
    def execute(self, name, args):
        self.executed.append((name, args))
        class TR:
            success=True; content="data"; error=None
        return TR()

container3.register(InputAdapter, MockAdapter3())
container3.register(ToolRegistry, MockTR3())

def coexistence_llm(msgs, tools):
    # 单次响应同时包含 text 和 tool_uses
    return _MinimalResponse(
        text="Let me check that file for you",
        tool_uses=[_MinimalToolCall(id="c1", name="read",
                    arguments='{"path": "/tmp/x"}')],
        stop_reason="end_turn"
    )

orch3 = LifecycleOrchestrator(container3, call_llm=coexistence_llm)
orch3._cached_guides = _MinimalGuidesBundle(identity="test")
orch3._cached_tools = []

ctx3 = orch3._phase_init()
orch3._phase_loop(ctx3)

# 验证 tool 被执行了
tr3 = container3.resolve(ToolRegistry)
assert len(tr3.executed) == 1

# 验证 text 被发送给用户
adapter3 = container3.resolve(InputAdapter)
assert "Let me check" in str(adapter3.outputs)

# 验证历史中记录了 text
assert any("Let me check" in str(h.get("content","")) for h in orch3._history)
```

**验证 D — 多轮对话（2 轮 + 退出）**：

```python
container4 = DIContainer()

class MultiTurnAdapter:
    def __init__(self):
        self.inputs = ["hello", "what's up", ""]
        self.outputs = []
        self.idx = 0
    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]; self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")
    def send(self, r): self.outputs.append(r.text)

container4.register(InputAdapter, MultiTurnAdapter())

class MockAssembler:
    def assemble(self, ctx):
        msgs = []
        if ctx.guides and ctx.guides.identity:
            msgs.append({"role": "system", "content": ctx.guides.identity})
        msgs.append({"role": "user", "content": ctx.user_request.text})
        return msgs

container4.register(ContextAssembler, MockAssembler())

def multi_turn_llm(msgs, tools):
    last_user_msg = [m for m in msgs if m["role"] == "user"][-1]["content"]
    return _MinimalResponse(text=f"Reply to: {last_user_msg}", stop_reason="end_turn")

orch4 = LifecycleOrchestrator(container4, call_llm=multi_turn_llm)
orch4._cached_guides = _MinimalGuidesBundle(identity="You are helpful")
orch4._cached_tools = []

ctx4 = orch4._phase_init()
orch4._phase_loop(ctx4)

# 两轮对话 + 第3轮空输入触发退出
adapter4 = container4.resolve(InputAdapter)
assert len(adapter4.outputs) == 2  # 两轮都有输出
assert len(orch4._history) == 2    # 两轮对话都在历史中
assert orch4._history[0]["content"] == "Reply to: hello"
assert orch4._history[1]["content"] == "Reply to: what's up"
```

**检查点**：四种场景全部通过 — (A) 纯文本单轮, (B) tool_use 循环, (C) text + tool_uses 共存, (D) 多轮对话 + 退出。

---

### 步骤 5.5：实现 `_phase_end()` — 会话结束 `[V]`

**操作**：实现阶段三的编排逻辑。

核心流程：
1. 从 `self._history`、`self._tool_call_records` 组装完整 `_MinimalTrajectory`
2. 解析 `Sensor` → 调用 `sense(trajectory)`
3. 清理内部状态

```python
def _phase_end(self) -> None:
    # 1. 组装 Trajectory
    trajectory = self._build_trajectory()

    # 2. Sensor（可选）
    sensor = self._resolve_optional(Sensor)
    if sensor:
        sensor.sense(trajectory)

    # 3. 清理
    self._history.clear()
    self._tool_call_records.clear()

def _build_trajectory(self) -> _MinimalTrajectory:
    execution_time = time.time() - self._start_time
    final_output = ""
    if self._history:
        last = self._history[-1]
        final_output = last.get("content", "")
    return _MinimalTrajectory(
        history=self._history,
        final_output=final_output,
        execution_time=execution_time,
    )
```

**验证方法**：

```python
container = DIContainer()

class MockSensor:
    def __init__(self):
        self.received_trajectory = None
    def sense(self, trajectory):
        self.received_trajectory = trajectory

container.register(InputAdapter, MockAdapter())
container.register(Sensor, MockSensor())

orch = LifecycleOrchestrator(container, call_llm=mock_llm)
orch._history = [{"role": "assistant", "content": "final answer"}]
orch._tool_call_records = [{"tool_name": "read", "result": "data"}]
orch._start_time = time.time() - 5.0  # 模拟 5 秒执行

orch._phase_end()

# 验证 Sensor 收到了完整的 Trajectory
sensor = container.resolve(Sensor)
assert sensor.received_trajectory is not None
assert len(sensor.received_trajectory.history) == 1
assert sensor.received_trajectory.final_output == "final answer"
assert sensor.received_trajectory.execution_time > 0

# 验证内部状态已清理
assert len(orch._history) == 0
```

**检查点**：Sensor 收到正确的 Trajectory，内部状态正确清理。

---

### 步骤 5.6：实现 `run()` — 完整生命周期入口 `[V]`

**操作**：实现编排器的唯一公开入口 `run()` 方法。

```python
def run(self) -> None:
    try:
        ctx = self._phase_init()
        self._phase_loop(ctx)
    except Exception as e:
        logger.error(f"Orchestrator error: {e}")
        raise OrchestratorError(str(e)) from e
    finally:
        self._phase_end()
```

**关键设计**：`_phase_end()` 在 `finally` 块中执行，确保即使中间出错也会执行清理。

**验证方法**：

```python
container = DIContainer()

class SpyAdapter:
    def __init__(self):
        self.inputs = ["hello", ""]
        self.outputs = []
        self.idx = 0
    def receive(self):
        if self.idx < len(self.inputs):
            t = self.inputs[self.idx]; self.idx += 1
            return _MinimalUserRequest(text=t)
        return _MinimalUserRequest(text="")
    def send(self, response):
        self.outputs.append(response.text)

class SpySensor:
    def __init__(self):
        self.called = False
    def sense(self, trajectory):
        self.called = True

container.register(InputAdapter, SpyAdapter())
container.register(Sensor, SpySensor())

def mock_llm(msgs, tools):
    return _MinimalResponse(text="response", stop_reason="end_turn")

orch = LifecycleOrchestrator(container, call_llm=mock_llm)
orch._cached_guides = _MinimalGuidesBundle()
orch._cached_tools = []

# 注册一个可选的 ContextAssembler 以避免 WARNING
class SpyAssembler:
    def assemble(self, ctx):
        return [{"role": "system", "content": "guide"}, {"role": "user", "content": ctx.user_request.text}]
container.register(ContextAssembler, SpyAssembler())

orch.run()

# 验证完整流程执行完毕
adapter = container.resolve(InputAdapter)
sensor = container.resolve(Sensor)
assert len(adapter.outputs) >= 1  # 至少有一个响应
assert sensor.called == True      # Sensor 被调用了
```

**检查点**：`run()` 完整执行三阶段，`finally` 确保 `_phase_end()` 总是被调用。

---

### 步骤 5.7：_should_exit 退出条件实现 `[V]`

**操作**：实现退出判断逻辑。

退出条件（任一满足即退出）：
1. `user_request.text` 为 `None` 或空字符串（EOF）
2. `user_request.text` 匹配退出关键词：`/exit`, `/quit`, `/bye`
3. `user_request.metadata` 中包含 `"exit": True`

**验证方法**：

```python
orch = LifecycleOrchestrator(DIContainer())

# 正常文本不退出
assert orch._should_exit(_MinimalUserRequest(text="hello")) == False

# 空字符串触发退出
assert orch._should_exit(_MinimalUserRequest(text="")) == True

# None 触发退出
assert orch._should_exit(_MinimalUserRequest(text=None)) == True

# 退出关键词触发退出
assert orch._should_exit(_MinimalUserRequest(text="/exit")) == True
assert orch._should_exit(_MinimalUserRequest(text="/quit")) == True
assert orch._should_exit(_MinimalUserRequest(text="/bye")) == True

# metadata 中的 exit 标志
assert orch._should_exit(
    _MinimalUserRequest(text="hello", metadata={"exit": True})
) == True
```

**检查点**：所有退出条件正确触发。

---

## Task 6：MinimalLLMAdapter 实现

**目标**：实现 `harness/adapters/llm_adapter.py` — 零外部依赖的 OpenAI 兼容 LLM 适配器。实现后 batch-01 就能 ping 通真实 LLM API。

**依赖**：Task 5.1（_MinimalResponse, _MinimalToolCall 数据结构）

**预估**：4 个步骤，40 分钟。

---

### 步骤 6.1：实现 `__init__()` 和 API key 解析 `[V]`

**操作**：实现构造函数，处理 base_url、api_key、model 等参数。

核心逻辑：
1. 存储 base_url（去除尾部 `/`，统一格式）
2. api_key 优先级：构造参数 > 环境变量 `OPENAI_API_KEY`
3. 存储 model、max_tokens、temperature、timeout
4. 构造完整的 API endpoint URL：`{base_url}/chat/completions`

```python
import os
import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional

class MinimalLLMAdapter:
    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._endpoint = f"{self.base_url}/chat/completions"
```

**验证方法**：

```python
import os
from harness.adapters.llm_adapter import MinimalLLMAdapter

# 1. 显式传入 api_key
adapter = MinimalLLMAdapter(api_key="sk-test123")
assert adapter.api_key == "sk-test123"
assert adapter.model == "gpt-4o"
assert adapter._endpoint == "https://api.openai.com/v1/chat/completions"

# 2. 从环境变量读取
os.environ["OPENAI_API_KEY"] = "sk-from-env"
adapter2 = MinimalLLMAdapter()
assert adapter2.api_key == "sk-from-env"

# 3. 构造参数优先于环境变量
adapter3 = MinimalLLMAdapter(api_key="sk-explicit")
assert adapter3.api_key == "sk-explicit"

# 4. base_url 尾部斜杠被去除
adapter4 = MinimalLLMAdapter(base_url="http://localhost:11434/v1/")
assert adapter4.base_url == "http://localhost:11434/v1"
assert adapter4._endpoint == "http://localhost:11434/v1/chat/completions"

# 5. 自定义 model 和参数
adapter5 = MinimalLLMAdapter(model="gpt-4o-mini", max_tokens=1024, temperature=0.3, timeout=60)
assert adapter5.model == "gpt-4o-mini"
assert adapter5.max_tokens == 1024
assert adapter5.temperature == 0.3
assert adapter5.timeout == 60

# 6. 两个都为空时不报错（延迟到首次调用时由 API 返回 401）
adapter6 = MinimalLLMAdapter(api_key="")
os.environ.pop("OPENAI_API_KEY", None)
assert adapter6.api_key == ""
```

**检查点**：API key 读取优先级正确，端点 URL 构造正确，参数默认值正确。

---

### 步骤 6.2：实现 `_build_request_body()` 和 `_send_request()` `[V]`

**操作**：实现 HTTP 请求构建和发送。

#### 6.2.1 `_build_request_body()`

```python
def _build_request_body(
    self, messages: List[Dict], tools: Optional[List[Dict]]
) -> Dict:
    """构建 OpenAI /v1/chat/completions 请求体。"""
    body = {
        "model": self.model,
        "messages": messages,
        "max_tokens": self.max_tokens,
        "temperature": self.temperature,
    }
    if tools:
        body["tools"] = tools
    return body
```

#### 6.2.2 `_send_request()`

```python
def _send_request(self, body: Dict) -> Dict:
    """发送 HTTP POST 请求，返回解析后的 JSON 响应。

    Raises:
        OrchestratorError: 网络错误、超时、非 2xx 响应。
    """
    import json as json_module

    data = json_module.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {self.api_key}",
    }

    req = urllib.request.Request(
        self._endpoint,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            response_bytes = resp.read()
            return json_module.loads(response_bytes.decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise OrchestratorError(
            f"LLM API error {e.code}: {error_body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise OrchestratorError(
            f"LLM API unreachable: {self._endpoint} — {e.reason}"
        ) from e
    except json_module.JSONDecodeError as e:
        raise OrchestratorError(
            f"LLM API returned invalid JSON"
        ) from e
    except Exception as e:
        raise OrchestratorError(
            f"LLM API unexpected error: {e}"
        ) from e
```

**验证方法**：

```python
from harness.adapters.llm_adapter import MinimalLLMAdapter

adapter = MinimalLLMAdapter(api_key="test-key", model="test-model")

# 1. 验证请求体结构 — 无 tools
body = adapter._build_request_body(
    messages=[{"role": "user", "content": "hi"}],
    tools=None
)
assert body["model"] == "test-model"
assert body["messages"] == [{"role": "user", "content": "hi"}]
assert body["max_tokens"] == 4096
assert "tools" not in body  # tools 为 None 时不包含

# 2. 验证请求体结构 — 含 tools
body2 = adapter._build_request_body(
    messages=[{"role": "user", "content": "read file"}],
    tools=[{"type": "function", "function": {"name": "read", "description": "...", "parameters": {}}}]
)
assert "tools" in body2
assert len(body2["tools"]) == 1
```

**检查点**：请求体格式符合 OpenAI API 规范，tools 字段可选。

---

### 步骤 6.3：实现 `_parse_response()` 和 `__call__()` `[V]`

**操作**：实现响应解析和主调用入口。

#### 6.3.1 `_parse_response()`

```python
def _parse_response(self, response_json: Dict) -> _MinimalResponse:
    """将 OpenAI chat completion 响应解析为 _MinimalResponse。

    处理三种响应形态：
    - 纯 text: choices[0].message.content 有值, 无 tool_calls
    - 纯 tool_use: choices[0].message.content 为 None, 有 tool_calls
    - text + tool_use 共存: 两者都有
    """
    try:
        choice = response_json["choices"][0]
    except (KeyError, IndexError) as e:
        raise OrchestratorError(
            f"LLM API unexpected response format: {str(response_json)[:500]}"
        ) from e

    message = choice.get("message", {})

    # 提取 text
    text = message.get("content")  # 可能为 None

    # 提取 tool_uses
    tool_uses = []
    raw_tool_calls = message.get("tool_calls", [])
    if raw_tool_calls:
        for tc in raw_tool_calls:
            tool_uses.append(_MinimalToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"]["arguments"],
            ))

    # 提取 stop_reason
    finish_reason = choice.get("finish_reason", "stop")
    # 标准化 stop_reason
    if finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason in ("stop", "length", "content_filter"):
        stop_reason = "end_turn"
    else:
        stop_reason = finish_reason

    return _MinimalResponse(
        text=text,
        tool_uses=tool_uses,
        stop_reason=stop_reason,
    )
```

#### 6.3.2 `__call__()`

```python
def __call__(
    self,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
) -> _MinimalResponse:
    """调用 LLM API。

    实现 call_llm 签名约定，可直接注入 LifecycleOrchestrator。
    """
    body = self._build_request_body(messages, tools)
    response_json = self._send_request(body)
    return self._parse_response(response_json)
```

**验证方法**：

```python
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.core.orchestrator import _MinimalResponse, _MinimalToolCall

adapter = MinimalLLMAdapter(api_key="test")

# 1. 解析纯 text 响应
text_response = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "Hello! How can I help?"
        },
        "finish_reason": "stop"
    }]
}
result = adapter._parse_response(text_response)
assert isinstance(result, _MinimalResponse)
assert result.text == "Hello! How can I help?"
assert result.tool_uses == []
assert result.stop_reason == "end_turn"

# 2. 解析纯 tool_use 响应
tool_response = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": '{"path": "/tmp/x"}'
                }
            }]
        },
        "finish_reason": "tool_calls"
    }]
}
result2 = adapter._parse_response(tool_response)
assert result2.text is None
assert len(result2.tool_uses) == 1
assert result2.tool_uses[0].id == "call_abc123"
assert result2.tool_uses[0].name == "read"
assert result2.tool_uses[0].arguments == '{"path": "/tmp/x"}'
assert result2.stop_reason == "tool_use"

# 3. 解析 text + tool_uses 共存响应
coexistence_response = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "Let me check that file for you",
            "tool_calls": [{
                "id": "call_def456",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": '{"path": "/tmp/x"}'
                }
            }]
        },
        "finish_reason": "stop"
    }]
}
result3 = adapter._parse_response(coexistence_response)
assert result3.text == "Let me check that file for you"
assert len(result3.tool_uses) == 1
# text 和 tool_uses 同时存在 — 关键验证点

# 4. 解析无效响应
from harness.core.exceptions import OrchestratorError
import pytest
with pytest.raises(OrchestratorError):
    adapter._parse_response({"no_choices": []})

# 5. 验证 finish_reason 映射
# "length" → "end_turn"
result4 = adapter._parse_response({
    "choices": [{"message": {"content": "truncated..."}, "finish_reason": "length"}]
})
assert result4.stop_reason == "end_turn"
```

**检查点**：三种响应形态（text、tool_use、共存）全部正确解析，无效响应抛异常，finish_reason 映射正确。

---

### 步骤 6.4：真实 API 连通性测试（手动）`[V]`

**操作**：用真实 API key 运行连通性测试。此步骤为手动验证，不阻塞后续 Task。

```python
# test_llm_live.py — 手动运行
"""真实 API 连通性测试。需要设置 OPENAI_API_KEY 环境变量。

用法：
    export OPENAI_API_KEY="sk-..."
    python test_llm_live.py
"""

from harness.adapters.llm_adapter import MinimalLLMAdapter

# 测试 1：连接到 OpenAI
adapter = MinimalLLMAdapter(model="gpt-4o-mini")
response = adapter([{"role": "user", "content": "Say 'hello' in exactly one word"}])
assert response.text is not None
assert response.stop_reason == "end_turn"
print(f"✓ OpenAI: {response.text}")

# 测试 2：连接到本地 Ollama（如果运行中）
try:
    local_adapter = MinimalLLMAdapter(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # Ollama 不需要真实 key
        model="llama3.2",
    )
    response2 = local_adapter([{"role": "user", "content": "Say hi"}])
    print(f"✓ Ollama: {response2.text}")
except Exception as e:
    print(f"⚠ Ollama not available: {e}")

print("\nAll connectivity tests passed!")
```

**检查点**：至少 OpenAI 端点可连通，返回有效响应。

---

## Task 7：harness/di.py 装配入口实现

**目标**：实现 `harness/di.py` — Harness 工厂类和框架顶层入口。

**依赖**：Task 5, Task 6

**预估**：2 个步骤，15 分钟。

---

### 步骤 7.1：实现 Harness 类 `[V]`

**操作**：在 `harness/di.py` 中实现 `Harness` 类。

```python
from harness.core.container import DIContainer
from harness.core.orchestrator import LifecycleOrchestrator

class Harness:
    """Harness Agent 框架的顶层入口。"""

    def __init__(self, orchestrator: LifecycleOrchestrator):
        self._orchestrator = orchestrator

    @staticmethod
    def from_container(
        container: DIContainer,
        call_llm: Optional[Callable] = None
    ) -> 'Harness':
        """从 DI 容器构造 Harness 实例。

        Args:
            container: 已装配好组件的 DI 容器
            call_llm: LLM 调用函数

        Returns:
            Harness 实例

        Raises:
            ComponentNotRegisteredError: InputAdapter 未注册
        """
        # 验证必需组件 InputAdapter 已注册
        if not container.is_registered(InputAdapter):
            raise ComponentNotRegisteredError(
                "InputAdapter is required but not registered"
            )
        orchestrator = LifecycleOrchestrator(container, call_llm=call_llm)
        return Harness(orchestrator)

    def run(self) -> None:
        """启动完整的会话生命周期。"""
        self._orchestrator.run()
```

**验证方法**：

```python
from harness.di import Harness
from harness.core.container import DIContainer
from harness.core.exceptions import ComponentNotRegisteredError
import pytest

# 1. 缺少 InputAdapter 时抛异常
container = DIContainer()
with pytest.raises(ComponentNotRegisteredError):
    Harness.from_container(container)

# 2. 有 InputAdapter 时正常构造
class MockAdapter:
    def receive(self): return _MinimalUserRequest(text="")
    def send(self, r): pass
container.register(InputAdapter, MockAdapter())
harness = Harness.from_container(container)
assert harness is not None
assert harness._orchestrator is not None
```

**检查点**：`Harness.from_container()` 正确校验必需组件。

---

### 步骤 7.2：更新 harness/__init__.py 导出 `[V]`

**操作**：在 `harness/__init__.py` 中添加版本号和顶层导出。

```python
__version__ = "0.1.0"

from .di import Harness
```

**验证方法**：

```python
import harness
assert harness.__version__ == "0.1.0"
from harness import Harness
```

**检查点**：版本号和 Harness 可从顶层 import。

---

## Task 8：完整测试实现

**目标**：编写 batch-01 所有模块的完整单元测试。

**依赖**：Task 2 ~ 6

**预估**：4 个测试文件，45 分钟。

---

### 步骤 8.1：`tests/test_exceptions.py` — 异常体系测试 `[V]`

**操作**：编写完整的异常测试。

测试清单：

```python
class TestHarnessError:
    def test_base_exception_is_exception(self):
        """HarnessError 是 Exception 的子类"""
        assert issubclass(HarnessError, Exception)

    def test_can_raise_and_catch(self):
        """可以正常抛出和捕获"""
        with pytest.raises(HarnessError):
            raise HarnessError("test")

    def test_message_preserved(self):
        """异常消息被正确保留"""
        try:
            raise HarnessError("hello world")
        except HarnessError as e:
            assert str(e) == "hello world"

class TestExceptionHierarchy:
    """验证完整的继承层次"""

    def test_config_error_hierarchy(self):
        assert issubclass(ConfigError, HarnessError)
        assert issubclass(ConfigNotFoundError, ConfigError)
        assert issubclass(ConfigParseError, ConfigError)
        assert issubclass(ConfigValidationError, ConfigError)

    def test_container_error_hierarchy(self):
        assert issubclass(ContainerError, HarnessError)
        assert issubclass(DuplicateRegistrationError, ContainerError)
        assert issubclass(ComponentNotRegisteredError, ContainerError)

    def test_orchestrator_error_hierarchy(self):
        assert issubclass(OrchestratorError, HarnessError)

    def test_catch_all_with_harness_error(self):
        """所有子类异常都可被 HarnessError 统一捕获"""
        for exc_cls in [
            ConfigNotFoundError, ConfigParseError, ConfigValidationError,
            DuplicateRegistrationError, ComponentNotRegisteredError,
            OrchestratorError,
        ]:
            try:
                raise exc_cls("test")
            except HarnessError:
                pass
            else:
                pytest.fail(f"{exc_cls.__name__} should be caught by HarnessError")

    def test_catch_with_intermediate_parent(self):
        """中间层父类可以捕获子类"""
        for exc_cls in [ConfigNotFoundError, ConfigParseError, ConfigValidationError]:
            try:
                raise exc_cls("test")
            except ConfigError:
                pass
            else:
                pytest.fail(f"{exc_cls.__name__} should be caught by ConfigError")
```

**验证方法**：`pytest tests/test_exceptions.py -v` → 全部通过。

---

### 步骤 8.2：`tests/test_container.py` — DIContainer 测试 `[V]`

**操作**：编写完整的 DIContainer 测试。

测试清单：

```python
class TestDIContainerRegistration:
    def test_register_single_component(self): ...
    def test_register_multiple_components(self): ...
    def test_register_duplicate_raises_error(self): ...
    def test_register_none_instance_raises_error(self): ...
    def test_register_non_type_interface_raises_error(self): ...

class TestDIContainerResolution:
    def test_resolve_returns_same_instance(self): ...
    def test_resolve_unregistered_raises_error(self): ...
    def test_resolve_unregistered_error_contains_name(self): ...
    def test_resolve_non_type_raises_error(self): ...

class TestDIContainerHelpers:
    def test_is_registered_positive(self): ...
    def test_is_registered_negative(self): ...
    def test_list_registered_empty(self): ...
    def test_list_registered_after_registration(self): ...
    def test_list_registered_returns_copy(self): ...

class TestDIContainerIntegration:
    def test_shared_instance_across_resolves(self):
        """验证同一实例被多处 resolve"""
        ...

    def test_component_graph(self):
        """验证多个组件间的依赖关系"""
        ...

    def test_error_messages_are_descriptive(self):
        """验证错误消息包含足够上下文"""
        ...
```

**验证方法**：`pytest tests/test_container.py -v` → 全部通过。

---

### 步骤 8.3：`tests/test_config.py` — ConfigLoader 测试 `[V]`

**操作**：编写完整的 ConfigLoader 测试。

测试清单：

```python
class TestProfileConfig:
    def test_create_with_required_fields(self): ...
    def test_default_values(self): ...
    def test_field_types(self): ...

class TestConfigLoaderLoad:
    def test_load_valid_minimal_config(self):
        """加载最小合法 TOML"""
        ...

    def test_load_valid_full_config(self):
        """加载完整 TOML（含 modules）"""
        ...

    def test_load_nonexistent_file_raises(self):
        """文件不存在抛 ConfigNotFoundError"""
        ...

    def test_load_invalid_toml_raises(self):
        """TOML 语法错误抛 ConfigParseError"""
        ...

    def test_load_unreadable_file_raises(self):
        """文件不可读抛异常"""
        ...

class TestConfigLoaderValidate:
    def test_validate_valid_config(self): ...
    def test_validate_empty_name_raises(self): ...
    def test_validate_empty_template_raises(self): ...
    def test_validate_missing_meta_section(self): ...
    def test_validate_non_bool_module_value_raises(self): ...
    def test_validate_empty_modules_ok(self): ...
    def test_validate_default_description_and_version(self): ...

class TestConfigLoaderIntegration:
    def test_load_and_validate_roundtrip(self):
        """完整流程：写入 TOML → load → validate → 字段正确"""
        ...
```

**验证方法**：`pytest tests/test_config.py -v` → 全部通过。

---

### 步骤 8.4：`tests/test_orchestrator.py` — LifecycleOrchestrator 测试 `[V]`

**操作**：编写完整的生命周期编排器测试。

测试清单：

```python
class TestOrchestratorInit:
    def test_init_with_container_only(self): ...
    def test_init_with_call_llm(self): ...
    def test_init_state_is_clean(self): ...

class TestResolveOptional:
    def test_returns_none_for_unregistered(self): ...
    def test_returns_instance_for_registered(self): ...
    def test_does_not_raise_on_missing(self): ...

class TestPhaseInit:
    def test_init_with_minimal_components(self):
        """仅有 InputAdapter 时 _phase_init 正常完成"""
        ...

    def test_init_with_guide_provider(self):
        """有 GuideProvider 时正确获取 guides"""
        ...

    def test_init_with_memory_backend(self):
        """有 MemoryBackend 时正确检索记忆"""
        ...

    def test_init_returns_assembly_context(self):
        """返回值是 AssemblyContext 结构"""
        ...

    def test_init_missing_input_adapter_raises(self):
        """InputAdapter 缺失时抛异常"""
        ...

class TestPhaseLoop:
    def test_single_turn_with_text_response(self): ...
    def test_multi_turn_conversation(self): ...
    def test_tool_use_loop(self): ...
    def test_exit_on_empty_input(self): ...
    def test_exit_on_exit_command(self): ...
    def test_exit_on_metadata_flag(self): ...
    def test_call_llm_none_handling(self): ...

class TestPhaseEnd:
    def test_sensor_called_with_trajectory(self): ...
    def test_sensor_not_called_when_not_registered(self): ...
    def test_trajectory_contains_history(self): ...
    def test_trajectory_contains_execution_time(self): ...
    def test_state_cleaned_after_end(self): ...

class TestRun:
    def test_full_lifecycle_with_mocks(self):
        """完整三阶段端到端测试"""
        ...

    def test_run_calls_phase_end_even_on_error(self):
        """异常时 finally 确保 _phase_end 被调用"""
        ...

    def test_minimal_valid_session(self):
        """最小可行的会话全程"""
        ...

class TestShouldExit:
    def test_normal_text_does_not_exit(self): ...
    def test_empty_text_exits(self): ...
    def test_none_text_exits(self): ...
    def test_exit_command_exits(self): ...
    def test_metadata_exit_flag(self): ...
```

**验证方法**：`pytest tests/test_orchestrator.py -v` → 全部通过。

---

### 步骤 8.5：`tests/test_llm_adapter.py` — MinimalLLMAdapter 测试 `[V]`

**操作**：编写完整的 LLM 适配器测试。

测试清单：

```python
class TestMinimalLLMAdapterInit:
    def test_default_values(self): ...
    def test_explicit_api_key(self): ...
    def test_api_key_from_env(self): ...
    def test_api_key_explicit_priority(self): ...
    def test_base_url_trailing_slash_removed(self): ...
    def test_custom_model_and_params(self): ...
    def test_empty_api_key_allowed(self): ...
    def test_endpoint_url_constructed(self): ...

class TestBuildRequestBody:
    def test_without_tools(self):
        """tools 为 None 时请求体不包含 tools 字段"""
        ...
    def test_with_tools(self):
        """tools 非空时正确包含"""
        ...
    def test_messages_passed_through(self): ...
    def test_max_tokens_and_temperature_included(self): ...

class TestParseResponse:
    def test_parse_text_response(self): ...
    def test_parse_tool_use_response(self): ...
    def test_parse_coexistence_response(self):
        """text + tool_uses 共存"""
        ...
    def test_parse_multiple_tool_calls(self): ...
    def test_parse_invalid_response_raises(self): ...
    def test_parse_missing_choices_raises(self): ...
    def test_finish_reason_tool_calls_maps_to_tool_use(self): ...
    def test_finish_reason_stop_maps_to_end_turn(self): ...
    def test_finish_reason_length_maps_to_end_turn(self): ...
    def test_finish_reason_unknown_passthrough(self): ...

class TestCall:
    def test_call_returns_minimal_response(self): ...
    def test_call_signature_matches_call_llm(self):
        """验证 __call__ 签名匹配 LifecycleOrchestrator 的 call_llm 约定"""
        ...

class TestErrorHandling:
    def test_http_error_wrapped_in_orchestrator_error(self): ...
    def test_url_error_wrapped_in_orchestrator_error(self): ...
    def test_invalid_json_wrapped_in_orchestrator_error(self): ...
```

**验证方法**：`pytest tests/test_llm_adapter.py -v` → 全部通过。

**注意**：测试使用 mock 替代真实 HTTP 调用（`unittest.mock.patch("urllib.request.urlopen")`），不依赖外部 API 可用性。

---

### 步骤 8.6：全局测试运行 `[V]`

**操作**：运行所有 batch-01 测试。

```bash
pytest tests/test_exceptions.py tests/test_container.py \
       tests/test_config.py tests/test_orchestrator.py \
       tests/test_llm_adapter.py -v
```

**验证方法**：全部测试通过，0 failure，0 error。

---

## Task 9：最终验证

**目标**：确保 batch-01 所有产出符合 SDD 规范。

**依赖**：Task 8

**预估**：3 个检查点，10 分钟。

---

### 步骤 9.1：文件结构验证 `[V]`

```bash
# 验证所有预期文件存在
find harness -type f | sort
# 预期：
# harness/__init__.py
# harness/di.py
# harness/core/__init__.py
# harness/core/exceptions.py
# harness/core/container.py
# harness/config/loader.py
# harness/core/orchestrator.py
# harness/adapters/llm_adapter.py

find tests -type f | sort
# 预期：
# tests/__init__.py
# tests/test_exceptions.py
# tests/test_container.py
# tests/test_config.py
# tests/test_orchestrator.py
# tests/test_llm_adapter.py
```

---

### 步骤 9.2：代码规范验证 `[V]`

按 `sdd/05-conventions.md` 检查：

- [ ] 所有类名 PascalCase
- [ ] 所有函数/方法名 snake_case
- [ ] 所有公开接口参数和返回值有类型标注
- [ ] 所有公开类有 docstring
- [ ] 异常继承自 `HarnessError`
- [ ] `harness/core/` 不 import `harness/components/`
- [ ] `harness/interfaces/` 不 import 任何实现模块（batch-01 中 interfaces/ 还不存在，此项 N/A）

---

### 步骤 9.3：SDD 需求覆盖验证 `[V]`

对照 `sdd/04-roadmap.md` 中 batch-01 的范围说明：

- [ ] `DIContainer` 类：预构造实例注册模式（`register` + `resolve`）
- [ ] `LifecycleOrchestrator` 类：三阶段编排（init → loop → end）
- [ ] TOML 配置解析器：读取 `profile.toml`，返回配置对象
- [ ] 组件缺失时可观测（日志 WARNING），不静默跳过
- [ ] `harness/di.py`：`Harness.from_container()` 工厂方法
- [ ] `MinimalLLMAdapter`：零依赖 OpenAI 兼容适配器，支持 `__call__` 签名
- [ ] 无任何组件实现（那是 batch-03 ~ 08）
- [ ] 无 Hook 系统（那是 batch-09）
- [ ] 无 CLI 入口（那是 batch-10）

---

## 依赖关系图（Task 间）

```
Task 1 (骨架)
   ↓
Task 2 (异常)
   ↓
Task 3 (DIContainer) ←── Task 4 (ConfigLoader) ←── 并行
   ↓                          ↓
   └──────────┬───────────────┘
              ↓
         Task 5 (Orchestrator)
              ↓
         Task 6 (LLM Adapter)  ← 依赖 Task 5.1 的数据结构
              ↓
         Task 7 (di.py)        ← 可集成 LLM adapter
              ↓
         Task 8 (测试)         ← 依赖 Task 2 ~ 7 全部完成
              ↓
         Task 9 (最终验证)
```
