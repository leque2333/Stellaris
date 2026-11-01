# -*- coding: utf-8 -*-
"""记忆调参：默认值、持久化、环境变量优先级、范围校验与面板接线。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core.memory import (  # noqa: E402
    settings,
    timeline_curve,
)
from agent_core.memory.episodic_stream import _recall_window  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class SettingsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="memory_settings_"))
        self.path = self.tmp / "memory_settings.json"
        self._old_path = os.environ.get("HUB_MEMORY_SETTINGS_PATH")
        self._old_env = {item["env"]: os.environ.get(item["env"]) for item in settings.PARAMS}
        os.environ["HUB_MEMORY_SETTINGS_PATH"] = str(self.path)
        for name in self._old_env:
            os.environ.pop(name, None)
        settings._invalidate_cache()

    def tearDown(self):
        if self._old_path is None:
            os.environ.pop("HUB_MEMORY_SETTINGS_PATH", None)
        else:
            os.environ["HUB_MEMORY_SETTINGS_PATH"] = self._old_path
        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        settings._invalidate_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)


class DefaultsTest(SettingsTestBase):
    def test_defaults_are_stable(self):
        values = settings.effective()
        self.assertEqual(values["timeline_budget_chars"], 3_000)
        self.assertEqual(values["candidate_chars"], 300_000)
        self.assertTrue(values["memory_tool_enabled"])
        self.assertFalse(values["prefetch_enabled"])

    def test_sources_start_as_default(self):
        self.assertEqual(set(settings.sources().values()), {"default"})


class PersistTest(SettingsTestBase):
    def test_save_persists_and_reloads(self):
        settings.save({"timeline_budget_chars": 5_000, "lookback_days": 7})
        settings._invalidate_cache()
        values = settings.effective()
        self.assertEqual(values["timeline_budget_chars"], 5_000)
        self.assertEqual(values["lookback_days"], 7.0)
        self.assertTrue(self.path.exists())

    def test_reset_returns_to_defaults(self):
        settings.save({"timeline_keep_chars": 9_000})
        settings.reset()
        self.assertEqual(settings.effective()["timeline_keep_chars"], 4_000)
        self.assertFalse(self.path.exists())

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            settings.save({"not_a_param": 1})

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            settings.save({"timeline_budget_chars": 10})
        with self.assertRaises(ValueError):
            settings.save({"timeline_budget_chars": "abc"})

    def test_bool_values_normalized(self):
        settings.save({"memory_tool_enabled": "0", "prefetch_enabled": "true"})
        values = settings.effective()
        self.assertFalse(values["memory_tool_enabled"])
        self.assertTrue(values["prefetch_enabled"])


class EnvPriorityTest(SettingsTestBase):
    def test_env_wins_over_saved(self):
        settings.save({"timeline_budget_chars": 5_000})
        os.environ["HUB_TIMELINE_BUDGET"] = "7777"
        self.assertEqual(settings.effective()["timeline_budget_chars"], 7_777)
        self.assertEqual(settings.sources()["timeline_budget_chars"], "env")

    def test_env_locked_field_cannot_be_saved(self):
        os.environ["HUB_TIMELINE_BUDGET"] = "7777"
        with self.assertRaises(ValueError):
            settings.save({"timeline_budget_chars": 5_000})

    def test_env_value_above_ui_slider_is_preserved(self):
        # 面板滑杆只到 50k，但环境变量原本允许更大；不能把部署值截断。
        os.environ["HUB_TIMELINE_BUDGET"] = "80000"
        self.assertEqual(settings.effective()["timeline_budget_chars"], 80_000)
        item = next(p for p in settings.public_payload()["params"]
                    if p["key"] == "timeline_budget_chars")
        self.assertEqual(item["ui_max"], 50_000)

    def test_panel_payload_marks_locked(self):
        os.environ["HUB_TIMELINE_BUDGET"] = "7777"
        payload = settings.public_payload()
        item = next(p for p in payload["params"] if p["key"] == "timeline_budget_chars")
        self.assertTrue(item["locked"])
        self.assertTrue(item["env_set"])


class IntegrationTest(SettingsTestBase):
    def test_curve_budget_uses_saved_value(self):
        settings.save({"timeline_budget_chars": 4_321})
        self.assertEqual(timeline_curve.budget_chars(), 4_321)

    def test_recall_window_uses_saved_value(self):
        settings.save({
            "candidate_chars": 123_000,
            "lookback_days": 3,
            "candidate_limit": 456,
        })
        chars, days, limit, _ = _recall_window()
        self.assertEqual((chars, days, limit), (123_000, 3.0, 456))


class StatsTest(SettingsTestBase):
    def setUp(self):
        super().setUp()
        self.store = SQLiteStore(self.tmp / "memory.db")
        for index in range(3):
            self.store.append_event(TimelineEvent(
                event_id=f"e{index}", timestamp=1_800_000_000 + index * 3600,
                source_module="qq", actor_id="master", actor_name="主人",
                narrative="字" * 100, person_id="master",
            ))
        self.store.save_detail("message", "hello")

    def test_counts_and_spans(self):
        stats = settings.memory_stats(self.store)
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["active_events"], 3)
        self.assertEqual(stats["active_chars"], 300)
        self.assertEqual(stats["details"], 1)
        self.assertGreater(stats["coverage_days"], 0)


class HandlerWiringTest(SettingsTestBase):
    def setUp(self):
        super().setUp()
        import server

        self.server = server
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.sent = []
        self._old_record = server.audit_log.record
        server.audit_log.record = lambda *args, **kwargs: None

    def tearDown(self):
        self.server.audit_log.record = self._old_record
        super().tearDown()

    def _handler(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler._memory_store = lambda: self.store
        handler._send_json = lambda data, status=200, extra_headers=None: self.sent.append((status, data))
        return handler

    def test_get_returns_effective_and_stats(self):
        handler = self._handler()
        handler._get_memory_settings()
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["timeline_budget_chars"], 3_000)
        self.assertIn("stats", data)

    def test_post_saves_settings(self):
        handler = self._handler()
        handler._post_memory_settings({"settings": {"timeline_budget_chars": 6_000}})
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["timeline_budget_chars"], 6_000)
        self.assertEqual(settings.effective()["timeline_budget_chars"], 6_000)

    def test_post_rejects_bad_value(self):
        handler = self._handler()
        handler._post_memory_settings({"settings": {"timeline_budget_chars": -1}})
        status, data = self.sent[-1]
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_post_reset(self):
        settings.save({"timeline_budget_chars": 6_000})
        handler = self._handler()
        handler._post_memory_settings({"reset": True})
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["timeline_budget_chars"], 3_000)


if __name__ == "__main__":
    unittest.main()
