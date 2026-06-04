# batch-08: InputAdapter — 验收标准

> 实现完成后逐条对照确认。全部通过即 batch-08 完成。

---

## 一、功能验收

- [ ] `CliAdapter` 可通过无参构造函数创建（session_id 自动生成）
- [ ] `CliAdapter` 符合 `InputAdapter` Protocol（`isinstance(cli_adapter, InputAdapter)` 为 True）
- [ ] `receive()` 从 stdin 读取一行并返回带正确 session_id 的 UserRequest
- [ ] `receive()` 在空输入时返回 `text=""` 的 UserRequest（编排器识别为退出信号）
- [ ] `receive()` 在用户输入 `/exit` 时返回 `text="/exit"` 的 UserRequest
- [ ] `send()` 将 response.text 正确输出到 stdout
- [ ] 当 Response 仅有 tool_uses 无 text 时，`send()` 不输出文本
- [ ] 自定义 prompt 在 `receive()` 显示正确
- [ ] 自定义 session_id 被正确保留

## 二、集成验收

- [ ] 编排器 Phase 1 通过 `CliAdapter.receive()` 获取用户输入
- [ ] 编排器 Phase 2 通过 `CliAdapter.send()` 输出 LLM 文本响应
- [ ] 空输入正确触发编排器退出流程
- [ ] `/exit` 输入正确触发编排器退出流程

## 三、代码质量

- [ ] 所有公开方法有完整类型标注
- [ ] 所有公开类有 docstring
- [ ] 符合 `05-conventions.md` 命名和结构规范
- [ ] `harness/components/input_adapter/` 不 import 任何其他 `harness/components/` 中的具体实现（仅 import 接口类型）
- [ ] 相关测试全部通过
