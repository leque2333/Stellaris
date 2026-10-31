# -*- coding: utf-8 -*-
"""人格卡裁剪：对话范例原样保留，其余部分压到指定字数。

为什么只压其余部分：对话范例决定「怎么说」，压了会丢性格反差；
定位/语言法则/称呼规范属于「设定」，可以更紧凑地表达。
"""

from __future__ import annotations

from typing import List, Tuple

DEFAULT_OTHER_LIMIT = 1_000
DIALOGUE_MARKERS = ("对话范例", "黄金对白", "经典情境")


def split_sections(text: str) -> List[Tuple[str, str]]:
    """按 `## ` 标题切成 [(标题, 正文)]，正文含标题行。"""
    lines = str(text or "").splitlines()
    sections: List[Tuple[str, str]] = []
    current_title, current = "", []
    for line in lines:
        if line.startswith("## "):
            if current:
                sections.append((current_title, "\n".join(current)))
            current_title, current = line.strip("# ").strip(), [line]
        else:
            current.append(line)
    if current:
        sections.append((current_title, "\n".join(current)))
    return sections


def is_dialogue(title: str) -> bool:
    return any(marker in title for marker in DIALOGUE_MARKERS)


def trim_persona(text: str, limit: int = DEFAULT_OTHER_LIMIT) -> str:
    """对话范例保持原样；其余章节合计压到 limit 字以内（按章节均分）。"""
    sections = split_sections(text)
    if not sections:
        return str(text or "")
    dialogue = [(title, body) for title, body in sections if is_dialogue(title)]
    others = [(title, body) for title, body in sections if not is_dialogue(title)]
    if not others or limit <= 0:
        return str(text or "")
    keep_chars = max(0, int(limit))
    per_section = max(60, keep_chars // max(1, len(others)))
    trimmed: List[str] = []
    for title, body in others:
        trimmed.append(_trim_section(body, per_section))
    merged = "\n\n".join(trimmed)
    if dialogue:
        merged += "\n\n" + "\n\n".join(body for _, body in dialogue)
    return merged.strip() + "\n"


def _trim_section(body: str, limit: int) -> str:
    """按行截断，尽量停在完整句子/条目前。"""
    if len(body) <= limit:
        return body
    kept: List[str] = []
    used = 0
    for line in body.splitlines():
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    if not kept:
        kept = [body[:limit]]
    result = "\n".join(kept).rstrip()
    return result if result.endswith(("。", "！", "？", "」", "）")) else result + "。"


def compact_prompt(text: str, limit: int = DEFAULT_OTHER_LIMIT) -> str:
    """对外入口：已经够短就原样返回。"""
    text = str(text or "")
    if not text or len(text) <= limit:
        return text
    return trim_persona(text, limit)
