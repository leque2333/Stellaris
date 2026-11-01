# -*- coding: utf-8 -*-
"""闸门：决定这一轮把哪些工具交给主力 AI。

调用顺序（全部在 try/except 内，任何异常 → 全量放行）：
    1. 没有 tools 或闸门关闭            → pass
    2. 客户端在豁免名单                → pass
    3. 快判 fast_trivial               → 沿用上轮类别，否则只留常驻
    4. 快判 tool_result                → 沿用上轮类别（粘性 N 轮），否则只留常驻
    5. 其余 → 问执行 AI 选类别          → 只留选中类别 + 常驻
    6. 判定 need_tools=false           → 只留常驻 + 未归类工具（按 unknown_tool_policy）
    7. 客户端 scope=request            → 不按类目裁，只做权限过滤与 max_tools 上限

失败兜底：on_failure=previous_then_all 时，分类失败先试上轮类别，再不行全量放行。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import catalog, fastpath
from .config import ShelfConfig
from .config import config as default_config
from .session import SessionStore
from .session import session_store as default_session_store

logger = logging.getLogger("ToolShelves.Gate")

PASS_PATHS = {
    fastpath.PATH_SKIP,
    "disabled",
    "exempt",
    "no_tools",
    "error",
    "empty_catalog",
}


@dataclass
class Decision:
    path: str = "skip"
    applied: bool = False
    categories: List[str] = field(default_factory=list)
    tools_in: List[str] = field(default_factory=list)
    tools_out: List[str] = field(default_factory=list)
    tools_before: int = 0
    tools_after: int = 0
    payload_tools: Optional[List[Any]] = None
    drop_tool_fields: bool = False
    confidence: float = 0.0
    reason: str = ""
    error: str = ""
    latency_ms: int = 0
    classifier_prompt_tokens: int = 0
    classifier_completion_tokens: int = 0
    unknown_tools: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    archive_policy: bool = False
    session_key: str = ""
    misfire: bool = False
    classifier_model: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "applied": self.applied,
            "categories": list(self.categories),
            "tools_in": len(self.tools_in),
            "tools_out": len(self.tools_out),
            "drop_tool_fields": self.drop_tool_fields,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "unknown": list(self.unknown_tools),
            "denied": list(self.denied),
            "model": self.classifier_model,
        }


class ToolGate:
    def __init__(
        self,
        cfg: Optional[ShelfConfig] = None,
        classifier=None,
        sessions: Optional[SessionStore] = None,
        metrics=None,
    ):
        self.cfg = cfg or default_config
        self._classifier = classifier
        self.sessions = sessions or default_session_store
        self._metrics = metrics
        self.last_decision: Optional[Dict[str, Any]] = None

    # ---------------- 依赖（懒加载，便于测试注入） ----------------
    def _get_classifier(self):
        if self._classifier is not None:
            return self._classifier
        from .classifier import classify
        return classify

    def _get_metrics(self):
        if self._metrics is not None:
            return self._metrics
        from .metrics import metrics
        return metrics

    # ---------------- 对外 ----------------
    def decide(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]],
        channel_id: str = "",
        actor_id: str = "",
        client_id: str = "",
        platform: str = "",
        channel_type: str = "",
        client_perms: Optional[Dict[str, Any]] = None,
        archive_id: str = "",
        plugin_id: str = "",
    ) -> Decision:
        started = time.time()
        session_key = ""
        try:
            session_key = self.sessions.key(channel_id, actor_id)
        except Exception:
            pass
        decision = Decision(session_key=session_key)
        if not isinstance(client_perms, dict):
            client_perms = {}

        # 每轮检查一次配置文件 mtime：改配置不用重启，配置后补上也能立即生效
        try:
            self.cfg.reload()
        except Exception:
            pass

        try:
            return self._decide(
                decision, messages, tools, channel_id, actor_id,
                client_id, platform, channel_type, client_perms, archive_id,
                plugin_id,
            )
        except Exception as exc:  # 绝不允许闸门把对话弄挂
            logger.warning("[ToolShelves] 闸门异常，全量放行: %s", exc)
            names = catalog.tool_names(tools)
            decision = Decision(
                path="error",
                error=f"{type(exc).__name__}:{exc}"[:120],
                session_key=session_key,
                tools_in=names,
                tools_before=len(tools or []),
                tools_after=len(tools or []),
                tools_out=list(names),
            )
            return self._finalize(decision, started)

    def apply(self, decision: Decision, extra: Dict[str, Any]) -> Dict[str, Any]:
        """把裁剪结果写回网关透传参数。"""
        if not decision.applied:
            return extra
        extra = dict(extra or {})
        if decision.payload_tools:
            extra["tools"] = decision.payload_tools
        elif decision.drop_tool_fields:
            # 没有工具时三个字段整段删除；绝不保留 tool_choice:"none"，上游会 400
            for key in ("tools", "tool_choice", "parallel_tool_calls"):
                extra.pop(key, None)
        return extra

    def remember(self, session_key: str, messages: List[Dict[str, Any]]) -> None:
        """把本轮对话记进短路上下文（只记 user/assistant 文本，工具结果单独计数）。"""
        if not session_key:
            return
        try:
            role = fastpath.last_role(messages)
            if role == "tool":
                self.sessions.note_tool_result(session_key)
                return
            text = fastpath.last_user_text(messages)
            if text:
                self.sessions.remember(session_key, "user", text)
        except Exception:
            pass

    def remember_reply(self, session_key: str, content: str, tool_calls=None) -> None:
        if not session_key:
            return
        try:
            if tool_calls:
                return
            if content:
                self.sessions.remember(session_key, "assistant", content)
        except Exception:
            pass

    def note_response(self, decision: Decision, tool_calls) -> None:
        """主力 AI 在无工具轮里仍尝试调工具 → 记为误裁，供调参。"""
        try:
            if not tool_calls:
                return
            if decision.tools_after > 0:
                return
            decision.misfire = True
            logger.info(
                "[ToolShelves] 误裁：本轮无工具但主 AI 仍尝试调用 %s",
                [((tc.get("function") or {}).get("name")) for tc in tool_calls],
            )
            self._get_metrics().record(
                path="misfire",
                tools_in=decision.tools_before,
                tools_out=0,
                categories=decision.categories,
                latency_ms=0,
                error="misfire",
                detail=True,
            )
        except Exception:
            pass

    # ---------------- 权限 ----------------
    def client_denied(self, tools, client_id: str = "", client_perms=None) -> set:
        """对外：某个客户端的硬拒绝工具名集合（供网关先摘掉再走分类）。"""
        return self._denied_tools(Decision(), tools, client_id, client_perms)

    def allowed_tools(
        self,
        tools,
        *,
        client_id: str = "",
        client_perms=None,
        archive_id: str = "",
        actor_id: str = "",
    ) -> list:
        """按客户端 + 档案权限过滤后的工具集合，供动态申请使用。"""
        decision = Decision()
        denied = self._resolve_denied(
            decision, tools, client_id, client_perms, archive_id, actor_id
        )
        return [tool for tool in tools or [] if catalog.tool_name(tool) not in denied]

    @staticmethod
    def _tools_in(tools, names) -> set:
        available = set(catalog.tool_names(tools))
        return available & set(names or [])

    def _archive_policy_denied(self, archive_id: str, actor_id: str, tools) -> set:
        """档案策略 → 要剔除的工具集合。

        mode=all   不限制
        mode=allow 只保留 tools 列表里的，其余剔除
        mode=deny  剔除 tools 列表里的
        archive_id 为空（陌生访客）时用 _guest 策略。
        """
        policy = self.cfg.archive_policy(archive_id)
        if not policy and actor_id:
            policy = self.cfg.archive_policy(actor_id)
        if not policy:
            return set()
        mode = str(policy.get("mode") or "all").strip().lower()
        if mode not in ("allow", "deny"):
            return set()
        listed = {str(x) for x in (policy.get("tools") or []) if x}
        names = catalog.tool_names(tools)
        if mode == "allow":
            return {n for n in names if n not in listed}
        return {n for n in names if n in listed}

    def _denied_tools(self, decision: Decision, tools, client_id, client_perms) -> set:
        """硬拒绝集合：三层叠加，任何一层命中都算拒绝。

        1) protection.admin_clients 之外的客户端 → 强制拒绝 protection.host_tools
           （纯代码兜底：客户端的 deny 列表写漏了也挡得住）
        2) protection.host_tools → 对所有客户端一律拒绝（最高优先级）
        3) clients.json 的 tool_permissions.deny → 逐客户端拒绝
        """
        denied: set = set()
        try:
            perms = client_perms if isinstance(client_perms, dict) else {}
            role = str(perms.get("role") or "").strip().lower()
            admin_clients = set(self.cfg.admin_clients())
            in_admin_list = bool(client_id) and client_id in admin_clients
            if not role:
                role = "admin" if in_admin_list else "guest"

            # 双条件：配置里写了 role=admin 还不够，客户端 ID 也必须在 admin_clients 名单里
            is_admin = (role == "admin") and in_admin_list

            protected = set(self.cfg.host_tools())
            if self.cfg.deny_for_non_admin() and not is_admin:
                denied |= self._tools_in(tools, protected)

            # 需要管理员提权的工具：非管理员一律不给
            if not is_admin:
                denied |= self._tools_in(tools, self.cfg.approval_tools())

            global_deny = {str(x) for x in (self.cfg.protection().get("global_deny") or [])}
            denied |= self._tools_in(tools, global_deny)
            denied |= self._tools_in(tools, perms.get("deny") or [])
        except Exception as exc:
            # 权限解析异常时宁可全拒宿主机工具，也不能放开
            logger.warning("[ToolShelves] 权限解析异常，兜底拒绝宿主机工具: %s", exc)
            try:
                denied |= self._tools_in(tools, self.cfg.host_tools())
            except Exception:
                pass
        return denied

    # ---------------- 内部 ----------------
    def _decide(
        self, decision: Decision, messages, tools, channel_id, actor_id,
        client_id, platform, channel_type, client_perms=None, archive_id="",
        plugin_id="",
    ) -> Decision:
        self._prepare_decision(decision, tools, channel_id, actor_id, plugin_id)
        client_perms = client_perms if isinstance(client_perms, dict) else {}
        denied = self._resolve_denied(decision, tools, client_id, client_perms, archive_id, actor_id)
        if not tools:
            return self._skip(decision, "no_tools", 0)
        if not self.cfg.enabled():
            return self._gate_disabled(decision, tools, denied)
        if self._is_exempt(client_id):
            return self._gate_exempt(decision, tools, denied)
        if self._client_scope(client_perms) == "request":
            return self._gate_request_scope(decision, tools, denied)
        return self._trim_by_category(decision, messages, tools, denied, platform, channel_type)

    @staticmethod
    def _client_scope(client_perms) -> str:
        """客户端工具范围：catalog=按类目裁（默认），request=本轮工具全量保留。"""
        perms = client_perms if isinstance(client_perms, dict) else {}
        value = str(perms.get("scope") or perms.get("tool_scope") or "catalog").strip().lower()
        return "request" if value == "request" else "catalog"

    def _gate_request_scope(self, decision, tools, denied):
        """scope=request：不跑分类器、不按类目裁；仍执行 deny/approval 与 max_tools。"""
        decision.path = "request_scope"
        decision.reason = "客户端工具范围=request，保留本轮工具，仅执行权限过滤"
        decision.tools_out = catalog.tool_names(tools)
        decision.tools_after = decision.tools_before
        self._apply_deny(decision, tools, denied)
        payload = list(decision.payload_tools if decision.applied else tools)
        max_tools = int(self.cfg.gateway().get("max_tools") or 0)
        if max_tools > 0 and len(payload) > max_tools:
            payload = payload[:max_tools]
            decision.payload_tools = payload
            decision.tools_out = catalog.tool_names(payload)
            decision.tools_after = len(payload)
            decision.applied = True
            decision.drop_tool_fields = not payload
            decision.reason += f"；超过 max_tools={max_tools}，按原顺序截断"
        return self._finalize(decision, None)

    def _prepare_decision(self, decision, tools, channel_id, actor_id, plugin_id="") -> None:
        """记录本轮入口状态：会话键、工具名、注册表。"""
        decision.session_key = self.sessions.key(channel_id, actor_id)
        decision.tools_in = catalog.tool_names(tools)
        decision.tools_before = len(tools or [])
        try:
            from . import registry as _registry

            _registry.record(tools or [], plugin=plugin_id)
        except Exception:
            pass

    def _resolve_denied(self, decision, tools, client_id, client_perms, archive_id, actor_id) -> set:
        """硬拒绝集合 = 客户端权限 + 档案策略；两层都生效。"""
        denied = self._denied_tools(decision, tools, client_id, client_perms)
        try:
            policy_denied = self._archive_policy_denied(archive_id, actor_id, tools)
        except Exception:
            policy_denied = set()
        if policy_denied:
            denied |= policy_denied
            decision.archive_policy = True
        decision.denied = sorted(denied)
        return denied

    def _skip(self, decision, path, tools_after=None):
        """不裁剪就收工：只记路径与最终条数。"""
        decision.path = path
        decision.tools_after = decision.tools_before if tools_after is None else tools_after
        decision.tools_out = list(decision.tools_in)
        return self._finalize(decision, None)

    def _apply_deny(self, decision, tools, denied) -> None:
        """没走裁剪也要把 deny 掉的摘掉；只有真摘掉了才算 applied。"""
        if not denied:
            return
        payload = [t for t in tools if catalog.tool_name(t) not in denied]
        if len(payload) == len(tools):
            return
        decision.payload_tools = payload
        decision.tools_out = catalog.tool_names(payload)
        decision.tools_after = len(payload)
        decision.applied = True
        decision.drop_tool_fields = not payload

    def _gate_disabled(self, decision, tools, denied):
        """闸门关着也要执行 deny：安全策略不随灰度开关失效。"""
        decision.path = "disabled"
        decision.error = (self.cfg.load_error() or "")[:120]
        decision.tools_after = decision.tools_before
        self._apply_deny(decision, tools, denied)
        return self._finalize(decision, None)

    def _is_exempt(self, client_id) -> bool:
        """客户端可以在配置里申请豁免，豁免也只免裁剪、不免 deny。"""
        exempt = {str(x) for x in (self.cfg.gateway().get("exempt_clients") or [])}
        return bool(client_id) and client_id in exempt

    def _gate_exempt(self, decision, tools, denied):
        decision.path = "exempt"
        decision.tools_after = decision.tools_before
        self._apply_deny(decision, tools, denied)
        return self._finalize(decision, None)

    def _trim_by_category(self, decision, messages, tools, denied, platform, channel_type):
        """按类目裁剪：快判 → 分类器 → 组装 payload → max_tools 兜底。"""
        cats = self.cfg.categories()
        if not cats:
            return self._skip(decision, "empty_catalog")
        always, view = self._catalog_view(decision, tools)
        # 本次全是未登记工具：没有已知类目可裁，不白跑分类器
        if not view.by_category:
            decision.path = "unknown_only"
            policy = str((self.cfg.get() or {}).get("unknown_tool_policy") or "always")
            if policy == "always":
                decision.reason = "全部是未登记工具，按 unknown_tool_policy=always 放行"
                max_tools = int(self.cfg.gateway().get("max_tools") or 0)
                if max_tools > 0 and len(tools) > max_tools:
                    decision.reason += f"；超过 max_tools={max_tools}，按原顺序截断"
                    return self._finish_payload(decision, list(tools[:max_tools]))
                return self._skip(decision, "unknown_only")
            decision.reason = "全部是未登记工具，按 unknown_tool_policy=deny 裁掉"
            return self._finish_payload(decision, list(always))
        used = self._pick_categories(decision, messages, view, cats, always, platform, channel_type)
        return self._finish_payload(decision, self._cap_payload(tools, always, used, denied))

    def _catalog_view(self, decision, tools):
        """按配置把工具分成 常驻 / 类目 / 未归类 三份。"""
        always = [t for t in self.cfg.always_available() if t in set(decision.tools_in)]
        view = catalog.build_view(tools, self.cfg.category_by_tool(), always)
        decision.unknown_tools = view.unknown
        return always, view

    def _pick_categories(self, decision, messages, view, cats, always, platform, channel_type):
        """先走快判，判不出来再问执行 AI。"""
        settings = self.cfg.get()
        route = fastpath.route(messages, settings)
        used = self._from_fastpath(route, decision, view, cats, always, settings)
        if used:
            return used
        return self._from_classifier(
            decision, messages, view, cats, always, settings,
            channel_type=channel_type, platform=platform,
        )

    def _cap_payload(self, tools, always, used, denied):
        """保持上游顺序组装；超过 max_tools 时先保常驻工具。"""
        allowed = (set(always) | set(used)) - denied
        payload = [t for t in tools if catalog.tool_name(t) in allowed]
        max_tools = int(self.cfg.gateway().get("max_tools") or 0)
        if max_tools <= 0 or len(payload) <= max_tools:
            return payload
        always_set = set(always)
        head = [t for t in payload if catalog.tool_name(t) in always_set]
        tail = [t for t in payload if catalog.tool_name(t) not in always_set]
        return (head + tail)[:max_tools]

    def _finish_payload(self, decision, payload):
        decision.payload_tools = payload
        decision.tools_out = catalog.tool_names(payload)
        decision.tools_after = len(payload)
        decision.applied = True
        decision.drop_tool_fields = not payload
        return self._finalize(decision, None)

    @staticmethod
    def strip_choice_fields(extra: Dict[str, Any]) -> Dict[str, Any]:
        """只要工具集被改动过，就剥掉 tool_choice / parallel_tool_calls。

        上游对"缺省工具却带着 tool_choice:'auto'"的行为不一致，部分会直接 400；
        统一按 OpenAI 规范处理：没有显式工具集就不带这两个字段。
        """
        out = dict(extra or {})
        out.pop("tool_choice", None)
        out.pop("parallel_tool_calls", None)
        return out

    @staticmethod
    def choice_still_valid(choice: Any, tools: Any) -> bool:
        """强制指定的工具还在 payload 里时，不要剥 tool_choice。

        auto / none / required 以及缺省都视为仍有效；只有
        {"type":"function","function":{"name":...}} 指向已被裁掉的工具时才算失效。
        """
        if choice is None or isinstance(choice, str):
            return True
        if isinstance(choice, dict):
            name = str(((choice.get("function") or {}).get("name")) or "").strip()
            if not name:
                return True
            return name in set(catalog.tool_names(tools or []))
        return True

    def _previous(self, decision: Decision, settings: Dict[str, Any]) -> List[str]:
        gateway = (settings or {}).get("gateway") or {}
        sticky = max(0, int(gateway.get("sticky_tool_turns") or 0))
        key = decision.session_key
        cats = self.sessions.categories(key) if key else []
        if not cats:
            return []
        turns = self.sessions.tool_turns(key)
        if turns > sticky:
            return []
        return cats

    def _from_fastpath(self, route, decision, view, cats, always, settings) -> List[str]:
        if route == fastpath.PATH_TOOL_RESULT:
            decision.path = fastpath.PATH_TOOL_RESULT
            previous = self._previous(decision, settings)
            if not previous:
                return self._tools_for([], view, always, cats, settings)
            decision.categories = previous
            decision.reason = "工具结果回灌，沿用上轮类别"
            return self._tools_for(previous, view, always, cats, settings)

        if route == fastpath.PATH_TRIVIAL:
            decision.path = fastpath.PATH_TRIVIAL
            previous = self._previous(decision, settings)
            if not previous:
                return self._tools_for([], view, always, cats, settings)
            decision.categories = previous
            decision.reason = "语气词，沿用上轮类别"
            return self._tools_for(previous, view, always, cats, settings)

        return []

    def _from_classifier(
        self, decision, messages, view, cats, always, settings,
        channel_type: str, platform: str,
    ) -> List[str]:
        gateway = (settings or {}).get("gateway") or {}
        cls_cfg = gateway.get("classifier") or {}
        floor = float(cls_cfg.get("confidence_floor") or 0.0)
        on_failure = str(gateway.get("on_failure") or "previous_then_all")
        user_text = fastpath.last_user_text(messages)

        classify = self._get_classifier()
        classify_error = ""
        try:
            result = classify(
                user_text,
                self.sessions.as_lines(decision.session_key),
                cats,
                settings,
                channel_type=channel_type,
                platform=platform,
            )
        except Exception as exc:
            # 分类器本身抛异常（网络/解析/代码 bug）也按"分类失败"兜底，不影响对话
            result = None
            classify_error = f"{type(exc).__name__}:{exc}"
            logger.info("[ToolShelves] 分类器异常，走兜底: %s", exc)

        decision.path = fastpath.PATH_CLASSIFY
        decision.latency_ms = int(getattr(result, "latency_ms", 0) or 0)
        decision.confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        decision.classifier_prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
        decision.classifier_completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
        decision.classifier_model = "logic_slot"

        ok = bool(getattr(result, "ok", False))
        need = bool(getattr(result, "need_tools", False))
        picked = [c for c in (getattr(result, "categories", []) or []) if c in cats]

        # 分类成功且高置信
        if ok and need and picked and decision.confidence >= floor:
            decision.categories = picked
            decision.reason = str(getattr(result, "reason", "") or "")
            self._remember_categories(decision)
            return self._tools_for(picked, view, always, cats, settings)

        # 明确判定不需要工具 → 只留常驻 + 未归类（按 unknown_tool_policy）
        if ok and not need:
            decision.categories = []
            decision.reason = str(getattr(result, "reason", "") or "") or "判定无需工具"
            self._remember_categories(decision)
            return self._tools_for([], view, always, cats, settings)

        # 其余（低置信 / 空类别 / 解析失败 / 超时 / 抛异常）→ 兜底
        decision.error = classify_error or str(getattr(result, "error", "") or "")
        if on_failure == "previous_then_all":
            previous = self._previous(decision, settings)
            if previous:
                decision.categories = previous
                decision.reason = f"分类失败({decision.error or 'low_conf'})，沿用上轮类别"
                return self._tools_for(previous, view, always, cats, settings)

        decision.path = "fallback_all"
        decision.categories = list(cats.keys())
        decision.reason = f"分类失败({decision.error or 'low_conf'})，全量放行"
        # 全量放行 = 类别工具 + 未归类工具，绝不丢任何上游工具
        return self._tools_for(list(cats.keys()), view, always, cats, settings)

    def _remember_categories(self, decision: Decision) -> None:
        if decision.session_key:
            try:
                self.sessions.set_categories(decision.session_key, decision.categories)
            except Exception:
                pass

    def _tools_for(self, categories, view, always, cats, settings) -> List[str]:
        names: List[str] = list(always)
        for cat in categories:
            for tool in (view.by_category.get(cat) or []):
                if tool not in names:
                    names.append(tool)
        # 配置里没登记的未知工具：默认放行（unknown_tool_policy=always）
        policy = str((settings or {}).get("unknown_tool_policy") or "always")
        if policy == "always":
            for tool in view.unknown:
                if tool not in names:
                    names.append(tool)
        return names

    def _finalize(self, decision: Decision, started: Optional[float]) -> Decision:
        if started is not None:
            decision.latency_ms = max(decision.latency_ms, int((time.time() - started) * 1000))
        try:
            self.last_decision = decision.as_dict()
        except Exception:
            pass
        try:
            self._get_metrics().record(
                path=decision.path,
                tools_in=decision.tools_before,
                tools_out=decision.tools_after,
                categories=decision.categories,
                confidence=decision.confidence,
                latency_ms=decision.latency_ms,
                classifier_ok=decision.path == fastpath.PATH_CLASSIFY and not decision.error,
                error=decision.error,
                detail=decision.path not in PASS_PATHS and decision.path != "fallback_all",
            )
        except Exception:
            pass
        return decision


tool_gate = ToolGate()

__all__ = ["ToolGate", "Decision", "tool_gate", "PASS_PATHS"]
