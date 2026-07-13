# batch-07: Sensor — 任务清单

> 按顺序逐条执行，完成后勾选。

---

## T1. 创建组件目录和 `__init__.py`

- [ ] 创建 `harness/components/sensor/` 目录
- [ ] 创建 `harness/components/sensor/__init__.py`，导出 `LoggingSensor`

## T2. 实现 `LoggingSensor`

- [ ] 创建 `harness/components/sensor/logging_sensor.py`
- [ ] 实现 `LoggingSensor.__init__(self, memory: MemoryBackend)`
- [ ] 实现 `LoggingSensor.sense(self, trajectory: Trajectory) -> None`
  - 从 Trajectory 中提取关键信息
  - 构造结构化的 value 字典
  - 调用 `self.memory.write(key, value, namespace="episodic")`
  - 错误处理：写入失败记录 WARNING，不抛异常
- [ ] 添加完整 docstring
- [ ] 添加日志记录（INFO: sense 调用、DEBUG: 写入成功、WARNING: 写入失败）

## T3. 编写单元测试

- [ ] 创建 `tests/test_sensor.py`
- [ ] 测试 1：`sense()` 成功写入 episodic 命名空间
- [ ] 测试 2：`sense()` 写入的 value 包含所有必需字段
- [ ] 测试 3：MemoryBackend 注入 — 通过构造函数接收
- [ ] 测试 4：Protocol conformance — `isinstance(logging_sensor, Sensor)` 为 True
- [ ] 测试 5：空 Trajectory（关键字段缺失）时正常降级，不抛异常
- [ ] 测试 6：MemoryBackend.write() 抛异常时 LoggingSensor 捕获并记录 WARNING
- [ ] 测试 7：多次 `sense()` 调用产生不同 key（基于 session_id）

## T4. 端到端集成测试

- [ ] 创建/扩展集成测试，验证 Sensor 在编排器 Phase 3 中被正常调用
- [ ] 验证：编排器 run() 结束后 MemoryBackend 中有对应的 episodic 记录
- [ ] 验证：下一会话初始化时能从 MemoryBackend 检索到上一会话写入的记忆

## T5. 代码对齐校验

- [ ] 确认 `harness/interfaces/sensor.py` 签名未被修改
- [ ] 确认 `LoggingSensor` 符合 `Sensor` Protocol（无需显式继承）
- [ ] 运行全量测试确认无回归
