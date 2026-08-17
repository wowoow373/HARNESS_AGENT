"""配对修复计划测试：三条规则与重启集合过滤。"""

from harness.core.session.replay import Edge, ReplayResult, plan_redelivery


def _replay(pid, parent=None, edges=(), received=(), user_metas=(),
            status="crashed", final_output=""):
    return ReplayResult(pid=pid, conv_id="c", parent=parent,
                        edges=list(edges), received_msg_ids=set(received),
                        user_metas=list(user_metas),
                        status=status, final_output=final_output)


class TestMsgIdEdgeRule:
    def test_unreceived_edge_is_redelivered(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b", received=set())
        plans = plan_redelivery({"a": a, "b": b}, restarted={"a", "b"})
        assert len(plans) == 1
        assert plans[0].target == "b"
        assert plans[0].request.text == "在吗"
        assert plans[0].request.metadata["msg_id"] == "M-1"
        assert plans[0].request.metadata["from"] == "a"

    def test_received_edge_not_redelivered(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b", received={"M-1"})
        assert plan_redelivery({"a": a, "b": b}, restarted={"a", "b"}) == []

    def test_target_not_restarted_is_skipped(self):
        """Mode A：只重启 root，不发往未重启 agent。"""
        a = _replay("a", edges=[Edge("M-1", "a", "b", "talk_to", "在吗")])
        b = _replay("b")
        assert plan_redelivery({"a": a, "b": b}, restarted={"a"}) == []


class TestChildFinishedRule:
    def test_ended_child_without_parent_ack_is_redelivered(self):
        child = _replay("w1", parent="root", status="paused",
                        final_output="做完了")
        root = _replay("root", user_metas=[{"from": "user"}])
        plans = plan_redelivery({"root": root, "w1": child},
                                restarted={"root", "w1"})
        assert len(plans) == 1
        p = plans[0]
        assert p.dedup_key == "child_finished:w1"
        assert p.target == "root"
        assert p.request.metadata["type"] == "child_finished"
        assert p.request.metadata["from"] == "w1"
        assert "做完了" in p.request.text

    def test_parent_already_aware_skips(self):
        child = _replay("w1", parent="root", status="paused")
        root = _replay("root", user_metas=[{"from": "w1", "type": "talk_to"}])
        assert plan_redelivery({"root": root, "w1": child},
                               restarted={"root", "w1"}) == []

    def test_running_child_not_redelivered(self):
        child = _replay("w1", parent="root", status="crashed")  # 未结束
        root = _replay("root")
        assert plan_redelivery({"root": root, "w1": child},
                               restarted={"root", "w1"}) == []


class TestSpawnEntryRule:
    def test_missing_spawn_entry_is_topped_up(self):
        child = _replay("w1", parent="root", received=set())
        plans = plan_redelivery({"root": _replay("root"), "w1": child},
                                restarted={"w1"}, script_entry_prompts={
                                    "w1": "去干活"})
        assert any(p.dedup_key == "spawn_entry:w1" for p in plans)

    def test_received_spawn_entry_not_topped_up(self):
        child = _replay("w1", parent="root", received={"spawn_entry:w1"})
        assert plan_redelivery({"w1": child}, restarted={"w1"},
                               script_entry_prompts={"w1": "去干活"}) == []

    def test_new_agent_without_old_log_gets_entry(self):
        """半成品 spawn：agent 无旧日志（replays 缺失）→ 仍补投 entry。"""
        plans = plan_redelivery({"root": _replay("root")},
                                restarted={"root", "w1"},
                                script_entry_prompts={"w1": "去干活"})
        assert any(p.dedup_key == "spawn_entry:w1" and p.target == "w1"
                   for p in plans)


class TestDedupKey:
    def test_plans_carry_stable_dedup_keys(self):
        a = _replay("a", edges=[Edge("M-1", "a", "b", "publish", "进度")])
        plans = plan_redelivery({"a": a, "b": _replay("b")}, restarted={"b"})
        assert plans[0].dedup_key == "M-1"
