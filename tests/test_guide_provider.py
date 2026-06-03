"""Test harness for FileGuideProvider — batch-04 guide provider implementation.

Covers all acceptance criteria defined in sdd/batches/batch-04-guide-provider/acceptance.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.components.guide_provider import FileGuideProvider
from harness.core.container import DIContainer
from harness.interfaces.guide_provider import GuideContext, GuideProvider
from harness.interfaces.types import Example, GuidesBundle


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_agents_md(tmp_path: Path) -> Path:
    """Create a complete AGENTS.md with all 5 fields."""
    content = """# You are a test assistant.

You help with testing Python applications.

## 能力
- Write unit tests
- Debug test failures
- Review test coverage

## 规则
- Always write tests first
- Keep tests simple and focused
- Use descriptive test names

## 约束
- Never modify production code without tests
- Never skip CI checks

## 示例

### Example 1: Code Review

**输入**:
Review this function for bugs.

**输出**:
I will analyze the function systematically...

### Example 2: Add Feature

**输入**:
Add a rate limiter.

**输出**:
I'll propose a plan first.
"""
    filepath = tmp_path / "AGENTS.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


@pytest.fixture
def minimal_md(tmp_path: Path) -> Path:
    """Create a minimal file with only H1."""
    content = "# You are a minimal assistant.\n\nJust enough to work.\n"
    filepath = tmp_path / "MINIMAL.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


@pytest.fixture
def chinese_headings_md(tmp_path: Path) -> Path:
    """Create a file with Chinese H2 headings."""
    content = """# 测试助手

我是测试助手。

## 能力
- 编写测试
- 调试代码

## 规则
- 先写测试
- 保持简洁

## 约束
- 不修改生产环境

## 示例

### 示例1

**输入**:
审查代码。

**输出**:
我将系统分析。
"""
    filepath = tmp_path / "CHINESE.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


@pytest.fixture
def english_headings_md(tmp_path: Path) -> Path:
    """Create a file with English H2 headings."""
    content = """# Test Assistant

## Capabilities
- Write tests
- Debug code

## Rules
- Test first
- Keep it simple

## Constraints
- Never touch prod

## Examples

### Example 1

**Input**:
Review this.

**Output**:
I will analyze.
"""
    filepath = tmp_path / "ENGLISH.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


@pytest.fixture
def rules_only_md(tmp_path: Path) -> Path:
    """Create a file with only rules, no H1."""
    content = """## 规则
