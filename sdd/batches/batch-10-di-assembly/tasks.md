# batch-10: DI Assembly — 任务清单

> 实现 DI 装配层：YAML 装配器、CLI 入口、领域模板、端到端集成测试。

---

## 任务 1：接口短名映射表

**目标**：定义 interface 短名到完整类型的映射，供 YamlAssembler 使用。

**文件**：`harness/config/yaml_assembler.py`（新建，第一部分）

**详细要求**：
- 定义 `INTERFACE_REGISTRY` 字典常量：
  - key：接口短名（如 `"InputAdapter"`）
  - value：完整接口类型（如 `harness.interfaces.InputAdapter`）
- 包含全部 7 个接口短名：`InputAdapter`、`GuideProvider`、`MemoryBackend`、
  `ContextAssembler`、`Sensor`、`SystemToolProvider`、`MCPAdapter`
- 定义 `_INTERFACE_SHORT_TO_FULL` 反向映射辅助函数（短名 → 完整类型）

**常量定义**：
```python
from harness.interfaces import (
    InputAdapter, GuideProvider, MemoryBackend,
    ContextAssembler, Sensor, SystemToolProvider, MCPAdapter,
)

INTERFACE_REGISTRY: Dict[str, type] = {
    "InputAdapter": InputAdapter,
    "GuideProvider": GuideProvider,
    "MemoryBackend": MemoryBackend,
    "ContextAssembler": ContextAssembler,
    "Sensor": Sensor,
    "SystemToolProvider": SystemToolProvider,
    "MCPAdapter": MCPAdapter,
}

REQUIRED_INTERFACES = {"InputAdapter"}  # 必须注册的接口
```

**验收**：映射表包含全部 7 个接口，短名与 design.md §3.3 表格一致。

---

## 任务 2：YamlAssembler — 核心实现

**目标**：实现从 YAML 装配声明文件构建 DIContainer 和 Harness 的加载器。

**文件**：`harness/config/yaml_assembler.py`（新建，第二部分）

**详细要求**：

### 2.1 `__init__`

```python
class YamlAssembler:
    def __init__(self):
        self._config: Optional[Dict] = None
        self._container: Optional[DIContainer] = None
```

### 2.2 `load(path: str) -> "YamlAssembler"`

1. 校验文件存在且可读，否则 `FileNotFoundError`
2. 使用 `yaml.safe_load()` 解析 YAML
3. 校验顶层结构：必须包含 `harness` key
4. 校验 `harness.components` 为 list（允许空列表，但 `InputAdapter` 必须在其中）
5. 校验 `harness.hooks` 为 list（允许空列表）
6. 校验 `harness.llm` 合法（如存在，`provider` 字段必须为非空字符串）
7. 存储解析结果到 `self._config`

### 2.3 `assemble() -> Harness`

1. 创建空 `DIContainer`
2. 解析 `harness.llm` 段 → 创建 `MinimalLLMAdapter` 实例
   - 如果 `llm` 段不存在：`call_llm=None`（LLM 调用将被跳过，仅用于测试场景）
   - 如果 `provider` 是 `"custom"`：可选指定 `base_url` 和 `api_key`
3. 按 `harness.components` 列表顺序依次注册组件：
   a. 动态 import 实现类（`importlib.import_module`）
   b. 解析 `params` 字段 → 传给 `__init__`
   c. 解析 `inject` 字段 → 从 `self._container.resolve()` 获取已注册的依赖实例
   d. 构造实例：`impl_class(**params, **injected_deps)`
   e. 调用 `container.register(interface_type, instance)`
4. 校验必需接口 `InputAdapter` 已注册
5. 调用 `Harness.from_container(container, call_llm=llm)`
6. 解析 `harness.hooks` → 对每个 hook 条目：
   a. 动态 import handler 函数
   b. 调用 `harness._orchestrator.register_hook(event, handler)`

### 2.4 动态 import 辅助方法

```python
@staticmethod
def _import_class(full_path: str) -> type:
    """从 'module.path.ClassName' 动态加载类。"""
    module_path, class_name = full_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
```

### 2.5 依赖注入辅助方法

