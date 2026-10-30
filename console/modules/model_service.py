# -*- coding: utf-8 -*-
"""
Stellaris Console - Model Service
大模型管理、双凭证池调度、厂商智能嗅探与 Token 账本
"""

import os
import json
import urllib.request
import urllib.error
import time
import datetime
from .common import (
    MODELS_CONFIG_PATH,
    UPSTREAM_CACHE_PATH,
    get_db_connection
)
from agent_core.storage import get_store

def sniff_provider_from_key(key: str) -> dict:
    k = (key or "").strip()
    if not k:
        return {"provider": "Custom", "base_url": "https://api.openai.com/v1"}
    if k.startswith("AIza") or k.startswith("AQ.") or "generativelanguage" in k:
        return {"provider": "Google", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"}
    elif k.startswith("sk-ant-"):
        return {"provider": "Anthropic", "base_url": "https://api.anthropic.com/v1"}
    elif k.startswith("sk-or-"):
        return {"provider": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"}
    elif k.startswith("sk-") and len(k) > 30 and any(c in k for c in "abcdef"):
        if len(k) == 35 or "deepseek" in k.lower():
            return {"provider": "DeepSeek", "base_url": "https://api.deepseek.com/v1"}
        return {"provider": "OpenAI", "base_url": "https://api.openai.com/v1"}
    elif "ollama" in k.lower() or "11434" in k:
        return {"provider": "Ollama", "base_url": "http://127.0.0.1:11434/v1"}
    elif "ide" in k.lower() or "session" in k.lower():
        return {"provider": "IDE Bridge", "base_url": "http://127.0.0.1:18890/v1"}
    else:
        return {"provider": "Custom", "base_url": "https://api.openai.com/v1"}

def handle_sniff_key(body: dict) -> dict:
    key = body.get("key", "")
    return sniff_provider_from_key(key)

def get_model_reasoning_options(model_id: str) -> list:
    mid = (model_id or "").lower()
    if "flash" in mid and "gemini" in mid:
        return ["none", "low", "medium", "high"]
    elif "pro" in mid and "gemini" in mid:
        return ["low", "medium", "high"]
    elif "r1" in mid or "reasoner" in mid or "o1" in mid or "o3" in mid:
        return ["low", "medium", "high"]
    return ["none"]

def check_model_reasoning_support(model_id: str) -> bool:
    opts = get_model_reasoning_options(model_id)
    return opts != ["none"]

def build_upstream_network_opener():
    return urllib.request.build_opener()

def sync_models_from_upstream():
    try:
        url = "https://openrouter.ai/api/v1/models"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = []
            for item in data.get("data", []):
                mid = item.get("id")
                pricing = item.get("pricing", {})
                in_p = float(pricing.get("prompt", 0)) * 1000000
                out_p = float(pricing.get("completion", 0)) * 1000000
                cache_p = float(pricing.get("discount", 0)) * 1000000
                models.append({
                    "id": mid,
                    "name": item.get("name", mid),
                    "provider": item.get("owned_by", "OpenRouter"),
                    "input_price": round(in_p, 4),
                    "output_price": round(out_p, 4),
                    "cache_price": round(cache_p, 4),
                    "currency": "USD"
                })
            os.makedirs(os.path.dirname(UPSTREAM_CACHE_PATH), exist_ok=True)
            with open(UPSTREAM_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(models, f, ensure_ascii=False, indent=2)
            return {"success": True, "count": len(models)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_preset_models():
    data = []
    if os.path.exists(UPSTREAM_CACHE_PATH):
        try:
            with open(UPSTREAM_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if not data:
        sync_models_from_upstream()
        if os.path.exists(UPSTREAM_CACHE_PATH):
            try:
                with open(UPSTREAM_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
    return data or []

def get_models_config():
    default_config = {
        "active_model": "gemini-3.8-flash",
        "reasoning_effort": "medium",
        "credits": {
            "total_quota": 0.0,
            "used_quota": 0.0,
            "balance": 0.0,
            "currency": "USD"
        },
        "models": [],
        "api_pool": [],
        "master_credentials": [],
        "function_credentials": []
    }
    config = dict(default_config)
    if os.path.exists(MODELS_CONFIG_PATH):
        try:
            with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception:
            pass

    presets = get_preset_models()
    for m in presets:
        m["available_reasoning_efforts"] = get_model_reasoning_options(m.get("id", ""))
    for m in config.get("models", []):
        m["available_reasoning_efforts"] = get_model_reasoning_options(m.get("id", ""))

    config["preset_templates"] = presets

    if not config.get("active_model"):
        config["active_model"] = "gemini-3.8-flash"

    active_m = config.get("active_model", "")
    valid_efforts = get_model_reasoning_options(active_m)
    if config.get("reasoning_effort") not in valid_efforts:
        config["reasoning_effort"] = valid_efforts[0]

    return config

def save_models_config(new_config):
    downgraded = False
    active_m = new_config.get("active_model", "")
    effort = new_config.get("reasoning_effort", "none")

    valid_efforts = get_model_reasoning_options(active_m)
    if effort not in valid_efforts:
        new_config["reasoning_effort"] = valid_efforts[0]
        downgraded = True

    os.makedirs(os.path.dirname(MODELS_CONFIG_PATH), exist_ok=True)
    with open(MODELS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_config, f, ensure_ascii=False, indent=2)

    active_key = ""
    master_creds = new_config.get("master_credentials", [])
    if master_creds:
        active_master = next((c for c in master_creds if c.get("is_active", True) and c.get("api_key")), master_creds[0])
        active_key = active_master.get("api_key", "").strip()
        if active_master.get("model"):
            active_m = active_master.get("model")
            new_config["active_model"] = active_m
        if active_master.get("reasoning_effort"):
            new_config["reasoning_effort"] = active_master.get("reasoning_effort")
    if not active_key:
        for pool in new_config.get("api_pool", []):
            k = pool.get("api_key", "").strip()
            if k:
                active_key = k
                break

    return {
        "success": True,
        "downgraded_reasoning": downgraded,
        "reasoning_effort": new_config.get("reasoning_effort", "none"),
        "message": "已自动匹配模型可用思考强度" if downgraded else "中枢模型配置已保存"
    }

def save_credits(data):
    current_credits = {
        "total_quota": 0.0,
        "balance": 0.0,
        "currency": "USD"
    }
    cfg = get_models_config()
    old_credits = cfg.get("credits", {})
    if "credits" in cfg:
        current_credits.update(cfg["credits"])

    top_up_val = float(data.get("top_up_amount", 0.0))
    old_bal = float(old_credits.get("balance", current_credits["balance"]))

    # 兼容 total_granted, total_quota, amount, balance
    if "total_granted" in data:
        current_credits["total_quota"] = float(data["total_granted"])
        if "balance" not in data:
            current_credits["balance"] = float(data["total_granted"])
    if "total_quota" in data:
        current_credits["total_quota"] = float(data["total_quota"])
    if "balance" in data:
        current_credits["balance"] = float(data["balance"])
    if "currency" in data:
        current_credits["currency"] = str(data["currency"])

    # 若未显式传入 top_up_amount，但 balance 有正向增长，自动计算充值增量
    if top_up_val <= 0 and current_credits["balance"] > old_bal:
        top_up_val = round(current_credits["balance"] - old_bal, 4)

    # 充值同时抬高额度池，保证“同单位总金额”在刷新后仍然增加
    if top_up_val > 0 and "total_quota" not in data and "total_granted" not in data:
        current_credits["total_quota"] = float(current_credits.get("total_quota", 0.0)) + top_up_val

    # 写入充值入账流水（统一账本 token_usage）
    if top_up_val > 0:
        try:
            get_store().record_usage(
                session_type="recharge",
                model="账户充值",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=float(top_up_val),
                source="人工充值",
                credential_type="recharge",
                currency=current_credits.get("currency", "USD"),
            )
        except Exception:
            pass

    cfg["credits"] = current_credits
    save_models_config(cfg)
    return {"success": True, "credits": current_credits, "recharge_amount": top_up_val}

def reset_credits_and_balances(config_path=None):
    """清空账本时同步归零：额度池 + 所有凭证余额。"""
    path = config_path or MODELS_CONFIG_PATH
    cfg = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    credits = cfg.get("credits") or {}
    credits["total_quota"] = 0.0
    credits["balance"] = 0.0
    credits["used_quota"] = 0.0
    cfg["credits"] = credits
    for pool in ("master_credentials", "function_credentials"):
        for cred in cfg.get(pool, []) or []:
            if not isinstance(cred, dict):
                continue
            cred["balance"] = 0.0
            if "allocated_quota" in cred:
                cred["allocated_quota"] = 0.0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def get_model_pricing_map():
    cfg = get_models_config()
    pricing = {}
    for m in cfg.get("preset_templates", []):
        pricing[m.get("id")] = {
            "input": m.get("input_price", 0.0),
            "output": m.get("output_price", 0.0),
            "cache": m.get("cache_price", 0.0)
        }
    for m in cfg.get("models", []):
        pricing[m.get("id")] = {
            "input": m.get("input_price", 0.0),
            "output": m.get("output_price", 0.0),
            "cache": m.get("cache_price", 0.0)
        }
    # 凭证里手动改过的价格优先级最高，账本按这个算
    for pool in ("master_credentials", "function_credentials"):
        for cred in cfg.get(pool, []) or []:
            model_id = str(cred.get("model") or "").strip()
            if model_id:
                pricing[model_id] = {
                    "input": cred.get("input_price", 0.0),
                    "output": cred.get("output_price", 0.0),
                    "cache": cred.get("cache_price", 0.0),
                }
    return pricing

_RECHARGE_SOURCES = ('人工充值', '账户充值', '系统注资')


def _normalize_currency(value) -> str:
    """账本币种统一：RMB/CNY 合并，空值按 USD。"""
    currency = str(value or "USD").strip().upper()
    if currency in ("RMB", "CNY", "¥", "￥", "元"):
        return "CNY"
    if currency in ("USD", "US$", "$"):
        return "USD"
    return currency or "USD"


def _currency_symbol(currency: str) -> str:
    return {"USD": "$", "CNY": "¥"}.get(currency, f"{currency} ")


def _format_signed_amount(amount, currency: str, decimals: int = 2) -> str:
    """金额允许为负；负数把减号放在币种符号前（-$5.00 / -¥5.00）。"""
    value = float(amount or 0)
    symbol = _currency_symbol(currency)
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):.{decimals}f}"


def _format_amount(amount, currency: str) -> str:
    return _format_signed_amount(amount, currency, 4)


def _currency_sort_key(currency: str):
    """USD 优先，其次 CNY；其他币种按字母序。"""
    return ({"USD": 0, "CNY": 1}.get(currency, 2), currency)


def _joined_amounts(amounts_by_currency: dict) -> str:
    """不同币种各自列示，绝不跨币种相加。"""
    parts = []
    for currency in sorted(amounts_by_currency, key=_currency_sort_key):
        amount = float(amounts_by_currency[currency] or 0)
        if abs(amount) < 0.0000005:
            continue
        parts.append(_format_amount(amount, currency))
    return " · ".join(parts)


def _credential_label(credential_type: str) -> str:
    if credential_type == "master":
        return "主控凭证"
    if credential_type == "function":
        return "执行凭证"
    return "未归类"


_CNY_PROVIDER_HINTS = (
    "deepseek", "qwen", "千问", "通义", "glm", "智谱", "baidu", "文心",
    "ernie", "minimax", "moonshot", "kimi", "百炼", "国产",
)


def _credential_currency(cred: dict) -> str:
    """凭证币种：显式 currency 优先，否则按 provider/model/name 推断。"""
    explicit = str(cred.get("currency") or "").strip()
    if explicit:
        return _normalize_currency(explicit)
    haystack = f"{cred.get('provider', '')} {cred.get('model', '')} {cred.get('name', '')}".lower()
    return "CNY" if any(key in haystack for key in _CNY_PROVIDER_HINTS) else "USD"


def _credential_currency_meta(cfg: dict) -> dict:
    """返回各凭证池实际存在的币种，以及全局主/第二币种顺序。"""
    master, function = [], []
    for pool, target in (("master_credentials", master), ("function_credentials", function)):
        credentials = cfg.get(pool, []) or []
        # 启用中的凭证优先，保证主控/执行的主币种来自当前生效凭证
        credentials = sorted(
            credentials,
            key=lambda item: 0 if (
                isinstance(item, dict)
                and item.get("is_active", True)
                and any(item.get(key) for key in ("model", "api_key", "provider", "base_url"))
            ) else 1,
        )
        for cred in credentials:
            if not isinstance(cred, dict):
                continue
            if not any(cred.get(key) for key in ("model", "api_key", "provider", "base_url")):
                continue
            currency = _credential_currency(cred)
            if currency not in target:
                target.append(currency)

    all_currencies = []
    for currency in master + function:
        if currency not in all_currencies:
            all_currencies.append(currency)

    credits_currency = _normalize_currency((cfg.get("credits") or {}).get("currency") or "USD")
    if not all_currencies:
        all_currencies = [credits_currency]
    primary = credits_currency if credits_currency in all_currencies else all_currencies[0]
    ordered = [primary] + [currency for currency in all_currencies if currency != primary]
    return {
        "master": master,
        "function": function,
        "all": ordered,
        "primary": primary,
    }


def _empty_cost(currency: str) -> dict:
    return {
        "currency": currency,
        "symbol": _currency_symbol(currency),
        "today_cost": 0.0,
        "today_cost_formatted": _format_amount(0, currency),
        "overall_cost": 0.0,
        "overall_cost_formatted": _format_amount(0, currency),
        "today_calls": 0,
        "today_tokens": 0,
        "overall_calls": 0,
        "overall_tokens": 0,
        "has_usage": False,
    }


def _group_currency_order(kind: str, actual_currencies: list, currency_meta: dict) -> list:
    """只用“本组凭证”决定币种，不能用另一组的凭证补第二币种。"""
    group_currencies = []
    if kind in ("master", "function"):
        group_currencies = list(currency_meta.get(kind) or [])

    if group_currencies:
        order = list(group_currencies)
    else:
        # 本组没有凭证时，退回全局额度币种；不主动补第二币种
        order = [currency_meta.get("primary") or "USD"]

    # 历史流水可能实际使用过其他币种：有真实消耗就保留，避免金额丢失
    for currency in actual_currencies:
        if currency not in order:
            order.append(currency)
    return order


def _build_accounting_groups(buckets: list, currency_meta: dict = None) -> list:
    """主控/执行分开；每组金额按凭证币种分别列示，缺失币种补 0。"""
    currency_meta = currency_meta or {"master": [], "function": [], "all": ["USD"], "primary": "USD"}
    grouped = {}
    for bucket in buckets:
        grouped.setdefault(bucket["credential_type"], []).append(bucket)

    ordered = [("master", "主控凭证"), ("function", "执行凭证")]
    if grouped.get(""):
        ordered.append(("", "未归类"))
    for kind in sorted(grouped):
        if kind not in ("master", "function", ""):
            ordered.append((kind, "未归类"))

    groups = []
    for kind, label in ordered:
        entries = grouped.get(kind, [])
        actual_by_currency = {bucket["currency"]: bucket for bucket in entries}
        actual_currencies = [
            bucket["currency"]
            for bucket in sorted(entries, key=lambda item: _currency_sort_key(item["currency"]))
        ]
        currency_order = _group_currency_order(kind, actual_currencies, currency_meta)

        costs = []
        for currency in currency_order:
            bucket = actual_by_currency.get(currency)
            if bucket is None:
                costs.append(_empty_cost(currency))
                continue
            costs.append({
                "currency": currency,
                "symbol": _currency_symbol(currency),
                "today_cost": round(float(bucket["today_cost"]), 6),
                "today_cost_formatted": _format_amount(bucket["today_cost"], currency),
                "overall_cost": round(float(bucket["overall_cost"]), 6),
                "overall_cost_formatted": _format_amount(bucket["overall_cost"], currency),
                "today_calls": int(bucket["today_calls"]),
                "today_tokens": int(bucket["today_tokens"]),
                "overall_calls": int(bucket["overall_calls"]),
                "overall_tokens": int(bucket["overall_tokens"]),
                "has_usage": True,
            })

        groups.append({
            "credential_type": kind,
            "label": label,
            "currencies": [cost["currency"] for cost in costs],
            "currency_count": len(costs),
            "primary_currency": costs[0]["currency"] if costs else None,
            "secondary_currency": costs[1]["currency"] if len(costs) > 1 else None,
            "has_secondary_currency": len(costs) > 1,
            "costs": costs,
            "today_cost_display": " · ".join(
                cost["today_cost_formatted"] for cost in costs if cost.get("has_usage")
            ),
            "overall_cost_display": " · ".join(
                cost["overall_cost_formatted"] for cost in costs if cost.get("has_usage")
            ),
            "today_calls": sum(cost["today_calls"] for cost in costs),
            "today_tokens": sum(cost["today_tokens"] for cost in costs),
            "overall_calls": sum(cost["overall_calls"] for cost in costs),
            "overall_tokens": sum(cost["overall_tokens"] for cost in costs),
            "has_data": bool(entries),
        })
    return groups


def _monthly_daily_activity(c) -> dict:
    """当前自然月的每日 Token/调用热力；综合分 = tokens + calls * 5000。"""
    today = datetime.date.today()
    start = today.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)

    start_ts = datetime.datetime.combine(start, datetime.time.min).timestamp()
    next_ts = datetime.datetime.combine(next_month, datetime.time.min).timestamp()
    rows = c.execute(
        """
        SELECT timestamp, prompt_tokens, completion_tokens
        FROM token_usage
        WHERE timestamp >= ? AND timestamp < ?
          AND COALESCE(source, '') NOT IN ('人工充值', '账户充值', '系统注资')
        """,
        (start_ts, next_ts),
    ).fetchall()

    by_day = {}
    for row in rows:
        ts = row["timestamp"]
        if not ts:
            continue
        day = datetime.date.fromtimestamp(ts)
        item = by_day.setdefault(day, {"calls": 0, "tokens": 0})
        item["calls"] += 1
        item["tokens"] += int(row["prompt_tokens"] or 0) + int(row["completion_tokens"] or 0)

    days_in_month = (next_month - start).days
    days = []
    max_score = 0
    for offset in range(days_in_month):
        day = start + datetime.timedelta(days=offset)
        data = by_day.get(day, {"calls": 0, "tokens": 0})
        score = data["tokens"] + data["calls"] * 5000
        max_score = max(max_score, score)
        days.append({
            "date": day.isoformat(),
            "day": day.day,
            "weekday": day.weekday(),
            "calls": data["calls"],
            "tokens": data["tokens"],
            "score": score,
            "level": 0,
        })

    for item in days:
        if item["score"] <= 0 or max_score <= 0:
            item["level"] = 0
            continue
        ratio = item["score"] / max_score
        if ratio <= 0.25:
            item["level"] = 1
        elif ratio <= 0.50:
            item["level"] = 2
        elif ratio <= 0.75:
            item["level"] = 3
        else:
            item["level"] = 4

    return {
        "year": start.year,
        "month": start.month,
        "days": days,
        "max_score": max_score,
        "formula": "tokens + calls * 5000",
    }


def _credential_balance_value(cred: dict) -> float:
    for key in ("balance", "allocated_quota"):
        value = cred.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return 0.0


def _build_currency_balances(cfg: dict, currency_meta: dict, spent_by_currency: dict):
    """按币种汇总“所有同单位金额”：额度池 + 主控/执行凭证余额 - 该币种消耗。"""
    credits = cfg.get("credits") or {}
    credits_currency = _normalize_currency(credits.get("currency") or "USD")
    totals = {}
    counts = {}

    def add_amount(currency, amount, credential_delta=0):
        totals[currency] = totals.get(currency, 0.0) + float(amount or 0.0)
        counts[currency] = counts.get(currency, 0) + int(credential_delta)

    # 主额度池计入它自己的币种
    add_amount(credits_currency, float(credits.get("total_quota", 0.0) or 0.0))

    # 主控 + 执行凭证的余额/分配额度，按各自币种累加
    for pool in ("master_credentials", "function_credentials"):
        for cred in cfg.get(pool, []) or []:
            if not isinstance(cred, dict):
                continue
            if not any(cred.get(key) for key in ("model", "api_key", "provider", "base_url")):
                continue
            currency = _credential_currency(cred)
            add_amount(currency, _credential_balance_value(cred), 1)

    # 每个币种扣掉该币种自己的累计消耗，不跨币种；允许透支为负数
    for currency in list(totals):
        totals[currency] = totals[currency] - float(spent_by_currency.get(currency, 0.0))

    # 主控凭证的币种坐主位，其后按凭证币种顺序补第二/第三/第四
    primary = (currency_meta.get("master") or [credits_currency])[0]
    order = [primary]
    for currency in currency_meta.get("all", []):
        if currency not in order:
            order.append(currency)
    for currency in totals:
        if currency not in order:
            order.append(currency)

    balances = []
    for currency in order:
        total = float(totals.get(currency, 0.0))
        credential_count = counts.get(currency, 0)
        if currency != primary and credential_count <= 0 and total == 0:
            continue
        balances.append({
            "currency": currency,
            "symbol": _currency_symbol(currency),
            "total": round(total, 6),
            "total_formatted": _format_signed_amount(total, currency, 2),
            "is_primary": currency == primary,
            "credential_count": credential_count,
        })
        if len(balances) >= 4:
            break
    return primary, balances


def get_token_analytics():
    get_store()  # 确保数据库已迁移到最新 schema（token_usage_ledger 兼容视图存在）
    try:
        from .system_service import backfill_token_costs, backfill_usage_meta
        backfill_token_costs()
        backfill_usage_meta()
    except Exception:
        pass
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT SUM(input_tokens) as total_in,
               SUM(output_tokens) as total_out,
               SUM(cache_tokens) as total_cache,
               SUM(CASE WHEN source NOT IN ('人工充值', '账户充值', '系统注资') THEN cost_usd ELSE 0 END) as total_cost,
               COUNT(CASE WHEN source NOT IN ('人工充值', '账户充值', '系统注资') THEN id ELSE NULL END) as total_calls
        FROM token_usage_ledger
    """)
    row = c.fetchone()
    total_in = row["total_in"] or 0
    total_out = row["total_out"] or 0
    total_cache = row["total_cache"] or 0
    total_cost = row["total_cost"] or 0.0
    total_calls = row["total_calls"] or 0

    # 今日数据统计
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    c.execute("""
        SELECT SUM(input_tokens) as today_in,
               SUM(output_tokens) as today_out,
               SUM(cache_tokens) as today_cache,
               SUM(CASE WHEN source NOT IN ('人工充值', '账户充值', '系统注资') THEN cost_usd ELSE 0 END) as today_cost,
               COUNT(CASE WHEN source NOT IN ('人工充值', '账户充值', '系统注资') THEN id ELSE NULL END) as today_calls
        FROM token_usage_ledger
        WHERE timestamp >= ?
    """, (today_start,))
    t_row = c.fetchone()
    today_in = t_row["today_in"] or 0
    today_out = t_row["today_out"] or 0
    today_cache = t_row["today_cache"] or 0
    today_cost = t_row["today_cost"] or 0.0
    today_calls = t_row["today_calls"] or 0

    # 按主/执行凭证 + 币种分桶；RMB 与 CNY 先归一再合并，避免同币种被拆成两行
    c.execute("""
        SELECT COALESCE(credential_type, '') AS credential_type,
               COALESCE(currency, 'USD') AS currency,
               COUNT(*) AS calls,
               SUM(prompt_tokens) AS input_tokens,
               SUM(completion_tokens) AS output_tokens,
               SUM(cached_tokens) AS cached_tokens,
               SUM(cost_usd) AS overall_cost,
               SUM(CASE WHEN timestamp >= ? THEN cost_usd ELSE 0 END) AS today_cost,
               COUNT(CASE WHEN timestamp >= ? THEN id ELSE NULL END) AS today_calls,
               SUM(CASE WHEN timestamp >= ?
                        THEN COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)
                        ELSE 0 END) AS today_tokens
        FROM token_usage
        WHERE COALESCE(source, '') NOT IN ('人工充值', '账户充值', '系统注资')
        GROUP BY credential_type, currency
        ORDER BY credential_type, currency
    """, (today_start, today_start, today_start))

    bucket_map = {}
    for r in c.fetchall():
        kind = str(r["credential_type"] or "").strip()
        currency = _normalize_currency(r["currency"])
        key = (kind, currency)
        bucket = bucket_map.setdefault(key, {
            "credential_type": kind,
            "currency": currency,
            "today_calls": 0,
            "today_tokens": 0,
            "overall_calls": 0,
            "overall_tokens": 0,
            "today_cost": 0.0,
            "overall_cost": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
        })
        input_tokens = int(r["input_tokens"] or 0)
        output_tokens = int(r["output_tokens"] or 0)
        bucket["today_calls"] += int(r["today_calls"] or 0)
        bucket["today_tokens"] += int(r["today_tokens"] or 0)
        bucket["overall_calls"] += int(r["calls"] or 0)
        bucket["overall_tokens"] += input_tokens + output_tokens
        bucket["today_cost"] += float(r["today_cost"] or 0)
        bucket["overall_cost"] += float(r["overall_cost"] or 0)
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cached_tokens"] += int(r["cached_tokens"] or 0)

    buckets = sorted(
        bucket_map.values(),
        key=lambda item: (item["credential_type"], _currency_sort_key(item["currency"])),
    )
    cost_breakdown = []
    for bucket in buckets:
        currency = bucket["currency"]
        cost_breakdown.append({
            "credential_type": bucket["credential_type"],
            "credential_label": _credential_label(bucket["credential_type"]),
            "currency": currency,
            "calls": bucket["overall_calls"],
            "input_tokens": bucket["input_tokens"],
            "output_tokens": bucket["output_tokens"],
            "cached_tokens": bucket["cached_tokens"],
            "total_tokens": bucket["overall_tokens"],
            "cost": float(bucket["overall_cost"]),
            "cost_formatted": _format_amount(bucket["overall_cost"], currency),
            "today_cost": float(bucket["today_cost"]),
            "today_cost_formatted": _format_amount(bucket["today_cost"], currency),
            "today_calls": bucket["today_calls"],
            "today_tokens": bucket["today_tokens"],
        })

    c.execute("""
        SELECT id, timestamp, model AS model_id, prompt_tokens AS input_tokens,
               completion_tokens AS output_tokens, cached_tokens AS cache_tokens,
               source, cost_usd, credential_type, currency
        FROM token_usage
        ORDER BY timestamp DESC
        LIMIT 30
    """)
    recent_ledger = []
    recent_calls = []
    for r in c.fetchall():
        t_str = time.strftime('%H:%M:%S', time.localtime(r["timestamp"])) if r["timestamp"] else '--:--:--'
        cost_val = r["cost_usd"] or 0.0
        is_recharge = (r["source"] or "") in _RECHARGE_SOURCES
        currency = _normalize_currency(r["currency"])
        symbol = _currency_symbol(currency)
        kind = str(r["credential_type"] or "").strip()
        item = {
            # 字符串 ID，兼容核心页面 / 明细表对 id.length、id.slice(-8) 的调用
            "id": str(r["id"]),
            "timestamp": r["timestamp"],
            "model_id": r["model_id"],
            "model": r["model_id"],
            "input_tokens": r["input_tokens"],
            "input": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "output": r["output_tokens"],
            "cache_tokens": r["cache_tokens"],
            "cached": r["cache_tokens"],
            "source": r["source"],
            "credential_type": kind,
            "credential_label": "充值" if is_recharge else _credential_label(kind),
            "currency": currency,
            "currency_symbol": symbol,
            "cost_usd": cost_val,
            "cost_formatted": f"+{symbol}{cost_val:.2f}" if is_recharge else f"{symbol}{cost_val:.4f}",
            "is_recharge": is_recharge,
            "time": t_str
        }
        recent_ledger.append(item)
        recent_calls.append(item)
    daily_activity = _monthly_daily_activity(c)
    conn.close()

    # 读取额度信息
    cfg = get_models_config()
    currency_meta = _credential_currency_meta(cfg)
    cfg_credits = cfg.get("credits", {})
    total_granted = float(cfg_credits.get("total_quota", 0.0))

    today_by_currency = {}
    overall_by_currency = {}
    for bucket in buckets:
        currency = bucket["currency"]
        today_by_currency[currency] = today_by_currency.get(currency, 0.0) + float(bucket["today_cost"])
        overall_by_currency[currency] = overall_by_currency.get(currency, 0.0) + float(bucket["overall_cost"])

    primary_currency, currency_balances = _build_currency_balances(
        cfg, currency_meta, overall_by_currency
    )
    primary_balance = next(
        (item["total"] for item in currency_balances if item["is_primary"]),
        0.0,
    )
    main_today_cost = float(today_by_currency.get(primary_currency, 0.0))
    main_overall_cost = float(overall_by_currency.get(primary_currency, 0.0))
    credits_data = {
        "balance": primary_balance,
        "balance_formatted": _format_signed_amount(primary_balance, primary_currency, 2),
        "currency": primary_currency,
        "primary_currency": primary_currency,
        "currency_count": len(currency_balances),
        "balances": currency_balances,
        "total_granted": total_granted,
        # 兼容旧字段：数字只代表主控币种，不再把不同币种硬加在一起
        "today_cost": round(main_today_cost, 6),
        "today_cost_formatted": _joined_amounts(today_by_currency) or _format_amount(0, primary_currency),
        "today_cost_by_currency": {c: round(float(v), 6) for c, v in sorted(today_by_currency.items(), key=lambda kv: _currency_sort_key(kv[0]))},
        "overall_cost": round(main_overall_cost, 6),
        "overall_cost_formatted": _joined_amounts(overall_by_currency) or _format_amount(0, primary_currency),
        "overall_cost_by_currency": {c: round(float(v), 6) for c, v in sorted(overall_by_currency.items(), key=lambda kv: _currency_sort_key(kv[0]))},
    }

    today_data = {
        "calls": today_calls,
        "input_tokens": today_in,
        "output_tokens": today_out,
        "cached_tokens": today_cache,
        "total_tokens": today_in + today_out
    }

    overall_data = {
        "calls": total_calls,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cached_tokens": total_cache,
        "total_tokens": total_in + total_out
    }

    # 合计口径只保留 Token 与调用次数；金额在主控/执行分组内按币种分别核算
    combined_data = {
        "today_calls": today_calls,
        "today_tokens": today_in + today_out,
        "overall_calls": total_calls,
        "overall_tokens": total_in + total_out,
    }

    accounting_groups = _build_accounting_groups(buckets, currency_meta)

    return {
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cache_tokens": total_cache,
        "total_tokens": total_in + total_out,
        # 兼容字段：只给 USD 实际消费，跨币种请看 total_cost_by_currency
        "total_cost_usd": round(float(overall_by_currency.get("USD", 0.0)), 4),
        "total_cost_by_currency": credits_data["overall_cost_by_currency"],
        "recent_ledger": recent_ledger,
        "recent_calls": recent_calls,
        "credits": credits_data,
        "today": today_data,
        "overall": overall_data,
        "combined": combined_data,
        "accounting_groups": accounting_groups,
        "daily_activity": daily_activity,
        "cost_breakdown": cost_breakdown,
    }

def get_universal_models_list():
    cfg = get_models_config()
    models_out = []
    active_m = cfg.get("active_model", "gemini-3.8-flash")
    for m in cfg.get("preset_templates", []):
        models_out.append({
            "id": m.get("id"),
            "object": "model",
            "created": 1700000000,
            "owned_by": m.get("provider", "hub")
        })
    if not any(m["id"] == active_m for m in models_out):
        models_out.insert(0, {
            "id": active_m,
            "object": "model",
            "created": 1700000000,
            "owned_by": "hub"
        })
    return {"object": "list", "data": models_out}
