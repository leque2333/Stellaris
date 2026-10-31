# -*- coding: utf-8 -*-
"""
Token 账本聚合口径回归测试。

覆盖：
1. “合计”只返回 Token 与调用次数，不返回混合币种金额；
2. 金额 / Token / 调用次数按主控、执行凭证分开；
3. 同一凭证类型内按币种分别核算，RMB 与 CNY 合并；
4. 充值流水不进入消费统计；
5. 明细流水带 credential_type / currency，供页面展示归属。
"""

import calendar
import datetime
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console.modules import (
    model_service,  # noqa: E402
    system_service,  # noqa: E402
)


class TokenAnalyticsAccountingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_ledger_"))
        self.db = self.tmp / "memory.db"

        conn = sqlite3.connect(self.db)
        conn.execute(
            """
            CREATE TABLE token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                source TEXT DEFAULT 'universal_proxy',
                session_type TEXT DEFAULT '',
                tools TEXT DEFAULT '',
                credential_type TEXT DEFAULT '',
                currency TEXT DEFAULT 'USD'
            )
            """
        )
        conn.execute(
            """
            CREATE VIEW token_usage_ledger AS
            SELECT 'tu_' || id AS id,
                   timestamp,
                   model AS model_id,
                   prompt_tokens AS input_tokens,
                   completion_tokens AS output_tokens,
                   cached_tokens AS cache_tokens,
                   COALESCE(source, 'universal_proxy') AS source,
                   COALESCE(cost_usd, 0) AS cost_usd
            FROM token_usage
            """
        )
        conn.commit()
        conn.close()

        today_start = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        self.today_start = today_start
        today_ts = today_start + 60
        old_ts = today_start - 60

        # 主控：USD 两笔今日 + 一笔历史；CNY / RMB 各一笔今日（应合并为 CNY）
        self._insert("gemini-3.8-flash", 100, 50, 10, 0.20, "master", "USD", today_ts)
        self._insert("gemini-3.8-flash", 200, 100, 0, 0.05, "master", "USD", today_ts + 1)
        self._insert("deepseek-chat", 50, 25, 0, 0.05, "master", "CNY", today_ts + 2)
        self._insert("deepseek-chat", 60, 30, 0, 0.02, "master", "RMB", today_ts + 3)
        self._insert("gemini-3.8-flash", 200, 100, 0, 0.10, "master", "USD", old_ts + 1)

        # 执行：CNY 今日一笔；USD 历史一笔
        self._insert("deepseek-chat", 300, 150, 0, 1.50, "function", "CNY", today_ts + 4)
        self._insert("deepseek-chat", 400, 200, 0, 0.30, "function", "USD", old_ts)

        # 充值：必须排除
        self._insert(
            "账户充值", 0, 0, 0, 10.00, "recharge", "USD", today_ts + 5, source="人工充值"
        )

        self.cfg = {
            "active_model": "gemini-3.8-flash",
            "credits": {"total_quota": 10.0, "currency": "USD"},
            "master_credentials": [{
                "name": "Gemini 主控凭证",
                "provider": "Google",
                "model": "gemini-3.8-flash",
                "currency": "USD",
                "is_active": True,
            }, {
                "name": "USD 备用凭证",
                "provider": "OpenAI",
                "model": "gpt-4o",
                "currency": "USD",
                "balance": 2.0,
                "is_active": True,
            }],
            "function_credentials": [{
                "name": "执行 AI（DeepSeek）",
                "provider": "DeepSeek",
                "model": "deepseek-chat",
                "currency": "",
                "balance": 20.0,
                "is_active": True,
            }, {
                "name": "CNY 备用凭证",
                "provider": "DeepSeek",
                "model": "deepseek-chat",
                "currency": "CNY",
                "balance": 5.0,
                "is_active": True,
            }],
        }
        with patch.object(model_service, "get_db_connection", side_effect=self._connect), \
             patch.object(model_service, "get_store", return_value=None), \
             patch.object(model_service, "get_models_config", return_value=self.cfg), \
             patch.object(system_service, "backfill_token_costs"), \
             patch.object(system_service, "backfill_usage_meta"):
            self.result = model_service.get_token_analytics()

    def _analytics(self):
        """同一套 mock 下重算一次，供需要追加流水的用例使用。"""
        with patch.object(model_service, "get_db_connection", side_effect=self._connect), \
             patch.object(model_service, "get_store", return_value=None), \
             patch.object(model_service, "get_models_config", return_value=self.cfg), \
             patch.object(system_service, "backfill_token_costs"), \
             patch.object(system_service, "backfill_usage_meta"):
            return model_service.get_token_analytics()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert(self, model, prompt, completion, cached, cost, kind, currency, ts,
                source="universal_proxy", tools=""):
        conn = sqlite3.connect(self.db)
        conn.execute(
            """
            INSERT INTO token_usage
            (timestamp, model, prompt_tokens, completion_tokens, cached_tokens,
             cost_usd, source, session_type, tools, credential_type, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, model, prompt, completion, cached, cost, source, source, tools, kind, currency),
        )
        conn.commit()
        conn.close()

    def _group(self, kind):
        for group in self.result["accounting_groups"]:
            if group["credential_type"] == kind:
                return group
        self.fail(f"missing accounting group: {kind}")

    def test_combined_has_only_tokens_and_calls(self):
        combined = self.result["combined"]
        self.assertEqual(combined["today_calls"], 5)
        self.assertEqual(combined["today_tokens"], 1065)
        self.assertEqual(combined["overall_calls"], 7)
        self.assertEqual(combined["overall_tokens"], 1965)
        self.assertNotIn("today_cost", combined)
        self.assertNotIn("overall_cost", combined)
        self.assertNotIn("cost_formatted", combined)

    def test_master_and_function_are_separated(self):
        master = self._group("master")
        function = self._group("function")

        self.assertEqual(master["today_calls"], 4)
        self.assertEqual(master["today_tokens"], 615)
        self.assertEqual(master["overall_calls"], 5)
        self.assertEqual(master["overall_tokens"], 915)

        self.assertEqual(function["today_calls"], 1)
        self.assertEqual(function["today_tokens"], 450)
        self.assertEqual(function["overall_calls"], 2)
        self.assertEqual(function["overall_tokens"], 1050)

        self.assertEqual(
            [kind for kind in (g["credential_type"] for g in self.result["accounting_groups"])],
            ["master", "function"],
        )

    def test_amounts_are_split_by_currency_inside_each_group(self):
        master = self._group("master")
        function = self._group("function")

        self.assertEqual(master["currencies"], ["USD", "CNY"])
        self.assertEqual(master["currency_count"], 2)
        self.assertEqual(master["costs"][0]["overall_cost_formatted"], "$0.3500")
        self.assertEqual(master["costs"][0]["today_cost_formatted"], "$0.2500")
        # RMB 与 CNY 必须合并为一组
        self.assertEqual(master["costs"][1]["currency"], "CNY")
        self.assertEqual(master["costs"][1]["overall_cost_formatted"], "¥0.0700")
        self.assertEqual(master["costs"][1]["today_cost_formatted"], "¥0.0700")

        # 执行凭证是 CNY，所以执行组主币种是 CNY，第二币种才是 USD
        self.assertEqual(function["currencies"], ["CNY", "USD"])
        self.assertEqual(function["costs"][0]["overall_cost_formatted"], "¥1.5000")
        self.assertEqual(function["costs"][0]["today_cost_formatted"], "¥1.5000")
        self.assertEqual(function["costs"][1]["overall_cost_formatted"], "$0.3000")
        self.assertEqual(function["costs"][1]["today_cost_formatted"], "$0.0000")

    def test_recharge_is_excluded_and_currency_totals_are_not_summed(self):
        self.assertEqual(self.result["overall"]["calls"], 7)
        self.assertEqual(self.result["overall"]["total_tokens"], 1965)

        credits = self.result["credits"]
        self.assertEqual(credits["currency"], "USD")
        self.assertEqual(credits["today_cost_by_currency"], {"USD": 0.25, "CNY": 1.57})
        self.assertEqual(credits["overall_cost_by_currency"], {"USD": 0.65, "CNY": 1.57})
        self.assertEqual(credits["today_cost_formatted"], "$0.2500 · ¥1.5700")
        self.assertEqual(credits["overall_cost_formatted"], "$0.6500 · ¥1.5700")
        # 主余额汇总所有 USD 额度/凭证余额，只扣 USD 消耗
        self.assertEqual(credits["currency"], "USD")
        self.assertEqual(credits["primary_currency"], "USD")
        self.assertAlmostEqual(credits["balance"], 11.35, places=4)
        self.assertEqual([item["currency"] for item in credits["balances"]], ["USD", "CNY"])
        balances = {item["currency"]: item for item in credits["balances"]}
        self.assertTrue(balances["USD"]["is_primary"])
        self.assertAlmostEqual(balances["USD"]["total"], 11.35, places=4)
        self.assertAlmostEqual(balances["CNY"]["total"], 23.43, places=4)
        self.assertEqual(balances["USD"]["credential_count"], 2)
        self.assertEqual(balances["CNY"]["credential_count"], 2)
        self.assertAlmostEqual(self.result["total_cost_usd"], 0.65, places=4)
        self.assertEqual(self.result["total_cost_by_currency"], {"USD": 0.65, "CNY": 1.57})

    def test_recent_ledger_carries_credential_and_currency(self):
        recent = self.result["recent_ledger"]
        self.assertEqual(len(recent), 8)
        self.assertIsInstance(recent[0]["id"], str)
        self.assertTrue(recent[0]["id"].isdigit())
        self.assertEqual(recent[0]["credential_label"], "充值")
        self.assertEqual(recent[0]["currency"], "USD")
        self.assertTrue(recent[0]["is_recharge"])

        normal = next(item for item in recent if not item["is_recharge"])
        self.assertIn(normal["credential_type"], ("master", "function"))
        self.assertIn(normal["currency"], ("USD", "CNY"))
        self.assertTrue(normal["cost_formatted"].startswith(("$", "¥")))

    def test_recent_ledger_carries_channel_and_tools(self):
        """账本「来源」列：通道（session_type）+ 本轮调用过的工具名。"""
        self._insert(
            "gemini-3.8-flash", 10, 5, 0, 0.01, "master", "USD",
            self.today_start + 3600, source="qq_test", tools="you-search,call_raw_tool",
        )
        recent = self._analytics()["recent_ledger"]
        self.assertEqual(recent[0]["session_type"], "qq_test")
        self.assertEqual(recent[0]["tools"], "you-search,call_raw_tool")

    def test_monthly_daily_activity(self):
        activity = self.result["daily_activity"]
        today = datetime.date.today()
        self.assertEqual(activity["year"], today.year)
        self.assertEqual(activity["month"], today.month)
        self.assertEqual(
            len(activity["days"]),
            calendar.monthrange(today.year, today.month)[1],
        )
        today_item = next(day for day in activity["days"] if day["day"] == today.day)
        self.assertEqual(today_item["calls"], 5)
        self.assertEqual(today_item["tokens"], 1065)
        self.assertGreaterEqual(today_item["level"], 1)
        self.assertEqual(activity["formula"], "tokens + calls * 5000")

    def test_negative_balance_is_allowed_and_signed(self):
        cfg = {
            "credits": {"total_quota": 0.0, "currency": "USD"},
            "master_credentials": [{
                "model": "gpt-test",
                "provider": "OpenAI",
                "api_key": "test",
                "currency": "USD",
                "balance": 10.0,
            }],
            "function_credentials": [],
        }
        meta = {"master": ["USD"], "function": [], "all": ["USD"], "primary": "USD"}
        primary, balances = model_service._build_currency_balances(
            cfg, meta, {"USD": 25.0}
        )
        self.assertEqual(primary, "USD")
        self.assertAlmostEqual(balances[0]["total"], -15.0)
        self.assertEqual(balances[0]["total_formatted"], "-$15.00")

    def test_secondary_currency_shows_zero_when_credential_exists(self):
        meta = model_service._credential_currency_meta({
            "credits": {"currency": "USD"},
            "master_credentials": [
                {"provider": "Google", "model": "gemini-3.8-flash", "currency": "USD"},
                {"provider": "DeepSeek", "model": "deepseek-chat", "currency": "CNY"},
            ],
            "function_credentials": [{
                "provider": "DeepSeek", "model": "deepseek-chat", "currency": "",
            }],
        })
        self.assertEqual(meta["all"], ["USD", "CNY"])
        self.assertEqual(meta["master"], ["USD", "CNY"])
        self.assertEqual(meta["function"], ["CNY"])

        buckets = [{
            "credential_type": "master", "currency": "USD",
            "today_calls": 1, "today_tokens": 10,
            "overall_calls": 1, "overall_tokens": 10,
            "today_cost": 0.10, "overall_cost": 0.10,
            "input_tokens": 10, "output_tokens": 0, "cached_tokens": 0,
        }]
        groups = model_service._build_accounting_groups(buckets, meta)
        master = next(g for g in groups if g["credential_type"] == "master")
        self.assertEqual([cost["currency"] for cost in master["costs"]], ["USD", "CNY"])
        self.assertEqual(master["costs"][1]["overall_cost_formatted"], "¥0.0000")
        self.assertEqual(master["costs"][1]["today_cost_formatted"], "¥0.0000")
        self.assertTrue(master["has_secondary_currency"])
        self.assertEqual(master["secondary_currency"], "CNY")

    def test_default_credits_start_at_zero(self):
        missing = self.tmp / "missing_models_config.json"
        with patch.object(model_service, "MODELS_CONFIG_PATH", str(missing)), \
             patch.object(model_service, "get_preset_models", return_value=[]):
            cfg = model_service.get_models_config()
        self.assertEqual(cfg["credits"]["total_quota"], 0.0)
        self.assertEqual(cfg["credits"]["balance"], 0.0)

    def test_reset_credits_and_balances(self):
        config_path = self.tmp / "models_config.json"
        config_path.write_text(json.dumps({
            "active_model": "gemini-3.8-flash",
            "credits": {"total_quota": 50.0, "balance": 50.0, "used_quota": 0.0, "currency": "USD"},
            "master_credentials": [{
                "provider": "Google", "model": "gemini-3.8-flash",
                "balance": 10.0, "allocated_quota": 10.0,
            }],
            "function_credentials": [{
                "provider": "DeepSeek", "model": "deepseek-chat",
                "balance": 5.0, "allocated_quota": 5.0,
            }],
        }), encoding="utf-8")

        cfg = model_service.reset_credits_and_balances(config_path)
        self.assertEqual(cfg["credits"]["total_quota"], 0.0)
        self.assertEqual(cfg["credits"]["balance"], 0.0)
        self.assertEqual(cfg["credits"]["used_quota"], 0.0)
        self.assertEqual(cfg["master_credentials"][0]["balance"], 0.0)
        self.assertEqual(cfg["master_credentials"][0]["allocated_quota"], 0.0)
        self.assertEqual(cfg["function_credentials"][0]["balance"], 0.0)
        self.assertEqual(cfg["function_credentials"][0]["allocated_quota"], 0.0)
        self.assertEqual(cfg["active_model"], "gemini-3.8-flash")

    def test_third_and_fourth_currency_are_kept(self):
        """币种数量不能硬编码为 2，第三/第四币种也要各自成卡。"""
        meta = model_service._credential_currency_meta({
            "credits": {"currency": "USD"},
            "master_credentials": [
                {"provider": "Google", "model": "gemini-3.8-flash", "currency": "USD"},
                {"provider": "DeepSeek", "model": "deepseek-chat", "currency": "CNY"},
                {"provider": "OpenRouter", "model": "eur-model", "currency": "EUR"},
            ],
            "function_credentials": [{
                "provider": "DeepSeek", "model": "deepseek-chat", "currency": "CNY",
            }],
        })
        self.assertEqual(meta["master"], ["USD", "CNY", "EUR"])

        buckets = [{
            "credential_type": "master", "currency": "USD",
            "today_calls": 1, "today_tokens": 10,
            "overall_calls": 1, "overall_tokens": 10,
            "today_cost": 0.10, "overall_cost": 0.10,
            "input_tokens": 10, "output_tokens": 0, "cached_tokens": 0,
        }]
        groups = model_service._build_accounting_groups(buckets, meta)
        master = next(g for g in groups if g["credential_type"] == "master")
        self.assertEqual([cost["currency"] for cost in master["costs"]], ["USD", "CNY", "EUR"])
        self.assertEqual(master["currency_count"], 3)
        self.assertEqual(master["costs"][1]["overall_cost_formatted"], "¥0.0000")
        self.assertEqual(master["costs"][2]["overall_cost_formatted"], "EUR 0.0000")

    def test_second_currency_hidden_when_only_other_group_has_it(self):
        """主控只有 USD、执行只有 CNY 时，两组都只应显示自己的币种。"""
        meta = model_service._credential_currency_meta({
            "credits": {"currency": "USD"},
            "master_credentials": [{
                "provider": "Google", "model": "gemini-3.8-flash", "currency": "USD",
            }],
            "function_credentials": [{
                "provider": "DeepSeek", "model": "deepseek-chat", "currency": "",
            }],
        })
        self.assertEqual(meta["master"], ["USD"])
        self.assertEqual(meta["function"], ["CNY"])

        buckets = [{
            "credential_type": "master", "currency": "USD",
            "today_calls": 1, "today_tokens": 10,
            "overall_calls": 1, "overall_tokens": 10,
            "today_cost": 0.10, "overall_cost": 0.10,
            "input_tokens": 10, "output_tokens": 0, "cached_tokens": 0,
        }]
        groups = model_service._build_accounting_groups(buckets, meta)
        master = next(g for g in groups if g["credential_type"] == "master")
        function = next(g for g in groups if g["credential_type"] == "function")

        self.assertEqual([cost["currency"] for cost in master["costs"]], ["USD"])
        self.assertFalse(master["has_secondary_currency"])
        self.assertIsNone(master["secondary_currency"])

        self.assertEqual([cost["currency"] for cost in function["costs"]], ["CNY"])
        self.assertFalse(function["has_secondary_currency"])
        self.assertIsNone(function["secondary_currency"])

    def test_secondary_currency_hidden_without_second_currency_credential(self):
        meta = model_service._credential_currency_meta({
            "credits": {"currency": "USD"},
            "master_credentials": [{
                "provider": "Google", "model": "gemini-3.8-flash", "currency": "USD",
            }],
            "function_credentials": [{
                "provider": "OpenAI", "model": "gpt-4o", "currency": "USD",
            }],
        })
        self.assertEqual(meta["all"], ["USD"])

        buckets = [{
            "credential_type": "master", "currency": "USD",
            "today_calls": 1, "today_tokens": 10,
            "overall_calls": 1, "overall_tokens": 10,
            "today_cost": 0.10, "overall_cost": 0.10,
            "input_tokens": 10, "output_tokens": 0, "cached_tokens": 0,
        }]
        groups = model_service._build_accounting_groups(buckets, meta)
        master = next(g for g in groups if g["credential_type"] == "master")
        self.assertEqual([cost["currency"] for cost in master["costs"]], ["USD"])
        self.assertFalse(master["has_secondary_currency"])
        self.assertIsNone(master["secondary_currency"])


if __name__ == "__main__":
    unittest.main()
