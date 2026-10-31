# -*- coding: utf-8 -*-
"""账本与会话段：token 流水、活动、连续会话。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from ...models import get_cst_now_str
from ..jsonutil import _json, _loads


class LedgerOps:
    """账本与会话段：token 流水、活动、连续会话。"""

    def record_usage(
        self,
        session_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
        cached_tokens: int = 0,
        call_duration_ms: int = 0,
        cost_usd: float = 0.0,
        source: str = "universal_proxy",
        credential_type: str = "",
        currency: str = "USD",
        tools: str = "",
    ) -> int:
        now_ts = time.time()
        total = total_tokens if total_tokens is not None else int(prompt_tokens) + int(completion_tokens)
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO token_usage
                (timestamp, time_str, session_type, model, prompt_tokens, completion_tokens,
                 total_tokens, cached_tokens, call_duration_ms, cost_usd, source,
                 credential_type, currency, tools)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ts, get_cst_now_str(now_ts), session_type, model,
                    int(prompt_tokens), int(completion_tokens), int(total),
                    int(cached_tokens), int(call_duration_ms), float(cost_usd), source,
                    credential_type or "", (currency or "USD").upper(),
                    str(tools or ""),
                ),
            )
            return int(cur.lastrowid or 0)

    def query_usage(self, limit: int = 50, session_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if session_type:
                rows = conn.execute(
                    "SELECT * FROM token_usage WHERE session_type = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM token_usage ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    def insert_activities(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        now = time.time()
        with self._tx() as conn:
            for item in items:
                activity_id = str(item.get("activity_id") or "").strip()
                plugin_id = str(item.get("plugin_id") or "").strip()
                if not activity_id or not plugin_id:
                    results.append({"activity_id": activity_id, "status": "rejected", "reason": "missing activity_id/plugin_id"})
                    continue
                if conn.execute(
                    "SELECT 1 FROM plugin_activities WHERE activity_id = ?", (activity_id,)
                ).fetchone():
                    results.append({"activity_id": activity_id, "status": "duplicate"})
                    continue
                occurred_at = float(item.get("occurred_at") or now)
                conn.execute(
                    """
                    INSERT INTO plugin_activities
                    (activity_id, plugin_id, session_id, context_kind, context_id, context_name,
                     occurred_at, kind, verb, object, outcome, actor_id, actor_name, detail,
                     importance, emotional_impact, trace_id, metadata, segment_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        activity_id, plugin_id, item.get("session_id"),
                        item.get("context_kind"), item.get("context_id"), item.get("context_name"),
                        occurred_at, item.get("kind", "action"), item.get("verb"), item.get("object"),
                        item.get("outcome"), item.get("actor_id"), item.get("actor_name"),
                        item.get("detail"), float(item.get("importance", 1.0)),
                        int(item.get("emotional_impact", 0)), item.get("trace_id"),
                        _json(item.get("metadata", {}), "{}"), item.get("segment_id"), now,
                    ),
                )
                results.append({"activity_id": activity_id, "status": "accepted"})
        return results

    def activities_for_segment(self, segment_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plugin_activities WHERE segment_id = ? ORDER BY occurred_at ASC", (segment_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def activity_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM plugin_activities").fetchone()[0])

    def get_open_segment(self, plugin_id: str, context_kind: str, context_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM timeline_segments WHERE plugin_id = ? AND context_kind = ? AND context_id = ? "
                "AND status = 'open' ORDER BY started_at DESC LIMIT 1",
                (plugin_id, context_kind, context_id),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["participants"] = _loads(data.get("participants"), [])
        data["stats"] = _loads(data.get("stats"), {})
        return data

    def create_segment(
        self,
        plugin_id: str,
        context_kind: str,
        context_id: str,
        context_name: str,
        started_at: float,
    ) -> Dict[str, Any]:
        segment_id = f"seg_{int(started_at * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO timeline_segments
                (segment_id, plugin_id, context_kind, context_id, context_name, started_at, last_at,
                 status, fragment_count, participants, stats, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, '[]', '{}', ?)
                """,
                (segment_id, plugin_id, context_kind, context_id, context_name, started_at, started_at, now),
            )
        return {
            "segment_id": segment_id, "plugin_id": plugin_id, "context_kind": context_kind,
            "context_id": context_id, "context_name": context_name, "started_at": started_at,
            "last_at": started_at, "status": "open", "fragment_count": 0,
            "participants": [], "stats": {},
        }

    def touch_segment(
        self,
        segment_id: str,
        last_at: float,
        add_fragments: int = 1,
        participants: Optional[List[str]] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT participants, stats, fragment_count FROM timeline_segments WHERE segment_id = ?",
                (segment_id,),
            ).fetchone()
            if not row:
                return
            merged_participants = list(_loads(row["participants"], []))
            for name in participants or []:
                if name and name not in merged_participants:
                    merged_participants.append(name)
            merged_stats = dict(_loads(row["stats"], {}))
            for key, value in (stats or {}).items():
                if isinstance(value, (int, float)) and isinstance(merged_stats.get(key), (int, float)):
                    merged_stats[key] = merged_stats[key] + value
                else:
                    merged_stats[key] = value
            conn.execute(
                "UPDATE timeline_segments SET last_at = ?, fragment_count = ?, participants = ?, stats = ? "
                "WHERE segment_id = ?",
                (
                    float(last_at), int(row["fragment_count"] or 0) + int(add_fragments),
                    _json(merged_participants, "[]"), _json(merged_stats, "{}"), segment_id,
                ),
            )

    def close_segment(self, segment_id: str, ended_at: float) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE timeline_segments SET status = 'closed', ended_at = ?, last_at = ? WHERE segment_id = ?",
                (float(ended_at), float(ended_at), segment_id),
            )

    def set_segment_event(self, segment_id: str, event_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE timeline_segments SET event_id = ? WHERE segment_id = ?", (event_id, segment_id)
            )

    def list_stale_segments(self, now_ts: float, idle_seconds: float) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM timeline_segments WHERE status = 'open' AND last_at <= ? ORDER BY last_at ASC",
                (float(now_ts) - float(idle_seconds),),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["participants"] = _loads(data.get("participants"), [])
            data["stats"] = _loads(data.get("stats"), {})
            result.append(data)
        return result
