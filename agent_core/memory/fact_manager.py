# -*- coding: utf-8 -*-
"""
Stellaris Core - 语义原子事实与画像管理器 (Fact Manager)
借鉴：Mem0 架构范式
职责：
1. 维护细粒度原子事实库 (user_facts)，支持时效状态追踪 (active / superseded)
2. 语义冲突自愈覆盖：新偏好与旧事实冲突时，旧事实退役并标记 supersedes_id
3. 维护并同步兼容用户偏好与好感度画像 (user_profiles)

存储细节已下沉到 agent_core.storage，这里只保留事实语义与主客体映射。
"""

import time
from typing import Any, Dict, List, Optional

from ..identity import IdentityResolver
from ..storage import Store

INITIAL_MASTER_FACTS = [
    "是当前智能体的唯一管理员与主人，在群聊与私聊中平视相处",
    "喜欢二次元与技术探索，常在群内聊明日方舟、边狱公司、前沿AI大模型等话题",
    "平时使用 Windows 宿主机与终端控制环境",
    "答应给智能体买零食和小鱼干作为动力",
]


class FactManager:
    """语义事实画像与冲突自愈管理器"""

    def __init__(self, store: Store):
        self.store = store
        self.dal = store  # 兼容旧属性名
        self._ensure_master_initialized()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _master_qq(self) -> str:
        return IdentityResolver.get_master_qq()

    def _master_aid(self) -> str:
        """主人的档案 ID（事实与好感度统一挂在档案上，不再挂 QQ 号）。"""
        try:
            from ..profile.archives import ArchiveManager

            return ArchiveManager(self.store).admin()["archive_id"]
        except Exception:
            return self._master_qq()

    def _ensure_master_initialized(self) -> None:
        """确保最高管理员的基底画像与好感度就绪（统一用档案 ID）。"""
        master_aid = self._master_aid()
        profile = self.store.get_profile(master_aid)
        if profile:
            # 旧库可能只有 profile.facts、没有原子事实；补一次，保证画像页和提示词一致
            if not self.store.get_active_facts(master_aid, limit=1):
                for fact in INITIAL_MASTER_FACTS:
                    self.add_fact(master_aid, fact, category="preference", source="legacy")
            return

        now_ts = time.time()
        self.store.insert_profile(
            user_id=master_aid,
            nickname=IdentityResolver.get_master_name(),
            role_type="admin",
            facts=list(INITIAL_MASTER_FACTS),
            affection=90,
            affection_history=[{
                "time": now_ts, "delta": 0, "old": 90, "new": 90, "reason": "契约初始好感度",
            }],
        )
        # 同步写入原子事实库（add_fact 会反写 user_profiles.facts）
        for fact in INITIAL_MASTER_FACTS:
            self.add_fact(master_aid, fact, category="preference", source="legacy")

    def _sync_profile_facts(self, user_id: str) -> None:
        """将当前生效事实回写 user_profiles.facts，保持旧版读取兼容"""
        active = self.store.get_active_facts(user_id, limit=30)
        self.store.set_profile_facts(user_id, [f["content"] for f in active])

    def _normalize_uid(self, user_id: str) -> str:
        """把 master / 管理员 QQ 统一映射到主人档案 ID。"""
        raw = str(user_id or "").strip()
        master_qq = self._master_qq()
        if raw.lower() in ("master", "admin", "owner") or raw == master_qq:
            return self._master_aid()
        return raw

    # ------------------------------------------------------------------
    # 事实
    # ------------------------------------------------------------------
    def add_fact(
        self,
        user_id: str,
        content: str,
        category: str = "preference",
        supersedes_id: Optional[str] = None,
        source: str = "manual",
        confidence: float = 1.0,
        status: Optional[str] = None,
    ) -> str:
        uid = self._normalize_uid(user_id)
        fact_id = self.store.add_fact(
            uid, content, category=category, supersedes_id=supersedes_id,
            source=source, confidence=confidence, status=status,
        )
        self._sync_profile_facts(uid)
        return fact_id

    def supersede_fact(
        self, old_fact_id: str, new_content: str, category: str = "preference", source: str = "manual"
    ) -> str:
        """语义冲突自愈：旧事实失效，新事实建立 supersedes_id 指针"""
        new_id = self.store.supersede_fact(old_fact_id, new_content, category=category, source=source)
        self._sync_profile_facts(self._master_aid())
        return new_id

    def get_active_facts(
        self, user_id: str, category: Optional[str] = None, limit: int = 15
    ) -> List[Dict[str, Any]]:
        return self.store.get_active_facts(self._normalize_uid(user_id), category=category, limit=limit)

    def get_facts_for_prompt(self, user_id: str, limit: int = 8) -> List[str]:
        return self.store.get_facts_for_prompt(self._normalize_uid(user_id), limit=limit)

    # ------------------------------------------------------------------
    # 好感度 / 画像（兼容旧 API 命名）
    # ------------------------------------------------------------------
    def get_affection(self, user_id: str):
        """好感度；None 表示"还没交流过，未定"。"""
        uid = self._normalize_uid(user_id)
        return self.store.get_affection(uid)

    def update_affection(self, user_id: str, delta: int, reason: str = "") -> int:
        uid = self._normalize_uid(user_id)
        return self.store.update_affection(uid, delta, reason)

    def set_affection(self, user_id: str, value: int, reason: str = "") -> int:
        """直接设定好感度（首次交流后 AI 给初始值用）。"""
        uid = self._normalize_uid(user_id)
        return self.store.set_affection(uid, value, reason)

    def get_user_affection(self, user_id: str) -> int:
        return self.get_affection(user_id)

    def update_user_affection(self, user_id: str, delta: int, reason: str = "") -> int:
        return self.update_affection(user_id, delta, reason)

    def add_user_fact(self, user_id: str, fact: str, source: str = "manual") -> str:
        return self.add_fact(user_id, fact, category="preference", source=source)

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_profile(self._normalize_uid(user_id))

    def list_settings(self, archive_id: str) -> List[Dict[str, Any]]:
        return self.store.list_archive_settings(archive_id)

    def upsert_setting(self, archive_id: str, label: str, content: str, **kwargs) -> Dict[str, Any]:
        return self.store.upsert_archive_setting(archive_id, label, content, **kwargs)

    def delete_setting(self, archive_id: str, setting_id: str) -> bool:
        return self.store.delete_archive_setting(archive_id, setting_id)
