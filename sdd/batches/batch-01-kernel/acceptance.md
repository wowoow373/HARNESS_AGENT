# batch-01 — Kernel 验收标准

> **验收原则**：每条标准必须可验证。验证方式可以是自动化测试、手动脚本或代码审查。
>
> **通过条件**：所有 `[ ]` 被勾选为 `[x]`。

---

## 一、功能验收

### 1.1 异常体系

- [ ] **AC-EX-01**：`HarnessError` 继承自 `Exception`
  - **验证**：`issubclass(HarnessError, Exception)` → True

- [ ] **AC-EX-02**：所有异常类形成正确的继承层次
  - **验证脚本**：
    ```python
    from harness.core.exceptions import *

    # Config 分支
    assert issubclass(ConfigNotFoundError, ConfigError)
    assert issubclass(ConfigParseError, ConfigError)
    assert issubclass(ConfigValidationError, ConfigError)
    assert issubclass(ConfigError, HarnessError)

    # Container 分支
    assert issubclass(DuplicateRegistrationError, ContainerError)
    assert issubclass(ComponentNotRegisteredError, ContainerError)
    assert issubclass(ContainerError, HarnessError)

    # Orchestrator 分支
    assert issubclass(OrchestratorError, HarnessError)
    ```

- [ ] **AC-EX-03**：所有异常可被 `HarnessError` 统一捕获
  - **验证脚本**：
    ```python
    all_exc = [
        ConfigNotFoundError, ConfigParseError, ConfigValidationError,
        DuplicateRegistrationError, ComponentNotRegisteredError,
        OrchestratorError,
    ]
    for exc_cls in all_exc:
        try:
            raise exc_cls("test message")
        except HarnessError:
            pass  # expected
        else:
            assert False, f"{exc_cls.__name__} not caught by HarnessError"
    ```

- [ ] **AC-EX-04**：异常消息被正确保留并可访问
  - **验证**：`str(HarnessError("hello"))` → `"hello"`

---

### 1.2 DIContainer

- [ ] **AC-DI-01**：`register(interface, instance)` 正常注册单个组件
  - **验证脚本**：
    ```python
    container = DIContainer()
    class IFoo: pass
    foo = object()
    container.register(IFoo, foo)
    assert container.is_registered(IFoo) == True
    assert container.resolve(IFoo) is foo
    ```

- [ ] **AC-DI-02**：`register()` 重复注册同一接口抛出 `DuplicateRegistrationError`
  - **验证脚本**：
    ```python
    container = DIContainer()
    class IFoo: pass
    container.register(IFoo, object())
    with pytest.raises(DuplicateRegistrationError):
        container.register(IFoo, object())
    ```

- [ ] **AC-DI-03**：`register()` 拒绝 `None` 实例，抛出 `ValueError`
  - **验证脚本**：
    ```python
    with pytest.raises(ValueError):
        container.register(IFoo, None)
    ```

- [ ] **AC-DI-04**：`register()` 拒绝非 `type` 的 interface 参数，抛出 `TypeError`
  - **验证脚本**：
    ```python
    with pytest.raises(TypeError):
        container.register("not_a_type", object())
    ```

- [ ] **AC-DI-05**：`resolve(interface)` 返回与注册时相同的实例（同一性）
  - **验证**：`container.resolve(IFoo) is foo` → True

- [ ] **AC-DI-06**：`resolve()` 未注册接口抛出 `ComponentNotRegisteredError`，消息含接口名
  - **验证脚本**：
    ```python
    class IBar: pass
    with pytest.raises(ComponentNotRegisteredError) as exc_info:
        container.resolve(IBar)
    assert "IBar" in str(exc_info.value)
    ```

- [ ] **AC-DI-07**：`resolve()` 拒绝非 `type` 参数，抛出 `TypeError`
  - **验证脚本**：
    ```python
    with pytest.raises(TypeError):
        container.resolve(123)
    ```

- [ ] **AC-DI-08**：`is_registered()` 正确区分已注册/未注册状态
  - **验证**：未注册返回 False，注册后返回 True

