"""PolicyRegistry 匹配优先级测试。"""

from harness.core.governance.policy import (
    PolicyRegistry, ToolPolicy, RetryPolicy, RUNTIME_TOOL_NAMES,
)


def test_lookup_returns_default_when_no_rule():
    r = PolicyRegistry()
    p = r.lookup("anything")
    assert isinstance(p, ToolPolicy)
    assert p.timeout == 60.0
    assert p.gate is False


def test_exact_match_beats_wildcard():
    r = PolicyRegistry()
    r.register("mcp_*", ToolPolicy(timeout=30))
    r.register("mcp_fs_read", ToolPolicy(timeout=5))
    assert r.lookup("mcp_fs_read").timeout == 5
    assert r.lookup("mcp_other").timeout == 30


def test_later_wildcard_wins():
    r = PolicyRegistry()
    r.register("mcp_*", ToolPolicy(timeout=10))
    r.register("mcp_fs_*", ToolPolicy(timeout=20))
    assert r.lookup("mcp_fs_read").timeout == 20


def test_set_default_overrides_builtin():
    r = PolicyRegistry()
    r.set_default(ToolPolicy(timeout=1))
    assert r.lookup("unmatched").timeout == 1


def test_runtime_tool_names_are_direct():
    # 模块级单例已预注册 runtime tools 为 executor="direct"
    from harness.core.governance.policy import policy_registry
    assert set(RUNTIME_TOOL_NAMES) == {
        "spawn_workflow", "end_workflow", "finish_agent",
        "talk_to", "list_agents",
    }
    for name in RUNTIME_TOOL_NAMES:
        assert policy_registry.lookup(name).executor == "direct"


def test_retry_policy_defaults():
    rp = RetryPolicy()
    assert rp.max_attempts == 1
    assert rp.backoff == "exponential"
    assert rp.retry_on == ("timeout", "exception")
