# -*- coding: utf-8 -*-
"""JSON 小工具：写库前转字符串，读库后容错还原。"""

from __future__ import annotations

import json
from typing import Any


def _json(value: Any, default: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return default


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default
