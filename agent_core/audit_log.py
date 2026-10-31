# -*- coding: utf-8 -*-
"""安全审计日志：登录、密钥轮换、访问限制、签名拒绝都记到这里。

时间轴（agent_timeline）是给 AI 看的记忆，运维事件不该混进去，
所以单独写 logs/audit.log，JSON Lines，一行一条。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_BYTES = 5 * 1024 * 1024
_LOCK = threading.Lock()
_DEDUPE: Dict[str, float] = {}
DEDUPE_SECONDS = 60.0


def log_path() -> Path:
    env = os.getenv("HUB_AUDIT_LOG", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "logs" / "audit.log"


def record(event: str, level: str = "info", dedupe: bool = True, **fields: Any) -> Optional[Dict[str, Any]]:
    """追加一条审计记录；可去重的按 (event, ip, error) 60 秒内只记一次，防止刷盘。"""
    key = f"{event}|{fields.get('ip', '')}|{fields.get('error', '')}"
    now = time.time()
    with _LOCK:
        if dedupe and DEDUPE_SECONDS > 0 and key in _DEDUPE and now - _DEDUPE[key] < DEDUPE_SECONDS:
            return None
        if dedupe:
            _DEDUPE[key] = now
        entry = {"ts": int(now), "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                 "event": event, "level": level, **fields}
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_big(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return None
    return entry


def _rotate_if_big(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def tail(limit: int = 200) -> List[Dict[str, Any]]:
    """读最近 N 条（最新的在前）。"""
    path = log_path()
    if not path.exists():
        return []
    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line)
                if len(lines) > limit * 2:
                    lines = lines[-limit:]
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def reset_dedupe() -> None:
    """测试用：清掉去重窗口。"""
    with _LOCK:
        _DEDUPE.clear()
