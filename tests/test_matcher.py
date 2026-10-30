# -*- coding: utf-8 -*-
"""双填匹配：名称 + UID 交叉验证"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.connectors.matcher import match_contexts, normalize_terms  # noqa: E402

CONTEXTS = [
    {
        "platform": "qq",
        "external_id": "onebot_napcat:GroupMessage:2000000001",
        "display_name": "乌合之众喵",
        "external_type": "group",
        "status": "active",
    },
    {
        "platform": "qq",
        "external_id": "onebot_napcat:FriendMessage:1000000001",
        "display_name": "主人",
        "external_type": "friend",
        "status": "active",
    },
    {
        "platform": "bilibili",
        "external_id": "bilibili:uid:12345",
        "display_name": "B站账号 12345",
        "external_type": "account",
        "status": "active",
    },
]


class MatcherTest(unittest.TestCase):
    def test_split_commas(self):
        self.assertEqual(
            normalize_terms("乌合之众喵, 主人，B站"),
            ["乌合之众喵", "主人", "B站"],
        )

    def test_named_uid_pairs(self):
        matches = match_contexts(
            CONTEXTS,
            "乌合之众喵, 主人",
            "2000000001, 1000000001",
        )
        by_id = {m["external_id"]: m for m in matches}
        group = by_id["onebot_napcat:GroupMessage:2000000001"]
        admin = by_id["onebot_napcat:FriendMessage:1000000001"]
        self.assertEqual(group["match_type"], "paired")
        self.assertEqual(group["confidence"], 0.95)
        self.assertEqual(admin["match_type"], "paired")

    def test_name_only(self):
        matches = match_contexts(CONTEXTS, "乌合之众喵", "")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_type"], "name")

    def test_uid_only(self):
        matches = match_contexts(CONTEXTS, "", "2000000001")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_type"], "uid")
        self.assertEqual(matches[0]["external_id"], "onebot_napcat:GroupMessage:2000000001")

    def test_archive_alias_matches_name(self):
        contexts = [{
            "platform": "qq",
            "external_id": "onebot_napcat:FriendMessage:1000000001",
            "display_name": "1000000001",
            "aliases": ["主人"],
            "external_type": "friend",
            "status": "active",
        }]
        matches = match_contexts(contexts, "主人", "1000000001")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_type"], "paired")
        self.assertEqual(matches[0]["name"], "主人")

    def test_platform_filter(self):
        matches = match_contexts(CONTEXTS, "", "12345", platform="qq")
        self.assertEqual(matches, [])

    def test_no_match(self):
        self.assertEqual(match_contexts(CONTEXTS, "不存在", "999999"), [])


if __name__ == "__main__":
    unittest.main()
