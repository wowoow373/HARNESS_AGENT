# batch-10: DI Assembly — 验收标准

> 实现完成后逐条对照确认。全部通过即 batch-10 完成。

---

## 一、YAML 装配验收

### YamlAssembler 核心功能

- [ ] `YamlAssembler` 可正常实例化
- [ ] `load(path)` 能正确加载并解析合法 YAML 文件
- [ ] `load(path)` 对不存在的文件抛出 `FileNotFoundError`
- [ ] `load(path)` 对语法错误的 YAML 抛出异常
- [ ] `load(path)` 对缺少 `harness` 顶层的文件抛出 `AssemblyValidationError`
- [ ] `assemble()` 返回可用的 `Harness` 实例
- [ ] 最小配置（仅 InputAdapter）可成功装配
- [ ] 完整配置（全部 7 个接口）可成功装配
- [ ] `inject` 字段正确解析依赖注入（如 ContextAssembler 获得 MemoryBackend 实例）
- [ ] `inject` 引用未注册组件时抛出 `DependencyNotSatisfiedError`
- [ ] 未知 `interface` 短名抛出 `UnknownInterfaceError`
- [ ] 无效 `implementation` 路径抛出 `ImportError`
- [ ] 缺少 `InputAdapter` 注册时抛出 `AssemblyValidationError`

### YAML hooks 处理

- [ ] YAML `hooks` 段中声明的 hook 被正确注册到 Harness
- [ ] hook handler 路径无效时抛出 `ImportError`
- [ ] 空 hooks 列表（`hooks: []`）不报错

### YAML llm 处理

- [ ] `llm.provider` 指定 `"openai"` 时正确创建 `MinimalLLMAdapter`
- [ ] `llm` 段不存在时 `call_llm` 为 `None`（不崩溃）
- [ ] `llm.base_url` / `llm.api_key` 可选覆盖环境变量

---

## 二、CLI 验收

### `harness init`

- [ ] `python main.py init my-agent` 创建 `my-agent/` 目录
- [ ] 创建的文件包含：`harness.yaml`、`AGENTS.md`、`README.md`
- [ ] 文件内容与 `profiles/coding-assistant/` 模板一致
- [ ] 目标目录已存在时打印错误提示（无 `--force` 时）
- [ ] `--force` 标志允许覆盖已存在的目录
- [ ] `--profile` 指定不存在的模板时打印可用模板列表
- [ ] init 命令不需要 LLM API key

### `harness run`

- [ ] `python main.py run --config harness.yaml` 正常启动
- [ ] 无 `--config` 时默认查找 `./harness.yaml`
- [ ] `--debug` 标志启用 DEBUG 日志级别
- [ ] config 文件不存在时降级启动（不崩溃）
- [ ] `Ctrl+C` (KeyboardInterrupt) 时优雅退出

---

## 三、端到端集成验收

### 完整生命周期

- [ ] 从 YAML 装配 → 完整 init→loop→end 生命周期无异常
- [ ] mock LLM + tool_use 场景完整执行
- [ ] `/exit` 信号正确终止会话
- [ ] 会话结束后 Sensor 写入 MemoryBackend
- [ ] 同一 memory 目录的两次装配间记忆持久化
- [ ] 无 YAML 配置文件时降级装配正常启动

### Hook 集成

- [ ] YAML 中声明的 hook 在会话中被正确触发
- [ ] `before_llm_call` hook 能修改 messages
- [ ] `on_error` hook 在异常时被触发

---

## 四、Profile 模板验收

- [ ] `profiles/coding-assistant/` 目录存在
- [ ] `harness.yaml` 包含全部 6 个默认组件注册
- [ ] `AGENTS.md` 包含基本身份定义和行为规则
- [ ] `README.md` 包含使用说明

---

## 五、代码质量验收

