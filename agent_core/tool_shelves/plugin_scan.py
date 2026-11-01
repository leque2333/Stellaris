# -*- coding: utf-8 -*-
"""通用插件工具扫描：不绑定某个宿主，AstrBot / NoneBot / LangBot / Dify / 通用 Python 插件都能扫。

两类来源：
1. 插件目录（用户可在「设置 → 常规与系统 → 插件工具扫描」里配置，支持多个）
2. 自动识别（未配置或勾选自动时）：ASTRBOT_DATA_DIR/plugins、项目下常见的
   `astrbot/data/plugins`、`AstrBot/data/plugins`、`plugins`

扫描只读，不写插件目录、不注册工具：
- Python 装饰器：`@llm_tool` / `@tool` / `@function_tool` / `@register_tool` /
  `@mcp_tool` / `@ai_function`，以及 `@filter.llm_tool`
- YAML/JSON manifest 里的 tools 列表（Dify / LangBot 一类声明式插件）
- manifest 的 display_name / label / name 作为工具页分组名

结果只用于 `tool_shelves.admin.list_tools()` 展示（source=plugin），
管理员在工具页确认后才改 mode / approval。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CONFIG_ENV = "HUB_PLUGIN_SCAN_CONFIG"
ROOTS_ENV = "HUB_PLUGIN_ROOTS"

MANIFEST_FILES = (
    "manifest.yaml", "manifest.yml", "manifest.json",
    "plugin.yaml", "plugin.yml", "plugin.json",
    "metadata.yaml", "metadata.yml",
    "pyproject.toml",
)
_SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", ".tox", "dist", "build"}
_MAX_PLUGINS = 300
_MAX_PY_FILES = 40
_MAX_FILE_BYTES = 400_000

_CACHE: Dict[str, Any] = {"at": 0.0, "data": []}
_CACHE_SECONDS = 15.0
_LABEL_CACHE: Dict[str, Any] = {"at": 0.0, "data": {}}
_LABEL_SECONDS = 60.0

_TOOL_DECORATOR = re.compile(
    r"@\s*(?:(?:[A-Za-z_]\w*)\.)*(llm_tool|function_tool|register_tool|mcp_tool|ai_function|tool)\b"
    r"\s*(?:\(([^)]*)\))?",
    re.S,
)
_DEF_AFTER = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
def config_path() -> Path:
    env = os.getenv(CONFIG_ENV, "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "plugin_scan.json"


def load_config() -> Dict[str, Any]:
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        raw = {}
    roots = raw.get("roots") if isinstance(raw, dict) else None
    return {
        "auto": bool(raw.get("auto", True)) if isinstance(raw, dict) else True,
        "roots": [str(x) for x in roots] if isinstance(roots, list) else [],
    }


def save_config(roots: Optional[Iterable[str]] = None, auto: Optional[bool] = None) -> Dict[str, Any]:
    """保存插件目录配置；返回 {config, invalid}。不存在的目录不会写入配置。"""
    current = load_config()
    wanted = current["roots"] if roots is None else list(roots)
    cleaned, invalid = _clean_root_strings(wanted)
    config = {
        "auto": current["auto"] if auto is None else bool(auto),
        "roots": cleaned,
    }
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    clear_cache()
    return {"config": config, "invalid": invalid}


def _clean_root_strings(values: Iterable[str]) -> tuple[List[str], List[str]]:
    cleaned: List[str] = []
    invalid: List[str] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        expanded = os.path.expandvars(os.path.expanduser(text))
        path = Path(expanded)
        if not path.is_dir():
            invalid.append(text)
            continue
        resolved = str(path.resolve())
        if resolved not in cleaned:
            cleaned.append(resolved)
    return cleaned, invalid


# ----------------------------------------------------------------------
# 目录解析
# ----------------------------------------------------------------------
def _default_roots() -> List[Path]:
    roots: List[Path] = []
    for part in (os.getenv(ROOTS_ENV) or "").split(os.pathsep):
        if part.strip():
            roots.append(Path(os.path.expanduser(part.strip())))
    data_env = os.getenv("ASTRBOT_DATA_DIR", "").strip()
    if data_env:
        roots.append(Path(data_env).expanduser() / "plugins")
    cwd = Path.cwd()
    for rel in ("astrbot/data/plugins", "AstrBot/data/plugins", "plugins"):
        roots.append(cwd / rel)
    roots.append(Path.home() / ".astrbot" / "plugins")

    result: List[Path] = []
    seen = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            continue
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        result.append(root.resolve())
    return result


def resolve_roots() -> List[Path]:
    """显式配置 + 自动识别，去重后返回。"""
    config = load_config()
    explicit, _ = _clean_root_strings(config["roots"])
    roots: List[Path] = [Path(item) for item in explicit]
    if config["auto"]:
        for path in _default_roots():
            if path not in roots:
                roots.append(path)
    return roots[:20]


# ----------------------------------------------------------------------
# manifest / 工具名解析
# ----------------------------------------------------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_toml(path: Path) -> Dict[str, Any]:
    try:
        import tomllib

        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        # Python 3.10 没有 tomllib：退回正则抓 name / description
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
        data: Dict[str, str] = {}
        for key in ("name", "description"):
            match = re.search(rf"(?m)^\s*{key}\s*=\s*[\"']([^\"'\n]+)", text)
            if match:
                data[key] = match.group(1)
        return data


def _flatten_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    """pyproject 的 [project] / [tool.poetry] 拍平。"""
    if not isinstance(data, dict):
        return {}
    for key in ("project", "tool"):
        value = data.get(key)
        if isinstance(value, dict):
            if key == "tool" and isinstance(value.get("poetry"), dict):
                return value["poetry"]
            if key == "project":
                return value
    return data


def _yaml_tools(node: Any, depth: int = 0) -> List[str]:
    if depth > 6:
        return []
    found: List[str] = []
    if isinstance(node, dict):
        tools = node.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label") or item.get("id")
                    if name:
                        found.append(str(name))
        for value in node.values():
            found.extend(_yaml_tools(value, depth + 1))
    elif isinstance(node, list):
        for item in node[:200]:
            found.extend(_yaml_tools(item, depth + 1))
    return found


def _declarative_tools(plugin_dir: Path) -> List[str]:
    """Dify / LangBot 一类声明式插件：provider/*.yaml、tools/*.yaml。"""
    names: List[str] = []
    for sub in ("provider", "tools"):
        folder = plugin_dir / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.y*ml"))[:50]:
            raw = _read_yaml(path)
            if not raw:
                continue
            names.extend(_yaml_tools(raw))
            identity = raw.get("identity")
            if isinstance(identity, dict) and identity.get("name"):
                names.append(str(identity["name"]))
    return names


def _manifest(plugin_dir: Path) -> Dict[str, Any]:
    """读第一个可用 manifest，返回 name / description / tools。"""
    for name in MANIFEST_FILES:
        path = plugin_dir / name
        if not path.is_file():
            continue
        if name.endswith(".json"):
            raw = _read_json(path)
        elif name.endswith(".toml"):
            raw = _flatten_meta(_read_toml(path))
        else:
            raw = _read_yaml(path)
        if not raw:
            continue
        label = (
            raw.get("display_name") or raw.get("label") or raw.get("title")
            or raw.get("name") or plugin_dir.name
        )
        description = raw.get("description") or raw.get("desc") or raw.get("short_desc") or ""
        return {
            "name": str(label).strip() or plugin_dir.name,
            "display_name": str(label).strip() or plugin_dir.name,
            "raw_name": str(raw.get("name") or plugin_dir.name).strip(),
            "description": str(description).strip()[:200],
            "tools": _yaml_tools(raw),
            "manifest": name,
        }
    return {
        "name": plugin_dir.name,
        "display_name": plugin_dir.name,
        "raw_name": plugin_dir.name,
        "description": "",
        "tools": [],
        "manifest": "",
    }


def _tool_names(text: str) -> List[str]:
    """从 Python 源码里抽工具名；优先 name=，其次首个字符串，最后被装饰的函数名。"""
    names: List[str] = []
    for match in _TOOL_DECORATOR.finditer(text):
        args = match.group(2) or ""
        named = re.search(r"name\s*=\s*[\"']([^\"']+)[\"']", args)
        if named:
            names.append(named.group(1).strip())
            continue
        first = re.search(r"[\"']([^\"']+)[\"']", args)
        if first:
            names.append(first.group(1).strip())
            continue
        tail = text[match.end(): match.end() + 500]
        fn = _DEF_AFTER.search(tail)
        if fn:
            names.append(fn.group(1))
    return [name for name in dict.fromkeys(names) if name]


def _python_files(plugin_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root, dirs, names in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= _MAX_PY_FILES:
                return files
    return files


def _looks_like_plugin(path: Path) -> bool:
    if any((path / name).is_file() for name in MANIFEST_FILES):
        return True
    return (path / "main.py").is_file() or (path / "__init__.py").is_file()


def _plugin_dirs(root: Path) -> List[Path]:
    if _looks_like_plugin(root):
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir() and not child.name.startswith(".")]
    except OSError:
        return []
    direct = [child for child in children if _looks_like_plugin(child)]
    if direct:
        return direct[:_MAX_PLUGINS]
    # 厂商目录（如 Dify 的 plugins/<author>/<plugin>）：再向下一层找插件
    nested: List[Path] = []
    for child in children:
        try:
            grandchildren = sorted(child.iterdir())
        except OSError:
            continue
        for grand in grandchildren:
            if grand.is_dir() and not grand.name.startswith(".") and _looks_like_plugin(grand):
                nested.append(grand)
                if len(nested) >= _MAX_PLUGINS:
                    return nested
    return nested


def _scan_labels(roots: Iterable[Path]) -> Dict[str, str]:
    """内部包名 / 目录名 → display_name。"""
    labels: Dict[str, str] = {}
    for root in roots:
        for plugin_dir in _plugin_dirs(Path(root)):
            meta = _manifest(plugin_dir)
            display = str(meta.get("display_name") or meta.get("name") or plugin_dir.name).strip()
            for key in (meta.get("raw_name"), plugin_dir.name):
                text = str(key or "").strip()
                if text:
                    labels.setdefault(text, display)
    return labels


def plugin_labels(roots: Optional[Iterable[Path]] = None) -> Dict[str, str]:
    """插件显示名映射；60 秒缓存，扫描路径变化时会被 clear_cache 清掉。"""
    if roots is not None:
        return _scan_labels(roots)
    now = time.time()
    if now - float(_LABEL_CACHE["at"]) < _LABEL_SECONDS:
        return dict(_LABEL_CACHE["data"])
    data = _scan_labels(resolve_roots())
    _LABEL_CACHE["at"] = now
    _LABEL_CACHE["data"] = data
    return dict(data)


def label_for(name: str) -> str:
    """把 `astrbot_plugin_x` / `x` / `astrbot → astrbot_plugin_x` 解析成显示名。"""
    text = str(name or "").strip()
    if not text:
        return ""
    labels = plugin_labels()
    parts = [p.strip() for p in re.split(r"\s*(?:→|->|>|,|\|)\s*", text) if p.strip()]
    for candidate in reversed(parts + [text]):
        if candidate in labels:
            return labels[candidate]
    return text


def _framework(plugin_dir: Path, meta: Dict[str, Any], texts: List[str]) -> str:
    haystack = " ".join(texts).lower()
    manifest_name = str(meta.get("manifest") or "")
    if (
        "@filter." in haystack
        or "astrbot_plugin_" in str(meta.get("name") or "")
        or (manifest_name == "metadata.yaml" and "@filter." in haystack)
    ):
        return "astrbot"
    if "nonebot" in haystack:
        return "nonebot"
    if "langbot" in haystack or "qchatgpt" in haystack:
        return "langbot"
    if manifest_name == "manifest.yaml" and "dify" in haystack:
        return "dify"
    return "generic"


# ----------------------------------------------------------------------
# 扫描
# ----------------------------------------------------------------------
def _scan_one(plugin_dir: Path) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """扫一个插件：返回 (工具条目, 插件摘要)。"""
    meta = _manifest(plugin_dir)
    texts: List[str] = []
    names: List[str] = list(meta.get("tools") or [])
    names.extend(_declarative_tools(plugin_dir))
    for file in _python_files(plugin_dir):
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts.append(text)
        names.extend(_tool_names(text))
    framework = _framework(plugin_dir, meta, texts)
    plugin = {
        "name": meta.get("name") or plugin_dir.name,
        "framework": framework,
        "description": meta.get("description") or "",
        "path": str(plugin_dir),
        "tools": [],
    }
    entries: List[Dict[str, Any]] = []
    for name in dict.fromkeys(str(x).strip() for x in names):
        if not name:
            continue
        plugin["tools"].append(name)
        entries.append({
            "name": name,
            "plugin": plugin["name"],
            "framework": framework,
            "description": plugin["description"],
            "path": plugin["path"],
        })
    return entries, plugin


def _scan_all(roots: Iterable[Path]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """一次遍历：工具条目 + 插件摘要。带时间预算，超大目录不会卡住控制台。"""
    entries: List[Dict[str, Any]] = []
    plugins: List[Dict[str, Any]] = []
    seen: set = set()
    deadline = time.time() + 8.0
    for root in roots:
        for plugin_dir in _plugin_dirs(Path(root)):
            if time.time() > deadline or len(plugins) >= _MAX_PLUGINS:
                break
            current, plugin = _scan_one(plugin_dir)
            plugins.append(plugin)
            for entry in current:
                if entry["name"] in seen:
                    continue
                seen.add(entry["name"])
                entries.append(entry)
    return entries, plugins


def scan_plugins(roots: Optional[Iterable[Path]] = None) -> List[Dict[str, Any]]:
    """扫一遍插件目录，返回 [{name, plugin, framework, description, path}]。"""
    entries, _ = _scan_all(roots if roots is not None else resolve_roots())
    return entries


def tool_entries(roots: Optional[Iterable[Path]] = None) -> List[Dict[str, Any]]:
    """带 15 秒缓存的版本；工具页打开/刷新时用。"""
    if roots is not None:
        return scan_plugins(roots)
    now = time.time()
    if now - float(_CACHE["at"]) < _CACHE_SECONDS:
        return list(_CACHE["data"])
    data = scan_plugins()
    _CACHE["at"] = now
    _CACHE["data"] = data
    return list(data)


def status() -> Dict[str, Any]:
    """设置页/工具页共用：配置 + 解析到的目录 + 插件与工具预览。"""
    config = load_config()
    roots = resolve_roots()
    entries, plugins = _scan_all(roots)
    plugin_list = sorted(plugins, key=lambda item: (item["name"].lower(), item["path"]))
    return {
        "ok": True,
        "config_path": str(config_path()),
        "auto": bool(config["auto"]),
        "roots": list(config["roots"]),
        "resolved_roots": [str(item) for item in roots],
        "plugins": plugin_list[:200],
        "plugin_count": len(plugin_list),
        "tool_count": len(entries),
        "tools": entries[:500],
    }


def clear_cache() -> None:
    _CACHE["at"] = 0.0
    _CACHE["data"] = []
    _LABEL_CACHE["at"] = 0.0
    _LABEL_CACHE["data"] = {}


__all__ = [
    "scan_plugins",
    "tool_entries",
    "status",
    "load_config",
    "save_config",
    "resolve_roots",
    "config_path",
    "clear_cache",
    "plugin_labels",
    "label_for",
]
