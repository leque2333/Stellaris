# -*- coding: utf-8 -*-
"""统一知识库：入库、检索、列表、删除"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.knowledge import KnowledgeBase  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_kb_"))
        self.kb = KnowledgeBase(SQLiteStore(self.tmp / "memory.db"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_search_list_delete(self):
        result = self.kb.ingest(
            "测试手册",
            "中枢测试口令是蓝色风筝。\n\n另一个段落用于分块。",
            source="unit",
            tags=["test"],
        )
        self.assertGreaterEqual(result["chunks"], 1)
        hits = self.kb.search("蓝色风筝", limit=3)
        self.assertTrue(hits)
        self.assertIn("蓝色风筝", hits[0]["content"])
        self.assertEqual(len(self.kb.list()), 1)
        self.assertTrue(self.kb.delete(result["source_id"]))
        self.assertEqual(self.kb.list(), [])

    def test_empty_content_rejected(self):
        with self.assertRaises(ValueError):
            self.kb.ingest("空", "")


if __name__ == "__main__":
    unittest.main()
