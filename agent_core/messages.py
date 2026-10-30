# -*- coding: utf-8 -*-
"""进程内消息队列：只保留"这次启动以后"发生的事。

控制台顶部那个消息图标读的就是这里。进程重启即清空，
所以不会把历史噪音带给用户。

级别：
    info  普通（紫色）
    warn  警告（黄色）
    error 错误（红色）
"""

from __future__ import annotations

import threading
import time
from collections import deque

_MAX = 300
_LOCK = threading.Lock()
_START_TS = time.time()
_MESSAGES: deque = deque(maxlen=_MAX)


def push(level: str, text: str) -> None:
    """记一条消息。level 取 info / warn / error。"""
    level = str(level or "info").lower()
    if level not in ("info", "warn", "error"):
        level = "info"
    body = str(text or "").strip()
    if not body:
        return
    with _LOCK:
        _MESSAGES.append({
            "ts": time.time(),
            "level": level,
            "text": body[:600],
        })


def info(text: str) -> None:
    push("info", text)


def warn(text: str) -> None:
    push("warn", text)


def error(text: str) -> None:
    push("error", text)


def snapshot(limit: int = 200) -> dict:
    """返回本次启动以来的消息（新的在后面）。"""
    with _LOCK:
        items = list(_MESSAGES)[-max(1, limit):]
    counts = {"info": 0, "warn": 0, "error": 0}
    for item in items:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    return {
        "started_at": _START_TS,
        "uptime_seconds": int(time.time() - _START_TS),
        "counts": counts,
        "total": len(items),
        "messages": items,
    }


def clear() -> None:
    with _LOCK:
        _MESSAGES.clear()


__all__ = ["push", "info", "warn", "error", "snapshot", "clear"]
