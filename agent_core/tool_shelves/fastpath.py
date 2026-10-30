# -*- coding: utf-8 -*-
"""零模型调用的硬规则快判。

三条路径：
- trivial      纯语气词/寒暄，且不含动作词 → 不用工具
- tool_result  最后一条是工具返回值 → 沿用上一轮类别（链条不断）
- classify     其余 → 交给执行 AI 选类别

快判只做"要不要问小模型"，不做"选哪一类"；选类是小模型的事。
"""

from __future__ import annotations

import re

PATH_TRIVIAL = "fast_trivial"
PATH_TOOL_RESULT = "tool_result"
PATH_CLASSIFY = "classify"
PATH_SKIP = "skip"


def last_user_text(messages) -> str:
    """取最后一条 user 消息的纯文本。"""
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for sub in content:
                if isinstance(sub, dict) and sub.get("type") == "text":
                    parts.append(str(sub.get("text") or ""))
            return "".join(parts)
        return ""
    return ""


def last_role(messages) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role"):
            return str(msg.get("role"))
    return ""


def last_tool_name(messages) -> str:
    """最后一条消息是工具返回时，尽量取出工具名（部分上游不带 name 字段）。"""
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            return ""
        return str(msg.get("name") or msg.get("tool_name") or "")
    return ""


def normalize_text(text: str) -> str:
    """去掉首尾空白、@、括号指令、表情与标点，用于短句判定。"""
    t = str(text or "").strip()
    if not t:
        return ""
    # 群聊前缀 "[群] 用户(123): 嗯"
    t = re.sub(r"^\[[^\]]{0,20}\]\s*", "", t)
    t = re.sub(r"^.*?\(\d{5,12}\)[:：]\s*", "", t)
    # 纯表情/指令，如 "[CQ:face,id=1]"、"[图片]"
    t = re.sub(r"\[[^\]]{0,40}\]", "", t)
    # 标点与空白
    t = re.sub(r"[\s，。！？、,.!?~～…:：;；\"'“”‘’()（）【】]+", "", t)
    return t


def is_trivial(text: str, settings: dict) -> bool:
    fast = (settings or {}).get("fast_path") or {}
    regex = str(fast.get("trivial_regex") or "").strip()
    if not regex:
        return False
    norm = normalize_text(text)
    if not norm:
        return True
    min_chars = int(fast.get("min_chars") or 2)
    max_chars = int(fast.get("max_chars") or 10)
    if len(norm) > max_chars:
        return False
    try:
        if re.match(regex, norm, re.IGNORECASE):
            return True
    except re.error:
        return False
    # 单字语气词兜底（正则写不全会漏）
    if len(norm) <= min_chars and norm in {
        "嗯", "啊", "哦", "哈", "好", "行", "对", "是", "不", "喂", "嘿", "诶", "额",
    }:
        return True
    return False


def has_force_keyword(text: str, settings: dict) -> bool:
    fast = (settings or {}).get("fast_path") or {}
    regex = str(fast.get("force_tools_regex") or "").strip()
    if not regex or not text:
        return False
    try:
        return bool(re.search(regex, str(text), re.IGNORECASE))
    except re.error:
        return False


def route(messages, settings: dict) -> str:
    """返回 fast_trivial / tool_result / classify / skip。"""
    if not messages:
        return PATH_SKIP
    role = last_role(messages)
    if role == "tool":
        return PATH_TOOL_RESULT
    if role != "user":
        # assistant 结尾的续写请求：不给新工具，沿上轮
        return PATH_TOOL_RESULT
    text = last_user_text(messages)
    if not text.strip():
        return PATH_SKIP
    if has_force_keyword(text, settings):
        return PATH_CLASSIFY
    if is_trivial(text, settings):
        return PATH_TRIVIAL
    return PATH_CLASSIFY


__all__ = [
    "route",
    "is_trivial",
    "has_force_keyword",
    "normalize_text",
    "last_user_text",
    "last_role",
    "last_tool_name",
    "PATH_TRIVIAL",
    "PATH_TOOL_RESULT",
    "PATH_CLASSIFY",
    "PATH_SKIP",
]
