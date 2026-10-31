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
    with open(COMPUTE_PROVIDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return {"success": True, "message": "算力调度策略已保存"}

def test_compute_provider(provider_id: str):
    config = get_compute_config()
    target = next((p for p in config.get("providers", []) if p.get("id") == provider_id), None)
    if not target:
        return {"success": False, "error": f"找不到算力服务商: {provider_id}"}

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
        return {"success": True, "latency_ms": latency, "msg": f"连接正常 ({latency}ms)"}
    except Exception as e:
        return {"success": False, "latency_ms": 0, "msg": f"连接超时或不可达: {str(e)}"}
