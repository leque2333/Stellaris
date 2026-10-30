# -*- coding: utf-8 -*-
"""工具目录：请求里的 tools → 名字 → 类别。

兼容两种 schema 形态：
- OpenAI 嵌套：{"type":"function","function":{"name":...}}
- 扁平：{"name":...}（少数客户端直接给 Gemini 风格声明）

未知工具（配置里没登记）默认放行：宁可多给，也不让新接的工具悄悄失效；
同时在 /api/tools/shelves 的 unknown 列表里暴露出来，方便补归类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn.get("name"))
    if tool.get("name"):
        return str(tool.get("name"))
    return ""


def tool_names(tools: Optional[List[Any]]) -> List[str]:
    names: List[str] = []
    for tool in tools or []:
        name = tool_name(tool)
        if name and name not in names:
            names.append(name)
    return names


def tool_description(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    if isinstance(fn, dict) and fn.get("description"):
        return str(fn.get("description"))
    return str(tool.get("description") or "")


@dataclass
class CatalogView:
    """一次请求视角下的工具归属快照。"""

    all_names: List[str] = field(default_factory=list)
    by_category: Dict[str, List[str]] = field(default_factory=dict)
    unknown: List[str] = field(default_factory=list)
    always_available: List[str] = field(default_factory=list)

    def resolve(self, category_by_tool: Dict[str, List[str]]) -> None:
        for name in self.all_names:
            cats = category_by_tool.get(name) or []
            if cats:
                for cat in cats:
                    self.by_category.setdefault(cat, []).append(name)
            elif name not in self.always_available:
                self.unknown.append(name)
        for cat in self.by_category:
            self.by_category[cat] = list(dict.fromkeys(self.by_category[cat]))


def build_view(
    tools: Optional[List[Any]],
    category_by_tool: Dict[str, List[str]],
    always_available: List[str],
) -> CatalogView:
    view = CatalogView(
        all_names=tool_names(tools),
        always_available=[str(x) for x in (always_available or [])],
    )
    view.resolve(category_by_tool)
    return view


def known_categories(view: CatalogView, all_categories: List[str]) -> List[str]:
    return [c for c in all_categories if view.by_category.get(c)]


__all__ = [
    "tool_name",
    "tool_names",
    "tool_description",
    "CatalogView",
    "build_view",
    "known_categories",
]
