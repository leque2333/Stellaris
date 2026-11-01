# -*- coding: utf-8 -*-
"""AstrBot 宿主适配器：只在控制台里做可选发现，不进入中枢核心"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .base import Adapter, RequestView


class AstrBotAdapter(Adapter):
    """可选宿主适配：没有 AstrBot 时所有路径返回空，控制台照常运行。"""

    plugin_id = "astrbot"
    priority = 10

    def __init__(self) -> None:
        self.data_dir = self._resolve_data_dir()
        self._group_cache: Dict[str, str] = {}

    def detect(self, request: RequestView) -> float:
        host = request.header("X-Host-Id").lower()
        ua = request.header("User-Agent").lower()
        session = str(request.session_id or "").lower()
        if host == "astrbot" or "astrbot" in ua or "astrbot" in session:
            return 1.0
        if request.header("x-astrbot-conversation-id"):
            return 0.9
        return 0.2 if self.available else 0.0

    def context(self, request: RequestView) -> Dict[str, Any]:
        body = request.body or {}
        platform = request.header("X-Platform") or str(body.get("platform") or "")
        session = str(request.session_id or request.header("X-Session-Id") or "")
        group = request.header("X-Group-Id") or self._group_from_session(session)
        return {
            "plugin_id": self.plugin_id,
            "host_id": request.header("X-Host-Id") or "astrbot",
            "platform": platform.lower(),
            "uid": request.header("X-User-Id") or str(body.get("user") or ""),
            "name": request.header("X-User-Name") or "",
            "session": session,
            "group": group,
            "channel": request.channel_id,
        }

    def group_name(self, group_id: str) -> str:
        gid = str(group_id or "").strip()
        if not gid:
            return ""
        if gid in self._group_cache:
            return self._group_cache[gid]
        path = self.v4_db_path()
        if not path:
            return ""
        try:
            uri = "file:" + path.replace("\\", "/") + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                row = conn.execute(
                    "SELECT auto_name, user_alias FROM umo_aliases "
                    "WHERE umo LIKE ? ORDER BY updated_at DESC LIMIT 1",
                    (f"%GroupMessage:{gid}",),
                ).fetchone()
            finally:
                conn.close()
            if row:
                name = str(row[1] or row[0] or "").strip()
                if name:
                    self._group_cache[gid] = name
                    return name
        except Exception:
            pass
        return ""

    @staticmethod
    def _group_from_session(session: str) -> str:
        match = re.search(r"(?:GroupMessage|group):([0-9]+)", str(session or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _resolve_data_dir() -> Optional[Path]:
        env = os.getenv("ASTRBOT_DATA_DIR", "").strip()
        if env:
            path = Path(env).expanduser()
            return path if path.is_dir() else None

        roots = [
            Path(__file__).resolve().parents[2],
            Path.cwd(),
        ]
        for root in roots:
            for rel in ("astrbot/data", "AstrBot/data", "astrbot"):
                candidate = root / rel
                if candidate.is_dir() and (
                    (candidate / "cmd_config.json").exists()
                    or (candidate / "plugins").is_dir()
                ):
                    return candidate
        return None

    def path(self, name: str) -> str:
        if not self.data_dir:
            return ""
        candidate = self.data_dir / name
        return str(candidate) if candidate.exists() else ""

    @property
    def available(self) -> bool:
        return self.data_dir is not None

    def cmd_config_path(self) -> str:
        return self.path("cmd_config.json")

    def plugins_dir(self) -> str:
        path = self.data_dir / "plugins" if self.data_dir else None
        return str(path) if path and path.is_dir() else ""

    def mcp_path(self) -> str:
        return self.path("mcp.json")

    def v4_db_path(self) -> str:
        # AstrBot 实际库名是 data_v4.db；旧名 astrbot_v4.db 只作兼容
        return self.path("data_v4.db") or self.path("astrbot_v4.db")