- [ ] **AC-DI-09**：`list_registered()` 返回注册表副本（修改不影响容器内部状态）
  - **验证脚本**：
    ```python
    reg = container.list_registered()
    reg.clear()
    # 容器内部状态不变
    assert container.is_registered(IFoo) == True
    ```

- [ ] **AC-DI-10**：多组件注册，共享同一个实例（MemoryBackend 共享场景）
  - **验证脚本**：
    ```python
    class IMemory: pass
    class IAssembler: pass
    class ISensor: pass

    memory = object()
    container.register(IMemory, memory)
    container.register(IAssembler, memory)  # 注意：同一实例注册到不同接口
    container.register(ISensor, memory)

    assert container.resolve(IMemory) is memory
    assert container.resolve(IAssembler) is memory
    assert container.resolve(ISensor) is memory
    ```

---

### 1.3 ConfigLoader

- [ ] **AC-CFG-01**：正常加载最小合法 TOML 文件
  - **输入**：
    ```toml
    [meta]
    name = "test"
    template = "coding-assistant"
    ```
  - **验证**：
    ```python
    config = loader.load(path)
    assert config.name == "test"
    assert config.template == "coding-assistant"
    assert config.description == ""      # 默认值
    assert config.version == "0.1.0"     # 默认值
    assert config.modules == {}          # 缺失时为空 dict
    ```

- [ ] **AC-CFG-02**：正常加载完整 TOML 文件（含 modules）
  - **输入**：
    ```toml
    [meta]
    name = "full"
    description = "A full config"
    template = "coding-assistant"
    version = "2.0.0"

    [modules]
    input_adapter = true
    guide_provider = false
    context_assembler = true
    mcp_manager = true
    sensor = true
    memory_backend = false
    ```
  - **验证**：所有字段值与 TOML 一致，modules 中 bool 值正确

- [ ] **AC-CFG-03**：文件不存在抛出 `ConfigNotFoundError`，消息含路径
  - **验证脚本**：
    ```python
    with pytest.raises(ConfigNotFoundError) as exc_info:
        loader.load("/nonexistent/path/config.toml")
    assert "/nonexistent/path" in str(exc_info.value)
    ```

- [ ] **AC-CFG-04**：TOML 语法错误抛出 `ConfigParseError`
  - **输入**：`[meta\nname = = broken[[`
  - **验证**：`pytest.raises(ConfigParseError)`

- [ ] **AC-CFG-05**：`validate()` 拒绝空 `name`
  - **验证脚本**：
    ```python
    config = ProfileConfig(name="", template="coding", description="", version="0.1")
    with pytest.raises(ConfigValidationError) as exc_info:
        loader.validate(config)
    assert "name" in str(exc_info.value).lower()
    ```

- [ ] **AC-CFG-06**：`validate()` 拒绝空 `template`
  - **验证脚本**：
    ```python
    config = ProfileConfig(name="test", template="", description="", version="0.1")
    with pytest.raises(ConfigValidationError) as exc_info:
        loader.validate(config)
    assert "template" in str(exc_info.value).lower()
    ```

- [ ] **AC-CFG-07**：`validate()` 拒绝 modules 中非 bool 值
  - **验证脚本**：
    ```python
    config = ProfileConfig(name="test", template="coding",
                           modules={"input_adapter": "yes"})  # 应为 bool
    with pytest.raises(ConfigValidationError) as exc_info:
        loader.validate(config)
    assert "input_adapter" in str(exc_info.value)
    ```

- [ ] **AC-CFG-08**：`validate()` 接受空 modules dict
  - **验证**：`loader.validate(ProfileConfig(name="t", template="c", modules={}))` 不抛异常

- [ ] **AC-CFG-09**：`load()` + `validate()` 完整工作流端到端正确
  - **验证**：创建临时 TOML → load → validate → 所有字段正确 → 清理

- [ ] **AC-CFG-10**：`ProfileConfig.raw` 保留原始 TOML 解析结果
  - **验证**：`config.raw` 是完整 TOML 解析后的 dict，包含所有段和键值

---

### 1.4 LifecycleOrchestrator

