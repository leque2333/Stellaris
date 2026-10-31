# -*- coding: utf-8 -*-
"""工具货架配置：data/tool_shelves.json，支持按 mtime 热加载。

设计原则：
- 分类是数据，不是代码。加工具/改分类只动 JSON。
- 配置文件缺失或损坏 → 闸门整体关闭（等价于旧行为：全量透传）。
  这样新装环境、测试环境、误删配置都不会把机器人弄哑。
- 可用环境变量覆盖：
  TOOL_SHELVES_CONFIG_PATH  指定配置路径
  TOOL_SHELVES_DISABLED=1   强制关闭闸门
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "data" / "tool_shelves.json"

_DEFAULT_ALWAYS = ["send_message_to_user", "future_task"]
_DEFAULT_TRIVIAL = (
    r"^(嗯+|嗯嗯+|啊+|哦+|噢+|喔+|哈+|哈哈+|嘿嘿|好|好滴|好的|好呀|好嘞|行|可以|收到|"
    r"谢谢|多谢|晚安|早安|在吗|在不在|没事|没什么|随便|对|对的|是|是的|不|不是|"
    r"ok|okay|emmm+|…+|\.+|\?+|？+)$"
)
_DEFAULT_FORCE = (
    r"(播放|放首歌|放个|听歌|音量|开灯|关灯|灯|空调|窗帘|帮我|给我|查|搜|找|生成|画|"
    r"发|提醒|定时|启动|重启|关掉|打开|关闭|下载|上传|天气|几点|温度|服务器|电脑|"
    r"qq|b站|bilibili)"
)


def default_settings() -> Dict[str, Any]:
    """内置兜底默认值（正常运行时会被 data/tool_shelves.json 覆盖）。"""
    return {
        "version": 1,
        "gateway": {
            "enabled": False,
            "mode": "enforce_soft",
            "exempt_clients": [],
            "max_tools": 12,
            "sticky_tool_turns": 2,
            "on_failure": "previous_then_all",
            "log_decisions": False,
            "classifier": {
                "slot": "logic_slot",
                "timeout_ms": 2500,
                "temperature": 0.0,
                "max_tokens": 120,
                "confidence_floor": 0.45,
                "situational_inference": False,
            },
        },
        "fast_path": {
            "min_chars": 2,
            "max_chars": 10,
            "trivial_regex": _DEFAULT_TRIVIAL,
            "force_tools_regex": _DEFAULT_FORCE,
        },
        "always_available": list(_DEFAULT_ALWAYS),
        "approval_tools": [],
        "categories": {},
        "unknown_tool_policy": "always",
        "protection": {
            "admin_clients": [],
            "host_tools": ["control_host_pc_via_antigravity"],
            "deny_for_non_admin": True,
        },
        "compression": {
            "enabled": False,
            "slot": "logic_slot",
            "timeout_ms": 6000,
            "max_tokens": 700,
            "temperature": 0.0,
            "max_raw_chars": 24000,
            "profiles": {},
        },
        "prompt_hint": {
            "enabled": False,
            "with_tools": "",
            "no_tools": "",
        },
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ShelfConfig:
    """线程安全的配置容器，按 mtime 热加载。"""

    def __init__(self, path: Optional[Path] = None):
        env_path = os.getenv("TOOL_SHELVES_CONFIG_PATH", "").strip()
        self.path = Path(path or env_path or DEFAULT_CONFIG_PATH)
        self._lock = threading.Lock()
        self._mtime: Optional[float] = None
        self._data: Dict[str, Any] = default_settings()
        self._loaded_ok = False
        self._error: Optional[str] = None
        self.reload()

    # ---------------- 内部 ----------------
    def _read(self) -> Optional[Dict[str, Any]]:
        """读配置。utf-8-sig 兼容 PowerShell / 记事本写出的 BOM。"""
        if not self.path.exists():
            self._error = f"配置文件不存在: {self.path}"
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            self._error = f"配置文件解析失败: {self.path} ({type(exc).__name__}: {exc})"
            return None
        if not isinstance(raw, dict):
            self._error = f"配置根节点必须是对象: {self.path}"
            return None
        self._error = None
        return _deep_merge(default_settings(), raw)

    @property
    def disabled_by_env(self) -> bool:
        return os.getenv("TOOL_SHELVES_DISABLED", "").strip() in ("1", "true", "yes", "on")

    # ---------------- 对外 ----------------
    def reload(self, force: bool = False) -> Dict[str, Any]:
        """按 mtime 判断是否需要重读；force=True 强制重读。"""
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
        except OSError:
            mtime = None
        with self._lock:
            if not force and mtime == self._mtime and self._loaded_ok:
                return self._data
            self._mtime = mtime
            data = self._read()
            if data is None:
                # 没有配置或解析失败：闸门关闭，全量透传
                self._data = default_settings()
                self._loaded_ok = False
            else:
                self._data = data
                self._loaded_ok = True
            return self._data

    def get(self) -> Dict[str, Any]:
        return self.reload()

    def load_error(self) -> Optional[str]:
        if not self._loaded_ok:
            return self._error or f"配置文件不可用: {self.path}"
        return None

    def gateway(self) -> Dict[str, Any]:
        return self.get().get("gateway") or {}

    def categories(self) -> Dict[str, Dict[str, Any]]:
        cats = self.get().get("categories") or {}
        return {str(k): v for k, v in cats.items() if isinstance(v, dict)}

    def tools_of(self, category: str) -> List[str]:
        cat = self.categories().get(category) or {}
        return [str(t) for t in (cat.get("tools") or []) if t]

    def always_available(self) -> List[str]:
        return [str(t) for t in (self.get().get("always_available") or []) if t]

    def approval_tools(self) -> List[str]:
        """需要管理员提权才能自动调用的工具。"""
        return [str(t) for t in (self.get().get("approval_tools") or []) if t]

    def archive_policies(self) -> Dict[str, Dict[str, Any]]:
        """按档案（或访客）区分的工具权限。

        结构：{"arc_master": {"mode": "all"},
               "arc_person_x": {"mode": "allow", "tools": [...]},
               "_guest": {"mode": "allow", "tools": [...]}}
        mode: all=不限制 / allow=只给列表 / deny=除了列表都给
        """
        raw = self.get().get("archive_policies") or {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for key, spec in raw.items():
            if isinstance(spec, dict):
                out[str(key)] = spec
        return out

    def archive_policy(self, key: str) -> Dict[str, Any]:
        """取某档案的策略；key 为空时落到陌生访客 _guest。"""
        policies = self.archive_policies()
        name = str(key or "").strip()
        if name and name in policies:
            return policies[name]
        if not name and "_guest" in policies:
            return policies["_guest"]
        return {}

    def category_by_tool(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for cat, spec in self.categories().items():
            for tool in (spec.get("tools") or []):
                mapping.setdefault(str(tool), []).append(cat)
        return mapping

    def hint(self) -> Dict[str, Any]:
        hint = self.get().get("prompt_hint") or {}
        return hint if isinstance(hint, dict) else {}

    def protection(self) -> Dict[str, Any]:
        prot = self.get().get("protection") or {}
        return prot if isinstance(prot, dict) else {}

    def admin_clients(self) -> List[str]:
        return [str(x) for x in (self.protection().get("admin_clients") or []) if x]

    def host_tools(self) -> List[str]:
        return [str(x) for x in (self.protection().get("host_tools") or []) if x]

    def deny_for_non_admin(self) -> bool:
        return bool(self.protection().get("deny_for_non_admin", True))

    def compression(self) -> Dict[str, Any]:
        comp = self.get().get("compression") or {}
        return comp if isinstance(comp, dict) else {}

    def enabled(self) -> bool:
        if self.disabled_by_env:
            return False
        if self.load_error():
            return False
        return bool(self.gateway().get("enabled", False))


config = ShelfConfig()
