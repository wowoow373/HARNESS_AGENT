# Batch-02-1: 接口测试 + 类型迁移 — 验收标准

> **验收原则**：每条标准可验证。验证方式包括自动化测试 (`pytest`)、grep 搜索、Python one-liner 静态检查。
>
> **通过条件**：所有 `[ ]` 被勾选为 `[x]`。

---

## 一、类型迁移验收 — 源码零残留

### 1.1 orchestrator.py

- [ ] **AC-MIG-01**：`harness/core/orchestrator.py` 中无 `_Minimal*` import
  - **验证**：`grep -c '_Minimal' harness/core/orchestrator.py` → `0`
- [ ] **AC-MIG-02**：`harness/core/orchestrator.py` 中无 `_normalize_` 方法
  - **验证**：`grep -c '_normalize_' harness/core/orchestrator.py` → `0`
- [ ] **AC-MIG-03**：`harness/core/orchestrator.py` 中无 `parse_arguments()` 调用
  - **验证**：`grep -c 'parse_arguments' harness/core/orchestrator.py` → `0`
- [ ] **AC-MIG-04**：`harness/core/orchestrator.py` import 来自 `..interfaces.types`（而非 `.types`）
  - **验证**：`python -c "import ast, sys; tree = ast.parse(open('harness/core/orchestrator.py').read()); imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]; assert any(n.module == '..interfaces.types' for n in imports if isinstance(n, ast.ImportFrom)), 'Missing import from ..interfaces.types'"`

### 1.2 llm_adapter.py

- [ ] **AC-MIG-05**：`harness/adapters/llm_adapter.py` 中无 `_Minimal*` 引用
  - **验证**：`grep -c '_Minimal' harness/adapters/llm_adapter.py` → `0`
- [ ] **AC-MIG-06**：`tests/test_llm_adapter.py` 中无 dead import（第 16-17 行 `_MinimalToolCall`, `_MinimalToolCallFunction`）
  - **验证**：`grep '_MinimalToolCall\|_MinimalToolCallFunction' tests/test_llm_adapter.py` → 空

### 1.3 全量 grep

- [ ] **AC-MIG-07**：源码目录中 `_Minimal*` 引用全部消除（仅在 `core/types.py` 中有定义）
  - **验证脚本**：
    ```bash
    # 检查源码中无 _Minimal* 引用（排除 core/types.py）
    grep -r '_Minimal' harness/core/orchestrator.py \
      harness/adapters/ \
      harness/messaging/ \
      harness/di.py \
      harness/core/container.py \
      harness/core/config.py \
      harness/core/llm_adapter.py \
      harness/core/exceptions.py \
      || echo "PASS: no _Minimal references found in source"
    ```
- [ ] **AC-MIG-08**：测试文件中 `_Minimal*` 引用全部消除
  - **验证**：`grep -r '_Minimal' tests/` → 空

### 1.4 core/types.py 废弃

- [ ] **AC-MIG-09**：`harness/core/types.py` 导入时触发 `DeprecationWarning`
  - **验证**：`python -c "import warnings; warnings.simplefilter('always'); from harness.core.types import _MinimalUserRequest"` 2>&1 | grep -q "deprecated"
- [ ] **AC-MIG-10**：源码中无 `from .types import` 或 `from ..core.types import` 引用
  - **验证**：`grep -r 'from .types import\|from ..core.types import' harness/adapters/ harness/messaging/ harness/core/orchestrator.py` → 空

---

## 二、转换层验收

### 2.1 Message ↔ dict 转换

- [ ] **AC-CONV-01**：`message_to_dict(Message(role="user", content="hi"))` 返回 `{"role": "user", "content": "hi"}`
  - **验证**：`python -c "from harness.interfaces.types import Message; from harness.messaging import message_to_dict; assert message_to_dict(Message(role='user', content='hi')) == {'role': 'user', 'content': 'hi'}"`
