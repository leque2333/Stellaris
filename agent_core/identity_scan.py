# -*- coding: utf-8 -*-
"""身份线索扫描器：把所有插件里像 UID 的值全部罗列出来。

设计原则（按需求）：
- **只罗列，不判断**。不猜谁是群、谁是人、谁是主人，不自动建档、不自动绑定。
- 平台名只做"提示"，从插件名/路径推断，认不出就标 unknown。
- 每条线索都保留来源（文件/数据库/字段/原始值），方便人工核对。
- 幂等、只读：扫描本身不写任何数据库。

给谁用：
    控制台"身份扫描"页 / CLI `scripts/scan_identities.py`
    人看完列表后自己决定哪个 UID 对应哪个档案。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 默认扫描根：项目自身模块 + AstrBot 数据目录（都找不到就只扫当前目录）
SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", "temp", "attachments",
    "backups", "site-packages", ".venv", "venv", "logs",
}
TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".env", ".ini"}
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MAX_FILE_BYTES = 5_000_000

# 键名里带这些才算"可能装 ID"
KEY_HINT = re.compile(
    r"(?i)(uid|user_?id|userid|qq|uin|account|owner|master|admin|"
    r"whitelist|allow_?list|friend|group|member|contact|sender|umo|peer)"
)
# 明确排除的配置键（避免 token/超时这类数字误报）。
# 注意必须用下划线/首尾边界匹配：否则 "admin"/"admins_id" 里的 "min" 会被误伤。
NOISE_KEY = re.compile(
    r"(?i)(?:^|_)(token|timeout|port|size|length|len|limit|count|max|min|"
    r"interval|version|days|hours|pages|score|temp|price|quota)(?:_|$)"
)
# 纯数字 UID：5~12 位
NUMERIC = re.compile(r"^\d{5,12}$")
# OneBot / 带前缀的 ID：onebot_napcat:FriendMessage:<你的QQ>
PREFIXED = re.compile(r"^[A-Za-z0-9_\-]+:(?:FriendMessage|GroupMessage):(\d{5,12})$")
# 数据库里可能是 ID 的列
ID_COLUMN = re.compile(r"(?i)(uid|user_?id|qq|uin|account|sender|member|friend|target|owner)")

# 平台提示：从路径/插件名里认
PLATFORM_HINTS = (
    (re.compile(r"(?i)bilibili|bili"), "bilibili"),
    (re.compile(r"(?i)onebot|napcat|qq"), "qq"),
    (re.compile(r"(?i)telegram|tg_"), "telegram"),
    (re.compile(r"(?i)discord"), "discord"),
    (re.compile(r"(?i)lark|feishu"), "lark"),
)


@dataclass
class IdentitySource:
    """一条线索的来源。"""

    kind: str                     # file / sqlite
    path: str                     # 文件或数据库的绝对路径（相对项目根更好读）
    field: str = ""               # JSON 路径 / 数据库 表.列
    raw: str = ""                 # 原始值（可能带前缀）
    plugin: str = ""              # 所属插件目录名，方便人工定位
    host: str = ""                # 所属宿主（如 onebot_napcat），根目录文件的归属

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "field": self.field,
            "raw": self.raw,
            "plugin": self.plugin,
            "host": self.host,
        }


@dataclass
class IdentityHit:
    """一个 UID 及其全部来源（已按 UID 聚合）。"""

    uid: str
    platform_hint: str = "unknown"
    sources: List[IdentitySource] = field(default_factory=list)
    # 已配置状态（扫描时顺带查，只读）
    configured: bool = False
    archive_id: str = ""
    configured_via: str = ""      # identity / binding
    hidden: bool = False          # 被人工隐藏（重新扫描可恢复）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "platform_hint": self.platform_hint,
            "sources": [s.to_dict() for s in self.sources],
            "source_count": len(self.sources),
            "configured": self.configured,
            "archive_id": self.archive_id,
            "configured_via": self.configured_via,
            "hidden": self.hidden,
        }


def _norm_uid(raw: Any) -> str:
    s = str(raw).strip()
    m = PREFIXED.match(s)
    return m.group(1) if m else s


def _is_uid(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 10_000 <= value <= 999_999_999_999
    s = str(value).strip()
    return bool(NUMERIC.match(s) or PREFIXED.match(s))


def _plugin_of(path: Path, roots: List[Path]) -> str:
    """取相对某个根目录的第一段，当作插件名。"""
    for root in roots:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        return parts[0] if len(parts) > 1 else ""
    return ""


def _platform_hint(text: str) -> str:
    for pattern, name in PLATFORM_HINTS:
        if pattern.search(text):
            return name
    return "unknown"


HOST_LABELS = {
    "onebot_napcat": "NapCat",
    "onebot": "OneBot",
    "aiocqhttp": "NapCat / OneBot",
    "napcat": "NapCat",
    "bilibili": "B站",
    "telegram": "Telegram",
    "discord": "Discord",
    "lark": "飞书",
    "feishu": "飞书",
    "wechat": "微信",
}


def host_label(host_id: str) -> str:
    """把宿主 ID 转成可读名字；认不出就原样返回。"""
    key = str(host_id or "").strip()
    if not key:
        return ""
    return HOST_LABELS.get(key.lower(), key)


def probe_host(root: Path) -> str:
    """读宿主配置，认出这批文件属于哪个平台（如 onebot_napcat）。

    只认配置里明写的字段，不猜：platform[].id / platform[].type。
    """
    for name in ("cmd_config.json", "config.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        platforms = data.get("platform")
        if isinstance(platforms, list):
            for item in platforms:
                if not isinstance(item, dict):
                    continue
                if item.get("enable") is False:
                    continue
                ident = str(item.get("id") or "").strip()
                if ident:
                    return ident
        # 没有 platform 时退回 hub.host_id（部分插件这么写）
        hub = data.get("hub")
        if isinstance(hub, dict) and hub.get("host_id"):
            return str(hub["host_id"]).strip()
    return ""


def _walk_json(obj: Any, prefix: str = "") -> Iterable[tuple]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_json(value, path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk_json(value, f"{prefix}[{index}]")
    else:
        yield prefix, obj


def _scan_text_file(path: Path, roots: List[Path], host: str = "") -> List[IdentitySource]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    plugin = _plugin_of(path, roots)
    out: List[IdentitySource] = []

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except Exception:
            return []
        for field_path, value in _walk_json(data):
            leaf = field_path.split(".")[-1].split("[")[0]
            if not KEY_HINT.search(leaf) or NOISE_KEY.search(leaf):
                continue
            if _is_uid(value):
                out.append(IdentitySource(
                    kind="file", path=str(path), field=field_path,
                    raw=str(value), plugin=plugin, host=host,
                ))
        return out

    # 非 JSON：按 key: value / key = value 扫
    for match in re.finditer(
        r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*[\"']?([^\s\"'#,}]+)", text
    ):
        key, value = match.group(1), match.group(2)
        if not KEY_HINT.search(key) or NOISE_KEY.search(key):
            continue
        if _is_uid(value):
            out.append(IdentitySource(
                kind="file", path=str(path), field=key, raw=value,
                plugin=plugin, host=host,
            ))
    return out


def _scan_sqlite(path: Path, roots: List[Path], host: str = "") -> List[IdentitySource]:
    plugin = _plugin_of(path, roots)
    out: List[IdentitySource] = []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
    except Exception:
        return []
    try:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            try:
                columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            except Exception:
                continue
            for column in columns:
                if not ID_COLUMN.search(column) or NOISE_KEY.search(column):
                    continue
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT {column} FROM {table} "
                        f"WHERE {column} IS NOT NULL LIMIT 200"
                    ).fetchall()
                except Exception:
                    continue
                for row in rows:
                    value = row[0]
                    if _is_uid(value):
                        out.append(IdentitySource(
                            kind="sqlite", path=str(path),
                            field=f"{table}.{column}", raw=str(value),
                            plugin=plugin, host=host,
                        ))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def default_roots() -> List[Path]:
    """默认扫描根：环境变量优先，其次项目内 modules/ 与 AstrBot data。"""
    roots: List[Path] = []
    env = os.getenv("IDENTITY_SCAN_ROOTS", "").strip()
    if env:
        for part in env.split(os.pathsep):
            if part.strip():
                roots.append(Path(part).expanduser())
    project = Path(__file__).resolve().parent.parent
    candidates = [
        project / "modules",
        project / "astrbot" / "data",
        project / "astrbot_plugins",
        project.parent / "机器人_完整版" / "astrbot" / "data",
        Path("/AstrBot/data"),
    ]
    for path in candidates:
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots or [project]


def _existing_bindings(store: Any) -> Dict[tuple, Dict[str, str]]:
    """读现有身份/绑定，用于标记"已配置"。只读。"""
    result: Dict[tuple, Dict[str, str]] = {}
    if store is None:
        return result
    try:
        for row in store.list_archives():
            for ident in store.list_identities(row["archive_id"]):
                key = (str(ident.get("platform") or ""), str(ident.get("platform_user_id") or ""))
                result[key] = {"archive_id": row["archive_id"], "via": "identity"}
    except Exception:
        pass
    try:
        for binding in store.list_bindings():
            external_id = str(binding.get("external_id") or "")
            uid = _norm_uid(external_id)
            key = (str(binding.get("platform") or ""), uid)
            if key not in result:
                result[key] = {
                    "archive_id": str(binding.get("archive_id") or ""),
                    "via": "binding",
                }
    except Exception:
        pass
    return result


HIDDEN_FILE = Path(__file__).resolve().parent.parent / "data" / "identity_hidden.json"


def load_hidden() -> set:
    """读人工隐藏的 UID 集合。文件不存在就返回空集。"""
    try:
        data = json.loads(HIDDEN_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    items = data.get("hidden") if isinstance(data, dict) else None
    if isinstance(items, list):
        return {str(x).strip() for x in items if str(x).strip()}
    return set()


def save_hidden(uids: Iterable[str]) -> int:
    """写回隐藏集合，返回写入条数。"""
    cleaned = sorted({str(x).strip() for x in uids if str(x).strip()})
    HIDDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HIDDEN_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "hidden": cleaned}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, HIDDEN_FILE)
    return len(cleaned)


def set_hidden(uids: Iterable[str], hidden: bool = True) -> set:
    """批量隐藏 / 取消隐藏，返回新的隐藏集合。"""
    current = load_hidden()
    target = {str(x).strip() for x in uids if str(x).strip()}
    if hidden:
        current |= target
    else:
        current -= target
    save_hidden(current)
    return current


def reset_hidden() -> None:
    """清空全部隐藏（重新扫描时恢复所有 UID）。"""
    save_hidden(set())


def scan(
    roots: Optional[List[Path]] = None,
    store: Any = None,
    max_depth_files: int = 5000,
    plugin: str = "",
    reset_hidden_for_plugin: bool = False,
) -> Dict[str, Any]:
    """扫描并罗列所有 UID。返回 {ok, roots, hits, ...}，不写库。

    plugin: 只返回这个插件/宿主分组（按插件重扫）
    reset_hidden_for_plugin: 重扫该插件时，把它下面被隐藏的 UID 恢复显示
    """
    scan_roots = [Path(r) for r in (roots or default_roots())]
    scan_roots = [r for r in scan_roots if r.is_dir()]

    host_map: Dict[Path, str] = {root: probe_host(root) for root in scan_roots}
    sources: List[IdentitySource] = []
    scanned_files = 0
    for root in scan_roots:
        for path in root.rglob("*"):
            if scanned_files >= max_depth_files:
                break
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix not in TEXT_SUFFIXES and suffix not in DB_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            scanned_files += 1
            if suffix in DB_SUFFIXES:
                sources.extend(_scan_sqlite(path, scan_roots, host_map.get(root, "")))
            else:
                sources.extend(_scan_text_file(path, scan_roots, host_map.get(root, "")))

    existing = _existing_bindings(store)

    merged: Dict[str, IdentityHit] = {}
    for src in sources:
        uid = _norm_uid(src.raw)
        if not uid:
            continue
        hit = merged.get(uid)
        if hit is None:
            hit = IdentityHit(uid=uid)
            merged[uid] = hit
        # 平台提示：取第一个能认出来的来源
        if hit.platform_hint == "unknown":
            hint = _platform_hint(f"{src.plugin} {src.path} {src.field}")
            if hint != "unknown":
                hit.platform_hint = hint
        hit.sources.append(src)

    # 标记已配置（按平台 + uid 精确匹配；平台未知时只按 uid 匹配）
    for hit in merged.values():
        candidates = [
            (hit.platform_hint, hit.uid),
            ("qq", hit.uid),
        ]
        for key in candidates:
            found = existing.get(key)
            if found:
                hit.configured = True
                hit.archive_id = found["archive_id"]
                hit.configured_via = found["via"]
                break

    hits = sorted(merged.values(), key=lambda h: (-len(h.sources), h.uid))

    # 分组键：插件目录名优先，其次宿主，最后兜底
    def group_key(hit: IdentityHit) -> str:
        name = next((s.plugin for s in hit.sources if s.plugin), "")
        host = next((s.host for s in hit.sources if s.host), "")
        return name or host or "未知来源"

    # 按插件重扫：只保留目标分组；并恢复该组的隐藏
    if plugin:
        hits = [h for h in hits if group_key(h) == plugin]
        if reset_hidden_for_plugin:
            set_hidden([h.uid for h in hits], hidden=False)

    # 标记隐藏（重新扫描后仍会带上 hidden=true，前端默认过滤）
    hidden_now = load_hidden()
    for hit in hits:
        hit.hidden = hit.uid in hidden_now

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        groups.setdefault(group_key(hit), []).append(hit.to_dict())

    grouped = [
        {
            "plugin": name,
            "label": host_label(name) or name,
            "uid_count": len(items),
            "configured": sum(1 for it in items if it["configured"]),
            "hidden": sum(1 for it in items if it["hidden"]),
            "uids": items,
        }
        for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    return {
        "ok": True,
        "roots": [str(r) for r in scan_roots],
        "scanned_files": scanned_files,
        "total_sources": len(sources),
        "total_uids": len(hits),
        "configured": sum(1 for h in hits if h.configured),
        "unconfigured": sum(1 for h in hits if not h.configured),
        "hidden": sum(1 for h in hits if h.hidden),
        "visible": sum(1 for h in hits if not h.hidden),
        "groups": grouped,
        "hits": [h.to_dict() for h in hits],
    }


__all__ = ["scan", "default_roots", "IdentityHit", "IdentitySource",
           "load_hidden", "save_hidden", "set_hidden", "reset_hidden"]
