# -*- coding: utf-8 -*-
"""
Stellaris Core - SQLite 存储实现
所有 SQL 只出现在这里；业务层通过 repositories.Store 接口访问。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..models import TimelineEvent, get_cst_now_str
from .config import default_db_path
from .repositories import Store
from .schema import apply_migrations


def _json(value: Any, default: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return default


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


class SQLiteStore(Store):
    """SQLite 后端：默认实现，文件即边界，可随时替换为 Postgres 适配器"""

    def __init__(self, path: Optional[Path] = None, *, read_only: bool = False, migrate: bool = True):
        self.path = Path(path) if path else default_db_path()
        self.read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if migrate:
                apply_migrations(self.path)
        elif not self.path.exists():
            raise FileNotFoundError(f"storage not found: {self.path}")

    # ------------------------------------------------------------------
    # 连接与事务
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=15.0)
        else:
            conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        # 连接按需创建，无需常驻连接池；保留接口供未来后端使用
        return None

    # ---- 兼容旧 StorageDAL 的少量用法（新代码请勿依赖） ----
    @property
    def db_path(self) -> Path:
        return self.path

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def schema_version(self) -> int:
        with self._connect() as conn:
            try:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                return int(row[0] or 0) if row else 0
            except sqlite3.OperationalError:
                return 0

    # ------------------------------------------------------------------
    # 时间轴
    # ------------------------------------------------------------------
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
                 plugin_id, tag_primary, tag_secondary, segment_id, started_at, ended_at, visibility, detail_id,
                 person_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, now_ts, get_cst_now_str(now_ts), event.source_module, event.actor_id,
                    event.actor_name, event.narrative, event.emotional_impact, event.importance,
                    prev_id, event.causal_relation, _json(meta, "{}"), event.embedding,
                    event.plugin_id, event.tag_primary, event.tag_secondary, event.segment_id,
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
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_timeline {where} ORDER BY timestamp DESC LIMIT ?", tuple(params)
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

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
            "metadata", "embedding", "plugin_id", "tag_primary", "tag_secondary", "segment_id",
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

    # ------------------------------------------------------------------
    # 用户画像 / 好感度
    # ------------------------------------------------------------------
    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        profile = dict(row)
        profile["facts"] = _loads(profile.get("facts"), [])
        profile["affection_history"] = _loads(profile.get("affection_history"), [])
        return profile

    def insert_profile(
        self,
        user_id: str,
        nickname: str,
        role_type: str,
        facts: List[str],
        affection: int,
        affection_history: List[Dict[str, Any]],
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, nickname, role_type, _json(facts, "[]"), time.time(),
                    affection, _json(affection_history, "[]"),
                ),
            )

    def set_profile_facts(self, user_id: str, facts: List[str]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE user_profiles SET facts = ?, updated_at = ? WHERE user_id = ?",
                (_json(facts, "[]"), time.time(), user_id),
            )

    def get_affection(self, user_id: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute("SELECT affection FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["affection"] is not None:
            return int(row["affection"])
        return None

    def update_affection(
        self,
        user_id: str,
        delta: int,
        reason: str = "",
        default: int = 50,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> int:
        """按档案的浮动窗口更新好感度。

        窗口来源优先级：显式传参 > archives.affection_min/max > 默认 0~100。
        到边界后 delta 无效，但历史仍记一条，方便观察"想加但加不动"。
        """
        now_ts = time.time()
        with self._tx() as conn:
            if min_value is None or max_value is None:
                arch = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (user_id,),
                ).fetchone()
                if arch:
                    if min_value is None:
                        min_value = int(arch["affection_min"] if arch["affection_min"] is not None else 0)
                    if max_value is None:
                        max_value = int(arch["affection_max"] if arch["affection_max"] is not None else 100)
                else:
                    if min_value is None:
                        min_value = 0
                    if max_value is None:
                        max_value = 100
            lo, hi = int(min_value), int(max_value)
            if lo > hi:
                lo, hi = hi, lo

            row = conn.execute(
                "SELECT affection, affection_history FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            current = int(row["affection"]) if row and row["affection"] is not None else default
            history = _loads(row["affection_history"], []) if row else []
            new_value = min(hi, max(lo, current + int(delta)))
            history.append({
                "time": now_ts,
                "delta": int(delta),
                "old": current,
                "new": new_value,
                "reason": reason,
                "range": [lo, hi],
            })
            history = history[-50:]
            if row:
                conn.execute(
                    "UPDATE user_profiles SET affection = ?, affection_history = ?, updated_at = ? WHERE user_id = ?",
                    (new_value, _json(history, "[]"), now_ts, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                    VALUES (?, ?, 'member', '[]', ?, ?, ?)
                    """,
                    (user_id, f"用户_{user_id}", now_ts, new_value, _json(history, "[]")),
                )
            return new_value

    def set_affection(
        self,
        user_id: str,
        value: int,
        reason: str = "",
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> int:
        """直接设定好感度（不累加），clamp 到档案窗口。"""
        now_ts = time.time()
        with self._tx() as conn:
            if min_value is None or max_value is None:
                arch = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (user_id,),
                ).fetchone()
                if arch:
                    if min_value is None:
                        min_value = int(arch["affection_min"] if arch["affection_min"] is not None else 0)
                    if max_value is None:
                        max_value = int(arch["affection_max"] if arch["affection_max"] is not None else 100)
                else:
                    if min_value is None:
                        min_value = 0
                    if max_value is None:
                        max_value = 100
            lo, hi = int(min_value), int(max_value)
            if lo > hi:
                lo, hi = hi, lo

            row = conn.execute(
                "SELECT affection, affection_history FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current = int(row["affection"]) if row and row["affection"] is not None else None
            history = _loads(row["affection_history"], []) if row else []
            new_value = min(hi, max(lo, int(value)))
            history.append({
                "time": now_ts,
                "delta": (new_value - current) if current is not None else None,
                "old": current,
                "new": new_value,
                "reason": reason,
                "range": [lo, hi],
            })
            history = history[-50:]
            if row:
                conn.execute(
                    "UPDATE user_profiles SET affection = ?, affection_history = ?, updated_at = ? WHERE user_id = ?",
                    (new_value, _json(history, "[]"), now_ts, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                    VALUES (?, ?, 'member', '[]', ?, ?, ?)
                    """,
                    (user_id, f"用户_{user_id}", now_ts, new_value, _json(history, "[]")),
                )
            return new_value

    # ------------------------------------------------------------------
    # 语义事实
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 反思 / 日记
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 三层记忆：L1 调用索引 / L3 详情
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 档案层（身份映射）
    # ------------------------------------------------------------------
    def ensure_archive(
        self,
        archive_id: str,
        role: str = "user",
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        # 档案是人物，不再承载 AI 人设；旧列为兼容保留，永远写空
        persona = ""
        sections_json = "[]"
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO archives
                (archive_id, role, display_name, status, created_at, updated_at, metadata, persona_id, persona_sections)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    archive_id, role, display_name or archive_id, now, now,
                    _json(metadata or {}, "{}"), persona, sections_json,
                ),
            )

    def get_archive(self, archive_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM archives WHERE archive_id = ?", (archive_id,)).fetchone()
        return dict(row) if row else None

    def list_archives(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if role:
                rows = conn.execute(
                    "SELECT * FROM archives WHERE role = ? ORDER BY created_at ASC", (role,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM archives ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def link_identity(
        self,
        archive_id: str,
        platform: str,
        platform_user_id: str,
        *,
        display_name: Optional[str] = None,
        is_primary: bool = False,
        confidence: float = 1.0,
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT identity_id FROM archive_identities WHERE platform = ? AND platform_user_id = ?",
                (platform, str(platform_user_id)),
            ).fetchone()
            if existing:
                return existing["identity_id"]
            identity_id = f"id_{platform}_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO archive_identities
                (identity_id, archive_id, platform, platform_user_id, display_name,
                 is_primary, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_id, archive_id, platform, str(platform_user_id),
                    display_name, 1 if is_primary else 0, float(confidence), now,
                ),
            )
        return identity_id

    def find_archive_by_identity(self, platform: str, platform_user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.* FROM archives a
                JOIN archive_identities i ON i.archive_id = a.archive_id
                WHERE i.platform = ? AND i.platform_user_id = ?
                LIMIT 1
                """,
                (platform, str(platform_user_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_identities(self, archive_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archive_identities WHERE archive_id = ? ORDER BY created_at ASC",
                (archive_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_archive(
        self,
        archive_id: str,
        *,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
        affection_min: Optional[int] = None,
        affection_max: Optional[int] = None,
    ) -> None:
        fields: List[str] = []
        params: List[Any] = []
        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if affection_min is not None or affection_max is not None:
            # 读取当前窗口，合并后统一校验
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (archive_id,),
                ).fetchone()
            cur_min = int(row["affection_min"]) if row and row["affection_min"] is not None else 0
            cur_max = int(row["affection_max"]) if row and row["affection_max"] is not None else 100
            lo = int(affection_min) if affection_min is not None else cur_min
            hi = int(affection_max) if affection_max is not None else cur_max
            lo = max(0, min(100, lo))
            hi = max(0, min(100, hi))
            if lo > hi:
                raise ValueError("affection_min 不能大于 affection_max")
            fields.append("affection_min = ?")
            params.append(lo)
            fields.append("affection_max = ?")
            params.append(hi)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(time.time())
        params.append(archive_id)
        with self._tx() as conn:
            conn.execute(f"UPDATE archives SET {', '.join(fields)} WHERE archive_id = ?", tuple(params))

    # ------------------------------------------------------------------
    # 自定义设定（用户手填，AI 不可改）
    # ------------------------------------------------------------------
    def list_archive_settings(self, archive_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archive_settings WHERE archive_id = ? "
                "ORDER BY sort_order ASC, created_at ASC",
                (archive_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_archive_setting(
        self,
        archive_id: str,
        label: str,
        content: str,
        *,
        setting_id: str = "",
        sort_order: Optional[int] = None,
    ) -> Dict[str, Any]:
        label = str(label or "").strip()
        content = str(content or "").strip()
        if not label:
            raise ValueError("label 不能为空")
        now = time.time()
        with self._tx() as conn:
            existing = None
            if setting_id:
                existing = conn.execute(
                    "SELECT * FROM archive_settings WHERE setting_id = ? AND archive_id = ?",
                    (setting_id, archive_id),
                ).fetchone()
            if existing:
                order = int(existing["sort_order"] or 0) if sort_order is None else int(sort_order)
                conn.execute(
                    "UPDATE archive_settings SET label = ?, content = ?, sort_order = ?, updated_at = ? "
                    "WHERE setting_id = ?",
                    (label, content, order, now, setting_id),
                )
                sid = setting_id
            else:
                sid = setting_id or f"set_{uuid.uuid4().hex[:10]}"
                if sort_order is None:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS nxt FROM archive_settings WHERE archive_id = ?",
                        (archive_id,),
                    ).fetchone()
                    order = int(row["nxt"] or 1)
                else:
                    order = int(sort_order)
                conn.execute(
                    "INSERT INTO archive_settings "
                    "(setting_id, archive_id, label, content, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, archive_id, label, content, order, now, now),
                )
        return {
            "setting_id": sid, "archive_id": archive_id, "label": label,
            "content": content, "sort_order": order, "created_at": now, "updated_at": now,
        }

    def delete_archive_setting(self, archive_id: str, setting_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM archive_settings WHERE setting_id = ? AND archive_id = ?",
                (setting_id, archive_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 平台发现：外部上下文台账 + 人设绑定
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 插件注册表
    # ------------------------------------------------------------------
    def upsert_plugin(
        self,
        plugin_id: str,
        *,
        name: str = "",
        root_path: str = "",
        protocol: str = "",
        spec: Optional[Dict[str, Any]] = None,
        status: str = "active",
        source: str = "",
        last_error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT plugin_id FROM plugins WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE plugins
                       SET name = COALESCE(NULLIF(?, ''), name),
                           root_path = COALESCE(NULLIF(?, ''), root_path),
                           protocol = COALESCE(NULLIF(?, ''), protocol),
                           spec_json = ?, status = ?, source = ?,
                           last_error = ?, metadata = ?, updated_at = ?
                     WHERE plugin_id = ?
                    """,
                    (
                        name, root_path, protocol, _json(spec or {}, "{}"),
                        status, source, last_error, _json(metadata or {}, "{}"),
                        now, plugin_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO plugins
                    (plugin_id, name, root_path, protocol, spec_json, status,
                     source, last_error, metadata, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plugin_id, name or plugin_id, root_path, protocol,
                        _json(spec or {}, "{}"), status, source, last_error,
                        _json(metadata or {}, "{}"), now, now,
                    ),
                )
        return plugin_id

    def list_plugins(
        self,
        *,
        protocol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conditions: List[str] = []
        params: List[Any] = []
        if protocol:
            conditions.append("protocol = ?")
            params.append(protocol)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM plugins {where} ORDER BY updated_at DESC",
                tuple(params),
            ).fetchall()
        return [self._plugin_row(r) for r in rows]

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plugins WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
        return self._plugin_row(row) if row else None

    def delete_plugin(self, plugin_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM plugins WHERE plugin_id = ?", (plugin_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    @staticmethod
    def _plugin_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["spec"] = _loads(data.pop("spec_json", "{}"), {})
        data["metadata"] = _loads(data.get("metadata"), {})
        return data

    # ------------------------------------------------------------------
    # 桥接层：事件去重 + 游标
    # ------------------------------------------------------------------
    def mark_ingested(self, event_key: str, source: str = "", event_id: str = "") -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO ingested_events (event_key, source, event_id, ingested_at) "
                "VALUES (?, ?, ?, ?)",
                (event_key, source, event_id, time.time()),
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    def get_bridge_cursor(self, source_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM bridge_cursors WHERE source_id = ?", (source_id,)
            ).fetchone()
        return row["cursor"] if row else None

    def set_bridge_cursor(self, source_id: str, cursor: str) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO bridge_cursors (source_id, cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (source_id, str(cursor), time.time()),
            )

    # ------------------------------------------------------------------
    # 桥 spec 持久化
    # ------------------------------------------------------------------
    def upsert_bridge_spec(
        self, spec: Dict[str, Any], *, auto_start: bool = False, status: str = "stopped"
    ) -> str:
        source_id = str(spec.get("source_id") or "")
        if not source_id:
            raise ValueError("source_id 必填")
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO bridge_specs
                (source_id, kind, scope, locator, options_json, status, auto_start, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    scope = excluded.scope,
                    locator = excluded.locator,
                    options_json = excluded.options_json,
                    status = excluded.status,
                    auto_start = excluded.auto_start,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id, str(spec.get("kind") or ""), str(spec.get("scope") or ""),
                    str(spec.get("locator") or ""), _json(spec.get("options") or {}, "{}"),
                    status, 1 if auto_start else 0, now, now,
                ),
            )
        return source_id

    def list_bridge_specs(self, *, auto_start: Optional[bool] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if auto_start is None:
                rows = conn.execute("SELECT * FROM bridge_specs ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bridge_specs WHERE auto_start = ? ORDER BY updated_at DESC",
                    (1 if auto_start else 0,),
                ).fetchall()
        return [self._bridge_spec_row(r) for r in rows]

    def get_bridge_spec(self, source_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bridge_specs WHERE source_id = ?", (source_id,)
            ).fetchone()
        return self._bridge_spec_row(row) if row else None

    def delete_bridge_spec(self, source_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM bridge_specs WHERE source_id = ?", (source_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    def set_bridge_spec_status(
        self, source_id: str, status: str, *, auto_start: Optional[bool] = None
    ) -> None:
        with self._tx() as conn:
            if auto_start is None:
                conn.execute(
                    "UPDATE bridge_specs SET status = ?, updated_at = ? WHERE source_id = ?",
                    (status, time.time(), source_id),
                )
            else:
                conn.execute(
                    "UPDATE bridge_specs SET status = ?, auto_start = ?, updated_at = ? WHERE source_id = ?",
                    (status, 1 if auto_start else 0, time.time(), source_id),
                )

    @staticmethod
    def _bridge_spec_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["options"] = _loads(data.pop("options_json", "{}"), {})
        data["auto_start"] = bool(data.get("auto_start"))
        return data

    # ------------------------------------------------------------------
    # 直连服务注册表
    # ------------------------------------------------------------------
    def upsert_service(
        self,
        service_id: str,
        *,
        name: str = "",
        kind: str = "",
        endpoint: str = "",
        auth_token: str = "",
        capabilities: Optional[List[str]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        status: str = "active",
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO services
                (service_id, name, kind, endpoint, auth_token, capabilities,
                 manifest, status, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), name),
                    kind = COALESCE(NULLIF(excluded.kind, ''), kind),
                    endpoint = COALESCE(NULLIF(excluded.endpoint, ''), endpoint),
                    auth_token = COALESCE(NULLIF(excluded.auth_token, ''), auth_token),
                    capabilities = excluded.capabilities,
                    manifest = excluded.manifest,
                    status = excluded.status,
                    last_seen = excluded.last_seen,
                    updated_at = excluded.updated_at
                """,
                (
                    service_id, name, kind, endpoint, auth_token,
                    _json(capabilities or [], "[]"), _json(manifest or {}, "{}"),
                    status, now, now, now,
                ),
            )
        return service_id

    def list_services(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM services WHERE status = ? ORDER BY updated_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM services ORDER BY updated_at DESC").fetchall()
        return [self._service_row(r) for r in rows]

    def get_service(self, service_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM services WHERE service_id = ?", (service_id,)
            ).fetchone()
        return self._service_row(row) if row else None

    def delete_service(self, service_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM services WHERE service_id = ?", (service_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    def touch_service(self, service_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE services SET last_seen = ?, updated_at = ? WHERE service_id = ?",
                (time.time(), time.time(), service_id),
            )

    @staticmethod
    def _service_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["capabilities"] = _loads(data.get("capabilities"), [])
        data["manifest"] = _loads(data.get("manifest"), {})
        return data

    # ------------------------------------------------------------------
    # 统一知识库
    # ------------------------------------------------------------------
    def add_knowledge_source(
        self, source_id: str, title: str, source: str = "", tags: Optional[List[str]] = None
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_sources
                (source_id, title, source, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    tags = excluded.tags,
                    updated_at = excluded.updated_at
                """,
                (source_id, title, source, _json(tags or [], "[]"), now, now),
            )

    def add_knowledge_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks:
            return
        with self._tx() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO knowledge_chunks
                (chunk_id, source_id, title, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        c["chunk_id"], c["source_id"], c.get("title", ""),
                        c["content"], c.get("created_at") or time.time(),
                    )
                    for c in chunks
                ],
            )

    def list_knowledge_sources(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_sources ORDER BY updated_at DESC"
            ).fetchall()
            counts = {
                r["source_id"]: r["cnt"]
                for r in conn.execute(
                    "SELECT source_id, COUNT(*) AS cnt FROM knowledge_chunks GROUP BY source_id"
                )
            }
        result = []
        for row in rows:
            data = dict(row)
            data["tags"] = _loads(data.get("tags"), [])
            data["chunks"] = int(counts.get(data["source_id"], 0))
            result.append(data)
        return result

    def search_knowledge(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        raw_terms = [
            t for t in re.split(r"[\s，。！？、,.!?：:；;]+", str(query or "").strip())
            if t
        ]
        terms: List[str] = []
        for term in raw_terms:
            if len(term) > 6:
                # 中文没有空格，用 4 字滑窗提高“共享暗号”这类短语命中
                terms.extend(term[i:i + 4] for i in range(0, len(term) - 3, 2))
            else:
                terms.append(term)
        terms = list(dict.fromkeys(terms))[:20]
        if not terms:
            return []
        clauses = []
        params: List[Any] = []
        for term in terms:
            clauses.append("(content LIKE ? OR title LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM knowledge_chunks WHERE {' OR '.join(clauses)} "
                "ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        results = [dict(r) for r in rows]
        for item in results:
            text = str(item.get("content") or "")
            item["score"] = sum(text.count(t) for t in terms) + (2 if any(t in str(item.get("title") or "") for t in terms) else 0)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def delete_knowledge_source(self, source_id: str) -> bool:
        with self._tx() as conn:
            conn.execute("DELETE FROM knowledge_chunks WHERE source_id = ?", (source_id,))
            cur = conn.execute("DELETE FROM knowledge_sources WHERE source_id = ?", (source_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    # ------------------------------------------------------------------
    # 宿主内部二级插件
    # ------------------------------------------------------------------
    def upsert_component(
        self,
        component_id: str,
        host_id: str,
        name: str,
        *,
        version: str = "",
        kind: str = "secondary",
        path: str = "",
        entry: str = "",
        config_schema: str = "",
        category: str = "二级插件",
        capabilities: Optional[List[str]] = None,
        status: str = "active",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
        fingerprint: str = "",
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO components
                (component_id, host_id, name, version, kind, path, entry,
                 config_schema, category, capabilities, status, source, manifest,
                 fingerprint, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component_id) DO UPDATE SET
                    host_id = excluded.host_id,
                    name = excluded.name,
                    version = excluded.version,
                    kind = excluded.kind,
                    path = excluded.path,
                    entry = excluded.entry,
                    config_schema = excluded.config_schema,
                    category = excluded.category,
                    capabilities = excluded.capabilities,
                    status = excluded.status,
                    source = excluded.source,
                    manifest = excluded.manifest,
                    fingerprint = excluded.fingerprint,
                    last_seen = excluded.last_seen,
                    removed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    component_id, host_id, name, version, kind, path, entry,
                    config_schema, category, _json(capabilities or [], "[]"),
                    status, source, _json(manifest or {}, "{}"), fingerprint, now, now, now,
                ),
            )

    def list_components(self, host_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM components"
        params: List[Any] = []
        if host_id:
            sql += " WHERE host_id = ?"
            params.append(host_id)
        sql += " ORDER BY host_id, name"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["capabilities"] = _loads(data.get("capabilities"), [])
            data["manifest"] = _loads(data.get("manifest"), {})
            result.append(data)
        return result

    def delete_component(self, component_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM components WHERE component_id = ?", (component_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    # ------------------------------------------------------------------
    # 宿主身份
    # ------------------------------------------------------------------
    def upsert_host(
        self,
        host_id: str,
        name: str,
        *,
        platform: str = "",
        internal_uid: str = "",
        root: str = "",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO hosts
                (host_id, name, platform, internal_uid, root, source, manifest, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host_id) DO UPDATE SET
                    name = excluded.name,
                    platform = excluded.platform,
                    internal_uid = excluded.internal_uid,
                    root = excluded.root,
                    source = excluded.source,
                    manifest = excluded.manifest,
                    updated_at = excluded.updated_at
                """,
                (
                    host_id, name, platform, internal_uid, root, source,
                    _json(manifest or {}, "{}"), now, now,
                ),
            )

    def list_hosts(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM hosts ORDER BY updated_at DESC").fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["manifest"] = _loads(data.get("manifest"), {})
            result.append(data)
        return result

    # ------------------------------------------------------------------
    # Token 账本
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 插件活动（暂存区）
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 会话段（复杂事件聚合）
    # ------------------------------------------------------------------
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