- [ ] **AC-ORCH-01**：最小组件场景（仅 InputAdapter）下 `_phase_init()` 正常完成
  - **验证脚本**：
    ```python
    container = DIContainer()
    class MockAdapter:
        def receive(self): return _MinimalUserRequest(text="hello")
        def send(self, r): pass
    container.register(InputAdapter, MockAdapter())
    orch = LifecycleOrchestrator(container)
    ctx = orch._phase_init()
    assert ctx is not None
    assert ctx.user_request.text == "hello"
    ```

- [ ] **AC-ORCH-02**：InputAdapter 缺失时 `_phase_init()` 抛出异常
  - **验证**：`container` 中无 InputAdapter → `orch._phase_init()` 抛出 `ComponentNotRegisteredError`

- [ ] **AC-ORCH-03**：可选组件（GuideProvider、MemoryBackend、ToolRegistry、Sensor）缺失时不阻塞流程
  - **验证**：所有可选组件缺失 → `_phase_init()`, `_phase_loop()`, `_phase_end()` 均不抛异常

- [ ] **AC-ORCH-04**：单轮对话（text 响应）正确执行
  - **场景**：用户输入 "hello" → LLM 返回 text 响应 → 用户收到响应
  - **验证**：
    ```python
    # InputAdapter.send() 被调用，传出 LLM 返回的 text
    adapter = container.resolve(InputAdapter)
    assert len(adapter.outputs) >= 1
    assert adapter.outputs[0] == "mock reply"
    ```

- [ ] **AC-ORCH-05**：多轮对话正确执行（两轮以上）
  - **场景**：用户依次输入 "hello", "what's up", ""（第三轮退出）
  - **验证**：
    ```python
    # 两轮对话都产生响应，第三轮空输入触发退出
    assert len(adapter.outputs) >= 2
    ```

- [ ] **AC-ORCH-06**：tool_use 循环正确处理（无 text，纯 tool_use → 再次 LLM → text）
  - **场景**：LLM 第一响应仅含 tool_use（无 text）→ ToolRegistry 执行 → tool result 追加到 messages → 再次调用 LLM → LLM 第二响应含 text → 用户收到
  - **验证脚本**：
    ```python
    # 使用 mock call_llm: 第1次返回 tool_use, 第2次返回 text
    call_count = [0]
    def two_phase_llm(msgs, tools):
        call_count[0] += 1
        if call_count[0] == 1:
            return _MinimalResponse(
                tool_uses=[_MinimalToolCall(id="c1", name="read",
                            arguments='{"path":"/x"}')],
                stop_reason="tool_use"
            )
        else:
            return _MinimalResponse(text="File says: hello", stop_reason="end_turn")

    # ... 注册组件并运行 ...
    orch._phase_loop(ctx)

    # 验证
    assert call_count[0] == 2  # LLM 被调用了 2 次（内层循环）
    assert len(tr.executed) == 1  # ToolRegistry 执行了 1 次
    assert orch._tool_call_records[0]["tool_name"] == "read"
    assert orch._tool_call_records[0]["error"] is None  # 执行成功
    # 最终用户收到了 text 响应
    ```

- [ ] **AC-ORCH-07**：text + tool_uses 共存正确处理
  - **场景**：LLM 单次响应同时包含 text 和 tool_uses → 先执行 tools → 同时 text 发给用户 → 跳出内层循环
  - **验证脚本**：
    ```python
    def coexistence_llm(msgs, tools):
        return _MinimalResponse(
            text="Let me check that file for you",
            tool_uses=[_MinimalToolCall(id="c1", name="read",
                        arguments='{"path":"/x"}')],
            stop_reason="end_turn"
        )
    # ... 运行 ...
    # tool 被执行了
    assert len(tr.executed) == 1
    # text 被发送给用户
    assert "Let me check" in str(adapter.outputs)
    # 两者都处理了，不是互斥的
    ```

