"""Minimal Multi-Turn Agent — 最简多轮对话 Agent。

使用 Harness Agent Template 框架组装一个完整的、调用真实 LLM API 的多轮对话 Agent。
所有组件通过 DI 容器显式装配，展示框架的模块化裁剪能力。

组件清单:
  - CliAdapter        : stdin 读取用户输入 / stdout 打印响应
  - MdMemory          : Markdown 文件记忆存储（跨会话持久化）
  - FileGuideProvider : 从 AGENTS.md 加载 Agent 指导信息
  - SimpleAssembler   : 滑动窗口上下文拼接
  - LoggingSensor     : 会话轨迹写入 episodic 记忆
  - MinimalLLMAdapter : 真实 LLM API 调用（OpenAI 兼容，自动读取 .env 配置）

用法::

    cd /path/to/harness_agent
    python examples/minimal_agent.py

    > 你好
    [Agent] 你好！有什么我可以帮你的吗？
    > 今天天气怎么样
    [Agent] 我无法获取实时天气数据...
    > /exit
    [系统] Agent 已退出。轨迹已保存到 ./memory/

跨会话记忆::

    # 第二次运行时，Agent 能从 episodic 记忆中找到上次对话的记录
    python examples/minimal_agent.py
    > 还记得我们上次聊了什么吗
    [Agent] 根据记忆，上次我们聊到了...

LLM 配置::

    框架自动从 ``harness/config/.env`` 读取配置（base_url、api_key、model）。
    也支持环境变量 ``LLM_BASE_URL`` / ``OPENAI_API_KEY`` / ``LLM_MODEL``，
    或直接传参：``MinimalLLMAdapter(base_url=..., api_key=..., model=...)``。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 将项目根目录加入 sys.path（使得 examples/ 可以在任意位置运行）
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Harness 框架
from harness.di import Harness
from harness.core.container import DIContainer

# 接口（DI 注册 key）
from harness.interfaces import (
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
)

# 组件默认实现
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.components.context_assembler.simple_assembler import SimpleAssembler
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.input_adapter.cli_adapter import CliAdapter
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("minimal_agent")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    """启动多轮对话 Agent — DI 容器显式装配。"""
    print("=" * 50)
    print("  Harness Agent Template — 多轮对话示例")
    print("=" * 50)
    print()
    print("组件:")
    print("  CliAdapter        — stdin/stdout 交互")
    print("  MdMemory          — Markdown 文件记忆（./memory/）")
    print("  FileGuideProvider — AGENTS.md 指导加载")
    print("  SimpleAssembler   — 滑动窗口上下文拼接")
    print("  LoggingSensor     — 轨迹写入 episodic 记忆")
    print("  MinimalLLMAdapter — 真实 LLM API（自动读取 .env）")
    print()
    print("输入 /exit 或 Ctrl+D 退出")
    print()

    # ── DI 容器：显式注册所有组件 ──────────────────────────────────────────
    container = DIContainer()

    # MemoryBackend — 跨会话持久化
    memory = MdMemory(path="./memory")
    container.register(MemoryBackend, memory)

    # InputAdapter — stdin/stdout 交互
    adapter = CliAdapter()
    adapter.prompt = "> "
    container.register(InputAdapter, adapter)

    # GuideProvider — 加载 AGENTS.md / CLAUDE.md 指导文件
    guide_paths = []
    for candidate in ["AGENTS.md", "CLAUDE.md"]:
        if os.path.exists(candidate):
            guide_paths.append(candidate)
    example_guide = os.path.join(os.path.dirname(__file__), "AGENTS.md")
    if os.path.exists(example_guide):
        guide_paths.append(example_guide)

    if guide_paths:
        container.register(GuideProvider, FileGuideProvider(guide_paths))
    # 无指导文件时跳过 — GuideProvider 是可选的

    # ContextAssembler — 滑动窗口上下文拼接（max_history=50，可注入 memory 做增强检索）
    container.register(ContextAssembler, SimpleAssembler(max_history=50, memory=memory))

    # Sensor — 会话结束后将轨迹写入 episodic 记忆
    container.register(Sensor, LoggingSensor(memory=memory))

    # LLM 适配器 — 自动从 .env / 环境变量读取配置
    # 切模型: MinimalLLMAdapter(model="claude-opus-4-8")
    # 自定义 endpoint: MinimalLLMAdapter(base_url="http://localhost:11434/v1", api_key="ollama")
    llm = MinimalLLMAdapter()

    # ── 装配并启动 ──────────────────────────────────────────────────────────
    harness = Harness.from_container(container, call_llm=llm)

    try:
        harness.run()
    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")
    finally:
        print("\n[系统] Agent 已退出。轨迹已保存到 ./memory/")


if __name__ == "__main__":
    main()
