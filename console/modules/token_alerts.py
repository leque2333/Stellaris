# -*- coding: utf-8 -*-
"""Token 预警：余额提醒 + 按最近几天用量推算的日 token 上限。"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULTS = {
    "enabled": True,
    "balance_threshold": 5.0,
    "balance_thresholds": {},
    "lookback_days": 7,
    "limit_multiplier": 1.5,
    "min_samples": 3,
}
RECHARGE_SOURCES = ("人工充值", "账户充值", "系统注资")


def _data_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "data"


def _settings_path() -> Path:
    raw = os.getenv("HUB_TOKEN_ALERTS_PATH", "")
    return Path(raw) if raw else _data_dir() / "token_alerts.json"


def _state_path() -> Path:
    raw = os.getenv("HUB_TOKEN_ALERTS_STATE", "")
    return Path(raw) if raw else _data_dir() / "token_alert_state.json"


def load_settings() -> Dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        saved = json.loads(_settings_path().read_text(encoding="utf-8-sig"))
        if isinstance(saved, dict):
            data.update(saved)
    except Exception:
        pass
    return data


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_settings()
    for key in DEFAULTS:
        if key in patch:
            data[key] = patch[key]
    data["enabled"] = bool(data.get("enabled", True))
    data["balance_threshold"] = max(0.0, float(data.get("balance_threshold", 5.0)))
    thresholds = data.get("balance_thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
    data["balance_thresholds"] = {
        str(key).upper(): max(0.0, float(value))
        for key, value in thresholds.items()
        if str(key).strip()
    }
    data["lookback_days"] = max(1, min(int(data.get("lookback_days", 7)), 30))
    data["limit_multiplier"] = max(1.0, min(float(data.get("limit_multiplier", 1.5)), 5.0))
    data["min_samples"] = max(1, min(int(data.get("min_samples", 3)), 14))
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return data


def _load_state() -> Dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _today() -> str:
    return datetime.date.today().isoformat()


def _db_path(store=None) -> Path:
    if store is not None and getattr(store, "path", None):
        return Path(store.path)
    from agent_core.storage import get_store

    return Path(get_store().path)


def _config() -> Dict[str, Any]:
    try:
        from .model_service import get_token_analytics

        data = get_token_analytics() or {}
        return {"credits": data.get("credits") or {}}
    except Exception:
        return {}


def _rows(db_path: Path, lookback_days: int) -> list:
    since = time.time() - (lookback_days + 1) * 86400
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT timestamp, prompt_tokens, completion_tokens, cached_tokens, cost_usd, source "
            "FROM token_usage WHERE timestamp >= ? "
            "ORDER BY timestamp",
            (since,),
        ).fetchall()
    finally:
        con.close()
    days: Dict[str, Dict[str, float]] = {}
    for row in rows:
        if str(row["source"] or "") in RECHARGE_SOURCES:
            continue
        day = datetime.datetime.fromtimestamp(float(row["timestamp"] or 0)).date().isoformat()
        bucket = days.setdefault(day, {"tokens": 0.0, "cost": 0.0})
        bucket["tokens"] += float(row["prompt_tokens"] or 0) + float(row["completion_tokens"] or 0)
        bucket["cost"] += float(row["cost_usd"] or 0)
    return [
        {"date": day, "tokens": int(values["tokens"]), "cost": round(values["cost"], 6)}
        for day, values in sorted(days.items())
    ]


def evaluate(store=None, config: Optional[Dict[str, Any]] = None, mark: bool = False) -> Dict[str, Any]:
    settings = load_settings()
    today = _today()
    daily = _rows(_db_path(store), int(settings["lookback_days"]))
    history = [item for item in daily if item["date"] != today]
    today_tokens = next((item["tokens"] for item in daily if item["date"] == today), 0)
    samples = [item["tokens"] for item in history if item["tokens"] > 0]
    limit = None
    if samples:
        avg = statistics.mean(samples)
        spread = statistics.pstdev(samples) if len(samples) > 1 else 0.0
        limit = int(max(avg * float(settings["limit_multiplier"]), avg + 2 * spread))
    cfg = config if isinstance(config, dict) else _config()
    credits = cfg.get("credits") or {}
    balances = credits.get("balances")
    if not isinstance(balances, list) or not balances:
        balances = [{
            "currency": str(credits.get("currency") or "USD"),
            "total": float(credits.get("balance", 0.0) or 0.0),
            "is_primary": True,
        }]
    thresholds = settings.get("balance_thresholds") or {}
    alerts = []
    balance_rows = []
    if settings.get("enabled", True):
        for item in balances:
            currency = str(item.get("currency") or "USD").upper()
            total = float(item.get("total", 0.0) or 0.0)
            threshold = float(thresholds.get(currency, settings["balance_threshold"]))
            balance_rows.append({
                "currency": currency,
                "total": round(total, 6),
                "threshold": round(threshold, 6),
                "is_primary": bool(item.get("is_primary")),
            })
            if total <= threshold:
                alerts.append({
                    "type": f"balance:{currency}",
                    "level": "critical" if total <= 0 else "warning",
                    "message": f"{currency} 余额 {total:.2f}，低于阈值 {threshold:.2f}",
                })
        if limit and len(samples) >= int(settings["min_samples"]) and today_tokens > limit:
            alerts.append({
                "type": "token",
                "level": "warning",
                "message": f"今日 {today_tokens:,} tokens，超过动态上限 {limit:,}",
            })
    state = _load_state()
    notified_types = set(state.get("types") or []) if state.get("date") == today else set()
    active_types = {item["type"] for item in alerts}
    notified_today = bool(active_types) and active_types <= notified_types
    if mark and alerts and not notified_today:
        _save_state({"date": today, "types": sorted(notified_types | active_types)})
        notified_today = True
    primary = next((item for item in balance_rows if item["is_primary"]), balance_rows[0] if balance_rows else {})
    return {
        "ok": True,
        "settings": settings,
        "current": {
            "balance": primary.get("total", 0.0),
            "currency": primary.get("currency", "USD"),
            "balances": balance_rows,
            "today_tokens": today_tokens,
            "daily_limit": limit,
            "samples": len(samples),
        },
        "daily": daily,
        "alerts": alerts,
        "notified_today": notified_today,
    }


__all__ = ["DEFAULTS", "load_settings", "save_settings", "evaluate"]
