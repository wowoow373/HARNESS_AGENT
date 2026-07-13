# batch-11: Event-Driven Adapter — 任务清单

> 按顺序逐条执行，完成后勾选。

---

## T1. 新增事件类型到 `harness/interfaces/types.py`

- [ ] 添加 `ThinkingEvent` dataclass（`content: str`）
- [ ] 添加 `ToolCallEvent` dataclass（`call_id`, `tool_name`, `arguments`）
- [ ] 添加 `ToolResultEvent` dataclass（`call_id`, `tool_name`, `success`, `result`, `error`, `duration_ms`）
- [ ] 添加 `TextEvent` dataclass（`content: str`）
- [ ] 添加 `StopEvent` dataclass（`stop_reason: str`）
- [ ] 添加 `AdapterEvent = Union[ThinkingEvent, ToolCallEvent, ToolResultEvent, TextEvent, StopEvent]`
- [ ] 更新 `__all__` 列表，加入所有新类型

## T2. 更新 `harness/interfaces/input_adapter.py`

- [ ] 修改 import：`Response` → `AdapterEvent`
- [ ] 修改 `send()` 签名：`response: Response` → `event: AdapterEvent`
- [ ] 更新 `send()` 的 docstring，描述事件分发
- [ ] 保持 `receive()` 不变

## T3. 更新 `harness/interfaces/__init__.py`

- [ ] 从 `.types` 导入所有新事件类型
- [ ] 更新 `__all__` 列表

## T4. 重写 `harness/components/input_adapter/cli_adapter.py`

- [ ] 更新 import：不再 import `Response`，改 import 所有事件类型
- [ ] 重写 `send()` 方法：`isinstance` 分发所有 5 种事件
  - `TextEvent` → `print(content)` 到 stdout
  - `ThinkingEvent` → debug 模式时 `print` 到 stderr
  - `ToolCallEvent` → `print` 到 stderr（含参数摘要）
  - `ToolResultEvent` → `print` 到 stderr（含结果摘要/错误）
  - `StopEvent` → no-op
- [ ] 从 `orchestrator.py` 迁移 `_summarize_args()` 和 `_summarize_result()` 方法到 CliAdapter
- [ ] 添加 `debug` 属性（默认 `False`）
- [ ] 更新模块 docstring

## T5. 修改 `harness/core/orchestrator.py`

- [ ] 新增 import：所有事件类型
- [ ] 内层循环中，收到 `response.thinking` 非空时推送 `ThinkingEvent`
- [ ] 内层循环中，每个 tool_use 执行前推送 `ToolCallEvent`
- [ ] 内层循环中，每个 tool 执行完成后推送 `ToolResultEvent`（替换所有 `logger.info/warning("🔧 ...")` 调用）
- [ ] 内层循环中，`response.text` 非空时推送 `TextEvent` + `StopEvent`（替换 `adapter.send(response)`）
- [ ] 空响应时推送 `StopEvent(stop_reason="empty_response")`
- [ ] 移除 `_summarize_args()` 和 `_summarize_result()` 静态方法（已迁移到 CliAdapter）
- [ ] 更新 `_phase_loop()` 的 docstring

## T6. 更新所有测试文件的 mock adapter

- [ ] `tests/test_input_adapter.py` — 更新 `_make_response` helper 和所有 `send()` 调用
- [ ] `tests/test_orchestrator.py` — 更新所有 `def send(self, r)` mock
- [ ] `tests/test_black_box.py` — 更新所有 mock adapter 的 `send()` 签名
- [ ] `tests/test_e2e_assembly.py` — 更新 `_make_mock_input_adapter`
- [ ] `tests/test_e2e_tool_flow.py` — 更新 mock adapters
- [ ] `tests/test_hooks.py` — 更新 mock adapters
- [ ] `tests/test_md_memory.py` — 更新 mock adapter
- [ ] `tests/test_guide_provider.py` — 更新 mock adapters
- [ ] `tests/test_real_llm_trace.py` — 更新 mock adapters
- [ ] `tests/test_e2e_sensor_adapter.py` — 更新 mock adapters

## T7. 运行全量测试

- [ ] 运行 `python -m pytest tests/ -v` 确认所有测试通过
- [ ] 修复所有回归问题

## T8. 端到端验证

- [ ] `echo "hello" | timeout 5 python main.py run --config agents/coding-assistant/harness.yaml` 确认不崩溃（即使无 LLM）
- [ ] 确认工具事件正确输出到 stderr

## T9. 更新 SDD 文档

- [ ] 更新 `sdd/04-roadmap.md` 加入 batch-11
- [ ] 更新 `sdd/02-interfaces.md`（如涉及接口变更）
