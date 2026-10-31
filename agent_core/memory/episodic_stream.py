# -*- coding: utf-8 -*-
"""
Stellaris Core - 情境经历时间轴流 (Episodic Stream)
职责：
1. 记录 24~48 小时内的连续自传经历流水 (agent_timeline)
2. 维护物理时序与前后前驱关联 (prev_event_id)
3. 实现基于艾宾浩斯遗忘曲线的时间近因衰减与三维拟人召回模型

存储细节已下沉到 agent_core.storage，这里只保留记忆语义。
"""

import math
import os
import struct
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from ..identity import IdentityResolver
from ..models import RecallResult, TimelineEvent, get_cst_now_str
from ..storage import Store


@dataclass(frozen=True)
class _RecallWeights:
    """召回打分的权重与阈值，集中传，免得六七个参数满天飞。"""

    alpha: float
    beta: float
    gamma: float
    tau: float
    threshold: float


def _allowed_actor_ids(actor_id, person_id, include_all_actors):
    """定了 person 或要求全量时不限 actor；主人同时认 master 和 QQ 号。"""
    if person_id or include_all_actors or not actor_id:
        return None
    master_qq = IdentityResolver.get_master_qq()
    return ["master", master_qq] if actor_id in ("master", master_qq) else [actor_id]


RECALL_DEFAULT_DAYS = 0.0          # 0 = 不按时间窗，改按字数预算
RECALL_DEFAULT_CHARS = 300_000     # 候选池总字数上限（约 3500 条平均长度的记忆）
RECALL_DEFAULT_LIMIT = 3000
RECALL_DEFAULT_EXCLUDE = (
    "verify", "acceptance", "sec-live", "archcheck", "keycheck", "sig-probe", "kb_test",
)


def _recall_window():
    """候选池参数：字数预算、时间窗（可选）、条数上限、排除来源。"""
    try:
        chars = int(os.getenv("HUB_RECALL_CHARS", "") or RECALL_DEFAULT_CHARS)
    except ValueError:
        chars = RECALL_DEFAULT_CHARS
    try:
        days = float(os.getenv("HUB_RECALL_DAYS", "") or RECALL_DEFAULT_DAYS)
    except ValueError:
        days = RECALL_DEFAULT_DAYS
    try:
        limit = int(os.getenv("HUB_RECALL_CANDIDATES", "") or RECALL_DEFAULT_LIMIT)
    except ValueError:
        limit = RECALL_DEFAULT_LIMIT
    raw = os.getenv("HUB_RECALL_EXCLUDE", "")
    excluded = tuple(item.strip() for item in raw.split(",") if item.strip()) if raw else RECALL_DEFAULT_EXCLUDE
    return chars, days, limit, excluded


def _trim_to_budget(events, budget: int):
    """从新到旧累计字数，超过预算就停；保证候选池大小可预期。"""
    if budget <= 0:
        return events
    kept = []
    used = 0
    for event in events:
        length = len(getattr(event, "narrative", "") or "")
        if kept and used + length > budget:
            break
        kept.append(event)
        used += length
    return kept


RECALL_DEFAULT_HALF_LIFE = 168.0  # 7 天：旧记忆也够得着，不至于被 36h 衰减压死


def _recall_half_life() -> float:
    """近因半衰期（小时）；HUB_RECALL_HALF_LIFE_HOURS 覆盖。"""
    try:
        value = float(os.getenv("HUB_RECALL_HALF_LIFE_HOURS", "") or RECALL_DEFAULT_HALF_LIFE)
    except ValueError:
        value = RECALL_DEFAULT_HALF_LIFE
    return max(1.0, value)


def _query_vector(query_embedding):
    """把查询向量转成 (np 数组, 模长)；没有就返回 (None, 0)。"""
    if not query_embedding:
        return None, 0.0
    vector = np.array(query_embedding, dtype=np.float32)
    return vector, float(np.linalg.norm(vector))


def _score_candidates(candidates, query, query_embedding, weights: _RecallWeights) -> List[RecallResult]:
    """逐条打分，低于阈值的直接丢。"""
    now_ts = time.time()
    vector, norm = _query_vector(query_embedding)
    scored: List[RecallResult] = []
    for event in candidates:
        similarity = _similarity(event, query, vector, norm)
        recency = math.exp(-max(0.0, now_ts - float(event.timestamp)) / weights.tau)
        importance = min(max(float(event.importance or 1.0) / 5.0, 0.2), 1.0)
        total = _total_score(similarity, recency, importance, weights, vector is not None or similarity > 0)
        if total >= weights.threshold:
            scored.append(RecallResult(
                event=event,
                score=round(total, 4),
                similarity=round(similarity, 4),
                recency_score=round(recency, 4),
                importance_score=round(importance, 4),
            ))
    return scored


