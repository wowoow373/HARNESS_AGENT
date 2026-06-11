"""group_chat_demo.py — 三人群聊演示（Runtime Mode B）。

启动方式:
    python -c "
from harness.runtime.cli_console import CliConsole
from harness.runtime.runtime import Runtime
console = CliConsole(mode='mode_b')
Runtime(console).run_from_script('agents/group-chat/group_chat_demo.py')
"

架构:
    ┌──────────────────────────────────────┐
    │         MessageBus                   │
    │  user ──→ xiaoming, xiaohong         │
    │  xiaoming ──→ xiaohong, user前端     │
    │  xiaohong ──→ xiaoming, user前端     │
    └──────────────────────────────────────┘

每个 Agent 的 Adapter 链:
    AtomicOutputAdapter → FlexibleGroupChatInputAdapter → KernelBridgeAdapter
"""

import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from harness.core.container import DIContainer
from harness.di import Harness
from harness.adapters.llm_adapter import MinimalLLMAdapter
from harness.interfaces import (
    AsyncInputAdapter,
    ContextAssembler,
    GuideProvider,
    InputAdapter,
    MemoryBackend,
    Sensor,
    SystemToolProvider,
)
from harness.components.memory_backend.md_memory import MdMemory
from harness.components.sensor.logging_sensor import LoggingSensor
from harness.components.tool.default_system_tool_provider import (
    DefaultSystemToolProvider,
)
from harness.components.guide_provider.file_guide_provider import FileGuideProvider
from harness.components.input_adapter.atomic_output_adapter import AtomicOutputAdapter
from harness.components.input_adapter.flexible_group_chat_input_adapter import (
    FlexibleGroupChatInputAdapter,
)
from harness.components.context_assembler.selective_group_chat_assembler import (
    SelectiveGroupChatAssembler,
)
from harness.runtime.decorators import agent, subscribe

# ── 群聊配置 ──
USER_NAME = "主人"  # 用户在群聊中的显示名


def _assemble_agent(
    name: str,
    display_name: str,
    persona: str,
    min_wait: float = 1.0,
    max_wait: float = 5.0,
    jitter: float = 0.5,
    speaking_style: str = "",
    interests: Optional[str] = None,
    max_consecutive_replies: Optional[int] = None,
    initial_injection: Optional[str] = None,
    injection_rounds: int = 0,
) -> Harness:
    """装配单个群聊 Agent。

    每个 Agent 拥有独立的 DI 容器和组件实例。
    Adapter 链: AtomicOutputAdapter → FlexibleGroupChatInputAdapter → KBA

    Args:
        name: Agent pid。
        display_name: 群聊显示名。
        persona: 性格描述。
        min_wait: 最短等待时间（秒）。
        max_wait: 最长等待时间（秒）。
        jitter: 随机抖动上限（秒）。

    Returns:
        装配好的 Harness 实例。
    """
    container = DIContainer()

    # ── MemoryBackend — per-agent isolated storage ──
    memory = MdMemory(path=f"./memory/group_chat/{name}")
    container.register(MemoryBackend, memory)

    # ── Sensor — trajectory logging ──
    container.register(Sensor, LoggingSensor(memory=memory))

    # ── SystemToolProvider — basic built-in tools only (no extras needed) ──
    container.register(SystemToolProvider, DefaultSystemToolProvider())

    # ── GuideProvider — optional, identity is injected by ContextAssembler ──
    guide_path = Path(__file__).parent / "AGENTS.md"
    if guide_path.exists():
        container.register(
            GuideProvider,
            FileGuideProvider(paths=[str(guide_path)]),
        )

    # ── ContextAssembler — group chat specific ──
    container.register(
        ContextAssembler,
        SelectiveGroupChatAssembler(
            display_name=display_name,
            persona=persona,
            speaking_style=speaking_style,
            interests=interests,
            max_consecutive_replies=max_consecutive_replies,
            initial_injection=initial_injection,
            injection_rounds=injection_rounds,
        ),
    )

    # ── InputAdapter placeholder — 满足 Harness.from_container 校验 ──
    # 实际 I/O 由 AsyncInputAdapter（通过 _resolve_adapter）接管。
    container.register(InputAdapter, object())  # 由 KBA 覆盖

    # ── AsyncInputAdapter — the full group chat adapter chain ──
    # FlexibleGroupChatInputAdapter handles buffering + timing.
    # AtomicOutputAdapter wraps it for structured output parsing + atomic sending.
    # KernelBridgeAdapter is created internally when _inject_kernel_context is called.
    inner = FlexibleGroupChatInputAdapter(
        min_wait=min_wait,
        max_wait=max_wait,
        jitter=jitter,
        user_name=USER_NAME,
    )
    atomic = AtomicOutputAdapter(inner)
    container.register(AsyncInputAdapter, atomic)

    return Harness.from_container(container, call_llm=MinimalLLMAdapter())


