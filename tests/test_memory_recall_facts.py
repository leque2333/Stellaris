# -*- coding: utf-8 -*-
"""指令式检索：重答前让出预算 + 档案事实联动。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class RelatedFactsTest(unittest.TestCase):
    def _handler(self, facts):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)

        class _Store:
            def get_facts_for_prompt(self, person_id, limit=20):
                return facts

        class _FactStore:
            store = _Store()

            def __init__(self):
                pass

        import agent_core.memory.facts as facts_module

        self._orig = getattr(facts_module, "FactStore", None)
        facts_module.FactStore = _FactStore
        return handler

    def tearDown(self):
        import agent_core.memory.facts as facts_module

        if self._orig is not None:
            facts_module.FactStore = self._orig

    def test_picks_matching_facts(self):
        handler = self._handler(["喜欢小鱼干", "住在杭州", "养了一只猫"])
        facts = handler._related_facts({"person_id": "master"}, ["小鱼干"])
        self.assertEqual(facts, ["喜欢小鱼干"])

    def test_caps_at_three(self):
        handler = self._handler(["小鱼干1", "小鱼干2", "小鱼干3", "小鱼干4"])
        self.assertEqual(len(handler._related_facts({"person_id": "master"}, ["小鱼干"])), 3)

    def test_no_terms(self):
        handler = self._handler(["喜欢小鱼干"])
        self.assertEqual(handler._related_facts({"person_id": "master"}, []), [])

    def test_no_person(self):
        handler = self._handler(["喜欢小鱼干"])
        self.assertEqual(handler._related_facts({}, ["小鱼干"]), [])


if __name__ == "__main__":
    unittest.main()
