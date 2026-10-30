# -*- coding: utf-8 -*-
"""宿主识别：一次 AI 调查同时补身份与二级插件，并写入身份映射"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.adapters.astrbot import HostResolver  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class _FakeLLM:
    def __init__(self, payload=None, reply=""):
        self.api_key = "fake"
        self.payload = payload or {}
        self.reply = reply
        self.calls = 0

    async def generate_with_tools(self, **kwargs):
        self.calls += 1
        if self.reply:
            return self.reply, []
        return json.dumps(self.payload, ensure_ascii=False), []


class _BoomLLM:
    api_key = "fake"
    calls = 0

    async def generate_with_tools(self, **kwargs):
        _BoomLLM.calls += 1
        raise AssertionError("不应该调用执行 AI")


class HostResolverAITest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_host_ai_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        plugins = self.tmp / "plugins"
        unknown = plugins / "plugin_x"
        unknown.mkdir(parents=True)
        (unknown / "main.py").write_text("print('x')\n", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_host_dir(self, config=None):
        host_dir = self.tmp / "host"
        host_dir.mkdir(exist_ok=True)
        (host_dir / "plugins").mkdir(exist_ok=True)
        if config is not None:
            (host_dir / "config.json").write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
        return host_dir

    def test_unknown_host_gets_identity_and_components_in_one_ai_call(self):
        host_dir = self._make_host_dir({"theme": "dark"})
        (host_dir / "plugins").mkdir(exist_ok=True)
        plugin = host_dir / "plugins" / "plugin_x"
        plugin.mkdir()
        (plugin / "main.py").write_text("print('x')\n", encoding="utf-8")

        fake = _FakeLLM({
            "host": {
                "name": "小安",
                "platform": "qq",
                "internal_uid": "10001",
                "evidence": ["config.json"],
            },
            "components": [{
                "component_id": "plugin_x",
                "name": "X 插件",
                "entry": "main.py",
                "capabilities": ["event:message"],
            }],
        })
        result = asyncio.run(HostResolver(self.store, llm=fake).resolve(
            str(host_dir), {"host_id": "astrbot"}
        ))

        self.assertEqual(fake.calls, 1)
        self.assertTrue(result["ai"]["used"])
        self.assertTrue(result["ai"]["identity"])
        self.assertEqual(result["host"]["name"], "小安")
        self.assertEqual(result["host"]["platform"], "qq")
        self.assertEqual(result["host"]["internal_uid"], "10001")
        self.assertEqual(result["host"]["manifest"]["identity_source"]["platform"], "ai")
        self.assertTrue(result["context_id"])

        components = {c["component_id"]: c for c in self.store.list_components("astrbot")}
        self.assertEqual(components["plugin_x"]["source"], "ai")
        self.assertEqual(components["plugin_x"]["name"], "X 插件")

        contexts = self.store.list_contexts(host_id="astrbot")
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["platform"], "qq")
        self.assertEqual(contexts[0]["external_id"], "10001")
        self.assertEqual(contexts[0]["external_type"], "host")

        hosts = self.store.list_hosts()
        self.assertEqual(hosts[0]["host_id"], "astrbot")
        self.assertEqual(hosts[0]["manifest"]["context_id"], result["context_id"])

    def test_complete_host_skips_ai(self):
        host_dir = self._make_host_dir({
            "bot_name": "助手",
            "platform": [{"id": "onebot_napcat", "type": "aiocqhttp"}],
        })
        (host_dir / "plugins").mkdir(exist_ok=True)
        _BoomLLM.calls = 0
        result = asyncio.run(HostResolver(self.store, llm=_BoomLLM()).resolve(
            str(host_dir), {"host_id": "astrbot"}
        ))

        self.assertEqual(_BoomLLM.calls, 0)
        self.assertFalse(result["ai"]["used"])
        self.assertEqual(result["host"]["name"], "助手")
        self.assertEqual(result["host"]["internal_uid"], "onebot_napcat")
        self.assertEqual(len(self.store.list_contexts(host_id="astrbot")), 1)

    def test_hints_are_not_overridden_by_ai(self):
        host_dir = self._make_host_dir({"theme": "dark"})
        (host_dir / "plugins").mkdir(exist_ok=True)
        fake = _FakeLLM({
            "host": {"name": "AI 名称", "platform": "qq", "internal_uid": "99999"},
            "components": [],
        })
        result = asyncio.run(HostResolver(self.store, llm=fake).resolve(
            str(host_dir),
            {"host_id": "astrbot", "name": "人工名称", "platform": "bilibili"},
        ))
        self.assertEqual(result["host"]["name"], "人工名称")
        self.assertEqual(result["host"]["platform"], "bilibili")
        self.assertEqual(result["host"]["internal_uid"], "99999")

    def test_archive_binding_uses_context_identity(self):
        host_dir = self._make_host_dir({
            "bot_name": "助手",
            "platform": [{"id": "10001", "type": "aiocqhttp"}],
        })
        (host_dir / "plugins").mkdir(exist_ok=True)
        self.store.ensure_archive("self_persona", role="user", display_name="助手")
        result = asyncio.run(HostResolver(self.store, llm=_BoomLLM()).resolve(
            str(host_dir), {"host_id": "astrbot"}, archive_id="self_persona"
        ))
        self.assertEqual(self.store.resolve_binding("qq", "10001"), "self_persona")
        self.assertTrue(result["host"]["binding_id"])

    def test_upsert_context_keeps_host_id(self):
        self.store.upsert_context(
            "qq", "onebot_napcat", display_name="A", host_id="astrbot"
        )
        self.store.upsert_context("qq", "onebot_napcat", display_name="B")
        rows = self.store.list_contexts(host_id="astrbot")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], "B")
        self.assertEqual(rows[0]["host_id"], "astrbot")


if __name__ == "__main__":
    unittest.main()
