# -*- coding: utf-8 -*-
"""P3 结果压缩模式：工具返回的生肉在送进主力 AI 之前先清洗。

两步走，顺序固定：

    第 1 步（纯代码，0 次模型调用）
        去 ANSI 颜色码 → 去 HTML 标签 → 压缩空白/空行 → 去重 → 头尾截断
        这一步能把 20KB 的搜索结果压到 6~8KB，延迟是 0。

    第 2 步（只有大体积工具才走）
        把粗筛后的文本交给执行 AI（DeepSeek），按严格 JSON 协议提取
        summary / points / sources / dropped。

三类工具三条路：

| 类型 | 工具 | 处理 |
|---|---|---|
| 大体积文本 | you-search / you-contents / bilibili_hub / control_host_pc_via_antigravity | 粗筛 + 模型摘要 |
| 简短数值/状态 | you-balance / check_image_quota / get_weather | **代码格式化或原样放行，绝不进模型** |
| 其余 | 其他工具 | 原样放行（仅超长时截断） |

防幻觉硬约束：
- URL / ID / 数字一律原样保留；模型输出的每一个数字、URL、BV 号都必须在原文里出现过，
  出现原文没有的数字就整段作废，回退到代码粗筛结果。
- 必然带 `truncated` 标记，主力 AI 知道"还有东西没看到"。
- 任何异常都原样放行，绝不让清洗失败把对话弄挂。
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ToolShelves.Cleaner")

SUMMARY_FORMAT = (
    '输出严格 JSON，不要解释：{"ok":true,"summary":"一句话结论（≤120字）",'
    '"points":[{"k":"字段名","v":"值"}],"sources":["URL"],"error":null,'
    '"truncated":false,"dropped":"丢弃了什么（≤40字）"}'
)

# 原文里提取 URL / BV 号 / 长数字用
_URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]，。；、]+", re.I)
_BV_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]{1,300}>")
_LINK_RE = re.compile(r"\[([^\]]{1,80})\]\((https?://[^)\s]+)\)")
_A_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_WS_RE = re.compile(r"[ \t\u3000]+")
_BLANK_RE = re.compile(r"\n{3,}")
_JSON_RE = re.compile(r"\{.*\}", re.S)

# 这些工具天生输出短，走安全快通道，绝不进模型
SAFE_SHORT_TOOLS = {
    "you-balance": {"max_chars": 400},
    "check_image_quota": {"max_chars": 800},
    "get_weather": {"max_chars": 400},
    "send_message_to_user": {"max_chars": 200},
    "future_task": {"max_chars": 600},
}


@dataclass
class CompressionResult:
    tool: str = ""
    text: str = ""
    truncated: bool = False
    used_model: bool = False
    dropped: str = ""
    error: str = ""
    latency_ms: int = 0
    raw_chars: int = 0
    clean_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    guard: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "truncated": self.truncated,
            "model": self.used_model,
            "raw_chars": self.raw_chars,
            "clean_chars": self.clean_chars,
            "saved": max(0, self.raw_chars - self.clean_chars),
            "latency_ms": self.latency_ms,
            "error": self.error,
            "guard": self.guard,
        }


# ----------------------------------------------------------------------
# 第 1 步：代码粗筛
# ----------------------------------------------------------------------
def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def strip_html(text: str) -> str:
    """去 script/style、保留链接文字与 href、去标签、反转义实体。"""
    t = _SCRIPT_RE.sub(" ", text or "")
    # 顺序要紧：先把 markdown 链接转成明文，再摘 <a href>，最后才去掉所有标签
    t = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", t)
    t = _A_RE.sub(lambda m: f"{m.group(2)} ({m.group(1)})", t)
    t = _TAG_RE.sub(" ", t)
    t = html.unescape(t)
    return t


def compact(text: str) -> str:
    """压缩空白、空行与重复短行。"""
    t = _WS_RE.sub(" ", text or "")
    t = "\n".join(line.strip() for line in t.splitlines())
    t = _BLANK_RE.sub("\n\n", t)
    # 连续重复行只留一条（搜索结果里常见的重复片段）
    out, seen = [], set()
    for line in t.splitlines():
        key = line.strip()
        if key and key in seen and len(key) <= 80:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return "\n".join(out).strip()


def head_tail(text: str, limit: int) -> Tuple[str, bool]:
    """头 70% / 尾 30% 截断，保留首尾关键信息（结论往往在尾部）。"""
    if limit <= 0 or len(text) <= limit:
        return text, False
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    return (
        text[:head] + "\n…（中间省略 {n} 字）…\n".format(n=max(0, len(text) - limit)) + text[-tail:],
        True,
    )


def pre_clean(text: str, limit: int, strip_tags: bool = True) -> Tuple[str, bool]:
    t = strip_ansi(str(text or ""))
    if strip_tags:
        t = strip_html(t)
    t = compact(t)
    return head_tail(t, limit)


def extract_guards(text: str) -> Dict[str, set]:
    """提取必须原样保留的 URL / BV 号 / 数字。

    数字的取样口径故意放宽到"原文任意位置出现的数字串"（含 URL 里的）：
    宁可少拦，也不要把原文里真实存在的数字误判成幻觉。
    """
    t = str(text or "")
    urls = {u.rstrip(".,;") for u in _URL_RE.findall(t)}
    bvs = set(_BV_RE.findall(t))
    nums = set(_NUM_RE.findall(t))
    return {"urls": urls, "bvs": bvs, "nums": nums}


def _url_key(url: str) -> str:
    """比较 URL 时忽略 query/fragment 与协议差异，避免同一个链接误判。"""
    u = str(url or "").strip().rstrip(".,;")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/").lower()


def guard_violation(output_text: str, guards: Dict[str, set], source_text: str = "") -> str:
    """检查模型输出有没有凭空造出原文没有的 URL / BV / 具体数值。"""
    raw_out = str(output_text or "")
    raw_src = str(source_text or "")

    fake_nums = set()
    for token in strict_numbers(raw_out):
        if token in raw_src:
            continue
        # 原文里以其他形式出现过（如 12.4万）也放过
        if token.replace(".", "") in raw_src.replace(".", ""):
            continue
        fake_nums.add(token)

    known_urls = {_url_key(u) for u in (guards.get("urls") or set())}
    known_urls |= {_url_key(u) for u in _URL_RE.findall(raw_src)}
    fake_urls = {u for u in _URL_RE.findall(raw_out) if _url_key(u) not in known_urls}

    fake_bvs = {b for b in _BV_RE.findall(raw_out) if b not in (guards.get("bvs") or set())}

    parts = []
    if fake_nums:
        parts.append(f"数值 {sorted(fake_nums)[:5]}")
    if fake_urls:
        parts.append(f"URL {sorted(fake_urls)[:3]}")
    if fake_bvs:
        parts.append(f"BV {sorted(fake_bvs)[:3]}")
    return "；".join(parts)


# ----------------------------------------------------------------------
# 第 2 步：模型摘要
# ----------------------------------------------------------------------
def build_cleaner_prompt(tool: str, args: Any, raw: str, user_query: str = "") -> Tuple[str, str]:
    system = "\n".join([
        "你是查询结果清洗器。把工具原始输出压缩成给主模型看的高密度事实摘要。",
        "硬规则：",
        "1. 只做压缩与摘录，绝不推理、绝不补充原文没有的信息。",
        "2. URL、ID、BV号、数字必须原样照抄，禁止四舍五入、换算或改写。",
        "3. 拿不准的宁可丢掉，也不要猜。",
        "4. 最多 8 条 points，每条 value ≤ 120 字；sources 最多 5 个。",
        "5. 原文被截断时 truncated=true，并在 dropped 里写清丢了什么。",
        "6. 工具报错时 ok=false，error 照抄错误信息（可截断到 200 字）。",
        SUMMARY_FORMAT,
    ])
    arg_text = ""
    try:
        arg_text = json.dumps(args, ensure_ascii=False)[:300] if args is not None else ""
    except Exception:
        arg_text = str(args)[:300]
    user = "\n".join([
        f"用户意图：{str(user_query or '')[:200] or '（未知）'}",
        f"工具：{tool}",
        f"调用参数：{arg_text or '（无）'}",
        "原始输出（已代码粗筛，可能被截断）：",
        "<<<",
        raw,
        ">>>",
    ])
    return system, user


_META_FIELDS = {"truncated", "ok", "dropped", "error", "confidence", "count", "total"}
# "具体数值"的特征：小数、百分比、货币、千分位。这类数字最容易造成幻觉，必须原样。
_STRICT_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_PLAIN_INT_RE = re.compile(r"^\d+$")


def strict_numbers(text: str) -> set:
    """只挑出"具体数值"：小数 / 百分号 / 货币 / 千分位。

    纯整数（60 条、第 3 集、worker 12）属于可推导的计数或序号，不作硬校验，
    否则模型写个"共 60 条"就会被误判成幻觉。
    """
    out = set()
    for token in _STRICT_NUM_RE.findall(str(text or "")):
        t = token.replace(",", "")
        if _PLAIN_INT_RE.match(t):
            if "," in token or len(t) >= 6:
                out.add(t)
            continue
        out.add(t)
    return out


def content_text(data: Dict[str, Any]) -> str:
    """只取"进上下文的内容字段"做防篡改校验。

    summary / points[].k / points[].v / sources 是主力 AI 真正会读到的东西；
    dropped / truncated 这类元数据由清洗器自己写，不该被当成幻觉拦截。
    """
    parts: List[str] = []
    summary = data.get("summary")
    if summary:
        parts.append(str(summary))
    points = data.get("points")
    if isinstance(points, list):
        for item in points:
            if not isinstance(item, dict):
                continue
            for key in ("k", "v"):
                val = item.get(key)
                if val:
                    parts.append(str(val))
    for key in ("sources", "urls", "links"):
        vals = data.get(key)
        if isinstance(vals, list):
            parts.extend(str(v) for v in vals if v)
    err = data.get("error")
    if err:
        parts.append(str(err))
    return "\n".join(parts)


def guard_violation(output_text: str, guards: Dict[str, set], source_text: str = "") -> str:
    """检查模型输出有没有凭空造出原文没有的 URL / BV / 具体数值。"""
    raw_out = str(output_text or "")
    raw_src = str(source_text or "")

    fake_nums = set()
    for token in strict_numbers(raw_out):
        if token in raw_src:
            continue
        # 原文里以其他形式出现过（如 12.4万）也放过
        if token.replace(".", "") in raw_src.replace(".", ""):
            continue
        fake_nums.add(token)

    known_urls = {_url_key(u) for u in (guards.get("urls") or set())}
    known_urls |= {_url_key(u) for u in _URL_RE.findall(raw_src)}
    fake_urls = {u for u in _URL_RE.findall(raw_out) if _url_key(u) not in known_urls}

    fake_bvs = {b for b in _BV_RE.findall(raw_out) if b not in (guards.get("bvs") or set())}

    parts = []
    if fake_nums:
        parts.append(f"数值 {sorted(fake_nums)[:5]}")
    if fake_urls:
        parts.append(f"URL {sorted(fake_urls)[:3]}")
    if fake_bvs:
        parts.append(f"BV {sorted(fake_bvs)[:3]}")
    return "；".join(parts)


def parse_cleaner_output(
    text: str, guards: Dict[str, set], source_text: str = ""
) -> Tuple[Optional[Dict[str, Any]], str]:
    match = _JSON_RE.search(str(text or ""))
    if not match:
        return None, "no_json"
    try:
        data = json.loads(match.group(0))
    except Exception as exc:
        return None, f"json_error:{type(exc).__name__}"
    if not isinstance(data, dict):
        return None, "not_object"
    violation = guard_violation(content_text(data), guards, source_text)
    if violation:
        return None, f"guard_violation:{violation}"
    return data, ""


def render_summary(data: Dict[str, Any], guards: Dict[str, set], truncated: bool) -> str:
    lines: List[str] = []
    ok = data.get("ok", True)
    if ok is False:
        lines.append(f"[工具报错] {str(data.get('error') or '未知错误')[:300]}")
    summary = str(data.get("summary") or "").strip()
    if summary:
        lines.append(f"结论：{summary[:300]}")

    points = data.get("points")
    if isinstance(points, list):
        for item in points[:8]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("k") or "").strip()[:40]
            val = str(item.get("v") or "").strip()[:200]
            if key and val:
                lines.append(f"- {key}：{val}")

    sources = data.get("sources")
    urls: List[str] = []
    if isinstance(sources, list):
        for item in sources:
            u = str(item or "").strip()
            if u and u not in urls:
                urls.append(u)
    # 模型漏掉的原文 URL 补回来（宁可多给来源，也不丢链接）
    for u in sorted(guards.get("urls") or []):
        if u not in urls:
            urls.append(u)
    if urls:
        lines.append("来源：")
        lines.extend(f"- {u}" for u in urls[:5])

    dropped = str(data.get("dropped") or "").strip()[:40]
    if dropped:
        lines.append(f"丢弃：{dropped}")
    if truncated or bool(data.get("truncated")):
        lines.append("[truncated] 原文过长已截断，需要更细的数据请带更窄的条件重新调用")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------
def _profile_for(tool: str, compression: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    profiles = compression.get("profiles") or {}
    prof = profiles.get(tool)
    if isinstance(prof, dict):
        return prof
    return None


def compress_tool_result(
    tool: str,
    raw: Any,
    compression: Dict[str, Any],
    args: Any = None,
    user_query: str = "",
    complete_once=None,
) -> CompressionResult:
    """压缩单个工具结果。任何异常都原样放行。"""
    started = time.time()
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    result = CompressionResult(tool=tool, raw_chars=len(text))

    try:
        # 1) 安全快通道：简短数值/状态类工具绝不进模型
        if tool in SAFE_SHORT_TOOLS:
            limit = int(SAFE_SHORT_TOOLS[tool].get("max_chars") or 400)
            out, cut = head_tail(text, limit)
            result.text = out
            result.truncated = cut
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        profile = _profile_for(tool, compression)
        if not profile:
            # 未配置的工具：只做超长截断，不改内容
            limit = int(compression.get("max_raw_chars") or 24000)
            out, cut = head_tail(text, limit)
            result.text = out
            result.truncated = cut
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        # 2) 代码粗筛
        mode = str(profile.get("mode") or "model")
        limit = int(profile.get("max_chars") or 1600)
        strip_tags = bool(profile.get("strip_html", True))
        model_limit = int(compression.get("max_raw_chars") or 24000)
        cleaned, cut = pre_clean(text, model_limit, strip_tags=strip_tags)
        result.truncated = cut

        if mode == "code":
            out, cut2 = head_tail(cleaned, limit)
            result.text = out
            result.truncated = result.truncated or cut2
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        # 3) 模型摘要（可选：短内容直接放过，省一次调用）
        min_model_chars = int(compression.get("min_model_chars") or 1200)
        if complete_once is None or len(cleaned) < min_model_chars:
            out, cut2 = head_tail(cleaned, limit)
            result.text = out
            result.truncated = result.truncated or cut2
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        guards = extract_guards(cleaned)
        system, user = build_cleaner_prompt(tool, args, cleaned, user_query)
        output, p_in, p_out, err = complete_once(system, user, compression)
        result.prompt_tokens = p_in
        result.completion_tokens = p_out
        if err or not output:
            result.error = err or "empty"
            out, cut2 = head_tail(cleaned, limit)
            result.text = out
            result.truncated = result.truncated or cut2
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        data, parse_err = parse_cleaner_output(output, guards, cleaned)
        if data is None:
            # 数字/URL 对不上或解析失败 → 整段作废，回退代码粗筛
            result.error = parse_err
            out, cut2 = head_tail(cleaned, limit)
            result.text = out
            result.truncated = result.truncated or cut2
            result.clean_chars = len(out)
            result.latency_ms = int((time.time() - started) * 1000)
            return result

        rendered = render_summary(data, guards, result.truncated or bool(data.get("truncated")))
        out, cut2 = head_tail(rendered, limit)
        result.text = out
        result.truncated = result.truncated or cut2
        result.used_model = True
        result.dropped = str(data.get("dropped") or "")[:40]
        result.clean_chars = len(out)
        result.latency_ms = int((time.time() - started) * 1000)
        return result
    except Exception as exc:
        logger.info("[ToolShelves] 清洗失败，原样放行 (%s): %s", tool, exc)
        out, cut = head_tail(text, int(compression.get("max_raw_chars") or 24000))
        result.text = out
        result.truncated = cut
        result.error = f"{type(exc).__name__}:{exc}"[:120]
        result.clean_chars = len(out)
        result.latency_ms = int((time.time() - started) * 1000)
        return result


def tool_call_index(messages: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """从历史里把 tool_call_id / 工具名 / 参数 对上号（tool 消息本身不带工具名）。"""
    index: Dict[str, Dict[str, Any]] = {}
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for call in (msg.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            fn = call.get("function") or {}
            if call_id:
                index[call_id] = {
                    "name": str(fn.get("name") or ""),
                    "arguments": fn.get("arguments"),
                }
    return index


def compress_messages(
    messages: List[Dict[str, Any]],
    compression: Dict[str, Any],
    user_query: str = "",
    complete_once=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把请求里所有 role=tool 的消息内容替换成清洗后的版本。

    返回 (新消息列表, 每条的压缩记录)。不改原列表，避免影响其它引用。
    """
    if not compression.get("enabled"):
        return messages, []
    if not isinstance(messages, list):
        return messages, []

    index = tool_call_index(messages)
    out: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            out.append(msg)
            continue
        call_id = str(msg.get("tool_call_id") or "")
        meta = index.get(call_id) or {}
        tool = str(msg.get("name") or meta.get("name") or "")
        if not tool:
            out.append(msg)
            continue
        if tool in (compression.get("skip_tools") or []):
            out.append(msg)
            continue

        content = msg.get("content")
        if content is None or content == "":
            out.append(msg)
            continue

        result = compress_tool_result(
            tool, content, compression,
            args=meta.get("arguments"),
            user_query=user_query,
            complete_once=complete_once,
        )
        new_msg = dict(msg)
        new_msg["content"] = result.text
        out.append(new_msg)
        records.append(result.as_dict())
    return out, records
