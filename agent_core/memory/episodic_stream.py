# -*- coding: utf-8 -*-
"""
Stellaris Core - 情境经历时间轴流 (Episodic Stream)
职责：
1. 记录 24~48 小时内的连续自传经历流水 (agent_timeline)
2. 维护物理时序与前后前驱关联 (prev_event_id)
3. 实现基于艾宾浩斯遗忘曲线的时间近因衰减与三维拟人召回模型

存储细节已下沉到 agent_core.storage，这里只保留记忆语义。
"""

import json
import math
import struct
import time
from typing import Any, Dict, List, Optional

import numpy as np

from ..identity import IdentityResolver
from ..models import RecallResult, TimelineEvent, get_cst_now_str
from ..storage import Store


class EpisodicStream:
    """情境经历时间轴流管理器"""

    def __init__(self, store: Store):
        self.store = store
        # 兼容旧属性名
        self.dal = store

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
        detail_id: str = "",
        prev_event_id: Optional[str] = None,
        causal_relation: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
        session_window_seconds: int = 0,
        plugin_id: Optional[str] = None,
        tag_primary: Optional[str] = None,
        tag_secondary: Optional[str] = None,
        segment_id: Optional[str] = None,
        started_at: Optional[float] = None,
        ended_at: Optional[float] = None,
        visibility: str = "private",
        person_id: Optional[str] = None,
    ) -> TimelineEvent:
        """向自传时间轴追加一条经历；session_window_seconds 用于同源高频折叠"""
        now_ts = timestamp if timestamp is not None else time.time()

        if not actor_name:
            if actor_id in ("master", IdentityResolver.get_master_qq()):
                actor_name = IdentityResolver.get_master_name()
            elif actor_id == "self":
                actor_name = IdentityResolver.get_bot_name()
            else:
                actor_name = f"用户_{actor_id}"

        emb_blob = embedding_bytes
        if emb_blob is None and embedding is not None and len(embedding) == 768:
            emb_blob = struct.pack("768f", *embedding)

        # person_id 归一：master / QQ 号 → 档案 ID，避免时间轴按人查询漏掉
        if not person_id:
            try:
                from ..profile.archives import ArchiveManager

                arch = ArchiveManager(self.store).resolve_actor(actor_id)
                if arch:
                    person_id = str(arch.get("archive_id") or "") or None
            except Exception:
                person_id = None

        event = TimelineEvent(
            event_id="",
            timestamp=now_ts,
            time_str=get_cst_now_str(now_ts),
            source_module=source_module,
            actor_id=actor_id,
            actor_name=actor_name,
            narrative=narrative,
            emotional_impact=emotional_impact,
            importance=importance,
            prev_event_id=prev_event_id,
            causal_relation=causal_relation,
            metadata=metadata or {},
            embedding=emb_blob,
            detail_id=detail_id or "",
            plugin_id=plugin_id,
            tag_primary=tag_primary,
            tag_secondary=tag_secondary,
            segment_id=segment_id,
            started_at=started_at,
            ended_at=ended_at,
            visibility=visibility,
            person_id=person_id,
        )
        return self.store.append_event(event, session_window_seconds=session_window_seconds)

    def recall_timeline(
        self,
        query: Optional[str] = None,
        query_embedding: Optional[List[float]] = None,
        actor_id: Optional[str] = "master",
        person_id: Optional[str] = None,
        limit: int = 3,
        threshold: float = 0.40,
        alpha: float = 0.45,           # 语义相关度权重
        beta: float = 0.35,            # 艾宾浩斯时间近因权重
        gamma: float = 0.20,           # 重要度权重
        half_life_hours: float = 36.0, # 记忆近因半衰期 (小时)
        max_per_module: int = 1,       # 单模块配额上限 (防单一高频刷屏淹没)
        include_all_actors: bool = False,
        channel_id: Optional[str] = None,
        include_channel: bool = False,
        exclude_group: bool = False,
    ) -> List[RecallResult]:
        """三维拟人召回：Score = α·Similarity + β·Recency + γ·Importance"""
        now_ts = time.time()
        tau = half_life_hours * 3600.0 / math.log(2)

        allowed_actor_ids: Optional[List[str]] = None
        if not person_id and not include_all_actors and actor_id:
            master_qq = IdentityResolver.get_master_qq()
            if actor_id in ("master", master_qq):
                allowed_actor_ids = ["master", master_qq]
            else:
                allowed_actor_ids = [actor_id]

        candidate_kwargs: Dict[str, Any] = {
            "actor_ids": allowed_actor_ids,
            "person_ids": [str(person_id)] if person_id else None,
            "exclude_group": exclude_group,
            "limit": 200,
        }
        if include_channel and channel_id:
            candidate_kwargs["source_modules"] = [channel_id]
        candidates = self.store.list_candidates(**candidate_kwargs)
        if not candidates:
            return []

        results: List[RecallResult] = []
        q_vec = np.array(query_embedding, dtype=np.float32) if query_embedding else None
        q_norm = np.linalg.norm(q_vec) if q_vec is not None else 0.0

        for evt in candidates:
            sim = 0.0
            if q_vec is not None and q_norm > 0 and evt.embedding:
                try:
                    c_vec = np.frombuffer(evt.embedding, dtype=np.float32)
                    c_norm = np.linalg.norm(c_vec)
                    if c_norm > 0:
                        sim = float(np.dot(q_vec, c_vec) / (q_norm * c_norm))
                except Exception:
                    sim = 0.0

            if query and evt.narrative:
                q_clean = str(query).strip().lower()
                narr_clean = str(evt.narrative).lower()
                if q_clean in narr_clean:
                    sim = max(sim, 0.95)
                else:
                    q_chars = [c for c in q_clean if c.strip() and c not in "，。！？,.!?“”：:"]
                    if q_chars:
                        overlap = sum(1 for c in q_chars if c in narr_clean)
                        ratio = overlap / len(q_chars)
                        if ratio >= 0.4:
                            sim = max(sim, float(ratio * 0.9))

            delta_t = max(0.0, now_ts - float(evt.timestamp))
            recency_score = math.exp(-delta_t / tau)
            importance_raw = float(evt.importance or 1.0)
            importance_score = min(max(importance_raw / 5.0, 0.2), 1.0)

            if q_vec is not None or (query and sim > 0):
                total_score = (alpha * max(0.0, sim)) + (beta * recency_score) + (gamma * importance_score)
            else:
                total_score = (0.7 * recency_score) + (0.3 * importance_score)

            if total_score >= threshold:
                results.append(RecallResult(
                    event=evt,
                    score=round(total_score, 4),
                    similarity=round(sim, 4),
                    recency_score=round(recency_score, 4),
                    importance_score=round(importance_score, 4),
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        if max_per_module and max_per_module > 0:
            filtered: List[RecallResult] = []
            module_counts: Dict[str, int] = {}
            for res in results:
                mod = res.event.source_module
                if module_counts.get(mod, 0) < max_per_module:
                    filtered.append(res)
                    module_counts[mod] = module_counts.get(mod, 0) + 1
                    if len(filtered) >= limit:
                        break
            if len(filtered) < limit:
                for res in results:
                    if res not in filtered:
                        filtered.append(res)
                        if len(filtered) >= limit:
                            break
            return filtered
        return results[:limit]

    def get_surrounding_events(
        self,
        event_id: str,
        before_n: int = 2,
        after_n: int = 2,
    ) -> List[TimelineEvent]:
        """顺藤摸瓜：围绕指定事件抽取前后关联事件"""
        return self.store.surrounding(event_id, before_n=before_n, after_n=after_n)

    def get_recent_stream(
        self,
        limit: int = 10,
        since_ts: Optional[float] = None,
        source_module: Optional[str] = None,
        actor_id: Optional[str] = None,
        diverse: bool = True,
    ) -> List[TimelineEvent]:
        """获取最近经历流；diverse=True 时做跨通道配额与同源连续折叠"""
        fetch_limit = max(limit * 4, 25) if (diverse and not source_module) else limit
        raw_evts = self.store.list_events(
            limit=fetch_limit,
            since_ts=since_ts,
            source_module=source_module,
            actor_ids=[actor_id] if actor_id else None,
            order="desc",
        )
        if not raw_evts:
            return []

        if diverse and not source_module:
            collapsed: List[TimelineEvent] = []
            i = 0
            while i < len(raw_evts):
                curr = raw_evts[i]
                run = [curr]
                j = i + 1
                while (
                    j < len(raw_evts)
                    and raw_evts[j].source_module == curr.source_module
                    and (curr.timestamp - raw_evts[j].timestamp) <= 3600
                ):
                    run.append(raw_evts[j])
                    j += 1

                if len(run) > 1:
                    collapsed.append(TimelineEvent(
                        event_id=curr.event_id,
                        timestamp=curr.timestamp,
                        time_str=curr.time_str,
                        source_module=curr.source_module,
                        actor_id=curr.actor_id,
                        actor_name=curr.actor_name,
                        narrative=f"连续进行 {len(run)} 次{curr.source_module}交互 (最新: {curr.narrative})",
                        emotional_impact=curr.emotional_impact,
                        importance=max(e.importance for e in run),
                        prev_event_id=curr.prev_event_id,
                        causal_relation=curr.causal_relation,
                        metadata={"collapsed_count": len(run)},
                        tag_primary=curr.tag_primary,
                        tag_secondary=curr.tag_secondary,
                    ))
                else:
                    collapsed.append(curr)
                i = j

            selected: List[TimelineEvent] = []
            module_seen: Dict[str, int] = {}
            for e in collapsed:
                m = e.source_module
                if module_seen.get(m, 0) < 2:
                    selected.append(e)
                    module_seen[m] = module_seen.get(m, 0) + 1
                if len(selected) >= limit:
                    break

            if len(selected) < limit:
                for e in collapsed:
                    if e not in selected:
                        selected.append(e)
                        if len(selected) >= limit:
                            break

            selected.reverse()
            return selected

        return list(reversed(raw_evts[:limit]))

    # ------------------------------------------------------------------
    # L1 调用索引（时间轴的最细粒度）
    # ------------------------------------------------------------------
    def record_invocation(self, trace_id: str, layer: str, module_id: str, **kwargs) -> str:
        """记录一次模块/工具调用：只存索引，不存正文"""
        return self.store.record_invocation(trace_id, layer, module_id, **kwargs)

    def finish_invocation(self, invocation_id: str, **kwargs) -> None:
        return self.store.finish_invocation(invocation_id, **kwargs)

    def list_invocations(self, **kwargs) -> List[Dict[str, Any]]:
        return self.store.list_invocations(**kwargs)
