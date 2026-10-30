# -*- coding: utf-8 -*-
"""模块本地状态：去重、配额、评论追踪、回复去重。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data: Dict[str, Any] = self._load()

    def _default(self) -> Dict[str, Any]:
        return {
            "date": today_str(),
            "today_comment_count": 0,
            "last_comment_ts": 0.0,
            "sessions": {},
            "seen_bvids": [],
            "tracked_comments": [],
            "processed_reply_rpids": [],
        }

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default()
        if not isinstance(data, dict):
            return self._default()
        merged = self._default()
        merged.update(data)
        if merged.get("date") != today_str():
            merged["date"] = today_str()
            merged["today_comment_count"] = 0
            merged["sessions"] = {}
        return merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix=".bili_state_", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def reset_daily_if_needed(self) -> None:
        if self.data.get("date") != today_str():
            self.data["date"] = today_str()
            self.data["today_comment_count"] = 0
            self.data["sessions"] = {}
            self.save()

    # ---- 去重 ----
    def seen_bvids(self) -> List[str]:
        return [str(x) for x in (self.data.get("seen_bvids") or [])]

    def mark_seen(self, bvid: str) -> None:
        seen = self.seen_bvids()
        if bvid and bvid not in seen:
            seen.append(bvid)
            self.data["seen_bvids"] = seen[-1000:]
            self.save()

    # ---- 配额 ----
    def quota_state(self, session_key: str) -> Dict[str, Any]:
        sessions = self.data.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
            self.data["sessions"] = sessions
        current = sessions.get(session_key)
        if not isinstance(current, dict):
            current = {"count": 0, "last_comment_ts": 0.0}
            sessions[session_key] = current
        return current

    def can_comment(
        self,
        session_key: str,
        *,
        daily_limit: int,
        session_limit: int,
        min_interval_seconds: int,
        now: float = 0.0,
    ) -> Tuple[bool, str]:
        self.reset_daily_if_needed()
        now = now or time.time()
        if int(self.data.get("today_comment_count") or 0) >= int(daily_limit):
            return False, f"今日评论额度已满（{daily_limit}）"
        current = self.quota_state(session_key)
        if int(current.get("count") or 0) >= int(session_limit):
            return False, f"本轮评论额度已满（{session_limit}）"
        last_ts = float(current.get("last_comment_ts") or self.data.get("last_comment_ts") or 0)
        if int(current.get("count") or 0) > 0 and min_interval_seconds > 0:
            remaining = int(min_interval_seconds - (now - last_ts))
            if remaining > 0:
                return False, f"评论节流中，还需 {remaining} 秒"
        return True, ""

    def record_comment(self, session_key: str, now: float = 0.0) -> None:
        now = now or time.time()
        self.reset_daily_if_needed()
        self.data["today_comment_count"] = int(self.data.get("today_comment_count") or 0) + 1
        self.data["last_comment_ts"] = now
        current = self.quota_state(session_key)
        current["count"] = int(current.get("count") or 0) + 1
        current["last_comment_ts"] = now
        self.save()

    # ---- 追踪 ----
    def tracked_comments(self) -> List[Dict[str, Any]]:
        rows = self.data.get("tracked_comments")
        return rows if isinstance(rows, list) else []

    def add_tracked(self, record: Dict[str, Any]) -> None:
        rows = [r for r in self.tracked_comments() if int(r.get("rpid") or 0) != int(record.get("rpid") or 0)]
        rows.append(record)
        self.data["tracked_comments"] = rows[-100:]
        self.save()

    def processed_replies(self) -> List[int]:
        return [int(x) for x in (self.data.get("processed_reply_rpids") or [])]

    def mark_reply_processed(self, rpid: int) -> None:
        rows = self.processed_replies()
        if rpid and rpid not in rows:
            rows.append(rpid)
            self.data["processed_reply_rpids"] = rows[-500:]
            self.save()