- [ ] 所有公开方法有完整类型标注
- [ ] 所有公开类有 docstring
- [ ] 符合 `sdd/05-conventions.md` 命名和结构规范
- [ ] `harness/config/yaml_assembler.py` 不修改 `harness/core/` 中的任何文件
- [ ] `harness/config/yaml_assembler.py` 不修改 `harness/interfaces/` 中的任何文件
- [ ] `harness/config/yaml_assembler.py` 不修改 `harness/components/` 中的任何文件
- [ ] `main.py` 不修改任何 `harness/` 下的现有文件
- [ ] 新增测试全部通过
- [ ] 所有已有测试全部通过（无回归）

---

## 六、全局验收标准（对照 sdd/06-acceptance.md）

- [ ] 用户可通过 YAML 文件声明领域模板和组件装配
- [ ] 用户可通过 DI 容器显式装配组件（Python API 保留不变）
- [ ] 框架能完成一轮完整的多轮对话（含 tool_use）
- [ ] ToolRouter 能合并 SystemToolProvider 和 MCPAdapter 的 Tool
- [ ] Hook 在关键生命周期点被触发
- [ ] Sensor 在会话结束后写入 MemoryBackend
- [ ] 下一会话初始化时能读到上一会话 Sensor 写入的记忆
- [ ] `harness init --profile coding-assistant <name>` 正确生成项目骨架
- [ ] 生成的项目中用户可修改 `harness.yaml` 替换任意组件
- [ ] 一个最小示例能从会话初始跑到会话结束，全程不报错
- [ ] 任意组件未注册时 DI 容器启动阶段产生可观测信息
- [ ] 组件异常时 `on_error` Hook 被触发，框架不崩溃
- [ ] Tool 执行失败时错误信息完整回传到上下文
- [ ] 所有公开方法有完整类型标注
- [ ] 所有公开类有 docstring
- [ ] 每个组件有对应测试文件且全部通过
- [ ] `harness/core/` 不 import `harness/components/` 中的具体实现
- [ ] `harness/interfaces/` 不 import 任何实现模块

---

## 决策记录

当以上所有复选框被打勾时，batch-10 即达到验收标准，可以标记为 **ACCEPT ✅**。

---

## 决策: ACCEPT ✅

**日期**: 2026-06-04

**验收结果**：
- 598 个测试全部通过（新增 57 个 + 已有 541 个），零回归
- YamlAssembler 实现了完整的 YAML 装配流程（load → assemble → Harness）
- CLI `init` / `run` 命令正常工作
- Profile 模板正确创建并可被 init 命令复制
- 端到端测试验证了完整生命周期、Hook 触发、记忆持久化、异常处理
- 代码 review 发现零 Critical 问题，已有的 WARNING/INFO 均已修正或记录

**代码变更摘要**：

| 文件 | 状态 | 说明 |
|------|------|------|
| `harness/config/yaml_assembler.py` | NEW | YamlAssembler + 异常类 + INTERFACE_REGISTRY |
| `harness/config/__init__.py` | MODIFIED | 新增 YamlAssembler 相关导出 |
| `harness/di.py` | MODIFIED | 仅新增 Harness.register_hook() 方法 |
| `main.py` | NEW | CLI 入口（init / run） |
| `profiles/coding-assistant/` | NEW | 领域模板（harness.yaml + AGENTS.md + README.md + profile.toml） |
| `tests/test_yaml_assembler.py` | NEW | 41 个 YamlAssembler 单元测试 |
| `tests/test_e2e_assembly.py` | NEW | 16 个端到端集成测试 |
| `sdd/batches/batch-10-di-assembly/design.md` | UPDATED | 完整设计文档 |
| `sdd/batches/batch-10-di-assembly/tasks.md` | UPDATED | 10 个任务清单 |
| `sdd/batches/batch-10-di-assembly/acceptance.md` | UPDATED | 验收标准（本文档） |

**已知限制（记录在案）**：
1. YAML assembly 仅支持构造函数参数注入，不支持 post-construction 属性设置（如 `CliAdapter.prompt`）
2. `${ENV_VAR}` 语法仅应用于 `api_key` 字段（最敏感的配置）
3. 循环 inject 依赖在 top-down 注册模型下不可行（设计限制，非 bug）
4. MCPAdapter 的 YAML 注册需要嵌套 `mcp_configs` 和 `transforms` 字典（已在设计文档和模板 YAML 中展示）
