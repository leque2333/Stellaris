# -*- coding: utf-8 -*-
"""
Stellaris Core - 画像 (Profile)

与「时间轴」「记忆库」并列的第三块：

- 时间轴 Timeline : 按时间记录“发生了什么”（L1 调用索引 + L2 事件摘要）
- 记忆库 MemoryLibrary : 只存 L3 详情（原话、参数、结果）
- 画像 Profile : 长期稳定的“她知道的关于这个人/世界的信息”
    - 事实 facts   : 一条条结论（喜欢什么、用什么、答应过什么）
    - 档案 profile : 一个人的封面（昵称、身份、好感度、变化历史）
    - 日记 diary   : 一段时间的压缩总结（由反思引擎生成）
    - 身份映射     : 后续把 QQ / B站 / 微信 等平台 ID 归到同一个人
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..storage import Store
from ..memory.fact_manager import FactManager
from ..memory.reflection_engine import ReflectionEngine
from .archives import ArchiveManager


class ProfileManager:
    """画像门面：档案 + 事实 + 好感 + 日记"""

    def __init__(self, store: Store):
        self.store = store
        # 档案先于画像初始化：管理员档案必须存在
        self.archives = ArchiveManager(store)
        self.facts = FactManager(store)
        self.diary = ReflectionEngine(store)
        # 兼容旧命名
        self.reflection = self.diary

    # ---- 档案解析 ----
    def _aid(self, user_id: str) -> Optional[str]:
        archive = self.archives.resolve_actor(str(user_id))
        return archive["archive_id"] if archive else None

    def get_archive(self, user_id: str):
        return self.archives.resolve_actor(str(user_id))

    def list_identities(self, archive_id: str):
        return self.archives.identities(archive_id)

    # ---- 事实 ----
    def add_fact(
        self, user_id: str, content: str, category: str = "preference", source: str = "manual"
    ) -> str:
        aid = self._aid(user_id)
        if not aid:
            return ""  # 未建档用户不写入；需先手动创建档案
        return self.facts.add_fact(aid, content, category=category, source=source)

    def supersede_fact(
        self, old_fact_id: str, new_content: str, category: str = "preference", source: str = "manual"
    ) -> str:
        return self.facts.supersede_fact(old_fact_id, new_content, category=category, source=source)

    def get_active_facts(
        self, user_id: str, category: Optional[str] = None, limit: int = 15
    ) -> List[Dict[str, Any]]:
        aid = self._aid(user_id)
        return self.facts.get_active_facts(aid, category=category, limit=limit) if aid else []

    def get_facts_for_prompt(self, user_id: str, limit: int = 8) -> List[str]:
        aid = self._aid(user_id)
        return self.facts.get_facts_for_prompt(aid, limit=limit) if aid else []

    # ---- 档案 / 好感 ----
    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        aid = self._aid(user_id)
        return self.facts.get_user_profile(aid) if aid else None

    def get_affection(self, user_id: str):
        aid = self._aid(user_id)
        return self.facts.get_affection(aid) if aid else None

    def update_affection(self, user_id: str, delta: int, reason: str = "") -> int:
        aid = self._aid(user_id)
        return self.facts.update_affection(aid, delta, reason) if aid else 50

    def set_affection(self, user_id: str, value: int, reason: str = "") -> int:
        aid = self._aid(user_id)
        return self.facts.set_affection(aid, value, reason) if aid else 50

    # ---- 自定义设定（用户手填，AI 不可改） ----
    def list_settings(self, archive_id: str):
        return self.store.list_archive_settings(archive_id)

    def upsert_setting(self, archive_id: str, label: str, content: str, **kwargs):
        return self.store.upsert_archive_setting(archive_id, label, content, **kwargs)

    def delete_setting(self, archive_id: str, setting_id: str) -> bool:
        return self.store.delete_archive_setting(archive_id, setting_id)

    # ---- 日记 ----
    def consolidate_episodes(self, min_events: int = 5, older_than_hours: float = 12.0):
        return self.diary.consolidate_episodes(min_events=min_events, older_than_hours=older_than_hours)

    def recall_digests(self, query: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        return self.diary.recall_digests(query=query, limit=limit)
