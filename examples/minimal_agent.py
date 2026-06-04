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

.. note::

   batch-10（di-assembly）尚未实现，因此当前通过 ``build_agent()`` 手动装配 DI 容器。
   batch-10 完成后将提供 ``Harness.from_profile()`` 等更便捷的入口，
   但手动装配方式将始终保留 — 这正是框架"显式装配"的核心设计。

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
from typing import List

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
# 装配函数 — batch-10（di-assembly）之前的显式装配方式
# ---------------------------------------------------------------------------
def build_agent(
    memory_path: str = "./memory",
    guide_paths: List[str] | None = None,
    llm_model: str | None = None,
) -> Harness:
    """装配并返回一个完整的 Agent 实例。

    通过 DI 容器显式注册所有组件的默认实现。
    这是框架的核心使用方式：用户创建组件实例 → 注册到容器 → 启动。

    用户可按需替换任意组件：
      - 换用 MemoryBackend: ``container.register(MemoryBackend, RedisMemory(...))``
      - 换用 InputAdapter: ``container.register(InputAdapter, WebAdapter(...))``
      - 换用 LLM:         ``MinimalLLMAdapter(model="claude-opus-4-8")``
      - 裁切 MCP:         不注册 MCPAdapter 即可

    .. note::

       batch-10 完成后可简化为 ``Harness.from_profile(...)``，
       但 ``build_agent()`` 这种显式装配方式将始终可用。

    Args:
        memory_path: 记忆存储目录路径。
        guide_paths: AGENTS.md 等指导文件路径列表。
                     为 None 时自动检测当前目录和项目根目录。
        llm_model: LLM 模型名称，为 None 时使用 .env 中的默认值。

    Returns:
        Harness: 可运行的框架实例（调用 ``harness.run()`` 启动）。
    """
    container = DIContainer()

    # --- MemoryBackend ---
    memory = MdMemory(path=memory_path)
    container.register(MemoryBackend, memory)
    logger.info("MemoryBackend: %s → %s", type(memory).__name__, memory_path)

    # --- InputAdapter ---
    adapter = CliAdapter()
    adapter.prompt = "> "
    container.register(InputAdapter, adapter)
    logger.info("InputAdapter: %s", type(adapter).__name__)

    # --- GuideProvider ---
    if guide_paths is None:
        guide_paths = []
        for candidate in ["AGENTS.md", "CLAUDE.md"]:
            if os.path.exists(candidate):
                guide_paths.append(candidate)
        example_guide = os.path.join(os.path.dirname(__file__), "AGENTS.md")
        if os.path.exists(example_guide):
            guide_paths.append(example_guide)

    if guide_paths:
        guide = FileGuideProvider(guide_paths)
        container.register(GuideProvider, guide)
        logger.info("GuideProvider: %s → %s", type(guide).__name__, guide_paths)
    else:
        logger.info("GuideProvider: (未注册 — 无 AGENTS.md 文件)")

    # --- ContextAssembler ---
    assembler = SimpleAssembler(max_history=50, memory=memory)
    container.register(ContextAssembler, assembler)
    logger.info("ContextAssembler: %s (max_history=50)", type(assembler).__name__)

    # --- Sensor ---
    sensor = LoggingSensor(memory=memory)
    container.register(Sensor, sensor)
    logger.info("Sensor: %s", type(sensor).__name__)

    # --- LLM Adapter（真实 API）---
    llm_kwargs = {}
    if llm_model:
        llm_kwargs["model"] = llm_model
    llm = MinimalLLMAdapter(**llm_kwargs)
    logger.info(
        "LLM: %s (model=%s, base_url=%s)",
        type(llm).__name__,
        llm.model,
        llm.base_url,
    )

    # --- 装配 Harness ---
    harness = Harness.from_container(container, call_llm=llm)
    logger.info("Harness: 装配完成，所有组件已就绪")

    return harness


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    """启动多轮对话 Agent。"""
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

    harness = build_agent(memory_path="./memory")

    try:
        harness.run()
    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")
    finally:
        print("\n[系统] Agent 已退出。轨迹已保存到 ./memory/")


if __name__ == "__main__":
    main()
