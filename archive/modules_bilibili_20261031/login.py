# -*- coding: utf-8 -*-
"""B 站扫码登录：生成二维码、轮询、把 Cookie 写回 config.json。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PASSPORT = "https://passport.bilibili.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
STATUS_TEXT = {
    86101: "等待扫码",
    86090: "已扫码，等待确认",
    86038: "二维码已失效",
    0: "登录成功",
}


def _get_json(url: str) -> Dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        },
    )
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if int(data.get("code", 0) or 0) != 0:
                raise RuntimeError(str(data.get("message") or data.get("msg") or "B站登录接口失败"))
            return data.get("data") or {}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"B站登录接口网络失败：{last_error}")


def generate_qr() -> Tuple[str, str]:
    data = _get_json(f"{PASSPORT}/x/passport-login/web/qrcode/generate?source=main-fe-header")
    url = str(data.get("url") or "")
    key = str(data.get("qrcode_key") or "")
    if not url or not key:
        raise RuntimeError("B站没有返回二维码")
    return url, key


def render_qr(url: str, path: Path) -> bool:
    try:
        import qrcode
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url)
    image.save(path)
    return True


def poll_qr(key: str) -> Tuple[int, str, Dict[str, str]]:
    url = (
        f"{PASSPORT}/x/passport-login/web/qrcode/poll"
        f"?qrcode_key={urllib.parse.quote(key)}&source=main-fe-header"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        },
    )
    with opener.open(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
        set_cookies = response.headers.get_all("Set-Cookie") or []
    payload = json.loads(raw)
    if int(payload.get("code", 0) or 0) != 0:
        raise RuntimeError(str(payload.get("message") or payload.get("msg") or "B站登录接口失败"))
    data = payload.get("data") or {}
    code = int(data.get("code") or 0)
    message = str(data.get("message") or STATUS_TEXT.get(code, ""))
    cookies: Dict[str, str] = {}

    # 登录成功后 Cookie 主要在 Set-Cookie 里；data.url 只作为兜底
    for header in set_cookies:
        first = str(header).split(";", 1)[0]
        if "=" not in first:
            continue
        name, value = first.split("=", 1)
        if name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid", "buvid3"):
            cookies[name] = value

    if code == 0 and data.get("url"):
        for part in str(data["url"]).split("&"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.rsplit("?", 1)[-1]
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
                cookies.setdefault(name, value)
    return code, message, cookies


def fetch_buvid3() -> str:
    try:
        data = _get_json("https://api.bilibili.com/x/frontend/finger/spi")
        return str(data.get("b_3") or data.get("buvid3") or "")
    except Exception:
        return ""


def save_cookies(config_path: Path, cookies: Dict[str, str], buvid3: str) -> int:
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    bcfg = config.setdefault("bilibili", {})
    if cookies.get("SESSDATA"):
        bcfg["sessdata"] = cookies["SESSDATA"]
    if cookies.get("bili_jct"):
        bcfg["bili_jct"] = cookies["bili_jct"]
    if cookies.get("DedeUserID"):
        bcfg["dedeuserid"] = cookies["DedeUserID"]
        try:
            bcfg["bot_uid"] = int(cookies["DedeUserID"])
        except ValueError:
            pass
    if buvid3:
        bcfg["buvid3"] = buvid3
    payload = json.dumps(config, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".bili_config_", dir=str(config_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, config_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    try:
        return int(bcfg.get("bot_uid") or 0)
    except (TypeError, ValueError):
        return 0


def _write_manifest(config_path: Path) -> None:
    try:
        from .manifest import write_manifest

        config = json.loads(config_path.read_text(encoding="utf-8"))
        write_manifest(config)
    except Exception:
        pass


def qr_login(config_path: Path, timeout: int = 600) -> int:
    qr_path = config_path.parent / "data" / "login_qr.png"
    url, key = generate_qr()
    rendered = render_qr(url, qr_path)
    print(f"二维码：{qr_path}" if rendered else f"二维码链接：{url}")
    print("请用 B 站手机客户端扫码；过期会自动刷新，登录成功后 Cookie 会自动写回配置。", flush=True)

    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        try:
            code, message, cookies = poll_qr(key)
        except Exception as exc:  # noqa: BLE001
            print(f"[{time.strftime('%H:%M:%S')}] 网络重试：{exc}", flush=True)
            time.sleep(2)
            continue
        status = STATUS_TEXT.get(code, message or f"状态 {code}")
        if status != last_status:
            print(f"[{time.strftime('%H:%M:%S')}] {status}", flush=True)
            last_status = status
        if code == 0:
            buvid3 = fetch_buvid3()
            uid = save_cookies(config_path, cookies, buvid3)
            _write_manifest(config_path)
            print(f"登录成功，UID={uid}，Cookie 已写入 {config_path}", flush=True)
            if qr_path.exists():
                qr_path.unlink()
            return 0
        if code == 86038:
            url, key = generate_qr()
            render_qr(url, qr_path)
            print(f"[{time.strftime('%H:%M:%S')}] 二维码已过期，已刷新：{qr_path}", flush=True)
            last_status = None
            continue
        time.sleep(2)
    print("二维码等待超时，请重新运行 login。", flush=True)
    return 3
