# -*- coding: utf-8 -*-
"""把身份映射落到「发送者 UID」：只认宿主传来的底层用户 UID，不做格式猜测

宿主必须传 X-User-Id（底层用户 UID）。不要再从 UMO、群号、后缀里猜人：
那些是二级 ID，猜错会把群/插件账号当成人。
"""

from __future__ import annotations

from typing import Any, Optional


def resolve_archive_for_sender(
    store: Any,
    platform: str,
    sender_id: str = "",
    external_id: str = "",
) -> Optional[str]:
    """按底层发送者 UID 精确找人设档案；platform 为空时跨平台精确匹配"""
    platform = str(platform or "").strip()
    keys = [str(key or "").strip() for key in (sender_id, external_id)]
    keys = [key for key in keys if key]
    if not keys:
        return None
    if platform:
        for key in keys:
            try:
                archive_id = store.resolve_binding(platform, key)
            except Exception:
                archive_id = None
            if archive_id:
                return archive_id
        return None
    try:
        bindings = store.list_bindings()
    except Exception:
        return None
    for binding in bindings:
        if str(binding.get("external_id") or "") in keys:
            return str(binding.get("archive_id") or "") or None
    return None


def infer_platform(channel_id: str = "", external_id: str = "") -> str:
    """从频道/外部键前缀取平台；仅用于补 X-Platform，不参与身份判断"""
    channel = str(channel_id or "").strip().lower()
    external = str(external_id or "").strip().lower()
    for prefix, platform in (
        ("qq_group", "qq"),
        ("qq:", "qq"),
        ("onebot", "qq"),
        ("telegram", "telegram"),
        ("discord", "discord"),
        ("bilibili", "bilibili"),
    ):
        if channel.startswith(prefix) or external.startswith(prefix):
            return platform
    return ""
