# -*- coding: utf-8 -*-
"""逐轮决策明细落盘（默认关闭）。

开启方式：data/tool_shelves.json → gateway.log_decisions = true
文件：<项目>/data/tool_shelves/decisions.jsonl（5MB × 3 滚动）
用于排查"这一轮为什么没给工具"，不做聚合统计（聚合看 metrics.json）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_LOGGER: Optional[logging.Logger] = None


def _logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger("ToolShelves.Decisions")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        _LOGGER = logger
        return logger

    env_dir = os.getenv("TOOL_SHELVES_LOG_DIR", "").strip()
    target = Path(env_dir) if env_dir else (BASE_DIR / "data" / "tool_shelves")
    try:
        target.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target / "decisions.jsonl",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


def log_decision(record: Dict[str, Any]) -> None:
    """写一行 JSONL；异常静默，绝不影响主流程。"""
    try:
        payload = dict(record or {})
        payload.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        _logger().info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


__all__ = ["log_decision"]
