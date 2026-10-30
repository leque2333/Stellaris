# -*- coding: utf-8 -*-
"""统一知识库：宿主共享的文档片段"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

from .storage import Store


class KnowledgeBase:
    def __init__(self, store: Store):
        self.store = store

    def ingest(
        self,
        title: str,
        content: str,
        source: str = "",
        tags: Optional[List[str]] = None,
        chunk_size: int = 800,
        overlap: int = 100,
    ) -> Dict[str, Any]:
        title = str(title or "").strip() or "未命名知识"
        text = str(content or "").strip()
        if not text:
            raise ValueError("content 不能为空")
        source_id = "kb_" + hashlib.sha1(
            f"{title}|{source}|{time.time()}".encode("utf-8")
        ).hexdigest()[:12]
        chunks = self._chunk(text, max(200, int(chunk_size)), max(0, int(overlap)))
        now = time.time()
        self.store.add_knowledge_source(source_id, title, source, tags or [])
        self.store.add_knowledge_chunks([
            {
                "chunk_id": f"{source_id}_{idx}",
                "source_id": source_id,
                "title": title,
                "content": chunk,
                "created_at": now,
            }
            for idx, chunk in enumerate(chunks)
        ])
        return {"source_id": source_id, "title": title, "chunks": len(chunks)}

    def search(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        return self.store.search_knowledge(query, limit=max(1, int(limit)))

    def list(self) -> List[Dict[str, Any]]:
        return self.store.list_knowledge_sources()

    def delete(self, source_id: str) -> bool:
        return self.store.delete_knowledge_source(source_id)

    @staticmethod
    def _chunk(text: str, size: int, overlap: int) -> List[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            if len(para) > size:
                if current:
                    chunks.append(current)
                    current = ""
                step = max(1, size - overlap)
                for start in range(0, len(para), step):
                    piece = para[start:start + size]
                    if piece:
                        chunks.append(piece)
                continue
            if current and len(current) + len(para) + 2 > size:
                chunks.append(current)
                current = current[-overlap:] if overlap else ""
            current = (current + "\n\n" + para).strip() if current else para
        if current:
            chunks.append(current)
        return chunks or [text[:size]]
