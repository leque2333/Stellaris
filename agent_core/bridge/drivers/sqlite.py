# -*- coding: utf-8 -*-
"""SQLite 事件驱动：按 spec 的游标增量读表，统一成 HubEvent"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine import BridgeDriver
from ..models import BridgeSpec, HubEvent

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WRAPPER_RE = re.compile(r"<\s*message_batch[^>]*>(.*?)</\s*message_batch\s*>", re.S)


def _safe_ident(name: str) -> str:
    text = str(name or "").strip()
    if not IDENT_RE.match(text):
        raise ValueError(f"非法表/字段名: {text}")
    return text


def _extract_text(value: Any, content_format: str = "") -> str:
    if value is None:
        return ""
    if content_format in ("json", "astrbot_json"):
        try:
            obj = json.loads(value) if isinstance(value, str) else value
        except Exception:
            return str(value)
        texts: List[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, val in node.items():
                    if key in ("text", "content", "message") and isinstance(val, str):
                        texts.append(val)
                    else:
                        walk(val)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)
        text = " ".join(t for t in texts if t).strip()
        if text:
            return text
        return json.dumps(obj, ensure_ascii=False)[:2000]
    return str(value)


def _strip_wrapper(text: str, wrapper: str = "") -> str:
    if not text:
        return ""
    if wrapper == "message_batch" or "<message_batch" in text:
        match = WRAPPER_RE.search(text)
        if match:
            return match.group(1).strip()
    return text.strip()


def _get_pointer(data: Any, pointer: str) -> Any:
    cur = data
    for part in str(pointer or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
    return cur


def _parse_time(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


class SqliteEventDriver(BridgeDriver):
    kind = "sqlite"

    def __init__(self, spec: BridgeSpec, emit, store=None):
        super().__init__(spec, emit, store)
        self.path = Path(spec.locator).resolve()
        opts = spec.options
        self.table = _safe_ident(opts.get("table") or "messages")
        self.cursor_col = _safe_ident(opts.get("cursor") or "id")
        self.id_col = _safe_ident(opts.get("id") or opts.get("cursor") or "id")
        self.time_col = _safe_ident(opts["time"]) if opts.get("time") else ""
        self.channel_col = _safe_ident(opts["channel"]) if opts.get("channel") else ""
        self.actor_col = _safe_ident(opts["actor"]) if opts.get("actor") else ""
        self.content_col = _safe_ident(opts.get("content") or "content")
        self.channel_name_col = opts.get("channel_name")
        self.actor_name_col = opts.get("actor_name")
        self.platform = str(opts.get("platform") or "unknown")
        self.content_format = str(opts.get("content_format") or "")
        self.strip_wrapper = str(opts.get("strip_wrapper") or "")
        self.poll_interval = max(0.5, float(opts.get("poll_interval") or 2.0))
        self.limit = max(1, min(int(opts.get("limit") or 100), 500))
        self.cursor = (
            store.get_bridge_cursor(spec.source_id)
            if store else opts.get("initial_cursor")
        ) or str(opts.get("initial_cursor") or "0")

    def poll_once(self) -> List[HubEvent]:
        if not self.path.exists():
            return []
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"SELECT * FROM {self.table} WHERE {self.cursor_col} > ? "
                f"ORDER BY {self.cursor_col} ASC LIMIT ?",
                (self.cursor, self.limit),
            ).fetchall()
        finally:
            conn.close()

        events: List[HubEvent] = []
        for row in rows:
            cursor_value = row[self.cursor_col]
            if cursor_value is None:
                continue
            event_id = str(row[self.id_col])
            raw_content = str(row[self.content_col] or "")
            content = _extract_text(
                _strip_wrapper(raw_content, self.strip_wrapper),
                self.content_format,
            ).strip()
            if not content:
                continue
            channel_id = str(row[self.channel_col] or "") if self.channel_col else ""
            actor_id = str(row[self.actor_col] or "") if self.actor_col else ""
            channel_name = str(row[self.channel_name_col] or "") if self.channel_name_col else ""
            actor_name = str(row[self.actor_name_col] or "") if self.actor_name_col else ""
            events.append(HubEvent(
                source=self.spec.source_id,
                event_key=f"{self.spec.source_id}:{event_id}",
                platform=self.platform,
                channel_id=channel_id,
                channel_name=channel_name,
                actor_id=actor_id,
                actor_name=actor_name,
                content=content,
                timestamp=_parse_time(row[self.time_col]) if self.time_col else time.time(),
                raw={"table": self.table, "id": event_id},
            ))
            self.cursor = str(cursor_value)
            if self.store:
                self.store.set_bridge_cursor(self.spec.source_id, self.cursor)
        return events

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                events = await asyncio.to_thread(self.poll_once)
                for event in events:
                    await self.emit(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue
