# -*- coding: utf-8 -*-
"""
Stellaris Console - Compute Service
全合规算力矩阵与双池调度
"""

import json
import os
import socket
import time
from urllib.parse import urlparse

from .common import COMPUTE_PROVIDERS_PATH

DEFAULT_COMPUTE_CONFIG = {
    "enabled": True,
    "primary_mode": "cloud",
    "failover_mode": "round_robin",
    "providers": [
        {
            "id": "provider_openrouter",
            "name": "OpenRouter 聚合矩阵",
            "type": "cloud",
            "base_url": "https://openrouter.ai/api/v1",
            "enabled": True,
            "weight": 100
        },
        {
            "id": "provider_ollama_local",
            "name": "本地 Ollama 算力池",
            "type": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "enabled": False,
            "weight": 50
        }
    ]
}

_MASKED_MARK = "****"


def _mask_key(raw) -> str:
    key = str(raw or "")
    if not key:
        return ""
    if len(key) > 8 and not key.startswith("local"):
        return f"{key[:3]}{_MASKED_MARK}{key[-4:]}"
    return key[:1] + _MASKED_MARK


def redact_compute_config(config):
    """把算力配置里的 api_key 换成掩码 + has_key（GET 用）。

    控制台曾经只脱敏了 `models_config`，`compute_providers.json` 走的是另一套读取，
    于是面板里填的官方 Key 会被原样回传（复核报告 §4.2）。
    """
    if not isinstance(config, dict):
        return config
    safe = json.loads(json.dumps(config))
    providers = safe.get("providers")
    items = providers.values() if isinstance(providers, dict) else (providers or [])
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.pop("api_key", "") or "")
        item["api_key_masked"] = _mask_key(raw)
        item["has_key"] = bool(raw and raw != "none")
    return safe


def _looks_masked(value) -> bool:
    text = str(value or "")
    return (not text) or _MASKED_MARK in text


def _restore_masked_keys(new_config: dict, old_config: dict):
    """保存时把掩码/空的 api_key 还原成旧值，避免把掩码写进配置。"""
    for pool in ("providers", "api_pool"):
        olds = old_config.get(pool) or []
        if isinstance(olds, dict):
            olds = list(olds.values())
        olds = [x for x in olds if isinstance(x, dict)]
        news = new_config.get(pool) or []
        for index, item in enumerate(news.values() if isinstance(news, dict) else news):
            if not isinstance(item, dict):
                continue
            if not _looks_masked(item.get("api_key")):
                item.pop("api_key_masked", None)
                item.pop("has_key", None)
                continue
            match = _find_old_provider(olds, item, index)
            item["api_key"] = str((match or {}).get("api_key") or "")
            item.pop("api_key_masked", None)
            item.pop("has_key", None)


def _find_old_provider(olds, item, index):
    """优先按 id / name + base_url 找回旧凭证，其次按位置。"""
    for field in ("id", "name"):
        value = str(item.get(field) or "")
        if not value:
            continue
        for old in olds:
            if str(old.get(field) or "") == value:
                return old
    base_url = str(item.get("base_url") or "")
    if base_url:
        for old in olds:
            if str(old.get("base_url") or "") == base_url:
                return old
    return olds[index] if index < len(olds) else {}


def get_compute_config():
    if os.path.exists(COMPUTE_PROVIDERS_PATH):
        try:
            with open(COMPUTE_PROVIDERS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_COMPUTE_CONFIG

def save_compute_config(config: dict):
    os.makedirs(os.path.dirname(COMPUTE_PROVIDERS_PATH), exist_ok=True)
    # 前端拿到的是掩码，落盘前把没改动的 Key 还原
    _restore_masked_keys(config, get_compute_config())
    with open(COMPUTE_PROVIDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return {"success": True, "message": "算力调度策略已保存"}

def _provider_by_id(config: dict, provider_id: str):
    """兼容 providers 是 dict（线上真实结构）和 list（旧默认模板）两种形态。"""
    providers = config.get("providers") or {}
    if isinstance(providers, dict):
        item = providers.get(provider_id)
        if isinstance(item, dict):
            return item
        for value in providers.values():
            if isinstance(value, dict) and value.get("id") == provider_id:
                return value
        return None
    if isinstance(providers, list):
        return next(
            (p for p in providers if isinstance(p, dict) and p.get("id") == provider_id),
            None,
        )
    return None


def test_compute_provider(provider_id: str):
    config = get_compute_config()
    target = _provider_by_id(config, provider_id)
    if not target:
        return {"success": False, "ok": False, "error": f"找不到算力服务商: {provider_id}"}

    base_url = target.get("base_url", "")
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        start_t = time.perf_counter()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect((host, port))
        s.close()
        latency = round((time.perf_counter() - start_t) * 1000, 1)
        return {"success": True, "ok": True, "latency_ms": latency, "msg": f"连接正常 ({latency}ms)"}
    except Exception as e:
        return {"success": False, "ok": False, "latency_ms": 0,
                "error": f"连接超时或不可达: {str(e)}", "msg": f"连接超时或不可达: {str(e)}"}
