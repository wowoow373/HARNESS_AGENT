# 05 — 开发约定

> 所有批次 agent 必须遵守的统一规范，保证跨批次代码风格一致、可互操作。

---

## 一、代码规范

### 命名

| 类别 | 规则 | 示例 |
|------|------|------|
| 类名 | PascalCase | `ContextAssembler`, `JsonlMemory` |
| 函数/方法名 | snake_case | `get_guides()`, `list_namespaces()` |
| 变量名 | snake_case | `user_request`, `assembly_context` |
| 模块文件名 | snake_case | `input_adapter.py`, `memory_backend.py` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_MAX_HISTORY` |
| 私有成员 | 前缀 `_` | `_validate_input()` |

### 类型标注

- **公开接口方法**：参数和返回值**必须**标注类型
- **内部/私有方法**：可省略，但建议标注
- **大包对象字段**：所有字段必须标注类型（使用 `dataclass` + 类型标注）
- 使用 `typing` 模块：`List`, `Dict`, `Optional`, `Any`

```python
from typing import List, Dict, Optional, Any

@dataclass
class AssemblyContext:
    user_request: UserRequest
    guides: GuidesBundle
    available_tools: List[ToolDefinition]
    history: List[Message]
    memories: List[MemoryItem]
    system_state: SystemState
    metadata: Dict[str, Any]
```

### 错误处理

- 公开接口方法：抛出明确的异常（自定义异常类），附带描述性消息
- 内部方法：异常向上传播，由调用者决定如何处理
- 异常必须可观测：记录到日志或 stderr，不能静默吞掉
- 自定义异常继承自 `HarnessError` 基类

```python
class HarnessError(Exception):
    """Harness 框架所有异常的基类"""

class ComponentNotFoundError(HarnessError):
    """组件未注册时抛出"""
    pass

class ToolExecutionError(HarnessError):
    """工具执行失败时抛出"""
    pass
```

### 日志

- 使用 Python 标准库 `logging` 模块
- 实例化方式：每个模块 `logger = logging.getLogger(__name__)`
- 日志级别采用默认约定：`DEBUG`（开发调试）、`INFO`（关键节点）、`WARNING`（可能的问题）、`ERROR`（明确的错误）
- 框架不强制配置日志格式，用户在 DI 装配时自行设置

### 文档

- 每个公开类：docstring 描述职责
- 每个公开方法：docstring 描述参数和返回值
- 构造函数参数：必须在 docstring 中注释

```python
class JsonlMemory:
    """MemoryBackend 的 JSONL 文件存储实现。

    追加式写入，每行一条 JSON 记录。
    """
    def __init__(self, path: str):
        """初始化。

        Args:
            path: JSONL 文件路径，不存在时自动创建。
        """
```

---

## 二、接口/实现分离

- 每个组件 **必须同时提供**：
  - `harness/interfaces/<name>.py` — 抽象接口（Protocol 或 ABC）
  - `harness/components/<name>/<name>_impl.py` — 至少一个默认实现
- 接口文件只包含抽象定义，**不得**包含任何实现逻辑
- `harness/interfaces/__init__.py` 导出所有公开接口
- 组件的 `harness/components/<name>/__init__.py` 导出默认实现类

---

## 三、测试规范

- 测试框架：`pytest`
- 每个组件至少一个测试文件：`tests/test_<component>.py`
- 测试覆盖：接口符合性 + 基本功能路径
- 可以使用 mock 替代 LLM 调用等外部依赖
- 测试文件结构：
  - 顶部：需要的 imports
  - 中间：fixtures
  - 底部：测试函数/方法

---

## 四、DI 装配约定

- 组件依赖通过**构造函数注入**，不通过 import 直接引用
- DI 容器采用**预构造实例注册**模式：
  - `register(interface, instance)` — 注册已创建的组件实例
  - `resolve(interface)` — 按接口类型获取实例
- 用户手动创建组件实例并显式注入依赖，容器仅负责存储和按类型解析
- 同一个实例注册后可被框架在多个位置使用（如 MemoryBackend 被 ContextAssembler 和 Sensor 共享）

```python
# 正确：构造函数注入 + 预构造实例注册
memory = JsonlMemory(path="./memory.jsonl")
assembler = SimpleAssembler(memory=memory)

class SimpleAssembler:
    def __init__(self, memory: MemoryBackend):
        self.memory = memory

# 错误：在组件内部直接引用具体实现
from harness.components.memory_backend.jsonl_memory import JsonlMemory
class SimpleAssembler:
    def __init__(self):
        self.memory = JsonlMemory("./memory.jsonl")
```

---

## 五、Git 提交约定

- 提交信息格式：`batch-XX: <what was done>`
- 示例：`batch-03: implement JsonlMemory with read/write/search`
- 批次完成后提交，作为一个独立的 commit
