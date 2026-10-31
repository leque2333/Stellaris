# -*- coding: utf-8 -*-
"""API 安全：本机访问限制 + 请求签名（HMAC + 防重放）

配置文件：data/api_security.json（缺失时用默认值：不限制来源、不校验签名）

    {
      "local_only": false,
      "allow_cidrs": ["127.0.0.1/32", "::1/128"],
      "trusted_proxies": ["127.0.0.1/32", "::1/128"],
      "signature": {"enabled": false, "window_seconds": 300, "secret": ""}
    }

签名规则（客户端与服务端必须一致）：

    canonical = METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + SHA256_HEX(RAW_BODY)
    signature = HMAC_SHA256(secret, canonical) 的小写 hex

请求头：X-Timestamp: <unix 秒>、X-Signature: <hex>
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "local_only": False,
    "allow_cidrs": ["127.0.0.1/32", "::1/128"],
    "trusted_proxies": ["127.0.0.1/32", "::1/128"],
    "signature": {
        "enabled": False,
        "window_seconds": 300,
        "secret": "",
        # 内网（本机 / docker / 局域网）不参与签名：保护的是经 nginx 进来的公网流量，
        # AstrBot 这类客户端不用改任何配置
        "exempt_cidrs": [
            "127.0.0.0/8", "::1/128",
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        ],
        # 轮换过渡：旧密钥在 previous_until 之前仍然有效
        "previous_secret": "",
        "previous_until": 0,
    },
}

_CACHE: Dict[str, Any] = {"key": None, "config": None}


def config_path() -> Path:
    """配置文件位置；测试可用 HUB_API_SECURITY_PATH 指到临时目录。"""
    env = os.getenv("HUB_API_SECURITY_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "api_security.json"


def load_config() -> Dict[str, Any]:
    """读配置；文件改了会按 mtime 自动重载，读不到就用默认值。"""
    path = config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return dict(DEFAULT_CONFIG)
    cache_key = (str(path), mtime)
    if _CACHE["key"] == cache_key and _CACHE["config"] is not None:
        return _CACHE["config"]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    config = _merged(data if isinstance(data, dict) else {})
    _CACHE["key"] = cache_key
    _CACHE["config"] = config
    return config


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """按字段合并写入，返回保存后的完整配置。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_config()
    merged = dict(current)
    for key, value in (patch or {}).items():
        if key == "signature" and isinstance(value, dict):
            merged["signature"] = {**current["signature"], **value}
        else:
            merged[key] = value
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE["key"] = (str(path), path.stat().st_mtime)
    _CACHE["config"] = merged
    return merged


def _merged(data: Dict[str, Any]) -> Dict[str, Any]:
    config = {
        "local_only": bool(data.get("local_only", False)),
        "allow_cidrs": _cidrs(data.get("allow_cidrs"), DEFAULT_CONFIG["allow_cidrs"]),
        "trusted_proxies": _cidrs(data.get("trusted_proxies"), DEFAULT_CONFIG["trusted_proxies"]),
        "signature": _signature_block(data.get("signature")),
    }
    return config


def _cidrs(value, fallback) -> list:
    """None / 非列表 → 用默认值；显式空列表就是"不放行任何网段"。"""
    if value is None or not isinstance(value, list):
        items = list(fallback)
    else:
        items = value
    out = []
    for item in items:
        try:
            ipaddress.ip_network(str(item).strip(), strict=False)
            out.append(str(item).strip())
        except ValueError:
            continue
    return out


def _signature_block(value) -> Dict[str, Any]:
    block = value if isinstance(value, dict) else {}
    try:
        window = int(block.get("window_seconds", 300))
    except (TypeError, ValueError):
        window = 300
    return {
        "enabled": bool(block.get("enabled", False)),
        "window_seconds": max(30, min(window, 3600)),
        "secret": str(block.get("secret") or ""),
        "exempt_cidrs": _cidrs(
            block.get("exempt_cidrs"), DEFAULT_CONFIG["signature"]["exempt_cidrs"]
        ),
        "previous_secret": str(block.get("previous_secret") or ""),
        "previous_until": int(block.get("previous_until") or 0),
    }


