# -*- coding: utf-8 -*-
"""B 站独立模块：纯函数与状态测试（不访问网络）"""

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 中枢不再挂载功能模块：B 站模块已归档（archive/modules_bilibili_*）。
# 代码迁到 AstrBot 侧后再恢复这些测试。
if not (ROOT / "modules" / "bilibili").is_dir():
    raise unittest.SkipTest("B站模块已归档，中枢不再挂载功能模块")

from modules.bilibili.bili_api import (  # noqa: E402
    _mixin_key,
    _url_key,
    clean_html,
    filter_videos,
    legacy_filter,
    merge_video_detail,
    normalize_video,
    parse_duration,
    reply_view,
)
from modules.bilibili.hub_client import HubClient, _header_value  # noqa: E402
from modules.bilibili.manifest import build_manifest  # noqa: E402
from modules.bilibili.patrol import Patrol  # noqa: E402
from modules.bilibili.state import StateStore  # noqa: E402


class BiliModuleTest(unittest.TestCase):
    def test_normalize_video_and_clean_html(self):
        item = {
            "bvid": "BV1xx",
            "aid": 42,
            "title": '<em class="keyword">AI</em> 动画',
            "desc": "<p>简介</p>",
            "owner": {"mid": 7, "name": "UP"},
            "stat": {"view": 123},
            "duration": "12:34",
            "tags": ["动画"],
        }
        video = normalize_video(item)
        self.assertEqual(video["bvid"], "BV1xx")
        self.assertEqual(video["title"], "AI 动画")
        self.assertEqual(video["desc"], "简介")
        self.assertEqual(video["duration"], 754)
        self.assertEqual(video["view"], 123)
        self.assertEqual(video["url"], "https://www.bilibili.com/video/BV1xx")

    def test_parse_duration(self):
        self.assertEqual(parse_duration("01:02:03"), 3723)
        self.assertEqual(parse_duration(90), 90)
        self.assertEqual(parse_duration("bad"), 0)

    def test_filter_videos(self):
        rows = [
            normalize_video({"bvid": "BV1", "title": "AI 动画", "desc": "", "owner": {"mid": 1}, "duration": 100}),
            normalize_video({"bvid": "BV2", "title": "广告", "desc": "AI", "owner": {"mid": 2}, "duration": 100}),
            normalize_video({"bvid": "BV3", "title": "AI", "desc": "", "owner": {"mid": 3}, "duration": 10}),
        ]
        result = filter_videos(
            rows,
            seen=["BV2"],
            keywords=["AI", "动画"],
            blocked=["广告"],
            min_hits=1,
            min_duration=60,
        )
        self.assertEqual([v["bvid"] for v in result], ["BV1"])

    def test_merge_detail_keeps_tags(self):
        base = {"bvid": "BV1", "title": "t", "tags": ["动画"], "duration": 10}
        detail = {"bvid": "BV1", "aid": 1, "title": "detail", "desc": "d", "owner": {"mid": 1, "name": "u"}}
        merged = merge_video_detail(base, detail)
        self.assertEqual(merged["title"], "detail")
        self.assertEqual(merged["tags"], ["动画"])

    def test_reply_view(self):
        reply = reply_view({
            "rpid": 9,
            "member": {"mid": 5, "uname": "用户"},
            "content": {"message": "<b>你好</b>"},
            "ctime": 1,
        })
        self.assertEqual(reply["rpid"], 9)
        self.assertEqual(reply["message"], "你好")

    def test_state_quota_and_dedup(self):
        tmp = Path(tempfile.mkdtemp(prefix="bili_state_"))
        try:
            state = StateStore(str(tmp / "state.json"))
            allowed, _ = state.can_comment(
                "s", daily_limit=2, session_limit=1, min_interval_seconds=0
            )
            self.assertTrue(allowed)
            state.record_comment("s")
            allowed, _ = state.can_comment(
                "s", daily_limit=2, session_limit=1, min_interval_seconds=0
            )
            self.assertFalse(allowed)
            state.mark_seen("BV1")
            state.mark_seen("BV1")
            self.assertEqual(state.seen_bvids(), ["BV1"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_active_session(self):
        config = {"bilibili": {"active_hours": [[18, 19]]}}
        patrol = Patrol(config, bili=None, hub=None, state=None)
        key, active = patrol.active_session(datetime(2026, 1, 1, 18, 30))
        self.assertTrue(active)
        self.assertIn("18-19", key)
        _, active = patrol.active_session(datetime(2026, 1, 1, 17, 0))
        self.assertFalse(active)

    def test_hub_url_and_header_encoding(self):
        hub = HubClient("http://127.0.0.1:18895/v1", "", "m")
        self.assertEqual(hub._url(), "http://127.0.0.1:18895/v1/chat/completions")
        encoded = _header_value("B站")
        self.assertIn("%", encoded)
        self.assertNotIn("站", encoded)

    def test_hub_chat_sends_module_and_user_headers(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                captured["headers"] = dict(self.headers)
                captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                payload = json.dumps({
                    "choices": [{"message": {"content": "你好"}}]
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            hub = HubClient(
                f"http://127.0.0.1:{server.server_port}/v1",
                "hub-sk-test",
                "test-model",
                host_id="bilibili",
                host_name="B站",
                host_uid="42",
            )
            text = hub.chat(
                [{"role": "user", "content": "hi"}],
                user_id="7",
                user_name="用户",
                channel="bilibili:video:BV1",
            )
            self.assertEqual(text, "你好")
            headers = captured["headers"]
            lower = {str(k).lower(): v for k, v in headers.items()}
            self.assertEqual(lower.get("x-host-id"), "bilibili")
            self.assertEqual(lower.get("x-host-uid"), "42")
            self.assertEqual(lower.get("x-user-id"), "7")
            self.assertIn("%", lower.get("x-host-name", ""))
            self.assertIn("%", lower.get("x-user-name", ""))
            self.assertEqual(captured["body"]["model"], "test-model")
        finally:
            server.shutdown()
            server.server_close()

    def test_clean_html(self):
        self.assertEqual(clean_html("<em>你好</em> &amp; 世界"), "你好 & 世界")

    def test_legacy_three_layer_filter(self):
        ok = {"bvid": "BV1", "title": "大肥鱼 动画", "desc": "AI", "tags": ["二次元"]}
        matched = legacy_filter(ok, must_keywords=["大肥鱼"], anime_keywords=["动画", "二次元"])
        self.assertIsNotNone(matched)
        self.assertIn("大肥鱼", matched["keyword_hits"])
        self.assertIsNone(legacy_filter(
            {"bvid": "BV2", "title": "动画", "tags": []},
            must_keywords=["大肥鱼"], anime_keywords=["动画", "二次元"],
        ))
        self.assertIsNone(legacy_filter(
            {"bvid": "BV3", "title": "大肥鱼 游戏", "tags": []},
            must_keywords=["大肥鱼"], anime_keywords=["动画", "二次元"],
        ))

    def test_manifest_has_identity_without_cookies(self):
        manifest = build_manifest({
            "hub": {"host_id": "bilibili", "host_name": "B站"},
            "bilibili": {
                "sessdata": "very-secret-cookie",
                "bili_jct": "secret-jct",
                "bot_uid": 3000000002,
                "master_name": "主人2333",
            },
        })
        self.assertEqual(manifest["account"]["uid"], "3000000002")
        self.assertEqual(manifest["account"]["name"], "主人2333")
        self.assertNotIn("very-secret-cookie", json.dumps(manifest, ensure_ascii=False))

    def test_wbi_mixin_key(self):
        self.assertEqual(len(_mixin_key("abcdefghijklmnopqrstuvwxyz0123456789" * 2)), 32)
        self.assertEqual(_url_key("https://i0.hdslb.com/bfs/wbi/abc123.png"), "abc123")


if __name__ == "__main__":
    unittest.main()
