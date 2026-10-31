# -*- coding: utf-8 -*-
"""时间轴记忆曲线：分层压缩、边界安全、预算控制。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core.memory import timeline_curve as curve  # noqa: E402


class _Event:
    def __init__(self, event_id, timestamp, narrative):
        self.event_id = event_id
        self.timestamp = timestamp
        self.narrative = narrative


def _events(count, length=100):
    rows = []
    for index in range(count):
        prefix = f"第{index}条"
        rows.append(_Event(
            f"e{index}", timestamp=1000 + index,
            narrative=prefix + "甲" * max(1, length - len(prefix)),
        ))
    return rows


class TierTest(unittest.TestCase):
    def test_tiers_follow_char_shares(self):
        entries = curve.build_curve(_events(10, 10), budget_chars=100)
        # 10 条 × 10 字，预算 100 → 全进；前 30 字 tier0、30~70 tier1、70~100 tier2
        self.assertEqual([item.tier for item in entries], [0, 0, 0, 1, 1, 1, 1, 2, 2, 2])

    def test_over_budget_dropped(self):
        entries = curve.build_curve(_events(20, 10), budget_chars=50)
        self.assertEqual(len(entries), 5)

    def test_newest_first(self):
        entries = curve.build_curve(_events(3, 10), budget_chars=100)
        self.assertEqual([item.event_id for item in entries], ["e2", "e1", "e0"])

    def test_allow_overflow_keeps_whole_record(self):
        events = _events(3, 100)  # 每条 100 字
        entries = curve.build_curve(events, budget_chars=150, allow_overflow=True)
        # 一条 100，第二条会到 200 > 150，但还没到预算前允许收下 -> 共 2 条
        self.assertEqual(len(entries), 2)
        self.assertLessEqual(sum(item.original_chars for item in entries), 200)

    def test_no_overflow_when_disabled(self):
        entries = curve.build_curve(_events(3, 100), budget_chars=150, allow_overflow=False)
        self.assertEqual(len(entries), 1)

    def test_empty(self):
        self.assertEqual(curve.build_curve([], budget_chars=100), [])


class CompressTest(unittest.TestCase):
    def test_full_level_unchanged(self):
        text = "这是一句话。这是第二句。"
        self.assertEqual(curve.compress_narrative(text, 1.0), text)

    def test_short_text_kept(self):
        text = "短"
        self.assertEqual(curve.compress_narrative(text, 0.6), text)

    def test_cut_at_sentence_end(self):
        text = ("第一句话在这里，说了一件事。" * 8) + "最后一句话在这里。"
        result = curve.compress_narrative(text, 0.5)
        self.assertTrue(result.endswith("…"))
        self.assertLess(len(result), len(text))

    def test_quote_not_split(self):
        text = "某说：「" + ("这句话很长不能被切开，" * 10) + "。」后面还有别的。"
        result = curve.compress_narrative(text, 0.5)
        self.assertEqual(result.count("「"), result.count("」"))

    def test_no_dangling_quote(self):
        text = "他说：「" + ("一二三四五六七八九十，" * 8) + "」"
        result = curve.compress_narrative(text, 0.5)
        self.assertEqual(result.count("「"), result.count("」"))

    def test_short_text_never_grows(self):
        text = "乐阙说：「帮我搜一下今天有什么科技新闻」；安桉回答：「已经同步到记忆库了」"
        out = curve.compress_narrative(text, 0.85)
        self.assertEqual(out, text)
        self.assertLessEqual(len(out), len(text))


class TailByCharsTest(unittest.TestCase):
    def test_tail_entry_capped_by_chars(self):
        events = [_Event(f"e{index}", timestamp=100 - index, narrative=f"第{index}条" + "字" * 96) for index in range(10)]
        entries = curve.build_curve(events, budget_chars=1000)
        tail = [e for e in entries if e.tier >= 2]
        self.assertTrue(tail)
        for item in tail:
            self.assertLessEqual(item.kept_chars, curve.TAIL_ENTRY_CHARS + 1)

    def test_middle_still_ratio(self):
        events = [_Event(f"m{index}", timestamp=100 - index, narrative=f"第{index}条" + "字" * 196) for index in range(10)]
        entries = curve.build_curve(events, budget_chars=2000)
        middle = [e for e in entries if e.tier == 1]
        self.assertTrue(middle)
        self.assertLess(middle[0].kept_chars, middle[0].original_chars)


class DedupeTest(unittest.TestCase):
    def test_same_prefix_keeps_newest(self):
        events = [
            _Event("new", timestamp=200, narrative="乐阙说：「今天吃什么」；安桉回答：「随便」"),
            _Event("old", timestamp=100, narrative="乐阙说：「今天吃什么」；安桉回答：「都行」"),
        ]
        entries = curve.build_curve(events, budget_chars=1000)
        self.assertEqual([e.event_id for e in entries], ["new"])

    def test_different_prefix_kept(self):
        events = [
            _Event("a", timestamp=200, narrative="乐阙说：「今天吃什么」"),
            _Event("b", timestamp=100, narrative="安桉说：「我想吃鱼」"),
        ]
        self.assertEqual(len(curve.build_curve(events, budget_chars=1000)), 2)


class SummaryTest(unittest.TestCase):
    def test_summarize(self):
        entries = curve.build_curve(_events(10, 10), budget_chars=100)
        info = curve.summarize(entries)
        self.assertEqual(info["events"], 10)
        self.assertEqual(sum(info["per_tier"]), 10)
        self.assertLessEqual(info["kept_chars"], info["original_chars"])

    def test_render_lines(self):
        entries = curve.build_curve(_events(2, 10), budget_chars=100)
        lines = curve.render(entries).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("[e1]"))


if __name__ == "__main__":
    unittest.main()
