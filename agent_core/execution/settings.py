# -*- coding: utf-8 -*-
"""执行 AI 凭据：文件配置、控制台读写、联通性测试

优先级：data/execution_ai.json → HUB_EXEC_* 环境变量 → 主模型配置（LLMClient 兜底）。
密钥只落盘、不回显；控制台读到的永远是掩码值。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..llm import LLMClient

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MASKED_KEY = "********"

# 设置页“功能凭证”只存 provider，不存云端 base_url；这里补成 OpenAI 兼容地址
PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}

# LLMClient 在网络/鉴权失败时返回的兜底文案；它们不是模型真实回复
FALLBACK_PREFIXES = (
    "唔，思考时接口发生异常了",
    "连接模型中枢超时啦",
    "（提示：未检测到",
    "（底层过滤器拦截",
    "思考轮次过多",
    "……",
)


def settings_path() -> Path:
    env = os.getenv("HUB_EXECUTION_AI_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "execution_ai.json"


def _read_file() -> Dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def models_config_path() -> Path:
    env = os.getenv("HUB_MODELS_CONFIG_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "models_config.json"


def _function_credential() -> Dict[str, Any]:
    """设置页功能凭证里被标记为「执行 AI」的那一条（active + 有 Key）"""
    path = models_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    entries: List[Dict[str, Any]] = []
    for cred in data.get("function_credentials") or []:
        if not isinstance(cred, dict) or cred.get("is_active") is False:
            continue
        api_key = str(cred.get("api_key") or "").strip()
        if not api_key:
            continue
        auth_type = str(cred.get("auth_type") or "official_api")
        base_url = str(cred.get("base_url") or "").strip()
        if not base_url:
            provider = str(cred.get("provider") or "").strip().lower()
            base_url = PROVIDER_BASE_URLS.get(provider, "")
        if not base_url:
            continue
        entries.append({
            "name": str(cred.get("name") or "功能凭证"),
            "api_key": api_key,
            "model": str(cred.get("model") or "").strip(),
            "base_url": base_url,
            "auth_type": auth_type,
            "for_execution": bool(cred.get("for_execution")),
        })
    for entry in entries:
        if entry["for_execution"]:
            return entry
    # 纯内部逻辑：没有显式标记时，用第一条启用的功能凭证
    return entries[0] if entries else {}


def mask_api_key(api_key: str) -> str:
    key = str(api_key or "")
    if not key:
        return ""
    if len(key) <= 8:
        return MASKED_KEY
    return f"{key[:4]}{MASKED_KEY}{key[-4:]}"


def is_masked(value: str) -> bool:
    raw = str(value or "")
    return not raw.strip() or MASKED_KEY in raw


def load_settings() -> Dict[str, Any]:
    """返回生效凭据（含明文 key，只给内部用，不要直接下发前端）

    优先级：设置页功能凭证（同一份凭据）→ data/execution_ai.json → 环境变量 → 主模型。
    """
    function = _function_credential()
    if function:
        return {
            "base_url": function["base_url"] or DEFAULT_BASE_URL,
            "model": function["model"] or DEFAULT_MODEL,
            "api_key": function["api_key"],
            "has_api_key": True,
            "source": "function",
            "credential_name": function["name"],
            "path": str(models_config_path()),
            "file_exists": True,
        }

    file_cfg = _read_file()
    api_key = str(file_cfg.get("api_key") or "").strip()
    base_url = str(file_cfg.get("base_url") or "").strip()
    model = str(file_cfg.get("model") or "").strip()
    source = "file" if api_key else ""

    if not api_key:
        api_key = os.getenv("HUB_EXEC_API_KEY", "").strip()
        if api_key:
            source = "env"
            base_url = base_url or os.getenv("HUB_EXEC_BASE_URL", "").strip()
            model = model or os.getenv("HUB_EXEC_MODEL", "").strip()

    if not api_key:
        source = "main"

    return {
        "base_url": base_url or DEFAULT_BASE_URL,
        "model": model or DEFAULT_MODEL,
        "api_key": api_key,
        "has_api_key": bool(api_key),
        "source": source,
        "path": str(settings_path()),
        "file_exists": settings_path().is_file(),
    }


def public_settings() -> Dict[str, Any]:
    data = load_settings()
    payload = {k: v for k, v in data.items() if k != "api_key"}
    payload["api_key"] = mask_api_key(data.get("api_key", ""))
    payload["masked"] = MASKED_KEY
    return payload


def _clean(value: Optional[str]) -> str:
    return str(value or "").strip()


def save_settings(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    *,
    clear_api_key: bool = False,
) -> Dict[str, Any]:
    """写回 data/execution_ai.json；掩码/空 key 表示沿用原值"""
    path = settings_path()
    existing = _read_file()

    next_base = _clean(base_url) or _clean(existing.get("base_url")) or DEFAULT_BASE_URL
    next_model = _clean(model) or _clean(existing.get("model")) or DEFAULT_MODEL

    if clear_api_key:
        next_key = ""
    elif is_masked(api_key):
        # 掩码/空值只表示“保持不动”；环境变量里的 Key 不落盘
        next_key = _clean(existing.get("api_key"))
    else:
        next_key = _clean(api_key)

    payload = {"base_url": next_base, "model": next_model, "api_key": next_key}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return public_settings()


def build_llm() -> LLMClient:
    cfg = load_settings()
    if cfg["source"] == "main":
        return LLMClient()
    return LLMClient(
        api_key=cfg["api_key"],
        model=cfg["model"],
        api_base=cfg["base_url"],
    )


def looks_like_failure(text: str) -> bool:
    raw = str(text or "").strip()
    return not raw or any(raw.startswith(prefix) for prefix in FALLBACK_PREFIXES)


async def test_connection(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    *,
    llm: Optional[LLMClient] = None,
) -> Dict[str, Any]:
    """用一组凭据或已保存凭据做一次最小对话，验证 key / base_url / model"""
    cfg = load_settings()
    use_base = _clean(base_url) or cfg["base_url"]
    use_model = _clean(model) or cfg["model"]
    if is_masked(api_key):
        use_key = cfg["api_key"]
    else:
        use_key = _clean(api_key)

    if not use_key:
        return {
            "ok": False,
            "base_url": use_base,
            "model": use_model,
            "error": "未配置执行 AI Key",
            "latency_ms": 0,
        }

    client = llm or LLMClient(api_key=use_key, model=use_model, api_base=use_base)
    started = time.time()
    try:
        text, _ = await client.generate_with_tools(
            system_prompt="你是连通性探针，只回复 OK，不要解释。",
            messages=[{"role": "user", "content": "只回复 OK"}],
            tool_registry=None,
            max_tool_turns=1,
            temperature=0.0,
        )
    except Exception as exc:  # 网络/鉴权异常
        return {
            "ok": False,
            "base_url": use_base,
            "model": use_model,
            "error": str(exc)[:500],
            "latency_ms": int((time.time() - started) * 1000),
        }

    latency = int((time.time() - started) * 1000)
    reply = str(text or "").strip()
    if looks_like_failure(reply):
        return {
            "ok": False,
            "base_url": use_base,
            "model": use_model,
            "error": reply[:500] or "模型没有返回内容",
            "latency_ms": latency,
        }
    return {
        "ok": True,
        "base_url": use_base,
        "model": use_model,
        "reply": reply[:200],
        "latency_ms": latency,
    }
