# batch-07: Sensor — 验收标准

> 实现完成后逐条对照确认。全部通过即 batch-07 完成。

---

## 一、功能验收

- [ ] `LoggingSensor` 可通过构造注入 `MemoryBackend` 实例创建
- [ ] `LoggingSensor` 符合 `Sensor` Protocol（`isinstance(logging_sensor, Sensor)` 为 True）
- [ ] `sense(trajectory)` 被调用后，MemoryBackend 的 `episodic` 命名空间中存在对应记录
- [ ] 写入的 value 包含：`session_id`、`timestamp`、`user_request`、`final_output`、`execution_time`、`message_count`、`tool_call_count`、`tool_calls_summary`、`history_excerpt`
- [ ] 当 Trajectory 的关键字段为空/缺失时，`sense()` 正常降级不抛异常
- [ ] 当 MemoryBackend.write() 失败时，LoggingSensor 捕获异常并记录 WARNING 日志，不向上传播

## 二、集成验收

- [ ] 编排器 Phase 3 中 `Sensor.sense()` 被正确调用
- [ ] 编排器 run() 完整执行后，MemoryBackend 中存在 `episodic` 命名空间的轨迹记录
- [ ] 下一会话初始化时，框架能在 `episodic` 中检索到上一会话写入的记忆

## 三、代码质量

- [ ] 所有公开方法有完整类型标注
- [ ] 所有公开类有 docstring
- [ ] 符合 `05-conventions.md` 命名和结构规范
- [ ] `harness/components/sensor/` 不 import 任何其他 `harness/components/` 中的具体实现（仅 import MemoryBackend 接口）
- [ ] 相关测试全部通过
