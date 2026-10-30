# -*- coding: utf-8 -*-
"""AstrBot 插件：中枢工具执行器壳（P4）

背景：
    原始工具（搜索 / 宿主机 / B站 …）的 schema 又长又会诱导模型乱用。
    AstrBot 的 on_tool_end 钩子拿得到 tool_result 但改不了，所以"结果中继清洗"
    在插件侧走不通；这里换成"薄壳执行器"：

        主模型只看到 1 个薄工具 call_raw_tool
          → 插件按白名单执行被隐藏的原始工具
          → 生肉回灌中枢 /api/tools/clean 压缩
          → 只把摘要交回主模型

安全：
    1. 只有管理员能调用（非管理员直接拒绝，不给执行机会）
    2. 只能调用 hidden_tools 白名单里的工具，其余一律拒绝
    3. 原始工具不进主模型工具表，模型无法直接点名调用
    4. 插件本身完全不会执行任意命令，只是转发到 AstrBot 已注册的工具

配置见同目录 _conf_schema.json。
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import urllib.error
import urllib.request

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


DEFAULT_HIDDEN_TOOLS = (
    "you-search",
    "you-contents",
    "you-balance",
    "bilibili_hub",
    "control_host_pc_via_antigravity",
)


class HubToolExecutor(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.on_astrbot_loaded()
    async def _hide_raw_tools(self) -> None:
        """AstrBot 全部加载完成后，把原始工具从模型工具表里摘掉。

        必须挂在 on_astrbot_loaded 而不是 initialize：initialize 早于 MCP 连接，
        那时 you-search 等工具还没注册。
        停用后的工具仍在 func_list 里，get_func() 有"未激活回退"，
        所以本插件仍能按白名单调用它们 —— 模型看不见、执行器调得到。
        """
        try:
            manager = self.context.get_llm_tool_manager()
        except Exception as exc:
            logger.warning(f"[hub_tool_executor] 拿不到工具管理器，跳过隐藏: {exc}")
            return
        wanted = self._hidden_tools()
        pending = set(wanted)
        logger.info(f"[hub_tool_executor] 待隐藏工具: {sorted(wanted)}")
        for _ in range(30):  # 最多等 30 秒，让 MCP 工具注册完
            if not pending:
                break
            for name in sorted(pending):
                try:
                    tool = manager.get_func(name)
                    if tool is None:
                        continue
                    if getattr(tool, "active", True):
                        await manager.deactivate_llm_tool_async(name)
                        logger.info(f"[hub_tool_executor] 已从模型工具表隐藏: {name}")
                    pending.discard(name)
                except Exception as exc:
                    logger.warning(f"[hub_tool_executor] 隐藏 {name} 失败: {exc}")
                    pending.discard(name)
            if pending:
                await asyncio.sleep(1)
        if pending:
            logger.info(f"[hub_tool_executor] 以下工具未出现，跳过隐藏: {sorted(pending)}")

    # ---------------- 配置读取 ----------------
    def _cfg(self, key: str, default):
        try:
            value = self.config.get(key, default)
        except Exception:
            value = getattr(self.config, key, default)
        return default if value is None else value

    def _hidden_tools(self) -> set:
        """白名单：优先用插件配置，读不到就用内置默认值。

        AstrBot 的插件配置持久化依赖 metadata 的 config 字段，不同版本行为不一致；
        内置默认值保证"部署即生效"，配置只作为覆盖。
        """
        raw = self._cfg("hidden_tools", None)
        if raw is None:
            return set(DEFAULT_HIDDEN_TOOLS)
        if isinstance(raw, str):
            raw = re.split(r"[,，\s]+", raw)
        names = {str(x).strip() for x in (raw or []) if str(x).strip()}
        return names or set(DEFAULT_HIDDEN_TOOLS)

    def _clean_url(self) -> str:
        return str(self._cfg("hub_clean_url", "http://192.168.3.4:18895/api/tools/clean")).strip()

    def _hub_key(self) -> str:
        return str(self._cfg("hub_key", "")).strip()

    def _timeout(self) -> float:
        try:
            return float(self._cfg("clean_timeout", 25))
        except (TypeError, ValueError):
            return 25.0

    def _max_raw(self) -> int:
        try:
            return int(self._cfg("max_raw_chars", 200000))
        except (TypeError, ValueError):
            return 200000

    # ---------------- 中枢清洗 ----------------
    def _hub_clean(self, tool: str, args: dict, raw: str, sender: str) -> tuple:
        """返回 (文本, 是否截断)。任何失败都退回截断原文，绝不让工具调用失败。"""
        text = raw or ""
        cut = False
        if len(text) > self._max_raw():
            text = text[: self._max_raw()]
            cut = True
        payload = json.dumps({
            "tool": tool,
            "args": args,
            "raw": text,
            "user_query": sender,
        }, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = self._hub_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            req = urllib.request.Request(self._clean_url(), data=payload, headers=headers, method="POST")
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=self._timeout(), context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore")) or {}
            if data.get("ok") and isinstance(data.get("text"), str):
                return data["text"], bool(data.get("truncated")) or cut
            raise RuntimeError(f"hub 返回异常: {data.get('error')}")
        except Exception as exc:
            logger.warning(f"[hub_tool_executor] 中枢清洗失败，回退原文: {exc}")
            suffix = "\n[truncated] 中枢清洗不可用，已截断原文" if cut else ""
            return text + suffix, cut

    # ---------------- 执行原始工具 ----------------
    async def _call_raw(self, event, name: str, args: dict) -> str:
        hidden = self._hidden_tools()
        if name not in hidden:
            allowed = " / ".join(sorted(hidden)) or "（未配置）"
            return f"错误：工具 {name} 不在可调用白名单内。白名单：{allowed}"
        try:
            manager = self.context.get_llm_tool_manager()
        except Exception as exc:
            return f"错误：拿不到工具管理器（{exc}）"
        tool = manager.get_func(name)
        if tool is None:
            return f"错误：AstrBot 里没有名为 {name} 的工具（可能被禁用或未加载）"

        # AstrBot 的 FunctionTool.call 需要 run_context（内含 event）；这里包一层，
        # 让工具内若用到 event 也能拿到当前消息事件。
        class _Ctx:
            def __init__(self, evt):
                self.event = evt

        class _Wrapper:
            def __init__(self, evt):
                self.context = _Ctx(evt)

        try:
            result = await tool.call(_Wrapper(event), **(args or {}))
        except Exception as exc:
            logger.warning(f"[hub_tool_executor] 工具 {name} 执行异常: {exc}")
            return f"工具执行失败：{type(exc).__name__}: {exc}"

        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)

    # ---------------- 对主模型暴露的薄工具 ----------------
    @filter.llm_tool(name="call_raw_tool")
    async def call_raw_tool(self, event: AstrMessageEvent, tool: str, arguments: str = "{}") -> str:
        """调用一个被隐藏的原始工具，并把清洗后的结果返回。

        Args:
            tool(string): 要调用的工具名，必须是白名单里的工具（例如 control_host_pc_via_antigravity、bilibili_hub、you-search）。
            arguments(string): 工具参数的 JSON 字符串，例如 {"city":"北京"}；没有参数就留空 {}。
        """
        if not event.is_admin():
            return "错误：该工具仅限管理员使用。"
        name = str(tool or "").strip()
        if not name:
            return "错误：tool 不能为空。"
        try:
            args = json.loads(arguments) if str(arguments or "").strip() else {}
        except Exception:
            return f"错误：arguments 不是合法 JSON：{arguments!r}"
        if not isinstance(args, dict):
            return "错误：arguments 必须是 JSON 对象。"

        hidden = self._hidden_tools()
        if not hidden:
            return "错误：插件未配置 hidden_tools 白名单，拒绝执行。"
        if name not in hidden:
            return f"错误：工具 {name} 不在可调用白名单内。白名单：{' / '.join(sorted(hidden))}"

        raw = await self._call_raw(event, name, args)
        cleaned, truncated = self._hub_clean(name, args, raw, f"tool:{name}")
        if truncated and "[truncated]" not in cleaned:
            cleaned += "\n[truncated] 原始输出过长已压缩，需要更细的数据请带更窄的参数重新调用"
        return cleaned
