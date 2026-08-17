"""CLI --resume/--force 入口测试。"""

import pytest

from main import build_parser
from harness.core.session.config import load_session_config


class TestParser:
    def test_run_accepts_resume_and_force(self):
        args = build_parser().parse_args(["run", "--resume", "conv-1", "--force"])
        assert args.resume == "conv-1"
        assert args.force is True

    def test_defaults(self):
        args = build_parser().parse_args(["run"])
        assert args.resume is None
        assert args.force is False

    def test_workflow_accepts_resume(self):
        args = build_parser().parse_args(
            ["workflow", "s.py", "--resume", "conv-9"])
        assert args.resume == "conv-9"


class TestConfigLoading:
    def test_sessions_section_parsed(self, tmp_path):
        cfg = tmp_path / "harness.yaml"
        cfg.write_text("sessions:\n  root: /tmp/s\n  enabled: true\n",
                       encoding="utf-8")
        sc = load_session_config(str(cfg))
        assert sc.root == "/tmp/s" and sc.enabled is True

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        sc = load_session_config(str(tmp_path / "nope.yaml"))
        assert sc.enabled is True and sc.root.endswith("sessions")
