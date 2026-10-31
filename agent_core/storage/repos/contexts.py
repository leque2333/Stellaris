# -*- coding: utf-8 -*-
"""上下文与绑定：平台会话到档案的关系。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class ContextOps:
    """上下文与绑定：平台会话 ↔ 档案的关系。"""

    def upsert_context(
        self,
        platform: str,
        external_id: str,
        *,
        external_type: str = "",
        display_name: str = "",
        source: str = "",
        evidence: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        seen_at: Optional[float] = None,
        host_id: str = "",
    ) -> str:
        now = float(seen_at if seen_at is not None else time.time())
        evidence = list(evidence or [])
        metadata = dict(metadata or {})
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM platform_contexts WHERE platform = ? AND external_id = ?",
                (platform, external_id),
            ).fetchone()
            if row:
                old_evidence = _loads(row["evidence"], [])
                merged = list(dict.fromkeys(list(old_evidence) + evidence))[-20:]
                old_meta = _loads(row["metadata"], {})
                old_meta.update(metadata)
                old_sources = [s for s in str(row["source"] or "").split(",") if s]
                new_sources = [s for s in str(source or "").split(",") if s]
                merged_sources = ",".join(dict.fromkeys(old_sources + new_sources))
                conn.execute(
                    """
                    UPDATE platform_contexts
                       SET external_type = COALESCE(NULLIF(?, ''), external_type),
                           display_name = COALESCE(NULLIF(?, ''), display_name),
                           source = ?,
                           status = CASE WHEN status = 'ignored' THEN 'ignored' ELSE 'active' END,
                           evidence = ?, last_seen = ?, metadata = ?,
                           host_id = COALESCE(NULLIF(?, ''), host_id)
                     WHERE context_id = ?
                    """,
                    (
                        external_type, display_name, merged_sources,
                        _json(merged, "[]"), now, _json(old_meta, "{}"),
                        host_id, row["context_id"],
                    ),
                )
                return row["context_id"]

            context_id = f"ctx_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO platform_contexts
                (context_id, platform, external_id, external_type, display_name, source,
                 status, evidence, first_seen, last_seen, metadata, host_id)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    context_id, platform, external_id, external_type, display_name, source,
                    _json(evidence, "[]"), now, now, _json(metadata, "{}"), host_id,
                ),
            )
            return context_id

    def list_contexts(
        self,
        *,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        host_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        conditions: List[str] = []
        params: List[Any] = []
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if host_id:
            conditions.append("host_id = ?")
            params.append(host_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM platform_contexts {where} ORDER BY last_seen DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_contexts_stale(
        self, source: str, seen_external_ids: List[str], now: Optional[float] = None
    ) -> int:
        seen = set(seen_external_ids)
        changed = 0
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT context_id, external_id FROM platform_contexts WHERE source = ? AND status = 'active'",
                (source,),
            ).fetchall()
            for row in rows:
                if row["external_id"] not in seen:
                    conn.execute(
                        "UPDATE platform_contexts SET status = 'stale' WHERE context_id = ?",
                        (row["context_id"],),
                    )
                    changed += 1
        return changed

    def set_context_status(self, context_id: str, status: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE platform_contexts SET status = ? WHERE context_id = ?",
                (status, context_id),
            )

    def bind_context(
        self,
        platform: str,
        external_id: str,
        archive_id: str,
        *,
        confidence: float = 0.0,
        verified_by: str = "manual",
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT binding_id FROM platform_bindings WHERE platform = ? AND external_id = ?",
                (platform, external_id),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE platform_bindings
                       SET archive_id = ?, enabled = 1, updated_at = ?,
                           confidence = ?, verified_by = ?
                     WHERE binding_id = ?
                    """,
                    (archive_id, now, float(confidence), verified_by, row["binding_id"]),
                )
                return row["binding_id"]
            binding_id = f"bind_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO platform_bindings
                (binding_id, platform, external_id, archive_id, enabled,
                 created_at, updated_at, confidence, verified_by)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    binding_id, platform, external_id, archive_id, now, now,
                    float(confidence), verified_by,
                ),
            )
            return binding_id

    def unbind_context(self, platform: str, external_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM platform_bindings WHERE platform = ? AND external_id = ?",
                (platform, external_id),
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    def list_bindings(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT b.*, c.display_name, c.external_type
                  FROM platform_bindings b
                  LEFT JOIN platform_contexts c
                    ON c.platform = b.platform AND c.external_id = b.external_id
                 ORDER BY b.updated_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_binding(self, platform: str, external_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT archive_id FROM platform_bindings "
                "WHERE platform = ? AND external_id = ? AND enabled = 1",
                (platform, external_id),
            ).fetchone()
        return row["archive_id"] if row else None
