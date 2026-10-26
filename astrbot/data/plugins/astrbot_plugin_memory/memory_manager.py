# -*- coding: utf-8 -*-
import json
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("MemoryManager")

# 引入统一智能体核心 (Agent Core)
_CORE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

from agent_core import IdentityResolver, TimelineEvent, TimelineHub

DB_PATH = Path("/AstrBot/data/memory.db")


class MemoryManager:
    """
    记忆管理器（以 TimelineHub 时间轴生命流为中枢核心）
    向上兼容原有的用户画像与情景记忆接口，底层全量接入拟人自传时间轴
    """
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        # 初始化自传式时间轴中枢（自动完成旧数据迁移与多维索引建立）
        self.hub = TimelineHub(db_path=self.db_path)
        self._init_legacy_tables()

    def _get_connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15.0)
        con.row_factory = sqlite3.Row
        return con

    def _init_legacy_tables(self):
        """保持原有表结构存在，确保老插件直连无阻"""
        with self._get_connection() as con:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    group_id TEXT,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    created_at REAL,
                    importance INTEGER DEFAULT 1
                );
                """
            )
            con.commit()

    # ================= 用户好感度与画像 =================

    def get_user_affection(self, user_id: str) -> int:
        actor_id, _, _ = IdentityResolver.resolve_actor("qq", str(user_id))
        return self.hub.get_user_affection(actor_id)

    def update_user_affection(
        self, user_id: str, delta: int, reason: str = "", nickname: str = ""
    ) -> Tuple[int, int]:
        actor_id, resolved_name, _ = IdentityResolver.resolve_actor("qq", str(user_id), nickname)
        old_val = self.hub.get_user_affection(actor_id)
        new_val = self.hub.update_user_affection(actor_id, delta, reason=reason)
        logger.info(
            f"[MemoryManager] 用户 {user_id}({resolved_name}) 好感度浮动: {old_val} -> {new_val} (delta={delta}, 原因: {reason})"
        )
        return old_val, new_val

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        actor_id, _, _ = IdentityResolver.resolve_actor("qq", str(user_id))
        return self.hub.get_user_profile(actor_id)

    def update_user_profile(
        self,
        user_id: str,
        nickname: Optional[str] = None,
        new_facts: Optional[List[str]] = None,
        role_type: Optional[str] = None,
    ):
        actor_id, resolved_name, _ = IdentityResolver.resolve_actor("qq", str(user_id), nickname)
        if new_facts:
            for f in new_facts:
                self.hub.add_user_fact(actor_id, f)

    # ================= 情景记忆与时间轴同步 =================

    def add_episodic_memory(
        self,
        user_id: str,
        group_id: str,
        content: str,
        embedding: Optional[List[float]] = None,
        importance: int = 1,
    ) -> int:
        """
        存储一条情景事件：
        1. 写入老表 episodic_memories (向后兼容)
        2. 同步写入 agent_timeline 时间轴生命流 (核心沉淀)
        """
        emb_bytes = None
        if embedding and len(embedding) == 768:
            emb_bytes = struct.pack("768f", *embedding)

        actor_id, actor_name, _ = IdentityResolver.resolve_actor("qq", str(user_id))
        source_module = "bilibili_patrol" if group_id == "bilibili_human_patrol" else "qq_chat"

        # 1. 注入核心时间轴
        self.hub.append_event(
            source_module=source_module,
            actor_id=actor_id,
            actor_name=actor_name,
            narrative=content.strip(),
            importance=float(importance),
            embedding=embedding,
            metadata={"group_id": group_id, "legacy_user_id": user_id}
        )

        # 2. 写入老表保障兼容
        with self._get_connection() as con:
            cur = con.cursor()
            cur.execute(
                """
                INSERT INTO episodic_memories (user_id, group_id, content, embedding, created_at, importance)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user_id),
                    str(group_id),
                    content.strip(),
                    emb_bytes,
                    time.time(),
                    importance,
                ),
            )
            inserted_id = cur.lastrowid
            con.commit()
            return inserted_id

    def search_episodic_memories(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        top_k: int = 2,
        threshold: float = 0.50,
    ) -> List[Dict[str, Any]]:
        """
        三维拟人时间轴检索：融合语义相似度 + 艾宾浩斯时间近因 + 重要度权重
        返回带有清晰时间标识的经历记忆，杜绝无时间感知的幻觉
        """
        actor_id = "master"
        if user_id:
            actor_id, _, _ = IdentityResolver.resolve_actor("qq", str(user_id))

        recalls = self.hub.recall_timeline(
            query_embedding=query_embedding,
            actor_id=actor_id,
            limit=top_k,
            threshold=threshold
        )

        results = []
        for r in recalls:
            # 格式化输出：带时间戳和模块标记，极大增强模型时序感知
            time_tag = r.event.time_str
            module_name = "B站见闻" if r.event.source_module == "bilibili_patrol" else ("PC操控" if r.event.source_module == "pc_bridge" else "交互经历")
            formatted_content = f"[{time_tag}·{module_name}] {r.event.narrative}"

            results.append({
                "id": r.event.event_id,
                "user_id": r.event.actor_id,
                "group_id": r.event.source_module,
                "content": formatted_content,
                "created_at": r.event.timestamp,
                "importance": r.event.importance,
                "similarity": r.similarity,
                "score": r.score,
                "recency": r.recency_score
            })

        return results

    def get_recent_memories(
        self, user_id: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        events = self.hub.get_recent_stream(limit=limit)
        return [
            {
                "id": ev.event_id,
                "user_id": ev.actor_id,
                "group_id": ev.source_module,
                "content": f"[{ev.time_str}] {ev.narrative}",
                "created_at": ev.timestamp,
                "importance": ev.importance,
            }
            for ev in events
        ]

    def delete_episodic_memory(self, memory_id: int) -> bool:
        with self._get_connection() as con:
            cur = con.execute(
                "DELETE FROM episodic_memories WHERE id = ?", (memory_id,)
            )
            con.commit()
            return cur.rowcount > 0
