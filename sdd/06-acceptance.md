# 06 — 全局验收标准

> 整个 Harness Agent Template 框架开发完成的最终验收标准。batch-10（di-assembly）完成时，逐条对照确认。
>
> **版本: 1.0** — **全部通过 ✅**（2026-06-04，598 tests passed）

---

## 一、功能验收

- [x] 用户可通过 TOML 文件声明领域模板元数据（`profile.toml`），通过 YAML 文件声明 DI 装配（`harness.yaml`），框架解析并装配对应组件
- [x] 用户可通过 DI 容器显式装配组件（`container.register(Interface, instance)` — 预构造实例注册），替换任意默认实现
- [x] 用户可通过 YAML 文件声明式装配组件，覆盖 80% 简单场景（与 Python API 互补）
- [x] 框架能完成一轮完整的多轮对话：
  - InputAdapter 接收用户输入 → GuideProvider 提供指导 → ContextAssembler 组装上下文 → LLM 调用（可用 mock）→ 如果 LLM 返回 tool_use → ToolRouter 执行工具 → 结果回传 LLM 继续生成 → 最终 text 响应通过 InputAdapter 返回
- [x] ToolRouter 能合并 SystemToolProvider 和 MCPAdapter 提供的 Tool，对外提供统一的工具列表
- [x] Hook 在关键生命周期点被触发（11 个 Hook 点全部验证）
- [x] Sensor 在会话结束后写入 MemoryBackend（通过构造注入的实例）
- [x] 下一会话初始化时，ContextAssembler 能读到上一会话 Sensor 写入的记忆

---

## 二、集成验收

- [x] `harness init --profile coding-assistant <project-name>` 能正确生成项目骨架
- [x] 生成的项目中，用户可修改 `harness.yaml` 替换任意组件，也可修改 `main.py` 使用 Python API 装配
- [x] 一个最小示例能从会话初始跑到会话结束，全程不报错：
  ```
  用户输入: "Hello"
  → GuideProvider 加载 AGENTS.md
  → ContextAssembler 组装上下文
  → LLM（mock）返回 text 响应
  → InputAdapter 输出响应
  → on_session_end Hook 触发
  → Sensor 写入轨迹到 MemoryBackend
  → after_sensor Hook 触发
  ```
- [x] YAML 装配路径与 Python API 装配路径均可独立完成完整生命周期

---

## 三、边界条件

- [x] 任意组件未注册时，DI 容器启动阶段产生可观测信息（日志或异常），不静默跳过
- [x] 一个组件执行中抛异常时：
  - 有可追踪的日志/错误信息
  - `on_error` Hook 被触发（如果已注册）
  - 框架不崩溃（或优雅退出，不留下无提示的僵尸状态）
- [x] MemoryBackend 文件路径不可写时，产生明确错误信息
- [x] Tool 执行失败时（`success=False`），错误信息完整回传到 LLM 上下文（不丢失）

---

## 四、代码质量

- [x] 所有公开接口方法有完整类型标注
- [x] 所有公开类有 docstring
- [x] 每个组件有对应测试文件，且全部通过（598 tests, 0 failures）
- [x] 所有代码符合 `05-conventions.md` 中定义的命名和结构规范
- [x] `harness/core/` 中的代码不 import `harness/components/` 中的具体实现
- [x] `harness/interfaces/` 中的代码不 import 任何实现模块
