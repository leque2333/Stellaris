# -*- coding: utf-8 -*-
"""事实与摘要：原子事实、双时间线、日摘要。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from ...models import TimelineEvent
from ..jsonutil import _json, _loads

# 事实里的常见动词前缀：用来判断两条事实是不是在说同一个"轴"
_FACT_VERBS = (
    "喜欢", "讨厌", "使用", "住在", "养了", "叫", "是", "在", "有", "会",
    "答应", "承诺", "生日", "年龄", "工作", "习惯",
)


class FactOps:
    """事实与摘要：原子事实、双时间线、日摘要。"""

    # 泛化分类不做自动覆盖：这类桶里装的是互不排斥的多条事实
    GENERIC_CATEGORIES = {"preference", "偏好", "事实", "general", "other", ""}
    # 低于这个置信度的事实进 pending，等人确认后才参与召回
    ACTIVE_CONFIDENCE = 0.6

    @classmethod
    def _conflict_key(cls, category: str, content: str) -> str:
        """把「同义反复」收敛成同一个键：同 category + 同前缀动词才算冲突。

        - 喜欢猫 / 喜欢狗   → 同一轴，后者覆盖前者
        - 喜欢猫 / 讨厌狗   → 不同轴，各留各的（不会互相覆盖）
        - preference 这类泛化桶 → 永远不覆盖
        """
        cat = str(category or "").strip()
        if cat.lower() in cls.GENERIC_CATEGORIES:
            return ""
        text = str(content or "").strip()
        for verb in _FACT_VERBS:
            if text.startswith(verb):
                return f"{cat}:{verb}"
        return cat

    def add_fact(
        self,
        user_id: str,
        content: str,
        category: str = "preference",
        supersedes_id: Optional[str] = None,
        source: str = "manual",
        confidence: float = 1.0,
        status: Optional[str] = None,
    ) -> str:
        """写一条事实。

        source: ai_extract（AI 从对话总结）/ manual（人工填写）/ legacy（历史迁移）

        写入即维护时效：同 conflict key 的旧 active 事实自动 superseded，
        低置信度事实写 pending，等人工确认后才升 active。
        """
        text = str(content or "").strip()
        if not text:
            return ""
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 1.0
        status = status or ("active" if confidence >= self.ACTIVE_CONFIDENCE else "pending")
        now_ts = time.time()
        fact_id = f"fact_{int(now_ts * 1000)}_{uuid.uuid4().hex[:6]}"
        with self._tx() as conn:
            if status == "active":
                duplicate = conn.execute(
                    "SELECT fact_id FROM user_facts WHERE user_id = ? AND category = ? "
                    "AND content = ? AND status = 'active' LIMIT 1",
                    (user_id, category, text),
                ).fetchone()
                if duplicate:
                    return str(duplicate["fact_id"])
                supersedes_id = supersedes_id or self._retire_conflicts(
                    conn, user_id, category, text, now_ts,
                )
            conn.execute(
                """
                INSERT INTO user_facts
                (fact_id, user_id, category, content, status, supersedes_id,
                 created_at, updated_at, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fact_id, user_id, category, text, status, supersedes_id,
                 now_ts, now_ts, source, confidence),
            )
        return fact_id

    def _retire_conflicts(self, conn, user_id: str, category: str, content: str, now_ts: float) -> Optional[str]:
        """把同一冲突轴上仍 active 的旧事实退役，返回被覆盖的 id。"""
        key = self._conflict_key(category, content)
        if not key:
            return None
        rows = conn.execute(
            "SELECT fact_id, category, content FROM user_facts "
            "WHERE user_id = ? AND category = ? AND status = 'active' "
            "ORDER BY updated_at DESC",
            (user_id, category),
        ).fetchall()
        retired = None
        for row in rows:
            if str(row["content"] or "").strip() == content:
                continue
            if self._conflict_key(row["category"], row["content"]) != key:
                continue
            conn.execute(
                "UPDATE user_facts SET status = 'superseded', updated_at = ?, superseded_at = ? "
                "WHERE fact_id = ?",
                (now_ts, now_ts, row["fact_id"]),
            )
            retired = retired or str(row["fact_id"])
        return retired


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
