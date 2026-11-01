# -*- coding: utf-8 -*-
"""长期事实层：把人说过的事实存成可查询、可失效的记录。

参考 Graphiti 的 episode → entity/fact + 双时间模型，但先落在 SQLite。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..storage import get_store
from .graph_store import GraphStore

_CATEGORY_LABELS = {
    "preference": "偏好",
    "identity": "身份",
    "relationship": "关系",
    "plan": "计划",
}

# 事实是整句，关系视图要从句子里抽出「谓词 + 客体」，不能整句照搬
_RELATION_RULES = (
    (re.compile(r"^是(?P<obj>.+?)的(?:唯一)?(?:主人|管理员)"), "主人"),
    (re.compile(r"^喜欢(?P<obj>[^，。]+)"), "喜欢"),
    (re.compile(r"^讨厌(?P<obj>[^，。]+)"), "讨厌"),
    (re.compile(r"^平时?使用(?P<obj>[^，。]+)"), "使用"),
    (re.compile(r"^住在(?P<obj>[^，。]+)"), "住在"),
    (re.compile(r"^养了(?P<obj>[^，。]+)"), "养了"),
    (re.compile(r"^答应(?:给)?(?P<obj>[^，。]+?)(?:作为|，|$)"), "承诺给"),
)


def _relation_of(predicate: str, value: str):
    """从一条事实里抽关系；抽不到就退回 category + 原文。"""
    text = str(value or "").strip()
    for pattern, label in _RELATION_RULES:
        match = pattern.search(text)
        if match:
            return label, _clean(match.group("obj"))
    if predicate and predicate not in ("偏好", "preference", "事实"):
        return predicate, text
    return predicate or "事实", text


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n，。；;：:")


def extract_facts(text: str) -> List[Dict[str, str]]:
    """从用户原话里抽取确定性事实；抽取不到就返回空。"""
    text = str(text or "").strip()
    if not text:
        return []
    facts: List[Dict[str, str]] = []

    # 记住一个测试暗号：琥珀-5521
    for match in re.finditer(
        r"记住(?:一下|一个|一条)?\s*(?P<predicate>[^：:，。\n]{1,24})\s*[：:]\s*(?P<value>[^，。\n]{1,80})",
        text,
    ):
        predicate = _clean(match.group("predicate"))
        predicate = re.sub(r"^(一个|一条|一下)", "", predicate).strip()
        value = _clean(match.group("value"))
        if predicate and value:
            facts.append({"predicate": predicate, "value": value})

    # 我的测试暗号是琥珀-5521
    for match in re.finditer(
        r"我的(?P<predicate>[^，。；;：:\n]{1,24})是\s*(?P<value>[^，。；;\n]{1,80})",
        text,
    ):
        predicate = _clean(match.group("predicate"))
        value = _clean(match.group("value"))
        if predicate and value:
            facts.append({"predicate": predicate, "value": value})

    # 我叫 <名字>
    for match in re.finditer(r"我叫\s*(?P<value>[^，。；;\n]{1,30})", text):
        value = _clean(match.group("value"))
        if value:
            facts.append({"predicate": "名字", "value": value})

    # 我喜欢/我讨厌
    for match in re.finditer(r"我(?P<verb>喜欢|讨厌)\s*(?P<value>[^，。；;\n]{1,50})", text):
        value = _clean(match.group("value"))
        if value:
            facts.append({"predicate": "喜好", "value": f"{match.group('verb')}{value}"})

    deduped = []
    seen = set()
    for item in facts:
        key = (item["predicate"], item["value"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


_FACT_CUES = (
    "我", "我的", "记住", "喜欢", "讨厌", "生日", "住在", "工作",
    "习惯", "计划", "养了", "名字", "叫我",
)


def _parse_json_array(text: str) -> List[Dict[str, str]]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    items = []
    for item in data:
        if not isinstance(item, dict):
            continue
        predicate = _clean(item.get("predicate") or "")
        value = _clean(item.get("value") or "")
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 0.5
        if predicate and value and len(predicate) <= 30 and len(value) <= 120:
            items.append({
                "predicate": predicate,
                "value": value,
                "confidence": max(0.0, min(1.0, confidence)),
            })
    return items


_last_ai_extract = 0.0
_ai_extract_lock = threading.Lock()


def extract_facts_ai(text: str, usage_cb=None) -> List[Dict[str, str]]:
    """AI 抽取稳定事实；失败/关闭时返回空。"""
    global _last_ai_extract
    if os.getenv("HUB_AI_FACT_EXTRACTION", "1") in ("0", "false", "False"):
        return []
    text = str(text or "").strip()
    if len(text) < 4 or not any(cue in text for cue in _FACT_CUES):
        return []
    try:
        min_interval = float(os.getenv("HUB_AI_FACT_EXTRACTION_MIN_INTERVAL", "3"))
    except ValueError:
        min_interval = 3.0
    now = time.time()
    with _ai_extract_lock:
        if now - _last_ai_extract < min_interval:
            return []
        _last_ai_extract = now
    try:
        from ..compute_providers import compute_manager

        messages = [
            {
                "role": "system",
                "content": (
                    "你是记忆事实抽取器。只抽取用户本人稳定、长期有效的事实，"
                    "不要抽临时情绪、一次性动作、猜测。只输出 JSON 数组，"
                    '格式 [{"predicate":"事实名","value":"事实值","confidence":0.0到1.0}]；没有就输出 []。'
                ),
            },
            {"role": "user", "content": text},
        ]
        slot = os.getenv("HUB_FACT_AI_SLOT", "logic_slot")
        out, p_in, p_out, used_model = compute_manager.chat_completion_meta(
            slot, messages, timeout=30,
        )
        if usage_cb:
            try:
                usage_cb(int(p_in or 0), int(p_out or 0), used_model or slot)
            except TypeError:  # 兼容只收两个参数的旧回调
                usage_cb(int(p_in or 0), int(p_out or 0))
            except Exception:
                pass
        return _parse_json_array(out)
    except Exception:
        return []


def _tokenize(text: str) -> str:
    try:
        import jieba
        return " ".join(jieba.cut_for_search(str(text or "")))
    except Exception:
        # 无 jieba 时退化为单字/空格切分
        return " ".join(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", str(text or "")))


_READY_DBS: set = set()
_READY_LOCK = threading.Lock()


class FactStore:
    def __init__(self, store: Any = None) -> None:
        self.store = store or get_store()
        # 建表/建索引/建图库只对每个库做一次：以前每次 /v1 都跑一遍 DDL，
        # 写锁被反复抢占（审计 F22）。这里只记「已就绪」，不共享 FactStore 实例。
        key = str(getattr(self.store, "path", "") or id(self.store))
        with _READY_LOCK:
            first_init = key not in _READY_DBS
            _READY_DBS.add(key)
        if first_init:
            self._ensure_schema()
            self._ensure_fts()
            self._ensure_tokens()
        graph_path = None
        raw_graph = os.getenv("HUB_GRAPH_DB_PATH", "").strip()
        if raw_graph:
            graph_path = Path(raw_graph)
        elif getattr(self.store, "path", None):
            graph_path = Path(self.store.path).parent / "memory_graph.db"
        self.graph_store = GraphStore(graph_path)
        if first_init:
            self._sync_graph()

    def _sync_graph(self) -> None:
        """图库为空时，用**现行事实表**补齐（user_facts）。

        历史实现只读 memory_facts，而事实写入早已统一到 user_facts，
        于是内存图永远是空的（审计 B13）。
        """
        try:
            if self.graph_store.counts().get("edges", 0):
                return
            with self.store._connect() as conn:
                rows = conn.execute(
                    "SELECT fact_id, user_id AS subject_id, category AS predicate, "
                    "content AS value, status, updated_at AS valid_at, '' AS source_event_id "
                    "FROM user_facts WHERE status = 'active' ORDER BY updated_at"
                ).fetchall()
            for row in rows:
                self.graph_store.index_fact(dict(row))
        except Exception:
            pass

    def _ensure_fts(self) -> None:
        """给时间轴补 FTS5 trigram 索引，支持中文子串检索。"""
        try:
            with self.store._tx() as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS timeline_fts USING fts5("
                    "narrative, content='agent_timeline', content_rowid='rowid', "
                    "tokenize='trigram')"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_fts_ai AFTER INSERT ON agent_timeline BEGIN "
                    "INSERT INTO timeline_fts(rowid, narrative) VALUES (new.rowid, new.narrative); END"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_fts_ad AFTER DELETE ON agent_timeline BEGIN "
                    "INSERT INTO timeline_fts(timeline_fts, rowid, narrative) VALUES ('delete', old.rowid, old.narrative); END"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_fts_au AFTER UPDATE ON agent_timeline BEGIN "
                    "INSERT INTO timeline_fts(timeline_fts, rowid, narrative) VALUES ('delete', old.rowid, old.narrative); "
                    "INSERT INTO timeline_fts(rowid, narrative) VALUES (new.rowid, new.narrative); END"
                )
                count = conn.execute("SELECT count(*) FROM timeline_fts").fetchone()[0]
                if not count:
                    conn.execute("INSERT INTO timeline_fts(timeline_fts) VALUES ('rebuild')")
        except Exception:
            # 某些 SQLite 构建不带 FTS5/trigram；事实层仍可独立工作
            pass

    def _ensure_tokens(self) -> None:
        """jieba 分词 + FTS5 unicode61：解决中文子串/分词退化。"""
        try:
            with self.store._tx() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS timeline_tokens ("
                    "event_id TEXT PRIMARY KEY, tokens TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS timeline_tokens_fts USING fts5("
                    "tokens, content='timeline_tokens', content_rowid='rowid')"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_tokens_ai AFTER INSERT ON timeline_tokens BEGIN "
                    "INSERT INTO timeline_tokens_fts(rowid, tokens) VALUES (new.rowid, new.tokens); END"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_tokens_ad AFTER DELETE ON timeline_tokens BEGIN "
                    "INSERT INTO timeline_tokens_fts(timeline_tokens_fts, rowid, tokens) VALUES ('delete', old.rowid, old.tokens); END"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS timeline_tokens_au AFTER UPDATE ON timeline_tokens BEGIN "
                    "INSERT INTO timeline_tokens_fts(timeline_tokens_fts, rowid, tokens) VALUES ('delete', old.rowid, old.tokens); "
                    "INSERT INTO timeline_tokens_fts(rowid, tokens) VALUES (new.rowid, new.tokens); END"
                )
                missing = conn.execute(
                    "SELECT t.event_id, t.narrative FROM agent_timeline t "
                    "LEFT JOIN timeline_tokens k ON k.event_id = t.event_id "
                    "WHERE k.event_id IS NULL LIMIT 500"
                ).fetchall()
                for row in missing:
                    conn.execute(
                        "INSERT INTO timeline_tokens (event_id, tokens) VALUES (?, ?)",
                        (row["event_id"], _tokenize(row["narrative"])),
                    )
        except Exception:
            pass

    def index_event(self, event_id: str, narrative: str) -> None:
        if not event_id:
            return
        try:
            with self.store._tx() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO timeline_tokens (event_id, tokens) VALUES (?, ?)",
                    (str(event_id), _tokenize(narrative)),
                )
        except Exception:
            pass

    def search_episodes(
        self,
        query: str,
        *,
        person_id: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        query = _clean(query)
        if len(query) < 2:
            return []
        # jieba 分词 + unicode61 FTS：中文查询按词命中
        terms = [token for token in _tokenize(query).split() if len(token) >= 1][:12]
        if terms:
            token_match = " OR ".join(
                '"' + token.replace('"', '""') + '"' for token in terms
            )
            token_clauses = ["timeline_tokens_fts MATCH ?"]
            token_params: List[Any] = [token_match]
            if person_id:
                token_clauses.append("t.person_id = ?")
                token_params.append(str(person_id))
            token_params.append(int(limit))
            try:
                with self.store._connect() as conn:
                    rows = conn.execute(
                        "SELECT t.event_id, t.time_str, t.narrative, t.person_id, t.actor_id "
                        "FROM timeline_tokens_fts f "
                        "JOIN timeline_tokens k ON k.rowid = f.rowid "
                        "JOIN agent_timeline t ON t.event_id = k.event_id "
                        f"WHERE {' AND '.join(token_clauses)} ORDER BY t.timestamp DESC LIMIT ?",
                        tuple(token_params),
                    ).fetchall()
                if rows:
                    return [dict(row) for row in rows]
            except Exception:
                pass
        match = '"' + query.replace('"', '""') + '"'
        clauses = ["timeline_fts MATCH ?"]
        params: List[Any] = [match]
        if person_id:
            clauses.append("t.person_id = ?")
            params.append(str(person_id))
        params.append(int(limit))
        sql = (
            "SELECT t.event_id, t.time_str, t.narrative, t.person_id, t.actor_id "
            "FROM timeline_fts f JOIN agent_timeline t ON t.rowid = f.rowid "
            f"WHERE {' AND '.join(clauses)} ORDER BY t.timestamp DESC LIMIT ?"
        )
        try:
            with self.store._connect() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
            if rows:
                return [dict(row) for row in rows]
        except Exception:
            rows = []
        # FTS5 tokenizer 在部分 SQLite 构建上对中文子串不稳定，用 LIKE 兜底
        like_clauses = ["narrative LIKE ?"]
        like_params: List[Any] = [f"%{query}%"]
        if person_id:
            like_clauses.append("person_id = ?")
            like_params.append(str(person_id))
        like_params.append(int(limit))
        try:
            with self.store._connect() as conn:
                rows = conn.execute(
                    "SELECT event_id, time_str, narrative, person_id, actor_id "
                    "FROM agent_timeline WHERE " + " AND ".join(like_clauses) +
                    " ORDER BY timestamp DESC LIMIT ?",
                    tuple(like_params),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def _ensure_schema(self) -> None:
        with self.store._tx() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_facts (
                    fact_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value TEXT NOT NULL,
                    valid_at REAL NOT NULL,
                    invalid_at REAL,
                    created_at REAL NOT NULL,
                    expired_at REAL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'active',
                    source_event_id TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_facts_subject "
                "ON memory_facts(subject_id, predicate, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_facts_active "
                "ON memory_facts(subject_id, status, valid_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    person_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(person_id, entity_type, name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_relations (
                    relation_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_value TEXT NOT NULL,
                    fact_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    valid_at REAL NOT NULL,
                    invalid_at REAL,
                    source_event_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_relations_subject "
                "ON memory_relations(subject_id, status)"
            )

    def _user_fact_view(self, row: Any, status: Optional[str] = None) -> Dict[str, Any]:
        """把 user_facts 行映射成旧 memory_facts 形状，兼容控制台字段。"""
        data = dict(row)
        created = float(data.get("created_at") or 0)
        updated = float(data.get("updated_at") or created)
        state = str(status or data.get("status") or "active")
        return {
            "fact_id": data.get("fact_id", ""),
            "subject_id": data.get("user_id", ""),
            "predicate": _CATEGORY_LABELS.get(
                str(data.get("category") or ""), data.get("category") or "事实"
            ),
            "value": data.get("content") or "",
            "valid_at": created,
            "invalid_at": None if state == "active" else updated,
            "created_at": created,
            "expired_at": None if state == "active" else updated,
            "confidence": 1.0,
            "status": state,
            "source_event_id": "",
            "metadata": {
                "source": data.get("source") or "",
                "supersedes_id": data.get("supersedes_id") or "",
            },
        }

    def _fetch_user_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_facts WHERE fact_id = ?", (str(fact_id),)
            ).fetchone()
        return self._user_fact_view(row) if row else None

    def add(
        self,
        subject_id: str,
        predicate: str,
        value: str,
        *,
        source_event_id: str = "",
        confidence: float = 1.0,
        valid_at: Optional[float] = None,
        require_confirmation: bool = False,
    ) -> Dict[str, Any]:
        subject_id = str(subject_id or "").strip()
        predicate = _clean(predicate)
        value = _clean(value)
        if not subject_id or not predicate or not value:
            return {}
        now = time.time()
        valid = float(valid_at or now)
        pending = float(confidence) < 0.65
        with self.store._tx() as conn:
            old = conn.execute(
                "SELECT * FROM user_facts WHERE user_id = ? AND category = ? "
                "AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (subject_id, predicate),
            ).fetchone()
            if old and _clean(old["content"]) == value:
                conn.execute(
                    "UPDATE user_facts SET updated_at = ? WHERE fact_id = ?",
                    (now, old["fact_id"]),
                )
                return self._user_fact_view(old)
            if old and require_confirmation:
                pending = True
            old_id = old["fact_id"] if old else ""
            if old and not pending:
                conn.execute(
                    "UPDATE user_facts SET status = 'superseded', updated_at = ? WHERE fact_id = ?",
                    (now, old["fact_id"]),
                )
            fact_id = f"fact_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO user_facts "
                "(fact_id, user_id, category, content, status, supersedes_id, created_at, updated_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fact_id, subject_id, predicate, value,
                    "pending" if pending else "active",
                    old_id or None, valid, now, "ai_extract",
                ),
            )
            row = conn.execute("SELECT * FROM user_facts WHERE fact_id = ?", (fact_id,)).fetchone()
        return self._user_fact_view(row)

    def graph(self, subject_id: str) -> Dict[str, Any]:
        """实体关系直接由当前生效事实生成，避免图库与事实表双写漂移。"""
        rows = self.list_facts(subject_id=subject_id, status="active")
        edges = []
        for item in rows:
            predicate, object_value = _relation_of(item["predicate"], item["value"])
            edges.append({
                "relation_id": "rel_" + item["fact_id"],
                "subject_id": item["subject_id"],
                "predicate": predicate,
                "object_value": object_value,
                "fact_id": item["fact_id"],
                "status": "active",
                "valid_at": item["valid_at"],
                "invalid_at": None,
                "source_event_id": "",
                "created_at": item["created_at"],
            })
        nodes = []
        if subject_id and rows:
            nodes.append({
                "entity_id": "person_" + str(subject_id),
                "entity_type": "person",
                "name": str(subject_id),
                "person_id": str(subject_id),
                "created_at": rows[-1]["created_at"],
                "updated_at": rows[0]["valid_at"],
            })
        return {"nodes": nodes, "edges": edges}

    def get(self, fact_id: str) -> Optional[Dict[str, Any]]:
        return self._fetch_user_fact(fact_id)

    def search(self, subject_id: str, query: str = "", limit: int = 12) -> List[Dict[str, Any]]:
        items = self.list_facts(subject_id=subject_id, status="active", limit=limit)
        query = _clean(query)
        if query:
            for item in items:
                text = f"{item['predicate']} {item['value']}"
                item["_score"] = 2 if query in text else 1
            items.sort(key=lambda item: (item["_score"], item["valid_at"]), reverse=True)
        return items

    def facts_at(self, subject_id: str, at_time: float) -> List[Dict[str, Any]]:
        """时间点查询：看当时有效的事实；user_facts 用 updated_at 作为失效时间。"""
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_facts WHERE user_id = ? AND created_at <= ? "
                "AND status != 'deleted' AND (status = 'active' OR updated_at > ?) "
                "ORDER BY created_at DESC",
                (str(subject_id or ""), float(at_time), float(at_time)),
            ).fetchall()
        return [self._user_fact_view(row, status="active") for row in rows]

    def list_facts(
        self,
        *,
        subject_id: str = "",
        status: str = "active",
        query: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        subject_id = str(subject_id or "").strip()
        if subject_id:
            clauses.append("user_id = ?")
            params.append(subject_id)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("(category LIKE ? OR content LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self.store._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM user_facts {where} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._user_fact_view(row) for row in rows]

    def update_value(self, fact_id: str, value: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        try:
            with self.store._tx() as conn:
                cur = conn.execute(
                    "UPDATE user_facts SET content = ?, status = 'active', updated_at = ? "
                    "WHERE fact_id = ?",
                    (_clean(value), now, str(fact_id)),
                )
                if not cur.rowcount:
                    return None
        except Exception:
            return None
        return self._fetch_user_fact(fact_id)

    def delete(self, fact_id: str) -> bool:
        with self.store._tx() as conn:
            cur = conn.execute(
                "UPDATE user_facts SET status = 'deleted', updated_at = ? WHERE fact_id = ?",
                (time.time(), str(fact_id)),
            )
        return bool(cur.rowcount)

    def rollback(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """把历史/待确认事实设回当前；同类别当前事实自动失效。"""
        fact = self._fetch_user_fact(fact_id)
        if not fact:
            return None
        now = time.time()
        with self.store._tx() as conn:
            conn.execute(
                "UPDATE user_facts SET status = 'superseded', updated_at = ? "
                "WHERE user_id = ? AND category = ? AND status = 'active' AND fact_id != ?",
                (now, fact["subject_id"], fact["predicate"], str(fact_id)),
            )
            conn.execute(
                "UPDATE user_facts SET status = 'active', updated_at = ? WHERE fact_id = ?",
                (now, str(fact_id)),
            )
        return self._fetch_user_fact(fact_id)

    def confirm(self, fact_id: str) -> Optional[Dict[str, Any]]:
        return self.rollback(fact_id)

    def reject(self, fact_id: str) -> bool:
        return self.delete(fact_id)

    def extract_and_store(
        self,
        subject_id: str,
        user_text: str,
        assistant_text: str = "",
        source_event_id: str = "",
    ) -> List[Dict[str, Any]]:
        stored = []
        for item in extract_facts(user_text):
            fact = self.add(
                subject_id,
                item["predicate"],
                item["value"],
                source_event_id=source_event_id,
            )
            if fact:
                stored.append(fact)
        return stored

    def extract_and_store_ai(
        self,
        subject_id: str,
        user_text: str,
        source_event_id: str = "",
        usage_cb=None,
    ) -> List[Dict[str, Any]]:
        stored = []
        for item in extract_facts_ai(user_text, usage_cb=usage_cb):
            fact = self.add(
                subject_id,
                item["predicate"],
                item["value"],
                source_event_id=source_event_id,
                confidence=float(item.get("confidence", 0.5)),
                require_confirmation=True,
            )
            if fact:
                stored.append(fact)
        return stored

    def store_extracted(
        self,
        subject_id: str,
        user_text: str,
        *,
        use_ai: bool = False,
        source: str = "ai_extract",
        usage_cb=None,
    ) -> List[str]:
        """抽取事实并写入统一事实表 user_facts。

        这是唯一的事实写入入口：规则抽取和 AI 抽取都走这里，
        不再写没人读的 memory_facts。
        """
        try:
            from ..profile import ProfileManager
        except Exception:
            return []
        pm = ProfileManager(self.store)
        if use_ai:
            items = extract_facts_ai(user_text, usage_cb=usage_cb)
        else:
            items = extract_facts(user_text)
        stored: List[str] = []
        for item in items:
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            # 置信度必须一路传到写入层：太低的事实进 pending，不直接参与召回（审计 B11）
            try:
                confidence = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            fact_id = pm.add_fact(
                subject_id,
                value,
                category=str(item.get("category") or item.get("predicate") or "preference"),
                source=source,
                confidence=confidence,
            )
            if fact_id:
                stored.append(fact_id)
        return stored

    def prompt_lines(self, subject_id: str, query: str = "", limit: int = 8) -> List[str]:
        lines = []
        for item in self.search(subject_id, query, limit=limit):
            state = "当前" if item.get("status") == "active" else "历史"
            lines.append(f"[{state}事实] {item['predicate']}：{item['value']}")
        return lines

