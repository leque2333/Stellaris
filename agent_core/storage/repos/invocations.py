# -*- coding: utf-8 -*-
"""调用段与明细：一次请求的分层留痕。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from ..jsonutil import _json


class InvocationOps:
    """调用段与明细：一次请求的分层留痕。"""

    def record_invocation(
        self,
        trace_id: str,
        layer: str,
        module_id: str,
        *,
        tool_name: Optional[str] = None,
        channel_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        started_at: Optional[float] = None,
        parent_id: Optional[str] = None,
        summary: Optional[str] = None,
        detail_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = float(started_at if started_at is not None else time.time())
        invocation_id = f"inv_{int(now * 1000)}_{uuid.uuid4().hex[:6]}"
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO invocations
                (invocation_id, trace_id, parent_id, layer, module_id, tool_name, channel_id,
                 actor_id, started_at, status, summary, detail_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    invocation_id, trace_id, parent_id, layer, module_id, tool_name,
                    channel_id, actor_id, now, summary, detail_id,
                    _json(metadata or {}, "{}"),
                ),
            )
        return invocation_id

    def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: str = "ok",
        ended_at: Optional[float] = None,
        summary: Optional[str] = None,
        detail_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> None:
        fields = ["status = ?", "ended_at = ?"]
        params: List[Any] = [status, float(ended_at if ended_at is not None else time.time())]
        if summary is not None:
            fields.append("summary = ?")
            params.append(summary)
        if detail_id is not None:
            fields.append("detail_id = ?")
            params.append(detail_id)
        if event_id is not None:
            fields.append("event_id = ?")
            params.append(event_id)
        params.append(invocation_id)
        with self._tx() as conn:
            conn.execute(f"UPDATE invocations SET {', '.join(fields)} WHERE invocation_id = ?", tuple(params))

    def list_invocations(
        self,
        *,
        trace_id: Optional[str] = None,
        event_id: Optional[str] = None,
        module_id: Optional[str] = None,
        segment_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        conditions: List[str] = []
        params: List[Any] = []
        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if event_id:
            conditions.append("event_id = ?")
            params.append(event_id)
        if module_id:
            conditions.append("module_id = ?")
            params.append(module_id)
        if segment_id:
            conditions.append("segment_id = ?")
            params.append(segment_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM invocations {where} ORDER BY started_at DESC LIMIT ?", tuple(params)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_unassigned_invocations(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invocations WHERE segment_id IS NULL AND status != 'running' "
                "ORDER BY started_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def assign_invocation(self, invocation_id: str, segment_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE invocations SET segment_id = ? WHERE invocation_id = ?",
                (segment_id, invocation_id),
            )

    def set_invocation_event(self, invocation_id: str, event_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE invocations SET event_id = ? WHERE invocation_id = ?",
                (event_id, invocation_id),
            )

    def list_unassigned_activities(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plugin_activities WHERE segment_id IS NULL "
                "ORDER BY occurred_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def assign_activity_segment(self, activity_id: str, segment_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE plugin_activities SET segment_id = ? WHERE activity_id = ?",
                (segment_id, activity_id),
            )

    def save_detail(
        self,
        kind: str,
        content: Any,
        *,
        invocation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        mime: str = "application/json",
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False)
        elif isinstance(content, bytes):
            text = content.decode("utf-8", "replace")
        else:
            text = str(content)
        now = time.time()
        detail_id = f"dtl_{int(now * 1000)}_{uuid.uuid4().hex[:6]}"
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO memory_details
                (detail_id, invocation_id, event_id, kind, mime, content, size_bytes,
                 created_at, expires_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail_id, invocation_id, event_id, kind, mime, text,
                    len(text.encode("utf-8")), now, expires_at,
                    _json(metadata or {}, "{}"),
                ),
            )
        return detail_id

    def get_detail(self, detail_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_details WHERE detail_id = ?", (detail_id,)).fetchone()
        return dict(row) if row else None

    def list_details(
        self,
        *,
        event_id: Optional[str] = None,
        invocation_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conditions: List[str] = []
        params: List[Any] = []
        if event_id:
            conditions.append("event_id = ?")
            params.append(event_id)
        if invocation_id:
            conditions.append("invocation_id = ?")
            params.append(invocation_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_details {where} ORDER BY created_at DESC LIMIT ?", tuple(params)
            ).fetchall()
        return [dict(r) for r in rows]
