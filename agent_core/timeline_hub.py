# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 拟人自传式时间轴记忆中枢 (TimelineHub)
将所有模块（B站巡航、QQ对话、PC控制、Telegram交互）汇总为一条连续的生命经历时间轴
具备：三维拟人召回（语义相关度 + 艾宾浩斯时间衰减 + 重要度加权）、时序链顺藤摸瓜、旧数据无缝迁移
"""

import json
import math
import os
import sqlite3
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .identity import IdentityResolver
from .models import RecallResult, TimelineEvent, UserProfile, get_cst_now_str

def _resolve_default_db_path() -> Path:
    if os.name != "nt" and Path("/.dockerenv").exists():
        return Path("/app/data/memory.db")
    base = Path(__file__).resolve().parent.parent
    return base / "data" / "memory.db"

DEFAULT_DB_PATH = _resolve_default_db_path()


class TimelineHub:
    """
    自传式时间轴记忆中枢
    """
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy_episodic_if_needed()
        self._seed_master_profile()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化新一代时间轴生命流表与用户画像表"""
        with self._get_connection() as conn:
            cur = conn.cursor()
            
            # 1. 核心自传式时间轴表 (agent_timeline)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_timeline (
                event_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                time_str TEXT NOT NULL,
                source_module TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                narrative TEXT NOT NULL,
                emotional_impact INTEGER DEFAULT 0,
                importance REAL DEFAULT 1.0,
                prev_event_id TEXT,
                causal_relation TEXT,
                metadata TEXT DEFAULT '{}',
                embedding BLOB
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_timeline_ts ON agent_timeline(timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_timeline_actor ON agent_timeline(actor_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_timeline_module ON agent_timeline(source_module);")

            # 2. 用户长效偏好与好感度画像表 (兼容原有 user_profiles)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                nickname TEXT,
                role_type TEXT DEFAULT 'member',
                facts TEXT DEFAULT '[]',
                updated_at REAL,
                affection INTEGER DEFAULT 50,
                affection_history TEXT DEFAULT '[]'
            );
            """)
            # 3. 统一 Token 账本与大模型交互消耗流水表
            cur.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                time_str TEXT,
                session_type TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cached_tokens INTEGER DEFAULT 0,
                call_duration_ms INTEGER DEFAULT 0
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ts ON token_usage(timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage(session_type);")
            conn.commit()

    def _seed_master_profile(self):
        """预置主人（主人）长效基底偏好与初始好感度 (90)"""
        master_qq = IdentityResolver.get_master_qq()
        with self._get_connection() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM user_profiles WHERE user_id = ?", (master_qq,)).fetchone()
            if not row:
                init_facts = [
                    "是助手的唯一管理员与主人，在群聊与私聊中平视相处",
                    "最近在外面上班不常在家，叮嘱维护网络时必须使用重启命令而非关闭命令",
                    "喜欢二次元与技术探索，常在群内聊明日方舟、边狱公司、前沿AI大模型等话题",
                    "答应给助手买零食和小鱼干作为动力",
                ]
                now_ts = time.time()
                cur.execute(
                    """
                    INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        master_qq,
                        "主人",
                        "admin",
                        json.dumps(init_facts, ensure_ascii=False),
                        now_ts,
                        90,
                        json.dumps([{"time": now_ts, "delta": 0, "old": 90, "new": 90, "reason": "契约初始好感度"}], ensure_ascii=False),
                    ),
                )
                conn.commit()

    def _migrate_legacy_episodic_if_needed(self):
        # 自动合并 anan_memory.db 中的历史经历与 Token 记录
        try:
            anan_db = self.db_path.parent / "anan_memory.db"
            if anan_db.exists() and anan_db.resolve() != self.db_path.resolve():
                with sqlite3.connect(anan_db) as a_conn:
                    a_conn.row_factory = sqlite3.Row
                    a_cur = a_conn.cursor()
                    with self._get_connection() as conn:
                        cur = conn.cursor()
                        # 合并 agent_timeline
                        a_tbl = a_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_timeline'").fetchone()
                        if a_tbl:
                            a_rows = a_cur.execute("SELECT * FROM agent_timeline").fetchall()
                            for ar in a_rows:
                                cur.execute(
                                    """
                                    INSERT OR IGNORE INTO agent_timeline 
                                    (event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative, emotional_impact, importance, prev_event_id, causal_relation, metadata, embedding)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        ar["event_id"], ar["timestamp"], ar["time_str"], ar["source_module"],
                                        ar["actor_id"], ar["actor_name"], ar["narrative"], ar["emotional_impact"],
                                        ar["importance"], ar["prev_event_id"], ar["causal_relation"],
                                        ar["metadata"], ar["embedding"]
                                    )
                                )
                        # 合并 token_usage
                        t_tbl = a_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'").fetchone()
                        if t_tbl:
                            t_rows = a_cur.execute("SELECT * FROM token_usage").fetchall()
                            for tr in t_rows:
                                cur.execute(
                                    """
                                    INSERT OR IGNORE INTO token_usage
                                    (id, timestamp, time_str, session_type, model, prompt_tokens, completion_tokens, total_tokens, cached_tokens, call_duration_ms)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        tr["id"], tr["timestamp"], tr["time_str"], tr["session_type"],
                                        tr["model"], tr["prompt_tokens"], tr["completion_tokens"],
                                        tr["total_tokens"], tr["cached_tokens"], tr["call_duration_ms"]
                                    )
                                )
                        conn.commit()
        except Exception:
            pass

        """
        无缝数据升级：将原 episodic_memories 老表中的所有历史经历平滑迁入 agent_timeline
        确保老系统已有经历（如历史B站巡检、历史群聊记忆）一条不丢！
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            # 检查是否有老表
            tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='episodic_memories'").fetchone()
            if not tbl:
                return

            # 查看老表总数与新表总数
            old_count = cur.execute("SELECT count(*) FROM episodic_memories").fetchone()[0]
            new_count = cur.execute("SELECT count(*) FROM agent_timeline").fetchone()[0]
            
            # 若新表为空且老表有数据，全量平滑迁移并构建初始时间轴时序
            if old_count > 0 and new_count == 0:
                rows = cur.execute("SELECT * FROM episodic_memories ORDER BY created_at ASC").fetchall()
                prev_id = None
                for r in rows:
                    raw_id = r["id"]
                    user_id = str(r["user_id"] or "").strip()
                    group_id = str(r["group_id"] or "").strip()
                    content = str(r["content"] or "").strip()
                    emb_blob = r["embedding"]
                    ts = float(r["created_at"] or time.time())
                    importance = float(r["importance"] or 1.0)

                    # 识别模块来源
                    if group_id == "bilibili_human_patrol" or "B站" in content:
                        source_module = "bilibili_patrol"
                    else:
                        source_module = "qq_chat"

                    # 统一解析身份
                    actor_id, actor_name, _ = IdentityResolver.resolve_actor("qq", user_id)
                    evt_id = f"evt_migrated_{raw_id}"

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO agent_timeline 
                        (event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative, emotional_impact, importance, prev_event_id, causal_relation, metadata, embedding)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evt_id,
                            ts,
                            get_cst_now_str(ts),
                            source_module,
                            actor_id,
                            actor_name,
                            content,
                            0,
                            importance,
                            prev_id,
                            "legacy_record",
                            json.dumps({"migrated_from_id": raw_id, "legacy_group_id": group_id}, ensure_ascii=False),
                            emb_blob
                        )
                    )
                    prev_id = evt_id
                conn.commit()

    # ================= 核心时间轴写入与追加 =================

    def append_event(
        self,
        source_module: str,
        actor_id: str,
        narrative: str,
        actor_name: Optional[str] = None,
        emotional_impact: int = 0,
        importance: float = 1.0,
        embedding: Optional[List[float]] = None,
        embedding_bytes: Optional[bytes] = None,
        prev_event_id: Optional[str] = None,
        causal_relation: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None
    ) -> TimelineEvent:
        """
        向时间轴生命流追加一条经历事件
        - 自动建立时序前驱关系
        - 自动根据 emotional_impact 调节主仆好感度
        - 返回创建成功的 TimelineEvent
        """
        now_ts = timestamp if timestamp is not None else time.time()
        time_str = get_cst_now_str(now_ts)
        event_id = f"evt_{int(now_ts * 1000)}_{uuid.uuid4().hex[:6]}"

        if not actor_name:
            if actor_id in ("master", IdentityResolver.get_master_qq()):
                actor_name = "主人"
            elif actor_id == "self":
                actor_name = "助手"
            else:
                actor_name = f"用户_{actor_id}"

        # 向量字节处理
        emb_blob = embedding_bytes
        if emb_blob is None and embedding is not None:
            if len(embedding) == 768:
                emb_blob = struct.pack("768f", *embedding)

        # 若未指定前驱事件，自动寻找 30 分钟内同一实体或全局上一条事件构建时序链
        if prev_event_id is None:
            with self._get_connection() as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT event_id FROM agent_timeline WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                    (now_ts - 1800,)
                ).fetchone()
                if row:
                    prev_event_id = row["event_id"]

        meta_dict = metadata or {}
        meta_json = json.dumps(meta_dict, ensure_ascii=False)

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO agent_timeline 
                (event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative, emotional_impact, importance, prev_event_id, causal_relation, metadata, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    now_ts,
                    time_str,
                    source_module,
                    actor_id,
                    actor_name,
                    narrative,
                    emotional_impact,
                    importance,
                    prev_event_id,
                    causal_relation,
                    meta_json,
                    emb_blob
                )
            )
            conn.commit()

        # 好感度即时联动
        if emotional_impact != 0 and actor_id in ("master", IdentityResolver.get_master_qq()):
            self.update_user_affection(
                user_id=IdentityResolver.get_master_qq(),
                delta=emotional_impact,
                reason=f"事件联动[{source_module}]: {narrative[:30]}"
            )

        return TimelineEvent(
            event_id=event_id,
            timestamp=now_ts,
            time_str=time_str,
            source_module=source_module,
            actor_id=actor_id,
            actor_name=actor_name,
            narrative=narrative,
            emotional_impact=emotional_impact,
            importance=importance,
            prev_event_id=prev_event_id,
            causal_relation=causal_relation,
            metadata=meta_dict,
            embedding=emb_blob
        )

    # ================= 核心拟人三维时间轴召回 =================

    def recall_timeline(
        self,
        query_embedding: Optional[List[float]] = None,
        actor_id: Optional[str] = "master",
        limit: int = 3,
        threshold: float = 0.40,
        alpha: float = 0.45,    # 语义相似度权重
        beta: float = 0.35,     # 艾宾浩斯时间近因权重
        gamma: float = 0.20,    # 重要度权重
        half_life_hours: float = 36.0,  # 记忆近因半衰期 (小时)
        query: Optional[str] = None     # 文本关键字/用户原句
    ) -> List[RecallResult]:
        """
        三维拟人召回模型：
        Score = α·Similarity + β·Recency + γ·Importance
        - 彻底解决传统纯向量检索把半年前事件当成刚才发生的幻觉
        - 兼顾时间衰减与重大记忆留存
        """
        now_ts = time.time()
        tau = half_life_hours * 3600.0 / math.log(2)  # 半衰期时间常数

        with self._get_connection() as conn:
            cur = conn.cursor()
            
            # 若限定 actor_id
            if actor_id:
                master_qq = IdentityResolver.get_master_qq()
                if actor_id in ("master", master_qq):
                    query_sql = "SELECT * FROM agent_timeline WHERE actor_id IN ('master', ?) ORDER BY timestamp DESC LIMIT 200"
                    params = (master_qq,)
                else:
                    query_sql = "SELECT * FROM agent_timeline WHERE actor_id = ? ORDER BY timestamp DESC LIMIT 200"
                    params = (actor_id,)
            else:
                query_sql = "SELECT * FROM agent_timeline ORDER BY timestamp DESC LIMIT 200"
                params = ()

            rows = cur.execute(query_sql, params).fetchall()

        if not rows:
            return []

        results: List[RecallResult] = []
        q_vec = np.array(query_embedding, dtype=np.float32) if query_embedding else None
        q_norm = np.linalg.norm(q_vec) if q_vec is not None else 0.0

        for r in rows:
            # 1. 语义相似度计算 (支持向量匹配与文本关键字/语义重叠)
            sim = 0.0
            if q_vec is not None and q_norm > 0 and r["embedding"]:
                try:
                    c_vec = np.frombuffer(r["embedding"], dtype=np.float32)
                    c_norm = np.linalg.norm(c_vec)
                    if c_norm > 0:
                        sim = float(np.dot(q_vec, c_vec) / (q_norm * c_norm))
                except Exception:
                    sim = 0.0

            # 文本相关度启发式匹配 (无需向量也能顺畅按语意与事件匹配召回)
            if query and r["narrative"]:
                q_clean = str(query).strip().lower()
                narr_clean = str(r["narrative"]).lower()
                if q_clean in narr_clean:
                    sim = max(sim, 0.95)
                else:
                    q_chars = [c for c in q_clean if c.strip() and c not in ("，", "。", "！", "？", " ", ",", ".", "!", "?", "“", "”", "：")]
                    if q_chars:
                        overlap = sum(1 for c in q_chars if c in narr_clean)
                        ratio = overlap / len(q_chars)
                        if ratio >= 0.4:
                            sim = max(sim, float(ratio * 0.9))

            # 2. 艾宾浩斯时间近因评分
            delta_t = max(0.0, now_ts - float(r["timestamp"]))
            recency_score = math.exp(- delta_t / tau)

            # 3. 重要度折算评分 (归一化到 0.0 ~ 1.0)
            importance_raw = float(r["importance"] or 1.0)
            importance_score = min(max(importance_raw / 5.0, 0.2), 1.0)

            # 综合多维打分
            if q_vec is not None or (query and sim > 0):
                # 具备向量或文本关键字检索时的三维融分
                total_score = (alpha * max(0.0, sim)) + (beta * recency_score) + (gamma * importance_score)
            else:
                # 纯时序近期生命流，依据时间近因与重要度检索
                total_score = (0.7 * recency_score) + (0.3 * importance_score)

            if total_score >= threshold:
                evt = TimelineEvent(
                    event_id=r["event_id"],
                    timestamp=r["timestamp"],
                    time_str=r["time_str"],
                    source_module=r["source_module"],
                    actor_id=r["actor_id"],
                    actor_name=r["actor_name"],
                    narrative=r["narrative"],
                    emotional_impact=r["emotional_impact"],
                    importance=r["importance"],
                    prev_event_id=r["prev_event_id"],
                    causal_relation=r["causal_relation"],
                    metadata=json.loads(r["metadata"] or "{}"),
                    embedding=r["embedding"]
                )
                results.append(RecallResult(
                    event=evt,
                    score=total_score,
                    similarity=sim,
                    recency_score=recency_score,
                    importance_score=importance_score
                ))

        # 按总得分降序排列
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    # ================= 顺藤摸瓜：前后时序链展开 =================

    def get_surrounding_events(
        self,
        event_id: str,
        before_n: int = 2,
        after_n: int = 2
    ) -> List[TimelineEvent]:
        """
        顺藤摸瓜时序能力：围绕指定事件节点，抽取其前后发生的连续事件
        使助手能够清楚交代前因后果
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            target = cur.execute("SELECT * FROM agent_timeline WHERE event_id = ?", (event_id,)).fetchone()
            if not target:
                return []
            
            target_ts = target["timestamp"]
            
            # 前驱事件 (发生在该事件之前的最近 before_n 条)
            before_rows = cur.execute(
                "SELECT * FROM agent_timeline WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
                (target_ts, before_n)
            ).fetchall()
            before_rows.reverse()  # 恢复正向时序

            # 后续事件 (发生在该事件之后的紧跟 after_n 条)
            after_rows = cur.execute(
                "SELECT * FROM agent_timeline WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                (target_ts, after_n)
            ).fetchall()

        all_rows = before_rows + [target] + after_rows
        events = []
        for r in all_rows:
            events.append(TimelineEvent(
                event_id=r["event_id"],
                timestamp=r["timestamp"],
                time_str=r["time_str"],
                source_module=r["source_module"],
                actor_id=r["actor_id"],
                actor_name=r["actor_name"],
                narrative=r["narrative"],
                emotional_impact=r["emotional_impact"],
                importance=r["importance"],
                prev_event_id=r["prev_event_id"],
                causal_relation=r["causal_relation"],
                metadata=json.loads(r["metadata"] or "{}"),
                embedding=r["embedding"]
            ))
        return events

    def get_recent_stream(
        self,
        limit: int = 10,
        since_ts: Optional[float] = None,
        source_module: Optional[str] = None
    ) -> List[TimelineEvent]:
        """
        获取最近的自传式生命流列表（用于构建助手当前的即时经历背景）
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            conditions = []
            params = []
            
            if since_ts:
                conditions.append("timestamp >= ?")
                params.append(since_ts)
            if source_module:
                conditions.append("source_module = ?")
                params.append(source_module)
                
            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = f"SELECT * FROM agent_timeline {where_clause} ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            rows = cur.execute(sql, params).fetchall()

        rows.reverse() # 恢复按时间推进的时序正序
        events = []
        for r in rows:
            events.append(TimelineEvent(
                event_id=r["event_id"],
                timestamp=r["timestamp"],
                time_str=r["time_str"],
                source_module=r["source_module"],
                actor_id=r["actor_id"],
                actor_name=r["actor_name"],
                narrative=r["narrative"],
                emotional_impact=r["emotional_impact"],
                importance=r["importance"],
                prev_event_id=r["prev_event_id"],
                causal_relation=r["causal_relation"],
                metadata=json.loads(r["metadata"] or "{}"),
                embedding=r["embedding"]
            ))
        return events

    # ================= 用户长效画像与好感度兼容层 =================

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        target_id = IdentityResolver.get_master_qq() if user_id in ("master", IdentityResolver.get_master_qq()) else user_id
        with self._get_connection() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM user_profiles WHERE user_id = ?", (target_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["facts"] = json.loads(res.get("facts") or "[]")
            except Exception:
                res["facts"] = []
            try:
                res["affection_history"] = json.loads(res.get("affection_history") or "[]")
            except Exception:
                res["affection_history"] = []
            return res

    def get_user_affection(self, user_id: str) -> int:
        prof = self.get_user_profile(user_id)
        if prof and "affection" in prof and prof["affection"] is not None:
            return int(prof["affection"])
        return 90 if user_id in ("master", IdentityResolver.get_master_qq()) else 50

    def update_user_affection(self, user_id: str, delta: int, reason: str = "") -> int:
        target_id = IdentityResolver.get_master_qq() if user_id in ("master", IdentityResolver.get_master_qq()) else user_id
        current = self.get_user_affection(target_id)
        new_val = max(0, min(100, current + delta))
        now_ts = time.time()

        with self._get_connection() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT affection_history FROM user_profiles WHERE user_id = ?", (target_id,)).fetchone()
            history = []
            if row and row["affection_history"]:
                try:
                    history = json.loads(row["affection_history"])
                except Exception:
                    pass
            history.append({
                "time": now_ts,
                "delta": delta,
                "old": current,
                "new": new_val,
                "reason": reason
            })
            if len(history) > 50:
                history = history[-50:]

            cur.execute(
                """
                UPDATE user_profiles 
                SET affection = ?, affection_history = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (new_val, json.dumps(history, ensure_ascii=False), now_ts, target_id)
            )
            conn.commit()
        return new_val

    def add_user_fact(self, user_id: str, fact: str):
        target_id = IdentityResolver.get_master_qq() if user_id in ("master", IdentityResolver.get_master_qq()) else user_id
        prof = self.get_user_profile(target_id)
        if not prof:
            return
        facts = prof.get("facts", [])
        clean_fact = fact.strip()
        if clean_fact and clean_fact not in facts:
            facts.append(clean_fact)
            if len(facts) > 30:
                facts = facts[-30:]
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE user_profiles SET facts = ?, updated_at = ? WHERE user_id = ?",
                    (json.dumps(facts, ensure_ascii=False), time.time(), target_id)
                )
                conn.commit()
