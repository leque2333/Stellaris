# -*- coding: utf-8 -*-
"""好感度自动评估（方案 A）。

每次交流结束后，用轻量模型判断这次交流让好感度变化多少：
- 档案还没定值 -> 给一个初始好感（0-100）
- 已有值      -> 给一个 delta（-10 ~ +10）
- 没有明显变化 -> delta 0，不写库

不影响主模型生成，异步执行，失败静默。
关掉：HUB_AFFECTION_AI=0
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

_last_eval = 0.0
_eval_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv("HUB_AFFECTION_AI", "1") not in ("0", "false", "False")


def _throttled(min_interval: float) -> bool:
    """同一时间内只评估一次，避免连发消息刷好感。"""
    global _last_eval
    now = time.time()
    with _eval_lock:
        if now - _last_eval < min_interval:
            return True
        _last_eval = now
        return False


def _chat(messages, timeout: float = 25.0):
    from ..compute_providers import compute_manager

    out, p_in, p_out = compute_manager.chat_completion(
        os.getenv("HUB_AFFECTION_AI_SLOT", "logic_slot"), messages, timeout=timeout,
    )
    return str(out or "")


def _parse_json_object(text: str) -> dict:
    text = str(text or "").strip()
    if not text:
        return {}
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def estimate_initial_affection(user_text: str, assistant_text: str = "") -> int | None:
    """第一次交流：给一个 0-100 的初始好感度。"""
    if not _enabled():
        return None
    text = str(user_text or "").strip()
    if not text:
        return None
    try:
        out = _chat([
            {
                "role": "system",
                "content": (
                    "你刚和这个人第一次交流。根据这次对话，给出你对 ta 的初始好感度。"
                    "只输出 0-100 的整数，不要解释，不要标点。"
                ),
            },
            {"role": "user", "content": f"对方说：{text[:500]}\n你的回答：{str(assistant_text or '')[:500]}"},
        ])
        match = re.search(r"-?\d+", out)
        if not match:
            return None
        return max(0, min(100, int(match.group())))
    except Exception:
        return None


def estimate_affection_delta(
    user_text: str,
    assistant_text: str,
    current: int,
    min_interval: float = 60.0,
):
    """评估这轮交流的好感变化，返回 (delta, reason)；无可评估返回 None。"""
    if not _enabled():
        return None
    text = str(user_text or "").strip()
    if not text:
        return None
    if _throttled(min_interval):
        return None
    try:
        out = _chat([
            {
                "role": "system",
                "content": (
                    "你在判断一段对话对你的好感影响。只看对方这一轮的表现，"
                    "不要因为寒暄、道谢、普通提问就加分。"
                    '只输出 JSON：{"delta": 整数, "reason": "不超过20字的理由"}。'
                    'delta 范围 -10 到 10；没有明显变化就输出 {"delta": 0, "reason": ""}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前好感度：{current}/100\n"
                    f"对方说：{text[:600]}\n"
                    f"你的回答：{str(assistant_text or '')[:600]}"
                ),
            },
        ])
        data = _parse_json_object(out)
        if not data:
            return None
        try:
            delta = int(data.get("delta", 0))
        except (TypeError, ValueError):
            return None
        delta = max(-10, min(10, delta))
        reason = str(data.get("reason") or "").strip()[:40]
        if delta == 0:
            return None
        return delta, reason or "交流后自动评估"
    except Exception:
        return None


__all__ = ["estimate_initial_affection", "estimate_affection_delta"]