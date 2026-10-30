# -*- coding: utf-8 -*-
"""好感度自动评估（方案 A）：解析、节流、开关、clamp。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.profile import affection_ai  # noqa: E402


class AffectionAiTest(unittest.TestCase):
    def setUp(self):
        affection_ai._last_eval = 0.0
        self._env = patch.dict("os.environ", {"HUB_AFFECTION_AI": "1"}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_disabled_returns_none(self):
        with patch.dict("os.environ", {"HUB_AFFECTION_AI": "0"}):
            self.assertIsNone(affection_ai.estimate_initial_affection("你好"))
            self.assertIsNone(affection_ai.estimate_affection_delta("你好", "你好", 50))

    def test_initial_parses_number(self):
        with patch.object(affection_ai, "_chat", return_value="42"):
            self.assertEqual(affection_ai.estimate_initial_affection("你好"), 42)

    def test_initial_clamps(self):
        with patch.object(affection_ai, "_chat", return_value="999"):
            self.assertEqual(affection_ai.estimate_initial_affection("你好"), 100)
        with patch.object(affection_ai, "_chat", return_value="-20"):
            self.assertEqual(affection_ai.estimate_initial_affection("你好"), 0)

    def test_delta_parses_json(self):
        with patch.object(affection_ai, "_chat", return_value='{"delta": 3, "reason": "夸我"}'):
            result = affection_ai.estimate_affection_delta("你真棒", "谢谢", 50)
        self.assertEqual(result, (3, "夸我"))

    def test_delta_zero_returns_none(self):
        with patch.object(affection_ai, "_chat", return_value='{"delta": 0, "reason": ""}'):
            self.assertIsNone(affection_ai.estimate_affection_delta("在吗", "在", 50))

    def test_delta_clamped(self):
        with patch.object(affection_ai, "_chat", return_value='{"delta": 99, "reason": "太好了"}'):
            result = affection_ai.estimate_affection_delta("太好了", "嗯", 50)
        self.assertEqual(result[0], 10)

    def test_delta_bad_json_returns_none(self):
        with patch.object(affection_ai, "_chat", return_value="我觉得加 5 分"):
            self.assertIsNone(affection_ai.estimate_affection_delta("你好", "你好", 50))

    def test_delta_throttled(self):
        affection_ai._last_eval = 1e18  # 刚刚评估过
        with patch.object(affection_ai, "_chat", return_value='{"delta": 5, "reason": "好"}'):
            self.assertIsNone(affection_ai.estimate_affection_delta("你好", "你好", 50))

    def test_empty_input_returns_none(self):
        with patch.object(affection_ai, "_chat", return_value="50"):
            self.assertIsNone(affection_ai.estimate_initial_affection(""))
            self.assertIsNone(affection_ai.estimate_affection_delta("", "x", 50))

    def test_model_error_returns_none(self):
        with patch.object(affection_ai, "_chat", side_effect=RuntimeError("boom")):
            self.assertIsNone(affection_ai.estimate_initial_affection("你好"))
            self.assertIsNone(affection_ai.estimate_affection_delta("你好", "你好", 50))


if __name__ == "__main__":
    unittest.main(verbosity=2)