- [ ] **AC-ORCH-08**：内层循环中 message 格式正确（OpenAI 兼容）
  - **验证**：tool_use 循环后检查 messages 列表中的消息格式
    ```python
    # 假设有一次 tool_use → tool_result 的循环
    # messages 应包含：
    # [..., assistant_msg_with_tool_calls, tool_result_msg]

    # assistant msg 格式检查
    assistant_msgs = [m for m in captured_messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant_msgs) >= 1
    tc = assistant_msgs[0]["tool_calls"][0]
    assert tc["type"] == "function"
    assert "id" in tc
    assert "name" in tc["function"]
    assert "arguments" in tc["function"]

    # tool result msg 格式检查
    tool_msgs = [m for m in captured_messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert "tool_call_id" in tool_msgs[0]
    assert "content" in tool_msgs[0]
    ```

- [ ] **AC-ORCH-09**：Tool 执行失败时错误信息完整保留（不丢失）
  - **场景**：ToolRegistry.execute() 返回 `success=False`
  - **验证脚本**：
    ```python
    class FailingTR:
        def list_tools(self): return []
        def execute(self, name, args):
            class TR:
                success = False
                content = None
                error = "Permission denied"
            return TR()

    # ... 运行 tool_use 循环 ...
    # tool_call_records 中记录了错误
    assert orch._tool_call_records[0]["error"] == "Permission denied"
    # tool result message 中的 content 应包含错误信息（让 LLM 看到）
    ```

- [ ] **AC-ORCH-10**：tool_use 的 JSON arguments 被正确 parse 后传给 ToolRegistry
  - **验证**：`_MinimalToolCall(arguments='{"path":"/x","mode":"r"}')` → ToolRegistry.execute() 收到的是 `{"path":"/x","mode":"r"}` 这个 dict，而不是 JSON string

- [ ] **AC-ORCH-11**：空输入正确触发退出
  - **输入**：`_MinimalUserRequest(text="")`
  - **验证**：`orch._should_exit(req)` → True

- [ ] **AC-ORCH-12**：退出关键词正确触发退出
  - **输入**：`/exit`, `/quit`, `/bye`
  - **验证**：每个关键词 → `_should_exit()` 返回 True

- [ ] **AC-ORCH-13**：metadata exit 标志正确触发退出
  - **输入**：`_MinimalUserRequest(text="anything", metadata={"exit": True})`
  - **验证**：`_should_exit()` → True

- [ ] **AC-ORCH-14**：内层循环不走 ContextAssembler.assemble()
  - **验证**：tool_use 连续场景中，ContextAssembler.assemble() 只在外层循环入口被调用一次
  - **实现方式**：spy 计数 → tool_use 循环期间 assemble 调用次数不增加

- [ ] **AC-ORCH-15**：`_phase_end()` 正确组装 Trajectory 并传给 Sensor
  - **验证**：
    ```python
    sensor = container.resolve(Sensor)
    assert sensor.received_trajectory is not None
    assert len(sensor.received_trajectory.history) > 0
    assert sensor.received_trajectory.execution_time > 0
    ```

- [ ] **AC-ORCH-16**：`_phase_end()` 后内部状态被清理
  - **验证**：
    ```python
    orch._phase_end()
    assert len(orch._history) == 0
    assert len(orch._tool_call_records) == 0
    ```

- [ ] **AC-ORCH-17**：异常时 `finally` 确保 `_phase_end()` 被调用
  - **验证**：
    ```python
    # 注入一个在 _phase_loop 中抛异常的组件
    # run() 之后 Sensor.sense() 仍被调用
    sensor = container.resolve(Sensor)
    assert sensor.called == True
    ```

---

### 1.5 Harness 装配入口

- [ ] **AC-HAR-01**：`Harness.from_container()` 校验 InputAdapter 已注册
  - **验证**：
    ```python
    container = DIContainer()
    with pytest.raises(ComponentNotRegisteredError):
        Harness.from_container(container)
    ```

- [ ] **AC-HAR-02**：`Harness.from_container()` 正常构造
  - **验证**：
    ```python
    container = DIContainer()
    container.register(InputAdapter, MockAdapter())
    harness = Harness.from_container(container)
    assert isinstance(harness, Harness)
    ```

- [ ] **AC-HAR-03**：`Harness.run()` 完整执行三阶段
  - **验证**：提供完整的 mock 组件集 → `harness.run()` → 不抛异常、完整执行

---

### 1.6 MinimalLLMAdapter

