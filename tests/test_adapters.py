# -*- coding: utf-8 -*-
"""插件适配器注册表：内置 AstrBot + 生成适配器的归一化接口。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from adapters import Adapter, AdapterRegistry, AstrBotAdapter, RequestView  # noqa: E402
from adapters.declarative import DeclarativeAdapter  # noqa: E402


class _DemoAdapter(Adapter):
    plugin_id = "demo"
    priority = 5

    def detect(self, request):
        return 1.0 if request.header("X-Host-Id") == "demo" else 0.0

    def context(self, request):
        return {
            "plugin_id": self.plugin_id,
            "host_id": "demo",
            "platform": "demo",
            "uid": request.header("X-User-Id"),
        }


class AdapterRegistryTest(unittest.TestCase):
    def test_request_view_is_case_insensitive(self):
        view = RequestView(headers={"x-user-id": "10001"})
        self.assertEqual(view.header("X-User-Id"), "10001")

    def test_registry_resolves_demo_adapter(self):
        registry = AdapterRegistry()
        registry.register(_DemoAdapter())
        ctx = registry.resolve(RequestView(
            headers={"X-Host-Id": "demo", "X-User-Id": "10001"},
        ))
        self.assertEqual(ctx["plugin_id"], "demo")
        self.assertEqual(ctx["uid"], "10001")

    def test_declarative_adapter_maps_fields(self):
        adapter = DeclarativeAdapter({
            "plugin_id": "demo",
            "enabled": True,
            "match": {"path_prefix": "/demo", "header": {"X-Host-Id": "demo"}},
            "fields": {
                "platform": "'demo'",
                "uid": "headers.X-User-Id | body.sender.id",
                "host_id": "headers.X-Host-Id",
            },
        })
        view = RequestView(
            headers={"X-Host-Id": "demo", "X-User-Id": "10001"},
            body={"sender": {"id": "fallback"}},
            path="/demo/chat",
        )
        self.assertGreater(adapter.detect(view), 0.5)
        ctx = adapter.context(view)
        self.assertEqual(ctx["platform"], "demo")
        self.assertEqual(ctx["uid"], "10001")
        self.assertEqual(ctx["host_id"], "demo")

    def test_declarative_adapter_requires_match(self):
        adapter = DeclarativeAdapter({
            "plugin_id": "demo",
            "enabled": True,
            "match": {"path_prefix": "/other"},
            "fields": {},
        })
        self.assertEqual(adapter.detect(RequestView(path="/demo")), 0.0)

    def test_disabled_declarative_json_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.json").write_text(json.dumps({
                "plugin_id": "demo", "enabled": False,
                "match": {"path_prefix": "/demo"}, "fields": {},
            }), encoding="utf-8")
            with patch("adapters.registry._DECLARATIVE_DIR", root):
                registry = AdapterRegistry()
                self.assertEqual([a.plugin_id for a in registry.adapters()], [])

    def test_enabled_declarative_json_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.json").write_text(json.dumps({
                "plugin_id": "demo", "enabled": True,
                "match": {"path_prefix": "/demo"},
                "fields": {"platform": "'demo'"},
            }), encoding="utf-8")
            with patch("adapters.registry._DECLARATIVE_DIR", root):
                registry = AdapterRegistry()
                self.assertIn("demo", [a.plugin_id for a in registry.adapters()])

    def test_astrbot_detects_its_headers(self):
        adapter = AstrBotAdapter()
        self.assertGreater(
            adapter.detect(RequestView(headers={"X-Host-Id": "astrbot"})),
            0.5,
        )
        ctx = adapter.context(RequestView(
            headers={"X-Platform": "qq", "X-User-Id": "2986371638"},
            session_id="onebot_napcat:GroupMessage:784826874",
        ))
        self.assertEqual(ctx["platform"], "qq")
        self.assertEqual(ctx["uid"], "2986371638")
        self.assertEqual(ctx["group"], "784826874")


if __name__ == "__main__":
    unittest.main()
