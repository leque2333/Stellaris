# -*- coding: utf-8 -*-
"""时间轴事件：写入、读取、召回候选、摘要切分。"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from ...models import TimelineEvent, get_cst_now_str
from ..jsonutil import _json, _loads


class TimelineOps:
    """时间轴事件：写入、读取、召回候选、摘要切分。"""

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TimelineEvent:
        keys = row.keys()
        return TimelineEvent(
            event_id=row["event_id"],
            timestamp=float(row["timestamp"]),
            time_str=row["time_str"] or get_cst_now_str(float(row["timestamp"])),
            source_module=row["source_module"],
            actor_id=row["actor_id"],
            actor_name=row["actor_name"],
            narrative=row["narrative"],
            emotional_impact=int(row["emotional_impact"] or 0),
            importance=float(row["importance"] or 1.0),
            prev_event_id=row["prev_event_id"],
            causal_relation=row["causal_relation"],
            metadata=_loads(row["metadata"], {}),
            embedding=row["embedding"] if "embedding" in keys else None,
            detail_id=(row["detail_id"] if "detail_id" in keys and row["detail_id"] else ""),
            person_id=(row["person_id"] if "person_id" in keys and row["person_id"] else ""),
            plugin_id=row["plugin_id"] if "plugin_id" in keys else None,
            tag_primary=row["tag_primary"] if "tag_primary" in keys else None,
            tag_secondary=row["tag_secondary"] if "tag_secondary" in keys else None,
            tag_permission=row["tag_permission"] if "tag_permission" in keys else None,
            segment_id=row["segment_id"] if "segment_id" in keys else None,
            started_at=row["started_at"] if "started_at" in keys else None,
            ended_at=row["ended_at"] if "ended_at" in keys else None,
            visibility=(row["visibility"] if "visibility" in keys and row["visibility"] else "private"),
        )

    def append_event(
        self,
        event: TimelineEvent,
        *,
        session_window_seconds: int = 0,
        prev_window_seconds: int = 1800,
    ) -> TimelineEvent:
        now_ts = float(event.timestamp or time.time())

        with self._tx() as conn:
            # 同源高频会话折叠：命中窗口则更新最近一条，而不是新增
            if session_window_seconds > 0:
                last = conn.execute(
                    "SELECT * FROM agent_timeline WHERE source_module = ? AND actor_id = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (event.source_module, event.actor_id),
                ).fetchone()
                if last and (now_ts - float(last["timestamp"])) <= session_window_seconds:
                    meta = _loads(last["metadata"], {})
                    count = int(meta.get("session_count", 1)) + 1
                    items = list(meta.get("recent_items", []))[-4:] + [event.narrative[:60]]
                    meta["session_count"] = count
                    meta["recent_items"] = items
                    narrative = (
                        f"{event.narrative} "
                        f"(近{session_window_seconds // 60}分钟内已连续进行 {count} 次相关交互)"
                    )
                    merged_importance = max(float(event.importance or 1.0), float(last["importance"] or 1.0))
                    merged_impact = int(event.emotional_impact or 0) or int(last["emotional_impact"] or 0)
                    conn.execute(
                        "UPDATE agent_timeline SET timestamp = ?, time_str = ?, narrative = ?, metadata = ?, "
                        "importance = ?, emotional_impact = ?, detail_id = ?, embedding = ?, person_id = ? "
                        "WHERE event_id = ?",
                        (
                            now_ts, get_cst_now_str(now_ts), narrative, _json(meta, "{}"),
                            merged_importance, merged_impact, event.detail_id or "",
                            event.embedding, event.person_id or event.actor_id, last["event_id"],
                        ),
                    )
                    updated = conn.execute(
                        "SELECT * FROM agent_timeline WHERE event_id = ?", (last["event_id"],)
                    ).fetchone()
                    return self._row_to_event(updated)

            prev_id = event.prev_event_id
            if prev_id is None:
                prev = conn.execute(
                    "SELECT event_id FROM agent_timeline WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                    (now_ts - prev_window_seconds,),
                ).fetchone()
                if prev:
                    prev_id = prev["event_id"]

            event_id = event.event_id or f"evt_{int(now_ts * 1000)}_{uuid.uuid4().hex[:6]}"
            meta = event.metadata or {}
            conn.execute(
                """
                INSERT INTO agent_timeline
                (event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative,
                 emotional_impact, importance, prev_event_id, causal_relation, metadata, embedding,
                 plugin_id, tag_primary, tag_secondary, tag_permission, segment_id, started_at, ended_at, visibility, detail_id,
                 person_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, now_ts, get_cst_now_str(now_ts), event.source_module, event.actor_id,
                    event.actor_name, event.narrative, event.emotional_impact, event.importance,
                    prev_id, event.causal_relation, _json(meta, "{}"), event.embedding,
                    event.plugin_id, event.tag_primary, event.tag_secondary, event.tag_permission, event.segment_id,
                    event.started_at if event.started_at is not None else now_ts,
                    event.ended_at if event.ended_at is not None else now_ts,
                    event.visibility or "private",
                    event.detail_id or "",
                    event.person_id or event.actor_id,
                ),
            )

        event.event_id = event_id
        event.time_str = get_cst_now_str(now_ts)
        event.prev_event_id = prev_id
        return event

    def get_event(self, event_id: str) -> Optional[TimelineEvent]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_timeline WHERE event_id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def update_event_detail(self, event_id: str, detail_id: str) -> None:
        if not event_id or not detail_id:
            return
        with self._tx() as conn:
            conn.execute(
                "UPDATE agent_timeline SET detail_id = ? WHERE event_id = ?",
                (str(detail_id), str(event_id)),
            )

    def list_unembedded_events(self, limit: int = 200) -> List[TimelineEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_timeline WHERE embedding IS NULL "
                "ORDER BY timestamp DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def update_event_embedding(self, event_id: str, embedding: bytes) -> None:
        if not event_id or not embedding:
            return
        with self._tx() as conn:
            conn.execute(
                "UPDATE agent_timeline SET embedding = ? WHERE event_id = ?",
                (embedding, str(event_id)),
            )

    def list_events(
        self,
        *,
        limit: int = 10,
        since_ts: Optional[float] = None,
        source_module: Optional[str] = None,
        actor_ids: Optional[Iterable[str]] = None,
        order: str = "desc",
        tag_primary: Optional[str] = None,
        tag_secondary: Optional[str] = None,
        include_embedding: bool = False,
    ) -> List[TimelineEvent]:
        conditions: List[str] = []
        params: List[Any] = []
        if since_ts is not None:
            conditions.append("timestamp >= ?")
            params.append(since_ts)
        if source_module:
            conditions.append("source_module = ?")
            params.append(source_module)
        if actor_ids:
            ids = list(actor_ids)
            conditions.append(f"actor_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if tag_primary:
            conditions.append("tag_primary = ?")
            params.append(tag_primary)
        if tag_secondary:
            conditions.append("tag_secondary = ?")
            params.append(tag_secondary)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        sql = f"SELECT * FROM agent_timeline {where} ORDER BY timestamp {direction} LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_candidates(
        self,
        *,
        actor_ids: Optional[Iterable[str]] = None,
        person_ids: Optional[Iterable[str]] = None,
        source_modules: Optional[Iterable[str]] = None,
        exclude_group: bool = False,
        since_ts: Optional[float] = None,
        exclude_sources: Optional[Iterable[str]] = None,
        limit: int = 200,
    ) -> List[TimelineEvent]:
        params: List[Any] = []
        clauses: List[str] = []
        if actor_ids:
            ids = list(actor_ids)
            clauses.append(f"actor_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if source_modules:
            sources = list(source_modules)
            clauses.append(f"source_module IN ({','.join('?' for _ in sources)})")
            params.extend(sources)
        where = "WHERE (" + " OR ".join(clauses) + ")" if clauses else ""
        if person_ids:
            pids = list(person_ids)
            person_clause = f"person_id IN ({','.join('?' for _ in pids)})"
            where = f"{where} AND {person_clause}" if where else f"WHERE {person_clause}"
            params.extend(pids)
        if exclude_group:
            group_filter = (
                "source_module NOT LIKE 'qq_group:%' "
                "AND source_module NOT LIKE 'group:%'"
            )
            where = f"{where} AND {group_filter}" if where else f"WHERE {group_filter}"
        archive_filter = "COALESCE(visibility, 'private') != 'archived'"
        where = f"{where} AND {archive_filter}" if where else f"WHERE {archive_filter}"
        # 时间窗：不按条数截断，按「回看多少天」收敛
        if since_ts:
            clause = "timestamp >= ?"
            where = f"{where} AND {clause}" if where else f"WHERE {clause}"
            params.append(float(since_ts))
        # 来源过滤：测试/探针通道不进召回候选
        if exclude_sources:
            sources = [str(item) for item in exclude_sources if str(item)]
            if sources:
                clause = f"source_module NOT IN ({','.join('?' for _ in sources)})"
                where = f"{where} AND {clause}" if where else f"WHERE {clause}"
                params.extend(sources)
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_timeline {where} ORDER BY timestamp DESC LIMIT ?", tuple(params)
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def search_narratives(
        self,
        terms: List[str],
        *,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        person_ids: Optional[Iterable[str]] = None,
        sources: Optional[Iterable[str]] = None,
        limit: int = 200,
        match_all: bool = True,
    ) -> List[TimelineEvent]:
        """按关键词 + 时间范围翻时间轴；默认要求全部命中，precision 更稳。"""
        terms = [str(term).strip() for term in terms or [] if str(term).strip()]
        if not terms:
            return []
        joiner = " AND " if match_all else " OR "
        clauses = ["(" + joiner.join("narrative LIKE ?" for _ in terms) + ")",
                   "COALESCE(visibility, 'private') != 'archived'"]
        params: List[Any] = [f"%{term}%" for term in terms]
        if start_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(float(start_ts))
        if end_ts is not None:
            clauses.append("timestamp <= ?")
            params.append(float(end_ts))
        if person_ids:
            ids = [str(item) for item in person_ids]
            clauses.append(f"person_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if sources:
            picked = [str(item) for item in sources if str(item)]
            if picked:
                clauses.append(f"source_module IN ({','.join('?' for _ in picked)})")
                params.extend(picked)
        params.append(int(limit))
        sql = (
            "SELECT * FROM agent_timeline WHERE " + " AND ".join(clauses)
            + " ORDER BY timestamp DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_event(row) for row in rows]

    def surrounding(self, event_id: str, before_n: int = 2, after_n: int = 2) -> List[TimelineEvent]:
        with self._connect() as conn:
            target = conn.execute("SELECT * FROM agent_timeline WHERE event_id = ?", (event_id,)).fetchone()
            if not target:
                return []
            target_ts = target["timestamp"]
            before = conn.execute(
                "SELECT * FROM agent_timeline WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
                (target_ts, before_n),
            ).fetchall()
            after = conn.execute(
                "SELECT * FROM agent_timeline WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                (target_ts, after_n),
            ).fetchall()
        rows = list(reversed(before)) + [target] + list(after)
        return [self._row_to_event(r) for r in rows]

    def update_event_metadata(self, event_id: str, metadata: Dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE agent_timeline SET metadata = ? WHERE event_id = ?",
                (_json(metadata, "{}"), event_id),
            )

    def timeline_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM agent_timeline").fetchone()[0])

    def import_events(self, rows: Iterable[Dict[str, Any]]) -> int:
        cols = (
            "event_id", "timestamp", "time_str", "source_module", "actor_id", "actor_name",
            "narrative", "emotional_impact", "importance", "prev_event_id", "causal_relation",
            "metadata", "embedding", "plugin_id", "tag_primary", "tag_secondary", "tag_permission", "segment_id",
            "started_at", "ended_at", "visibility",
        )
        sql = (
            f"INSERT OR IGNORE INTO agent_timeline ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})"
        )
        inserted = 0
        with self._tx() as conn:
            for row in rows:
                data = dict(row)
                values = []
                for c in cols:
                    if c == "time_str":
                        values.append(data.get(c) or get_cst_now_str(data.get("timestamp") or time.time()))
                    elif c in ("started_at", "ended_at"):
                        values.append(data.get(c) if data.get(c) is not None else data.get("timestamp"))
                    elif c == "visibility":
                        values.append(data.get(c) or "private")
                    else:
                        values.append(data.get(c))
                cur = conn.execute(sql, tuple(values))
                inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return inserted