- [ ] **AC-LLM-01**：`__init__()` 正确处理 api_key 优先级（显式 > 环境变量 > 空字符串）
  - **验证脚本**：
    ```python
    # 显式传入优先
    a = MinimalLLMAdapter(api_key="sk-explicit")
    assert a.api_key == "sk-explicit"
    # 环境变量回退
    os.environ["OPENAI_API_KEY"] = "sk-env"
    a2 = MinimalLLMAdapter()
    assert a2.api_key == "sk-env"
    # 都为空不报错
    os.environ.pop("OPENAI_API_KEY", None)
    a3 = MinimalLLMAdapter()
    assert a3.api_key == ""
    ```

- [ ] **AC-LLM-02**：`base_url` 尾部斜杠被正确处理
  - **验证**：`MinimalLLMAdapter(base_url="http://localhost:11434/v1/")._endpoint` → `"http://localhost:11434/v1/chat/completions"`

- [ ] **AC-LLM-03**：`_build_request_body()` 正确构建 OpenAI 格式请求体
  - **验证**：
    ```python
    body = adapter._build_request_body(
        [{"role": "user", "content": "hi"}],
        tools=None
    )
    assert body["model"] == "gpt-4o"
    assert "tools" not in body  # None 时不包含
    assert body["max_tokens"] == 4096

    # 含 tools
    body2 = adapter._build_request_body(
        [{"role": "user", "content": "read"}],
        tools=[{"type": "function", "function": {"name": "read", ...}}]
    )
    assert "tools" in body2
    ```

- [ ] **AC-LLM-04**：`_parse_response()` 正确解析纯 text 响应
  - **验证**：
    ```python
    r = adapter._parse_response({
        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]
    })
    assert r.text == "Hello"
    assert r.tool_uses == []
    assert r.stop_reason == "end_turn"
    ```

- [ ] **AC-LLM-05**：`_parse_response()` 正确解析纯 tool_use 响应
  - **验证**：
    ```python
    r = adapter._parse_response({
        "choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read", "arguments": '{"path":"/x"}'}}]
        }, "finish_reason": "tool_calls"}]
    })
    assert r.text is None
    assert len(r.tool_uses) == 1
    assert r.tool_uses[0].name == "read"
    assert r.stop_reason == "tool_use"
    ```

- [ ] **AC-LLM-06**：`_parse_response()` 正确解析 text + tool_uses 共存响应
  - **验证**：单次响应中 `text` 和 `tool_uses` 同时非空
    ```python
    r = adapter._parse_response({
        "choices": [{"message": {
            "content": "Let me check",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read", "arguments": '{}'}}]
        }, "finish_reason": "stop"}]
    })
    assert r.text == "Let me check"
    assert len(r.tool_uses) == 1
    ```

- [ ] **AC-LLM-07**：`_parse_response()` 对无效响应抛出 `OrchestratorError`
  - **验证**：
    ```python
    with pytest.raises(OrchestratorError):
        adapter._parse_response({"no_choices": []})
    with pytest.raises(OrchestratorError):
        adapter._parse_response({"choices": []})
    ```

- [ ] **AC-LLM-08**：`__call__` 签名匹配 `call_llm` 约定
  - **验证**：`adapter([{"role": "user", "content": "hi"}])` 或 `adapter(messages, tools)` — 可用于 `LifecycleOrchestrator(container, call_llm=adapter)`

- [ ] **AC-LLM-09**：`finish_reason` 映射正确
  - **验证**：
    ```python
    # "stop" → "end_turn"
    # "tool_calls" → "tool_use"
    # "length" → "end_turn"
    # 未知值 → 原样保留
    ```

- [ ] **AC-LLM-10**：HTTP 错误统一包装为 `OrchestratorError`
  - **验证**（使用 mock）：
    ```python
    with mock.patch("urllib.request.urlopen") as m:
        m.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid_key"}')
        )
        with pytest.raises(OrchestratorError) as exc:
            adapter([{"role": "user", "content": "hi"}])
        assert "401" in str(exc.value)
    ```

