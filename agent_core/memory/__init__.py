# -*- coding: utf-8 -*-
"""
Stellaris Core - 记忆中枢统一门面 (MemoryHub)

记忆分成三块并列的东西：
1. 时间轴 (Timeline / EpisodicStream):
   L1 调用索引 + L2 事件摘要，按时间记录“发生了什么”，
   带艾宾浩斯遗忘曲线与三维拟人召回 (Similarity + Recency + Importance)
2. 记忆库 (MemoryLibrary):
   只存 L3 详情（原话、工具参数、结果），默认不注入提示词
3. 画像 (Profile):
   事实 / 档案 / 好感 / 日记，放在 agent_core.profile

MemoryTools (Letta / MemGPT 风格): 大脑原生自主记忆管理工具
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..identity import IdentityResolver
from ..models import RecallResult, TimelineEvent, get_cst_now_str
from ..profile import ProfileManager
from ..storage import DEFAULT_DB_PATH, Store, get_store
from .episodic_stream import EpisodicStream
from .fact_manager import FactManager
from .library import MemoryLibrary
from .memory_tools import register_memory_tools
from .reflection_engine import ReflectionEngine
from .storage import StorageDAL


class MemoryHub:
    """
    记忆总控中心 (Facade)
    对外统一入口，对内区分三条线：
      - self.timeline : 时间轴（L1 索引 + L2 摘要）
      - self.library  : 记忆库（L3 详情）
      - self.profile  : 画像（事实 / 档案 / 好感 / 日记）
    """

    def __init__(self, db_path: Optional[Path] = None, store: Optional[Store] = None):
        self.store = store or get_store(db_path)
        self.db_path = self.store.path
        self.dal = self.store  # 兼容旧属性名

        # 时间轴
        self.timeline = EpisodicStream(self.store)
        self.episodic = self.timeline  # 兼容旧属性名

        # 记忆库（L3 详情）
        self.library = MemoryLibrary(self.store)

        # 画像（事实 / 档案 / 好感 / 日记）
        self.profile = ProfileManager(self.store)
        self.facts = self.profile.facts  # 兼容旧属性名
        self.reflection = self.profile.diary  # 兼容旧属性名

    # ================= 自传经历时间轴流水 (Episodic) =================

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
        """追加一条连续经历，并根据 emotional_impact 联动好感度 (支持会话折叠与插件标签)"""
        evt = self.episodic.append_event(
            source_module=source_module,
            actor_id=actor_id,
            narrative=narrative,
            actor_name=actor_name,
            emotional_impact=emotional_impact,
            importance=importance,
            embedding=embedding,
            embedding_bytes=embedding_bytes,
            detail_id=detail_id,
            prev_event_id=prev_event_id,
            causal_relation=causal_relation,
            metadata=metadata,
            timestamp=timestamp,
            session_window_seconds=session_window_seconds,
            plugin_id=plugin_id,
            tag_primary=tag_primary,
            tag_secondary=tag_secondary,
            segment_id=segment_id,
            started_at=started_at,
            ended_at=ended_at,
            visibility=visibility,
            person_id=person_id,
        )

        # 好感度联动（画像层负责档案解析）
        if emotional_impact != 0 and actor_id in ("master", IdentityResolver.get_master_qq()):
            self.profile.update_affection(
                user_id=actor_id,
                delta=emotional_impact,
                reason=f"事件联动[{source_module}]: {narrative[:30]}"
            )

        return evt

    def recall_timeline(
        self,
        query: Optional[str] = None,
        query_embedding: Optional[List[float]] = None,
        actor_id: Optional[str] = "master",
        person_id: Optional[str] = None,
        limit: int = 3,
        threshold: float = 0.40,
        alpha: float = 0.45,
        beta: float = 0.35,
        gamma: float = 0.20,
        half_life_hours: float = 36.0,
        max_per_module: int = 1,
        include_all_actors: bool = False,
        channel_id: Optional[str] = None,
        include_channel: bool = False,
        exclude_group: bool = False,
    ) -> List[RecallResult]:
        """三维拟人召回模型：Score = α·Similarity + β·Recency + γ·Importance (带单模块配额保护)"""
        return self.episodic.recall_timeline(
            query=query,
            query_embedding=query_embedding,
            actor_id=actor_id,
            person_id=person_id,
            limit=limit,
            threshold=threshold,
            alpha=alpha,
            beta=beta,
            gamma=gamma,
            half_life_hours=half_life_hours,
            max_per_module=max_per_module,
            include_all_actors=include_all_actors,
            channel_id=channel_id,
            include_channel=include_channel,
            exclude_group=exclude_group,
        )

    def get_surrounding_events(
        self,
        event_id: str,
        before_n: int = 2,
        after_n: int = 2
    ) -> List[TimelineEvent]:
        """顺藤摸瓜时序能力：围绕指定事件节点展开前后关联事件"""
        return self.episodic.get_surrounding_events(event_id, before_n=before_n, after_n=after_n)

    def get_recent_stream(
        self,
        limit: int = 10,
        since_ts: Optional[float] = None,
        source_module: Optional[str] = None,
        actor_id: Optional[str] = None,
        diverse: bool = True
    ) -> List[TimelineEvent]:
        """获取最近发生的连续经历流 (diverse=True 时启用多通道多样性防淹没与连续同源折叠)"""
        return self.episodic.get_recent_stream(
            limit=limit,
            since_ts=since_ts,
            source_module=source_module,
            actor_id=actor_id,
            diverse=diverse
        )

    # ================= 用户长效事实与画像好感 (Semantic Facts & Profile) =================

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户档案/画像（经档案解析）"""
        return self.profile.get_profile(user_id)

    def get_user_affection(self, user_id: str):
        """获取用户好感度 (0~100)；None 表示尚未形成印象。"""
        return self.profile.get_affection(user_id)

    def set_user_affection(self, user_id: str, value: int, reason: str = "") -> int:
        """直接设定好感度（首次交流后定初始值）。"""
        return self.profile.set_affection(user_id, value, reason)

    def update_user_affection(self, user_id: str, delta: int, reason: str = "") -> int:
        """调整用户好感度"""
        return self.profile.update_affection(user_id, delta, reason)

    def add_user_fact(
        self, user_id: str, fact: str, category: str = "preference", source: str = "manual"
    ) -> str:
        """添加原子长效事实"""
        return self.profile.add_fact(user_id, fact, category=category, source=source)

    def get_active_facts(self, user_id: str, category: Optional[str] = None, limit: int = 15) -> List[Dict[str, Any]]:
        """获取当前生效的原子事实"""
        return self.profile.get_active_facts(user_id, category=category, limit=limit)

    def supersede_fact(
        self, old_fact_id: str, new_content: str, category: str = "preference", source: str = "manual"
    ) -> str:
        """更替旧事实 (Mem0 风格自愈覆盖)"""
        return self.profile.supersede_fact(old_fact_id, new_content, category=category, source=source)

    # ================= 周期反思与长程日记 (Reflection & Digest) =================

    def consolidate_episodes(self, min_events: int = 5, older_than_hours: float = 12.0) -> Optional[Dict[str, Any]]:
        """周期性反思提炼，将高频琐碎经历固化为日记节点"""
        return self.profile.consolidate_episodes(min_events=min_events, older_than_hours=older_than_hours)

    def recall_digests(self, query: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        """长程检索：跨越日/周/月跨度检索经历日记"""
        return self.profile.recall_digests(query=query, limit=limit)

    # ================= 跨通道统一 Token 账本与审计 =================

    def record_token_usage(
        self,
        session_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
        cached_tokens: int = 0,
        call_duration_ms: int = 0,
        cost_usd: float = 0.0,
        source: Optional[str] = None,
        credential_type: str = "",
        currency: str = "USD",
    ) -> int:
        """记录大模型推导与 Token 消耗流水（统一写入 token_usage）"""
        return self.store.record_usage(
            session_type=session_type,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            call_duration_ms=call_duration_ms,
            cost_usd=cost_usd,
            source=source or session_type,
            credential_type=credential_type,
            currency=currency,
        )

    def query_token_usage(self, limit: int = 50, session_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询 Token 消耗账本明细"""
        return self.store.query_usage(limit=limit, session_type=session_type)


# 100% 平滑兼容别名
TimelineHub = MemoryHub

__all__ = [
    "DEFAULT_DB_PATH",
    "StorageDAL",
    "EpisodicStream",
    "FactManager",
    "MemoryLibrary",
    "ProfileManager",
    "ReflectionEngine",
    "MemoryHub",
    "TimelineHub",
    "register_memory_tools",
]
