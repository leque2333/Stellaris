# -*- coding: utf-8 -*-
"""换页纯函数：腾出、回填、归档边界。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

from agent_core.memory import timeline_swap  # noqa: E402


def events(count, size=100):
    """新 → 旧排列，每条 size 字。"""
    return [{"event_id": f"e{index}", "narrative": "字" * size} for index in range(count)]


class ReleaseTest(unittest.TestCase):
    def test_release_from_oldest(self):
        kept, released = timeline_swap.release(events(10), need_chars=250)
        self.assertEqual([e["event_id"] for e in released], ["e7", "e8", "e9"])
        self.assertEqual([e["event_id"] for e in kept], [f"e{i}" for i in range(7)])

    def test_keeps_determined_part(self):
        # 10 条共 1000 字，最多腾出 70% = 700 字
        kept, released = timeline_swap.release(events(10), need_chars=9999)
        self.assertEqual(len(kept), 3)
        self.assertEqual(len(released), 7)

    def test_nothing_to_do(self):
        kept, released = timeline_swap.release(events(5), need_chars=0)
        self.assertEqual(len(released), 0)
        self.assertEqual(len(kept), 5)

    def test_empty(self):
        self.assertEqual(timeline_swap.release([], 100), ([], []))


class RefillTest(unittest.TestCase):
    def test_fifo_within_budget(self):
        taken, rest = timeline_swap.refill(events(5), free_chars=250)
        self.assertEqual([e["event_id"] for e in taken], ["e0", "e1"])
        self.assertEqual(len(rest), 3)

    def test_ratio_limits(self):
        taken, _ = timeline_swap.refill(events(5), free_chars=500, ratio=0.5)
        self.assertEqual(len(taken), 2)  # 250 字 / 每条 100

    def test_no_budget(self):
        taken, rest = timeline_swap.refill(events(3), free_chars=0)
        self.assertEqual(taken, [])
        self.assertEqual(len(rest), 3)


class ArchiveTest(unittest.TestCase):
    def test_keep_within_capacity(self):
        kept, archive = timeline_swap.archive_candidates(events(5), keep_chars=250)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(archive), 3)

    def test_zero_capacity_archives_all(self):
        kept, archive = timeline_swap.archive_candidates(events(3), keep_chars=0)
        self.assertEqual(kept, [])
        self.assertEqual(len(archive), 3)

    def test_roundtrip_consistency(self):
        original = events(10)
        kept, released = timeline_swap.release(original, need_chars=300)
        taken, _ = timeline_swap.refill(released, free_chars=300)
        self.assertEqual(len(kept) + len(taken), 10)


if __name__ == "__main__":
    unittest.main()
