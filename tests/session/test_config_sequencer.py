"""SessionConfig 与 Sequencer 测试。"""

from harness.core.session.config import SessionConfig, load_session_config
from harness.core.session.sequencer import Sequencer


class TestSessionConfig:
    def test_defaults_when_no_file(self, tmp_path):
        cfg = load_session_config(str(tmp_path / "missing.yaml"))
        assert cfg.enabled is True
        assert cfg.root == "./sessions"

    def test_defaults_when_none_path(self):
        cfg = load_session_config(None)
        assert cfg.enabled is True

    def test_sessions_section_parsed(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text("sessions:\n  root: /tmp/my-sessions\n  enabled: false\n",
                     encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.root == "/tmp/my-sessions"
        assert cfg.enabled is False

    def test_missing_section_uses_defaults(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text("llm:\n  model: gpt-4o\n", encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.enabled is True and cfg.root == "./sessions"

    def test_broken_yaml_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "harness.yaml"
        p.write_text(":::not yaml:::[", encoding="utf-8")
        cfg = load_session_config(str(p))
        assert cfg.enabled is True


class TestSequencer:
    def test_monotonic_from_zero(self):
        seq = Sequencer()
        assert [seq.next(), seq.next(), seq.next()] == [0, 1, 2]

    def test_start_offset_for_resume(self):
        seq = Sequencer(start=16)
        assert seq.next() == 16
        assert seq.next_value == 17

    def test_gaps_are_legal(self):
        """LSN 不校验连续：发号后崩溃产生合法空洞（设计 2.4）。"""
        seq = Sequencer()
        seq.next()  # 0 —— 假设该事件随崩溃丢失
        seq.next()  # 1
        assert seq.next() == 2  # 空洞 0/1 不报错
