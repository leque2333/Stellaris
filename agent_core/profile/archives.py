# -*- coding: utf-8 -*-
"""
Stellaris Core - 人物档案层 (Person Archive)

档案是“一个人”，不是 AI：

- 管理员档案：中枢启动时必须存在（默认用主人 QQ 作为 archive_id，兼容旧画像数据）
- 用户档案：可选，必须手动创建（不会因为陌生人说话就自动建档）
- 身份映射：QQ / B站 / 微信 / CLI 等平台 ID → 同一个 archive_id
- 画像：事实 / 好感 / 日记在 profile 模块，按 archive_id 绑定
- AI 人格：不在档案里；统一由「人格设定」的全局人设卡（PersonaManager）决定

其他插件只需要拿到 archive_id，就能把本地的“同一个人”对回中枢。
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List, Optional

from ..identity import IdentityResolver
from ..storage import Store

# 这些不是“人”，不建档案
SYSTEM_ACTORS = {"system", "hub_system", "compute_matrix", "self", "anan", "bot"}


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_\-]", "_", str(value or "")).strip("_")
    return cleaned[:64] or "unknown"


class ArchiveManager:
    """人物档案注册表：管理员必须有，用户必须手动创建"""

    def __init__(self, store: Store):
        self.store = store
        self.ensure_admin()

    def _decorate(self, archive: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """档案只承载“人”的信息；旧 AI 人设字段不再对外暴露"""
        if not archive:
            return None
        data = dict(archive)
        data.pop("persona_id", None)
        data.pop("persona_sections", None)
        return data

    # ---------------- 管理员 ----------------
    def _master_archive_id(self) -> str:
        return os.getenv("HUB_MASTER_ARCHIVE_ID", "arc_master").strip() or "arc_master"

    def _alias_target(self, archive_id: str) -> str:
        if not archive_id:
            return archive_id
        try:
            with self.store._connect() as conn:
                row = conn.execute(
                    "SELECT archive_id FROM archive_aliases WHERE alias_id = ?",
                    (str(archive_id),),
                ).fetchone()
            return str(row["archive_id"]) if row else archive_id
        except Exception:
            return archive_id

    def ensure_admin(self) -> Dict[str, Any]:
        """确保管理员档案存在（独立 archive_id，不再使用 QQ 号）"""
        master_qq = IdentityResolver.get_master_qq()
        master_name = IdentityResolver.get_master_name()
        archive_id = self._master_archive_id()
        self.store.ensure_archive(archive_id, role="admin", display_name=master_name)
        archive = self.store.get_archive(archive_id)
        self.store.link_identity(
            archive_id, "qq", master_qq, display_name=master_name, is_primary=True
        )
        # B站账号不再硬编码；需要时由宿主显式设置 MASTER_BILIBILI_ID，或走模块身份映射
        bili_id = os.getenv("MASTER_BILIBILI_ID", "").strip()
        if bili_id:
            self.store.link_identity(archive_id, "bilibili", bili_id, display_name="主人2333")
        return self._decorate(archive) or {
            "archive_id": archive_id, "role": "admin", "display_name": master_name,
        }

    def admin(self) -> Dict[str, Any]:
        return self.ensure_admin()

    # ---------------- 解析 ----------------
    def resolve_actor(self, actor_id: str, platform: str = "unknown") -> Optional[Dict[str, Any]]:
        """把中枢内部的 actor_id 解析成人物档案；系统角色或未建档用户返回 None"""
        raw = str(actor_id or "").strip()
        low = raw.lower()
        if not raw or low in SYSTEM_ACTORS:
            return None

        master_qq = IdentityResolver.get_master_qq()
        if low in ("master", "admin", "owner") or raw == master_qq:
            return self.admin()

        if raw.startswith("user_"):
            return self.resolve(platform, raw[5:], display_name=None, create=False)
        if raw.startswith("guest_"):
            return self.resolve(platform, raw[6:], display_name=None, create=False)

        # platform:uid 形式（如 qq:<你的QQ>）——历史事件里出现过，
        # 不拆开就永远解析不到档案
        if ":" in raw:
            parts = raw.split(":")
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                head, uid = parts[0].strip().lower(), parts[1].strip()
                if uid == master_qq:
                    return self.admin()
                found = self.resolve(head, uid, display_name=None, create=False)
                if found:
                    return self._decorate(found)

        existing = self.store.get_archive(self._alias_target(raw))
        if existing:
            return self._decorate(existing)
        return self.resolve(platform, raw, display_name=None, create=False)

    def resolve(
        self,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None,
        *,
        create: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """按平台 ID 找人物档案；默认不自动建档（用户档案需手动创建）"""
        uid = str(platform_user_id or "").strip()
        if not uid:
            return None

        found = self.store.find_archive_by_identity(platform, uid)
        if found:
            return self._decorate(found)

        # 平台未知时，跨平台找一遍
        if platform in ("unknown", "", "web"):
            for p in ("qq", "bilibili", "wechat", "cli", "telegram"):
                found = self.store.find_archive_by_identity(p, uid)
                if found:
                    return self._decorate(found)

        master_qq = IdentityResolver.get_master_qq()
        if uid == master_qq:
            return self.admin()

        if not create:
            return None
        return self.create_user(platform, uid, display_name)

    # ---------------- 创建 ----------------
    def create_user(
        self,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None,
        role: str = "guest",
    ) -> Dict[str, Any]:
        """手动创建用户档案（绑定一个平台账号）"""
        role = "admin" if str(role) == "admin" else "guest"
        archive_id = f"arc_{_safe_id(platform)}_{_safe_id(platform_user_id)}"
        self.store.ensure_archive(
            archive_id, role=role,
            display_name=display_name or f"用户_{platform_user_id}",
        )
        self.store.link_identity(
            archive_id, platform, str(platform_user_id), display_name=display_name
        )
        return self._decorate(self.store.get_archive(archive_id)) or {
            "archive_id": archive_id,
            "role": role,
            "display_name": display_name or f"用户_{platform_user_id}",
        }

    def create_person(self, display_name: str, role: str = "guest") -> Dict[str, Any]:
        """创建一个尚未绑定平台账号的人物档案"""
        role = "admin" if str(role) == "admin" else "guest"
        clean_name = str(display_name or "").strip() or "未命名人物"
        archive_id = f"arc_person_{uuid.uuid4().hex[:8]}"
        self.store.ensure_archive(archive_id, role=role, display_name=clean_name)
        return self._decorate(self.store.get_archive(archive_id)) or {
            "archive_id": archive_id,
            "role": role,
            "display_name": clean_name,
        }

    # ---------------- 查询 / 修改 ----------------
    def get(self, archive_id: str) -> Optional[Dict[str, Any]]:
        target = self._alias_target(archive_id)
        return self._decorate(self.store.get_archive(target))

    def update(
        self,
        archive_id: str,
        *,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
        affection_min: Optional[int] = None,
        affection_max: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.store.get_archive(archive_id):
            return None
        self.store.update_archive(
            archive_id,
            display_name=display_name,
            status=status,
            affection_min=affection_min,
            affection_max=affection_max,
        )
        return self._decorate(self.store.get_archive(archive_id))

    def list_archives(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        return [self._decorate(a) for a in self.store.list_archives(role=role)]

    def identities(self, archive_id: str) -> List[Dict[str, Any]]:
        return self.store.list_identities(archive_id)

    def link(
        self,
        archive_id: str,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None,
        is_primary: bool = False,
    ) -> str:
        """把另一个平台账号挂到同一个人物档案上"""
        return self.store.link_identity(
            archive_id, platform, platform_user_id,
            display_name=display_name, is_primary=is_primary,
        )
