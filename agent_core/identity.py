# -*- coding: utf-8 -*-
"""
Stellaris Core - 统一身份映射系统 (Identity Resolver)
通用实体身份解析与认主中枢，支持自定义配置与环境变量覆盖
"""

import json
import os
from pathlib import Path
from typing import Optional, Set, Tuple

from .persona_identity import get_persona_name

# 身份凭证不写死在源码里：优先环境变量，其次 data/identity.json（已被 .gitignore 忽略）
_IDENTITY_FILE = Path(__file__).resolve().parent.parent / "data" / "identity.json"


def _local_identity() -> dict:
    try:
        if _IDENTITY_FILE.is_file():
            data = json.loads(_IDENTITY_FILE.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


_LOCAL = _local_identity()

# 主人 (Master) 身份凭证池 (优先环境变量，支持自定义覆盖)
MASTER_ID = os.getenv("MASTER_ID", "master")
MASTER_NAME = os.getenv("MASTER_NAME") or str(_LOCAL.get("master_name") or "主人")
MASTER_QQ = os.getenv("MASTER_QQ") or str(_LOCAL.get("master_qq") or "")

# 平台 UID 别名：默认只认 MASTER_QQ。
# 历史版本内置过 admin / leque 这类通用英文词，任何平台把 UID 设成它们就能
# 直接成为主人（审计 P1-5），因此这里不再提供任何通用词兜底，
# 需要别名请在 MASTER_ALIASES 里显式配置。
MASTER_ALIASES: Set[str] = {
    x.strip().lower()
    for x in (os.getenv("MASTER_ALIASES") or "").split(",")
    if x.strip()
}
if MASTER_QQ:
    MASTER_ALIASES.add(MASTER_QQ.strip().lower())

# 智能体自身 (Self) 身份凭证池：名称从人设卡读取，环境变量可覆盖
BOT_NAME = (os.getenv("BOT_NAME") or "").strip() or get_persona_name()
BOT_ALIASES: Set[str] = {
    x.strip().lower()
    for x in os.getenv(
        "BOT_ALIASES",
        ",".join(["self", "anan", "bot"] + [str(x) for x in (_LOCAL.get("bot_aliases") or [])]),
    ).split(",")
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

        # 1. 判定是否为主人 (Master)：只认中枢自己的人和显式配置的 UID
        if raw_id and (low_id == MASTER_ID.lower() or low_id in MASTER_ALIASES):
            return "master", (display_name or MASTER_NAME), "admin"

        # 2. 判定是否为智能体自身 (Self)
        persona_name = get_persona_name()
        if raw_id and (low_id in BOT_ALIASES or low_id == persona_name.lower()):
            return "self", (display_name or persona_name), "self"

        # 3. 其他访客/交互对象
        if not raw_id:
            return "guest", (display_name or "访客"), "visitor"
        name = display_name or f"访客_{raw_id[-4:] if len(raw_id) >= 4 else raw_id}"
        return f"user_{raw_id}", name, "member"

    @classmethod
    def is_master(cls, actor_id: str) -> bool:
        if not actor_id:
            return False
        value = str(actor_id).strip()
        return value.lower() == MASTER_ID.lower() or value.lower() in MASTER_ALIASES

    @classmethod
    def get_master_id(cls) -> str:
        return MASTER_ID

    @classmethod
    def get_master_qq(cls) -> str:
        return MASTER_QQ

    @classmethod
    def get_master_name(cls) -> str:
        return MASTER_NAME

    @classmethod
    def get_bot_name(cls) -> str:
        return get_persona_name()
