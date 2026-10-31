# -*- coding: utf-8 -*-
"""部署自检逻辑：docker 网段覆盖判定、汇总、输出格式。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core import deploy_check  # noqa: E402


class DockerSubnetTest(unittest.TestCase):
    def test_missing_when_only_loopback(self):
        config = {"local_only": True, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertTrue(deploy_check.docker_subnet_missing(config, has_docker=True))

    def test_covered_by_wide_cidr(self):
        config = {"local_only": True, "allow_cidrs": ["172.16.0.0/12"]}
        self.assertFalse(deploy_check.docker_subnet_missing(config, has_docker=True))

    def test_covered_by_supernet(self):
        config = {"local_only": True, "allow_cidrs": ["172.0.0.0/8"]}
        self.assertFalse(deploy_check.docker_subnet_missing(config, has_docker=True))

    def test_ignored_without_docker(self):
        config = {"local_only": True, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertFalse(deploy_check.docker_subnet_missing(config, has_docker=False))

    def test_ignored_when_local_only_off(self):
        config = {"local_only": False, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertFalse(deploy_check.docker_subnet_missing(config, has_docker=True))

    def test_bad_cidr_does_not_crash(self):
        config = {"local_only": True, "allow_cidrs": ["not-a-cidr"]}
        self.assertTrue(deploy_check.docker_subnet_missing(config, has_docker=True))


class SummaryTest(unittest.TestCase):
    def test_counts(self):
        checks = [
            deploy_check.Check("a", "ok", ""),
            deploy_check.Check("b", "warn", ""),
            deploy_check.Check("c", "fail", ""),
        ]
        summary = deploy_check.summarize(checks)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["warn"], 1)
        self.assertEqual(summary["fail"], 1)
        self.assertTrue(summary["failed"])

    def test_all_ok(self):
        summary = deploy_check.summarize([deploy_check.Check("a", "ok", "")])
        self.assertFalse(summary["failed"])

    def test_format_lines_have_icons(self):
        lines = deploy_check.format_lines([
            deploy_check.Check("端口", "ok", "在监听"),
            deploy_check.Check("挂载", "fail", "源不存在"),
        ])
        self.assertIn("✓", lines[0])
        self.assertIn("✗", lines[1])


class ClientCheckTest(unittest.TestCase):
    def test_check_clients_runs(self):
        checks = deploy_check.check_clients()
        self.assertTrue(checks)
        self.assertIn(checks[0].status, {"ok", "warn", "fail"})


if __name__ == "__main__":
    unittest.main()
