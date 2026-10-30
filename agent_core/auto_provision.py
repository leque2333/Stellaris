# -*- coding: utf-8 -*-
"""
陌生模块首次通过 /v1 接入时，自动登记为「模块」并补一张宿主信息卡。

- 只认显式身份头：X-Host-Id / X-Module-Id；
- 不再按 User-Agent 识别任何平台；
- 不再登记 OpenAI 客户端；
- 每个 host_id 只自动登记一次；后续请求不再扫描。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import unquote

_INFLIGHT = set()
_DONE = set()
_LOCK = threading.Lock()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(value or "")).strip("_")
    return (cleaned or "module")[:48]


def detect_host(headers: Any) -> Optional[Dict[str, str]]:
    """从请求头判断这是哪个陌生模块；认不出就返回 None。"""
    get = getattr(headers, "get", lambda _k, _d=None: None)

    host_id = str(get("X-Host-Id") or get("X-Module-Id") or "").strip()
    if host_id:
        raw_name = str(get("X-Host-Name") or host_id)
        return {
            "host_id": _slug(host_id),
            "name": unquote(raw_name) if "%" in raw_name else raw_name,
            "hint": "header",
        }

    return None


def _ensure_context(store: Any, host_id: str, name: str, platform: str, uid: str) -> None:
    """给模块自己的宿主身份补一张信息卡；否则档案有身份、模块详情却是空的。"""
    platform = str(platform or "").strip()
    uid = str(uid or "").strip()
    if not platform or not uid or uid == host_id:
        return
    try:
        store.upsert_context(
            platform,
            uid,
            external_type="host",
            display_name=str(name or host_id),
            source="auto_provision",
            host_id=host_id,
        )
    except Exception:
        pass


def provision_async(store: Any, headers: Any) -> None:
    """首次见到陌生模块时，后台登记一次。"""
    if store is None:
        return
    hint = detect_host(headers)
    if not hint:
        return
    host_id = hint["host_id"]
    try:
        existing = next(
            (h for h in store.list_hosts() if str(h.get("host_id") or "") == host_id),
            None,
        )
        if existing:
            # 第一次可能没有带 X-Host-UID，后来模块补上真实 UID 时更新信息卡
            get = getattr(headers, "get", lambda _k, _d=None: None)
            new_uid = str(get("X-Host-UID") or "").strip()
            old_uid = str(existing.get("internal_uid") or "").strip()
            name = str(existing.get("name") or hint.get("name") or host_id)
            platform = str(existing.get("platform") or get("X-Platform") or "").strip()
            if (
                new_uid
                and str(existing.get("source") or "") == "auto_v1"
                and old_uid in ("", host_id)
            ):
                store.upsert_host(
                    host_id,
                    name,
                    platform=platform,
                    internal_uid=new_uid,
                    root=str(existing.get("root") or ""),
                    source="auto_v1",
                    manifest=dict(existing.get("manifest") or {}),
                )
                old_uid = new_uid
            _ensure_context(store, host_id, name, platform, old_uid)
            return
    except Exception:
        pass

    with _LOCK:
        if host_id in _INFLIGHT or host_id in _DONE:
            return
        _INFLIGHT.add(host_id)

    threading.Thread(
        target=_run,
        args=(store, hint, headers),
        daemon=True,
        name=f"auto-provision-{host_id}",
    ).start()


def _run(store: Any, hint: Dict[str, str], headers: Any) -> None:
    host_id = hint["host_id"]
    try:
        # 通用模块：登记最小宿主 + 信息卡；平台格式交给模块自己的 manifest / AI 扫描。
        get = getattr(headers, "get", lambda _k, _d=None: None)
        name = hint.get("name") or host_id
        platform = str(get("X-Platform") or "").strip()
        uid = str(
            get("X-Host-UID") or get("X-Host-Id") or ""
        ).strip()
        store.upsert_host(
            host_id,
            name,
            platform=platform,
            internal_uid=uid,
            root="",
            source="auto_v1",
            manifest={
                "auto_provisioned": True,
                "detected_from": hint.get("hint", ""),
                "detected_at": time.time(),
            },
        )
        _ensure_context(store, host_id, name, platform, uid)
    finally:
        with _LOCK:
            _INFLIGHT.discard(host_id)
            _DONE.add(host_id)