- [ ] **AC-CONV-02**：`message_to_dict` — `tool_call_id` 非 None 时输出含该字段
  - **验证**：`python -c "from harness.interfaces.types import Message; from harness.messaging import message_to_dict; d = message_to_dict(Message(role='tool', content='result', tool_call_id='c1')); assert d == {'role': 'tool', 'content': 'result', 'tool_call_id': 'c1'}"`
- [ ] **AC-CONV-03**：`message_to_dict` — `tool_call_id` 为 None 时输出不含该字段
  - **验证**：`python -c "from harness.interfaces.types import Message; from harness.messaging import message_to_dict; d = message_to_dict(Message(role='assistant', content='hi')); assert set(d.keys()) == {'role', 'content'}"`
- [ ] **AC-CONV-04**：`dict_to_message({"role": "tool", "content": "ok", "tool_call_id": "c1"})` 返回 `Message(role="tool", content="ok", tool_call_id="c1")`
  - **验证**：`python -c "from harness.messaging import dict_to_message; m = dict_to_message({'role': 'tool', 'content': 'ok', 'tool_call_id': 'c1'}); assert m.role == 'tool' and m.content == 'ok' and m.tool_call_id == 'c1'"`
- [ ] **AC-CONV-05**：`dict_to_message` — `content` 为 None 时默认空字符串
  - **验证**：`python -c "from harness.messaging import dict_to_message; assert dict_to_message({'role': 'assistant'}).content == ''"`
- [ ] **AC-CONV-06**：`message_to_dict` ↔ `dict_to_message` 往返一致性（三种 role）
  - **验证**：通过 `pytest tests/test_messaging.py`
- [ ] **AC-CONV-07**：`dict_to_message` 忽略 dict 中 `tool_calls` 等非标字段（不抛异常）
  - **验证**：`python -c "from harness.messaging import dict_to_message; dict_to_message({'role': 'assistant', 'content': 'hi', 'tool_calls': [{'id': 'c1'}]})"` 不报错

### 2.2 ToolDefinition 转换

- [ ] **AC-CONV-08**：`tool_definition_to_openai(ToolDefinition(name="read", description="Read file", parameters={...}))` 输出符合 OpenAI spec
  - **验证**：`python -c "from harness.interfaces.types import ToolDefinition; from harness.messaging import tool_definition_to_openai; td = ToolDefinition(name='read', description='Read file', parameters={'type': 'object'}); d = tool_definition_to_openai(td); assert d['type'] == 'function' and d['function']['name'] == 'read'"`
- [ ] **AC-CONV-09**：`tool_definitions_to_openai([])` 返回 `[]`
  - **验证**：`python -c "from harness.messaging import tool_definitions_to_openai; assert tool_definitions_to_openai([]) == []"`
- [ ] **AC-CONV-10**：`tool_definitions_to_openai([td1, td2])` 输出长度为 2，每个元素格式正确
  - **验证**：`python -c "from harness.interfaces.types import ToolDefinition; from harness.messaging import tool_definitions_to_openai; tds = [ToolDefinition(name='a'), ToolDefinition(name='b')]; result = tool_definitions_to_openai(tds); assert len(result) == 2; assert all('function' in r for r in result)"`

### 2.3 现有函数升级

- [ ] **AC-CONV-11**：`build_assistant_message` 接受 `Response` 正式类型参数
  - **验证**：`from harness.interfaces.types import Response; from harness.messaging import build_assistant_message; r = Response(text="hi"); build_assistant_message(r)` 不报错
- [ ] **AC-CONV-12**：`build_tool_result_message` 接受 `ToolCall` 正式类型参数
  - **验证**：`from harness.interfaces.types import ToolCall; from harness.messaging import build_tool_result_message; tc = ToolCall(id="c1"); build_tool_result_message(tc, "ok")` 不报错

---

## 三、行为保持验收

> **核心原则**：类型迁移后所有现有测试的断言逻辑不变且全部通过。

