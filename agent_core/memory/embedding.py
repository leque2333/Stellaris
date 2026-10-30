# -*- coding: utf-8 -*-
"""本地轻量文本向量：确定性哈希 n-gram，不依赖外部 embedding API。

- 768 维 Float32，和 agent_timeline.embedding 的既有格式一致；
- 词 + 字符 2/3-gram，带符号哈希，L2 归一化；
- 查询与写入用同一函数，保证可比较；
- 以后接真实 embedding 服务时，只需替换 embed_text_bytes 的实现。
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import unicodedata
from typing import List

DIM = 768
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokens(text: str) -> List[tuple[str, float]]:
    text = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    if not text:
        return []
    out: List[tuple[str, float]] = []
    for word in _WORD_RE.findall(text):
        if len(word) >= 2:
            out.append((f"w:{word}", 1.0))
    compact = re.sub(r"\s+", " ", text)
    chars = [ch for ch in compact if ch.strip()]
    for n, weight in ((2, 0.7), (3, 0.45)):
        for i in range(len(chars) - n + 1):
            out.append((f"c{n}:{''.join(chars[i:i + n])}", weight))
    return out


def embed_text_list(text: str) -> List[float]:
    vec = [0.0] * DIM
    for token, weight in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vec))
    if norm <= 0:
        return vec
    return [value / norm for value in vec]


def embed_text_bytes(text: str) -> bytes:
    return struct.pack(f"{DIM}f", *embed_text_list(text))


def cosine(query: List[float], blob: bytes) -> float:
    if not query or not blob:
        return 0.0
    try:
        vec = struct.unpack(f"{DIM}f", blob)
    except (struct.error, TypeError):
        return 0.0
    q_norm = math.sqrt(sum(value * value for value in query))
    v_norm = math.sqrt(sum(value * value for value in vec))
    if q_norm <= 0 or v_norm <= 0:
        return 0.0
    return sum(a * b for a, b in zip(query, vec)) / (q_norm * v_norm)
