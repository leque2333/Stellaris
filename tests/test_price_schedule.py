# -*- coding: utf-8 -*-
"""凭证分时价格：峰谷时段命中与回退。"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from modules import model_service  # noqa: E402


def _at(hour, minute=0):
    return time.mktime((2026, 1, 1, hour, minute, 0, 0, 0, -1))


def _at_day(day, hour=12):
    return time.mktime((2026, 1, day, hour, 0, 0, 0, 0, -1))


class PriceScheduleTest(unittest.TestCase):
    def setUp(self):
        self.cred = {
            "model": "test-model",
            "input_price": 1,
            "output_price": 2,
            "cache_price": 0.5,
            "price_schedule": [
                {"start": "08:00", "end": "22:00", "input_price": 3, "output_price": 4, "cache_price": 2},
                {"start": "22:00", "end": "08:00", "input_price": 1, "output_price": 2, "cache_price": 0.5},
            ],
        }

    def test_peak_window(self):
        price = model_service.resolve_credential_price(self.cred, _at(12))
        self.assertEqual(price, {"input": 3, "output": 4, "cache": 2})

    def test_valley_window_and_overnight(self):
        self.assertEqual(model_service.resolve_credential_price(self.cred, _at(23))["input"], 1)
        self.assertEqual(model_service.resolve_credential_price(self.cred, _at(7))["input"], 1)

    def test_boundary(self):
        self.assertEqual(model_service.resolve_credential_price(self.cred, _at(8))["input"], 3)
        self.assertEqual(model_service.resolve_credential_price(self.cred, _at(22))["input"], 1)

    def test_weekday_range(self):
        cred = dict(self.cred, price_schedule=[
            {"weekdays": "1-5", "start": "08:00", "end": "22:00", "input_price": 3},
            {"weekdays": "6-7", "start": "08:00", "end": "22:00", "input_price": 9},
        ])
        self.assertEqual(model_service.resolve_credential_price(cred, _at_day(1))["input"], 3)
        self.assertEqual(model_service.resolve_credential_price(cred, _at_day(3))["input"], 9)
        self.assertEqual(model_service.resolve_credential_price(cred, _at_day(5))["input"], 3)

    def test_weekday_names(self):
        self.assertTrue(model_service._weekday_in("mon-fri", 4))
        self.assertFalse(model_service._weekday_in("mon-fri", 6))
        self.assertTrue(model_service._weekday_in("sat,sun", 6))

    def test_flat_fallback(self):
        cred = dict(self.cred, price_schedule=[])
        price = model_service.resolve_credential_price(cred, _at(12))
        self.assertEqual(price["input"], 1)

    def test_pricing_map_uses_time(self):
        cfg = {"preset_templates": [], "models": [], "master_credentials": [self.cred], "function_credentials": []}
        with patch.object(model_service, "get_models_config", return_value=cfg):
            peak = model_service.get_model_pricing_map(_at(12))["test-model"]
            valley = model_service.get_model_pricing_map(_at(23))["test-model"]
        self.assertEqual(peak["output"], 4)
        self.assertEqual(valley["output"], 2)


if __name__ == "__main__":
    unittest.main()