- [ ] **AC-LLM-11**：网络不可达统一包装为 `OrchestratorError`
  - **验证**（使用 mock）：
    ```python
    with mock.patch("urllib.request.urlopen") as m:
        m.side_effect = urllib.error.URLError("connection refused")
        with pytest.raises(OrchestratorError) as exc:
            adapter([{"role": "user", "content": "hi"}])
        assert "unreachable" in str(exc.value).lower()
    ```

- [ ] **AC-LLM-12**：零外部依赖（仅标准库）
  - **验证**：`grep -r "import\|from" harness/core/llm_adapter.py` 输出中无第三方库（无 `openai`、`requests`、`httpx` 等）
  - 允许的 import：`os`, `json`, `urllib.request`, `urllib.error`, `typing`

- [ ] **AC-LLM-13**：`__call__` 返回的是 `_MinimalResponse` 类型
  - **验证**：`isinstance(adapter([...]), _MinimalResponse)` → True

- [ ] **AC-LLM-14**（手动）：真实 API 连通
  - **前置条件**：设置 `OPENAI_API_KEY` 环境变量
  - **验证**：
    ```bash
    python -c "
    from harness.core.llm_adapter import MinimalLLMAdapter
    a = MinimalLLMAdapter(model='gpt-4o-mini')
    r = a([{'role':'user','content':'Say hi in one word'}])
    assert r.text is not None
    print(f'OK: {r.text}')
    "
    ```

---

## 二、集成验收

### 2.1 完整装配场景

- [ ] **AC-INT-01**：全部组件注册后的完整生命周期
  - **场景**：
    ```
    DIContainer 注册:
      - InputAdapter (mock)
      - GuideProvider (mock)
      - MemoryBackend (mock)
      - ContextAssembler (mock)
      - ToolRegistry (mock)
      - Sensor (mock)

    Harness.from_container() → run()
    ```
  - **预期**：三阶段全部执行，所有 mock 的预期方法被调用

- [ ] **AC-INT-02**：共享 MemoryBackend 实例场景
  - **场景**：同一个 MemoryBackend 实例被 ContextAssembler 和 Sensor 各自通过 DI 容器获取
  - **验证**：
    ```python
    memory = container.resolve(MemoryBackend)
    assembler = container.resolve(ContextAssembler)
    sensor = container.resolve(Sensor)
    # 通过构造函数注入，两者持有同一个 memory 引用
    ```

---

## 三、边界条件与错误处理

- [ ] **AC-EDGE-01**：DIContainer 初始为空，`list_registered()` 返回 `{}`
- [ ] **AC-EDGE-02**：DIContainer 初始为空，`is_registered(AnyType)` 返回 `False`
- [ ] **AC-EDGE-03**：ConfigLoader 处理空文件（0 字节），抛出 `ConfigParseError`
- [ ] **AC-EDGE-04**：ConfigLoader 处理仅有注释的 TOML 文件，抛出 `ConfigValidationError`（缺少 `[meta]`）
- [ ] **AC-EDGE-05**：ConfigLoader 处理超大 TOML 文件（10MB+），正常解析
- [ ] **AC-EDGE-06**：LifecycleOrchestrator.call_llm 为 None 时，`_phase_loop` 不崩溃，跳出内层循环
- [ ] **AC-EDGE-07**：ToolRegistry 未注册时，tool_use 响应不崩溃（跳过工具执行，记录 WARNING）
- [ ] **AC-EDGE-08**：Sensor 未注册时，`_phase_end()` 不崩溃（跳过 sense 调用）
- [ ] **AC-EDGE-09**：多个 Tool 串行执行时，每个 Tool 独立记录执行结果
- [ ] **AC-EDGE-10**：`_should_exit()` 中 text 为仅空白字符（空格、tab）时，视为空输入并退出
  - **设计决策**：仅空白字符 = 空输入，触发退出

---

## 四、代码质量验收

### 4.1 命名规范

对照 `sdd/05-conventions.md`：

- [ ] **AC-QA-01**：所有类名使用 PascalCase
  - **检查**：`grep -r "^class " harness/` 输出中所有类名符合 PascalCase

- [ ] **AC-QA-02**：所有函数/方法名使用 snake_case
  - **检查**：`grep -r "def " harness/` 输出中所有函数名符合 snake_case

