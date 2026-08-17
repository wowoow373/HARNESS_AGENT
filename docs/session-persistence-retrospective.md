# 会话持久化与恢复 —— 开发回顾与运行指南

> 本文档记录「对话持久化与恢复」功能的**开发过程**、**端到端测试覆盖评估**、以及**当前阶段的启动方式**。
>
> - 设计定稿 → `docs/superpowers/plans/2026-08-16-session-persistence-resume.md`
> - 架构说明 → `ARCHITECTURE.md#会话持久化与恢复`

---

## 一、开发过程总结

这是一次**子代理驱动开发**（subagent-driven development）执行的长任务，目标是把一份已定稿的《对话持久化与恢复》设计落地为 harness 框架的崩溃恢复能力。

### 流程纪律

计划文档拆成 **15 个任务（T1–T15）**，每个任务严格走同一循环：

> 派发实现者（sonnet，全新上下文）→ 严格 TDD（先写失败测试 → 实现 → 全绿）→ 规格符合性审查（haiku）→ 代码质量审查（sonnet）→（发现问题则）实现者修复 → 复审 → 计划文档修订（把 plan 级发现回写成 canonical spec）→ 提交。

关键约束：绝不并行派实现者；绝不跳过审查；每任务一个 `feat` commit + 每修复轮一个 `fix` commit + 每计划修订一个 `docs` commit；全程在隔离 worktree（detached HEAD，线性提交栈）工作；回归基线固定（`--ignore=tests/test_real_llm_trace.py`，2 个 pre-existing `test_e2e_assembly` 失败 + 1 skip 恒定）。

### 15 个任务的交付

| 层 | 任务 | 交付 |
|---|---|---|
| 事件/存储 | T1–T5 | 事件 schema、id/token 生成、`Sequencer`、`_LogWriter` 单写协程、`SessionLog`（唯一咽喉点）、`index.json` 原子投影 |
| 插桩/接线 | T6–T8 | 编排器 R0–R6 记录点 + 轮次 flush、Kernel/Runtime 接线、`replay.py` 读侧（截断/中断检测） |
| 恢复核心 | T9–T12 | manifest 分级校验、`Kernel.boot`（create/start 拆分、所有权接管、种子恢复、Mode A/B）、msg_id 盖章、`plan_redelivery` 配对修复 |
| 入口/收尾 | T13–T15 | `--resume`/`--force` CLI 接线、E2E 测试、文档 |

最终规模：**40 个提交，41 文件，+9820 行**。

### 过程中审查逮到的真 bug（都被测试钉死并修复）

- manifest 探针 tool_names 恒空 → 工具会话 resume 必硬失败（C1）
- child_finished edge 永不落盘
- `_build_trajectory` 把 resume_marker 泄漏进 `session_end` 落盘
- Mode B 索引丢失后不可恢复
- ……

这些是「分任务审查 + E2E 测试」才暴露的，单靠单元测试会漏。

### 最终整体审查结论

7 条核心不变量全部成立：

1. 单写协程 per 文件
2. seq 严格连续（缺号=损坏）
3. LSN 会话级单调（空洞=崩溃损失证据）
4. 失败方向向下（持久化失败永不打断对话）
5. 所有权接管（token + pid 活性 + force）
6. boot 四步序（创建所有 → 种子 → 配对修复 → 启动所有）
7. msg_id 配对修复

---

## 二、端到端测试覆盖评估

`tests/session/test_e2e.py` 有 **4 个测试**。

### ✅ 已覆盖（真实端到端，两次真实 boot + 真实落盘读盘）

1. **全生命周期**：run1 正常结束 → run2 `boot(resume)` 续跑，断言单 header、seq 严格连续（`[0..n]`）、2 个 session_end、index 的 `final_output` 正确，且 run2 的 LLM spy 能看到 run1 的历史（种子真正进了 LLM 上下文）。
2. **崩溃变体（无 session_end）**：无 index.json → `rebuild_index` → `status_before=="crashed"` → 仍可 resume、index 重建。
3. **中断检测**：assistant 带未闭合 tool_calls → 注入 `resume_marker`，断言标记**只在内存 history、绝不落盘**（钉死泄漏 bug 的回归测试）。
4. **LSN 空洞**：手工构造 lsn 3 缺失 → `measure_lsn_gap == 1`。

### ⚠️ 覆盖缺口（诚实说明）

- **不是真实进程崩溃**：崩溃变体靠手工写日志 fixture（截断行 / 剥掉 session_end）模拟，没有真的 `kill -9` 正在写盘的进程再恢复。fsync 纪律、半行物理截断只在**单元层**（test_store / test_session_log / test_boot）验证。
- **没走真实 CLI**：E2E 直接调 `kernel.boot(...)`，未通过 `python main.py run --runtime --resume` 这条真实启动链验证（CLI 解析仅在 test_cli_resume 测了 parser）。
- **Mode B 的 E2E resume 缺失**：Mode B 崩溃重建恢复测试在 test_boot（`test_rebuild_index_recovers_mode_b_with_script`），但 test_e2e 无「workflow 脚本 → 崩溃 → resume 补投 entry」完整链路。
- **配对修复（redelivery）无 E2E**：`plan_redelivery` 三规则有单元测试（test_redelivery），但无「中途崩溃丢消息 → 恢复时按 msg_id 补投」的端到端用例。
- 第 4 个 LSN 测试本质是 replay 单元测试，非 E2E。

**结论**：核心「两次运行共享一条连续日志 + 崩溃后仍可 resume + 中断标记不落盘」主链是真正端到端且被钉死的；真实进程 kill、真实 CLI、Mode B 全链、redelivery 全链这四块还缺 E2E 覆盖，属已知后续补强点。

---

## 三、现阶段如何启动

### 配置

`harness.yaml`（可选，缺省即用默认）：

```yaml
sessions:
  root: ./sessions      # 默认
  enabled: true          # 默认
```

### Mode A（交互式对话，root agent）

```bash
# 全新启动
python main.py run --runtime

# 恢复指定会话（会话 id 见 sessions/ 目录名，形如 conv-20260817-151234-abcd）
python main.py run --runtime --resume conv-20260817-151234-abcd

# 强制接管（所有权冲突 / manifest 硬校验降级）
python main.py run --runtime --resume <conv_id> --force
```

### Mode B（workflow 脚本，多 agent）

```bash
# 全新启动
python main.py workflow my_workflow.py

# 恢复（脚本会被 sha1 校验，改了会硬失败，除非 --force）
python main.py workflow my_workflow.py --resume <conv_id> [--force]
```

### 说明

- `--resume` 只在 `--runtime` 模式下生效（Mode A 不带 `--runtime` 会收到警告并被忽略）。
- 会话 id 自动生成（`conv-<时间戳>-<4位hex>`），存在 `sessions/<conv_id>/` 下；恢复时从目录名取。
- 运行内交互命令：`/agents` `/kill <pid>` `/end <flag>` `/talk <pid>` `/exit`。
- 持久化默认开启；`sessions/` 目录与 `.env` 已加入 `.gitignore`。