def _in_networks(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return False
    for item in networks:
        try:
            if address in ipaddress.ip_network(str(item), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(peer_ip: str, forwarded_for: str, config: Optional[Dict[str, Any]] = None) -> str:
    """真实客户端 IP：从右往左跳过可信反代，取第一个不可信的地址。

    nginx 用 $proxy_add_x_forwarded_for 会在**末尾**追加真实来源，
    所以最左边那一节可能是客户端自己伪造的，不能直接用。
    """
    cfg = config or load_config()
    peer = str(peer_ip or "").strip()
    if not peer or not _in_networks(peer, cfg["trusted_proxies"]):
        return peer
    hops = [item.strip() for item in str(forwarded_for or "").split(",") if item.strip()]
    for hop in reversed(hops + [peer]):
        if hop and not _in_networks(hop, cfg["trusted_proxies"]):
            return hop
    return peer


def ip_allowed(ip: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """本机模式下判断这个 IP 放不放行；local_only 关着时永远放行。"""
    cfg = config or load_config()
    if not cfg["local_only"]:
        return True
    return _in_networks(ip, cfg["allow_cidrs"])


def sign(method: str, path: str, timestamp: str, raw_body: bytes, secret: str) -> str:
    """给客户端/测试用的签名函数。"""
    canonical = "\n".join([
        str(method or "").upper(),
        str(path or ""),
        str(timestamp or ""),
        hashlib.sha256(raw_body or b"").hexdigest(),
    ])
    return hmac.new(str(secret).encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def signature_exempt(ip: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """内网来源不参与签名（AstrBot / 本机脚本 / docker 都落在这里）。"""
    cfg = config or load_config()
    return _in_networks(ip, cfg["signature"]["exempt_cidrs"])


def signature_error(
    method: str,
    path: str,
    timestamp: str,
    signature: str,
    raw_body: bytes,
    config: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Optional[str]:
    """返回 None 表示签名合格，否则返回错误码（用于 401 的 error 字段）。"""
    cfg = config or load_config()
    block = cfg["signature"]
    if not block["enabled"]:
        return None
    secret = block["secret"]
    if not secret:
        # 开了签名却没配密钥：宁可放行，避免把整个入口锁死
        return None
    if not timestamp or not signature:
        return "missing_signature"
    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError):
        return "bad_timestamp"
    if abs((now if now is not None else time.time()) - sent_at) > block["window_seconds"]:
        return "stale_timestamp"
    now_ts = now if now is not None else time.time()
    sent = str(signature).strip().lower()
    if hmac.compare_digest(sign(method, path, timestamp, raw_body, secret), sent):
        return None
    previous = block["previous_secret"]
    if previous and now_ts <= block["previous_until"]:
        if hmac.compare_digest(sign(method, path, timestamp, raw_body, previous), sent):
            return None
    return "bad_signature"


def rotate_secret(grace_seconds: int = 1800, secret: str = "") -> Dict[str, Any]:
    """换新密钥，旧密钥在 grace_seconds 内继续有效（给客户端留出更新时间）。"""
    current = load_config()["signature"]
    new_secret = secret or secrets.token_hex(32)
    return save_config({
        "signature": {
            "secret": new_secret,
            "previous_secret": current["secret"],
            "previous_until": int(time.time()) + max(60, int(grace_seconds)),
        }
    })


class LoginThrottle:
    """按 IP 记登录失败：窗口内失败够数就锁一段时间。"""

    def __init__(self, max_failures: int = 5, window_seconds: int = 600, block_seconds: int = 600):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._failures: Dict[str, list] = {}
        self._blocked: Dict[str, float] = {}

    def blocked_for(self, ip: str, now: Optional[float] = None) -> int:
        """还要锁多少秒；没锁返回 0。"""
        now_ts = time.time() if now is None else now
        until = self._blocked.get(ip)
        if not until:
            return 0
        if until > now_ts:
            return int(until - now_ts)
        self._blocked.pop(ip, None)
        return 0

    def record_failure(self, ip: str, now: Optional[float] = None) -> None:
        now_ts = time.time() if now is None else now
        stamps = [t for t in self._failures.get(ip, []) if now_ts - t <= self.window_seconds]
        stamps.append(now_ts)
        if len(stamps) >= self.max_failures:
            self._blocked[ip] = now_ts + self.block_seconds
            self._failures[ip] = []
            return
        self._failures[ip] = stamps

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._blocked.pop(ip, None)


login_throttle = LoginThrottle()


def status() -> Dict[str, Any]:
    """给控制台看的脱敏状态：密钥只报是否已配置。"""
    cfg = load_config()
    return {
        "local_only": cfg["local_only"],
        "allow_cidrs": cfg["allow_cidrs"],
        "trusted_proxies": cfg["trusted_proxies"],
        "signature": {
            "enabled": cfg["signature"]["enabled"],
            "window_seconds": cfg["signature"]["window_seconds"],
            "secret_configured": bool(cfg["signature"]["secret"]),
            "exempt_cidrs": cfg["signature"]["exempt_cidrs"],
            "previous_valid_for": max(
                0, int(cfg["signature"]["previous_until"] - time.time())
            ) if cfg["signature"]["previous_secret"] else 0,
        },
        "path": str(config_path()),
    }
