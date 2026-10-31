# -*- coding: utf-8 -*-
"""全新部署演练：空 data 目录 → 初始化脚本 → 安全配置就绪 → AstrBot 能通。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_core import api_security  # noqa: E402


class FreshInstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fresh_install_")
        self.path = Path(self.dir) / "api_security.json"
        self._old = os.environ.get("HUB_API_SECURITY_PATH")
        os.environ["HUB_API_SECURITY_PATH"] = str(self.path)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HUB_API_SECURITY_PATH", None)
        else:
            os.environ["HUB_API_SECURITY_PATH"] = self._old
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_setup_creates_config_with_secret(self):
        import setup_security

        config = setup_security.apply(generate_secret=True, enable_signature=True)
        self.assertTrue(self.path.exists())
        self.assertTrue(config["signature"]["enabled"])
        self.assertTrue(config["signature"]["secret"])
        status = api_security.status()
        self.assertTrue(status["signature"]["secret_configured"])
        # 内网默认豁免
        self.assertTrue(api_security.signature_exempt("172.17.0.2", api_security.load_config()))

    def test_connect_astrbot_init_is_idempotent(self):
        import connect_astrbot

        first = connect_astrbot.ensure_security_config()
        self.assertIn("已生成", first[0])
        created = api_security.load_config()
        self.assertTrue(created["signature"]["secret"])
        self.assertFalse(created["signature"]["enabled"])

        second = connect_astrbot.ensure_security_config()
        self.assertIn("已存在", second[0])
        self.assertEqual(api_security.load_config()["signature"]["secret"],
                         created["signature"]["secret"])

    def test_detect_reverse_proxy(self):
        import socket as socket_mod

        import connect_astrbot

        server = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
        server.setsockopt(socket_mod.SOL_SOCKET, socket_mod.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            self.assertEqual(connect_astrbot.detect_reverse_proxy(ports=[port]), port)
        finally:
            server.close()
        self.assertIsNone(connect_astrbot.detect_reverse_proxy(ports=[1], timeout=0.05))

    def test_report_deployment_mode(self):
        import connect_astrbot

        lines = connect_astrbot.report_deployment_mode("http://127.0.0.1:18895/v1")
        self.assertTrue(lines)
        # 无论有没有反代，都必须给出一句可读的结论
        self.assertTrue(any("反代" in line or "0.0.0.0" in line or "本机" in line for line in lines))

    def test_generated_secret_can_sign(self):
        import setup_security

        setup_security.apply(generate_secret=True, enable_signature=True)
        secret = api_security.load_config()["signature"]["secret"]
        timestamp = "1000"
        body = b'{"hello": 1}'
        digest = api_security.sign("POST", "/v1/chat/completions", timestamp, body, secret)
        self.assertIsNone(
            api_security.signature_error(
                "POST", "/v1/chat/completions", timestamp, digest, body, now=1000
            )
        )
        self.assertEqual(
            api_security.signature_error(
                "POST", "/v1/chat/completions", timestamp, digest, b'{"hello": 2}', now=1000
            ),
            "bad_signature",
        )


if __name__ == "__main__":
    unittest.main()