# ═══════════════════════════════════════════════════════════════════════════
# Agent 声明
# ═══════════════════════════════════════════════════════════════════════════


@agent(
    "xiaoming",
    entry_prompt="你是小明，超级热情的一个人。现在群里很安静，随便打个招呼活跃一下气氛吧。",
    metadata={
        "display_name": "小明",
        "min_wait": 0.3,
        "max_wait": 2.0,
        "jitter": 0.3,
        "persona": "超级热情外向，话多且密，什么事都要掺和一脚，热心肠到有点烦人但很可爱",
    },
)
def assemble_xiaoming():
    return _assemble_agent(
        name="xiaoming",
        display_name="小明",
        persona="超级热情外向，话多且密，什么事都要掺和一脚，热心肠到有点烦人但很可爱",
        speaking_style="每句话都带感叹号！充满能量！喜欢用'哈哈'、'太棒了'、'我来我来'、'一起一起'，永远在安利和拉人，热情过头但很真诚",
        min_wait=0.3,
        max_wait=2.0,
        jitter=0.3,
        max_consecutive_replies=5,
        initial_injection=(
            "## 你今天想安利的东西\n"
            "你最近刚买了一个「喵喵智能音箱」，是个猫耳朵造型的AI音箱，"
            "可以语音控制家电、放歌、讲故事、学猫叫。你用了两周了，"
            "特别喜欢它每天早上用猫叫声叫你起床的功能，而且还能帮你"
            "点外卖！你逢人就安利这个东西，觉得所有人都应该买一个。\n"
            "注意：在群聊中找合适的机会自然地提起这个音箱，"
            "分享你的使用体验，推荐给大家。不要生硬地突然插入话题，"
            "要在相关语境下顺带提起。如果别人不感兴趣就不要再说了。"
        ),
        injection_rounds=4,  # 前4轮有推广，之后自动移除
    )


@agent(
    "xiaohong",
    entry_prompt="你是小红。你对群里的话题没什么兴趣，但出于礼貌偶尔回一下。",
    metadata={
        "display_name": "小红",
        "min_wait": 1.5,
        "max_wait": 5.0,
        "jitter": 0.6,
        "persona": "高冷、敷衍、惜字如金，觉得群里的人都很幼稚但偶尔也会被逗笑，回复极简短",
    },
)
def assemble_xiaohong():
    return _assemble_agent(
        name="xiaohong",
        display_name="小红",
        persona="高冷、敷衍、惜字如金，觉得群里的人都很幼稚但偶尔也会被逗笑，回复极简短。但对吃的东西完全没有抵抗力，一聊到美食就破防变成话痨。",
        speaking_style="平时回复非常短，3-5个字。常用'嗯'、'行吧'、'...'、'随便'、'6'、'哦'。但聊到美食就会破防，回复变长变热情，开始发感叹号和表情，完全忘了高冷人设。",
        interests=(
            "你对吃的话题特别感兴趣！包括但不限于：美食、探店、奶茶、甜品、火锅、"
            "烧烤、日料、川菜、网红餐厅、新开的店、外卖推荐、做饭、零食。\n"
            "当群里聊到这些话题时，你完全控制不住自己，会变得话很多很热情，"
            "像变了个人似的。但一旦话题转回其他内容，你会马上恢复高冷。"
        ),
        min_wait=1.5,
        max_wait=5.0,
        jitter=0.6,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 订阅关系 — 全连接拓扑
# ═══════════════════════════════════════════════════════════════════════════

# 互相订阅
subscribe("xiaoming").to("xiaohong")
subscribe("xiaohong").to("xiaoming")

# 订阅用户消息
subscribe("xiaoming").to("user")
subscribe("xiaohong").to("user")
