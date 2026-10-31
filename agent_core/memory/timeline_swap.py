# -*- coding: utf-8 -*-
"""时间轴换页：腾出 / 回填（纯函数，不碰数据库）。

约定：events 按「新 → 旧」排列，和曲线视图一致。
腾出顺序：先动尾部 30%，不够再动中部 40%，但绝不进前 30%（确定部分）。
回填顺序：先进先出，按剩余额度比例取回。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

TAIL_SHARE = 0.30
MIDDLE_SHARE = 0.40
KEEP_SHARE = 0.30  # 确定部分：任何情况下都不动


def _chars(events: Sequence[Dict[str, Any]]) -> int:
    return sum(len(str(item.get("narrative") or "")) for item in events)


def release(events: List[Dict[str, Any]], need_chars: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """从旧的一端腾出 need_chars 字；返回 (保留, 腾出)。

    腾出量会被「确定部分」下限卡住：最多丢掉总量的 70%。
    """
    if need_chars <= 0 or not events:
        return list(events), []
    total = _chars(events)
    floor = int(total * KEEP_SHARE)
    budget = max(0, total - floor)  # 可腾出的上限
    want = min(int(need_chars), budget)
    if want <= 0:
        return list(events), []
    released: List[Dict[str, Any]] = []
    freed = 0
    cut = len(events)
    for index in range(len(events) - 1, -1, -1):
        if freed >= want:
            break
        released.append(events[index])
        freed += len(str(events[index].get("narrative") or ""))
        cut = index
    released.reverse()  # 保持「较新在前」
    return list(events[:cut]), released


def refill(released: List[Dict[str, Any]], free_chars: int, ratio: float = 1.0) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按额度比例把腾出的记忆接回来；返回 (接回, 剩余)。

    ratio 允许只回填一部分（例如 0.8），剩下的留给下一轮或归档。
    """
    if free_chars <= 0 or not released:
        return [], list(released)
    target = int(free_chars * max(0.0, min(float(ratio), 1.0)))
    taken: List[Dict[str, Any]] = []
    used = 0
    cut = 0
    for index, item in enumerate(released):
        size = len(str(item.get("narrative") or ""))
        if used + size > target:
            break
        taken.append(item)
        used += size
        cut = index + 1
    return taken, list(released[cut:])


def archive_candidates(rest: List[Dict[str, Any]], keep_chars: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """缓冲区超容量时：最老的归档进记忆库，剩下的留着。"""
    if keep_chars <= 0:
        return [], list(rest)
    kept: List[Dict[str, Any]] = []
    used = 0
    for item in rest:
        size = len(str(item.get("narrative") or ""))
        if used + size <= keep_chars:
            kept.append(item)
            used += size
        else:
            break
    return kept, list(rest[len(kept):])
