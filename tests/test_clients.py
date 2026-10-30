# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.clients import ClientRegistry  # noqa: E402


class ClientRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_clients_"))

    def _write(self, data):
        path = self.tmp / "clients.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_no_config_keeps_legacy(self):
        reg = ClientRegistry(self.tmp / "missing.json")
        result = reg.resolve("")
        self.assertIsNone(result.error)
        self.assertEqual(result.profile.id, "legacy")
        self.assertEqual(result.profile.default_actor, "master")

    def test_require_key(self):
        path = self._write({
            "require_key": True,
            "clients": [{"id": "app", "name": "App", "keys": ["hub_sk_app"]}],
        })
        reg = ClientRegistry(path)
        self.assertEqual(reg.resolve("").error, "missing_key")
        self.assertEqual(reg.resolve("bad").error, "invalid_key")
        result = reg.resolve("hub_sk_app")
        self.assertIsNone(result.error)
        self.assertEqual(result.profile.id, "app")

    def test_guest_actor_and_permissions(self):
        path = self._write({
            "clients": [{
                "id": "nextchat", "name": "NextChat", "keys": ["k1"],
                "channel": "nextchat", "default_actor": "guest",
                "allow_recall_memory": False, "allow_ingest_timeline": True,
            }],
        })
        reg = ClientRegistry(path)
        result = reg.resolve("k1")
        self.assertEqual(result.profile.default_actor, "guest")
        self.assertFalse(result.profile.allow_recall_memory)
        self.assertTrue(result.profile.allow_ingest_timeline)

    def test_configured_but_empty(self):
        path = self._write({"require_key": True, "clients": []})
        reg = ClientRegistry(path)
        self.assertEqual(reg.resolve("").error, "missing_key")
        self.assertEqual(reg.resolve("x").error, "invalid_key")

    def test_rate_limit(self):
        path = self._write({
            "clients": [{"id": "limited", "name": "Limited", "keys": ["k"], "rate_limit_per_minute": 2}],
        })
        reg = ClientRegistry(path)
        self.assertTrue(reg.allow_request("limited", 2))
        self.assertTrue(reg.allow_request("limited", 2))
        self.assertFalse(reg.allow_request("limited", 2))
        self.assertTrue(reg.allow_request("other", 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
