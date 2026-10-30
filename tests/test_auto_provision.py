# -*- coding: utf-8 -*-
"""陌生模块首次 /v1 接入：自动登记模块并抓一次信息卡/二级插件"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.auto_provision import detect_host, provision_async  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class AutoProvisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_auto_"))
        self.store = SQLiteStore(self.tmp / "memory.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_host(self, host_id, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hosts = {h["host_id"]: h for h in self.store.list_hosts()}
            if host_id in hosts:
                return hosts[host_id]
            time.sleep(0.1)
        return None

    def _wait_context(self, host_id, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = [c for c in self.store.list_contexts() if c.get("host_id") == host_id]
            if rows:
                return rows
            time.sleep(0.05)
        return []

    def test_detect_only_explicit_headers(self):
        self.assertIsNone(detect_host({"User-Agent": "astrbot/4.4.0"}))
        explicit = detect_host({
            "User-Agent": "demo/1.0",
            "X-Host-Id": "demo_host",
            "X-Host-Name": "Demo 宿主",
        })
        self.assertEqual(explicit["host_id"], "demo_host")
        self.assertEqual(explicit["name"], "Demo 宿主")
        self.assertIsNone(detect_host({"User-Agent": "python-urllib/3.14"}))

    def test_explicit_host_registers_minimal_module(self):
        provision_async(self.store, {
            "User-Agent": "demo/1.0",
            "X-Host-Id": "demo_host",
            "X-Host-Name": "Demo 宿主",
            "X-Platform": "web",
        })
        host = self._wait_host("demo_host")
        self.assertIsNotNone(host)
        self.assertEqual(host["name"], "Demo 宿主")
        self.assertEqual(host["platform"], "web")

    def test_bilibili_host_uid_and_encoded_name(self):
        provision_async(self.store, {
            "User-Agent": "bili-module/1.0",
            "X-Host-Id": "bilibili",
            "X-Host-Name": "B%E7%AB%99",
            "X-Host-UID": "123456",
            "X-Platform": "bilibili",
        })
        host = self._wait_host("bilibili")
        self.assertIsNotNone(host)
        self.assertEqual(host["name"], "B站")
        self.assertEqual(host["platform"], "bilibili")
        self.assertEqual(host["internal_uid"], "123456")
        contexts = self._wait_context("bilibili")
        self.assertEqual([c["external_id"] for c in contexts], ["123456"])



if __name__ == "__main__":
    unittest.main()