- [ ] **AC-BEH-01**：`tests/test_container.py` — 全部通过
  - **验证**：`python -m pytest tests/test_container.py -v`
- [ ] **AC-BEH-02**：`tests/test_exceptions.py` — 全部通过
  - **验证**：`python -m pytest tests/test_exceptions.py -v`
- [ ] **AC-BEH-03**：`tests/test_config.py` — 全部通过
  - **验证**：`python -m pytest tests/test_config.py -v`
- [ ] **AC-BEH-04**：`tests/test_orchestrator.py` — 全部通过
  - **验证**：`python -m pytest tests/test_orchestrator.py -v`
- [ ] **AC-BEH-05**：`tests/test_llm_adapter.py` — 全部通过
  - **验证**：`python -m pytest tests/test_llm_adapter.py -v`
- [ ] **AC-BEH-06**：`tests/test_black_box.py` — 全部通过
  - **验证**：`python -m pytest tests/test_black_box.py -v`
- [ ] **AC-BEH-07**：`tests/test_real_llm_trace.py` — 全部通过（如跳过则不计）
  - **验证**：`python -m pytest tests/test_real_llm_trace.py -v`
- [ ] **AC-BEH-08**：全量测试套件 — 0 失败
  - **验证**：`python -m pytest tests/ -v`

---

## 四、测试覆盖验收

### 4.1 编排器

- [ ] **AC-COV-01**：AC-ORCH-14 — 内层 tool_use 循环中 `ContextAssembler.assemble()` 调用次数为 0
  - **验证**：`python -m pytest tests/test_orchestrator.py -v -k "assemble"`
- [ ] **AC-COV-02**：AC-ORCH-14 — 外层循环每轮 `assemble()` 仅调用一次
  - **验证**：同上测试用例

### 4.2 配置加载器

- [ ] **AC-COV-03**：AC-EDGE-03 — 空文件 (0 bytes) 抛出 `ConfigParseError`
  - **验证**：`python -m pytest tests/test_config.py -v -k "empty"`
- [ ] **AC-COV-04**：AC-EDGE-04 — 纯注释 TOML（无 `[meta]`）validate 抛出 `ConfigValidationError`
  - **验证**：`python -m pytest tests/test_config.py -v -k "comment"`
- [ ] **AC-COV-05**：AC-EDGE-05 — 10MB+ TOML 文件加载不崩溃、不超时
  - **验证**：`python -m pytest tests/test_config.py -v -k "large"`

### 4.3 转换层

- [ ] **AC-COV-06**：`tests/test_messaging.py` 存在，含 ≥ 10 个测试用例
  - **验证**：`python -m pytest tests/test_messaging.py -v --collect-only | wc -l` ≥ 10
- [ ] **AC-COV-07**：转换层测试全部通过
  - **验证**：`python -m pytest tests/test_messaging.py -v`

---

## 五、向后兼容验收

### 5.1 DI 容器兼容性

- [ ] **AC-COMPAT-01**：8 个接口类型可作为 DI 注册 key
  - **验证脚本**：
    ```python
    from harness.core.container import DIContainer
    from harness.interfaces import (
        InputAdapter, GuideProvider, ContextAssembler,
        MemoryBackend, Sensor, Tool, ToolRegistry, MCPManager,
    )
    c = DIContainer()
    for t in [InputAdapter, GuideProvider, ContextAssembler,
              MemoryBackend, Sensor, Tool, ToolRegistry, MCPManager]:
        c.register(t, object())
    print("PASS")
    ```

### 5.2 Import 路径兼容性

- [ ] **AC-COMPAT-02**：`from harness.interfaces import InputAdapter` 路径不变
  - **验证**：`python -c "from harness.interfaces import InputAdapter, GuideProvider, ContextAssembler, MemoryBackend, Sensor, Tool, ToolRegistry, MCPManager"`
