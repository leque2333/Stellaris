# -*- coding: utf-8 -*-
"""B站模块的可调用 actions：供中枢把模块自动转成工具。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .bili_api import BiliError, filter_videos, normalize_video
from .manifest import build_manifest
from .patrol import Patrol


class _Handler(BaseHTTPRequestHandler):
    server_version = "BiliActions/0.1"

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = getattr(self.server, "action_token", "")
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            auth = auth[7:].strip()
        return auth == token or self.headers.get("X-Action-Token", "") == token

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def log_message(self, *args) -> None:  # noqa: D401
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/health":
            self._json(200, {"ok": True, "module": "bilibili"})
            return
        if not self._authorized():
            self._json(401, {"ok": False, "error": "action token 无效"})
            return
        if self.path.split("?")[0] == "/actions":
            self._json(200, {"ok": True, "manifest": build_manifest(self.server.config)})
            return
        if self.path.split("?")[0] == "/actions/status":
            self._json(200, self._status())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(401, {"ok": False, "error": "action token 无效"})
            return
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/actions/recommend":
            self._json(200, self._recommend(body))
            return
        if path == "/actions/patrol":
            self._json(200, self._patrol(body))
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _status(self) -> Dict[str, Any]:
        patrol: Patrol = self.server.patrol
        print("[bili-action] status", flush=True)
        try:
            nav = patrol.bili.nav()
        except BiliError as exc:
            nav = {"is_login": False, "mid": 0, "uname": "", "error": str(exc)}
        session_key, active = patrol.active_session()
        allowed, reason = patrol.quota(session_key or "manual")
        return {
            "ok": True,
            "login": nav,
            "active": active,
            "session": session_key,
            "quota_ok": allowed,
            "quota_reason": reason,
            "tracked": len(patrol.state.tracked_comments()),
            "seen": len(patrol.state.seen_bvids()),
        }

    def _recommend(self, body: Dict[str, Any]) -> Dict[str, Any]:
        patrol: Patrol = self.server.patrol
        query = str(body.get("query") or "").strip()
        print(f"[bili-action] recommend query={query or '-'} body={body}", flush=True)
        try:
            limit = max(1, min(5, int(body.get("limit") or 3)))
        except (TypeError, ValueError):
            limit = 3
        if not query:
            keywords = patrol.bcfg.get("keywords") or []
            query = str(keywords[0]) if keywords else "动画"
        rows = []
        try:
            rows = [
                normalize_video(item)
                for item in patrol.bili.search_videos(query, order="pubdate")
            ]
        except BiliError:
            rows = []
        if not rows:
            rows = patrol.collect_candidates()
        rows = filter_videos(
            rows,
            seen=[],
            keywords=[],
            blocked=patrol.bcfg.get("blocked_keywords") or [],
            min_hits=0,
            min_duration=int(patrol.bcfg.get("min_duration_seconds", 0)),
            max_duration=int(patrol.bcfg.get("max_duration_seconds", 0)),
        )
        videos = []
        for row in rows[:limit]:
            videos.append({
                "bvid": row.get("bvid") or "",
                "title": row.get("title") or "",
                "author": row.get("author") or "",
                "url": row.get("url") or "",
                "duration": row.get("duration") or 0,
                "tags": (row.get("tags") or [])[:5],
            })
        return {"ok": True, "query": query, "videos": videos}

    def _patrol(self, body: Dict[str, Any]) -> Dict[str, Any]:
        patrol: Patrol = self.server.patrol
        allow_write = bool((self.server.config.get("actions") or {}).get("allow_write", False))
        dry_run = True
        if allow_write and body.get("dry_run") is False:
            dry_run = False
        try:
            result = patrol.patrol_once(dry_run=dry_run)
            return {"ok": True, "dry_run": dry_run, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class ActionServer:
    def __init__(self, config: Dict[str, Any], patrol: Patrol) -> None:
        acfg = config.get("actions") or {}
        self.config = config
        self.patrol = patrol
        self.host = str(acfg.get("host") or "127.0.0.1")
        self.port = int(acfg.get("port") or 18901)
        self.token = str(acfg.get("token") or "")
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.httpd is not None:
            return
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.config = self.config
        httpd.patrol = self.patrol
        httpd.action_token = self.token
        self.httpd = httpd
        self.thread = threading.Thread(
            target=httpd.serve_forever,
            daemon=True,
            name="bilibili-actions",
        )
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
