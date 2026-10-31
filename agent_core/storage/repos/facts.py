# -*- coding: utf-8 -*-
"""事实与摘要：原子事实、双时间线、日摘要。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from ...models import TimelineEvent
from ..jsonutil import _json, _loads


class FactOps:
    """事实与摘要：原子事实、双时间线、日摘要。"""

    def add_fact(
        self,
        user_id: str,
        content: str,
        category: str = "preference",
        supersedes_id: Optional[str] = None,
        source: str = "manual",
    ) -> str:
        """写一条事实。

        source: ai_extract（AI 从对话总结）/ manual（人工填写）/ legacy（历史迁移）
        """
        now_ts = time.time()
        fact_id = f"fact_{int(now_ts * 1000)}_{uuid.uuid4().hex[:6]}"
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO user_facts
                (fact_id, user_id, category, content, status, supersedes_id, created_at, updated_at, source)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (fact_id, user_id, category, content.strip(), supersedes_id, now_ts, now_ts, source),
            )
        return fact_id

    def supersede_fact(
        self, old_fact_id: str, new_content: str, category: Optional[str] = None, source: str = "manual"
    ) -> str:
        now_ts = time.time()
        with self._tx() as conn:
            old = conn.execute("SELECT * FROM user_facts WHERE fact_id = ?", (old_fact_id,)).fetchone()
            if not old:
                raise ValueError(f"fact not found: {old_fact_id}")
            conn.execute(
                "UPDATE user_facts SET status = 'superseded', updated_at = ? WHERE fact_id = ?",
                (now_ts, old_fact_id),
            )
            new_category = category or old["category"] or "preference"
            fact_id = f"fact_{int(now_ts * 1000)}_{uuid.uuid4().hex[:6]}"
            conn.execute(
                """
                INSERT INTO user_facts
                (fact_id, user_id, category, content, status, supersedes_id, created_at, updated_at, source)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (fact_id, old["user_id"], new_category, new_content.strip(), old_fact_id, now_ts, now_ts, source),
            )
            return fact_id

    def get_active_facts(
        self, user_id: str, category: Optional[str] = None, limit: int = 15
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM user_facts WHERE user_id = ? AND category = ? AND status = 'active' "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (user_id, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_facts WHERE user_id = ? AND status = 'active' "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_facts_for_prompt(self, user_id: str, limit: int = 8) -> List[str]:
        facts = self.get_active_facts(user_id, limit=limit)
        if facts:
            return [f["content"] for f in facts]
        profile = self.get_profile(user_id)
        if profile and isinstance(profile.get("facts"), list):
            return profile["facts"][-limit:]
        return []

    def list_undigested_before(self, cutoff_ts: float, limit: int = 100) -> List[TimelineEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_timeline WHERE timestamp <= ? ORDER BY timestamp ASC LIMIT ?",
                (cutoff_ts, limit),
            ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        return [e for e in events if not (e.metadata or {}).get("digested")]

    def insert_digest(
        self,
        digest_id: str,
        date_str: str,
        timestamp: float,
        narrative: str,
        key_facts: List[str],
        events_covered_count: int,
        created_at: float,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO memory_digests
                (digest_id, date_str, timestamp, narrative, key_facts, events_covered_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (digest_id, date_str, timestamp, narrative, _json(key_facts, "[]"), events_covered_count, created_at),
            )

    def mark_digested(self, event_ids: Iterable[str], digest_id: str) -> None:
        with self._tx() as conn:
            for event_id in event_ids:
                row = conn.execute(
                    "SELECT metadata FROM agent_timeline WHERE event_id = ?", (event_id,)
                ).fetchone()
                if not row:
                    continue
                meta = _loads(row["metadata"], {})
                meta["digested"] = True
                meta["digest_id"] = digest_id
                conn.execute(
                    "UPDATE agent_timeline SET metadata = ? WHERE event_id = ?",
                    (_json(meta, "{}"), event_id),
                )

    def list_digests(self, query: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if query:
                like = f"%{query.strip().lower()}%"
                rows = conn.execute(
                    "SELECT * FROM memory_digests WHERE LOWER(narrative) LIKE ? OR date_str LIKE ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memory_digests ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]
