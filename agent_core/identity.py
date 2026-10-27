# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 统一身份映射系统 (Identity Resolver)
通用实体身份解析与认主中枢，支持自定义配置与环境变量覆盖
"""

import os
from typing import Optional, Set, Tuple

# 主人 (Master) 身份凭证池 (优先环境变量，支持自定义覆盖)
MASTER_ID = os.getenv("MASTER_ID", "master")
MASTER_NAME = os.getenv("MASTER_NAME", "主人")
MASTER_QQ = os.getenv("MASTER_QQ", "1000000001")
MASTER_ALIASES: Set[str] = {
    x.strip().lower()
    for x in os.getenv("MASTER_ALIASES", f"master,admin,leque,{MASTER_QQ},3000000002").split(",")
    if x.strip()
}

# 智能体自身 (Self) 身份凭证池
BOT_NAME = os.getenv("BOT_NAME", "助手")
BOT_ALIASES: Set[str] = {
    x.strip().lower()
    for x in os.getenv("BOT_ALIASES", "self,anan,bot,1000000002,3000000001").split(",")
    if x.strip()
}


class IdentityResolver:
    """
    通用实体身份解析器
    """
    @classmethod
    def resolve_actor(
        cls,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None
    ) -> Tuple[str, str, str]:
        """
        统一解析为标准智能体实体
        返回: (actor_id, actor_name, role_type)
        - actor_id: "master" | "self" | "user_{id}"
        - actor_name: 展示昵称
        - role_type: "admin" | "self" | "member"
        """
        raw_id = str(platform_user_id or "").strip()
        low_id = raw_id.lower()

        # 1. 判定是否为主人 (Master)
        if low_id in MASTER_ALIASES:
            return "master", (display_name or MASTER_NAME), "admin"

        # 2. 判定是否为智能体自身 (Self)
        if low_id in BOT_ALIASES:
            return "self", (display_name or BOT_NAME), "self"

        # 3. 其他访客/交互对象
        name = display_name or f"访客_{raw_id[-4:] if len(raw_id) >= 4 else raw_id}"
        return f"user_{raw_id}", name, "member"

    @classmethod
    def is_master(cls, actor_id: str) -> bool:
        if not actor_id:
            return False
        return str(actor_id).strip().lower() in MASTER_ALIASES

    @classmethod
    def get_master_id(cls) -> str:
        return MASTER_ID

    @classmethod
    def get_master_qq(cls) -> str:
        return MASTER_QQ

    @classmethod
    def get_master_name(cls) -> str:
        return MASTER_NAME
