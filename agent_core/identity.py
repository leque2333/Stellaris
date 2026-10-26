# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 统一身份映射系统 (Identity Resolver)
打通跨平台实体绑定 (QQ / B站 / Telegram / 控制台 / 宿主机PC)，消除平台ID硬编码
"""

import os
from typing import Dict, Optional, Tuple

# 主人 (Master) 跨平台绑定凭证池 (优先环境变量，支持灵活配置)
MASTER_QQ = os.getenv("MASTER_QQ", "1000000001")
MASTER_BILI_UID = os.getenv("MASTER_BILI_UID", "3000000002")
MASTER_TG_IDS = {os.getenv("MASTER_TG_ID", "master"), "master", "leque"}

# 智能体自身跨平台账号凭证
ANAN_QQ = os.getenv("BOT_QQ", "1000000002")
ANAN_BILI_UID = os.getenv("BOT_BILI_UID", "3000000001")


class IdentityResolver:
    """
    统一实体身份解析器
    """
    @staticmethod
    def resolve_actor(
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None
    ) -> Tuple[str, str, str]:
        """
        根据平台与原生用户ID，统一解析为标准智能体实体
        返回: (actor_id, actor_name, role_type)
        - actor_id: "master" | "self" | "user_{id}"
        - actor_name: 展示昵称 (如 "主人", "助手", 或原名)
        - role_type: "admin" | "self" | "member"
        """
        raw_id = str(platform_user_id or "").strip()

        # 1. 判定是否为主人 (主人)
        if (
            (platform in ("qq", "onebot") and raw_id == MASTER_QQ)
            or (platform == "bilibili" and raw_id == MASTER_BILI_UID)
            or (platform == "telegram" and raw_id in MASTER_TG_IDS)
            or raw_id in ("master", "leque", "admin")
        ):
            return "master", (display_name or "主人"), "admin"

        # 2. 判定是否为助手自身
        if (
            (platform in ("qq", "onebot") and raw_id == ANAN_QQ)
            or (platform == "bilibili" and raw_id == ANAN_BILI_UID)
            or raw_id in ("self", "anan")
        ):
            return "self", (display_name or "助手"), "self"

        # 3. 其他群友/普通访客
        name = display_name or f"访客_{raw_id[-4:] if len(raw_id) >= 4 else raw_id}"
        return f"user_{raw_id}", name, "member"

    @staticmethod
    def get_master_qq() -> str:
        return MASTER_QQ

    @staticmethod
    def is_master(actor_id: str) -> bool:
        return actor_id in ("master", MASTER_QQ)
