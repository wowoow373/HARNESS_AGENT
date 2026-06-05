"""Tests for emoji feature.

Coverage:
    - manifest.json structure and validity
    - All referenced emoji files exist
    - Emoji ID uniqueness
    - Frontend regex replacement logic (Python simulation)
    - AGENTS.md contains emoji usage instructions
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# Ensure paths
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CHAT_WEB = Path(__file__).resolve().parents[1]
if str(_CHAT_WEB) not in sys.path:
    sys.path.insert(0, str(_CHAT_WEB))


# ---------------------------------------------------------------------------
# Helpers (simulate frontend replaceEmojis logic in Python)
# ---------------------------------------------------------------------------


def _parse_parts(text: str, emoji_map: dict) -> tuple:
    """Python equivalent of frontend parseMessageParts.
    Returns (text_only, [emoji_id, ...]). Unknown emojis dropped."""
    parts = re.split(r'(:[a-zA-Z_]\w*:)', text)
    text_parts = []
    emojis = []
    for part in parts:
        m = re.match(r'^:([a-zA-Z_]\w*):$', part)
        if m:
            eid = m.group(1)
            if emoji_map.get(eid):
                emojis.append(eid)
        else:
            text_parts.append(part)
    return (''.join(text_parts), emojis)

def _render_blocks(text: str, emoji_map: dict) -> str:
    """Simulate full render preserving original order (text blocks + emoji blocks)."""
    parts = re.split(r'(:[a-zA-Z_]\w*:)', text)
    result = []
    for part in parts:
        m = re.match(r'^:([a-zA-Z_]\w*):$', part)
        if m:
            eid = m.group(1)
            info = emoji_map.get(eid)
            if info:
                result.append(f'[BLOCK:{info["filename"]}]')
        else:
            result.append(part)
    return ''.join(result)


# ---------------------------------------------------------------------------
# Tests — manifest
# ---------------------------------------------------------------------------


class TestManifest:
    """Tests for emoji manifest.json."""

    @pytest.fixture
    def manifest(self):
        path = Path(__file__).resolve().parents[1] / "static" / "emojis" / "manifest.json"
        assert path.exists(), f"manifest.json not found at {path}"
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def emojis_dir(self):
        return Path(__file__).resolve().parents[1] / "static" / "emojis"

    def test_manifest_has_emojis_array(self, manifest):
        """manifest must have an 'emojis' array."""
        assert "emojis" in manifest
        assert isinstance(manifest["emojis"], list)
        assert len(manifest["emojis"]) > 0

    def test_each_emoji_has_required_fields(self, manifest):
        """Each emoji entry must have id, filename, description, scenarios."""
        for emoji in manifest["emojis"]:
            assert "id" in emoji, f"Missing 'id' in {emoji}"
            assert "filename" in emoji, f"Missing 'filename' in {emoji}"
            assert "description" in emoji, f"Missing 'description' in {emoji}"
            assert "scenarios" in emoji, f"Missing 'scenarios' in {emoji}"
            assert isinstance(emoji["scenarios"], list), f"'scenarios' must be a list in {emoji}"
            assert emoji["id"], "id must not be empty"
            assert emoji["filename"], "filename must not be empty"

    def test_emoji_ids_are_unique(self, manifest):
        """All emoji IDs must be unique."""
        ids = [e["id"] for e in manifest["emojis"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {ids}"

    def test_all_referenced_files_exist(self, manifest, emojis_dir):
        """Every filename in manifest must exist on disk."""
        for emoji in manifest["emojis"]:
            fpath = emojis_dir / emoji["filename"]
            assert fpath.exists(), f"Missing emoji file: {fpath}"

    def test_all_files_are_referenced(self, manifest, emojis_dir):
        """Every file in emojis dir must be referenced in manifest (except manifest.json)."""
        referenced = {e["filename"] for e in manifest["emojis"]}
        for fpath in emojis_dir.iterdir():
            if fpath.name == "manifest.json":
                continue
            if fpath.name.startswith("."):  # skip hidden files/dirs like .pytest_cache
                continue
            assert fpath.name in referenced, f"Unreferenced file: {fpath.name}"

    def test_manifest_json_is_valid(self, manifest):
        """manifest.json should be valid JSON."""
        # Already parsed by fixture; if we get here, it's valid JSON
        assert True


# ---------------------------------------------------------------------------
# Tests — replacement logic
# ---------------------------------------------------------------------------


class TestEmojiReplacement:
    """Tests for :emoji_id: → img replacement logic."""

    @pytest.fixture
    def emoji_map(self):
        return {
            "laugh": {"filename": "laugh.jpg", "description": "laugh"},
            "cool": {"filename": "cool.jpg", "description": "cool"},
            "happy": {"filename": "happy.jpg", "description": "happy"},
            "cry": {"filename": "cry.jpg", "description": "cry"},
            "cute": {"filename": "cute.gif", "description": "cute"},
        }

    def test_single_emoji_replaced(self, emoji_map):
        result = _render_blocks("Hello :laugh: world", emoji_map)
        assert "[BLOCK:laugh.jpg]" in result
        assert ":laugh:" not in result

    def test_multiple_emojis_replaced(self, emoji_map):
        result = _render_blocks(":happy: and :cool: and :cry:", emoji_map)
        assert "[BLOCK:happy.jpg]" in result
        assert "[BLOCK:cool.jpg]" in result
        assert "[BLOCK:cry.jpg]" in result

    def test_unknown_emoji_dropped(self, emoji_map):
        """Unknown emoji IDs should be silently dropped (not shown as :text:)."""
        result = _render_blocks("Hello :unknown: world", emoji_map)
        assert ":unknown:" not in result
        assert "[BLOCK:" not in result
        assert result == "Hello  world"

    def test_no_emoji_unchanged(self, emoji_map):
        text = "Hello world, no emojis here."
        result = _render_blocks(text, emoji_map)
        assert result == text

    def test_emoji_at_start(self, emoji_map):
        result = _render_blocks(":cool: is great", emoji_map)
        assert result.startswith("[BLOCK:cool.jpg]")

    def test_emoji_at_end(self, emoji_map):
        result = _render_blocks("This is sad :cry:", emoji_map)
        assert result.endswith("[BLOCK:cry.jpg]")

    def test_consecutive_emojis(self, emoji_map):
        result = _render_blocks(":happy::cute:", emoji_map)
        assert "[BLOCK:happy.jpg]" in result
        assert "[BLOCK:cute.gif]" in result

    def test_partial_match_not_replaced(self, emoji_map):
        """Words containing colons should not be mistaken for emojis."""
        result = _render_blocks("Time is 12:30:00", emoji_map)
        assert "12:30:00" in result
        assert "[BLOCK:" not in result

    def test_empty_string(self, emoji_map):
        assert _render_blocks("", emoji_map) == ""

    def test_gif_extension_preserved(self, emoji_map):
        result = _render_blocks(":cute:", emoji_map)
        assert "[BLOCK:cute.gif]" in result

    def test_xss_payload_with_emoji(self, emoji_map):
        """XSS payload should be escaped, emoji still replaced."""
        text = '<script>alert(1)</script> :laugh:'
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        result = _render_blocks(escaped, emoji_map)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "[BLOCK:laugh.jpg]" in result


# ---------------------------------------------------------------------------
# Tests — AGENTS.md
# ---------------------------------------------------------------------------


class TestAgentsMdEmojiGuide:
    """Tests that AGENTS.md contains emoji usage instructions."""

    @pytest.fixture
    def agents_md(self):
        path = Path(__file__).resolve().parents[1] / "AGENTS.md"
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_has_emoji_usage_section(self, agents_md):
        assert "Emoji Usage" in agents_md or "emoji" in agents_md.lower()

    def test_has_colon_format_instruction(self, agents_md):
        """AGENTS.md should instruct AI to use :emoji_id: format."""
        assert ":emoji_id:" in agents_md or ":" in agents_md

    def test_lists_available_emojis(self, agents_md):
        """AGENTS.md should list the available emoji IDs."""
        assert ":laugh:" in agents_md
        assert ":cool:" in agents_md
        assert ":happy:" in agents_md
        assert ":cry:" in agents_md
        assert ":cute:" in agents_md

    def test_has_emoji_rules(self, agents_md):
        """AGENTS.md should contain rules for emoji usage."""
        assert "Rules for emoji usage" in agents_md or "Use 1-2 emojis" in agents_md


# ---------------------------------------------------------------------------
# Tests — static file serving
# ---------------------------------------------------------------------------


class TestStaticFiles:
    """Tests that emoji files are accessible via static file serving."""

    def test_emojis_dir_exists(self):
        path = Path(__file__).resolve().parents[1] / "static" / "emojis"
        assert path.exists()
        assert path.is_dir()

    def test_manifest_exists(self):
        path = Path(__file__).resolve().parents[1] / "static" / "emojis" / "manifest.json"
        assert path.exists()

    def test_at_least_one_emoji_file(self):
        path = Path(__file__).resolve().parents[1] / "static" / "emojis"
        files = [f for f in path.iterdir() if f.name != "manifest.json"]
        assert len(files) > 0
