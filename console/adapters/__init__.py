# -*- coding: utf-8 -*-
"""插件适配器：内置 AstrBot + generated/ 生成适配器。"""

from .astrbot import AstrBotAdapter
from .base import Adapter, RequestView
from .registry import AdapterRegistry, get_registry, register_adapter

__all__ = [
    "Adapter",
    "RequestView",
    "AstrBotAdapter",
    "AdapterRegistry",
    "get_registry",
    "register_adapter",
]
