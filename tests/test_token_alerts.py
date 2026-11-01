# -*- coding: utf-8 -*-
"""Token 预警：余额阈值、动态上限、每日只提醒一次。"""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from modules import token_alerts  # noqa: E402


class _Store:
    def __init__(self, path):
        self.path = str(path)


class TokenAlertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "memory.db"
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE token_usage (timestamp REAL, prompt_tokens INTEGER, completion_tokens INTEGER, "
            "cached_tokens INTEGER, cost_usd REAL, source TEXT)"
        )
        now = time.time()
        for days_ago, tokens in ((3, 1000), (2, 1000), (1, 1000), (0, 5000)):
            con.execute(
                "INSERT INTO token_usage VALUES (?, ?, 0, 0, 0.1, 'test')",
                (now - days_ago * 86400, tokens),
            )
        con.commit()
        con.close()
        self.env = patch.dict("os.environ", {
            "HUB_TOKEN_ALERTS_PATH": str(self.root / "alerts.json"),
            "HUB_TOKEN_ALERTS_STATE": str(self.root / "state.json"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _config(self):
        return {"credits": {"balances": [
            {"currency": "USD", "total": 1.0, "is_primary": True},
            {"currency": "CNY", "total": 40.0, "is_primary": False},
        ]}}

    def test_balance_and_dynamic_token_alert(self):
        token_alerts.save_settings({"balance_thresholds": {"USD": 5, "CNY": 50}})
        data = token_alerts.evaluate(_Store(self.db), self._config(), mark=False)
        self.assertEqual(data["current"]["daily_limit"], 1500)
        types = {item["type"] for item in data["alerts"]}
        self.assertIn("balance:USD", types)
        self.assertIn("balance:CNY", types)
        self.assertIn("token", types)

    def test_notifies_once_per_day(self):
        token_alerts.save_settings({"balance_thresholds": {"USD": 5, "CNY": 50}})
        first = token_alerts.evaluate(_Store(self.db), self._config(), mark=True)
        second = token_alerts.evaluate(_Store(self.db), self._config(), mark=True)
        self.assertTrue(first["notified_today"])
        self.assertTrue(second["notified_today"])

    def test_save_per_currency_threshold(self):
        token_alerts.save_settings({"balance_thresholds": {"USD": 3.5, "CNY": 20}})
        settings = token_alerts.load_settings()
        self.assertEqual(settings["balance_thresholds"]["USD"], 3.5)
        self.assertEqual(settings["balance_thresholds"]["CNY"], 20)


if __name__ == "__main__":
    unittest.main()
