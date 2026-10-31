# -*- coding: utf-8 -*-
"""身份线索扫描器测试：不判断、只罗列，且不误报配置数字。"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.identity_scan import scan  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


def build_plugins(root: Path) -> None:
    """造三种格式不同的“插件”。"""
    # 1) JSON 配置
    a = root / "plugin_json"
    a.mkdir(parents=True)
    (a / "config.json").write_text(json.dumps({
        "bot_uid": 123456789,
        "master_uid": "123456789",
        "max_tokens": 12000,          # 噪声，不该出现
        "timeout": 30,                # 噪声
        "name": "测试插件",
    }), encoding="utf-8")

    # 2) YAML 配置（非 JSON 分支）
    b = root / "plugin_yaml"
    b.mkdir(parents=True)
    (b / "settings.yaml").write_text(
        "admin_qq: 987654321\n"
        "port: 8080\n"
        "owner: 555666777\n",
        encoding="utf-8",
    )

    # 3) SQLite 数据库
    c = root / "plugin_sqlite"
    c.mkdir(parents=True)
    conn = sqlite3.connect(c / "users.db")
    conn.execute("CREATE TABLE members (sender_id TEXT, nickname TEXT)")
    conn.executemany(
        "INSERT INTO members VALUES (?, ?)",
        [("111222333", "甲"), ("444555666", "乙"), ("111222333", "甲")],
    )
    conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('retry_limit', '99')")
    conn.commit()
    conn.close()


class IdentityScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="identity_scan_"))
        build_plugins(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, store=None):
        return scan(roots=[self.tmp], store=store)

    def test_scans_three_formats(self):
        result = self._run()
        uids = {hit["uid"] for hit in result["hits"]}
        self.assertIn("123456789", uids)   # JSON
        self.assertIn("987654321", uids)   # YAML
        self.assertIn("555666777", uids)   # YAML owner
        self.assertIn("111222333", uids)   # SQLite
        self.assertIn("444555666", uids)   # SQLite

    def test_noise_numbers_ignored(self):
        result = self._run()
        uids = {hit["uid"] for hit in result["hits"]}
        self.assertNotIn("12000", uids)    # max_tokens
        self.assertNotIn("30", uids)       # timeout
        self.assertNotIn("8080", uids)     # port
        self.assertNotIn("99", uids)       # retry_limit

    def test_same_uid_merged_with_multiple_sources(self):
        result = self._run()
        hit = next(h for h in result["hits"] if h["uid"] == "123456789")
        # bot_uid 与 master_uid 两个字段
        self.assertGreaterEqual(hit["source_count"], 2)
        self.assertEqual(len({s["field"] for s in hit["sources"]}), 2)

    def test_source_has_plugin_and_field(self):
        result = self._run()
        hit = next(h for h in result["hits"] if h["uid"] == "111222333")
        src = hit["sources"][0]
        self.assertEqual(src["kind"], "sqlite")
        self.assertIn("members.sender_id", src["field"])
        self.assertEqual(src["plugin"], "plugin_sqlite")

    def test_bilibili_prefix_normalized(self):
        tmp = Path(tempfile.mkdtemp(prefix="identity_prefix_"))
        try:
            (tmp / "p.json").write_text(json.dumps({
                "umo": "onebot_napcat:FriendMessage:1000000001",
            }), encoding="utf-8")
            result = scan(roots=[tmp])
            uids = {h["uid"] for h in result["hits"]}
            self.assertIn("1000000001", uids)
            self.assertNotIn("onebot_napcat:FriendMessage:1000000001", uids)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_configured_flag_from_store(self):
        db = self.tmp / "memory.db"
        store = SQLiteStore(db)
        store.ensure_archive("arc_master", role="admin", display_name="主人")
        store.link_identity("arc_master", "qq", "1000000001", display_name="主人")

        tmp = Path(tempfile.mkdtemp(prefix="identity_cfg_"))
        try:
            (tmp / "cfg.json").write_text(json.dumps({
                "admins_id": ["1000000001"],
            }), encoding="utf-8")
            result = scan(roots=[tmp], store=store)
            hit = next(h for h in result["hits"] if h["uid"] == "1000000001")
            self.assertTrue(hit["configured"])
            self.assertEqual(hit["archive_id"], "arc_master")
            self.assertEqual(hit["configured_via"], "identity")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_dir_returns_empty(self):
        empty = Path(tempfile.mkdtemp(prefix="identity_empty_"))
        try:
            result = scan(roots=[empty])
            self.assertTrue(result["ok"])
            self.assertEqual(result["total_uids"], 0)
        finally:
            import shutil
            shutil.rmtree(empty, ignore_errors=True)

    def test_does_not_write(self):
        """扫描前后文件数量不变（只读）。"""
        before = sorted(p.name for p in self.tmp.rglob("*"))
        self._run()
        after = sorted(p.name for p in self.tmp.rglob("*"))
        self.assertEqual(before, after)


class IdentityHiddenTest(unittest.TestCase):
    """隐藏 / 插件重扫：只影响展示，扫描能找回来。"""

    def setUp(self):

        from agent_core import identity_scan as mod

        self.mod = mod
        self.tmp = Path(tempfile.mkdtemp(prefix="identity_hidden_"))
        build_plugins(self.tmp)
        # 把隐藏文件重定向到临时目录
        self._orig_file = mod.HIDDEN_FILE
        mod.HIDDEN_FILE = self.tmp / "identity_hidden.json"

    def tearDown(self):
        import shutil
        self.mod.HIDDEN_FILE = self._orig_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hide_then_scan_marks_hidden(self):
        self.mod.set_hidden(["123456789"], hidden=True)
        result = self.mod.scan(roots=[self.tmp])
        hit = next(h for h in result["hits"] if h["uid"] == "123456789")
        self.assertTrue(hit["hidden"])
        self.assertEqual(result["hidden"], 1)

    def test_unhide(self):
        self.mod.set_hidden(["123456789"], hidden=True)
        self.mod.set_hidden(["123456789"], hidden=False)
        result = self.mod.scan(roots=[self.tmp])
        hit = next(h for h in result["hits"] if h["uid"] == "123456789")
        self.assertFalse(hit["hidden"])

    def test_plugin_rescan_restores_hidden(self):
        self.mod.set_hidden(["123456789"], hidden=True)
        # 123456789 来自 plugin_json
        result = self.mod.scan(
            roots=[self.tmp], plugin="plugin_json", reset_hidden_for_plugin=True,
        )
        hit = next(h for h in result["hits"] if h["uid"] == "123456789")
        self.assertFalse(hit["hidden"])

    def test_plugin_filter_returns_only_that_group(self):
        result = self.mod.scan(roots=[self.tmp], plugin="plugin_sqlite")
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["plugin"], "plugin_sqlite")

    def test_hidden_file_persists(self):
        self.mod.set_hidden(["111222333", "444555666"], hidden=True)
        self.assertTrue(self.mod.HIDDEN_FILE.exists())
        loaded = self.mod.load_hidden()
        self.assertEqual(loaded, {"111222333", "444555666"})

    def test_reset_hidden(self):
        self.mod.set_hidden(["123456789"], hidden=True)
        self.mod.reset_hidden()
        self.assertEqual(self.mod.load_hidden(), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