- [ ] **AC-QA-03**：所有常量使用 UPPER_SNAKE_CASE
  - **检查**：如有全局常量，符合 UPPER_SNAKE_CASE

- [ ] **AC-QA-04**：私有成员前缀 `_`
  - **检查**：编排器内部方法（`_phase_init`, `_phase_loop`, `_phase_end` 等）前缀 `_`

### 4.2 类型标注

- [ ] **AC-QA-05**：所有公开方法的参数和返回值有类型标注
  - **检查清单**：
    - `DIContainer.register(interface: type, instance: Any) → None`
    - `DIContainer.resolve(interface: type) → Any`
    - `DIContainer.is_registered(interface: type) → bool`
    - `DIContainer.list_registered() → Dict[type, Any]`
    - `ConfigLoader.load(path: str) → ProfileConfig`
    - `ConfigLoader.validate(config: ProfileConfig) → None`
    - `LifecycleOrchestrator.__init__(container: DIContainer, call_llm: Optional[Callable]) → None`
    - `LifecycleOrchestrator.run() → None`
    - `Harness.from_container(container: DIContainer, call_llm: Optional[Callable] = None) → Harness`
    - `Harness.run() → None`

- [ ] **AC-QA-06**：所有 dataclass 字段有类型标注
  - **检查**：`ProfileConfig` 和编排器内部数据结构的每个字段有类型标注

### 4.3 文档

- [ ] **AC-QA-07**：所有公开类有 docstring
- [ ] **AC-QA-08**：所有公开方法有 docstring（含参数和返回值描述）
- [ ] **AC-QA-09**：所有自定义异常类有 docstring 说明使用场景

### 4.4 模块边界

- [ ] **AC-QA-10**：`harness/core/` 不 import `harness/components/`
  - **检查**：`grep -r "from harness.components" harness/core/` 无输出
  - **注**：batch-01 时 `components/` 还不存在，此条确保不引用尚未实现的模块

- [ ] **AC-QA-11**：`harness/core/` 不 import `harness/interfaces/`
  - **注**：batch-02 才创建 interfaces/，batch-01 的编排器使用内部最小数据结构
  - **检查**：编排器不依赖任何外部接口定义

---

## 五、测试覆盖验收

### 5.1 测试文件覆盖

- [ ] **AC-TEST-01**：`tests/test_exceptions.py` 存在且通过
  - 测试类数：≥ 2（`TestHarnessError`, `TestExceptionHierarchy`）
  - 测试方法数：≥ 6

- [ ] **AC-TEST-02**：`tests/test_container.py` 存在且通过
  - 测试类数：≥ 4（Registration, Resolution, Helpers, Integration）
  - 测试方法数：≥ 12

- [ ] **AC-TEST-03**：`tests/test_config.py` 存在且通过
  - 测试类数：≥ 4（ProfileConfig, Load, Validate, Integration）
  - 测试方法数：≥ 10

- [ ] **AC-TEST-04**：`tests/test_orchestrator.py` 存在且通过
  - 测试类数：≥ 7（Init, ResolveOptional, PhaseInit, PhaseLoop, PhaseEnd, Run, ShouldExit）
  - 测试方法数：≥ 18

- [ ] **AC-TEST-04b**：`tests/test_llm_adapter.py` 存在且通过
  - 测试类数：≥ 5（Init, BuildRequestBody, ParseResponse, Call, ErrorHandling）
  - 测试方法数：≥ 15
  - 使用 mock 替代真实 HTTP 调用

### 5.2 测试结果

- [ ] **AC-TEST-05**：`pytest tests/ -v` 全部通过，0 failure，0 error
- [ ] **AC-TEST-06**：测试覆盖所有公开接口方法（每个方法至少 1 个测试）

---

## 六、快速验证脚本

以下脚本可一次性验证 batch-01 的核心功能：

