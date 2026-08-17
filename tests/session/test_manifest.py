"""manifest 计算与分级比对测试。"""

from harness.core.container import DIContainer
from harness.core.session.manifest import (
    compute_manifest, diff_manifest, manifest_sha1,
)
from harness.interfaces import (
    ContextAssembler, GuideProvider, MCPAdapter, SystemToolProvider,
)
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


class GuideNoFingerprint:
    def get_guides(self, ctx):
        return None


class GuideBadFingerprint:
    """fingerprint() 抛异常 → 应回落默认 {"id": 类全限定名}。"""

    def get_guides(self, ctx):
        return None

    def fingerprint(self):
        raise RuntimeError("boom")


class GuideNonDictFingerprint:
    """fingerprint() 返回非 dict → 应回落默认 {"id": 类全限定名}。"""

    def get_guides(self, ctx):
        return None

    def fingerprint(self):
        return ["not", "a", "dict"]


class GuideSharedFingerprint:
    """fingerprint() 返回共享可变对象 —— 钉死 manifest 不别名组件内部 dict。"""

    SHARED = {"content_sha1": ["ab12"]}

    def get_guides(self, ctx):
        return None

    def fingerprint(self):
        return self.SHARED


class ProviderX:
    def get_tools(self):
        return [ToolDefinition(name="bash"), ToolDefinition(name="talk_to")]

    def execute(self, name, args):
        return None


class FakeLLM:
    def __init__(self, model):
        self.model = model


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
        # 注册未实现 fingerprint() 的 guide → 退化为默认 {"id": 类全限定名}
        m = compute_manifest(_container(guide=GuideNoFingerprint()),
                             cached_tools=[], call_llm=None)
        assert m["GuideProvider"]["id"].endswith("GuideNoFingerprint")

    def test_fingerprint_exception_falls_back_to_id(self):
        m = compute_manifest(_container(guide=GuideBadFingerprint()),
                             cached_tools=[], call_llm=None)
        assert m["GuideProvider"]["id"].endswith("GuideBadFingerprint")

    def test_fingerprint_non_dict_falls_back_to_id(self):
        m = compute_manifest(_container(guide=GuideNonDictFingerprint()),
                             cached_tools=[], call_llm=None)
        assert set(m["GuideProvider"]) == {"id"}
        assert m["GuideProvider"]["id"].endswith("GuideNonDictFingerprint")

    def test_manifest_does_not_alias_component_fingerprint(self):
        # manifest 深拷贝指纹：事后原地 mutation 不得污染组件内部 dict
        guide = GuideSharedFingerprint()
        m = compute_manifest(_container(guide=guide),
                             cached_tools=[], call_llm=None)
        m["GuideProvider"]["content_sha1"].append("zz")
        assert guide.SHARED == {"content_sha1": ["ab12"]}


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
        assert not diff.soft  # 硬失败抑制软告警

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

    def test_llm_model_change_is_soft(self):
        old = compute_manifest(_container(), cached_tools=ProviderX().get_tools(),
                               call_llm=FakeLLM("gpt-4o"))
        new = compute_manifest(_container(), cached_tools=ProviderX().get_tools(),
                               call_llm=FakeLLM("gpt-5"))
        diff = diff_manifest(old, new, used_tool_names=set())
        assert not diff.hard
        assert any("model" in s for s in diff.soft)

    def test_identical_is_ok(self):
        m = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        diff = diff_manifest(m, m, used_tool_names=set())
        assert diff.ok

    def test_mcp_only_assembly_used_tool_ok(self):
        # 仅注册 MCPAdapter 的装配：工具名并集须覆盖 MCPAdapter 键，
        # 历史中用过的 MCP 工具不得误报硬失败
        c = DIContainer()
        c.register(ContextAssembler, AsmA())
        c.register(MCPAdapter, ProviderX())
        mcp_tools = [ToolDefinition(name="mcp_tool")]
        old = compute_manifest(c, cached_tools=mcp_tools)
        new = compute_manifest(c, cached_tools=mcp_tools)
        diff = diff_manifest(old, new, used_tool_names={"mcp_tool"})
        assert diff.ok

    def test_old_none_is_ok(self):
        # 首启语义：无历史 manifest 时全部跳过
        new = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        diff = diff_manifest(None, new, used_tool_names={"anything"})
        assert diff.ok

    def test_non_dict_entries_tolerated(self):
        # 手工编辑/跨版本存量 manifest 的非 dict 值不得让 diff 崩溃
        old = {"ContextAssembler": "x", "llm": "gpt-4",
               "SystemToolProvider": ["not-a-dict"],
               "MCPAdapter": {"tool_names": None}}
        new = compute_manifest(_container(), cached_tools=ProviderX().get_tools())
        diff = diff_manifest(old, new, used_tool_names=set())
        assert isinstance(diff.hard, list) and isinstance(diff.soft, list)
