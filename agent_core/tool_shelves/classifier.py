# -*- coding: utf-8 -*-
"""执行 AI 分类器：只发「时间 + 最近三句 + 大类名与一句话简介」。

- 只走云端 DeepSeek（logic_slot 的第一个候选凭证）。
- 单次调用、超时即放弃，不做本机兜底、不做多凭证轮换（应用层直接全量放行）。
- 输出严格 JSON，解析失败即视为失败。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ToolShelves.Classifier")

_DEFAULT_CATEGORY_BRIEFS = {
    "media": "影音类：放歌、切歌、音量、点播视频、生成图片、生图额度查询",
    "search": "搜索类：联网搜资料、读网页正文、查实时信息、查余额",
    "server": "服务器类：宿主机 PowerShell、进程/服务/文件/系统状态、天气",
    "platform": "平台类：B站热榜/投稿/评论/回复",
}

_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class ClassifierResult:
    ok: bool = False
    need_tools: bool = False
    categories: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    error: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: str = ""


def _now_line() -> str:
    """给分类器的时间上下文：本地时区，精确到分钟。"""
    try:
        now = time.localtime()
        weekday = "一二三四五六日"[now.tm_wday]
        return time.strftime("%Y-%m-%d %H:%M", now) + f" 周{weekday}"
    except Exception:
        return ""


def build_system_prompt(categories: Dict[str, Dict[str, Any]], settings: Optional[dict] = None) -> str:
    """静态前缀：类别清单 + 输出契约，不含动态内容（便于上游缓存）。

    默认口径：只有出现**明确的动作意图**才选类别；情绪、抱怨、状态陈述一律
    need_tools=false。理由：错误的类别会把主 AI 引向"用工具解决情绪问题"，
    这比少给工具更伤。需要"半夜关灯→联想到放歌"时，把
    gateway.classifier.situational_inference 设为 true。
    """
    gateway = (settings or {}).get("gateway") or {}
    cls_cfg = gateway.get("classifier") or {}
    situational = bool(cls_cfg.get("situational_inference", False))

    lines = [
        "你是工具分类器。根据情境判断这一轮可能需要哪几类工具，只输出 JSON，不要解释。",
        "可选类别：",
    ]
    if categories:
        for key, spec in categories.items():
            label = str(spec.get("label") or key)
            brief = str(spec.get("brief") or _DEFAULT_CATEGORY_BRIEFS.get(key, ""))
            hint = str(spec.get("hint") or "").strip() if situational else ""
            line = f"- {key} {label}：{brief}"
            if hint:
                line += f"（{hint}）"
            lines.append(line)
    else:
        for key, brief in _DEFAULT_CATEGORY_BRIEFS.items():
            lines.append(f"- {key} {brief}")

    lines.append(
        '输出格式：{"need_tools":true/false,"categories":["..."],"confidence":0.0,"reason":"不超过20字"}'
    )
    lines.append("规则：")
    if situational:
        lines.append(
            "1. 没有明确指令、但情境暗示可能要动手时（关灯、躺下、夜深、睡不着、无聊），"
            "按最可能的意图选类别，不要因为“没说要干嘛”就判 false。"
        )
    else:
        lines.append(
            "1. 用户没有明确说要做什么时，need_tools=false；只有出现明确的动作意图"
            "（想听歌、想查什么、想关灯、想看视频、想让你动手）才选类别。"
        )
    lines.append(
        "2. 纯闲聊、情绪宣泄、抱怨累、状态陈述（我困了/灯关了/好无聊）、只有语气词"
        "（嗯/哈哈/好）或只是回应上一句，need_tools=false。"
    )
    lines.append("3. 只选真正可能用到的类别，宁少不多；不确定时按最接近的一类。")
    lines.append("4. categories 只能用上面列出的英文 key。")
    return "\n".join(lines)


def build_user_prompt(
    user_query: str,
    recent_lines: List[str],
    channel_type: str = "",
    platform: str = "",
) -> str:
    scene = " ".join(x for x in [platform or "", channel_type or ""] if x).strip()
    parts = []
    if _now_line():
        parts.append(f"当前时间：{_now_line()}")
    if scene:
        parts.append(f"场景：{scene}")
    if recent_lines:
        parts.append("最近三句：\n" + "\n".join(f"  {line}" for line in recent_lines))
    parts.append(f"当前：{str(user_query or '').strip()[:300]}")
    return "\n".join(parts)


def parse_output(text: str, categories: Dict[str, Dict[str, Any]]) -> ClassifierResult:
    """严格解析；容忍 ```json 包裹和前后废话。"""
    result = ClassifierResult(raw=str(text or "")[:500])
    match = _JSON_RE.search(str(text or ""))
    if not match:
        result.error = "no_json"
        return result
    try:
        data = json.loads(match.group(0))
    except Exception as exc:
        result.error = f"json_error:{exc}"
        return result
    if not isinstance(data, dict):
        result.error = "not_object"
        return result

    valid = set(categories.keys()) or set(_DEFAULT_CATEGORY_BRIEFS.keys())
    cats: List[str] = []
    raw_cats = data.get("categories")
    if isinstance(raw_cats, str):
        raw_cats = [raw_cats]
    if isinstance(raw_cats, list):
        for item in raw_cats:
            name = str(item or "").strip().lower()
            if name in valid and name not in cats:
                cats.append(name)

    need = data.get("need_tools")
    if isinstance(need, str):
        need = need.strip().lower() in ("true", "1", "yes", "y")
    need = bool(need)

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    result.ok = True
    result.need_tools = need
    result.categories = cats
    result.confidence = confidence
    result.reason = str(data.get("reason") or "")[:40]
    return result


def complete_once(
    system: str,
    user: str,
    settings: Optional[Dict[str, Any]] = None,
    slot: str = "logic_slot",
    timeout_ms: Optional[int] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[str, int, int, str]:
    """单次、单凭证调用执行 AI（DeepSeek）。

    分类器与结果清洗器共用。刻意不做多凭证轮换、不做本地兜底：
    失败就返回错误，由调用方决定"全量放行 / 原文放行"。

    返回 (内容, 输入 tokens, 输出 tokens, 错误)。
    """
    settings = settings or {}
    gateway = settings.get("gateway") or {}
    cls_cfg = gateway.get("classifier") or {}
    timeout_ms = int(timeout_ms or cls_cfg.get("timeout_ms") or 2500)
    if temperature is None:
        temperature = float(cls_cfg.get("temperature") or 0.0)
    if max_tokens is None:
        max_tokens = int(cls_cfg.get("max_tokens") or 120)

    try:
        from agent_core.compute_providers import compute_manager
    except Exception as exc:
        return "", 0, 0, f"compute_unavailable:{type(exc).__name__}"

    try:
        candidates = compute_manager._candidate_providers(slot)
    except Exception as exc:
        return "", 0, 0, f"provider_error:{type(exc).__name__}"
    if not candidates:
        return "", 0, 0, "no_provider"

    provider = candidates[0]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        data = compute_manager._execute_request(
            provider,
            messages,
            temperature,
            max_tokens,
            max(1.0, timeout_ms / 1000.0),
            None,
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        err = type(exc).__name__ + (f":HTTP{code}" if code else "")
        logger.info("[ToolShelves] 执行 AI 调用失败，走兜底: %s", exc)
        return "", 0, 0, f"request_failed:{err}"

    if not data or not data.get("choices"):
        return "", 0, 0, "empty_response"

    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    usage = data.get("usage") or {}
    return (
        str(content),
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        "",
    )


def classify(
    user_query: str,
    recent_lines: List[str],
    categories: Dict[str, Dict[str, Any]],
    settings: Dict[str, Any],
    channel_type: str = "",
    platform: str = "",
) -> ClassifierResult:
    """调用 DeepSeek 分类。任何异常都返回 ok=False，由调用方走全量放行。"""
    gateway = (settings or {}).get("gateway") or {}
    cls_cfg = gateway.get("classifier") or {}
    slot = str(cls_cfg.get("slot") or "logic_slot")
    timeout_ms = int(cls_cfg.get("timeout_ms") or 2500)
    temperature = float(cls_cfg.get("temperature") or 0.0)
    max_tokens = int(cls_cfg.get("max_tokens") or 120)

    messages = [
        {"role": "system", "content": build_system_prompt(categories, settings)},
        {"role": "user", "content": build_user_prompt(user_query, recent_lines, channel_type, platform)},
    ]

    started = time.time()
    content, p_in, p_out, err = complete_once(
        messages[0]["content"],
        messages[1]["content"],
        settings,
        slot=slot,
        timeout_ms=timeout_ms,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    latency = int((time.time() - started) * 1000)
    if err:
        result = ClassifierResult(error=err)
        result.latency_ms = latency
        return result

    result = parse_output(content, categories)
    result.latency_ms = latency
    result.prompt_tokens = p_in
    result.completion_tokens = p_out
    result.raw = str(content or "")[:500]
    if not result.ok:
        logger.info("[ToolShelves] 分类器输出无法解析: %s | raw=%s", result.error, result.raw[:120])
    return result


__all__ = [
    "ClassifierResult",
    "classify",
    "complete_once",
    "parse_output",
    "build_system_prompt",
    "build_user_prompt",
]