```python
#!/usr/bin/env python3
"""batch-01 快速验收脚本"""

import sys
import tempfile
import os
import time

# --- 1. 异常体系 ---
from harness.core.exceptions import (
    HarnessError, ConfigError, ConfigNotFoundError,
    ConfigParseError, ConfigValidationError,
    ContainerError, DuplicateRegistrationError,
    ComponentNotRegisteredError, OrchestratorError,
)

assert issubclass(HarnessError, Exception), "FAIL: HarnessError not Exception subclass"
assert issubclass(ComponentNotRegisteredError, HarnessError), "FAIL: hierarchy broken"
print("✓ 异常体系正确")

# --- 2. DIContainer ---
from harness.core.container import DIContainer

container = DIContainer()
assert container.list_registered() == {}
assert container.is_registered(object) == False

class IFoo: pass
class IBar: pass
foo, bar = object(), object()
container.register(IFoo, foo)
container.register(IBar, bar)

assert container.resolve(IFoo) is foo
assert container.resolve(IBar) is bar
assert len(container.list_registered()) == 2

# 错误路径
try:
    container.register(IFoo, object())
    assert False, "Should raise DuplicateRegistrationError"
except DuplicateRegistrationError:
    pass

try:
    container.resolve(str)
    assert False, "Should raise ComponentNotRegisteredError"
except ComponentNotRegisteredError:
    pass

# list_registered 返回副本
reg = container.list_registered()
reg.clear()
assert container.is_registered(IFoo) == True

print("✓ DIContainer 正确")

# --- 3. ConfigLoader ---
from harness.core.config import ConfigLoader, ProfileConfig

loader = ConfigLoader()

toml_str = """
[meta]
name = "test-agent"
template = "coding-assistant"
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
    f.write(toml_str); tmp = f.name

try:
    config = loader.load(tmp)
    loader.validate(config)
    assert config.name == "test-agent"
    assert config.template == "coding-assistant"
    assert config.modules == {}
finally:
    os.unlink(tmp)

try:
    loader.load("/nonexistent/file.toml")
    assert False
except ConfigNotFoundError:
    pass

print("✓ ConfigLoader 正确")

# --- 4. LifecycleOrchestrator ---
from harness.core.orchestrator import (
    LifecycleOrchestrator, _MinimalUserRequest,
    _MinimalGuidesBundle, _MinimalResponse,
)

container2 = DIContainer()

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
    def send(self, resp):
        self.outputs.append(resp.text)

class MockSensor:
    def __init__(self):
        self.called = False
        self.traj = None
    def sense(self, traj):
        self.called = True
        self.traj = traj

class MockAssembler:
    def assemble(self, ctx):
        return [{"role": "user", "content": ctx.user_request.text}]

container2.register(InputAdapter, MockAdapter())
container2.register(ContextAssembler, MockAssembler())
container2.register(Sensor, MockSensor())

def mock_llm(msgs, tools):
    return _MinimalResponse(text="mock reply", stop_reason="end_turn")

orch = LifecycleOrchestrator(container2, call_llm=mock_llm)
orch._cached_guides = _MinimalGuidesBundle()
orch._cached_tools = []

orch.run()

adapter = container2.resolve(InputAdapter)
sensor = container2.resolve(Sensor)
assert len(adapter.outputs) >= 1
assert sensor.called == True

print("✓ LifecycleOrchestrator 正确")

# --- 5. Harness ---
from harness.di import Harness

container3 = DIContainer()
try:
    Harness.from_container(container3)
    assert False
except ComponentNotRegisteredError:
    pass

class Adapter2:
    def receive(self): return _MinimalUserRequest(text="hi")
    def send(self, r): pass

container3.register(InputAdapter, Adapter2())
harness = Harness.from_container(container3, call_llm=mock_llm)
assert harness is not None

print("✓ Harness 装配正确")
print("\n" + "="*50)
print("batch-01 验收全部通过！")
print("="*50)
```

---

## 七、验收完成签名

| 检查项 | 状态 | 检查人 | 日期 |
|--------|------|--------|------|
| 功能验收 (1.1–1.5) | [ ] | | |
| 集成验收 (2.1) | [ ] | | |
| 边界条件 (3.1) | [ ] | | |
| 代码质量 (4.1–4.4) | [ ] | | |
| 测试覆盖 (5.1–5.2) | [ ] | | |
| 快速验证脚本 | [ ] | | |

**通过条件**：所有检查项状态为 `[x]`。
