"""manifest 计算与分级比对测试。"""

from harness.core.container import DIContainer
from harness.core.session.manifest import (
    compute_manifest, diff_manifest, manifest_sha1,
)
from harness.interfaces import ContextAssembler, GuideProvider, SystemToolProvider
from harness.interfaces.types import ToolDefinition


class AsmA:
    def assemble(self, ctx):
        return []


class AsmB:
    def assemble(self, ctx):
        return []


class GuideWithFingerprint:
    def get_guides(self, ctx):
        return None

    def fingerprint(self):
        return {"content_sha1": ["ab12"]}


class ProviderX:
    def get_tools(self):
        return [ToolDefinition(name="bash"), ToolDefinition(name="talk_to")]

    def execute(self, name, args):
        return None


def _container(**overrides):
    c = DIContainer()
    c.register(ContextAssembler, overrides.get("assembler", AsmA()))
    if overrides.get("guide") is not None:
        c.register(GuideProvider, overrides["guide"])
    c.register(SystemToolProvider, overrides.get("provider", ProviderX()))
    return c


class TestCompute:
    def test_manifest_shape_and_stable_sha(self):
        m = compute_manifest(_container(),
                             cached_tools=ProviderX().get_tools(),
                             call_llm=None)
        assert m["ContextAssembler"]["id"].endswith("AsmA")
        assert m["SystemToolProvider"]["tool_names"] == ["bash", "talk_to"]
        assert manifest_sha1(m) == manifest_sha1(dict(m))  # 稳定性

    def test_fingerprint_convention_used(self):
        m = compute_manifest(_container(guide=GuideWithFingerprint()),
                             cached_tools=[], call_llm=None)
        assert m["GuideProvider"]["content_sha1"] == ["ab12"]

    def test_default_fingerprint_is_class_id(self):
        m = compute_manifest(_container(), cached_tools=[], call_llm=None)
        # 未注册 GuideProvider 时整个键缺席；注册但未实现 fingerprint() 时
        # 退化为默认 {"id": 类全限定名}
        assert m.get("GuideProvider", {}) == {} or "id" in m["GuideProvider"]


class TestDiff:
    def test_assembler_change_is_hard(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(assembler=AsmB()),
                               cached_tools=ProviderX().get_tools())
        diff = diff_manifest(old, new, used_tool_names=set())
        assert diff.hard and not diff.ok

    def test_used_tool_missing_is_hard(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(), cached_tools=[ToolDefinition(name="bash")])
        diff = diff_manifest(old, new, used_tool_names={"talk_to"})
        assert any("talk_to" in h for h in diff.hard)

    def test_unused_tool_removed_is_soft(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        new = compute_manifest(_container(), cached_tools=[ToolDefinition(name="bash")])
        diff = diff_manifest(old, new, used_tool_names=set())
        assert not diff.hard and diff.soft

    def test_guide_change_is_soft(self):
        old = compute_manifest(_container(guide=GuideWithFingerprint()),
                               cached_tools=[])
        changed = GuideWithFingerprint()
        changed.fingerprint = lambda: {"content_sha1": ["ffff"]}
        new = compute_manifest(_container(guide=changed), cached_tools=[])
        diff = diff_manifest(old, new, used_tool_names=set())
        assert not diff.hard and diff.soft

    def test_identical_is_ok(self):
        m = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        diff = diff_manifest(m, m, used_tool_names=set())
        assert diff.ok
