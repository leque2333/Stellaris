# -*- coding: utf-8 -*-
"""适配器注册表：内置 + generated/ 下的插件适配器。

生成适配器默认只加载、不执行任何工具；加载失败只影响自己。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import List, Optional

from .base import Adapter, RequestView

_GENERATED_DIR = Path(__file__).resolve().parent / "generated"
_DECLARATIVE_DIR = Path(__file__).resolve().parent / "declarative"


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: List[Adapter] = []
        self._loaded_generated = False

    def register(self, adapter: Adapter) -> None:
        if adapter not in self._adapters:
            self._adapters.append(adapter)

    def adapters(self) -> List[Adapter]:
        self._load_declarative()
        self._load_generated()
        return sorted(self._adapters, key=lambda item: (-int(getattr(item, "priority", 0)), item.plugin_id))

    def resolve(self, request: RequestView) -> Optional[dict]:
        best, score = None, 0.0
        for adapter in self.adapters():
            try:
                current = float(adapter.detect(request) or 0.0)
            except Exception:
                continue
            if current > score:
                best, score = adapter, current
        if best is None or score < 0.25:
            return None
        try:
            payload = dict(best.context(request) or {})
        except Exception:
            payload = {}
        payload.setdefault("plugin_id", best.plugin_id)
        payload["adapter_score"] = round(score, 3)
        return payload

    def group_name(self, group_id: str) -> str:
        for adapter in self.adapters():
            try:
                name = adapter.group_name(group_id)
            except Exception:
                name = ""
            if name:
                return name
        return ""

    def _load_declarative(self) -> None:
        if getattr(self, "_declarative_loaded", False):
            return
        self._declarative_loaded = True
        if not _DECLARATIVE_DIR.is_dir():
            return
        from .declarative import DeclarativeAdapter

        for path in sorted(_DECLARATIVE_DIR.glob("*.json")):
            try:
                adapter = DeclarativeAdapter.from_file(path)
                if adapter.config.get("enabled") is not True:
                    continue
                self.register(adapter)
            except Exception:
                continue

    def _load_generated(self) -> None:
        if self._loaded_generated:
            return
        self._loaded_generated = True
        if not _GENERATED_DIR.is_dir():
            return
        for path in sorted(_GENERATED_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"hub_adapter_{path.stem}", path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                if getattr(module, "ENABLED", False) is not True:
                    continue  # 生成物默认关闭，人工 review 后打开
                adapter = getattr(module, "adapter", None)
                if adapter is None:
                    cls = getattr(module, "Adapter", None)
                    adapter = cls() if cls else None
                if isinstance(adapter, Adapter):
                    self.register(adapter)
            except Exception:
                continue


_registry = AdapterRegistry()
_builtins_loaded = False


def get_registry() -> AdapterRegistry:
    global _builtins_loaded
    if not _builtins_loaded:
        from .astrbot import AstrBotAdapter

        _registry.register(AstrBotAdapter())
        _builtins_loaded = True
    return _registry


def register_adapter(adapter: Adapter) -> None:
    _registry.register(adapter)


__all__ = ["AdapterRegistry", "get_registry", "register_adapter"]
