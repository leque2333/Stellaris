# -*- coding: utf-8 -*-
"""独立图库：SQLite 图存储（节点/边），与主记忆库分离。

不依赖 Neo4j；单用户/本地部署下，独立文件足够，后续可替换为图数据库。
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _path() -> Path:
    raw = os.getenv("HUB_GRAPH_DB_PATH", "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parents[2] / "data" / "memory_graph.db"


class GraphStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_nodes (
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
                CREATE TABLE IF NOT EXISTS graph_edges (
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
                "CREATE INDEX IF NOT EXISTS idx_graph_edges_subject "
                "ON graph_edges(subject_id, status)"
            )

    def _ensure_node(self, conn: sqlite3.Connection, name: str, entity_type: str, person_id: str) -> str:
        now = time.time()
        row = conn.execute(
            "SELECT entity_id FROM graph_nodes WHERE person_id = ? AND entity_type = ? AND name = ?",
            (person_id, entity_type, name),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE graph_nodes SET updated_at = ? WHERE entity_id = ?",
                (now, row["entity_id"]),
            )
            return str(row["entity_id"])
        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO graph_nodes (entity_id, entity_type, name, person_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entity_id, entity_type, name, person_id, now, now),
        )
        return entity_id

    def index_fact(self, fact: Dict[str, Any]) -> None:
        if not fact or fact.get("status") != "active":
            return
        subject = str(fact.get("subject_id") or "")
        predicate = str(fact.get("predicate") or "")
        value = str(fact.get("value") or "")
        if not subject or not predicate or not value:
            return
        with self._connect() as conn:
            self._ensure_node(conn, subject, "person", subject)
            self._ensure_node(conn, value, "thing", subject)
            row = conn.execute(
                "SELECT relation_id FROM graph_edges WHERE fact_id = ?", (fact["fact_id"],)
            ).fetchone()
            now = time.time()
            if row:
                conn.execute(
                    "UPDATE graph_edges SET status = 'active', invalid_at = NULL, "
                    "valid_at = ? WHERE relation_id = ?",
                    (float(fact.get("valid_at") or now), row["relation_id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO graph_edges (relation_id, subject_id, predicate, object_value, "
                    "fact_id, status, valid_at, invalid_at, source_event_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?)",
                    (
                        f"rel_{uuid.uuid4().hex[:12]}", subject, predicate, value,
                        fact["fact_id"], float(fact.get("valid_at") or now),
                        str(fact.get("source_event_id") or ""), now,
                    ),
                )

    def deactivate_fact(self, fact_id: str) -> None:
        if not fact_id:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_edges SET status = 'inactive', invalid_at = ? WHERE fact_id = ?",
                (time.time(), str(fact_id)),
            )

    def graph(self, subject_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            nodes = conn.execute(
                "SELECT * FROM graph_nodes WHERE person_id = ? ORDER BY updated_at DESC",
                (str(subject_id or ""),),
            ).fetchall()
            edges = conn.execute(
                "SELECT * FROM graph_edges WHERE subject_id = ? AND status = 'active' "
                "ORDER BY valid_at DESC",
                (str(subject_id or ""),),
            ).fetchall()
        return {"nodes": [dict(row) for row in nodes], "edges": [dict(row) for row in edges]}

    def counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            nodes = conn.execute("SELECT count(*) FROM graph_nodes").fetchone()[0]
            edges = conn.execute("SELECT count(*) FROM graph_edges").fetchone()[0]
        return {"nodes": int(nodes), "edges": int(edges)}
