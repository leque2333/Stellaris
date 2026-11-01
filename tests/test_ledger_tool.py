# -*- coding: utf-8 -*-
"""hub_ledger_query：只读账本查询系统工具。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core import ledger_tool  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402
from agent_core.tool_shelves import system_tools  # noqa: E402


class LedgerToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "memory.db")
        self.store.record_usage(
            session_type="qq", model="gemini-3.8-flash",
            prompt_tokens=1000, completion_tokens=100, cached_tokens=0,
            cost_usd=0.01, source="qq_chat", credential_type="master",
        )
        self.store.record_usage(
            session_type="qq", model="gemini-3.8-flash",
            prompt_tokens=2000, completion_tokens=200, cached_tokens=0,
            cost_usd=0.02, source="qq_chat", credential_type="master",
        )
        self.patch = patch("agent_core.storage.get_store", return_value=self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_spec_and_registry(self):
        self.assertEqual(ledger_tool.tool_spec()["function"]["name"], "hub_ledger_query")
        self.assertTrue(system_tools.is_system("hub_ledger_query"))

    def test_summary(self):
        data = ledger_tool.execute("summary")
        self.assertTrue(data["ok"])
        self.assertEqual(data["overall"]["calls"], 2)
        self.assertEqual(data["overall"]["tokens"], 3300)
        self.assertAlmostEqual(data["overall"]["cost"], 0.03)

    def test_recent(self):
        data = ledger_tool.execute("recent", limit=1)
        self.assertEqual(len(data["recent"]), 1)
        self.assertEqual(data["recent"][0]["model"], "gemini-3.8-flash")

    def test_daily(self):
        data = ledger_tool.execute("daily", days=2)
        self.assertTrue(data["daily"])
        self.assertEqual(sum(item["tokens"] for item in data["daily"]), 3300)

    def test_unknown_action(self):
        self.assertFalse(ledger_tool.execute("delete")["ok"])


if __name__ == "__main__":
    unittest.main()
