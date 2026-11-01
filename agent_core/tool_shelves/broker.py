# -*- coding: utf-8 -*-
"""主 AI 的工具申请入口：按意图临时加载工具。

这不是执行器：主 AI 申请到工具后，仍然按正常 tool_call 交给 AstrBot 执行。
中枢只负责从「已授权工具目录」里挑出最多 N 个，塞进下一轮工具表。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from .catalog import tool_description, tool_name

TOOL_NAME = "hub_request_tools"
DEFAULT_LIMIT = 3
MAX_LIMIT = 5


def tool_spec() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "当前工具表里没有合适工具时，用一句话说明你要做的事，"
                "中枢会从已授权工具里再加载最多几个。拿到工具后直接调用，不要只申请。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "要完成的任务，例如：生成一张图片 / 查 B 站评论",
                    },
                    "max_tools": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIMIT,
                    },
                },
                "required": ["intent"],
            },
        },
    }


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _terms(intent: str) -> List[str]:
    text = _clean(intent)
    if not text:
        return []
    parts = [item for item in re.split(r"[\s,，。；;、|/]+", text) if item]
    terms = [text]
    for item in parts:
        if item not in terms:
            terms.append(item)
    # 中文长句补 2~4 字片段，避免工具描述只命中半句
    if len(text) >= 2:
        for size in (2, 3, 4):
            for index in range(0, min(len(text) - size + 1, 8)):
                piece = text[index:index + size]
                if piece not in terms:
                    terms.append(piece)
    return terms[:40]


def parse_request(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    args = ((tool_call or {}).get("function") or {}).get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    try:
        limit = int(args.get("max_tools") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return {
        "intent": str(args.get("intent") or "").strip(),
        "limit": max(1, min(limit, MAX_LIMIT)),
    }


def select_tools(
    intent: str,
    tools: Sequence[Dict[str, Any]],
    *,
    exclude: Optional[Sequence[str]] = None,
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    """按意图关键词挑工具；没有命中时按名字排序取前几个。"""
    excluded = set(exclude or [])
    terms = _terms(intent)
    scored = []
    for tool in tools or []:
        name = tool_name(tool)
        if not name or name in excluded or name == TOOL_NAME:
            continue
        text = _clean(name + " " + tool_description(tool))
        score = 0
        if terms and terms[0] in text:
            score += 12
        for term in terms[1:]:
            if term and term in text:
                score += 2 + min(len(term), 5)
        for part in name.split("_"):
            if part and part in _clean(intent):
                score += 1
        scored.append((score, name, tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked = [item[2] for item in scored[:max(1, int(limit))]]
    return picked


def result_payload(tools: Sequence[Dict[str, Any]]) -> str:
    items = []
    for tool in tools:
        items.append({
            "name": tool_name(tool),
            "description": tool_description(tool)[:300],
        })
    return json.dumps({
        "loaded": len(items),
        "tools": items,
        "note": "已加入下一轮工具表，直接调用即可；不要重复申请。",
    }, ensure_ascii=False)


__all__ = [
    "TOOL_NAME",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "tool_spec",
    "parse_request",
    "select_tools",
    "result_payload",
]
