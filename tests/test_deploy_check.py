# -*- coding: utf-8 -*-
"""部署自检脚本的纯逻辑：docker 网段覆盖判定。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_deploy  # noqa: E402


class DockerSubnetTest(unittest.TestCase):
    def test_missing_when_only_loopback(self):
        config = {"local_only": True, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertTrue(check_deploy.docker_subnet_missing(config, has_docker=True))

    def test_covered_by_wide_cidr(self):
        config = {"local_only": True, "allow_cidrs": ["172.16.0.0/12"]}
        self.assertFalse(check_deploy.docker_subnet_missing(config, has_docker=True))

    def test_covered_by_supernet(self):
        config = {"local_only": True, "allow_cidrs": ["172.0.0.0/8"]}
        self.assertFalse(check_deploy.docker_subnet_missing(config, has_docker=True))

    def test_ignored_without_docker(self):
        config = {"local_only": True, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertFalse(check_deploy.docker_subnet_missing(config, has_docker=False))

    def test_ignored_when_local_only_off(self):
        config = {"local_only": False, "allow_cidrs": ["127.0.0.1/32"]}
        self.assertFalse(check_deploy.docker_subnet_missing(config, has_docker=True))

    def test_bad_cidr_does_not_crash(self):
        config = {"local_only": True, "allow_cidrs": ["not-a-cidr"]}
        self.assertTrue(check_deploy.docker_subnet_missing(config, has_docker=True))


class ClientCheckTest(unittest.TestCase):
    def test_check_clients_runs(self):
        checks = check_deploy.check_clients()
        self.assertTrue(checks)
        self.assertIn(checks[0].status, {"ok", "warn", "fail"})


if __name__ == "__main__":
    unittest.main()
