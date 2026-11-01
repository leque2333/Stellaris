# -*- coding: utf-8 -*-
"""把账本只读地暴露给 AI：系统工具 hub_ledger_query。"""

from __future__ import annotations

import datetime
import sqlite3
import time
from typing import Any, Dict

TOOL_NAME = "hub_ledger_query"
MAX_LIMIT = 50
MAX_DAYS = 90
RECHARGE_SOURCES = ("人工充值", "账户充值", "系统注资")


def tool_spec() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "只读查询中枢账本：余额/今日/累计消耗、最近流水、按天汇总。"
                "用于回答花了多少钱、token 用在哪、最近调用记录。仅管理员可用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["summary", "recent", "daily"],
                        "description": "summary=汇总，recent=最近流水，daily=按天聚合",
                    },
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_DAYS,
                        "description": "daily 聚合天数，默认 7",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIMIT,
                        "description": "recent 返回条数，默认 10",
                    },
                },
                "required": ["action"],
            },
        },
    }


def _connect():
    from agent_core.storage import get_store

    path = get_store().path
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def _today_start() -> float:
    return datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _summary(con) -> Dict[str, Any]:
    today = _today_start()
    def row(where: str, params=()):
        return con.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens + completion_tokens), 0) tokens, "
            "COALESCE(SUM(cost_usd), 0) cost FROM token_usage "
            f"WHERE source NOT IN ({','.join('?' for _ in RECHARGE_SOURCES)}) {where}",
            tuple(RECHARGE_SOURCES) + params,
        ).fetchone()
    today_row = row("AND timestamp >= ?", (today,))
    all_row = row("")
    return {
        "today": {
            "calls": int(today_row["calls"] or 0),
            "tokens": int(today_row["tokens"] or 0),
            "cost": round(float(today_row["cost"] or 0), 6),
        },
        "overall": {
            "calls": int(all_row["calls"] or 0),
            "tokens": int(all_row["tokens"] or 0),
            "cost": round(float(all_row["cost"] or 0), 6),
        },
    }


def _recent(con, limit: int) -> list:
    rows = con.execute(
        "SELECT id, timestamp, model, prompt_tokens, completion_tokens, cached_tokens, "
        "cost_usd, source, credential_type, currency FROM token_usage "
        "WHERE source NOT IN ({}) ORDER BY timestamp DESC LIMIT ?".format(
            ",".join("?" for _ in RECHARGE_SOURCES)
        ),
        tuple(RECHARGE_SOURCES) + (limit,),
    ).fetchall()
    return [{
        "id": row["id"],
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row["timestamp"] or 0))),
        "model": row["model"],
        "input_tokens": int(row["prompt_tokens"] or 0),
        "cache_tokens": int(row["cached_tokens"] or 0),
        "output_tokens": int(row["completion_tokens"] or 0),
        "cost": round(float(row["cost_usd"] or 0), 6),
        "source": row["source"],
        "credential_type": row["credential_type"],
        "currency": row["currency"],
    } for row in rows]


def _daily(con, days: int) -> list:
    since = _today_start() - (days - 1) * 86400
    rows = con.execute(
        "SELECT date(timestamp, 'unixepoch', 'localtime') day, "
        "COUNT(*) calls, COALESCE(SUM(prompt_tokens + completion_tokens), 0) tokens, "
        "COALESCE(SUM(cost_usd), 0) cost FROM token_usage "
        f"WHERE timestamp >= ? AND source NOT IN ({','.join('?' for _ in RECHARGE_SOURCES)}) "
        "GROUP BY day ORDER BY day",
        (since,) + tuple(RECHARGE_SOURCES),
    ).fetchall()
    return [{
        "date": row["day"],
        "calls": int(row["calls"] or 0),
        "tokens": int(row["tokens"] or 0),
        "cost": round(float(row["cost"] or 0), 6),
    } for row in rows]


def execute(action: str = "summary", days: int = 7, limit: int = 10) -> Dict[str, Any]:
    action = str(action or "summary").strip().lower()
    if action not in ("summary", "recent", "daily"):
        return {"ok": False, "error": f"不支持的动作: {action}"}
    days = max(1, min(int(days or 7), MAX_DAYS))
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    try:
        con = _connect()
        try:
            if action == "recent":
                data = {"recent": _recent(con, limit)}
            elif action == "daily":
                data = {"daily": _daily(con, days)}
            else:
                data = _summary(con)
                data["daily"] = _daily(con, min(days, 7))
        finally:
            con.close()
        return {"ok": True, "action": action, **data}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


__all__ = ["TOOL_NAME", "tool_spec", "execute"]
