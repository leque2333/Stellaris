# -*- coding: utf-8 -*-
"""HTTP 层加固：CORS 只认同源、会话 Cookie 带 HttpOnly/Secure。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))


def _handler(headers):
    from server import ConsoleHandler  # noqa: PLC0415

    handler = ConsoleHandler.__new__(ConsoleHandler)
    handler.headers = headers
    return handler


class BindHostTest(unittest.TestCase):
    def test_default_bind(self):
        import os

        from server import _bind_host  # noqa: PLC0415

        old = os.environ.pop("HUB_BIND", None)
        old_path = os.environ.get("HUB_API_SECURITY_PATH")
        # 默认必须只监听本机，不能一装上就暴露在 0.0.0.0
        os.environ["HUB_API_SECURITY_PATH"] = str(ROOT / ".tmp" / "no_such_api_security.json")
        try:
            self.assertEqual(_bind_host(), "127.0.0.1")
        finally:
            os.environ.pop("HUB_API_SECURITY_PATH", None)
            if old_path is not None:
                os.environ["HUB_API_SECURITY_PATH"] = old_path
            if old is not None:
                os.environ["HUB_BIND"] = old

    def test_env_override(self):
        import os

        from server import _bind_host  # noqa: PLC0415

        old = os.environ.get("HUB_BIND")
        os.environ["HUB_BIND"] = "127.0.0.1"
        try:
            self.assertEqual(_bind_host(), "127.0.0.1")
        finally:
            if old is None:
                os.environ.pop("HUB_BIND", None)
            else:
                os.environ["HUB_BIND"] = old


class CorsTest(unittest.TestCase):
    def test_same_origin_allowed(self):
        handler = _handler({
            "Origin": "https://hub.example.test:18895",
            "Host": "hub.example.test:18895",
        })
        self.assertEqual(handler._allowed_origin(), "https://hub.example.test:18895")

    def test_cross_origin_blocked(self):
        handler = _handler({"Origin": "https://evil.example", "Host": "hub.example.test:18895"})
        self.assertEqual(handler._allowed_origin(), "")

    def test_no_origin_header(self):
        handler = _handler({"Host": "hub.example.test:18895"})
        self.assertEqual(handler._allowed_origin(), "")

    def test_origin_without_host(self):
        handler = _handler({"Origin": "https://hub.example.test:18895"})
        self.assertEqual(handler._allowed_origin(), "")


class CookieTest(unittest.TestCase):
    def test_httponly_always(self):
        handler = _handler({})
        cookie = handler._session_cookie("tok123", 3600)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)

    def test_secure_behind_tls(self):
        handler = _handler({"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", handler._session_cookie("tok123", 3600))

    def test_plain_http_no_secure(self):
        handler = _handler({"X-Forwarded-Proto": "http"})
        self.assertNotIn("Secure", handler._session_cookie("tok123", 3600))


if __name__ == "__main__":
    unittest.main()
