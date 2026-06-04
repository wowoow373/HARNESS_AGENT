# batch-11: Event-Driven Adapter — 验收标准

> 实现完成后逐条对照确认。全部通过即 batch-11 完成。

---

## 一、事件类型验收

- [ ] `ThinkingEvent` 可正常实例化，`content` 字段正确
- [ ] `ToolCallEvent` 可正常实例化，`call_id` / `tool_name` / `arguments` 字段正确
- [ ] `ToolResultEvent` 可正常实例化，`success` / `result` / `error` / `duration_ms` 字段正确
- [ ] `TextEvent` 可正常实例化，`content` 字段正确
- [ ] `StopEvent` 可正常实例化，`stop_reason` 字段正确
- [ ] `AdapterEvent = Union[...]` 类型别名包含全部 5 种事件
- [ ] 所有事件类型在 `harness/interfaces/__init__.py` 中正确导出

## 二、接口协议验收

- [ ] `InputAdapter` Protocol 的 `send()` 签名变为 `send(self, event: AdapterEvent) -> None`
- [ ] `receive()` 签名不变
- [ ] `isinstance(CliAdapter(), InputAdapter)` 为 `True`（Protocol conformance）
- [ ] `InputAdapter` 在 `harness/interfaces/__init__.py` 中正确导出

## 三、CliAdapter 验收

### 事件分发

- [ ] `send(TextEvent(content="hello"))` → 输出 `"hello"` 到 **stdout**
- [ ] `send(TextEvent(content=""))` → 不输出（空文本）
- [ ] `send(ThinkingEvent(content="推理..."))` → debug=True 时输出到 **stderr**，debug=False 时不输出
- [ ] `send(ToolCallEvent(call_id="1", tool_name="read_file", arguments={"path": "/x"}))` → 输出到 **stderr**，含工具名和参数摘要
- [ ] `send(ToolResultEvent(call_id="1", tool_name="read_file", success=True, result="content", duration_ms=12.5))` → 输出到 **stderr**，含 ✅ 标记和耗时
- [ ] `send(ToolResultEvent(call_id="1", tool_name="read_file", success=False, error="not found", duration_ms=5.0))` → 输出到 **stderr**，含 ❌ 标记和错误
- [ ] `send(StopEvent(stop_reason="end_turn"))` → no-op（不输出任何内容）

### 属性

- [ ] `debug` 属性默认 `False`，可通过 setter 设为 `True`

### 参数/结果摘要

- [ ] `_summarize_args("read_file", {"file_path": "/a/b"})` → 显示文件路径
- [ ] `_summarize_args("shell", {"command": "ls -la"})` → 显示命令
- [ ] `_summarize_result("a" * 200)` → 截断到 ≤120 字符
- [ ] `_summarize_result(None)` → `"null"`

## 四、编排器验收

- [ ] 编排器在内层循环中收到 `response.thinking` 非空时推送 `ThinkingEvent`
- [ ] 编排器在 tool_use 执行前推送 `ToolCallEvent`
- [ ] 编排器在 tool 执行完成后推送 `ToolResultEvent`
- [ ] 编排器在 tool 参数 JSON 解析失败时推送 `ToolCallEvent` + `ToolResultEvent(error=...)`
- [ ] 编排器在纯文本响应时推送 `TextEvent` + `StopEvent`
- [ ] 编排器在空响应时推送 `StopEvent(stop_reason="empty_response")`
- [ ] 编排器不再包含 `_summarize_args()` / `_summarize_result()` 方法
- [ ] 编排器在内层循环中不再使用 `logger.info("🔧 ...")` 输出工具调用（改为 `adapter.send()`）
- [ ] `logger` 调用仅保留纯开发调试用途（如 `logger.debug(...)` / `logger.error(...)`）

## 五、前后台分离验收（核心需求）

- [ ] 对话文本（`TextEvent`）→ stdout
- [ ] 工具调用（`ToolCallEvent` / `ToolResultEvent`）→ stderr
- [ ] 思考过程（`ThinkingEvent`）→ stderr（debug 模式下）
- [ ] 用户可通过 `2>bg.log` 将后台输出重定向到文件
- [ ] 用户可通过 `2>/dev/null` 完全静默后台输出

## 六、代码质量

- [ ] 所有新增/修改的公开方法有完整类型标注
- [ ] 所有新增 dataclass 有 docstring
- [ ] 符合 `sdd/05-conventions.md` 命名和结构规范
- [ ] 事件类型不引入任何外部依赖
- [ ] 全量测试通过（`python -m pytest tests/ -v`）

## 七、向后兼容性

虽 `send()` 签名变更属于破坏性变更，但：
- [ ] 所有框架内 `InputAdapter` 调用者已更新
- [ ] 所有测试 mock adapter 已更新
- [ ] 使用 `InputAdapter` Protocol 的新实现只需实现 `send(self, event)` 即可满足接口
