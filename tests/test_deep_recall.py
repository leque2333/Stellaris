# -*- coding: utf-8 -*-
"""深时检索：关键词搜索、字数闸门、时间边界解析。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core.memory import deep_recall  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class ModeTest(unittest.TestCase):
    def test_full(self):
        self.assertEqual(deep_recall.choose_mode(500), "full")

    def test_narrative(self):
        self.assertEqual(deep_recall.choose_mode(1_500), "narrative")

    def test_compressed(self):
        self.assertEqual(deep_recall.choose_mode(5_000), "compressed")

    def test_boundaries(self):
        self.assertEqual(deep_recall.choose_mode(700), "full")
        self.assertEqual(deep_recall.choose_mode(2_700), "narrative")
        self.assertEqual(deep_recall.choose_mode(2_701), "compressed")


class TermsTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(deep_recall.parse_terms("前天 QQ、小鱼干"), ["前天", "QQ", "小鱼干"])

    def test_commas(self):
        self.assertEqual(deep_recall.parse_terms("a，b|c;d"), ["a", "b", "c", "d"])

    def test_cap_eight(self):
        self.assertEqual(len(deep_recall.parse_terms(" ".join(f"t{i}" for i in range(20)))), 8)

    def test_empty(self):
        self.assertEqual(deep_recall.parse_terms(""), [])


class TimeBoundTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(deep_recall.parse_time_bound(None))

    def test_unix(self):
        self.assertEqual(deep_recall.parse_time_bound(1793000000), 1793000000.0)

    def test_iso_date(self):
        value = deep_recall.parse_time_bound("2026-10-29")
        self.assertIsInstance(value, float)

    def test_bad_string(self):
        self.assertIsNone(deep_recall.parse_time_bound("前天"))


class TrimTest(unittest.TestCase):
    def _hits(self, count, length=100):
        return [
            deep_recall.Hit(f"e{index}", "2026-10-29 10:00:00", 1000 + index,
                            "字" * length, "qq")
            for index in range(count)
        ]

    def test_under_budget_keeps_all(self):
        kept, mode = deep_recall.trim_hits(self._hits(3, 50), budget=1000)
        self.assertEqual(len(kept), 3)
        self.assertEqual(mode, "full")

    def test_over_budget_compresses_and_trims(self):
        kept, mode = deep_recall.trim_hits(self._hits(200, 100), budget=2000)
        self.assertEqual(mode, "compressed")
        self.assertLessEqual(sum(len(hit.narrative) for hit in kept), 2000)

    def test_newest_first(self):
        kept, _ = deep_recall.trim_hits(self._hits(3, 50), budget=1000)
        self.assertEqual([hit.event_id for hit in kept], ["e2", "e1", "e0"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="deep_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        for index, text in enumerate([
            "乐阙说：「给我发点小鱼干」",
            "乐阙说：「今天天气不错」",
            "乐阙说：「小鱼干收到了」",
        ]):
            self.store.append_event(TimelineEvent(
                event_id=f"e{index}", timestamp=1793000000 + index, source_module="qq",
                actor_id="master", actor_name="master", narrative=text,
                person_id="master",
            ))

    def test_keyword_match(self):
        rows = self.store.search_narratives(["小鱼干"], limit=10)
        self.assertEqual(len(rows), 2)

    def test_person_filter(self):
        rows = self.store.search_narratives(["小鱼干"], person_ids=["nobody"], limit=10)
        self.assertEqual(rows, [])

    def test_time_range(self):
        rows = self.store.search_narratives(
            ["小鱼干"], start_ts=1793000002, limit=10
        )
        self.assertEqual(len(rows), 1)

    def test_all_terms_by_default(self):
        rows = self.store.search_narratives(["小鱼干", "今天"], limit=10)
        self.assertEqual(len(rows), 0)  # 没有一条同时含两个词

    def test_any_terms_when_asked(self):
        rows = self.store.search_narratives(["小鱼干", "天气"], match_all=False, limit=10)
        self.assertEqual(len(rows), 3)

    def test_empty_terms(self):
        self.assertEqual(self.store.search_narratives([], limit=10), [])


if __name__ == "__main__":
    unittest.main()
