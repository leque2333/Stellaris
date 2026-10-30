# -*- coding: utf-8 -*-
"""访问策略：白名单 / 临时访客 / 访客时间轴。

默认只允许白名单档案；临时访客与访客时间轴默认关闭。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

DEFAULT_POLICY: Dict[str, Any] = {
    "guest_enabled": False,
    "guest_timeline_enabled": False,
    "guest_default_affection": 50,
}

_lock = threading.Lock()


def _path() -> Path:
    raw = os.getenv("HUB_ACCESS_POLICY_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "data" / "access_policy.json"


def load_policy() -> Dict[str, Any]:
    path = _path()
    policy = dict(DEFAULT_POLICY)
    if path.exists():
        try:
            with _lock:
                data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                policy.update(data)
        except Exception:
            pass
    policy["guest_enabled"] = bool(policy.get("guest_enabled", False))
    policy["guest_timeline_enabled"] = bool(policy.get("guest_timeline_enabled", False))
    try:
        policy["guest_default_affection"] = max(
            0, min(100, int(policy.get("guest_default_affection", 50)))
        )
    except (TypeError, ValueError):
        policy["guest_default_affection"] = 50
    return policy


def save_policy(data: Dict[str, Any]) -> Dict[str, Any]:
    current = load_policy()
    if "guest_enabled" in data:
        current["guest_enabled"] = bool(data["guest_enabled"])
    if "guest_timeline_enabled" in data:
        current["guest_timeline_enabled"] = bool(data["guest_timeline_enabled"])
    if "guest_default_affection" in data:
        try:
            current["guest_default_affection"] = max(
                0, min(100, int(data["guest_default_affection"]))
            )
        except (TypeError, ValueError):
            pass
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return current