- [ ] **AC-COMPAT-03**：`from harness.core.orchestrator import LifecycleOrchestrator` 路径不变
  - **验证**：`python -c "from harness.core.orchestrator import LifecycleOrchestrator"`
- [ ] **AC-COMPAT-04**：`from harness.di import Harness` 路径不变
  - **验证**：`python -c "from harness.di import Harness"`

### 5.3 编排器可实例化

- [ ] **AC-COMPAT-05**：`LifecycleOrchestrator(container)` 可正常实例化（无需 call_llm）
  - **验证**：`python -c "from harness.core.container import DIContainer; from harness.core.orchestrator import LifecycleOrchestrator; c = DIContainer(); o = LifecycleOrchestrator(c); print('OK')"`

### 5.4 Re-export 路径一致性

- [ ] **AC-COMPAT-06**：`harness/core/llm_adapter.py`（re-export 包装）路径可用，指向规范实现
  - **验证**：`python -c "from harness.core.llm_adapter import MinimalLLMAdapter; from harness.adapters.llm_adapter import MinimalLLMAdapter as M2; assert MinimalLLMAdapter is M2, 'Re-export path broken'"`

---

## 六、代码质量验收

### 6.1 Dead code

- [ ] **AC-QUAL-01**：无 dead import（`test_llm_adapter.py` 中的 `_MinimalToolCall` / `_MinimalToolCallFunction` 已删除）
  - **验证**：`grep '_MinimalToolCall\|_MinimalToolCallFunction' tests/test_llm_adapter.py` → 空
- [ ] **AC-QUAL-02**：`harness/core/orchestrator.py` 中 `_normalize_*` 方法已删除
  - **验证**：`grep -c '_normalize_' harness/core/orchestrator.py` → `0`

### 6.2 Naming

- [ ] **AC-QUAL-03**：所有新代码使用正式类型名称（PascalCase），无 `_Minimal*` 前缀残留
  - **验证**：`grep -r '_Minimal' harness/core/orchestrator.py harness/adapters/llm_adapter.py harness/messaging/builder.py` → 空

### 6.3 Module boundary

- [ ] **AC-QUAL-04**：`harness/messaging/builder.py` 不 import 任何实现模块
  - **验证**：`grep 'from harness.core\|from harness.adapters' harness/messaging/builder.py` → 空
- [ ] **AC-QUAL-05**：`harness/core/orchestrator.py` 的 imports 不包含 `harness/core/types.py`
  - **验证**：`grep 'from .types import' harness/core/orchestrator.py` → 空

---

## 七、文档验收

- [ ] **AC-DOC-01**：`CORE_DEVELOPER_GUIDE.md` 中无 `_Minimal*` 代码示例
  - **验证**：`grep -c '_Minimal' CORE_DEVELOPER_GUIDE.md` → `0`
- [ ] **AC-DOC-02**：`CORE_DEVELOPER_GUIDE.md` 中 §4, §5, §7.2, §11 示例使用正式类型名
  - **验证**：人工抽查
- [ ] **AC-DOC-03**：`harness/core/types.py` 模块 docstring 明确标注 "DEPRECATED"
  - **验证**：`head -10 harness/core/types.py | grep -i deprecated`

---

## 八、SDD 一致性验收

- [ ] **AC-SDD-01**：正式类型数量 = 16（对照 `sdd/02-interfaces.md`）
  - **验证**：`python -c "from harness.interfaces.types import __all__; assert len(__all__) == 16"`
- [ ] **AC-SDD-02**：接口 Protocol 数量 = 8 + Hook 函数类型 = 9
  - **验证**：`python -c "from harness.interfaces import __all__; assert len(__all__) == 27, f'Expected 27 items (16 types + 9 interface names + 2 contexts), got {len(__all__)}'"`
- [ ] **AC-SDD-03**：所有测试中的类型使用与 SDD 字段定义一致（名称、类型、拼写）
  - **验证**：代码审查确认测试文件中无 SDD-broken 的类型引用
