# -*- coding: utf-8 -*-
"""可观测性：每轮决策的计数、延迟与节省量。

写入 data/tool_shelves/metrics.json（原子替换），按天聚合；可选 JSONL 明细。
- 默认不写逐轮明细（log_decisions=false），避免每条闲聊都落盘；
- 通过 /api/tools/shelves 读取，用来判断误裁率、命中率。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("ToolShelves.Metrics")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DIR = BASE_DIR / "data" / "tool_shelves"
MAX_SAMPLES = 300


def metrics_dir() -> Path:
    env = os.getenv("TOOL_SHELVES_METRICS_DIR", "").strip()
    path = Path(env) if env else DEFAULT_DIR
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._file = metrics_dir() / "metrics.json"
        self._data: Dict[str, Any] = self._load()
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=MAX_SAMPLES)

    # ---------------- 持久化 ----------------
    def _load(self) -> Dict[str, Any]:
        empty = {"days": {}, "totals": {}}
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "days" in data:
                    return data
        except Exception:
            pass
        return empty

    def _flush_locked(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._file)
        except Exception as exc:
            logger.debug("[ToolShelves] metrics 落盘失败: %s", exc)

    # ---------------- 写 ----------------
    def record(
        self,
        path: str,
        tools_in: int,
        tools_out: int,
        categories: Optional[list] = None,
        confidence: float = 0.0,
        latency_ms: int = 0,
        classifier_ok: bool = False,
        error: str = "",
        detail: bool = False,
    ) -> None:
        today = _today()
        with self._lock:
            day = self._data.setdefault("days", {}).setdefault(
                today, {"calls": 0, "by_path": {}, "tools_in": 0, "tools_out": 0, "errors": 0}
            )
            day["calls"] = int(day.get("calls") or 0) + 1
            day["by_path"][path] = int(day["by_path"].get(path) or 0) + 1
            day["tools_in"] = int(day.get("tools_in") or 0) + max(0, tools_in)
            day["tools_out"] = int(day.get("tools_out") or 0) + max(0, tools_out)
            if error:
                day["errors"] = int(day.get("errors") or 0) + 1

            totals = self._data.setdefault("totals", {})
            totals["calls"] = int(totals.get("calls") or 0) + 1
            totals["tools_in"] = int(totals.get("tools_in") or 0) + max(0, tools_in)
            totals["tools_out"] = int(totals.get("tools_out") or 0) + max(0, tools_out)
            if classifier_ok:
                totals["classifier_ok"] = int(totals.get("classifier_ok") or 0) + 1
            if error:
                totals["errors"] = int(totals.get("errors") or 0) + 1

            if latency_ms > 0:
                lat = day.setdefault("latency_ms", [])
                lat.append(int(latency_ms))
                if len(lat) > 500:
                    del lat[:-500]

            if detail:
                self._recent.append({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "path": path,
                    "cats": list(categories or []),
                    "conf": round(float(confidence or 0.0), 2),
                    "in": tools_in,
                    "out": tools_out,
                    "lat": int(latency_ms or 0),
                    "err": str(error or "")[:60],
                })

            # 只保留最近 14 天，防止文件无限增长
            days = self._data.get("days") or {}
            if len(days) > 14:
                for old in sorted(days.keys())[:-14]:
                    days.pop(old, None)

            self._flush_locked()

    # ---------------- 读 ----------------
    def record_compression(self, records) -> None:
        """P3 结果压缩：累计原始/清洗后体积与模型调用次数。"""
        if not records:
            return
        today = _today()
        with self._lock:
            day = self._data.setdefault("days", {}).setdefault(
                today, {"calls": 0, "by_path": {}, "tools_in": 0, "tools_out": 0, "errors": 0}
            )
            comp = day.setdefault("compression", {})
            comp["results"] = int(comp.get("results") or 0) + len(records)
            comp["raw_chars"] = int(comp.get("raw_chars") or 0) + sum(
                int(r.get("raw_chars") or 0) for r in records
            )
            comp["clean_chars"] = int(comp.get("clean_chars") or 0) + sum(
                int(r.get("clean_chars") or 0) for r in records
            )
            comp["model_calls"] = int(comp.get("model_calls") or 0) + sum(
                1 for r in records if r.get("model")
            )
            comp["truncated"] = int(comp.get("truncated") or 0) + sum(
                1 for r in records if r.get("truncated")
            )
            comp["errors"] = int(comp.get("errors") or 0) + sum(
                1 for r in records if r.get("error")
            )
            totals = self._data.setdefault("totals", {})
            totals["compression_raw_chars"] = int(totals.get("compression_raw_chars") or 0) + sum(
                int(r.get("raw_chars") or 0) for r in records
            )
            totals["compression_clean_chars"] = int(totals.get("compression_clean_chars") or 0) + sum(
                int(r.get("clean_chars") or 0) for r in records
            )
            if len(records) <= 50 and self._recent is not None:
                for r in records:
                    self._recent.append({
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "path": "compress",
                        "tool": r.get("tool"),
                        "in": r.get("raw_chars"),
                        "out": r.get("clean_chars"),
                        "model": bool(r.get("model")),
                        "truncated": bool(r.get("truncated")),
                        "err": str(r.get("error") or "")[:60],
                    })
            self._flush_locked()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            days = dict(self._data.get("days") or {})
            today = days.get(_today()) or {}
            totals = dict(self._data.get("totals") or {})
            by_path = dict(today.get("by_path") or {})
            lat = list(today.get("latency_ms") or [])
            lat_sorted = sorted(lat)
            p95 = lat_sorted[int(len(lat_sorted) * 0.95) - 1] if lat_sorted else 0
            tin = int(today.get("tools_in") or 0)
            tout = int(today.get("tools_out") or 0)
            comp = dict(today.get("compression") or {})
            c_raw = int(comp.get("raw_chars") or 0)
            c_clean = int(comp.get("clean_chars") or 0)
            return {
                "date": _today(),
                "today": {
                    "calls": int(today.get("calls") or 0),
                    "by_path": by_path,
                    "tools_in": tin,
                    "tools_out": tout,
                    "tools_saved": max(0, tin - tout),
                    "save_ratio": round((tin - tout) / tin, 3) if tin else 0.0,
                    "errors": int(today.get("errors") or 0),
                    "latency_p95_ms": p95,
                },
                "compression": {
                    "results": int(comp.get("results") or 0),
                    "raw_chars": c_raw,
                    "clean_chars": c_clean,
                    "saved": max(0, c_raw - c_clean),
                    "save_ratio": round((c_raw - c_clean) / c_raw, 3) if c_raw else 0.0,
                    "model_calls": int(comp.get("model_calls") or 0),
                    "truncated": int(comp.get("truncated") or 0),
                    "errors": int(comp.get("errors") or 0),
                },
                "totals": totals,
                "recent": list(self._recent),
                "days": sorted(days.keys())[-14:],
            }

    def reset(self, keep_totals: bool = False) -> None:
        with self._lock:
            if keep_totals:
                self._data["days"] = {}
            else:
                self._data = {"days": {}, "totals": {}}
            self._recent.clear()
            self._flush_locked()


metrics = Metrics()

__all__ = ["Metrics", "metrics", "metrics_dir"]
