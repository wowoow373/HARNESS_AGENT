"""FileGuideProvider — GuideProvider 的文件系统实现。

从 AGENTS.md / CLAUDE.md 等 Markdown 文件解析 Agent 指导信息。
支持单文件或多文件聚合，使用手写行级解析器（零外部依赖）。

用法::

    guide = FileGuideProvider("AGENTS.md")
    guide = FileGuideProvider(["AGENTS.md", "TEAM_RULES.md"])
    bundle = guide.get_guides(context)
    print(bundle.identity)   # "You are a coding assistant..."
    print(bundle.rules)      # ["规则1", "规则2", ...]
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from harness.interfaces.guide_provider import GuideContext
from harness.interfaces.types import Example, GuidesBundle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FileGuideProvider 类
# ---------------------------------------------------------------------------


class FileGuideProvider:
    """GuideProvider 的文件系统实现。

    从 AGENTS.md / CLAUDE.md 等 Markdown 文件解析 Agent 指导信息。
    支持单文件或多文件聚合。

    每次调用 get_guides() 都会重新读取文件（支持热更新）。
    文件缺失时记录 WARNING 但不崩溃，返回空 GuidesBundle。
    """

    # ------------------------------------------------------------------
    # H2 标题关键词映射
    # ------------------------------------------------------------------

    SECTION_KEYWORDS: Dict[str, List[str]] = {
        "capabilities": ["能力", "capabilit", "技能", "skill"],
        "rules": ["规则", "rule", "行为", "behavior", "behaviour"],
        "constraints": ["约束", "constraint", "限制", "limit", "禁止"],
        "examples": ["示例", "example", "范例", "sample"],
    }

    # 输入/输出标记的正则模式（按优先级排序）
    _INPUT_PATTERNS = [
        re.compile(r"\*\*输入\*\*\s*:\s*(.*)"),
        re.compile(r"\*\*Input\*\*\s*:\s*(.*)"),
        re.compile(r"\*\*input\*\*\s*:\s*(.*)"),
        re.compile(r"输入\s*:\s*(.*)"),
        re.compile(r"Input\s*:\s*(.*)"),
        re.compile(r"input\s*:\s*(.*)"),
    ]

    _OUTPUT_PATTERNS = [
        re.compile(r"\*\*输出\*\*\s*:\s*(.*)"),
        re.compile(r"\*\*Output\*\*\s*:\s*(.*)"),
        re.compile(r"\*\*output\*\*\s*:\s*(.*)"),
        re.compile(r"输出\s*:\s*(.*)"),
        re.compile(r"Output\s*:\s*(.*)"),
        re.compile(r"output\s*:\s*(.*)"),
    ]

    # ------------------------------------------------------------------
    # 构造与公开接口
    # ------------------------------------------------------------------

    def __init__(self, paths: Union[str, List[str]]):
        """初始化 FileGuideProvider。

        Args:
            paths: 单个文件路径或文件路径列表。
                   文件不存在时不抛异常（记录 WARNING），
                   get_guides() 时返回空 GuidesBundle。
                   支持相对路径（相对于当前工作目录）和绝对路径。
                   支持 ~ 展开为用户主目录。

        Raises:
            TypeError: paths 为 None 时。
        """
        if paths is None:
            raise TypeError("paths must not be None")

        # 规范化输入：单字符串 → 单元素列表
        if isinstance(paths, str):
            paths = [paths]
        elif not isinstance(paths, list):
            raise TypeError(
                f"paths must be str or List[str], got {type(paths).__name__}"
            )

        # 展开 ~ 并转为 Path 对象（不检查文件是否存在）
        self._paths: List[Path] = [
            Path(p).expanduser() for p in paths
        ]

    def get_guides(self, context: GuideContext) -> GuidesBundle:
        """解析 Markdown 文件，返回 GuidesBundle。

        每次调用都会重新读取文件（支持热更新）。
        如果所有文件都不存在或无法读取，返回空 GuidesBundle。

        Args:
            context: 包含用户请求、系统状态、环境状态的上下文。
                     FileGuideProvider 当前不依赖 context 内容（纯静态文件读取），
                     但接受此参数以满足 GuideProvider Protocol 的签名约定。

        Returns:
            GuidesBundle: 完整的指导集。解析失败时返回默认值（所有字段为空）。
        """
        if not self._paths:
            logger.warning("No guide file paths configured")
            return GuidesBundle()

        result = GuidesBundle()
        any_loaded = False

        for filepath in self._paths:
            bundle = self._read_and_parse(filepath)
            if bundle is not None:
                result = self._merge_guides(result, bundle)
                any_loaded = True

        if not any_loaded:
            logger.warning("No guide files loaded from %s", self._paths)

        return result

    # ------------------------------------------------------------------
    # 内部方法 — 文件 I/O
    # ------------------------------------------------------------------

    def _read_and_parse(self, filepath: Path) -> Optional[GuidesBundle]:
        """读取单个文件并解析为 GuidesBundle。

        Args:
            filepath: 文件路径。

        Returns:
            解析成功时返回 GuidesBundle，文件不存在或读取失败时返回 None。
        """
        if not filepath.exists():
            logger.warning("Guide file not found: %s", filepath)
            return None

        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read guide file %s: %s", filepath, e)
            return None

        if not text.strip():
            logger.info("Guide file is empty: %s", filepath)
            return GuidesBundle()

        try:
            bundle = self._parse_markdown_guides(text)
        except Exception as e:
            logger.warning(
                "Failed to parse guide file %s: %s", filepath, e
            )
            return None

        logger.info(
            "Loaded guide from %s (%d rules, %d constraints)",
            filepath,
            len(bundle.rules),
            len(bundle.constraints),
        )

        return bundle

    # ------------------------------------------------------------------
    # 内部方法 — 解析
    # ------------------------------------------------------------------

    def _identify_section(self, heading: str) -> Optional[str]:
        """根据 H2 标题文本，判断属于哪个 GuidesBundle 字段。

        Args:
            heading: H2 标题文本（已去除 ## 前缀和首尾空白）。

        Returns:
            字段名（"capabilities" | "rules" | "constraints" | "examples"），
            无法识别时返回 None。
        """
        heading_lower = heading.lower()
        for field, keywords in self.SECTION_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in heading_lower:
                    return field
        return None

    def _parse_markdown_guides(self, text: str) -> GuidesBundle:
        """按 Markdown 标题层级解析指导内容。

        状态机：
        - H1 (``# ``): 收集内容到 identity 缓冲区
        - H2 (``## ``): 根据标题关键词判断字段类型，进入对应收集模式
        - H3 (``### ``): 在 examples 模式下开始新的示例分组
        - 列表项 (``- `` / ``* ``): 在 capabilities/rules/constraints 模式下收集
        - 普通文本: 在 identity 模式下收集

        Args:
            text: Markdown 文件完整内容。

        Returns:
            GuidesBundle: 解析后的指导集。
        """
        lines = text.split("\n")

        # 累积结果
        identity_parts: List[str] = []
        capabilities: List[str] = []
        rules: List[str] = []
        constraints: List[str] = []
        examples: List[Example] = []

        # 状态机变量
        current_section: Optional[str] = "identity"  # 默认在 identity 模式
        # 示例解析状态
        current_example_input: List[str] = []
        current_example_output: List[str] = []
        collecting_output: bool = False  # True = 正在收集 output, False = 正在收集 input/等待输入标记
        has_example_content: bool = False  # 当前示例是否有任何内容

        def _flush_example() -> None:
            """将当前收集的示例内容保存为 Example。"""
            nonlocal current_example_input, current_example_output
            nonlocal collecting_output, has_example_content

            if has_example_content:
                input_text = "\n".join(current_example_input).strip()
                output_text = "\n".join(current_example_output).strip()
                examples.append(Example(input=input_text, output=output_text))

            # 重置
            current_example_input = []
            current_example_output = []
            collecting_output = False
            has_example_content = False

        for line in lines:
            # === H1: 顶层标题 → identity ===
            if line.startswith("# ") and not line.startswith("## "):
                heading_text = line[2:].strip()
                if heading_text:
                    identity_parts.append(heading_text)
                current_section = "identity"
                continue

            # === H2: 二级标题 → 切换 section ===
            if line.startswith("## ") and not line.startswith("### "):
                heading_text = line[3:].strip()
                # 如果在 examples 模式下，刷新当前示例
                if current_section == "examples":
                    _flush_example()
                section = self._identify_section(heading_text)
                if section is not None:
                    current_section = section
                else:
                    current_section = None  # 无法识别的 section，跳过其内容
                continue

            # === H3: 三级标题 → 仅在 examples 模式下开始新示例 ===
            if line.startswith("### "):
                if current_section == "examples":
                    _flush_example()
                continue

            # === 列表项 ===
            if line.startswith("- ") or line.startswith("* "):
                item_text = line[2:].strip()
                if current_section == "capabilities":
                    capabilities.append(item_text)
                elif current_section == "rules":
                    rules.append(item_text)
                elif current_section == "constraints":
                    constraints.append(item_text)
                continue

            # === 输入/输出标记（仅在 examples 模式下） ===
            if current_section == "examples":
                # 检查是否为输入标记
                input_matched = False
                for pat in self._INPUT_PATTERNS:
                    m = pat.match(line)
                    if m:
                        # 如果有正在收集的内容，刷新上一个示例
                        if has_example_content and collecting_output:
                            # 已经收集了输入和至少部分输出，这是一个新示例
                            _flush_example()
                        elif has_example_content and not collecting_output and current_example_input:
                            # 有输入但还没看到输出，又一个输入 → 覆盖（或刷新）
                            _flush_example()

                        input_text = m.group(1).strip()
                        if input_text:
                            current_example_input.append(input_text)
                        collecting_output = False
                        has_example_content = True
                        input_matched = True
                        break

                if input_matched:
                    continue

                # 检查是否为输出标记
                for pat in self._OUTPUT_PATTERNS:
                    m = pat.match(line)
                    if m:
                        collecting_output = True
                        output_text = m.group(1).strip()
                        if output_text:
                            current_example_output.append(output_text)
                        has_example_content = True
                        break

                # 如果当前行匹配了输出标记，跳过后续的空行/普通文本处理
                if collecting_output and any(pat.match(line) for pat in self._OUTPUT_PATTERNS):
                    continue

            # === 空行 ===
            if line.strip() == "":
                if current_section == "identity":
                    identity_parts.append("")
                # 其他 section 中忽略空行
                continue

            # === 普通文本行 ===
            if current_section == "identity":
                identity_parts.append(line)
            elif current_section == "examples":
                # 示例模式下的普通文本：追加到当前收集的 input 或 output
                if collecting_output:
                    current_example_output.append(line)
                    has_example_content = True
                else:
                    # 如果没有看到输入标记，这些文本可能是示例的上下文描述
                    # 如果还没有输入，跳过（如示例标题下的描述文本）
                    # 如果已有输入内容但还没看到输出，追加到输入
                    if has_example_content:
                        current_example_input.append(line)

        # 文件结束时刷新最后一个示例
        if current_section == "examples":
            _flush_example()

        # 组装 identity：保留段落结构
        identity = "\n".join(identity_parts).strip()

        return GuidesBundle(
            identity=identity,
            capabilities=capabilities,
            rules=rules,
            constraints=constraints,
            examples=examples,
        )

    # ------------------------------------------------------------------
    # 内部方法 — 合并
    # ------------------------------------------------------------------

    def _merge_guides(
        self, base: GuidesBundle, new: GuidesBundle
    ) -> GuidesBundle:
        """将 new 的内容合并到 base。

        合并规则：
        - identity: 用空行拼接。
        - capabilities: 合并列表并去重（保持首次出现顺序）。
        - rules: 追加到末尾（不去重）。
        - constraints: 追加到末尾（不去重）。
        - examples: 追加到末尾（不去重）。

        Args:
            base: 已有累积的 GuidesBundle。
            new: 新解析出的 GuidesBundle。

        Returns:
            合并后的新 GuidesBundle 实例。
        """
        # identity: 拼接
        merged_identity = base.identity
        if new.identity:
            if merged_identity:
                merged_identity = merged_identity + "\n\n" + new.identity
            else:
                merged_identity = new.identity

        # capabilities: 合并 + 去重（保持首次出现顺序）
        seen: set = set(base.capabilities)
        merged_capabilities = list(base.capabilities)
        for cap in new.capabilities:
            if cap not in seen:
                seen.add(cap)
                merged_capabilities.append(cap)

        # rules / constraints / examples: 追加
        merged_rules = list(base.rules) + list(new.rules)
        merged_constraints = list(base.constraints) + list(new.constraints)
        merged_examples = list(base.examples) + list(new.examples)

        return GuidesBundle(
            identity=merged_identity,
            capabilities=merged_capabilities,
            rules=merged_rules,
            constraints=merged_constraints,
            examples=merged_examples,
        )
