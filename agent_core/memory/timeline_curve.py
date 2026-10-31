# -*- coding: utf-8 -*-
"""时间轴记忆曲线：越靠近现在越完整，越靠后越精简。

分层按**字数份额**而不是条数（一条长记忆不该顶掉十条短的）：

    前 30% 字数  完整（level 0，不改一个字）
    中 40% 字数  轻度压缩（level 1，默认保留 85%）
    后 30% 字数  深度压缩（level 2，默认保留 60%）

压缩只在句读处下刀，且不会把一句话引文切成两半；被挤出预算的事件不进视图，
它们的完整数据本来就已经在记忆库里（memory_details）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

DEFAULT_BUDGET_CHARS = 9_000
ENV_BUDGET = "HUB_TIMELINE_BUDGET"
DEFAULT_TIER_SHARES = (0.30, 0.40, 0.30)
DEFAULT_LEVELS = (1.00, 0.85, 0.60)
TAIL_ENTRY_CHARS = 80  # 尾段按字数压：一条最多 80 字，不再按比例
_CLAUSE_ENDS = "。！？；\n"
MIN_COMPRESS_CHARS = 100   # 短句本来就是一句话，压它只会压出歧义
_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'))


@dataclass
class CurveEntry:
    event_id: str
    tier: int
    text: str
    original_chars: int
    kept_chars: int

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "tier": self.tier,
            "text": self.text,
            "original_chars": self.original_chars,
            "kept_chars": self.kept_chars,
        }


def _cut_at_boundary(text: str, limit: int) -> str:
    """在句读处截断；如果落在引号内就补到收尾引号，避免半句引文。"""
    if limit >= len(text):
        return text
    cut = limit
    floor = int(limit * 0.6)
    for index in range(limit, floor, -1):
        if text[index - 1] in _CLAUSE_ENDS:
            cut = index
            break
    cut = _extend_past_quote(text, cut)
    if cut <= 0 or cut >= len(text):
        return text
    return text[:cut].rstrip() + "…"


def _extend_past_quote(text: str, cut: int) -> int:
    """落在「…」中间：近距离补到收尾引号，太远就退到引号前，别压出半句话。"""
    for opener, closer in _QUOTE_PAIRS:
        if opener == closer:
            continue
        opens = text.count(opener, 0, cut) - text.count(closer, 0, cut)
        if opens <= 0:
            continue
        close_at = text.find(closer, cut)
        if close_at != -1 and close_at - cut <= 20:
            return min(close_at + 1, len(text))
        opened_at = text.rfind(opener, 0, cut)
        if opened_at > 0:
            return opened_at
    return cut


def compress_narrative(text: str, ratio: float) -> str:
    """按比例轻度压缩；ratio>=1 原样返回。"""
    text = str(text or "")
    if ratio >= 1.0 or len(text) < MIN_COMPRESS_CHARS:
        return text
    limit = max(20, int(len(text) * ratio))
    return _cut_at_boundary(text, limit)


def budget_chars() -> int:
    """时间轴注入预算（字）；HUB_TIMELINE_BUDGET 覆盖，默认 8000。"""
    import os

    try:
        value = int(os.getenv(ENV_BUDGET, "") or 9000)
    except ValueError:
        value = 8000
    return max(500, min(value, 200_000))


def build_curve(
    events: Iterable,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    tier_shares: Sequence[float] = DEFAULT_TIER_SHARES,
    levels: Sequence[float] = DEFAULT_LEVELS,
    line_overhead: int = 0,
    allow_overflow: bool = True,
) -> List[CurveEntry]:
    """按新→旧铺满预算，并按字数份额分层压缩。"""
    ordered = sorted(events, key=lambda item: float(getattr(item, "timestamp", 0) or 0),
                     reverse=True)
    boundaries = _tier_boundaries(budget_chars, tier_shares)
    entries: List[CurveEntry] = []
    used = 0
    for event in ordered:
        text = str(getattr(event, "narrative", "") or "")
        cost = len(text) + max(0, line_overhead)
        if not text:
            continue
        if used + cost > budget_chars:
            # 允许整条浮动：还没到预算就再收一条，避免尾部出现半条缝
            if allow_overflow and used < budget_chars:
                pass
            else:
                break
        tier = _tier_for(used, boundaries)
        meta = getattr(event, "metadata", {}) or {}
        already = bool(meta.get("compressed")) if isinstance(meta, dict) else False
        if already:
            kept = text
        elif tier >= len(boundaries):
            kept = _cut_at_boundary(text, TAIL_ENTRY_CHARS)  # 尾段按字数
        else:
            kept = compress_narrative(text, levels[tier])  # 中段按比例
        entries.append(CurveEntry(
            event_id=str(getattr(event, "event_id", "")),
            tier=tier,
            text=kept,
            original_chars=len(text),
            kept_chars=len(kept),
        ))
        used += cost
    return entries


def _tier_boundaries(budget: int, shares: Sequence[float]) -> List[float]:
    bounds = []
    acc = 0.0
    for share in shares[:-1]:
        acc += float(share)
        bounds.append(budget * acc)
    return bounds


def _tier_for(used: int, boundaries: Sequence[float]) -> int:
    for index, edge in enumerate(boundaries):
        if used < edge:
            return index
    return len(boundaries)


def summarize(entries: List[CurveEntry]) -> dict:
    """给接口/控制台看的汇总。"""
    tiers = [0] * (max((item.tier for item in entries), default=-1) + 1)
    for item in entries:
        tiers[item.tier] += 1
    return {
        "events": len(entries),
        "original_chars": sum(item.original_chars for item in entries),
        "kept_chars": sum(item.kept_chars for item in entries),
        "per_tier": tiers,
    }


def render(entries: List[CurveEntry]) -> str:
    """拼成注入用的文本块。"""
    return "\n".join(f"[{item.event_id}] {item.text}" for item in entries)
