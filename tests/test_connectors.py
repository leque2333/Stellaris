# -*- coding: utf-8 -*-
"""平台自动发现：连接器只读扫描 + 上下文台账 + 人物档案绑定"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.connectors import DiscoveryService, SpecConnector  # noqa: E402
from agent_core.identity import IdentityResolver  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


def build_astrbot_data(root: Path, group_id="2000000001", admin="1000000001",
                       include_group=True):
    data = root / "astrbot" / "data"
    data.mkdir(parents=True)

    whitelist = [group_id, admin] if include_group else [admin]
    (data / "cmd_config.json").write_text(json.dumps({
        "admins_id": [admin],
        "platform_settings": {"id_whitelist": whitelist},
    }), encoding="utf-8")

    conn = sqlite3.connect(data / "data_v4.db")
    conn.execute("CREATE TABLE umo_aliases (umo TEXT, auto_name TEXT, user_alias TEXT)")
    conn.execute("CREATE TABLE conversations (platform_id TEXT, user_id TEXT)")
    aliases = [
        (f"onebot_napcat:FriendMessage:{admin}", admin, None),
        ("onebot_napcat:FriendMessage:1000000003", "1000000003", None),
    ]
    conversations = [("onebot_napcat", f"onebot_napcat:FriendMessage:{admin}")]
    if include_group:
        aliases.insert(0, (f"onebot_napcat:GroupMessage:{group_id}", "乌合之众喵", None))
        conversations.insert(0, ("onebot_napcat", f"onebot_napcat:GroupMessage:{group_id}"))
    conn.executemany("INSERT INTO umo_aliases VALUES (?, ?, ?)", aliases)
    conn.executemany("INSERT INTO conversations VALUES (?, ?)", conversations)
    conn.commit()
    conn.close()

    # 生成一份指向本次临时目录的 spec，避免依赖全局 data/plugin_specs.json
    _write_specs(data)
    return data


FIXTURE_SPECS = Path(__file__).resolve().parent / "fixtures" / "plugin_specs.json"


def _write_specs(data: Path) -> Path:
    import json
    tpl = json.loads(FIXTURE_SPECS.read_text(encoding="utf-8"))
    for spec in tpl.get("specs") or []:
        spec["base_dir_candidates"] = [str(data)]
    out = data / "plugin_specs.json"
    out.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


class ConnectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_conn_"))
        self.data = build_astrbot_data(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_astrbot_discovers_group_and_admin(self):
        report = SpecConnector(self.data, specs_path=self.data / "plugin_specs.json").discover()
        self.assertTrue(report.ok)
        by_id = {c.external_id: c for c in report.contexts}
        group = by_id["onebot_napcat:GroupMessage:2000000001"]
        self.assertEqual(group.external_type, "group")
        self.assertEqual(group.display_name, "乌合之众喵")
        self.assertIn("whitelist", group.evidence)
        self.assertIn("conversation", group.evidence)
        admin = by_id[f"onebot_napcat:FriendMessage:{IdentityResolver.get_master_qq()}"]
        self.assertEqual(admin.external_type, "friend")
        self.assertIn("admin", admin.evidence)

    def test_ignores_removed_bilibili_plugin_data(self):
        # 测试端已移除 B站插件；即使旧文件还在，连接器也不应再产出 B站上下文
        pdir = self.data / "plugin_data" / "astrbot_plugin_bilibili"
        pdir.mkdir(parents=True)
        (pdir / "astrbot_plugin_bilibili.json").write_text(json.dumps({
            "bili_sub_list": {"onebot_napcat:GroupMessage:2000000001": [{"uid": 1}]},
            "credential": {"dedeuserid": "12345"},
        }), encoding="utf-8")
        report = SpecConnector(self.data, specs_path=self.data / "plugin_specs.json").discover()
        self.assertTrue(report.ok)
        by_id = {c.external_id: c for c in report.contexts}
        self.assertNotIn("bilibili:uid:12345", by_id)
        self.assertNotIn("bilibili:session", by_id)

    def test_missing_data_reports_error(self):
        report = SpecConnector(self.tmp / "missing", specs_path=self.data / "plugin_specs.json").discover()
        self.assertFalse(report.ok)
        self.assertTrue(report.error)


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_disc_"))
        self.data = build_astrbot_data(self.tmp)
        self.store = SQLiteStore(self.tmp / "memory.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def service(self, data):
        return DiscoveryService(self.store, [SpecConnector(data, specs_path=data / "plugin_specs.json")])

    def test_scan_upserts_contexts_and_binds_admin(self):
        result = self.service(self.data).scan()
        self.assertTrue(result["ok"])
        contexts = {c["external_id"]: c for c in self.store.list_contexts(limit=100)}
        group = contexts["onebot_napcat:GroupMessage:2000000001"]
        self.assertEqual(group["display_name"], "乌合之众喵")
        self.assertIn("whitelist", json.loads(group["evidence"]))

        admin_id = IdentityResolver.get_master_qq()
        self.assertEqual(
            self.store.resolve_binding("qq", f"onebot_napcat:FriendMessage:{admin_id}"),
            admin_id,
        )

    def test_manual_binding_and_stale_marking(self):
        self.service(self.data).scan()
        self.store.bind_context(
            "qq", "onebot_napcat:GroupMessage:2000000001", "arc_person_test",
            confidence=0.95, verified_by="name_id_pair",
        )
        self.assertEqual(
            self.store.resolve_binding("qq", "onebot_napcat:GroupMessage:2000000001"),
            "arc_person_test",
        )
        binding = [
            b for b in self.store.list_bindings()
            if b["external_id"] == "onebot_napcat:GroupMessage:2000000001"
        ][0]
        self.assertEqual(binding["verified_by"], "name_id_pair")
        self.assertEqual(binding["confidence"], 0.95)

        # 新目录：只剩管理员，连接器成功 → 旧群应标记失效
        empty_data = build_astrbot_data(self.tmp / "empty", include_group=False)
        self.service(empty_data).scan()

        group = [
            c for c in self.store.list_contexts(limit=100)
            if c["external_id"] == "onebot_napcat:GroupMessage:2000000001"
        ][0]
        self.assertEqual(group["status"], "stale")


if __name__ == "__main__":
    unittest.main()
