# -*- coding: utf-8 -*-
"""中枢备用宿主机控制：schema 与执行边界。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core import host_control  # noqa: E402


class _Proc:
    returncode = 0
    stdout = "ok"
    stderr = ""


class HostControlTest(unittest.TestCase):
    def test_spec_is_admin_host_tool(self):
        spec = host_control.tool_spec()
        self.assertEqual(spec["function"]["name"], "hub_host_control")
        actions = spec["function"]["parameters"]["properties"]["action"]["enum"]
        self.assertIn("screenshot", actions)
        # 宿主机命令执行默认关闭：只有显式打开才出现在 schema 里
        self.assertNotIn("powershell", actions)

    def test_powershell_requires_explicit_enable(self):
        with patch.dict("os.environ", {host_control.POWERSHELL_ENV: "1"}, clear=False):
            actions = host_control.tool_spec()["function"]["parameters"]["properties"]["action"]["enum"]
            self.assertIn("powershell", actions)

    def test_powershell_disabled_by_default(self):
        result = host_control.execute("powershell", "Get-Date")
        self.assertFalse(result["ok"])
        self.assertIn("默认关闭", result["text"])

    def test_powershell_whitelist_blocks_chaining(self):
        with patch.dict("os.environ", {host_control.POWERSHELL_ENV: "1"}, clear=False):
            for command in ("Get-Date; Remove-Item C:\\x", "Invoke-Expression 'rm'", "Get-Date > out.txt"):
                result = host_control.execute("powershell", command)
                self.assertFalse(result["ok"], command)
            with patch.object(host_control.subprocess, "run", return_value=_Proc()):
                good = host_control.execute("powershell", "Get-Date")
        self.assertTrue(good["ok"])

    def test_unknown_action_is_rejected(self):
        result = host_control.execute("rm_rf")
        self.assertFalse(result["ok"])
        self.assertIn("未知", result["text"])

    def test_empty_powershell_is_rejected(self):
        result = host_control.execute("powershell", "")
        self.assertFalse(result["ok"])

    def test_backup_is_admin_only(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        admin = type("P", (), {"tool_permissions": {"role": "admin"}})()
        guest = type("P", (), {"tool_permissions": {"role": "guest"}})()
        old_gate = server.tool_gate
        server.tool_gate = None
        handler._admin_identity = lambda session: True
        try:
            self.assertTrue(handler._host_backup_enabled({"client_id": "astrbot", "profile": admin}))
            self.assertFalse(handler._host_backup_enabled({"client_id": "astrbot", "profile": guest}))
            self.assertFalse(handler._host_backup_enabled({"client_id": "nextchat", "profile": admin}))
        finally:
            server.tool_gate = old_gate

    def test_web_master_does_not_use_qq_uid(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        web = handler._timeline_person_uid({
            "platform": "web", "sender_uid": "master", "actor_id": "master",
            "archive_id": "arc_master",
        })
        qq = handler._timeline_person_uid({
            "platform": "qq", "sender_uid": "master", "actor_id": "master",
            "archive_id": "arc_master",
        })
        web_with_uid = handler._timeline_person_uid({
            "platform": "web", "sender_uid": "2000000001", "actor_id": "master",
            "archive_id": "arc_master",
        })
        self.assertEqual(web, "master")
        self.assertEqual(web_with_uid, "master")
        self.assertTrue(qq.isdigit())
        self.assertNotEqual(web, qq)

    def test_identifier_prefix_extracts_uid(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler.headers = {}
        self.assertEqual(
            handler._extract_raw_user_id({}, "用户ID: 2000000001 你好"),
            "2000000001",
        )
        self.assertEqual(
            handler._extract_raw_user_id(
                {}, "# CRON JOB CONTEXT\n<system_reminder>User ID: 2000000001, Nickname: Scheduler"
            ),
            "2000000001",
        )

    def test_admin_identity_requires_mapped_admin_archive(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        store = type("S", (), {
            "get_archive": lambda self, aid: {"role": "admin" if aid == "arc_master" else "guest"},
        })()
        with patch("agent_core.storage.get_store", return_value=store):
            self.assertTrue(handler._admin_identity({
                "sender_uid": "2000000001", "archive_id": "arc_master", "bound_by": "binding",
            }))
            self.assertFalse(handler._admin_identity({
                "sender_uid": "", "archive_id": "arc_master", "bound_by": "master",
            }))
            self.assertFalse(handler._admin_identity({
                "sender_uid": "123", "archive_id": "arc_guest", "bound_by": "binding",
            }))

    def test_powershell_runs_without_shell(self):
        with patch.dict("os.environ", {host_control.POWERSHELL_ENV: "1"}, clear=False):
            with patch.object(host_control.subprocess, "run", return_value=_Proc()) as run:
                result = host_control.execute("powershell", "Get-Date", 10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "ok")
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertIn("Get-Date", run.call_args.args[0])

    def test_hub_intercepts_and_reasks(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._host_control_rounds = lambda: 1
        handler._note_host_control = lambda *args, **kwargs: None
        handler._persona_request = lambda ctx, messages, extra: (
            {"content": "完成", "tool_calls": []}, "stop", {}, "",
        )
        ctx = {"host_backup": True, "extra": {"tools": [host_control.tool_spec()]}}
        message = {
            "content": "",
            "tool_calls": [{
                "id": "h1",
                "function": {"name": host_control.TOOL_NAME, "arguments": '{"action":"powershell","command":"Write-Output hi"}'},
            }],
        }
        with patch.object(host_control, "execute", return_value={"ok": True, "text": "hi"}) as run:
            again, _, _ = handler._maybe_host_control(ctx, [], message, "tool_calls", {})
        self.assertEqual(again["content"], "完成")
        self.assertTrue(run.called)
        self.assertEqual(ctx["host_control_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