def _similarity(event, query, vector, norm) -> float:
    """向量余弦 + 字面重叠，取较大者。"""
    similarity = _cosine(event, vector, norm)
    if query and event.narrative:
        similarity = max(similarity, _text_overlap(query, event.narrative))
    return similarity


def _cosine(event, vector, norm) -> float:
    """向量对不上（旧数据没 embedding）就当 0 分。"""
    if vector is None or norm <= 0 or not event.embedding:
        return 0.0
    try:
        candidate = np.frombuffer(event.embedding, dtype=np.float32)
        candidate_norm = np.linalg.norm(candidate)
        if candidate_norm > 0:
            return float(np.dot(vector, candidate) / (norm * candidate_norm))
    except Exception:
        pass
    return 0.0


def _text_overlap(query, narrative) -> float:
    """整句命中给 0.95；否则按字符重叠比例打折，低于 0.4 不算相关。"""
    query_clean = str(query).strip().lower()
    narrative_clean = str(narrative).lower()
    if query_clean in narrative_clean:
        return 0.95
    chars = [c for c in query_clean if c.strip() and c not in "，。！？,.!?“”：:"]
    if not chars:
        return 0.0
    ratio = sum(1 for c in chars if c in narrative_clean) / len(chars)
    return float(ratio * 0.9) if ratio >= 0.4 else 0.0


def _total_score(similarity, recency, importance, weights: _RecallWeights, has_query: bool) -> float:
    """有查询就三维加权；纯闲聊场景退化成近因 + 重要度。"""
    if has_query:
        return (
            weights.alpha * max(0.0, similarity)
            + weights.beta * recency
            + weights.gamma * importance
        )
    return 0.7 * recency + 0.3 * importance


def _apply_quota(results: List[RecallResult], limit: int, max_per_module: int) -> List[RecallResult]:
    """单模块配额：先保多样性，不够再按分数补满。"""
    if not max_per_module or max_per_module <= 0:
        return results[:limit]
    return _fill_remaining(results, _pick_diverse(results, limit, max_per_module), limit)


def _pick_diverse(results, limit, max_per_module) -> List[RecallResult]:
    """每个来源模块最多取 max_per_module 条，避免单一高频模块刷屏。"""
    picked: List[RecallResult] = []
    counts: Dict[str, int] = {}
    for item in results:
        module = item.event.source_module
        if counts.get(module, 0) >= max_per_module:
            continue
        picked.append(item)
        counts[module] = counts.get(module, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _fill_remaining(results, picked: List[RecallResult], limit: int) -> List[RecallResult]:
    """配额没填满时按分数顺序补齐。"""
    for item in results:
        if item not in picked:
            picked.append(item)
            if len(picked) >= limit:
                break
    return picked


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
        half_life_hours: Optional[float] = None,  # 记忆近因半衰期；None 用配置值
        max_per_module: int = 1,       # 单模块配额上限 (防单一高频刷屏淹没)
        include_all_actors: bool = False,
        channel_id: Optional[str] = None,
        include_channel: bool = False,
        exclude_group: bool = False,
    ) -> List[RecallResult]:
        """三维拟人召回：Score = α·Similarity + β·Recency + γ·Importance"""
        char_budget, days, candidate_limit, excluded = _recall_window()
        since_ts = time.time() - days * 86400 if days > 0 else None
        candidates = self._recall_candidates(
            person_id, actor_id, include_all_actors, channel_id, include_channel, exclude_group,
            since_ts=since_ts, limit=candidate_limit, exclude_sources=excluded,
        )
        candidates = _trim_to_budget(candidates, char_budget)
        if not candidates:
            return []
        if half_life_hours is None:
            half_life_hours = _recall_half_life()
        weights = _RecallWeights(alpha, beta, gamma, half_life_hours * 3600.0 / math.log(2), threshold)
        scored = _score_candidates(candidates, query, query_embedding, weights)
        scored.sort(key=lambda item: item.score, reverse=True)
        return _apply_quota(scored, limit, max_per_module)

    def _recall_candidates(
        self, person_id, actor_id, include_all_actors, channel_id, include_channel, exclude_group,
        *, since_ts=None, limit: int = 3000, exclude_sources=None,
    ) -> list:
        """按 person / actor + 时间窗取候选，再打分；不按条数截断。"""
        kwargs: Dict[str, Any] = {
            "actor_ids": _allowed_actor_ids(actor_id, person_id, include_all_actors),
            "person_ids": [str(person_id)] if person_id else None,
            "exclude_group": exclude_group,
            "since_ts": since_ts,
            "exclude_sources": list(exclude_sources or ()),
            "limit": limit,
        }
        if include_channel and channel_id:
            kwargs["source_modules"] = [channel_id]
        return self.store.list_candidates(**kwargs)

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
