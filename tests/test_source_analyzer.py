# -*- coding: utf-8 -*-
"""源码分析 + 候选 spec 生成"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.source import SourceAnalyzer, SourceSynthesizer  # noqa: E402


class SourceAnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_source_"))
        (self.tmp / "pyproject.toml").write_text(
            '[project]\nname="demo"\ndependencies=["fastapi","sqlalchemy"]\n',
            encoding="utf-8",
        )
        (self.tmp / "app.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/events')\n"
            "def events():\n"
            "    logger.info('called')\n",
            encoding="utf-8",
        )
        cfg = self.tmp / "config"
        cfg.mkdir()
        (cfg / "settings.yaml").write_text("bot:\n  name: demo\n", encoding="utf-8")
        dbdir = self.tmp / "data"
        dbdir.mkdir()
        conn = sqlite3.connect(dbdir / "app.db")
        conn.execute("CREATE TABLE messages (id INTEGER, content TEXT, created_at TEXT, group_id TEXT)")
        conn.execute("INSERT INTO messages VALUES (1, 'hello', '2026-01-01', 'g1')")
        conn.commit()
        conn.close()
        tests = self.tmp / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_analyze_repo(self):
        result = SourceAnalyzer().analyze(str(self.tmp))
        self.assertTrue(result["ok"])
        self.assertIn("python", result["languages"])
        self.assertIn("FastAPI", result["frameworks"])
        self.assertIn("SQLAlchemy", result["frameworks"])
        self.assertTrue(result["configs"])
        self.assertEqual(result["databases"][0]["tables"][0]["table"], "messages")
        self.assertTrue(result["routes"])
        self.assertTrue(result["logs"])
        self.assertIn("app.py", result["entrypoints"])

    def test_generate_candidate_spec(self):
        result = asyncio.run(SourceSynthesizer().generate(str(self.tmp)))
        self.assertTrue(result["ok"])
        sqlite = [x for x in result["specs"] if x["kind"] == "sqlite"]
        self.assertTrue(sqlite)
        self.assertTrue(sqlite[0]["ok"])
        self.assertEqual(sqlite[0]["spec"]["options"]["table"], "messages")
        self.assertGreaterEqual(len(sqlite[0]["preview"]), 1)


if __name__ == "__main__":
    unittest.main()