```python
def _resolve_inject(self, inject: Dict[str, str]) -> Dict[str, Any]:
    """将 inject 的短名映射解析为实际组件实例。"""
    resolved = {}
    for param_name, interface_short_name in inject.items():
        if interface_short_name not in INTERFACE_REGISTRY:
            raise UnknownInterfaceError(
                f"Unknown interface '{interface_short_name}'. "
                f"Available: {list(INTERFACE_REGISTRY.keys())}"
            )
        interface_type = INTERFACE_REGISTRY[interface_short_name]
        try:
            resolved[param_name] = self._container.resolve(interface_type)
        except ComponentNotRegisteredError as e:
            raise DependencyNotSatisfiedError(
                f"Cannot inject '{interface_short_name}' for parameter "
                f"'{param_name}': {e}"
            ) from e
    return resolved
```

> **注意**：`_resolve_inject` 必须将 `DIContainer.resolve()` 抛出的
> `ComponentNotRegisteredError` 包装为 `DependencyNotSatisfiedError`，
> 提供更清晰的错误信息（指明是哪个 inject 引用失败了）。

### 2.6 异常类（新增）

```python
class AssemblyError(Exception):
    """YAML 装配过程中的错误基类。"""

class UnknownInterfaceError(AssemblyError):
    """YAML 中引用了未知的 interface 短名。"""

class DependencyNotSatisfiedError(AssemblyError):
    """YAML 中 inject 引用的组件尚未注册。"""

class AssemblyValidationError(AssemblyError):
    """YAML 结构校验失败。"""
```

**验收**：
- `YamlAssembler().load("harness.yaml").assemble()` 完整流程正常运行
- 缺少 `InputAdapter` 时抛出 `AssemblyValidationError`
- inject 引用未注册组件时抛出 `DependencyNotSatisfiedError`
- 实现类路径无效时抛出 `ImportError`
- 空 YAML（无 components）时给出明确错误

---

## 任务 3：Harness 公开 Hook 注册方法

**目标**：在 Harness 类上新增 `register_hook(event, hook)` 公开方法，供 YamlAssembler 在装配后注册 Hook。

**文件**：`harness/di.py`（修改，仅新增方法，不修改现有逻辑）

**详细要求**：

```python
# 在 Harness 类中新增：
def register_hook(self, event: str, hook: Hook) -> None:
    """注册一个生命周期 Hook。

    代理到内部 LifecycleOrchestrator.register_hook()。
    供 YamlAssembler 在装配后注册 YAML 中声明的 Hook。

    Args:
        event: 生命周期事件名。
        hook: Hook 函数，签名 ``(context: HookContext) -> None``。
    """
    self._orchestrator.register_hook(event, hook)
```

**验收**：`Harness` 的公开 API 包含 `register_hook` 方法，不破坏现有 `from_container` / `run` 行为。

---

## 任务 4：CLI 入口 — main.py

**目标**：创建 `main.py` 作为框架的 CLI 入口点。

**文件**：`main.py`（新建）

**详细要求**：

### 3.1 命令路由

使用 `argparse` 实现两个子命令：

```python
# python main.py init --profile coding-assistant <output-dir>
# python main.py run [--config harness.yaml] [--debug]
```

### 3.2 `init` 子命令

1. 接收 `--profile` / `-p` 参数（默认 `"coding-assistant"`）
2. 接收 `output_dir` 位置参数
3. 检查 `output_dir` 是否已存在：
   - 存在且非空 → 打印错误并退出（`--force` 标志跳过检查）
4. 查找模板目录：`profiles/{profile}/`
5. 模板目录不存在 → 打印错误，列出 `profiles/` 下可用模板
6. 复制模板目录下所有文件到 `output_dir/`
7. 打印成功信息，列出创建的文件

### 3.3 `run` 子命令

1. 接收 `--config` / `-c` 参数（默认 `"./harness.yaml"`）
2. 接收 `--debug` / `-d` 标志
3. `--debug` 时设置 `logging.basicConfig(level=logging.DEBUG)`
4. 查找配置文件：
   - 存在 → 使用 `YamlAssembler` 加载并装配
   - 不存在 → 降级为全默认组件装配（类似 `examples/minimal_agent.py`）
