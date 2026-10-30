# -*- coding: utf-8 -*-
"""双填匹配：名称 + UID 两格，交叉验证已发现的外部上下文"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_SPLIT_RE = re.compile(r"[,，;\n；]+")


def normalize_terms(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw = [str(v) for v in value]
    else:
        raw = _SPLIT_RE.split(str(value))
    out: List[str] = []
    seen = set()
    for item in raw:
        text = item.strip().strip('"').strip("'").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _name_score(context: Dict[str, Any], name: str) -> float:
    low = name.casefold()
    candidates = [str(context.get("display_name") or "").strip()]
    extra = context.get("aliases")
    if isinstance(extra, (list, tuple)):
        candidates.extend(str(x).strip() for x in extra)
    best = 0.0
    for candidate in candidates:
        if not candidate:
            continue
        clow = candidate.casefold()
        if clow == low:
            best = max(best, 0.95)
        elif low in clow or clow in low:
            best = max(best, 0.75)
    external = str(context.get("external_id") or "")
    if low and low in external.casefold():
        best = max(best, 0.55)
    return best


def _uid_score(context: Dict[str, Any], uid: str) -> float:
    external = str(context.get("external_id") or "")
    low = uid.strip().casefold()
    if not low:
        return 0.0
    elow = external.casefold()
    if elow == low:
        return 0.98
    if elow.endswith(":" + low):
        return 0.95
    if low in elow:
        return 0.8
    return 0.0


def match_contexts(
    contexts: Iterable[Dict[str, Any]],
    names: Any,
    uids: Any,
    platform: Optional[str] = None,
) -> List[Dict[str, Any]]:
    name_list = normalize_terms(names)
    uid_list = normalize_terms(uids)
    if not name_list and not uid_list:
        return []

    matches: Dict[tuple, Dict[str, Any]] = {}
    for context in contexts:
        if platform and str(context.get("platform")) != platform:
            continue

        best_name, best_name_score = "", 0.0
        for name in name_list:
            score = _name_score(context, name)
            if score > best_name_score:
                best_name, best_name_score = name, score

        best_uid, best_uid_score = "", 0.0
        for uid in uid_list:
            score = _uid_score(context, uid)
            if score > best_uid_score:
                best_uid, best_uid_score = uid, score

        if not best_name_score and not best_uid_score:
            continue

        if best_name_score and best_uid_score:
            match_type = "paired"
            confidence = 0.95
            reason = ["名称+UID 同一条记录"]
        elif best_name_score:
            match_type = "name"
            confidence = best_name_score
            reason = ["仅名称匹配"]
        else:
            match_type = "uid"
            confidence = best_uid_score
            reason = ["仅 UID 匹配"]

        key = (context.get("platform"), context.get("external_id"))
        item = {
            "platform": context.get("platform"),
            "external_id": context.get("external_id"),
            "display_name": context.get("display_name") or "",
            "external_type": context.get("external_type") or "",
            "status": context.get("status") or "active",
            "name": best_name,
            "uid": best_uid,
            "match_type": match_type,
            "confidence": round(confidence, 2),
            "reason": reason,
        }
        if key not in matches or item["confidence"] > matches[key]["confidence"]:
            matches[key] = item

    return sorted(
        matches.values(),
        key=lambda m: (m["confidence"], m["match_type"] == "paired"),
        reverse=True,
    )
