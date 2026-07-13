# batch-08: InputAdapter — 任务清单

> 按顺序逐条执行，完成后勾选。

---

## T1. 创建组件目录和 `__init__.py`

- [ ] 创建 `harness/components/input_adapter/` 目录
- [ ] 创建 `harness/components/input_adapter/__init__.py`，导出 `CliAdapter`

## T2. 实现 `CliAdapter`

- [ ] 创建 `harness/components/input_adapter/cli_adapter.py`
- [ ] 实现 `CliAdapter.__init__(self, session_id: Optional[str] = None)`
  - 自动生成 session_id（基于时间戳）
  - 设置默认 prompt `"> "`
- [ ] 实现 `CliAdapter.receive(self) -> UserRequest`
  - 打印提示符到 stdout
  - 从 stdin 阻塞读取一行
  - 去除首尾空白
  - 构造并返回 UserRequest（含 session_id）
- [ ] 实现 `CliAdapter.send(self, response: Response) -> None`
  - 若 response.text 非空：print 到 stdout
  - 无 text 时：不输出（内部工具循环场景）
- [ ] 添加完整 docstring

## T3. 编写单元测试

- [ ] 创建 `tests/test_input_adapter.py`
- [ ] 测试 1：`receive()` 构造正确的 UserRequest（mock stdin）
- [ ] 测试 2：`receive()` 返回的 UserRequest 包含正确的 session_id
- [ ] 测试 3：`receive()` 对空输入返回 `text=""` 的 UserRequest
- [ ] 测试 4：`send()` 将 response.text 输出到 stdout（mock stdout）
- [ ] 测试 5：`send()` 对仅有 tool_uses 的 Response 不输出文本
- [ ] 测试 6：自定义 session_id 被正确使用
- [ ] 测试 7：Protocol conformance — `isinstance(cli_adapter, InputAdapter)` 为 True
- [ ] 测试 8：自定义 prompt 被正确显示

## T4. 端到端集成测试

- [ ] 创建/扩展集成测试，验证 CliAdapter 在编排器中的完整流程
- [ ] 验证：编排器 Phase 1 通过 CliAdapter.receive() 获取 UserRequest
- [ ] 验证：编排器 Phase 2 通过 CliAdapter.send() 输出 LLM 响应
- [ ] 验证：空输入触发会话退出

## T5. 代码对齐校验

- [ ] 确认 `harness/interfaces/input_adapter.py` 签名未被修改
- [ ] 确认 `CliAdapter` 符合 `InputAdapter` Protocol（无需显式继承）
- [ ] 运行全量测试确认无回归
