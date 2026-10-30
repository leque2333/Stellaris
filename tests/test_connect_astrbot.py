# -*- coding: utf-8 -*-
"""一键连接器（scripts/connect_astrbot.py）的纯逻辑测试。

不联网、不改生产配置：只测"改配置"和"准备 Key"这两块。
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "connect_astrbot.py"

spec = importlib.util.spec_from_file_location("connect_astrbot", SCRIPT)
conn = importlib.util.module_from_spec(spec)
sys.modules["connect_astrbot"] = conn
spec.loader.exec_module(conn)


BASE_CONFIG = {
    "config_version": 5,
    "provider_sources": [
        {"id": "gemini_embedding_source", "type": "gemini_embedding", "embedding_api_key": "x"},
    ],
    "provider": [
        {"id": "gemini_embedding_inst", "enable": False, "provider_source_id": "gemini_embedding_source"},
    ],
    "provider_settings": {"provider_pool": []},
    "agent_runner": {"runner_type": "local", "config": {"model": {"fallback_provider_ids": ["gemini_main"]}}},
    "platform": [{"id": "onebot_napcat", "type": "aiocqhttp", "enable": True}],
    "plugin_set": ["astrbot_plugin_group_observer"],
    "admins_id": ["1000000001"],
    "dashboard": {"enable": True, "port": 6185},
}


class PatchCmdConfigTest(unittest.TestCase):
    def test_adds_hub_provider_and_default(self):
        out = conn.patch_cmd_config(
            json.loads(json.dumps(BASE_CONFIG)),
            "http://192.168.3.4:18895/v1", "hub-sk-test", "gemini-3.8-flash",
        )
        sources = {s["id"]: s for s in out["provider_sources"]}
        self.assertEqual(sources["anan_hub_source"]["api_base"], "http://192.168.3.4:18895/v1")
        self.assertEqual(sources["anan_hub_source"]["api_key"], ["hub-sk-test"])

        providers = {p["id"]: p for p in out["provider"]}
        self.assertTrue(providers["anan_hub_main"]["enable"])
        self.assertEqual(providers["anan_hub_main"]["model"], "gemini-3.8-flash")
        self.assertEqual(providers["anan_hub_main"]["provider_source_id"], "anan_hub_source")
        self.assertEqual(out["agent_runner"]["config"]["model"]["provider_id"], "anan_hub_main")

    def test_keeps_existing_fields(self):
        out = conn.patch_cmd_config(
            json.loads(json.dumps(BASE_CONFIG)),
            "http://127.0.0.1:18895/v1", "k", "m",
        )
        self.assertEqual(out["admins_id"], ["1000000001"])
        self.assertEqual(out["dashboard"]["port"], 6185)
        self.assertEqual(out["platform"][0]["type"], "aiocqhttp")
        self.assertIn("astrbot_plugin_group_observer", out["plugin_set"])
        self.assertEqual(
            out["agent_runner"]["config"]["model"]["fallback_provider_ids"], ["gemini_main"]
        )

    def test_idempotent(self):
        once = conn.patch_cmd_config(json.loads(json.dumps(BASE_CONFIG)), "http://x:1/v1", "k", "m")
        twice = conn.patch_cmd_config(once, "http://x:1/v1", "k", "m")
        self.assertEqual(len([s for s in twice["provider_sources"] if s["id"] == "anan_hub_source"]), 1)
        self.assertEqual(len([p for p in twice["provider"] if p["id"] == "anan_hub_main"]), 1)
        self.assertEqual(twice["provider_settings"]["provider_pool"].count("anan_hub_main"), 1)

    def test_updates_existing_values(self):
        once = conn.patch_cmd_config(json.loads(json.dumps(BASE_CONFIG)), "http://old:1/v1", "old-key", "old-model")
        twice = conn.patch_cmd_config(once, "http://new:2/v1", "new-key", "new-model")
        src = next(s for s in twice["provider_sources"] if s["id"] == "anan_hub_source")
        prov = next(p for p in twice["provider"] if p["id"] == "anan_hub_main")
        self.assertEqual(src["api_base"], "http://new:2/v1")
        self.assertEqual(src["api_key"], ["new-key"])
        self.assertEqual(prov["model"], "new-model")

    def test_does_not_mutate_input(self):
        original = json.loads(json.dumps(BASE_CONFIG))
        conn.patch_cmd_config(original, "http://x:1/v1", "k", "m")
        self.assertEqual(original, BASE_CONFIG)


class HubKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="connect_key_"))
        self.path = self.tmp / "clients.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_file_with_astrbot_client(self):
        key = conn.ensure_hub_key(self.path)
        self.assertEqual(key, conn.DEFAULT_KEY)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        ids = [c["id"] for c in data["clients"]]
        self.assertEqual(ids, ["astrbot"])
        self.assertFalse(data["require_key"])

    def test_reuses_existing_key(self):
        self.path.write_text(json.dumps({
            "require_key": True,
            "clients": [{"id": "astrbot", "keys": ["hub_sk_existing"]}],
        }), encoding="utf-8")
        self.assertEqual(conn.ensure_hub_key(self.path), "hub_sk_existing")

    def test_idempotent(self):
        conn.ensure_hub_key(self.path)
        conn.ensure_hub_key(self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["clients"]), 1)

    def test_dry_run_does_not_write(self):
        conn.ensure_hub_key(self.path, dry_run=True)
        self.assertFalse(self.path.exists())


class DetectTest(unittest.TestCase):
    def test_explicit_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="connect_dir_"))
        try:
            (tmp / "cmd_config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(conn.detect_astrbot_data(str(tmp)), tmp)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_explicit_dir(self):
        self.assertIsNone(conn.detect_astrbot_data(str(Path(tempfile.gettempdir()) / "no_such_astrbot_x")))

    def test_safe_resolve_survives_broken_path(self):
        # 关键是不抛异常；返回 Path 或 None 都可接受（不同 Python 版本 resolve 行为不同）
        try:
            result = conn.safe_resolve(Path("D:/__no_such_path_xyz__/data"))
        except Exception as exc:  # pragma: no cover
            self.fail(f"safe_resolve 不应抛异常: {exc}")
        self.assertTrue(result is None or isinstance(result, Path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
