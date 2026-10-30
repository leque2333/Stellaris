# -*- coding: utf-8 -*-
"""中枢薄壳 MCP 服务（P4）——运行在 AstrBot 容器内，由 AstrBot 以 stdio MCP 拉起。

它只做三件事：
    1. 用 npx 拉起 You.com 官方 MCP（生肉入口留在这里，不进主模型工具表）
    2. 只对外暴露两个"薄工具"：you_search_clean / you_contents_clean
       —— 描述极短、参数与原生完全一致，调用能力不缩水
    3. 拿到生肉后立刻回灌中枢 /api/tools/clean，把 P3 清洗结果交给主模型

为什么不能直接让 AstrBot 自己执行原始工具：
    AstrBot 的 on_tool_end 钩子拿得到 tool_result 但改不了（返回 None），
    所以"插件侧中继清洗"走不通，只能由中枢自己拥有工具入口。

依赖：仅标准库 + mcp（AstrBot 容器自带）。不 import 中枢代码，靠 HTTP 通信。

环境变量：
    YDC_API_KEY        必填，You.com Key
    HUB_CLEAN_URL      清洗接口，默认 http://192.168.3.4:18895/api/tools/clean
    HUB_KEY            中枢客户端 Key（clients.json 里登记的值，可选）
    HUB_TIMEOUT        清洗超时秒数，默认 25
    UPSTREAM_NPX       npx 命令，默认 npx
    HTTP_PROXY / HTTPS_PROXY  透传给 npx 子进程
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# mcp 版本兼容：1.x 是 mcp.server.fastmcp.FastMCP，2.x 改名为 mcp.server.mcpserver.MCPServer。
# 容器里的 AstrBot 是 1.30（FastMCP），开发机可能是 2.x，两边都能跑。
try:  # pragma: no cover - 取决于运行环境
    from mcp.server.fastmcp import FastMCP as _Server
except Exception:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer as _Server

mcp = _Server("hub-tools")

HUB_CLEAN_URL = os.environ.get("HUB_CLEAN_URL", "http://192.168.3.4:18895/api/tools/clean")
HUB_KEY = os.environ.get("HUB_KEY", "").strip()
HUB_TIMEOUT = float(os.environ.get("HUB_TIMEOUT", "25"))
UPSTREAM_NPX = os.environ.get("UPSTREAM_NPX", "npx")
YDC_API_KEY = os.environ.get("YDC_API_KEY", "").strip()
MAX_RAW = int(os.environ.get("MAX_RAW_CHARS", "8000"))
TOOL_TIMEOUT = float(os.environ.get("TOOL_TIMEOUT", "90"))

# 薄工具名 → 上游工具名
THIN_TOOLS: Dict[str, str] = {
    "you_search_clean": "you-search",
    "you_contents_clean": "you-contents",
}

# ----------------------------------------------------------------------
# 上游 MCP 调用
# ----------------------------------------------------------------------
def _upstream_params() -> StdioServerParameters:
    env = {**os.environ, "YDC_API_KEY": YDC_API_KEY}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    # Windows 下 npx 是 .cmd；容器里是普通可执行文件
    command = UPSTREAM_NPX
    if os.name == "nt" and command.lower().endswith("npx"):
        command = "npx.cmd"
    return StdioServerParameters(
        command=command,
        args=["-y", "@youdotcom-oss/mcp"],
        env=env,
    )


async def _call_upstream(tool: str, args: Dict[str, Any]) -> str:
    """拉起 You.com MCP，调一次，返回拼接后的纯文本。"""
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            async with stdio_client(_upstream_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool(tool, args)
                    parts: List[str] = []
                    for item in res.content or []:
                        text = getattr(item, "text", None)
                        if text:
                            parts.append(str(text))
                    return "\n".join(parts)
        except Exception as exc:  # npx 冷启动偶尔失败，重试一次
            last_err = exc
            if attempt == 0:
                await asyncio.sleep(0.8)
    raise RuntimeError(f"上游 {tool} 调用失败: {last_err}")


# ----------------------------------------------------------------------
# 中枢清洗
# ----------------------------------------------------------------------
def _hub_clean(tool: str, raw: str, args: Dict[str, Any], user_query: str = "") -> Dict[str, Any]:
    """把生肉回灌中枢清洗；任何失败都退回截断原文，绝不让工具调用失败。"""
    if not raw:
        return {"text": "", "truncated": False, "stats": {"tool": tool, "empty": True}}

    payload = json.dumps({
        "tool": tool,
        "args": args,
        "raw": raw[:200_000],
        "user_query": user_query,
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if HUB_KEY:
        headers["Authorization"] = f"Bearer {HUB_KEY}"
    try:
        req = urllib.request.Request(HUB_CLEAN_URL, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=HUB_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore")) or {}
        if data.get("ok") and isinstance(data.get("text"), str):
            return {
                "text": data["text"],
                "truncated": bool(data.get("truncated")),
                "stats": data.get("stats") or {},
            }
        raise RuntimeError(f"hub 返回异常: {data.get('error')}")
    except Exception as exc:
        print(f"[hub-tools] 中枢清洗失败，回退截断原文: {exc}", file=sys.stderr)
        cut = len(raw) > MAX_RAW
        return {
            "text": (raw[:MAX_RAW] + "\n[truncated] 中枢清洗不可用，已截断原文") if cut else raw,
            "truncated": cut,
            "stats": {"tool": tool, "fallback": True, "error": str(exc)[:120]},
        }


def _format_result(clean: Dict[str, Any]) -> str:
    text = clean.get("text") or ""
    stats = clean.get("stats") or {}
    if clean.get("truncated") and "[truncated]" not in text:
        text += "\n[truncated] 原始输出过长已压缩，需要更细的数据请带更窄的条件重新调用"
    meta = []
    if stats.get("raw_chars"):
        meta.append(f"raw={stats['raw_chars']}chars")
    if stats.get("clean_chars"):
        meta.append(f"clean={stats['clean_chars']}chars")
    if stats.get("model"):
        meta.append("model=cleaned")
    if stats.get("fallback"):
        meta.append("fallback=raw")
    return text + (f"\n_[{' '.join(meta)}]_" if meta else "")


async def _run(tool_name: str, args: Dict[str, Any]) -> str:
    """薄壳主流程：上游取生肉 → 中枢清洗 → 返回压缩结果。

    必须是 async：FastMCP 在事件循环里调用工具，sync 函数里再 asyncio.run 会炸。
    """
    upstream = THIN_TOOLS.get(tool_name)
    if not upstream:
        return f"错误：未知薄工具 {tool_name}"
    if not YDC_API_KEY:
        return "错误：薄壳服务缺少 YDC_API_KEY，无法访问 You.com"
    query = str(args.get("query") or ", ".join(args.get("urls") or []) or "")
    try:
        raw = await asyncio.wait_for(_call_upstream(upstream, args), timeout=TOOL_TIMEOUT)
    except Exception as exc:
        return f"工具调用失败：{exc}"
    # 中枢清洗是同步 urllib 调用，丢线程池避免阻塞事件循环
    clean = await asyncio.to_thread(_hub_clean, upstream, raw, args, query)
    return _format_result(clean)


# ----------------------------------------------------------------------
# 薄工具（显式签名，便于 FastMCP 生成准确 schema）
# ----------------------------------------------------------------------
@mcp.tool(
    name="you_search_clean",
    description=(
        "联网搜索并返回已压缩的高密度摘要（含标题、链接、日期、播放量等关键字段）。"
        "参数与 You.com 原生搜索一致；需要网页全文请接着调 you_contents_clean。"
        "返回带 [truncated] 表示原文过长已截断，需要更细的数据请带更窄的条件重搜。"
    ),
)
async def you_search_clean(
    query: str,
    count: int = 30,
    freshness: str | None = None,
    offset: int | None = None,
    extraction: str = "highlights",
    knowledge: str = "core",
    safesearch: str | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """联网搜索（结果已压缩）。query 支持 site:/lang:/loc: 内联过滤。"""
    args: Dict[str, Any] = {"query": query, "count": count}
    for key, value in (
        ("freshness", freshness), ("offset", offset), ("extraction", extraction),
        ("knowledge", knowledge), ("safesearch", safesearch),
        ("exclude_domains", exclude_domains),
    ):
        if value is not None:
            args[key] = value
    return await _run("you_search_clean", args)


@mcp.tool(
    name="you_contents_clean",
    description=(
        "读取指定 URL 的正文并返回已压缩摘要（保留标题、关键段落、来源链接）。"
        "务必先有 URL 再调用；建议一次 1~5 个 URL。"
    ),
)
async def you_contents_clean(
    urls: list[str],
    formats: list[str] | None = None,
    crawl_timeout: float | None = None,
) -> str:
    """读取网页正文（结果已压缩）。"""
    args: Dict[str, Any] = {"urls": urls}
    if formats:
        args["formats"] = formats
    if crawl_timeout is not None:
        args["crawl_timeout"] = crawl_timeout
    return await _run("you_contents_clean", args)


if __name__ == "__main__":
    mcp.run()
