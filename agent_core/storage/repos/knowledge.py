# -*- coding: utf-8 -*-
"""知识库：来源、切片与检索。"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class KnowledgeOps:
    """知识库：来源、切片与检索。"""

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