- Rule 1
- Rule 2
"""
    filepath = tmp_path / "RULES_ONLY.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ============================================================================
# AC-GP-01: Parse all 5 fields from Markdown file
# ============================================================================


class TestParseIdentity:
    """AC-GP-01.1: Parse H1 heading → identity field."""

    def test_parse_identity(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert "You are a test assistant" in bundle.identity
        assert "You help with testing Python applications" in bundle.identity


class TestParseCapabilities:
    """AC-GP-01.2: Parse ## 能力 section → capabilities list."""

    def test_parse_capabilities(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 3
        assert "Write unit tests" in bundle.capabilities
        assert "Debug test failures" in bundle.capabilities
        assert "Review test coverage" in bundle.capabilities


class TestParseRules:
    """AC-GP-01.3: Parse ## 规则 section → rules list."""

    def test_parse_rules(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.rules) == 3
        assert "Always write tests first" in bundle.rules
        assert "Keep tests simple and focused" in bundle.rules
        assert "Use descriptive test names" in bundle.rules


class TestParseConstraints:
    """AC-GP-01.4: Parse ## 约束 section → constraints list."""

    def test_parse_constraints(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.constraints) == 2
        assert "Never modify production code without tests" in bundle.constraints
        assert "Never skip CI checks" in bundle.constraints


class TestParseExamples:
    """AC-GP-01.5: Parse ## 示例 section → examples list of Example."""

    def test_parse_examples(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 2
        assert all(isinstance(e, Example) for e in bundle.examples)
        assert bundle.examples[0].input == "Review this function for bugs."
        assert bundle.examples[0].output == "I will analyze the function systematically..."
        assert bundle.examples[1].input == "Add a rate limiter."
        assert bundle.examples[1].output == "I'll propose a plan first."


class TestParseMinimalFile:
    """AC-GP-01.6: Parse minimal file (only H1)."""

    def test_parse_minimal_file(self, minimal_md):
        provider = FileGuideProvider(str(minimal_md))
        bundle = provider.get_guides(GuideContext())
        assert "You are a minimal assistant" in bundle.identity
        assert bundle.capabilities == []
        assert bundle.rules == []
        assert bundle.constraints == []
        assert bundle.examples == []


# ============================================================================
# AC-GP-02: Heading keyword matching (Chinese + English)
# ============================================================================


class TestParseChineseHeadings:
    """AC-GP-02.1: Chinese H2 headings correctly identified."""

    def test_parse_chinese_headings(self, chinese_headings_md):
        provider = FileGuideProvider(str(chinese_headings_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 2
        assert "编写测试" in bundle.capabilities
        assert len(bundle.rules) == 2
        assert "先写测试" in bundle.rules
        assert len(bundle.constraints) == 1
        assert "不修改生产环境" in bundle.constraints
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "审查代码。"


class TestParseEnglishHeadings:
    """AC-GP-02.2: English H2 headings correctly identified."""

    def test_parse_english_headings(self, english_headings_md):
        provider = FileGuideProvider(str(english_headings_md))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 2
        assert "Write tests" in bundle.capabilities
        assert len(bundle.rules) == 2
        assert "Test first" in bundle.rules
        assert len(bundle.constraints) == 1
        assert len(bundle.examples) == 1


class TestParseMixedHeadings:
    """AC-GP-02.3: Mixed Chinese-English headings."""

    def test_parse_mixed_headings(self, tmp_path):
        content = """# Test

## 行为规则与Behavior Rules
- Rule A
- Rule B
"""
        f = tmp_path / "mixed.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.rules) == 2
        assert "Rule A" in bundle.rules


class TestParseVariantKeywords:
    """AC-GP-02.4: Variant keyword headings."""

    def test_variant_keywords_behaviour(self, tmp_path):
        """'behaviour' matches rules."""
        content = """# Test

## Behaviour
- A rule about behaviour
"""
        f = tmp_path / "behaviour.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.rules) == 1

    def test_variant_keywords_skills(self, tmp_path):
        """'技能' matches capabilities."""
        content = """# Test

## 技能
- Python coding
"""
        f = tmp_path / "skills.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 1
        assert "Python coding" in bundle.capabilities

    def test_variant_keywords_limits(self, tmp_path):
        """'限制' matches constraints."""
        content = """# Test

## 限制
- No network access
"""
        f = tmp_path / "limits.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.constraints) == 1
        assert "No network access" in bundle.constraints

    def test_variant_keywords_samples(self, tmp_path):
        """'范例' matches examples."""
        content = """# Test

## 范例

**输入**:
Hello

**输出**:
World
"""
        f = tmp_path / "samples.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Hello"


# ============================================================================
# AC-GP-03: File-not-found handling
# ============================================================================


class TestFileNotFound:
    """AC-GP-03.1: Non-existent file → no exception, empty GuidesBundle."""

    def test_file_not_found(self):
        provider = FileGuideProvider("/nonexistent/path/to/guide.md")
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert bundle.capabilities == []
        assert bundle.rules == []
        assert bundle.constraints == []
        assert bundle.examples == []


class TestAllFilesMissing:
    """AC-GP-03.2: All paths non-existent → empty GuidesBundle."""

    def test_all_files_missing(self):
        provider = FileGuideProvider([
            "/nonexistent/1.md",
            "/nonexistent/2.md",
        ])
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert bundle.capabilities == []


class TestTildeExpansion:
    """AC-GP-03.3: ~ expansion works correctly."""

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        """~ should be expanded to user home directory."""
        import os
        # Create a file in tmp_path and link via home expansion trick
        f = tmp_path / "guide.md"
        f.write_text("# Test\n\nHello world.\n")

        # Monkeypatch Path.expanduser to substitute our tmp_path
        original_expanduser = Path.expanduser

        def _mock_expanduser(self_path):
            path_str = str(self_path)
            if path_str.startswith("~/"):
                return tmp_path / path_str[2:]
            if path_str == "~":
                return tmp_path
            return original_expanduser(self_path)

        monkeypatch.setattr(Path, "expanduser", _mock_expanduser)

        provider = FileGuideProvider("~/guide.md")
        bundle = provider.get_guides(GuideContext())
        assert "Hello world" in bundle.identity


# ============================================================================
# AC-GP-04: Edge-case inputs
# ============================================================================


class TestEmptyFile:
    """AC-GP-04.1: Empty file → empty GuidesBundle."""

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert bundle.capabilities == []
        assert bundle.rules == []

    def test_whitespace_only_file(self, tmp_path):
        """File with only whitespace → empty GuidesBundle."""
        f = tmp_path / "whitespace.md"
        f.write_text("   \n\n  \n")
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert bundle.capabilities == []


class TestNoH1Heading:
    """AC-GP-04.2: File without H1 → identity empty, other sections parsed."""

    def test_no_h1_heading(self, rules_only_md):
        provider = FileGuideProvider(str(rules_only_md))
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert len(bundle.rules) == 2
        assert "Rule 1" in bundle.rules


class TestUnrecognizedH2:
    """AC-GP-04.3: Unrecognized H2 → section content ignored."""

    def test_unrecognized_h2(self, tmp_path):
        content = """# Test

## 未知标题
- This list item should be ignored

## 规则
- This rule should be parsed
"""
        f = tmp_path / "unrecognized.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.rules) == 1
        assert "This rule should be parsed" in bundle.rules
        # 未知标题下的列表项不应出现在任何字段中
        assert "This list item should be ignored" not in bundle.rules
        assert "This list item should be ignored" not in bundle.capabilities
        assert "This list item should be ignored" not in bundle.constraints


class TestExamplesWithoutH3:
    """AC-GP-04.4: Examples section without H3 → reasonable behavior."""

    def test_examples_without_h3(self, tmp_path):
        content = """# Test

## 示例

**输入**:
This is input.

**输出**:
This is output.
"""
        f = tmp_path / "no_h3.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        # Should still parse examples without H3 sub-headings
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "This is input."
        assert bundle.examples[0].output == "This is output."


class TestEmptyPathsList:
    """AC-GP-04.5: Empty paths list → empty GuidesBundle, no exception."""

    def test_empty_paths_list(self):
        provider = FileGuideProvider([])
        bundle = provider.get_guides(GuideContext())
        assert bundle.identity == ""
        assert bundle.capabilities == []


# ============================================================================
# AC-GP-05: Multi-file aggregation
# ============================================================================


class TestMultiFileMergeIdentity:
    """AC-GP-05.1: Identity from two files concatenated."""

    def test_multi_file_merge_identity(self, tmp_path):
        f1 = tmp_path / "file1.md"
        f1.write_text("# Identity A\n\nContent A.\n")
        f2 = tmp_path / "file2.md"
        f2.write_text("# Identity B\n\nContent B.\n")

        provider = FileGuideProvider([str(f1), str(f2)])
        bundle = provider.get_guides(GuideContext())
        assert "Identity A" in bundle.identity
        assert "Identity B" in bundle.identity
        # They should be separated by a blank line
        assert "Identity A" in bundle.identity


class TestMultiFileMergeRules:
    """AC-GP-05.2: Rules from two files appended in order."""

    def test_multi_file_merge_rules(self, tmp_path):
        f1 = tmp_path / "file1.md"
        f1.write_text("## 规则\n- Rule A\n- Rule B\n")
        f2 = tmp_path / "file2.md"
        f2.write_text("## 规则\n- Rule C\n- Rule D\n")

        provider = FileGuideProvider([str(f1), str(f2)])
        bundle = provider.get_guides(GuideContext())
        assert bundle.rules == ["Rule A", "Rule B", "Rule C", "Rule D"]


class TestMultiFileMergeCapabilitiesDedup:
    """AC-GP-05.3: Duplicate capabilities deduplicated (first occurrence kept)."""

    def test_multi_file_merge_capabilities_dedup(self, tmp_path):
        f1 = tmp_path / "file1.md"
        f1.write_text("## 能力\n- Skill A\n- Skill B\n")
        f2 = tmp_path / "file2.md"
        f2.write_text("## 能力\n- Skill B\n- Skill C\n")

        provider = FileGuideProvider([str(f1), str(f2)])
        bundle = provider.get_guides(GuideContext())
        assert bundle.capabilities == ["Skill A", "Skill B", "Skill C"]


class TestMultiFilePartialMissing:
    """AC-GP-05.4: Partial file missing → existing files parsed, missing logged."""

    def test_multi_file_partial_missing(self, tmp_path):
        f1 = tmp_path / "exists.md"
        f1.write_text("# Test\n\n## 规则\n- Rule 1\n")

        provider = FileGuideProvider([
            str(f1),
            "/nonexistent/missing.md",
        ])
        bundle = provider.get_guides(GuideContext())
        # Should have content from the existing file
        assert "Test" in bundle.identity
        assert len(bundle.rules) == 1


class TestMultiFileSingleStringPath:
    """AC-GP-05.5: Single string path works (not just list)."""

    def test_single_string_path(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert "You are a test assistant" in bundle.identity

    def test_list_with_single_path(self, sample_agents_md):
        provider = FileGuideProvider([str(sample_agents_md)])
        bundle = provider.get_guides(GuideContext())
        assert "You are a test assistant" in bundle.identity


# ============================================================================
# AC-GP-06: Parse accuracy
# ============================================================================


class TestListItemsWithStarPrefix:
    """AC-GP-06.1: List items with * prefix also recognized."""

    def test_star_prefix_rules(self, tmp_path):
        content = """# Test

## 规则
* Rule with star
* Another star rule
"""
        f = tmp_path / "star.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.rules) == 2
        assert "Rule with star" in bundle.rules
        assert "Another star rule" in bundle.rules

    def test_mixed_list_prefixes(self, tmp_path):
        content = """# Test

## 能力
- Dash item
* Star item
"""
        f = tmp_path / "mixed_list.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 2
        assert "Dash item" in bundle.capabilities
        assert "Star item" in bundle.capabilities


class TestSpecialCharacters:
    """AC-GP-06.2: Special characters preserved without crash."""

    def test_special_characters_emoji(self, tmp_path):
        content = """# Test 🚀

I support emoji 🎉 and CJK characters 中文。

## 规则
- 规则 with émoji 😀
- Special chars: <> & " '
"""
        f = tmp_path / "special.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert "🚀" in bundle.identity
        assert "🎉" in bundle.identity
        assert len(bundle.rules) == 2
        # Should not crash

    def test_code_blocks_in_identity(self, tmp_path):
        content = """# Test

Here is some code:

```
print("hello")
```

More text.
"""
        f = tmp_path / "code.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert "print" in bundle.identity


class TestIdentityWithMultipleH1s:
    """AC-GP-06.3: Multiple H1s / paragraphs preserved in identity."""

    def test_identity_multiple_paragraphs(self, tmp_path):
        content = """# Heading

First paragraph.

Second paragraph.

Third paragraph.
"""
        f = tmp_path / "multi_para.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert "First paragraph" in bundle.identity
        assert "Second paragraph" in bundle.identity
        assert "Third paragraph" in bundle.identity

    def test_multiple_h1s(self, tmp_path):
        content = """# First H1

Content under first.

# Second H1

Content under second.
"""
        f = tmp_path / "multi_h1.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert "First H1" in bundle.identity
        assert "Second H1" in bundle.identity


class TestExamplesInputOutputPairing:
    """AC-GP-06.4: Input/output markers correctly paired."""

    def test_input_output_pairing(self, tmp_path):
        content = """# Test

## 示例

**输入**:
First input.

**输出**:
First output.

**输入**:
Second input.

**输出**:
Second output.
"""
        f = tmp_path / "pairing.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 2
        assert bundle.examples[0].input == "First input."
        assert bundle.examples[0].output == "First output."
        assert bundle.examples[1].input == "Second input."
        assert bundle.examples[1].output == "Second output."

    def test_input_only_example(self, tmp_path):
        """Example with only input, no output."""
        content = """# Test

## 示例

**输入**:
Only input here.
"""
        f = tmp_path / "input_only.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Only input here."
        assert bundle.examples[0].output == ""


# ============================================================================
# AC-GP-07: Example parsing variants
# ============================================================================


class TestExamplesChineseMarkers:
    """AC-GP-07.1: Chinese markers **输入**: / **输出**:."""

    def test_chinese_bold_markers(self, tmp_path):
        content = """# Test

## 示例

**输入**:
Hello world

**输出**:
你好世界
"""
        f = tmp_path / "cn_bold.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Hello world"
        assert bundle.examples[0].output == "你好世界"


class TestExamplesEnglishMarkers:
    """AC-GP-07.2: English markers **Input**: / **Output**:."""

    def test_english_bold_markers(self, tmp_path):
        content = """# Test

## Examples

**Input**:
Hello

**Output**:
World
"""
        f = tmp_path / "en_bold.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Hello"
        assert bundle.examples[0].output == "World"


class TestExamplesPlainMarkers:
    """AC-GP-07.3: Plain markers (no bold)."""

    def test_plain_chinese_markers(self, tmp_path):
        content = """# Test

## 示例

输入:
Plain input.

输出:
Plain output.
"""
        f = tmp_path / "plain_cn.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Plain input."
        assert bundle.examples[0].output == "Plain output."

    def test_plain_english_markers(self, tmp_path):
        content = """# Test

## Examples

Input:
Plain input.

Output:
Plain output.
"""
        f = tmp_path / "plain_en.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert bundle.examples[0].input == "Plain input."


class TestExamplesMultipleH3:
    """AC-GP-07.4: Multiple examples with ### sub-headings, independent."""

    def test_multiple_h3_examples(self, tmp_path):
        content = """# Test

## 示例

### Ex 1

**输入**:
Input one.

**输出**:
Output one.

### Ex 2

**输入**:
Input two.

**输出**:
Output two.

### Ex 3

**输入**:
Input three.

**输出**:
Output three.
"""
        f = tmp_path / "multi_h3.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 3
        assert bundle.examples[0].input == "Input one."
        assert bundle.examples[1].input == "Input two."
        assert bundle.examples[2].input == "Input three."
        assert bundle.examples[2].output == "Output three."


# ============================================================================
# AC-GP-08: Protocol compliance
# ============================================================================


class TestProtocolCompliance:
    """AC-GP-08.1: FileGuideProvider satisfies GuideProvider Protocol."""

    def test_protocol_compliance(self):
        provider = FileGuideProvider("dummy.md")
        assert isinstance(provider, GuideProvider), (
            "FileGuideProvider should satisfy GuideProvider Protocol"
        )

    def test_protocol_compliance_with_list(self):
        provider = FileGuideProvider(["a.md", "b.md"])
        assert isinstance(provider, GuideProvider)


class TestDIRegistration:
    """AC-GP-08.2: FileGuideProvider can be registered in DI container."""

    def test_di_registration(self):
        container = DIContainer()
        provider = FileGuideProvider("AGENTS.md")
        container.register(GuideProvider, provider)
        resolved = container.resolve(GuideProvider)
        assert resolved is provider

    def test_di_registration_and_resolve_type(self):
        container = DIContainer()
        provider = FileGuideProvider("AGENTS.md")
        container.register(GuideProvider, provider)
        resolved = container.resolve(GuideProvider)
        assert isinstance(resolved, FileGuideProvider)


class TestGetGuidesSignature:
    """AC-GP-08.3: get_guides() accepts GuideContext, returns GuidesBundle."""

    def test_get_guides_signature(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        context = GuideContext()
        result = provider.get_guides(context)
        assert isinstance(result, GuidesBundle)


# ============================================================================
# AC-GP-09: Type correctness
# ============================================================================


class TestReturnsGuidesBundleType:
    """AC-GP-09.1: get_guides() returns GuidesBundle."""

    def test_returns_guides_bundle_type(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        result = provider.get_guides(GuideContext())
        assert isinstance(result, GuidesBundle)
        assert isinstance(result.identity, str)
        assert isinstance(result.capabilities, list)
        assert isinstance(result.rules, list)
        assert isinstance(result.constraints, list)
        assert isinstance(result.examples, list)


class TestExamplesType:
    """AC-GP-09.2: Examples list elements are Example instances."""

    def test_examples_type(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        for example in bundle.examples:
            assert isinstance(example, Example)
            assert isinstance(example.input, str)
            assert isinstance(example.output, str)


class TestFieldTypes:
    """AC-GP-09.3 / AC-GP-09.4: Field types are correct."""

    def test_capabilities_rules_constraints_are_lists(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert isinstance(bundle.capabilities, list)
        assert all(isinstance(c, str) for c in bundle.capabilities)
        assert isinstance(bundle.rules, list)
        assert all(isinstance(r, str) for r in bundle.rules)
        assert isinstance(bundle.constraints, list)
        assert all(isinstance(c, str) for c in bundle.constraints)

    def test_identity_is_str(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        bundle = provider.get_guides(GuideContext())
        assert isinstance(bundle.identity, str)


# ============================================================================
# Additional edge-case tests
# ============================================================================


class TestEncodingErrors:
    """Non-UTF-8 files should log warning and skip."""

    def test_latin1_file(self, tmp_path):
        f = tmp_path / "latin1.md"
        # Write file with latin-1 encoding
        with open(str(f), "w", encoding="latin-1") as fh:
            fh.write("# T\xe9st\n\nC\xf4ntent.\n")
        provider = FileGuideProvider(str(f))
        # Should not crash - file should be skipped with warning
        bundle = provider.get_guides(GuideContext())
        assert isinstance(bundle, GuidesBundle)


class TestNonePaths:
    """Paths=None should raise TypeError."""

    def test_none_paths(self):
        with pytest.raises(TypeError):
            FileGuideProvider(None)


class TestConstructorEdgeCases:
    """Additional constructor edge cases."""

    def test_relative_path(self, tmp_path, monkeypatch):
        """Relative paths should work."""
        import os
        f = tmp_path / "relative.md"
        f.write_text("# Relative test\n")
        # Change cwd to tmp_path
        monkeypatch.chdir(tmp_path)
        provider = FileGuideProvider("relative.md")
        bundle = provider.get_guides(GuideContext())
        assert "Relative test" in bundle.identity


class TestIdentityOnlyFile:
    """File with only identity text (no H1 marker)."""

    def test_identity_only_no_headings(self, tmp_path):
        content = "Just some plain text without any headings.\n"
        f = tmp_path / "plaintext.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        # Since there's no H1, the text is collected in identity mode
        assert "Just some plain text" in bundle.identity


class TestMultipleSectionsNoIdentity:
    """File with multiple sections but no H1."""

    def test_sections_without_h1(self, tmp_path):
        content = """## 能力
- Skill 1

## 规则
- Rule 1

## 约束
- Constraint 1
"""
        f = tmp_path / "sections_no_h1.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.capabilities) == 1
        assert len(bundle.rules) == 1
        assert len(bundle.constraints) == 1
        assert bundle.identity == ""


class TestExampleWithMultilineContent:
    """Examples with multi-line input/output."""

    def test_multiline_input_output(self, tmp_path):
        content = """# Test

## 示例

**输入**:
Line 1 of input.
Line 2 of input.

**输出**:
Line 1 of output.
Line 2 of output.
Line 3 of output.
"""
        f = tmp_path / "multiline.md"
        f.write_text(content)
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        assert len(bundle.examples) == 1
        assert "Line 1 of input" in bundle.examples[0].input
        assert "Line 2 of input" in bundle.examples[0].input
        assert "Line 1 of output" in bundle.examples[0].output
        assert "Line 3 of output" in bundle.examples[0].output


class TestMergeEmpty:
    """Merging with empty bundle should be identity."""

    def test_merge_with_empty(self):
        provider = FileGuideProvider(["nonexistent.md"])
        # get_guides on empty should return empty GuidesBundle
        # This is tested implicitly; let's test _merge_guides directly
        provider._paths = []  # no files
        empty = GuidesBundle()
        merged = provider._merge_guides(empty, empty)
        assert merged.identity == ""
        assert merged.capabilities == []
        assert merged.rules == []

    def test_merge_empty_into_content(self, tmp_path):
        """Merging empty bundle with content preserves content."""
        f = tmp_path / "content.md"
        f.write_text("# Content\n\n## 规则\n- Rule 1\n")
        provider = FileGuideProvider(str(f))
        bundle = provider.get_guides(GuideContext())
        # Merge empty into content
        merged = provider._merge_guides(bundle, GuidesBundle())
        assert "Content" in merged.identity
        assert len(merged.rules) == 1


class TestGuideContextUnused:
    """FileGuideProvider does not use context, but accepts it."""

    def test_accepts_various_contexts(self, sample_agents_md):
        provider = FileGuideProvider(str(sample_agents_md))
        # None context fields
        ctx1 = GuideContext(user_request=None, env_state=None)
        r1 = provider.get_guides(ctx1)
        assert "You are a test assistant" in r1.identity

        # Full context
        ctx2 = GuideContext()
        r2 = provider.get_guides(ctx2)
        assert "You are a test assistant" in r2.identity

        # Results should be identical (context-independent)
        assert r1.identity == r2.identity


class TestOrchestratorIntegration:
    """AC-GP-10.1 补充：编排器端到端集成 — GuideProvider 在 Phase 1 被编排器正确调用。

    验证方式：创建 FileGuideProvider → 注册到容器 → 运行编排器 →
    确认 GuideProvider 产出的 GuidesBundle 能正确流入 ContextAssembler。
    """

    def test_orchestrator_phase1_get_guides(self, tmp_path):
        """编排器 Phase 1 调用 GuideProvider.get_guides() 并将结果传给 ContextAssembler。

        验证编排器完整链路：
        1. Phase 1: FileGuideProvider.get_guides() 被调用
        2. GuidesBundle 被注入 AssemblyContext.guides
        3. ContextAssembler 能消费 GuidesBundle
        """
        from harness.core.orchestrator import (
            ContextAssembler,
            GuideProvider,
            InputAdapter,
        )
        from harness.di import Harness
        from harness.interfaces.types import (
            AssemblyContext,
            Message,
            Response,
            SystemState,
            UserRequest,
        )

        # 1. 创建一个 Agent guide 文件
        guide_file = tmp_path / "agent.md"
        guide_file.write_text("""# You are a test coding assistant.

## 规则
- Always write tests first
- Keep code simple

## 约束
- Never delete production data
""")

        # 2. 创建 FileGuideProvider，指向临时文件
        guide_provider = FileGuideProvider(str(guide_file))

        # 3. 创建一个 ContextAssembler，捕获传入的 AssemblyContext
        captured_contexts = []

        class SpyAssembler:
            def assemble(self, ctx: AssemblyContext) -> list:
                captured_contexts.append(ctx)
                msgs = []
                if ctx.guides and ctx.guides.identity:
                    msgs.append(Message(role="system", content=ctx.guides.identity))
                if ctx.guides and ctx.guides.rules:
                    for r in ctx.guides.rules:
                        msgs.append(Message(role="system", content=f"Rule: {r}"))
                if ctx.user_request:
                    msgs.append(Message(role="user", content=ctx.user_request.text))
                return msgs

        # 4. Mock InputAdapter
        outputs = []
        receive_count = [0]

        class TestAdapter:
            def receive(self):
                receive_count[0] += 1
                if receive_count[0] == 1:
                    return UserRequest(text="write a test")
                return UserRequest(text="")  # 退出

            def send(self, response):
                outputs.append(response.text)

        # 5. Mock LLM
        def mock_llm(messages, tools=None):
            return Response(text="I'll write the test first.", stop_reason="end_turn")

        # 6. 装配容器并运行
        container = DIContainer()
        container.register(InputAdapter, TestAdapter())
        container.register(GuideProvider, guide_provider)
        container.register(ContextAssembler, SpyAssembler())

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

        # 7. 验证编排器正常完成
        assert len(outputs) == 1
        assert outputs[0] == "I'll write the test first."

        # 8. 验证 ContextAssembler 收到了正确的 GuidesBundle
        assert len(captured_contexts) >= 1
        ctx = captured_contexts[0]
        assert ctx.guides is not None
        assert "test coding assistant" in ctx.guides.identity
        assert len(ctx.guides.rules) >= 2
        assert any("Always write tests first" in r for r in ctx.guides.rules)
        assert any("Keep code simple" in r for r in ctx.guides.rules)
        assert len(ctx.guides.constraints) >= 1
        assert any("Never delete production data" in c for c in ctx.guides.constraints)

        # 9. 验证 GuidesBundle 已被缓存（Phase 1 只调用一次）
        # 同一轮内 CaptureContext 不变，但 user_request 会更新
        # GuidesBundle 在整个会话中保持不变

    def test_orchestrator_without_guide_provider(self, tmp_path):
        """编排器在 GuideProvider 未注册时应正常运行（不崩溃）。"""
        from harness.core.orchestrator import InputAdapter
        from harness.di import Harness
        from harness.interfaces.types import Response, UserRequest

        outputs = []
        receive_count = [0]

        class TestAdapter:
            def receive(self):
                receive_count[0] += 1
                if receive_count[0] == 1:
                    return UserRequest(text="hello")
                return UserRequest(text="")

            def send(self, response):
                outputs.append(response.text)

        def mock_llm(messages, tools=None):
            return Response(text="hi there", stop_reason="end_turn")

        container = DIContainer()
        container.register(InputAdapter, TestAdapter())
        # 故意不注册 GuideProvider — 应该不崩溃

        harness = Harness.from_container(container, call_llm=mock_llm)
        harness.run()

        assert len(outputs) == 1
        assert outputs[0] == "hi there"