5. 装配完成后调用 `harness.run()`
6. 捕获 `KeyboardInterrupt` → 优雅退出
7. 捕获 `AssemblyError` → 打印错误信息并退出（exit code 1）

### 3.4 日志配置

- 默认日志级别 `INFO`
- `--debug` 时设为 `DEBUG`
- 日志格式：`"%(levelname)s | %(name)s | %(message)s"`

**验收**：
- `python main.py init my-agent` 正确创建目录和文件
- `python main.py run --config harness.yaml` 正常启动会话
- 无 config 文件时降级启动（不崩溃）
- `Ctrl+C` 时优雅退出

---

## 任务 5：Profile 模板 — coding-assistant

**目标**：创建 coding-assistant 领域模板骨架。

**文件**：
- `profiles/coding-assistant/harness.yaml`（新建）
- `profiles/coding-assistant/AGENTS.md`（新建）
- `profiles/coding-assistant/README.md`（新建）

**详细要求**：

### 4.1 harness.yaml
- 按 design.md §5.2 的内容创建
- 包含 6 个默认组件注册：
  `InputAdapter`(CliAdapter)、`MemoryBackend`(MdMemory)、
  `GuideProvider`(FileGuideProvider)、`ContextAssembler`(SimpleAssembler)、
  `Sensor`(LoggingSensor)、`SystemToolProvider`(DefaultSystemToolProvider)
- `inject` 关系：`ContextAssembler` 和 `Sensor` 都注入 `MemoryBackend`
- `hooks` 为空列表，`llm.provider` 为 `"openai"`

### 4.2 AGENTS.md
- 按 design.md §5.3 的内容创建
- 包含基本身份定义、能力清单、行为规则

### 4.3 README.md
- 简短使用说明：
  - 如何修改 `harness.yaml` 替换组件
  - 如何添加 Hook
  - 如何切换 LLM provider
  - 运行命令示例

**验收**：三个文件都存在且内容完整。

---

## 任务 6：单元测试 — YamlAssembler

**目标**：测试 YamlAssembler 的核心逻辑。

**文件**：`tests/test_yaml_assembler.py`（新建）

**测试用例**：

| # | 测试名 | 验证内容 |
|---|--------|---------|
| 1 | `test_load_valid_yaml` | 加载合法 YAML 文件成功 |
| 2 | `test_load_missing_file_raises` | 加载不存在的文件抛 FileNotFoundError |
| 3 | `test_load_invalid_yaml_syntax_raises` | 加载语法错误的 YAML 抛异常 |
| 4 | `test_load_missing_harness_key_raises` | 缺少顶层 `harness` key 抛 AssemblyValidationError |
| 5 | `test_assemble_minimal_config` | 最小配置（仅 InputAdapter）装配成功 |
| 6 | `test_assemble_full_config` | 完整配置（全部 6 个默认组件，不含 MCPAdapter）装配成功 |
| 6b | `test_assemble_with_mcp_adapter` | 含 MCPAdapter 的 7 组件配置装配成功（验证嵌套 `mcp_configs` 和 `transforms` 传递） |
| 7 | `test_assemble_missing_input_adapter_raises` | 缺少 InputAdapter 注册时抛 AssemblyValidationError |
| 8 | `test_assemble_with_inject_resolves_dependency` | inject 依赖被正确解析注入 |
| 9 | `test_assemble_with_unresolved_inject_raises` | inject 引用未注册组件抛 DependencyNotSatisfiedError |
| 10 | `test_assemble_with_invalid_implementation_raises` | 实现类路径无效抛 ImportError |
| 11 | `test_assemble_with_unknown_interface_raises` | 未知 interface 短名抛 UnknownInterfaceError |
| 12 | `test_assemble_registers_hooks` | YAML 中声明的 hook 被正确注册 |
| 13 | `test_assemble_with_llm_config_creates_adapter` | llm 段正确创建 MinimalLLMAdapter |
| 14 | `test_assemble_without_llm_config_call_llm_is_none` | 无 llm 段时 call_llm 为 None |
| 15 | `test_load_invalid_llm_provider_empty_raises` | llm.provider 为空字符串时抛 AssemblyValidationError |
| 16 | `test_load_components_not_a_list_raises` | components 不是列表时抛 AssemblyValidationError |
| 17 | `test_load_hooks_not_a_list_raises` | hooks 不是列表时抛 AssemblyValidationError |
| 18 | `test_assemble_invalid_hook_handler_raises` | hook handler 路径无效时抛 ImportError |

