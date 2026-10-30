# -*- coding: utf-8 -*-
"""执行 AI 的只读工具与 JSON 解析（不调用真实模型）"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.connectors.ai_matcher import (  # noqa: E402
    AIMatcher, _ReadOnlyTools,
)


class ReadOnlyToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_ai_tools_"))
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({
            "group": {"name": "乌合之众喵", "id": "2000000001"},
            "sessdata": "super-secret-cookie",
        }, ensure_ascii=False), encoding="utf-8")
        self.tools = _ReadOnlyTools([self.tmp])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_search_redacts_secret(self):
        result = self.tools.search_text("乌合之众喵")
        self.assertIn("config.json", result)
        self.assertIn("乌合之众喵", result)
        result2 = self.tools.search_text("sessdata")
        self.assertIn("***", result2)
        self.assertNotIn("super-secret-cookie", result2)

    def test_read_file_redacts_secret(self):
        text = self.tools.read_file(str(self.config))
        self.assertIn("乌合之众喵", text)
        self.assertIn("***", text)
        self.assertNotIn("super-secret-cookie", text)

    def test_path_outside_roots_denied(self):
        outside = Path(tempfile.gettempdir()) / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        self.assertEqual(self.tools.read_file(str(outside)), "路径不在允许范围或不是文件")

    def test_sqlite_schema_and_select_only(self):
        db = self.tmp / "data.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE groups (id TEXT, name TEXT)")
        conn.execute("INSERT INTO groups VALUES ('2000000001', '乌合之众喵')")
        conn.commit()
        conn.close()

        schema = self.tools.sqlite_schema(str(db))
        self.assertIn("groups", schema)
        rows = json.loads(self.tools.sqlite_query(str(db), "SELECT * FROM groups"))
        self.assertEqual(rows[0]["name"], "乌合之众喵")
        self.assertIn("只允许 SELECT", self.tools.sqlite_query(str(db), "DELETE FROM groups"))


class ParseTest(unittest.TestCase):
    def test_parse_fenced_json(self):
        text = '```json\n{"matches":[{"platform":"qq","external_id":"x:1","display_name":"群"}]}\n```'
        matches = AIMatcher._parse(text)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["external_id"], "x:1")

    def test_parse_empty(self):
        self.assertEqual(AIMatcher._parse("没有找到"), [])


class _FakeLLM:
    api_key = "fake"

    async def generate_with_tools(self, **kwargs):
        return json.dumps({
            "matches": [{
                "platform": "qq",
                "external_id": "onebot_napcat:GroupMessage:2000000001",
                "display_name": "乌合之众喵",
                "external_type": "group",
                "confidence": 0.99,
                "evidence": ["config.json:2"],
            }]
        }, ensure_ascii=False), []


class _ScanLLM:
    api_key = "fake"

    async def generate_with_tools(self, **kwargs):
        return json.dumps({
            "users": [
                {
                    "platform": "qq", "uid": "1000000001", "name": "主人",
                    "plugin": "astrbot", "source": "data_v4.db:friend",
                    "extra": {"备注": "主人", "消息数": 12},
                    "evidence": ["data_v4.db:friend"],
                },
                {
                    "platform": "qq", "uid": "2000000001", "name": "乌合之众喵",
                    "plugin": "astrbot", "source": "data_v4.db:group",
                    "extra": {"群号": "2000000001"},
                    "evidence": ["data_v4.db:group"],
                },
                {
                    "platform": "qq", "uid": "onebot_napcat", "name": "NapCat",
                    "plugin": "astrbot", "source": "cmd_config.json:platform",
                    "extra": {},
                    "evidence": ["cmd_config.json:platform"],
                },
                {
                    "platform": "bilibili", "uid": "3000000001", "name": "B站号",
                    "plugin": "bilibili", "source": "bili.json:uid",
                    "extra": {"粉丝": 3},
                    "evidence": ["bili.json:uid"],
                },
                {
                    "platform": "qq", "uid": "10001", "name": "",
                    "plugin": "other", "source": "app.log:uid",
                    "extra": {},
                    "evidence": ["app.log:1"],
                },
            ]
        }, ensure_ascii=False), []


class AIMatcherTest(unittest.TestCase):
    def test_match_caps_confidence_and_marks_source(self):
        import asyncio
        tmp = Path(tempfile.mkdtemp(prefix="anan_ai_match_"))
        try:
            matcher = AIMatcher(roots=[tmp], llm=_FakeLLM())
            result = asyncio.run(matcher.match("乌合之众喵", "2000000001"))
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["matches"]), 1)
            self.assertEqual(result["matches"][0]["match_type"], "ai")
            self.assertEqual(result["matches"][0]["source"], "execution_ai")
            self.assertLessEqual(result["matches"][0]["confidence"], 0.85)

            people = asyncio.run(
                matcher.match("乌合之众喵", "2000000001", people_only=True)
            )
            self.assertTrue(people["ok"])
            self.assertEqual(people["matches"], [])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_scan_all_keeps_all_uids_and_provenance(self):
        import asyncio
        tmp = Path(tempfile.mkdtemp(prefix="anan_ai_scan_"))
        try:
            matcher = AIMatcher(roots=[tmp], llm=_ScanLLM())
            result = asyncio.run(matcher.scan_all("qq"))
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["users"]), 5)
            by_uid = {item["uid"]: item for item in result["users"]}
            self.assertEqual(by_uid["1000000001"]["plugin"], "astrbot")
            self.assertEqual(by_uid["1000000001"]["extra"]["备注"], "主人")
            self.assertEqual(by_uid["2000000001"]["source"], "data_v4.db:group")
            self.assertIn("onebot_napcat", by_uid)
            self.assertIn("3000000001", by_uid)
            self.assertEqual(by_uid["10001"]["extra"], {})
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
