# -*- coding: utf-8 -*-
"""深时记忆检索：按时间范围 + 关键词翻记忆库，按字数做闸门。

回答的问题：「我前天在 QQ 上给你发小鱼干了」——时间轴只覆盖最近十几小时，
要翻更早的记录，但不能把一天的量整段灌进上下文。

闸门（正文 = 命中的时间轴叙述字数）：

    ≤ 2,000 字   原样返回（full）
    ≤ 8,000 字   只给叙述（narrative）
    > 8,000 字   压缩后返回，并提示「需要 AI 摘要」（compressed）

原文（llm_request 等）不在这里返回，避免一次性把记忆库灌进上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

GATE_PASS_CHARS = 2_000
GATE_NARRATIVE_CHARS = 8_000
DEFAULT_BUDGET = 8_000


@dataclass
class Hit:
    event_id: str
    time_str: str
    timestamp: float
    narrative: str
    source: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "time": self.time_str,
            "narrative": self.narrative,
            "source": self.source,
        }


def choose_mode(total_chars: int) -> str:
    """按字数决定给主 AI 的形态。"""
    if total_chars <= GATE_PASS_CHARS:
        return "full"
    if total_chars <= GATE_NARRATIVE_CHARS:
        return "narrative"
    return "compressed"


def parse_terms(query: str) -> List[str]:
    """关键词拆分：空白、逗号、顿号、竖线都算分隔。"""
    raw = str(query or "")
    for sep in ("，", "、", "|", ";", "；"):
        raw = raw.replace(sep, ",")
    parts = [item.strip() for item in raw.replace(",", " ").split() if item.strip()]
    return parts[:8]


def trim_hits(hits: Iterable[Hit], budget: int, ratio: float = 0.6) -> Tuple[List[Hit], str]:
    """按预算裁剪；超预算时从最旧开始压缩，返回 (命中的子集, 模式)。"""
    ordered = sorted(hits, key=lambda item: item.timestamp, reverse=True)
    total = sum(len(item.narrative) for item in ordered)
    mode = choose_mode(total)
    if total <= budget or not ordered:
        return ordered, mode

    from .timeline_curve import compress_narrative

    kept: List[Hit] = []
    used = 0
    for hit in ordered:
        text = compress_narrative(hit.narrative, ratio) if mode == "compressed" else hit.narrative
        if used + len(text) > budget:
            break
        kept.append(Hit(hit.event_id, hit.time_str, hit.timestamp, text, hit.source))
        used += len(text)
    return kept, mode


def summarize(hits: List[Hit], mode: str, total_chars: int) -> Dict[str, Any]:
    return {
        "mode": mode,
        "hits": len(hits),
        "chars": sum(len(item.narrative) for item in hits),
        "original_chars": total_chars,
        "needs_ai_summary": mode == "compressed",
        "note": {
            "full": "记录不多，原样给主 AI",
            "narrative": "只给精简叙述，原文按需再取",
            "compressed": "已压缩；必要时交执行 AI 再摘要",
        }[mode],
    }


def parse_time_bound(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    """时间边界：支持 unix 秒、ISO 日期/时间、YYYY-MM-DD HH:MM。"""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    from datetime import datetime

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return default