**验收**：18 个测试全部通过。

---

## 任务 7：端到端测试 — 完整生命周期

**目标**：验证从 YAML 装配到完整会话生命周期的端到端流程。

**文件**：`tests/test_e2e_assembly.py`（新建）

**测试用例**：

| # | 测试名 | 验证内容 |
|---|--------|---------|
| 1 | `test_e2e_yaml_assembly_full_lifecycle` | YAML 装配 → 完整 init→loop→end 生命周期 |
| 2 | `test_e2e_cli_init_creates_project` | `main.py init` 正确创建项目目录和文件 |
| 3 | `test_e2e_cli_run_with_yaml` | `main.py run --config` 正常启动 |
| 4 | `test_e2e_default_assembly_without_yaml` | 无 YAML 时降级装配不崩溃 |
| 5 | `test_e2e_hook_from_yaml_is_triggered` | YAML 中声明的 hook 在会话中被触发 |
| 6 | `test_e2e_yaml_with_mock_llm_tool_loop` | mock LLM → tool_use 场景完整执行 |
| 7 | `test_e2e_sensor_writes_after_session` | 会话结束后 Sensor 写入 MemoryBackend |
| 8 | `test_e2e_memory_persists_across_assemblies` | 同一 memory 目录的两次装配间记忆持久化 |
| 9 | `test_e2e_config_file_not_found_fallback` | config 文件不存在时降级提示 |
| 10 | `test_e2e_exit_signal_from_cli` | /exit 信号正确终止会话 |

**验收**：10 个端到端测试全部通过。

---

## 任务 8：现有测试回归验证

**目标**：确保新增代码不破坏现有测试。

**命令**：`python -m pytest tests/ -v`

**验收**：所有已有测试（500+ tests）全部通过，无回归。

---

## 任务 9：全局验收标准验证

**目标**：对照 `sdd/06-acceptance.md` 逐条验证。

**检查项**：
- [ ] 用户可通过 YAML 文件声明组件装配
- [ ] 用户可通过 DI 容器显式装配组件（Python API 保留）
- [ ] 框架能完成一轮完整的多轮对话（mock LLM）
- [ ] ToolRouter 能合并 SystemToolProvider 和 MCPAdapter 的 Tool
- [ ] Hook 在关键生命周期点被触发
- [ ] Sensor 在会话结束后写入 MemoryBackend
- [ ] 下一会话能读到上一会话 Sensor 写入的记忆
- [ ] `harness init --profile coding-assistant <name>` 正确生成项目骨架
- [ ] 一个最小示例能从会话初始跑到会话结束，全程不报错
- [ ] 组件未注册时产生可观测信息
- [ ] 组件异常时 `on_error` Hook 被触发，框架不崩溃
- [ ] 所有公开方法有完整类型标注
- [ ] 所有公开类有 docstring

---

## 任务 10：文档检查

**目标**：确保 design.md、tasks.md、acceptance.md 与代码实现一致。

**检查项**：
- [ ] design.md 中接口短名映射表与代码一致
- [ ] design.md 中 YAML 格式与 YamlAssembler 实际支持的字段一致
- [ ] design.md 中边界声明（"不修改的文件"列表）与实际一致
- [ ] design.md 与 sdd/01-architecture.md 无冲突
- [ ] design.md 与 sdd/02-interfaces.md 无冲突
- [ ] acceptance.md 中所有验收项可被测试覆盖
- [ ] tasks.md 中所有任务已完成

---

## 完成标准

全部 10 个任务完成，且：
- `python -m pytest tests/test_yaml_assembler.py -v` 全部通过
- `python -m pytest tests/test_e2e_assembly.py -v` 全部通过
- `python -m pytest tests/ -v` 全部通过（无回归）
- `python main.py init --profile coding-assistant /tmp/test-agent` 正常运行
- 代码符合 `sdd/05-conventions.md` 规范
- 对照 `sdd/06-acceptance.md` 全部验收项通过
