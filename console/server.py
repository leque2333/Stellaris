# -*- coding: utf-8 -*-
"""
Stellaris Console HTTP Server
极简高性能控制台服务与标准 OpenAI 兼容投射网关 (/v1)
"""

# ---------------------------------------------------------------------------
# 身份传输层契约（完整说明与示例见 docs/TRANSPORT_LAYER.md）
#
# 入口（模块 → 中枢）：
#   - 最省事：OpenAI 请求体里带 "user": "<发送者底层 UID>"；
#   - 或请求头 X-User-Id: <发送者底层 UID>，可配 X-Platform / X-User-Name；
#   - X-Channel: <群/会话 ID> 只用于记忆隔离，不参与身份匹配；
#   - 身份只认底层用户 UID，不认群号/会话 ID/复合 UMO。
#
# 出口（中枢 → 模块）：
#   - 非流式：响应体 "hub": {actor_id, archive_id, platform, sender_uid, affection, bound_by}；
#   - 流式/非流式：响应头 X-Hub-Actor-Id / X-Hub-Archive-Id / ... ；
#   - 模块不改代码也能靠这些字段知道中枢认成了谁。
#
# 模块不可改代码时：
#   - 边缘代理从 body.user / session / 正文提取 UID，注入 X-User-Id；
#   - 或一人一 Key，用 clients.json 的 default_actor / 档案映射。
# ---------------------------------------------------------------------------

import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import backend

# 端口：命令行参数优先（只认纯数字），其次环境变量，最后默认 18890。
# 不这么写的话，被 unittest / import 时会拿 argv[1] 当端口直接崩。
PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(os.getenv("CONSOLE_PORT", 18890))
os.environ["CONSOLE_PORT"] = str(PORT)
STATIC_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(STATIC_DIR)


def _persona_name() -> str:
    """当前人设角色名；来自 data/persona.md，可随时替换。"""
    from agent_core.persona_identity import get_persona_name

    return get_persona_name()


def _master_name() -> str:
    from agent_core.identity import IdentityResolver

    return IdentityResolver.get_master_name()


def _split_conversation_id(raw):
    """把 AstrBot 的会话标识拆成 (类型, 群号, 会话 id)。

    兼容 `platform:GroupMessage:784826874`、`platform:FriendMessage:2986371638`
    以及纯 UUID / 纯 session id 三种形态。
    """
    value = str(raw or "").strip()
    if not value:
        return "", "", ""
    parts = value.split(":")
    tail = parts[-1] if parts else value
    lowered = value.lower()
    if "groupmessage" in lowered or ":group:" in lowered:
        return "group", tail, value
    if "friendmessage" in lowered or "privatemessage" in lowered:
        return "private", "", tail
    return "conversation", "", value


_IMAGE_MARK = "[图片]"


MEMORY_TAG = "[[记忆检索:"
_MEMORY_TOOL_BLOCK = (
    "\n\n【记忆检索】如果你需要更早的记忆（时间轴只覆盖最近十几小时），"
    "可以在回复开头单独写一行：\n"
    "[[记忆检索: 关键词1 关键词2 | 起始日期 可选 | 结束日期 可选 | 工具名 可选]]\n"
    "写完这一行后，继续正常回答用户（不要只写这一行）；中枢会查记忆库，"
    "把结果补给你再让你重答一次。这一行不会发给用户。"
)


def _memory_tool_prompt() -> str:
    import os

    return "" if os.getenv("HUB_MEMORY_TOOL", "1") == "0" else _MEMORY_TOOL_BLOCK


def _parse_memory_directive(text) -> dict:
    """解析 [[记忆检索: 关键词 | 起 | 止 | 工具]]；没有就返回空。"""
    import re

    match = re.search(r"\[\[记忆检索[:：]([^\]]*)\]\]", str(text or ""))
    if not match:
        return {}
    parts = [item.strip() for item in match.group(1).split("|")]
    result = {"keywords": parts[0] if parts else ""}
    if len(parts) > 1 and parts[1]:
        result["start"] = parts[1]
    if len(parts) > 2 and parts[2]:
        result["end"] = parts[2]
    if len(parts) > 3 and parts[3]:
        result["source"] = parts[3]
    return result


def _strip_memory_directive(message):
    """把指令行从最终回复里去掉，用户看不到。"""
    import re

    if not message:
        return message
    content = re.sub(r"^\s*\[\[记忆检索[:：][^\]]*\]\]\s*\n?", "", str(message.get("content") or ""))
    return {**message, "content": content}


def _summarize_parts(parts) -> str:
    """多模态内容 → 纯文本 + 图片短语（图片数量记下来，二进制丢掉）。"""
    texts = []
    images = 0
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            texts.append(str(part.get("text") or ""))
        elif kind in ("image_url", "image", "input_image"):
            images += 1
    text = _scrub_binary("".join(texts))
    if images:
        text = f"{text} {_IMAGE_MARK}×{images}" if text else f"{_IMAGE_MARK}×{images}"
    return text


def _scrub_messages(messages):
    """留档用的消息：图片一律换成短语，别把 base64 写进记忆库。"""
    if not isinstance(messages, list):
        return messages
    cleaned = []
    for message in messages:
        if not isinstance(message, dict):
            cleaned.append(message)
            continue
        content = message.get("content")
        if isinstance(content, list):
            cleaned.append({**message, "content": _summarize_parts(content)})
        elif isinstance(content, str):
            cleaned.append({**message, "content": _scrub_binary(content)})
        else:
            cleaned.append(message)
    return cleaned


def _scrub_binary(value) -> str:
    """把 data:...;base64,... 换成一句短语，避免整段二进制进记忆。"""
    text = str(value or "")
    if "base64," not in text and "data:image" not in text:
        return text
    import re

    def _replace(match):
        head = match.group(0)
        return f"[图片数据已省略 {len(head)} 字节]"

    return re.sub(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=\s]+", _replace, text)


def _header_text(value) -> str:
    """HTTP 头只保证 ASCII；中文名称由客户端 URL 编码，这里还原。"""
    text = str(value or "")
    if "%" in text:
        try:
            return unquote(text)
        except Exception:
            return text
    return text


def _tool_name_of(tool) -> str:
    """工具条目有两种形状（裸 name / function.name），统一取名字。"""
    return (tool.get("function") or {}).get("name") or tool.get("name") or ""


def _is_tool_result(message) -> bool:
    """是不是工具返回的那一条（role == tool）。"""
    return isinstance(message, dict) and message.get("role") == "tool"


def _join_tool_names(tool_calls):
    """本轮实际调用的工具名，去重后逗号拼起来（容忍非列表输入）。"""
    if not isinstance(tool_calls, (list, tuple)):
        return ""
    names = []
    for call in tool_calls:
        name = ((call or {}).get("function") or {}).get("name")
        if name and name not in names:
            names.append(str(name))
    return ",".join(names)


def _narrative_of(ctx, content):
    """一句自传体叙述：谁说了什么、它怎么回。"""
    if ctx["is_logic_task"]:
        return f"{ctx['actor_name']}提出了复杂逻辑任务；{_persona_name()}调度幕后工兵推导并转述：「{content[:50]}」"
    return f"{ctx['actor_name']}说：「{ctx['user_query'][:50]}」；{_persona_name()}回答：「{content[:50]}」"


def _record_fact_usage(p_in, p_out):
    """AI 抽事实的 token 走 memory.facts 通道记账。"""
    backend.record_token_usage_event("memory.fact_extraction", p_in, p_out, source="memory.facts")


def _extract_facts_with_ai(person_id, user_query):
    """AI 抽事实：慢，放后台跑；token 也记账。"""
    try:
        from agent_core.memory.facts import FactStore

        FactStore().store_extracted(
            person_id, user_query, use_ai=True, source="ai_extract",
            usage_cb=_record_fact_usage,
        )
    except Exception:
        pass


def _evaluate_affection(ctx, content):
    """好感度评估失败静默：别让记忆副作用影响对话。"""
    try:
        _affection_step(ctx, content)
    except Exception:
        pass


def _affection_step(ctx, content):
    """未定给初始值，已定给增量；clamp 由 ProfileManager 负责。"""
    from agent_core.profile import ProfileManager
    from agent_core.profile.affection_ai import estimate_affection_delta, estimate_initial_affection
    from agent_core.storage import get_store

    person_id = ctx.get("person_id") or ctx["actor_id"]
    profile_manager = ProfileManager(get_store())
    current = profile_manager.get_affection(person_id)
    user_text = ctx.get("user_query") or ""
    if current is None:
        value = estimate_initial_affection(user_text, content)
        if value is not None:
            profile_manager.set_affection(person_id, value, reason="首次交流，AI 给出初始好感")
        return
    result = estimate_affection_delta(user_text, content, int(current))
    if result:
        delta, reason = result
        profile_manager.update_affection(person_id, delta, reason)


def _ledger_tool_calls(accumulator):
    """流式只给了 tool_calls 增量，这里还原成账本要的形状。"""
    return [{"function": {"name": name}} for name in accumulator.tool_call_names]

def _bearer_token(headers) -> str:
    """Authorization: Bearer xxx 或 X-Hub-Key，取一个。"""
    token = headers.get('Authorization', '')
    if token.lower().startswith('bearer '):
        return token[7:].strip()
    return headers.get('X-Hub-Key') or ''


def _json_text(value) -> str:
    """非字符串的 raw 先转 JSON 文本，转不了再 str()。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _record_compression(stats) -> None:
    """压缩指标落一份；统计失败不影响返回。"""
    try:
        from agent_core.tool_shelves.metrics import metrics as shelf_metrics

        shelf_metrics.record_compression([stats])
    except Exception:
        pass


def _split_setting_line(line):
    """一行「标签：内容」→ (标签, 内容)；空白行返回 None。"""
    text = str(line or '').strip()
    if not text:
        return None
    for separator in ('：', ':'):
        if separator in text:
            head, tail = text.split(separator, 1)
            if head.strip() and len(head.strip()) <= 20:
                return head.strip(), tail.strip()
            break
    return '设定', text


def _asset_version() -> int:
    """静态资源版本号：取 static/js、static/css 与 index.html 的最新 mtime。"""
    latest = 0
    static_root = os.path.join(STATIC_DIR, "static")
    for base, _dirs, files in os.walk(static_root):
        for name in files:
            if not name.endswith((".js", ".css")):
                continue
            try:
                latest = max(latest, os.path.getmtime(os.path.join(base, name)))
            except OSError:
                pass
    try:
        latest = max(latest, os.path.getmtime(os.path.join(STATIC_DIR, "index.html")))
    except OSError:
        pass
    return int(latest or time.time())
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent_core import api_security, audit_log, deploy_check  # noqa: E402
from agent_core.auth import AuthManager  # noqa: E402
from agent_core.clients import client_registry  # noqa: E402

auth_mgr = AuthManager(PROJECT_ROOT)

# 工具按需加载闸门：配置 data/tool_shelves.json。
# 三重兜底：import 失败 / 构造失败 / 运行异常，都只降级为"全量透传"，绝不让中枢起不来。
try:
    from agent_core.tool_shelves import ToolGate
    tool_gate = ToolGate()
except Exception as _tool_gate_err:  # pragma: no cover - 仅在极端环境下触发
    print(f"[ToolShelves] 闸门不可用，工具全量透传: {_tool_gate_err}", flush=True)
    tool_gate = None



_LAST_LOG_ERROR_COUNT = 0


_ERROR_PATTERNS = (
    "Traceback", "Error", "Exception", "失败", "报错", "超时",
    "401", "403", "500", "502", "503", "URLError", "refused", "timed out",
)


def _is_error_line(line: str) -> bool:
    text = str(line or "")
    if not text.strip():
        return False
    return any(pat in text for pat in _ERROR_PATTERNS)



# 静态资源类型表：只覆盖前端真正用到的几种
_STATIC_CONTENT_TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}

# 早期「连接管理」模块的路径，全部 404
_REMOVED_PATH_PREFIXES = (
    '/api/connection', '/api/modules', '/api/hosts', '/api/contexts',
    '/api/components', '/api/services', '/api/mcp', '/api/tool-permissions',
    '/api/bridges', '/api/plugins', '/api/execution_ai', '/qq/events',
)

# 控制台 GET 路由表：路径 → 方法名（方法名延迟解析，表可以写在类外）
_CONSOLE_GET_ROUTES = {
    '/api/overview': '_get_overview',
    '/api/system/resources': '_get_system_resources',
    '/api/timeline': '_get_timeline',
    '/api/persona': '_get_persona',
    '/api/persona/rules': '_get_persona_rules',
    '/api/models/sync_upstream': '_get_models_sync',
    '/api/sniff_key': '_get_sniff_key',
    '/api/models_config': '_get_models_config',
    '/api/compute/config': '_get_compute_config',
    '/api/ledger': '_get_ledger',
    '/api/tokens': '_get_tokens',
    '/api/access_policy': '_get_access_policy',
    '/api/connectors': '_get_connectors',
    '/api/bindings': '_get_bindings',
    '/api/knowledge': '_get_knowledge',
    '/api/memory/facts': '_get_memory_facts',
    '/api/memory/graph': '_get_memory_graph',
    '/api/tools/shelves': '_get_tools_shelves',
    '/api/tools/shelves/status': '_get_tools_shelves',
    '/api/identity/scan': '_get_identity_scan',
    '/api/affection': '_get_affection',
    '/api/affection/overview': '_get_affection',
    '/api/logs': '_get_logs',
    '/api/logs/alerts': '_get_log_alerts',
    '/api/tools': '_get_tools',
    '/api/tools/policies': '_get_tool_policies',
    '/api/messages': '_get_messages',
    '/api/access_control': '_get_access_control',
    '/api/audit': '_get_audit',
    '/api/deploy_check': '_get_deploy_check',
    '/api/memory/timeline_curve': '_get_timeline_curve',
}


# 控制台 POST 路由表：路径 → (方法名, 是否需要登录)
_POST_ROUTES = {
    '/api/tools/shelves/reload': ('_post_shelves_reload', True),
    '/api/tools/shelves/metrics/reset': ('_post_shelves_metrics_reset', True),
    '/api/tools/clean': ('_post_tool_clean', False),
    '/api/tools': ('_post_tool_update', True),
    '/api/tools/policy': ('_post_tool_policy', True),
    '/api/tools/policy/delete': ('_post_tool_policy_delete', True),
    '/api/identity/bind': ('_post_identity_bind', True),
    '/api/identity/hide': ('_post_identity_hide', True),
    '/api/ledger/clear': ('_post_ledger_clear', True),
    '/api/access_policy': ('_post_access_policy', True),
    '/api/memory/facts/rollback': ('_post_fact_rollback', True),
    '/api/memory/facts': ('_post_fact_upsert', True),
    '/api/auth/login': ('_post_login', False),
    '/api/auth/logout': ('_post_logout', False),
    '/api/auth/change_password': ('_post_change_password', True),
    '/api/connectors/scan': ('_post_connectors_scan', False),
    '/api/adapters/analyze': ('_post_adapter_analyze', False),
    '/api/adapters/generate': ('_post_adapter_generate', False),
    '/api/adapters/attach': ('_post_adapter_attach', False),
    '/api/bindings': ('_post_bindings', True),
    '/api/persona': ('_post_persona', True),
    '/api/persona/rules': ('_post_persona_rules', True),
    '/api/models/sync_upstream': ('_post_models_sync', True),
    '/api/models_config': ('_post_models_config', True),
    '/api/credits': ('_post_credits', True),
    '/api/compute/config': ('_post_compute_config', True),
    '/api/compute/test': ('_post_compute_test', True),
    '/api/access_control': ('_post_access_control', True),
}

class ConsoleHandler(BaseHTTPRequestHandler):
    # 高频轮询不写访问日志，避免把运行日志刷屏
    _NOISE_PATHS = (
        "/api/tokens", "/api/overview", "/api/resources",
        "/api/logs", "/api/health", "/favicon.ico",
    )

    def log_message(self, format, *args):
        """访问日志走 stdout；err.log 只留真正的异常，避免徽章误判。"""
        try:
            text = format % args
        except Exception:
            text = str(format)
        if any(path in text for path in self._NOISE_PATHS):
            return
        import sys as _sys

        try:
            _sys.stdout.write("%s - - [%s] %s\n" % (
                self.address_string(), self.log_date_time_string(), text,
            ))
            _sys.stdout.flush()
        except Exception:
            pass

    def _get_token(self):
        cookie_header = self.headers.get('Cookie', '')
        if cookie_header:
            from http.cookies import SimpleCookie
            try:
                cookies = SimpleCookie(cookie_header)
                if 'anan_session' in cookies:
                    return cookies['anan_session'].value
            except Exception:
                pass
        auth_hdr = self.headers.get('Authorization', '')
        if auth_hdr.startswith('Bearer '):
            return auth_hdr[7:].strip()
        return self.headers.get('X-Session-Token')

    def _is_authenticated(self):
        return auth_mgr.validate_session(self._get_token()) is not None

    def _allowed_origin(self):
        """只有 Origin 与本次请求同源才回 CORS 头；跨站请求一律不给。"""
        origin = (self.headers.get('Origin') or '').strip()
        if not origin:
            return ''
        host = (self.headers.get('Host') or '').strip()
        try:
            return origin if urlparse(origin).netloc == host else ''
        except ValueError:
            return ''

    def _session_cookie(self, token, max_age):
        """会话 Cookie：HttpOnly 防脚本读取，HTTPS 下再加 Secure。"""
        parts = [f'anan_session={token}', 'Path=/', 'SameSite=Lax',
                 f'Max-Age={max_age}', 'HttpOnly']
        if (self.headers.get('X-Forwarded-Proto') or '').lower() == 'https':
            parts.append('Secure')
        return '; '.join(parts)

    def _send_json(self, data, status=200, extra_headers=None):
        if int(status or 200) >= 500:
            try:
                from agent_core import messages as _msgs

                detail = data.get("error") if isinstance(data, dict) else ""
                _msgs.error(f"{self.path.split('?')[0]} -> {status} {detail or ''}".strip())
            except Exception:
                pass
        origin = self._allowed_origin()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Session-Token, X-Channel, X-Channel-Type, X-User-Id, X-User-Name')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        if self._access_blocked():
            return
        origin = self._allowed_origin()
        self.send_response(200)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Session-Token, X-Channel, X-Channel-Type, X-User-Id, X-User-Name')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if self._access_blocked():
            return
        if self._signature_blocked('GET', path, b''):
            return
        # 早期「连接管理」模块已下线，这些前缀统一 404，别让前端以为还能用
        if path.startswith(_REMOVED_PATH_PREFIXES):
            return self._send_json({'error': '连接管理已移除'}, status=404)
        if self._serve_public_page(path):
            return
        if path == '/api/auth/status':
            return self._send_json(auth_mgr.get_status(self._get_token()))
        if path.startswith('/v1/') or path.startswith('/api/v1/'):
            return self._handle_gateway_get(path)
        if path.startswith('/hub/v1/'):
            return self._handle_hub_get(path)
        if path.startswith('/api/'):
            return self._handle_console_api_get(path)
        self.send_response(404)
        self.end_headers()

    # ---------------- GET：页面与静态资源 ----------------
    def _serve_public_page(self, path):
        """登录页 / 静态资源 / 根路由：这三类不查登录态。"""
        if path in ('/login', '/login.html'):
            return self._send_login_page()
        if path.startswith('/static/'):
            self._send_static(path[len('/static/'):].lstrip('/'))
            return True
        if path in ('/', '/index.html'):
            self._send_console_page()
            return True
        return False

    def _send_login_page(self):
        """登录页存在就发；返回是否发过（根路由要靠这个判断）。"""
        login_file = os.path.join(STATIC_DIR, 'login.html')
        if not os.path.exists(login_file):
            return False
        self._send_file(login_file, 'text/html; charset=utf-8')
        return True

    def _send_console_page(self):
        """根路由：未登录先给登录页；登录页缺失就照旧给控制台。"""
        if not self._is_authenticated() and self._send_login_page():
            return
        html_file = os.path.join(STATIC_DIR, 'index.html')
        if not os.path.exists(html_file):
            self.send_response(404)
            self.end_headers()
            return
        self._send_console_html(html_file)

    def _send_console_html(self, html_file):
        """控制台首页：注入静态资源版本号，免得浏览器拿旧 js/css。"""
        with open(html_file, 'r', encoding='utf-8') as handle:
            html = handle.read()
        html = re.sub(r'\?v=\d+', f'?v={_asset_version()}', html)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _send_static(self, rel_path):
        """静态资源：命中就发，未命中 404。"""
        file_path = os.path.join(STATIC_DIR, 'static', rel_path)
        if not (os.path.exists(file_path) and os.path.isfile(file_path)):
            self.send_response(404)
            self.end_headers()
            return
        extension = os.path.splitext(file_path)[1].lower()
        content_type = _STATIC_CONTENT_TYPES.get(extension, 'application/octet-stream')
        self._send_file(file_path, content_type, 'no-cache, no-store, must-revalidate')

    def _send_file(self, file_path, content_type, cache_control=None):
        """最朴素的静态文件下发；要禁缓存就传 cache_control。"""
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if cache_control:
            self.send_header('Cache-Control', cache_control)
        self.end_headers()
        with open(file_path, 'rb') as handle:
            self.wfile.write(handle.read())

    # ---------------- GET：三套接口分发 ----------------
    def _handle_gateway_get(self, path):
        """ /v1 的 GET 只开放模型列表；其余由 POST 处理。"""
        if path in ('/v1/models', '/api/v1/models'):
            self._maybe_auto_provision()
            return self._send_json(backend.get_universal_models_list())
        self.send_response(404)
        self.end_headers()

    def _handle_hub_get(self, path):
        """/hub/v1：给外部工具用，Key 鉴权，不走 Web 登录态。"""
        if path.startswith('/hub/v1/archives'):
            return self._handle_archive_get()
        if path == '/hub/v1/knowledge/search':
            return self._get_hub_knowledge_search()
        # services 相关实现随 agent_core.services 一起下线了，别再抛 ImportError
        self.send_response(404)
        self.end_headers()

    def _handle_console_api_get(self, path):
        """控制台 /api/*：先过登录态，再查路由表。"""
        if not self._is_authenticated():
            return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
        if path.startswith('/api/archives'):
            return self._handle_console_archive_get()
        handler = _CONSOLE_GET_ROUTES.get(path)
        if handler:
            return getattr(self, handler)()
        self.send_response(404)
        self.end_headers()

    def _get_hub_knowledge_search(self):
        """知识库检索（外部工具用）。"""
        _, err = self._hub_client(require_admin=False)
        if err:
            return self._hub_error(err)
        from agent_core.knowledge import KnowledgeBase
        from agent_core.storage import get_store

        query = parse_qs(urlparse(self.path).query)
        text = (query.get('q') or query.get('query') or [''])[0]
        limit = int((query.get('limit') or ['3'])[0] or 3)
        return self._send_json({
            'object': 'list',
            'data': KnowledgeBase(get_store()).search(text, limit=limit),
        })

    # ---------------- GET：控制台账本 / 人格 / 档案 ----------------
    def _get_overview(self):
        """仪表盘一次拉全：摘要 / 资源 / 人格 / 账本 / 时间轴。"""
        return self._send_json({
            "summary": backend.get_system_summary(),
            "resources": backend.get_system_resources_metrics(),
            "current_persona": backend.get_current_persona(),
            "presets": backend.get_preset_personas(),
            "persona_rules": backend.get_persona_rules(),
            "tokens": backend.get_token_analytics(),
            "models_config": backend.get_models_config(),
            "timeline": backend.get_timeline_events(limit=60),
            "modules": backend.get_modules_overview(),
            "modules_permissions": backend.get_module_permissions(),
            "compute_config": backend.get_compute_config(),
        })

    def _get_system_resources(self):
        return self._send_json(backend.get_system_resources_metrics())

    def _get_timeline(self):
        return self._send_json(backend.get_timeline_events(limit=60))

    def _get_persona(self):
        return self._send_json(backend.get_current_persona())

    def _get_persona_rules(self):
        return self._send_json(backend.get_persona_rules())

    def _get_models_sync(self):
        return self._send_json(backend.sync_models_from_upstream())

    def _get_models_config(self):
        return self._send_json(backend.get_models_config())

    def _get_compute_config(self):
        return self._send_json(backend.get_compute_config())

    def _get_ledger(self):
        """账单页：完整 token_analytics（含 recent_ledger / 分组 / 余额）。"""
        return self._send_json(backend.get_token_analytics())

    def _get_tokens(self):
        """首页轻量统计：只查账本，保证首屏数字快。"""
        return self._send_json(backend.get_token_analytics())

    def _get_sniff_key(self):
        """从 URL 里取一个 key（前端粘贴用）。"""
        query = parse_qs(urlparse(self.path).query)
        key = (query.get('key') or query.get('api_key') or [''])[0]
        return self._send_json(backend.handle_sniff_key({"key": key}))

    def _get_access_policy(self):
        from agent_core.access_policy import load_policy

        return self._send_json({"ok": True, "policy": load_policy()})

    def _get_connectors(self):
        return self._send_json({'status': _get_discovery_service().status()})

    def _get_bindings(self):
        from agent_core.storage import get_store

        return self._send_json({'object': 'list', 'data': get_store().list_bindings()})

    def _get_knowledge(self):
        from agent_core.knowledge import KnowledgeBase
        from agent_core.storage import get_store

        return self._send_json({'object': 'list', 'data': KnowledgeBase(get_store()).list()})

    def _get_memory_facts(self):
        """事实列表；给了 subject_id + as_of 就查那个时间点的事实。"""
        query = parse_qs(urlparse(self.path).query)
        subject_id = (query.get('subject_id') or [''])[0].strip()
        as_of = (query.get('as_of') or [''])[0].strip()
        if subject_id and as_of:
            return self._get_facts_at(subject_id, as_of)
        from agent_core.memory.facts import FactStore

        facts = FactStore().list_facts(
            subject_id=subject_id,
            status=(query.get('status') or ['active'])[0].strip() or 'active',
            query=(query.get('q') or [''])[0].strip(),
            limit=int((query.get('limit') or ['200'])[0] or 200),
        )
        return self._send_json({'ok': True, 'facts': facts})

    def _get_facts_at(self, subject_id, as_of):
        """双时间事实：as_of 是 unix 时间戳，非法就 400。"""
        from agent_core.memory.facts import FactStore

        try:
            at_time = float(as_of)
        except ValueError:
            return self._send_json({'ok': False, 'error': 'as_of 必须是 unix 时间戳'}, status=400)
        return self._send_json({'ok': True, 'facts': FactStore().facts_at(subject_id, at_time)})

    def _get_memory_graph(self):
        from agent_core.memory.facts import FactStore

        query = parse_qs(urlparse(self.path).query)
        subject_id = (query.get('subject_id') or [''])[0].strip()
        if not subject_id:
            return self._send_json({'ok': False, 'error': 'subject_id 必填'}, status=400)
        return self._send_json({'ok': True, **FactStore().graph(subject_id)})

    def _get_tools_shelves(self):
        return self._send_json(self._tool_shelves_status())

    def _get_identity_scan(self):
        return self._send_json(self._identity_scan())

    def _get_affection(self):
        return self._send_json(self._affection_overview())

    def _get_logs(self):
        return self._send_json(self._logs_payload())

    def _get_log_alerts(self):
        return self._send_json(self._logs_alerts())

    def _get_tools(self):
        return self._send_json(self._tools_admin_list())

    def _get_tool_policies(self):
        return self._send_json(self._tools_policies())

    def _get_timeline_curve(self):
        """预览时间轴记忆曲线：前 30% 完整、中 40% 轻压、后 30% 深压。"""
        from agent_core.memory import timeline_curve

        params = dict(part.split('=', 1) for part in urlparse(self.path).query.split('&') if '=' in part)
        try:
            budget = max(500, min(int(params.get('budget', '20000')), 200000))
        except ValueError:
            budget = 20000
        store = self._memory_store()
        events = store.list_candidates(limit=5000) if store else []
        entries = timeline_curve.build_curve(events, budget_chars=budget)
        return self._send_json({
            "ok": True,
            "budget": budget,
            "summary": timeline_curve.summarize(entries),
            "entries": [item.as_dict() for item in entries[:200]],
        })

    def _get_deploy_check(self):
        """部署自检：端口 / docker 挂载 / 密钥 / 安全配置 / 审计日志。"""
        checks = deploy_check.run_checks(port=PORT)
        return self._send_json({
            "ok": True,
            "summary": deploy_check.summarize(checks),
            "checks": [item.as_dict() for item in checks],
        })

    def _get_audit(self):
        """安全审计日志（最新在前），?limit= 控制条数。"""
        query = urlparse(self.path).query
        params = dict(part.split('=', 1) for part in query.split('&') if '=' in part)
        try:
            limit = max(1, min(int(params.get('limit', '200')), 1000))
        except ValueError:
            limit = 200
        return self._send_json({"ok": True, "path": str(audit_log.log_path()),
                                "stats": audit_log.stats(),
                                "entries": audit_log.tail(limit)})

    def _get_access_control(self):
        """API 安全开关：本机限制 + 签名状态（密钥只报是否配置）。"""
        return self._send_json({"ok": True, **api_security.status()})

    def _post_access_control(self, body):
        """改开关：local_only / allow_cidrs / trusted_proxies / signature。"""
        allowed = ('local_only', 'allow_cidrs', 'trusted_proxies', 'signature')
        patch = {key: body[key] for key in allowed if key in body}
        if isinstance(patch.get('signature'), dict) and patch['signature'].pop('rotate', False):
            # 轮换：新密钥立刻生效，旧密钥留一段过渡期
            grace = int(patch['signature'].pop('grace_seconds', 1800) or 1800)
            saved = api_security.rotate_secret(grace)
            audit_log.record(
                "secret_rotated",
                dedupe=False,
                ip=api_security.client_ip(self.client_address[0], self.headers.get('X-Forwarded-For', '')),
                grace_seconds=grace,
            )
            return self._send_json({
                "ok": True,
                "rotated": True,
                "secret": saved["signature"]["secret"],
                "previous_valid_for": max(
                    0, saved["signature"]["previous_until"] - int(__import__("time").time())
                ),
            })
        if not patch:
            return self._send_json({"ok": False, "error": "没有可更新的字段"}, status=400)
        saved = api_security.save_config(patch)
        audit_log.record(
            "security_changed",
            dedupe=False,
            ip=api_security.client_ip(self.client_address[0], self.headers.get('X-Forwarded-For', '')),
            local_only=saved["local_only"],
            signature_enabled=saved["signature"]["enabled"],
            allow_cidrs=saved["allow_cidrs"],
        )
        return self._send_json({
            "ok": True,
            "local_only": saved["local_only"],
            "allow_cidrs": saved["allow_cidrs"],
            "signature": {
                "enabled": saved["signature"]["enabled"],
                "window_seconds": saved["signature"]["window_seconds"],
                "secret_configured": bool(saved["signature"]["secret"]),
            },
        })

    def _get_messages(self):
        """顶部消息铃铛：只返回本次启动之后的消息。"""
        try:
            from agent_core import messages as hub_messages

            return self._send_json({"ok": True, **hub_messages.snapshot()})
        except Exception as exc:
            return self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _identity_bind(self, body):
        """批量把 UID 绑到档案。

        写两处：
        - archive_identities：档案页可见、白名单生效
        - platform_bindings：请求期 resolve_archive_for_sender 用
        """
        if not self._is_authenticated():
            return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
        items = body.get('items') if isinstance(body, dict) else None
        if not isinstance(items, list) or not items:
            return self._send_json({'ok': False, 'error': 'items 必须是非空数组'}, status=400)

        from agent_core.storage import get_store

        store = get_store()
        results = []
        for raw in items:
            if not isinstance(raw, dict):
                results.append({'ok': False, 'error': 'item 必须是对象'})
                continue
            platform = str(raw.get('platform') or '').strip()
            uid = str(raw.get('uid') or '').strip()
            archive_id = str(raw.get('archive_id') or '').strip()
            display_name = str(raw.get('display_name') or '').strip()
            if not platform or not uid or not archive_id:
                results.append({'ok': False, 'uid': uid, 'error': 'platform / uid / archive_id 必填'})
                continue
            if not store.get_archive(archive_id):
                results.append({'ok': False, 'uid': uid, 'error': f'档案不存在: {archive_id}'})
                continue
            try:
                identity_id = store.link_identity(
                    archive_id, platform, uid,
                    display_name=display_name or None,
                    confidence=0.95,
                )
                binding_id = store.bind_context(
                    platform, uid, archive_id,
                    confidence=0.95, verified_by='identity_map',
                )
                results.append({
                    'ok': True, 'uid': uid, 'platform': platform,
                    'archive_id': archive_id,
                    'identity_id': identity_id, 'binding_id': binding_id,
                })
            except Exception as exc:
                results.append({'ok': False, 'uid': uid, 'error': f'{type(exc).__name__}: {exc}'})

        ok_count = sum(1 for r in results if r.get('ok'))
        return self._send_json({
            'ok': ok_count == len(results),
            'bound': ok_count,
            'failed': len(results) - ok_count,
            'results': results,
        })

    def _log_dir(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "logs")

    def _tool_shelf_cfg(self):
        try:
            gate = globals().get("tool_gate")
            if gate is not None and getattr(gate, "cfg", None) is not None:
                return gate.cfg
        except Exception:
            pass
        from agent_core.tool_shelves.config import ShelfConfig

        return ShelfConfig()

    def _tools_policies(self):
        """档案级工具策略 + 可选档案列表。"""
        from agent_core.tool_shelves import admin as _admin

        data = _admin.list_policies(self._tool_shelf_cfg())
        archives = []
        try:
            from agent_core.storage import get_store

            store = get_store()
            for row in store.list_archives() or []:
                archives.append({
                    "archive_id": row.get("archive_id"),
                    "display_name": row.get("display_name"),
                    "role": row.get("role"),
                    "status": row.get("status"),
                })
        except Exception:
            pass
        data["archives"] = archives
        return data

    def _tools_policy_update(self, body):
        from agent_core.tool_shelves import admin as _admin

        return _admin.update_policy(
            str(body.get("archive_id") or ""),
            str(body.get("mode") or "all"),
            [str(x) for x in (body.get("tools") or [])],
            self._tool_shelf_cfg(),
        )

    def _tools_policy_delete(self, body):
        from agent_core.tool_shelves import admin as _admin

        return _admin.delete_policy(str(body.get("archive_id") or ""), self._tool_shelf_cfg())

    def _tools_admin_list(self):
        """工具页：所有已知工具 + 当前设置。"""
        try:
            from agent_core.tool_shelves import admin

            return admin.list_tools(self._tool_shelf_cfg())
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _tools_admin_update(self, body):
        """工具页：改一个工具的模式/提权/类目。"""
        try:
            from agent_core.tool_shelves import admin

            name = str(body.get("name") or "").strip()
            if not name:
                return self._send_json({"ok": False, "error": "name 必填"}, status=400)
            mode = body.get("mode")
            if mode is not None and mode not in ("direct", "filtered"):
                return self._send_json({"ok": False, "error": "mode 只能是 direct/filtered"}, status=400)
            approval = body.get("approval")
            if approval is not None and not isinstance(approval, bool):
                return self._send_json({"ok": False, "error": "approval 必须是布尔值"}, status=400)
            result = admin.update_tool(
                name,
                mode=mode,
                approval=approval,
                category=body.get("category"),
                cfg=self._tool_shelf_cfg(),
            )
            return self._send_json(result)
        except ValueError as exc:
            return self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

    def _logs_payload(self):
        """读日志尾部，给控制台的「运行日志」页。"""
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(self.path).query)
        try:
            tail = max(50, min(2000, int((qs.get("tail") or ["400"])[0] or 400)))
        except ValueError:
            tail = 400
        only_error = (qs.get("filter") or ["all"])[0] == "error"
        include_prev = (qs.get("include_prev") or ["0"])[0] in ("1", "true", "yes")

        log_dir = self._log_dir()
        names = ["hub-18895.out.log", "hub-18895.err.log"]
        if include_prev:
            names += ["hub-18895.out.log.prev", "hub-18895.err.log.prev"]
        files = []
        for name in names:
            path = os.path.join(log_dir, name)
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()[-tail:]
            except Exception as exc:
                files.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            cleaned = [line.rstrip("\r\n") for line in lines]
            if only_error:
                cleaned = [line for line in cleaned if _is_error_line(line)]
            files.append({
                "name": name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "lines": cleaned,
            })
        return {"ok": True, "dir": log_dir, "files": files}

    def _logs_alerts(self):
        """顶部徽章用：最近错误条数 + 最后一条。"""
        log_dir = self._log_dir()
        errors = []
        for name in ("hub-18895.out.log", "hub-18895.err.log"):
            path = os.path.join(log_dir, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()[-500:]
            except Exception:
                continue
            for line in lines:
                text = line.rstrip("\r\n")
                if _is_error_line(text):
                    errors.append({"file": name, "line": text[:300]})
        # 错误数增加时推一条消息（同一批错误不重复推）
        global _LAST_LOG_ERROR_COUNT
        try:
            from agent_core import messages as _msgs

            if len(errors) > _LAST_LOG_ERROR_COUNT:
                last = errors[-1]["line"] if errors else ""
                _msgs.error(f"日志发现 {len(errors)} 条错误：{last[:160]}")
            _LAST_LOG_ERROR_COUNT = len(errors)
        except Exception:
            pass

        return {
            "ok": True,
            "count": len(errors),
            "last": errors[-1] if errors else None,
        }

    def _affection_overview(self):
        """好感度模块：所有档案的当前值 + 浮动窗口。"""
        try:
            from agent_core.profile import ProfileManager
            from agent_core.storage import get_store

            store = get_store()
            pm = ProfileManager(store)
            items = []
            for archive in store.list_archives():
                aid = archive["archive_id"]
                items.append({
                    "archive_id": aid,
                    "display_name": archive.get("display_name") or aid,
                    "role": archive.get("role") or "guest",
                    "status": archive.get("status") or "active",
                    "affection": pm.get_affection(aid),
                    "affection_min": archive.get("affection_min") if archive.get("affection_min") is not None else 0,
                    "affection_max": archive.get("affection_max") if archive.get("affection_max") is not None else 100,
                })
            items.sort(key=lambda x: (x["role"] != "admin", x["display_name"]))
            return {"ok": True, "items": items, "count": len(items)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _identity_scan(self):
        """罗列所有插件里的 UID，供人工配置用。只读，不写库。

        参数：
            roots=...        指定扫描目录（分号分隔）
            plugin=...       只返回这个插件/宿主分组（按插件重扫）
            reset_hidden=1   重扫该插件时，把它下面被隐藏的 UID 恢复显示
        """
        try:
            from agent_core.identity_scan import scan
            from agent_core.storage import get_store

            qs = parse_qs(urlparse(self.path).query)
            roots = (qs.get('roots') or [''])[0].strip()
            root_list = [r.strip() for r in roots.split(';') if r.strip()] if roots else None
            plugin = (qs.get('plugin') or [''])[0].strip()
            reset_hidden = (qs.get('reset_hidden') or [''])[0].strip() in ('1', 'true', 'yes')
            return scan(
                roots=root_list or None,
                store=get_store(),
                plugin=plugin,
                reset_hidden_for_plugin=reset_hidden,
            )
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    def _identity_hide(self, body):
        """隐藏 / 显示 UID（只影响展示，重新扫描可恢复）。"""
        if not self._is_authenticated():
            return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
        uids = body.get('uids') if isinstance(body, dict) else None
        if not isinstance(uids, list) or not uids:
            return self._send_json({'ok': False, 'error': 'uids 必须是非空数组'}, status=400)
        hidden = bool(body.get('hidden', True))
        try:
            from agent_core.identity_scan import set_hidden

            now_hidden = set_hidden(uids, hidden=hidden)
            return self._send_json({
                'ok': True,
                'hidden': sorted(now_hidden),
                'count': len(now_hidden),
            })
        except Exception as exc:
            return self._send_json({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}, status=500)


    def _handle_tool_clean(self, body):
        """P4 薄壳清洗接口：外部薄壳把工具生肉交回来，这里压一遍再回灌。"""
        tool = str(body.get('tool') or '').strip()
        raw = body.get('raw')
        if not tool:
            return self._send_json({'ok': False, 'error': 'tool 必填'}, status=400)
        if raw is None:
            return self._send_json({'ok': False, 'error': 'raw 必填'}, status=400)
        raw = raw if isinstance(raw, str) else _json_text(raw)
        if self._clean_auth_denied():
            return
        config = self._clean_config_for(tool)
        if config is None:
            return self._clean_passthrough(tool, raw)
        return self._clean_with_model(tool, raw, body, config)

    def _clean_auth_denied(self):
        """带 Key 就校验 Key；没带 Key 时按 clients.json 的 require_key 决定。"""
        token = _bearer_token(self.headers)
        if not token:
            if not client_registry.require_key:
                return False
            self._send_json({'ok': False, 'error': 'missing_key'}, status=401)
            return True
        auth = client_registry.resolve(token)
        if not auth.error:
            return False
        self._send_json({'ok': False, 'error': auth.error}, status=401)
        return True

    def _clean_config_for(self, tool):
        """压缩没开、或这个工具在 skip 名单里 → 不压。"""
        if tool_gate is None:
            return None
        try:
            config = tool_gate.cfg.compression()
        except Exception:
            return None
        if not config.get('enabled') or tool in (config.get('skip_tools') or []):
            return None
        return config

    def _clean_passthrough(self, tool, raw):
        """不压就原文回去，并标明 passthrough。"""
        return self._send_json({
            'ok': True, 'text': raw, 'truncated': False,
            'stats': {'tool': tool, 'passthrough': True},
        })

    def _clean_with_model(self, tool, raw, body, config):
        """真调执行 AI 清洗；任何异常都回原文，别让薄壳拿到空结果。"""
        from agent_core.tool_shelves.cleaner import compress_tool_result

        try:
            result = compress_tool_result(
                tool, raw, config,
                args=body.get('args'),
                user_query=str(body.get('user_query') or ''),
                complete_once=self._compression_completer(),
            )
        except Exception as exc:
            print(f"[ToolShelves] 清洗接口异常，原文放行: {exc}", flush=True)
            return self._send_json({
                'ok': True, 'text': raw, 'truncated': False,
                'stats': {'tool': tool, 'error': str(exc)[:120]},
            })
        stats = result.as_dict()
        _record_compression(stats)
        return self._send_json({
            'ok': True,
            'text': result.text,
            'truncated': bool(result.truncated),
            'stats': stats,
        })

    def _tool_shelves_status(self):
        """工具货架：配置视图 + 今日命中率/节省量。"""
        if tool_gate is None:
            return {'ok': False, 'enabled': False, 'error': 'tool gate 未初始化'}
        qs = parse_qs(urlparse(self.path).query)
        if (qs.get('reload') or [''])[0] in ('1', 'true', 'yes'):
            tool_gate.cfg.reload(force=True)
        settings = tool_gate.cfg.get()
        gateway = settings.get('gateway') or {}
        categories = []
        for key, spec in (settings.get('categories') or {}).items():
            if not isinstance(spec, dict):
                continue
            categories.append({
                'key': key,
                'label': spec.get('label') or key,
                'brief': spec.get('brief') or '',
                'tools': spec.get('tools') or [],
                'tool_count': len(spec.get('tools') or []),
            })
        try:
            from agent_core.tool_shelves.metrics import metrics as shelf_metrics
            snapshot = shelf_metrics.snapshot()
        except Exception:
            snapshot = {}
        return {
            'ok': True,
            'enabled': tool_gate.cfg.enabled(),
            'mode': gateway.get('mode') or 'enforce_soft',
            'config_path': str(tool_gate.cfg.path),
            'config_error': tool_gate.cfg.load_error(),
            'always_available': settings.get('always_available') or [],
            'unknown_tool_policy': settings.get('unknown_tool_policy') or 'always',
            'max_tools': gateway.get('max_tools'),
            'sticky_tool_turns': gateway.get('sticky_tool_turns'),
            'on_failure': gateway.get('on_failure'),
            'exempt_clients': gateway.get('exempt_clients') or [],
            'classifier': gateway.get('classifier') or {},
            'protection': settings.get('protection') or {},
            'compression': {
                'enabled': bool((settings.get('compression') or {}).get('enabled')),
                'profiles': list(((settings.get('compression') or {}).get('profiles') or {}).keys()),
                'safe_short_passthrough': True,
            },
            'prompt_hint_enabled': bool((settings.get('prompt_hint') or {}).get('enabled')),
            'last_decision': getattr(tool_gate, 'last_decision', None),
            'categories': categories,
            'metrics': snapshot,
        }

    def do_POST(self):
        path = self.path.split('?')[0]
        if self._access_blocked():
            return
        # 「已下线」前缀先拦，免得白读一遍 body
        if path.startswith(_REMOVED_PATH_PREFIXES):
            return self._send_json({'error': '连接管理已移除'}, status=404)
        raw = self._read_raw_body()
        if self._signature_blocked('POST', path, raw):
            return
        body = self._parse_json_body(raw)
        if body is None:  # 解析失败，_parse_json_body 已经回过 400
            return
        if path.startswith('/v1/') or path.startswith('/api/v1/'):
            return self._handle_gateway_post(path, body)
        if path.startswith('/hub/v1/'):
            return self._post_hub(path, body)
        if path.startswith('/api/'):
            return self._post_console_api(path, body)
        self.send_response(404)
        self.end_headers()

    def _read_raw_body(self):
        """把原始 body 读出来（签名要用原始字节）。"""
        length = int(self.headers.get('Content-Length', 0) or 0)
        return self.rfile.read(length) if length else b''

    def _parse_json_body(self, raw):
        """解析 JSON body；解析失败返回 None（已经回过 400）。"""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception:
            self._send_json({"error": "Invalid JSON"}, status=400)
            return None
        return data if isinstance(data, dict) else {}

    def _access_blocked(self):
        """只允许本机访问的开关；开着且来源不在白名单就 403。"""
        config = api_security.load_config()
        if not config["local_only"]:
            return False
        ip = api_security.client_ip(
            self.client_address[0], self.headers.get('X-Forwarded-For', ''), config
        )
        if api_security.ip_allowed(ip, config):
            return False
        audit_log.record("access_denied", level="warn", ip=ip, error="local_only",
                         path=self.path.split('?')[0])
        self._send_json({'ok': False, 'error': 'local_only', 'client_ip': ip}, status=403)
        return True

    def _signature_blocked(self, method, path, raw):
        """机器对机器接口（/v1、/hub/v1）在开启签名后必须带 X-Timestamp + X-Signature。

        内网来源（本机 / docker / 局域网）默认豁免：AstrBot 这类客户端不用改任何配置。
        """
        if not (path.startswith('/v1/') or path.startswith('/api/v1/') or path.startswith('/hub/v1/')):
            return False
        config = api_security.load_config()
        ip = api_security.client_ip(
            self.client_address[0], self.headers.get('X-Forwarded-For', ''), config
        )
        if api_security.signature_exempt(ip, config):
            return False
        error = api_security.signature_error(
            method,
            self.path,
            self.headers.get('X-Timestamp', ''),
            self.headers.get('X-Signature', ''),
            raw,
        )
        if not error:
            return False
        audit_log.record("signature_rejected", level="warn", ip=ip, error=error,
                         path=path, method=method)
        self._send_json({'ok': False, 'error': error}, status=401)
        return True

    def _require_login(self):
        """未登录就直接回 401；返回 True 表示已经处理完。"""
        if self._is_authenticated():
            return False
        self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
        return True

    # ---------------- POST：三套接口分发 ----------------
    def _handle_gateway_post(self, path, body):
        """/v1 的 POST 只有对话补全一个入口。"""
        if path in ('/v1/chat/completions', '/api/v1/chat/completions'):
            return self._handle_universal_chat_completions(body)
        self.send_response(404)
        self.end_headers()

    def _post_hub(self, path, body):
        """/hub/v1：外部工具用，Key 鉴权，不走 Web 登录态。"""
        if path.startswith('/hub/v1/archives'):
            return self._handle_archive_post(path, body)
        if path == '/hub/v1/knowledge/ingest':
            return self._post_hub_ingest(body)
        if path == '/hub/v1/memory/search':
            return self._post_memory_search(body)
        self.send_response(404)
        self.end_headers()

    def _post_console_api(self, path, body):
        """/api/*：先过登录态（公开路由除外），再查路由表。"""
        if path.startswith('/api/archives'):
            if self._require_login():
                return
            return self._handle_console_archive_post(path, body)
        route = _POST_ROUTES.get(path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        handler, needs_auth = route
        if needs_auth and self._require_login():
            return
        return getattr(self, handler)(body)

    # ---------------- POST：工具货架 ----------------
    def _post_shelves_reload(self, body):
        """热重载工具货架配置（改完 json 不用重启）。"""
        if tool_gate is None:
            return self._send_json({'ok': False, 'error': 'tool gate 未初始化'}, status=500)
        tool_gate.cfg.reload(force=True)
        return self._send_json({
            'ok': True,
            'enabled': tool_gate.cfg.enabled(),
            'config_error': tool_gate.cfg.load_error(),
        })

    def _post_shelves_metrics_reset(self, body):
        """清空工具货架的统计（今日节省比、压缩指标）。"""
        try:
            from agent_core.tool_shelves.metrics import metrics as shelf_metrics

            shelf_metrics.reset()
        except Exception as exc:
            return self._send_json({'ok': False, 'error': str(exc)}, status=500)
        return self._send_json({'ok': True})

    def _post_tool_clean(self, body):
        """P4 薄壳清洗：自带客户端 Key，不要求 Web 登录。"""
        return self._handle_tool_clean(body)

    def _post_tool_update(self, body):
        return self._tools_admin_update(body)

    def _post_tool_policy(self, body):
        return self._send_json(self._tools_policy_update(body))

    def _post_tool_policy_delete(self, body):
        return self._send_json(self._tools_policy_delete(body))

    # ---------------- POST：身份与账本 ----------------
    def _post_identity_bind(self, body):
        return self._identity_bind(body)

    def _post_identity_hide(self, body):
        return self._identity_hide(body)

    def _post_ledger_clear(self, body):
        """清空流水，并把额度池与所有凭证余额一起归零。"""
        try:
            from console.modules.common import get_db_connection
            from console.modules.model_service import reset_credits_and_balances

            conn = get_db_connection()
            count = conn.execute("DELETE FROM token_usage").rowcount
            conn.commit()
            conn.close()
            reset_credits_and_balances()
            return self._send_json({
                "ok": True,
                "cleared": int(count or 0),
                "balances_reset": True,
            })
        except Exception as exc:
            return self._send_json({"ok": False, "error": str(exc)}, status=500)

    # ---------------- POST：访客策略与事实 ----------------
    def _post_access_policy(self, body):
        from agent_core.access_policy import save_policy

        policy = save_policy(body if isinstance(body, dict) else {})
        return self._send_json({"ok": True, "policy": policy})

    def _post_fact_rollback(self, body):
        fact_id = str(body.get('fact_id') or '').strip()
        if not fact_id:
            return self._send_json({'ok': False, 'error': 'fact_id 必填'}, status=400)
        from agent_core.memory.facts import FactStore

        fact = FactStore().rollback(fact_id)
        return self._send_json({'ok': bool(fact), 'fact': fact})

    def _post_fact_upsert(self, body):
        """带 fact_id 是改值，不带就是新增一条。"""
        from agent_core.memory.facts import FactStore

        store = FactStore()
        fact_id = str(body.get('fact_id') or '').strip()
        if fact_id:
            return self._update_fact_value(store, fact_id, str(body.get('value') or '').strip())
        return self._add_fact(store, body)

    def _update_fact_value(self, store, fact_id, value):
        if not value:
            return self._send_json({'ok': False, 'error': 'value 必填'}, status=400)
        fact = store.update_value(fact_id, value)
        return self._send_json({'ok': bool(fact), 'fact': fact})

    def _add_fact(self, store, body):
        subject_id = str(body.get('subject_id') or '').strip()
        predicate = str(body.get('predicate') or '').strip()
        value = str(body.get('value') or '').strip()
        if not subject_id or not predicate or not value:
            return self._send_json(
                {'ok': False, 'error': 'subject_id/predicate/value 必填'}, status=400
            )
        fact = store.add(subject_id, predicate, value)
        return self._send_json({'ok': bool(fact), 'fact': fact})

    # ---------------- POST：登录 / 登出 / 改密 ----------------
    def _post_login(self, body):
        username = str(body.get('username') or '').strip()
        password = body.get('password') or ''
        remember = body.get('remember', True)
        ip = api_security.client_ip(self.client_address[0], self.headers.get('X-Forwarded-For', ''))
        wait = api_security.login_throttle.blocked_for(ip)
        if wait:  # 连续输错就锁一会儿，挡住公网爆破
            audit_log.record("login_blocked", level="warn", ip=ip, retry_after=wait)
            return self._send_json(
                {"success": False, "error": "too_many_attempts", "retry_after": wait},
                status=429,
            )
        if not auth_mgr.verify_credentials(username, password):
            api_security.login_throttle.record_failure(ip)
            audit_log.record("login_failed", level="warn", ip=ip, username=username)
            return self._send_json({"success": False, "error": "用户名或安全密码不正确"}, status=401)
        api_security.login_throttle.record_success(ip)
        audit_log.record("login_ok", dedupe=False, ip=ip, username=username)
        token = auth_mgr.create_session(username, duration_days=7 if remember else 1)
        max_age = 604800 if remember else 86400
        cookie = self._session_cookie(token, max_age)
        return self._send_json_with_cookie(
            {"success": True, "token": token, "username": username}, cookie
        )

    def _post_logout(self, body):
        auth_mgr.revoke_session(self._get_token())
        return self._send_json_with_cookie(
            {"success": True},
            'anan_session=; Path=/; HttpOnly; Expires=Thu, 01 Jan 1970 00:00:00 GMT',
        )

    def _post_change_password(self, body):
        ok, message = auth_mgr.change_password(
            body.get('old_password', ''), body.get('new_password', '')
        )
        return self._send_json({"success": ok, "message": message}, status=200 if ok else 400)

    def _send_json_with_cookie(self, payload, cookie):
        """登录态要顺手写 Set-Cookie，_send_json 不够用。"""
        origin = self._allowed_origin()
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Set-Cookie', cookie)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Credentials', 'true')
        self.end_headers()
        self.wfile.write(data)

    # ---------------- POST：发现与适配器 ----------------
    def _post_connectors_scan(self, body):
        return self._send_json(_get_discovery_service().scan())

    def _post_adapter_analyze(self, body):
        from agent_core.source import SourceAnalyzer

        repo = str(body.get('repo_path') or body.get('path') or '').strip()
        if not repo:
            return self._send_json({'ok': False, 'error': 'repo_path 必填'}, status=400)
        return self._send_json(SourceAnalyzer().analyze(repo))

    def _post_adapter_generate(self, body):
        return self._generate_adapters(body, attach=False)

    def _post_adapter_attach(self, body):
        return self._generate_adapters(body, attach=True)

    def _generate_adapters(self, body, attach):
        """生成适配器；attach 时顺手把识别出来的桥接起来。"""
        import asyncio

        from agent_core.source import SourceSynthesizer

        repo = str(body.get('repo_path') or body.get('path') or '').strip()
        if not repo:
            return self._send_json({'ok': False, 'error': 'repo_path 必填'}, status=400)
        try:
            result = asyncio.run(SourceSynthesizer().generate(repo))
        except Exception as exc:
            return self._send_json({'ok': False, 'error': str(exc)}, status=500)
        if attach and body.get('start'):
            result['started'] = self._start_bridges(result.get('specs') or [])
        return self._send_json(result)

    def _start_bridges(self, specs):
        """按生成结果批量起桥；单条失败不影响其它。"""
        from agent_core.bridge import BridgeSpec

        started = []
        for item in specs:
            if not item.get('ok') or not item.get('spec'):
                continue
            try:
                started.append(_get_bridge_manager().start(BridgeSpec.from_dict(item['spec'])))
            except Exception as exc:
                started.append({'ok': False, 'error': str(exc)})
        return started

    # ---------------- POST：档案绑定 ----------------
    def _post_bindings(self, body):
        platform = str(body.get('platform') or '').strip()
        external_id = str(body.get('external_id') or '').strip()
        archive_id = str(body.get('archive_id') or '').strip()
        if not platform or not external_id or not archive_id:
            return self._send_json({'error': 'platform/external_id/archive_id 必填'}, status=400)
        from agent_core.storage import get_store

        store = get_store()
        if not store.get_archive(archive_id):
            return self._send_json({'error': '档案不存在'}, status=404)
        try:
            confidence = float(body.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        verified_by = str(body.get('verified_by') or 'manual').strip() or 'manual'
        binding_id = store.bind_context(
            platform, external_id, archive_id,
            confidence=max(0.0, min(1.0, confidence)),
            verified_by=verified_by,
        )
        return self._send_json({'ok': True, 'binding_id': binding_id})

    # ---------------- POST：人格 / 模型 / 额度 / 计算 ----------------
    def _post_persona(self, body):
        """新版前端按字段存人设卡；老客户端只传 system_prompt。"""
        if any(key in body for key in ('name', 'persona', 'dialogue_examples', 'other')):
            return self._save_persona_fields(body)
        prompt = body.get('system_prompt', '')
        if not prompt:
            return self._send_json({"error": "Prompt cannot be empty"}, status=400)
        return self._send_json(backend.save_persona(prompt))

    def _save_persona_fields(self, body):
        fields = {
            'name': str(body.get('name') or '').strip(),
            'persona': str(body.get('persona') or '').strip(),
            'dialogue_examples': str(body.get('dialogue_examples') or '').strip(),
            'other': str(body.get('other') or '').strip(),
        }
        if not fields['name']:
            return self._send_json({"error": "AI 名称不能为空"}, status=400)
        if not fields['persona']:
            return self._send_json({"error": "人格设定不能为空"}, status=400)
        return self._send_json(backend.save_persona_profile(fields))

    def _post_persona_rules(self, body):
        return self._send_json(backend.save_persona_rules(body))

    def _post_models_sync(self, body):
        return self._send_json(backend.sync_models_from_upstream())

    def _post_models_config(self, body):
        return self._send_json(backend.save_models_config(body))

    def _post_credits(self, body):
        return self._send_json(backend.save_credits(body))

    def _post_compute_config(self, body):
        return self._send_json(backend.save_compute_config(body))

    def _post_compute_test(self, body):
        return self._send_json(backend.test_compute_provider(body.get('provider_key', '')))

    # ---------------- POST：知识库入库 ----------------
    def _post_memory_search(self, body):
        """深时检索：时间范围 + 关键词 → 时间轴命中，按字数闸门返回。"""
        _, err = self._hub_client(require_admin=True)
        if err:
            return self._hub_error(err)
        from agent_core.memory import deep_recall

        terms = deep_recall.parse_terms(body.get('query') or body.get('keywords') or '')
        if not terms:
            return self._send_json({'ok': False, 'error': '缺少关键词'}, status=400)
        start_ts = deep_recall.parse_time_bound(body.get('start'))
        end_ts = deep_recall.parse_time_bound(body.get('end'))
        try:
            budget = max(500, min(int(body.get('budget') or deep_recall.DEFAULT_BUDGET), 50_000))
        except (TypeError, ValueError):
            budget = deep_recall.DEFAULT_BUDGET
        store = self._memory_store()
        if store is None:
            return self._send_json({'ok': False, 'error': '记忆库不可用'}, status=503)
        person_id = str(body.get('person_id') or '') or None
        common = dict(start_ts=start_ts, end_ts=end_ts,
                      person_ids=[person_id] if person_id else None, limit=500)
        rows = store.search_narratives(terms, **common)
        if not rows and len(terms) > 1:
            rows = store.search_narratives(terms, match_all=False, **common)
        hits = [
            deep_recall.Hit(
                event_id=str(getattr(row, 'event_id', '')),
                time_str=str(getattr(row, 'time_str', '')),
                timestamp=float(getattr(row, 'timestamp', 0) or 0),
                narrative=str(getattr(row, 'narrative', '') or ''),
                source=str(getattr(row, 'source_module', '') or ''),
            )
            for row in rows
        ]
        total = sum(len(hit.narrative) for hit in hits)
        kept, mode = deep_recall.trim_hits(hits, budget)
        return self._send_json({
            'ok': True,
            **deep_recall.summarize(kept, mode, total),
            'query': terms,
            'records': [hit.as_dict() for hit in kept],
        })

    def _post_hub_ingest(self, body):
        _, err = self._hub_client(require_admin=True)
        if err:
            return self._hub_error(err)
        from agent_core.knowledge import KnowledgeBase
        from agent_core.storage import get_store

        try:
            result = KnowledgeBase(get_store()).ingest(
                title=str(body.get('title') or ''),
                content=str(body.get('content') or ''),
                source=str(body.get('source') or ''),
                tags=body.get('tags') if isinstance(body.get('tags'), list) else [],
            )
        except Exception as exc:
            return self._send_json({'ok': False, 'error': str(exc)}, status=400)
        return self._send_json(result, status=201)


    def do_PATCH(self):
        path = self.path.split('?')[0]
        if self._access_blocked():
            return
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        if self._signature_blocked('PATCH', path, raw):
            return
        try:
            body = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            return self._send_json({"error": {"message": "Invalid JSON", "code": "bad_request"}}, status=400)
        if path.startswith('/hub/v1/archives'):
            return self._handle_archive_patch(path, body)
        if path.startswith('/api/archives'):
            return self._handle_console_archive_patch(path, body)
        if path.startswith('/api/affection/'):
            archive_id = path.rstrip('/').rsplit('/', 1)[-1]
            try:
                value = int(body.get('affection'))
            except (TypeError, ValueError):
                return self._send_json({'error': 'affection 必须是整数'}, status=400)
            if not (0 <= value <= 100):
                return self._send_json({'error': 'affection 必须在 0~100 之间'}, status=400)
            try:
                from agent_core.profile import ProfileManager
                from agent_core.storage import get_store

                pm = ProfileManager(get_store())
                if not pm.get_archive(archive_id):
                    return self._send_json({'error': '档案不存在'}, status=404)
                new_value = pm.set_affection(archive_id, value, reason='好感度模块手动设定')
                return self._send_json({'ok': True, 'affection': new_value})
            except Exception as exc:
                return self._send_json({'error': f'{type(exc).__name__}: {exc}'}, status=500)
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        if self._access_blocked():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)
        if path.startswith('/api/'):
            if self._require_login():
                return
            return self._delete_console(path, query)
        # /hub/v1/services 随 agent_core.services 一起下线了
        self.send_response(404)
        self.end_headers()

    def _delete_console(self, path, query):
        """控制台删除类接口：按路径尾巴分派，全部要求登录。"""
        if path == '/api/memory/facts':
            return self._delete_fact(query)
        if path.endswith('/settings') and '/api/archives/' in path:
            return self._delete_archive_setting(path, query)
        if path == '/api/bindings':
            return self._delete_binding(query)
        if path == '/api/bridges/spec':
            return self._delete_bridge_spec(query)
        if path.startswith('/api/plugins/'):
            return self._delete_plugin(path)
        if path.startswith('/api/knowledge/'):
            return self._delete_knowledge(path)
        if path.startswith('/api/components/'):
            return self._delete_component(path)
        if path.startswith('/api/mcp/'):
            return self._send_json(_get_mcp_manager().close(path.rsplit('/', 1)[-1]))
        self.send_response(404)
        self.end_headers()

    def _delete_fact(self, query):
        fact_id = (query.get('fact_id') or [''])[0].strip()
        if not fact_id:
            return self._send_json({'error': 'fact_id 必填'}, status=400)
        from agent_core.memory.facts import FactStore

        removed = FactStore().delete(fact_id)
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_archive_setting(self, path, query):
        setting_id = (query.get('setting_id') or [''])[0].strip()
        if not setting_id:
            return self._send_json({'error': 'setting_id 必填'}, status=400)
        archive_id = path.rsplit('/', 2)[-2]
        from agent_core.profile import ProfileManager
        from agent_core.storage import get_store

        removed = ProfileManager(get_store()).delete_setting(archive_id, setting_id)
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_binding(self, query):
        platform = (query.get('platform') or [''])[0].strip()
        external_id = (query.get('external_id') or [''])[0].strip()
        if not platform or not external_id:
            return self._send_json({'error': 'platform/external_id 必填'}, status=400)
        from agent_core.storage import get_store

        removed = get_store().unbind_context(platform, external_id)
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_bridge_spec(self, query):
        """删桥接规格前先停掉在跑的实例，停不掉也照删。"""
        source_id = (query.get('source_id') or [''])[0].strip()
        if not source_id:
            return self._send_json({'error': 'source_id 必填'}, status=400)
        from agent_core.storage import get_store

        try:
            _get_bridge_manager().stop(source_id)
        except Exception:
            pass
        removed = get_store().delete_bridge_spec(source_id)
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_plugin(self, path):
        from agent_core.plugins import PluginRegistry
        from agent_core.storage import get_store

        removed = PluginRegistry(get_store()).delete(path.rsplit('/', 1)[-1])
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_knowledge(self, path):
        from agent_core.knowledge import KnowledgeBase
        from agent_core.storage import get_store

        removed = KnowledgeBase(get_store()).delete(path.rsplit('/', 1)[-1])
        return self._send_json({'ok': True, 'removed': removed})

    def _delete_component(self, path):
        from agent_core.storage import get_store

        removed = get_store().delete_component(path.rsplit('/', 1)[-1])
        return self._send_json({'ok': True, 'removed': removed})

    # ------------------------------------------------------------------
    # 档案 API（/hub/v1/archives）
    # ------------------------------------------------------------------
    def _hub_client(self, require_admin=False):
        auth_header = self.headers.get('Authorization', '')
        token = auth_header[7:].strip() if auth_header.lower().startswith('bearer ') else (
            self.headers.get('X-Hub-Key') or ''
        )
        result = client_registry.resolve(token)
        if result.error:
            return None, result.error
        if require_admin and result.profile.default_actor != 'master':
            return None, 'forbidden'
        return result.profile, None

    def _hub_error(self, code, status=None):
        mapping = {
            'missing_key': ('缺少 API Key', 401),
            'invalid_key': ('API Key 无效或已撤销', 401),
            'forbidden': ('该操作需要管理员档案的 Key', 403),
            'not_found': ('档案不存在', 404),
            'conflict': ('该平台账号已绑定到其他档案', 409),
            'bad_request': ('参数不完整或格式错误', 400),
        }
        message, default_status = mapping.get(code, ('请求失败', 400))
        self._send_json({
            'error': {'message': message, 'type': 'invalid_request_error', 'code': code}
        }, status=status or default_status)

    def _archive_manager(self):
        from agent_core.profile import ArchiveManager
        from agent_core.storage import get_store
        return ArchiveManager(get_store())

    def _archive_view(self, am, archive):
        data = dict(archive)
        # 档案是人物画像，不承载 AI 人设；旧字段不再对外暴露
        data.pop('persona_id', None)
        data.pop('persona_sections', None)
        archive_id = archive['archive_id']
        identities = list(am.identities(archive_id))
        seen = {
            (str(i.get('platform') or ''), str(i.get('platform_user_id') or ''))
            for i in identities
        }
        # 模块里做的身份映射也显示在人物档案里；这里只读，不在档案页改
        try:
            for binding in am.store.list_bindings():
                if str(binding.get('archive_id') or '') != archive_id:
                    continue
                platform = str(binding.get('platform') or '')
                external_id = str(binding.get('external_id') or '')
                if not platform or not external_id:
                    continue
                if 'GroupMessage' in external_id or 'qq_group' in external_id:
                    continue
                uid = external_id.rsplit(':', 1)[-1] if ':' in external_id else external_id
                if not uid.isdigit():
                    continue
                key = (platform, uid)
                if key in seen:
                    continue
                seen.add(key)
                identities.append({
                    'platform': platform,
                    'platform_user_id': uid,
                    'display_name': '',
                    'source': 'module',
                })
        except Exception:
            pass
        data['identities'] = identities
        try:
            from agent_core.profile import ProfileManager
            pm = ProfileManager(am.store)
            data['affection'] = pm.get_affection(archive_id)
            data['facts'] = pm.get_active_facts(archive_id, limit=30)
            data['profile'] = pm.get_profile(archive_id) or {}
            data['settings'] = pm.list_settings(archive_id)
        except Exception:
            data.setdefault('affection', None)
            data.setdefault('facts', [])
            data.setdefault('profile', {})
            data.setdefault('settings', [])
        return data

    def _handle_archive_get(self, console=False):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        qs = parse_qs(parsed.query)
        am = self._archive_manager()

        if path == '/hub/v1/archives':
            if not console:
                _, err = self._hub_client(require_admin=True)
                if err:
                    return self._hub_error(err)
            role = (qs.get('role') or [None])[0]
            data = [self._archive_view(am, a) for a in am.list_archives(role=role)]
            return self._send_json({'object': 'list', 'data': data})

        if path == '/hub/v1/archives/resolve':
            if not console:
                _, err = self._hub_client(require_admin=False)
                if err:
                    return self._hub_error(err)
            platform = (qs.get('platform') or [''])[0].strip()
            uid = (qs.get('user_id') or qs.get('platform_user_id') or [''])[0].strip()
            if not platform or not uid:
                return self._hub_error('bad_request')
            archive = am.resolve(platform, uid, create=False)
            if not archive:
                return self._hub_error('not_found')
            return self._send_json(self._archive_view(am, archive))

        if path.startswith('/hub/v1/archives/'):
            if not console:
                _, err = self._hub_client(require_admin=True)
                if err:
                    return self._hub_error(err)
            archive = am.get(path.rsplit('/', 1)[-1])
            if not archive:
                return self._hub_error('not_found')
            return self._send_json(self._archive_view(am, archive))

        return self._hub_error('not_found')

    def _handle_archive_post(self, path, body, console=False):
        if not console:
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
        manager = self._archive_manager()
        path = path.rstrip('/')
        if path == '/hub/v1/archives':
            return self._create_archive(manager, body)
        if path.endswith('/settings'):
            return self._save_archive_settings(manager, path, body)
        if path.endswith('/identities'):
            return self._link_archive_identity(manager, path, body)
        return self._hub_error('not_found')

    def _create_archive(self, manager, body):
        """建档案：给了平台账号就顺手绑身份，没给就只建人。"""
        platform = str(body.get('platform') or '').strip()
        uid = str(body.get('user_id') or body.get('platform_user_id') or '').strip()
        role = str(body.get('role') or 'guest').strip().lower()
        if role not in ('admin', 'guest'):
            role = 'guest'
        if not platform and not uid:
            return self._create_person_archive(manager, body, role)
        if not platform or not uid:
            return self._hub_error('bad_request')
        existing = manager.resolve(platform, uid, create=False)
        if existing:
            return self._send_json({
                'error': {'message': '该平台账号已有档案', 'code': 'conflict'},
                'archive': self._archive_view(manager, existing),
            }, status=409)
        archive = manager.create_user(
            platform, uid, display_name=body.get('display_name'), role=role
        )
        return self._send_json(self._archive_view(manager, archive), status=201)

    def _create_person_archive(self, manager, body, role):
        """没有平台账号的档案（先建人，之后再绑身份）。"""
        display_name = str(body.get('display_name') or '').strip()
        if not display_name:
            return self._hub_error('bad_request')
        archive = manager.create_person(display_name, role=role)
        return self._send_json(self._archive_view(manager, archive), status=201)

    def _save_archive_settings(self, manager, path, body):
        """档案自定义设定：整段保存或单条保存。"""
        archive_id = path.rsplit('/', 2)[-2]
        if not manager.get(archive_id):
            return self._hub_error('not_found')
        from agent_core.profile import ProfileManager

        profile_manager = ProfileManager(manager.store)
        if 'text' in body:
            return self._save_settings_text(profile_manager, archive_id, body)
        return self._save_one_setting(profile_manager, archive_id, body)

    def _save_settings_text(self, profile_manager, archive_id, body):
        """大文本框整段保存：每行「标签：内容」，没有冒号就整行当内容。"""
        items = [_split_setting_line(line) for line in str(body.get('text') or '').splitlines()]
        items = [item for item in items if item]
        for old in profile_manager.list_settings(archive_id):
            profile_manager.delete_setting(archive_id, old['setting_id'])
        saved = [
            profile_manager.upsert_setting(archive_id, label, content) for label, content in items
        ]
        return self._send_json({'ok': True, 'settings': saved, 'count': len(saved)})

    def _save_one_setting(self, profile_manager, archive_id, body):
        label = str(body.get('label') or '').strip()
        content = str(body.get('content') or '').strip()
        if not label:
            return self._send_json(
                {'error': {'message': 'label 必填', 'code': 'bad_request'}}, status=400
            )
        try:
            setting = profile_manager.upsert_setting(
                archive_id, label, content,
                setting_id=str(body.get('setting_id') or '').strip(),
                sort_order=body.get('sort_order'),
            )
        except ValueError as exc:
            return self._send_json(
                {'error': {'message': str(exc), 'code': 'bad_request'}}, status=400
            )
        return self._send_json({'ok': True, 'setting': setting})

    def _link_archive_identity(self, manager, path, body):
        """给已有档案挂一个平台身份；冲突就拒绝，不硬改绑。"""
        parts = path.split('/')
        archive_id = parts[-2] if len(parts) >= 2 else ''
        if not manager.get(archive_id):
            return self._hub_error('not_found')
        platform = str(body.get('platform') or '').strip()
        uid = str(body.get('user_id') or body.get('platform_user_id') or '').strip()
        if not platform or not uid:
            return self._hub_error('bad_request')
        existing = manager.resolve(platform, uid, create=False)
        if existing and existing['archive_id'] != archive_id:
            return self._hub_error('conflict')
        manager.link(
            archive_id, platform, uid,
            display_name=body.get('display_name'),
            is_primary=bool(body.get('is_primary')),
        )
        return self._send_json(self._archive_view(manager, manager.get(archive_id)))

    def _handle_archive_patch(self, path, body, console=False):
        if not console:
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
        archive_id = path.rstrip('/').rsplit('/', 1)[-1]
        am = self._archive_manager()
        if not am.get(archive_id):
            return self._hub_error('not_found')
        status = body.get('status')
        if status is not None and status not in ('active', 'disabled'):
            return self._hub_error('bad_request')

        # 好感度浮动窗口
        min_v = body.get('affection_min')
        max_v = body.get('affection_max')
        if min_v is not None or max_v is not None:
            try:
                min_v = int(min_v) if min_v is not None else None
                max_v = int(max_v) if max_v is not None else None
            except (TypeError, ValueError):
                return self._send_json(
                    {'error': {'message': 'affection_min/max 必须是整数', 'code': 'bad_request'}},
                    status=400,
                )
            for label, val in (('affection_min', min_v), ('affection_max', max_v)):
                if val is not None and not (0 <= val <= 100):
                    return self._send_json(
                        {'error': {'message': f'{label} 必须在 0~100 之间', 'code': 'bad_request'}},
                        status=400,
                    )
            cur = am.get(archive_id) or {}
            lo = min_v if min_v is not None else int(cur.get('affection_min') if cur.get('affection_min') is not None else 0)
            hi = max_v if max_v is not None else int(cur.get('affection_max') if cur.get('affection_max') is not None else 100)
            if lo > hi:
                return self._send_json(
                    {'error': {'message': 'affection_min 不能大于 affection_max', 'code': 'bad_request'}},
                    status=400,
                )

        updated = am.update(
            archive_id,
            display_name=body.get('display_name'),
            status=status,
            affection_min=min_v,
            affection_max=max_v,
        )
        # 窗口收窄后，把当前好感度夹回新窗口
        if min_v is not None or max_v is not None:
            try:
                from agent_core.profile import ProfileManager

                ProfileManager(am.store).update_affection(
                    archive_id, 0, reason="调整好感度浮动范围"
                )
            except Exception:
                pass
        return self._send_json(self._archive_view(am, updated))

    # ---- 控制台同源档案接口（不带客户端 Key） ----
    def _handle_console_archive_get(self):
        original = self.path
        self.path = self.path.replace('/api/archives', '/hub/v1/archives', 1)
        try:
            return self._handle_archive_get(console=True)
        finally:
            self.path = original

    def _handle_console_archive_post(self, path, body):
        return self._handle_archive_post(
            path.replace('/api/archives', '/hub/v1/archives', 1), body, console=True
        )

    def _handle_console_archive_patch(self, path, body):
        return self._handle_archive_patch(
            path.replace('/api/archives', '/hub/v1/archives', 1), body, console=True
        )

    # ==================================================================
    # OpenAI 兼容网关 v2：参数透传 / tools / 真流式
    # ==================================================================
    PASSTHROUGH_FIELDS = (
        "tools", "tool_choice", "temperature", "top_p", "max_tokens", "stop",
        "presence_penalty", "frequency_penalty", "response_format", "seed",
        "parallel_tool_calls",
    )

    def _bound_archive_id(self, body, sender_id: str = ""):
        """按发送者 UID 找人设档案：群聊也落到 sender，而不是群号"""
        from agent_core.identity_binding import infer_platform, resolve_archive_for_sender
        from agent_core.storage import get_store

        external_id = self.headers.get('X-External-Id') or str(body.get('external_id') or '')
        channel_id = self.headers.get('X-Channel') or ""
        platform = (
            self.headers.get('X-Platform')
            or str(body.get('platform') or '')
            or infer_platform(channel_id, external_id)
        )
        sender = (
            str(sender_id or '').strip()
            or self.headers.get('X-User-Id')
            or self.headers.get('X-Actor-Id')
            or str(body.get('user') or '')
        ).strip()
        try:
            return resolve_archive_for_sender(
                get_store(), platform, sender, external_id
            )
        except Exception:
            return None

    def _hub_identity_payload(self, ctx):
        return {
            "actor_id": ctx.get("actor_id") or "",
            "actor_name": ctx.get("actor_name") or "",
            "archive_id": ctx.get("archive_id") or "",
            "platform": ctx.get("platform") or "",
            "sender_uid": ctx.get("sender_uid") or "",
            "affection": ctx.get("affection") or 0,
            "bound_by": ctx.get("bound_by") or "default",
        }

    def _hub_identity_headers(self, ctx):
        from urllib.parse import quote
        payload = self._hub_identity_payload(ctx)
        return {
            "X-Hub-Actor-Id": payload["actor_id"],
            "X-Hub-Actor-Name": quote(payload["actor_name"]),
            "X-Hub-Archive-Id": payload["archive_id"],
            "X-Hub-Platform": payload["platform"],
            "X-Hub-Sender-Uid": payload["sender_uid"],
            "X-Hub-Affection": str(payload["affection"]),
            "X-Hub-Bound-By": payload["bound_by"],
            "Access-Control-Expose-Headers": (
                "X-Hub-Actor-Id, X-Hub-Actor-Name, X-Hub-Archive-Id, "
                "X-Hub-Platform, X-Hub-Sender-Uid, X-Hub-Affection, X-Hub-Bound-By"
            ),
        }

    def _parse_gateway_request(self, body):
        """请求体最外层几个字段，缺省值在这里统一兜住。"""
        return {
            "messages": body.get("messages") or [],
            "model": body.get("model") or "gemini-3.8-flash",
            "stream": bool(body.get("stream", False)),
            "trace_id": f"tr_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
        }

    def _authenticate_gateway_client(self):
        """认证 + 限流；失败时返回 (None, 错误码)。"""
        auth_header = self.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        else:
            token = self.headers.get("X-Hub-Key") or ""
        auth = client_registry.resolve(token)
        if auth.error:
            return None, auth.error
        profile = auth.profile
        if not client_registry.allow_request(profile.id, profile.rate_limit_per_minute):
            return None, "rate_limited"
        return profile, None

    def _resolve_channel_ids(self, profile):
        channel_id = self.headers.get("X-Channel") or profile.channel or "universal_proxy"
        channel_type = str(self.headers.get("X-Channel-Type") or "").strip().lower()
        return channel_id, channel_type

    def _extract_user_query(self, messages):
        """取最后一条 user 消息的纯文本；图片只留一句短语，不碰二进制。"""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return _scrub_binary(content)
            if isinstance(content, list):
                return _summarize_parts(content)
            return ""
        return ""

    def _extract_raw_user_id(self, body, user_query):
        """优先 body/header 里的显式 ID；没有就从正文的 "(12345):" 前缀里嗅探。"""
        raw = str(
            body.get("user")
            or self.headers.get("X-User-Id")
            or self.headers.get("X-Actor-Id")
            or ""
        ).strip()
        if raw or not user_query:
            return raw
        sniffed = re.search(r"\((\d{5,12})\):", user_query)
        return sniffed.group(1) if sniffed else ""

    def _strip_query_prefix(self, user_query):
        """去掉平台注入的 "[时间]" 和 "昵称(12345):" 前缀。"""
        if not user_query:
            return user_query
        cleaned = re.sub(r"^\[.*?\]\s*", "", user_query)
        cleaned = re.sub(r"^.*?\(\d{5,12}\):\s*", "", cleaned).strip()
        return cleaned or user_query

    def _resolve_platform_name(self, body, channel_id):
        from agent_core.identity_binding import infer_platform
        return (
            self.headers.get("X-Platform")
            or str(body.get("platform") or "")
            or infer_platform(channel_id, self.headers.get("X-External-Id") or "")
            or "web"
        )

    def _resolve_gateway_actor(self, profile, platform_name, raw_user_id, display_name):
        """访客客户端不许冒充主人；其余交给 IdentityResolver。"""
        if profile.default_actor == "guest":
            return f"guest_{profile.id}", profile.name, "guest"
        from agent_core.identity import IdentityResolver
        return IdentityResolver.resolve_actor(
            platform_name, raw_user_id or profile.default_actor or "master", display_name
        )

    def _resolve_channel_perms(self, profile, channel_id):
        """客户端自己声明了记忆开关就覆盖模块级默认。"""
        perms = backend.get_module_permissions().get(channel_id, {
            "allow_recall_memory": True,
            "allow_ingest_timeline": True,
        })
        if profile.id == "legacy":
            return perms
        perms = dict(perms)
        perms["allow_recall_memory"] = profile.allow_recall_memory
        perms["allow_ingest_timeline"] = profile.allow_ingest_timeline
        return perms

    def _resolve_archive_identity(self, body, actor_id, platform_name, raw_user_id, profile, perms, access_policy):
        """解析档案 / 访客身份 / 好感度 / 主人事实。

        访客开关关闭时返回 {"error": "guest_disabled"}，由调用方直接拒绝。
        """
        from agent_core.identity import IdentityResolver
        from agent_core.timeline_hub import TimelineHub

        bound_archive = self._bound_archive_id(body, raw_user_id)
        master_qq = str(IdentityResolver.get_master_qq() or "").strip()
        person_id = bound_archive or (master_qq if str(actor_id) in ("master", master_qq) else actor_id)
        result = {
            "actor_id": actor_id, "actor_name": profile.name, "role_type": "guest" if actor_id.startswith("guest_") else "user",
            "perms": perms, "person_id": person_id, "archive_id": None,
            "is_guest": False, "bound_archive": bound_archive,
            "affection": None, "master_facts": [],
        }
        try:
            th = TimelineHub()
            try:
                archive = th.profile.archives.resolve_actor(actor_id, platform_name)
                result["archive_id"] = archive["archive_id"] if archive else None
            except Exception:
                result["archive_id"] = None
            # 白名单制度：没有档案的人按访客处理；访客开关默认关闭
            is_guest = not bool(bound_archive or result["archive_id"])
            if is_guest:
                if not access_policy.get("guest_enabled", False):
                    return {"error": "guest_disabled"}
                return self._build_guest_identity(result, raw_user_id, platform_name, profile, access_policy)
            return self._build_member_identity(result, th, actor_id, master_qq, bound_archive)
        except Exception:
            return result

    def _build_guest_identity(self, result, raw_user_id, platform_name, profile, access_policy):
        """访客：不能召回记忆，时间轴按开关决定。"""
        safe_uid = re.sub(r"[^0-9A-Za-z_\-]", "_", raw_user_id or profile.id)[:48]
        actor_id = f"guest_{platform_name}_{safe_uid}"
        perms = dict(result["perms"])
        perms["allow_recall_memory"] = False
        perms["allow_ingest_timeline"] = bool(access_policy.get("guest_timeline_enabled", False))
        result.update({
            "actor_id": actor_id,
            "role_type": "guest",
            "person_id": actor_id,
            "is_guest": True,
            "perms": perms,
            "affection": int(access_policy.get("guest_default_affection", 50)),
        })
        return result

    def _build_member_identity(self, result, timeline_hub, actor_id, master_qq, bound_archive):
        """有档案的人：读好感度；主人再额外读一次长期事实。"""
        person_id = bound_archive or result["archive_id"] or (
            master_qq if str(actor_id) in ("master", master_qq) else actor_id
        )
        result["person_id"] = person_id
        result["affection"] = timeline_hub.get_user_affection(person_id)
        if str(actor_id) in ("master", master_qq):
            profile_row = timeline_hub.get_user_profile(person_id)
            if profile_row:
                result["master_facts"] = profile_row.get("facts", [])
        return result

    def _collect_recall_memories(self, timeline_hub, user_query, actor_id, person_id, channel_id, channel_type):
        """时间轴整段注入：按记忆曲线压缩，不再切 8 条。"""
        from agent_core.memory import timeline_curve

        events = self._timeline_events(timeline_hub, actor_id, person_id, channel_id, channel_type)
        entries = timeline_curve.build_curve(
            events, budget_chars=timeline_curve.budget_chars(), line_overhead=22,
        )
        by_id = {getattr(event, "event_id", ""): event for event in events}
        lines = []
        for item in entries:
            event = by_id.get(item.event_id)
            when = getattr(event, "time_str", "") if event else ""
            lines.append(f"[{when}] {item.text}")
        return lines

    def _timeline_events(self, timeline_hub, actor_id, person_id, channel_id, channel_type):
        """取这个人的时间轴事件（按人收敛，群聊不串到别人）。"""
        group_chat = channel_type == "group" or "group" in channel_id.lower()
        return timeline_hub.store.list_candidates(
            person_ids=[person_id] if person_id else None,
            actor_ids=[actor_id] if actor_id else None,
            exclude_group=(not group_chat and str(actor_id) != "master"),
            limit=5000,
        )

    def _gateway_context(self, body):
        request = self._parse_gateway_request(body)
        session = self._gateway_session(body, request)
        if session.get("error"):
            return {"error": session["error"]}
        memory = self._gateway_memory(session)
        compute = self._gateway_compute(session["user_query"])
        tools = self._gateway_tools(body, request, session)
        return self._gateway_ctx_build(body, request, session, memory, compute, tools)

    def _gateway_session(self, body, request):
        """鉴权 + 通道 + 身份：这一段决定"是谁在说话"。"""
        profile, auth_error = self._authenticate_gateway_client()
        if auth_error:
            return {"error": auth_error}
        channel_id, channel_type = self._resolve_channel_ids(profile)
        self._maybe_auto_provision()  # 陌生模块首次接入时后台登记一次
        ids = self._gateway_ids(body, request["messages"], channel_id)
        actor = self._gateway_actor(body, profile, ids, channel_id)
        if actor.get("error"):
            return actor
        actor.update({
            **ids,
            "profile": profile,
            "client_id": profile.id,
            "channel_id": channel_id,
            "channel_type": channel_type,
            **self._conversation_context(),
        })
        return actor

    def _conversation_context(self):
        """群聊/私聊的会话标识：优先显式头，其次 AstrBot 自带的会话头。"""
        group_id = _header_text(self.headers.get("X-Group-Id"))
        session_id = _header_text(self.headers.get("X-Session-Id"))
        raw = session_id or _header_text(self.headers.get("x-astrbot-conversation-id"))
        kind, parsed_group, parsed_session = _split_conversation_id(raw)
        return {
            "group_id": group_id or parsed_group,
            "session_id": session_id or parsed_session,
            "conversation_type": kind,
        }

    def _gateway_ids(self, body, messages, channel_id):
        """用户原话、平台 UID、平台名：后面到处都要用的三个值。"""
        user_query = self._strip_query_prefix(self._extract_user_query(messages))
        raw_user_id = self._extract_raw_user_id(body, user_query)
        platform_name = self._resolve_platform_name(body, channel_id)
        return {"user_query": user_query, "raw_user_id": raw_user_id, "platform": platform_name}

    def _gateway_actor(self, body, profile, ids, channel_id):
        """把平台身份换成中枢身份（actor / person / archive）。"""
        display_name = _header_text(self.headers.get("X-User-Name"))
        actor_id, _, _ = self._resolve_gateway_actor(
            profile, ids["platform"], ids["raw_user_id"], display_name
        )
        perms = self._resolve_channel_perms(profile, channel_id)
        from agent_core.access_policy import load_policy

        identity = self._resolve_archive_identity(
            body, actor_id, ids["platform"], ids["raw_user_id"], profile, perms, load_policy()
        )
        if identity.get("error"):
            return identity
        return self._identity_fields(identity)

    def _identity_fields(self, identity):
        """身份解析结果 → 统一字段；档案绑定在这里就生效。"""
        archive_id, bound_by = self._gateway_binding(identity)
        return {
            "actor_id": identity["actor_id"],
            "actor_name": identity["actor_name"],
            "role_type": identity["role_type"],
            "perms": identity["perms"],
            "person_id": identity["person_id"],
            "archive_id": archive_id,
            "is_guest": identity["is_guest"],
            "bound_archive": identity["bound_archive"],
            "affection": identity["affection"],
            "master_facts": identity["master_facts"],
            "bound_by": bound_by,
        }

    def _gateway_binding(self, identity):
        """档案绑定优先；没绑定时按 master / actor / 默认 归类。"""
        archive_id = identity["archive_id"]
        if identity["bound_archive"]:
            return identity["bound_archive"], "binding"
        if identity["actor_id"] == "master":
            return archive_id, "master"
        if archive_id:
            return archive_id, "actor"
        return archive_id, "default"

    def _gateway_memory(self, session):
        """召回 + 事实 + 系统提示词：这一段决定"它记得什么"。"""
        recalled = self._gateway_recall(session)
        master_facts, relations = self._gateway_facts(session)
        sys_prompt = self._gateway_system_prompt(session, master_facts, recalled, relations)
        return {
            "recalled": recalled,
            "master_facts": master_facts,
            "relations": relations,
            "sys_prompt": sys_prompt,
        }

    def _gateway_recall(self, session):
        """长期记忆召回；访客、没权限、查询太短都不查。"""
        if session["is_guest"] or not session["perms"].get("allow_recall_memory", True):
            return []
        if len(session["user_query"]) < 2:
            return []
        try:
            from agent_core.timeline_hub import TimelineHub

            return self._collect_recall_memories(
                TimelineHub(),
                session["user_query"],
                session["actor_id"],
                session["person_id"],
                session["channel_id"],
                session["channel_type"],
            )
        except Exception:
            return []

    def _gateway_facts(self, session):
        """事实层优先：确定的事实不靠语义碰运气，关系图再补一跳。"""
        master_facts = list(session["master_facts"] or [])
        if session["is_guest"]:
            return master_facts, []
        try:
            from agent_core.memory.facts import FactStore

            fact_store = FactStore()
            fact_lines = fact_store.prompt_lines(
                session["person_id"], session["user_query"], limit=8
            )
            if fact_lines:
                master_facts = master_facts + list(fact_lines)
            graph = fact_store.graph(session["person_id"])
            return master_facts, self._relation_lines(graph, session["actor_name"])
        except Exception:
            return master_facts, []

    def _relation_lines(self, graph, actor_name):
        """关系图 → 一行一条，进系统提示词当背景。"""
        lines = []
        for edge in graph.get("edges") or []:
            lines.append(
                f"{actor_name} --{edge.get('predicate') or '关系'}--> "
                f"{edge.get('object_value') or ''}"
            )
        return lines

    def _gateway_system_prompt(self, session, master_facts, recalled, relations):
        """人设提示词；人设卡组装不出来就退到控制台配置的老提示词。"""
        prompt = self._persona_system_prompt(session, master_facts, recalled, relations)
        return prompt or backend.get_current_persona().get("system_prompt", "")

    def _persona_system_prompt(self, session, master_facts, recalled, relations):
        """AI 人格只走「人格设定」的全局人设卡；档案不再承载人设。"""
        try:
            from agent_core.persona import PersonaManager

            persona = PersonaManager()
            if not persona.get_base_prompt():
                return ""
            return persona.build_system_context(
                actor_id=session["actor_id"],
                actor_name=session["actor_name"],
                role_type=session["role_type"],
                affection=session["affection"],
                master_facts=master_facts,
                recalled_memories=recalled,
                relations=relations,
            )
        except Exception:
            return ""

    def _gateway_compute(self, user_query):
        """模型路由：判断是不是逻辑重活，解析人格槽位的实际模型。"""
        try:
            from agent_core.compute_providers import compute_manager
        except Exception:
            return {"compute_mgr": None, "is_logic_task": False, "actual_model": ""}
        state = {"compute_mgr": compute_manager, "is_logic_task": False, "actual_model": ""}
        try:
            state["is_logic_task"] = compute_manager.is_logic_heavy_task(user_query)
            state["actual_model"] = compute_manager.resolve_model("persona_slot")
        except Exception:
            pass
        return state

    def _gateway_tools(self, body, request, session):
        """透传字段 + 工具闸门 + 工具结果压缩。"""
        extra = {k: body[k] for k in self.PASSTHROUGH_FIELDS if body.get(k) is not None}
        decision, extra = self._gateway_tool_gate(request["messages"], extra, session)
        messages, compression = self._gateway_compress(request["messages"], session["user_query"])
        return {
            "extra": extra,
            "messages": messages,
            "tools_all": list(body.get("tools") or []),
            "decision": decision,
            "compression": compression,
        }

    def _gateway_tool_gate(self, messages, extra, session):
        """工具闸门：权限硬拒绝 → 分类裁剪；任何异常都全量透传。"""
        if tool_gate is None or not extra.get("tools"):
            return None, extra
        try:
            extra, denied = self._gateway_tool_hard_deny(extra, session)
            decision = self._gateway_tool_decide(messages, extra, session)
            extra = self._gateway_tool_apply(decision, extra, denied, messages)
            return decision, extra
        except Exception as exc:
            print(f"[ToolShelves] 闸门跳过，全量透传: {exc}", flush=True)
            return None, extra

    def _gateway_tool_hard_deny(self, extra, session):
        """权限硬拒绝：先按客户端把宿主机等敏感工具摘掉，再走分类裁剪。"""
        client_perms = getattr(session["profile"], "tool_permissions", None)
        denied = tool_gate.client_denied(extra["tools"], session["client_id"], client_perms)
        if not denied:
            return extra, denied
        extra["tools"] = [t for t in extra["tools"] if _tool_name_of(t) not in denied]
        if not extra["tools"]:
            for key in ("tools", "tool_choice", "parallel_tool_calls"):
                extra.pop(key, None)
        return extra, denied

    def _gateway_tool_decide(self, messages, extra, session):
        """问闸门要一份裁剪决策；档案级策略用本轮解析出的档案 ID。"""
        return tool_gate.decide(
            messages,
            extra.get("tools"),
            channel_id=session["channel_id"],
            actor_id=session["actor_id"],
            client_id=session["client_id"],
            platform=session["platform"],
            channel_type=session["channel_type"],
            client_perms=getattr(session["profile"], "tool_permissions", None),
            archive_id=str(session["archive_id"] or ""),
        )

    def _gateway_tool_apply(self, decision, extra, denied, messages):
        """把决策落到 extra 上；裁剪过就顺手剥掉 tool_choice 类字段。"""
        if denied and not decision.applied:
            decision.applied = True
            decision.payload_tools = list(extra.get("tools") or [])
            decision.tools_after = len(decision.payload_tools)
            decision.drop_tool_fields = decision.tools_after == 0
        out = tool_gate.apply(decision, extra)
        if decision.applied and decision.tools_after < decision.tools_before:
            # 裁剪后某些客户端仍带 tool_choice:"auto"，上游可能 400，统一剥掉
            out = tool_gate.strip_choice_fields(out)
        tool_gate.remember(decision.session_key, messages)
        return out

    def _gateway_compress(self, messages, user_query):
        """P3 结果压缩：工具返回的生肉在送进主力 AI 前先由执行 AI 清洗。"""
        cfg = self._compression_cfg(messages)
        if not cfg:
            return messages, None
        cleaned, records = self._compress_tool_results(messages, cfg, user_query)
        if cleaned is None:
            return messages, None
        return cleaned, (self._compression_summary(records) if records else None)

    def _compression_cfg(self, messages):
        """要不要压缩：没有工具结果、或压缩没开，都返回 None。"""
        if tool_gate is None or not any(_is_tool_result(m) for m in messages):
            return None
        try:
            cfg = tool_gate.cfg.compression()
        except Exception:
            return None
        return cfg if cfg.get("enabled") else None

    def _compress_tool_results(self, messages, cfg, user_query):
        """调执行 AI 清洗工具结果；失败返回 (None, None) 表示原文透传。"""
        try:
            from agent_core.tool_shelves.cleaner import compress_messages
            from agent_core.tool_shelves.metrics import metrics as shelf_metrics

            cleaned, records = compress_messages(
                messages,
                cfg,
                user_query=user_query,
                complete_once=self._compression_completer(),
            )
            if records:
                shelf_metrics.record_compression(records)
            return cleaned, records
        except Exception as exc:
            print(f"[ToolShelves] 结果压缩跳过，原文透传: {exc}", flush=True)
            return None, None

    def _compression_completer(self):
        """清洗用的执行 AI 调用器：槽位 / 超时 / 温度都按配置走。"""
        from agent_core.tool_shelves.classifier import complete_once

        def complete(system, user, cfg):
            return complete_once(
                system,
                user,
                tool_gate.cfg.get(),
                slot=str(cfg.get("slot") or "logic_slot"),
                timeout_ms=cfg.get("timeout_ms"),
                temperature=cfg.get("temperature"),
                max_tokens=cfg.get("max_tokens"),
            )

        return complete

    def _compression_summary(self, records):
        """压缩指标：工具页要看的总量 + 明细。"""
        return {
            "tools": len(records),
            "raw_chars": sum(int(r.get("raw_chars") or 0) for r in records),
            "clean_chars": sum(int(r.get("clean_chars") or 0) for r in records),
            "truncated": sum(1 for r in records if r.get("truncated")),
            "model_calls": sum(1 for r in records if r.get("model")),
            "errors": sum(1 for r in records if r.get("error")),
            "records": records,
        }

    def _gateway_ctx_build(self, body, request, session, memory, compute, tools):
        """拼 ctx：_gateway_complete / _gateway_stream 都读这个 dict。"""
        decision = tools["decision"]
        ctx = {
            "body": body,
            "messages": tools["messages"],
            "req_model": request["model"],
            "stream": request["stream"],
            "trace_id": request["trace_id"],
            "user_query": session["user_query"],
            "extra": tools["extra"],
            "has_tools": bool(tools["extra"].get("tools")),
            "tools_all": tools["tools_all"],
            "tool_gate": decision.as_dict() if decision is not None else None,
            "tool_gate_state": {"decision": decision},
            "tool_compression": tools["compression"],
        }
        ctx.update(self._gateway_ctx_identity(session))
        ctx.update(self._gateway_ctx_model(memory, compute))
        return ctx

    def _gateway_ctx_identity(self, session):
        """ctx 的身份部分：谁在说话、属于哪个档案。"""
        return {
            "channel_id": session["channel_id"],
            "channel_type": session["channel_type"],
            "conversation_type": session.get("conversation_type", ""),
            "group_id": session.get("group_id", ""),
            "session_id": session.get("session_id", ""),
            "actor_id": session["actor_id"],
            "actor_name": session["actor_name"],
            "role_type": session["role_type"],
            "is_guest": session["is_guest"],
            "perms": session["perms"],
            "client_id": session["client_id"],
            "archive_id": session["archive_id"],
            "person_id": session["person_id"],
            "platform": session["platform"],
            "sender_uid": session["raw_user_id"],
            "affection": session["affection"],
            "bound_by": session["bound_by"],
        }

    def _gateway_ctx_model(self, memory, compute):
        """ctx 的模型 / 提示词部分。"""
        return {
            "sys_prompt": memory["sys_prompt"] + _memory_tool_prompt(),
            "compute_mgr": compute["compute_mgr"],
            "is_logic_task": compute["is_logic_task"],
            "actual_model": compute["actual_model"],
        }
    def _gateway_fallback(self, ctx):
        q = ctx["user_query"]
        if ctx["is_logic_task"]:
            return f"刚才把这个问题让幕后工兵跑了一遍推导，核心原因抓到了：关于「{q[:25]}」，改法和逻辑已经整理好，自己拿去看~"
        if "你好" in q or "在吗" in q:
            return "在呢，今天有什么想和我聊聊的吗"
        if "谁" in q:
            return "我是你的私人专属智能体内核，无论在哪里交互，我都始终是同一个我"
        return f"关于你说的「{q[:20]}」，我已经同步到中枢记忆库了，随时可以继续往下聊"

    def _memory_store(self):
        try:
            from agent_core.storage import get_store
            return get_store()
        except Exception:
            return None

    def _maybe_auto_provision(self):
        """陌生模块首次 /v1（含 /v1/models）时，后台登记一次模块。"""
        try:
            from agent_core.auto_provision import provision_async
            provision_async(self._memory_store(), self.headers)
        except Exception:
            pass

    def _l1_start(self, store, ctx, layer, module_id, **kwargs):
        if not store:
            return None
        try:
            metadata = dict(kwargs.get("metadata") or {})
            metadata.setdefault("channel_type", ctx.get("conversation_type") or ctx.get("channel_type", ""))
            metadata.setdefault("group_id", ctx.get("group_id", ""))
            metadata.setdefault("session_id", ctx.get("session_id", ""))
            kwargs["metadata"] = metadata
            return store.record_invocation(
                ctx["trace_id"], layer, module_id,
                channel_id=ctx["channel_id"], actor_id=ctx["actor_id"], **kwargs
            )
        except Exception:
            return None

    def _l3(self, store, kind, content, **kwargs):
        if not store:
            return None
        try:
            return store.save_detail(kind, content, **kwargs)
        except Exception:
            return None

    def _gateway_record(self, ctx, content, p_in, p_out, has_tool_calls=False, detail_id="", tool_calls=None):
        """账本 + 时间轴 + 事实/好感度：一次请求的记忆副作用都在这。"""
        tools = _join_tool_names(tool_calls)
        self._record_usage(ctx, p_in, p_out, tools)
        if has_tool_calls or tools or not content or not ctx["user_query"]:
            return None
        if not ctx["perms"].get("allow_ingest_timeline", True):
            return None
        narrative = _narrative_of(ctx, content)
        event_id = self._ingest_timeline(ctx, narrative, detail_id)
        if event_id and not ctx.get("is_guest"):
            self._remember_facts(ctx, event_id, narrative)
            self._start_affection_eval(ctx, content)
        return event_id

    def _record_usage(self, ctx, p_in, p_out, tools):
        """记一笔 token 流水；失败不影响对话。"""
        try:
            backend.record_token_usage_event(
                ctx.get("actual_model") or ctx["req_model"],
                p_in, p_out, source=ctx["channel_id"], tools=tools,
            )
        except Exception:
            pass

    def _ingest_timeline(self, ctx, narrative, detail_id):
        """成功对话才进时间轴；出错就当没记过。"""
        try:
            event = backend.ingest_timeline_event(
                source=ctx["channel_id"],
                actor_name=ctx["actor_name"],
                narrative=narrative,
                payload=self._timeline_payload(ctx, detail_id),
                session_window_seconds=0,
            )
        except Exception:
            return None
        return event if isinstance(event, str) else None

    def _timeline_payload(self, ctx, detail_id):
        """时间轴附加字段：档案归属、重要度、来源明细。"""
        return {
            "actor_id": ctx["actor_id"],
            "archive_id": ctx.get("archive_id"),
            "person_id": ctx.get("person_id") or ctx["actor_id"],
            "importance": 2.0 if ctx["actor_id"] == "master" else 1.0,
            "channel": ctx["channel_id"],
            "channel_type": ctx.get("conversation_type") or ctx.get("channel_type", ""),
            "group_id": ctx.get("group_id", ""),
            "session_id": ctx.get("session_id", ""),
            "detail_id": detail_id,
        }

    def _remember_facts(self, ctx, event_id, narrative):
        """事件入图 + 规则抽事实；抽不到再让 AI 后台补一次。"""
        person_id = ctx.get("person_id") or ctx["actor_id"]
        try:
            from agent_core.memory.facts import FactStore

            fact_store = FactStore()
            fact_store.index_event(event_id, narrative)
            # 统一写 user_facts（source=ai_extract）；memory_facts 已没人读
            stored = fact_store.store_extracted(
                person_id, ctx["user_query"], use_ai=False, source="ai_extract",
            )
        except Exception:
            return
        if not stored:
            threading.Thread(
                target=_extract_facts_with_ai, args=(person_id, ctx["user_query"]), daemon=True
            ).start()

    def _start_affection_eval(self, ctx, content):
        """好感度评估放后台线程，慢也不挡回复。"""
        threading.Thread(target=_evaluate_affection, args=(ctx, content), daemon=True).start()

    def _gateway_auth_error(self, code):
        mapping = {
            "missing_key": ("缺少 API Key，请在客户端填入中枢签发的 Key", 401),
            "invalid_key": ("API Key 无效或已撤销", 401),
            "rate_limited": ("请求过于频繁，请稍后再试", 429),
            "guest_disabled": ("当前仅允许白名单成员，访客模式未开启", 403),
        }
        message, status = mapping.get(code, ("认证失败", 401))
        self._send_json({
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": None,
                "code": code,
            }
        }, status=status)

    def _handle_universal_chat_completions(self, body):
        ctx = self._gateway_context(body)
        if "error" in ctx:
            return self._gateway_auth_error(ctx["error"])
        if ctx["stream"]:
            return self._gateway_stream(ctx)
        return self._gateway_complete(ctx)

    # ------------------------------------------------------------------
    # 工具按需加载：enforce_soft 的软兜底
    # ------------------------------------------------------------------
    def _gate_live(self, ctx):
        state = ctx.get("tool_gate_state") or {}
        return state.get("decision")

    def _gate_auto_expand_allowed(self, ctx, decision) -> bool:
        """本轮被裁剪后上游返回空响应 → 允许用全量工具重试一次。"""
        if not decision or not decision.applied:
            return False
        if decision.tools_after <= 0:
            return False
        if not decision.tools_after < decision.tools_before:
            return False
        if bool(ctx.get("tool_expanded")):
            return False
        try:
            auto = (tool_gate.cfg.gateway().get("auto_expand") or {}) if tool_gate else {}
        except Exception:
            auto = {}
        return bool(auto.get("enabled", True)) and bool(auto.get("retry_on_empty", True))

    def _gate_expand(self, ctx) -> None:
        """把本轮工具恢复为上游全量，并标记已展开（单轮只展开一次）。"""
        ctx["tool_expanded"] = True
        tools_all = ctx.get("tools_all") or []
        if tools_all:
            ctx["extra"] = dict(ctx.get("extra") or {})
            ctx["extra"]["tools"] = tools_all
            if ctx["body"].get("tool_choice") is not None:
                ctx["extra"]["tool_choice"] = ctx["body"]["tool_choice"]
            if ctx["body"].get("parallel_tool_calls") is not None:
                ctx["extra"]["parallel_tool_calls"] = ctx["body"]["parallel_tool_calls"]
        print("[ToolShelves] 空响应，自动展开全量工具重试一次", flush=True)

    def _gate_note(self, ctx, content, tool_calls) -> None:
        """记录主力 AI 回复到短路上下文；并统计"无工具轮仍想调工具"的误裁。"""
        if tool_gate is None:
            return
        decision = self._gate_live(ctx)
        try:
            if decision is not None and not ctx.get("tool_expanded"):
                tool_gate.note_response(decision, tool_calls)
            if decision is not None:
                tool_gate.remember_reply(decision.session_key, content or "", tool_calls)
        except Exception:
            pass

    def _gate_log(self, ctx, record) -> None:
        if tool_gate is None:
            return
        try:
            if not tool_gate.cfg.gateway().get("log_decisions", False):
                return
            from agent_core.tool_shelves.logging_sink import log_decision
            log_decision(record)
        except Exception:
            pass

    def _persona_request(self, ctx, active_msgs, extra):
        """一次人格模型调用，返回 (message, finish_reason, usage, model)。"""
        data = ctx["compute_mgr"].chat_completion_response(
            "persona_slot", active_msgs, current_port=PORT, timeout=120, extra=extra
        )
        if not data or not data.get("choices"):
            return None, "stop", {}, ""
        choice = data["choices"][0] or {}
        return (
            choice.get("message") or {},
            choice.get("finish_reason") or "stop",
            data.get("usage") or {},
            data.get("model") or "",
        )

    def _gateway_complete(self, ctx):
        state = self._complete_begin(ctx)
        out = self._complete_output(ctx, self._complete_reply(ctx))
        detail_id = self._complete_audit(ctx, state, out)
        event_id = self._gateway_record(
            ctx, out["message"]["content"], out["in"], out["out"],
            bool(out["tool_calls"]), detail_id=detail_id or "",
            tool_calls=out["tool_calls"],
        )
        self._complete_invocations(ctx, state, out, event_id)
        self._send_json(
            self._complete_payload(ctx, out),
            extra_headers=self._hub_identity_headers(ctx),
        )

    def _complete_begin(self, ctx):
        """开一轮调用：通道级 invocation + 留档真正发给模型的请求。"""
        store = self._memory_store()
        inv_channel = self._l1_start(
            store, ctx, "channel", ctx["channel_id"],
            summary=(ctx["user_query"] or "")[:80],
            metadata={"model": ctx["req_model"], "stream": False, "archive_id": ctx.get("archive_id")},
        )
        # 记忆库要留"实际发给模型的东西"：system prompt 是中枢重建的，
        # 和 AstrBot 原样发来的 ctx["messages"] 里的 system 不是一回事
        self._l3(store, "llm_request", {
            "messages": _scrub_messages(ctx["messages"]),
            "system_prompt": ctx["sys_prompt"],
            "tools": ctx["extra"].get("tools"),
            "model": ctx["req_model"],
        }, invocation_id=inv_channel)
        inv_model = self._complete_model_invocation(ctx, store, inv_channel)
        return {"store": store, "inv_channel": inv_channel, "inv_model": inv_model}

    def _complete_model_invocation(self, ctx, store, inv_channel, summary="人格模型调用"):
        """真有人格模型调用才开子 invocation，空记录不进时间轴。"""
        if not (store and ctx["compute_mgr"] and ctx["user_query"]):
            return None
        return self._l1_start(
            store, ctx, "model", "llm.persona",
            parent_id=inv_channel, summary=summary,
        )

    def _complete_reply(self, ctx):
        """真去调模型；拿不到回复返回 None，由上层走兜底话术。"""
        if not (ctx["compute_mgr"] and ctx["user_query"]):
            return None
        if ctx["is_logic_task"] and not ctx["has_tools"]:
            return self._complete_logic_worker(ctx)
        return self._complete_persona(ctx)

    def _complete_logic_worker(self, ctx):
        """逻辑重活：先让幕后工兵推导，再用人格口吻把结论交出去。"""
        worker_msgs = [
            {"role": "system", "content": "You are a top-tier software engineer and logical reasoning engine. Solve the problem directly, rigorously and clearly. Output clean markdown and code with zero fluff."},
            {"role": "user", "content": ctx["user_query"]},
        ]
        worker_out, w_in, w_out = ctx["compute_mgr"].chat_completion(
            "logic_slot", worker_msgs, current_port=PORT, timeout=90
        )
        if not worker_out:
            return None
        self._record_logic_usage(ctx, w_in, w_out)
        return self._complete_logic_handoff(ctx, worker_out)

    def _record_logic_usage(self, ctx, tokens_in, tokens_out):
        """工兵那一笔也要记账；失败不影响主流程。"""
        try:
            backend.record_token_usage_event(
                ctx["compute_mgr"].resolve_model("logic_slot") or "logic_slot",
                tokens_in, tokens_out, source=ctx["channel_id"],
            )
        except Exception:
            pass

    def _complete_logic_handoff(self, ctx, worker_out):
        """让工兵只出结论，人格模型负责口吻；交付物仍以工兵原文兜底。"""
        synth_query = (
            f"幕后工兵已推导解决完毕，技术产物与结论如下：\n{worker_out}\n\n"
            f"请以{_persona_name()}特有的从容、通透随性、至善而微厌世的口吻，把结论和改动交付给{_master_name()}（句末绝不带句号，纯口语）。"
        )
        synth_out, s_in, s_out = ctx["compute_mgr"].chat_completion(
            "persona_slot",
            [{"role": "system", "content": ctx["sys_prompt"]},
             {"role": "user", "content": synth_query}],
            current_port=PORT, timeout=90,
        )
        return {
            "message": {"role": "assistant", "content": synth_out or worker_out},
            "finish_reason": "stop",
            "usage": {"prompt_tokens": s_in, "completion_tokens": s_out},
        }

    def _complete_persona(self, ctx):
        """常规人格对话；被裁得太狠导致空回复时，用全量工具重试一次。"""
        active_msgs = [{"role": "system", "content": ctx["sys_prompt"]}] + [
            m for m in ctx["messages"] if m.get("role") != "system"
        ]
        message, finish, usage, used_model = self._persona_request(
            ctx, active_msgs, dict(ctx.get("extra") or {})
        )
        # enforce_soft：既无正文也无 tool_calls，多半是被裁没了工具
        if self._gate_empty_reply(message) and self._gate_auto_expand_allowed(ctx, self._gate_live(ctx)):
            self._gate_expand(ctx)
            message, finish, usage, used_model = self._persona_request(
                ctx, active_msgs, dict(ctx.get("extra") or {})
            )
        message = self._complete_with_memory_lookup(ctx, active_msgs, message, usage)
        if used_model:
            ctx["actual_model"] = used_model
        return {"message": message, "finish_reason": finish, "usage": usage}

    def _complete_with_memory_lookup(self, ctx, active_msgs, message, usage):
        """模型要求查旧记忆时：查一次、把结果追加进消息、再让它答。"""
        if not message:
            return message
        request = _parse_memory_directive(message.get("content") or "")
        if not request:
            return message
        found = self._run_memory_lookup(ctx, request)
        extra = [{"role": "user", "content": found + "\n\n请根据以上检索结果回答我上一个问题。"}]
        retry_msgs = active_msgs + [message] + extra
        again, _, usage2, _ = self._persona_request(ctx, retry_msgs, dict(ctx.get("extra") or {}))
        if again and (again.get("content") or "").strip():
            return _strip_memory_directive(again)
        stripped = _strip_memory_directive(message)
        # 复答为空：首轮如果还有正文就用它，否则交给兜底话术
        return stripped if (stripped.get("content") or "").strip() else None

    def _run_memory_lookup(self, ctx, request):
        """执行检索并把结果压成一段提示；失败就告诉它没查到。"""
        from agent_core.memory import deep_recall

        store = self._memory_store()
        if store is None:
            return "【记忆库】不可用。"
        common = {
            "start_ts": deep_recall.parse_time_bound(request.get("start")),
            "end_ts": deep_recall.parse_time_bound(request.get("end")),
            "person_ids": [ctx["person_id"]] if ctx.get("person_id") else None,
            "sources": [request["source"]] if request.get("source") else None,
            "limit": 500,
        }
        terms = deep_recall.parse_terms(request.get("keywords") or "")
        rows = store.search_narratives(terms, **common) if terms else []
        if not rows and terms and len(terms) > 1:
            rows = store.search_narratives(terms, match_all=False, **common)
        hits = [
            deep_recall.Hit(str(getattr(row, "event_id", "")), str(getattr(row, "time_str", "")),
                            float(getattr(row, "timestamp", 0) or 0),
                            str(getattr(row, "narrative", "") or ""))
            for row in rows
        ]
        kept, mode = deep_recall.trim_hits(hits, deep_recall.DEFAULT_BUDGET // 2)
        if not kept:
            return "【记忆库】没有匹配的旧记录。"
        lines = [f"【记忆库检索结果】mode={mode}，共 {len(kept)} 条："]
        lines.extend(f"- [{hit.time_str}] {hit.narrative}" for hit in kept)
        return "\n".join(lines)

    def _gate_empty_reply(self, message):
        """既没正文也没 tool_calls：上游回了空，值得重试。"""
        if message is None:
            return False
        return not (message.get("content") or "").strip() and not message.get("tool_calls")

    def _complete_output(self, ctx, reply):
        """整理要发回去的消息 + token 估算。"""
        reply = reply or {}
        message = reply.get("message")
        fallback = message is None
        if fallback:
            message = {"role": "assistant", "content": self._gateway_fallback(ctx)}
        out_message = self._out_message(message)
        if not (out_message.get("content") or "").strip() and not out_message.get("tool_calls"):
            out_message = {"role": "assistant", "content": self._gateway_fallback(ctx)}
            fallback = True
        usage = reply.get("usage") or {}
        p_in, p_out = self._estimate_tokens(ctx, out_message["content"], usage)
        return {
            "message": out_message,
            "tool_calls": out_message.get("tool_calls"),
            "finish_reason": reply.get("finish_reason") or "stop",
            "usage": usage,
            "fallback": fallback,
            "in": p_in,
            "out": p_out,
        }

    def _out_message(self, message):
        """发回去的那一条：只留 role/content/tool_calls/refusal。"""
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        if content and not tool_calls:
            content = backend.clean_persona_reply(content)
        out = {"role": "assistant", "content": content}
        if tool_calls:
            out["tool_calls"] = tool_calls
        if message.get("refusal"):
            out["refusal"] = message["refusal"]
        return out

    def _estimate_tokens(self, ctx, content, usage):
        """上游没给 usage 时按字数估，宁可偏高也别显示 0。"""
        p_in = int(usage.get("prompt_tokens")
                   or max(10, len(ctx["user_query"]) * 2 + len(ctx["sys_prompt"]) // 4))
        p_out = int(usage.get("completion_tokens") or max(5, len(content) // 2))
        return p_in, p_out

    def _complete_audit(self, ctx, state, out):
        """闸门留痕 + 记忆库留档；返回 detail_id 给账本关联。"""
        self._gate_note(ctx, out["message"]["content"], out["tool_calls"])
        self._log_gate_decision(ctx, out)
        if not state["store"]:
            return None
        return self._l3(state["store"], "llm_response", {
            "message": out["message"],
            "usage": out["usage"],
            "finish_reason": out["finish_reason"],
            "fallback": out["fallback"],
            "tool_gate": ctx.get("tool_gate"),
        }, invocation_id=state["inv_model"] or state["inv_channel"])

    def _log_gate_decision(self, ctx, out, stream=False):
        """工具货架逐轮明细：只有真过了闸门才记。"""
        gate_info = ctx.get("tool_gate")
        if not gate_info:
            return
        record = dict(
            gate_info,
            trace=ctx.get("trace_id"),
            channel=ctx.get("channel_id"),
            actor=ctx.get("actor_id"),
            platform=ctx.get("platform"),
            expanded=bool(ctx.get("tool_expanded")),
            compression=ctx.get("tool_compression"),
        )
        if stream:
            record["stream"] = True
        else:
            record["tool_calls"] = [
                ((tc.get("function") or {}).get("name")) for tc in (out.get("tool_calls") or [])
            ]
        self._gate_log(ctx, record)

    def _complete_invocations(self, ctx, state, out, event_id, record_tools=True):
        """工具与调用收尾：工具请求一条条记，再统一 finish。"""
        store = state["store"]
        if not store:
            return
        if record_tools:  # 流式路径不单独记 tool invocation，只记名字
            for tool_call in out["tool_calls"] or []:
                self._record_tool_call(ctx, state, tool_call)
        status = "fallback" if out["fallback"] else "ok"
        tokens = f"{out['in']}+{out['out']} tokens"
        for inv in (state["inv_model"], state["inv_channel"]):
            self._finish_invocation(store, inv, status, event_id, tokens)

    def _record_tool_call(self, ctx, state, tool_call):
        """客户端要调工具：开一条 tool invocation，参数原样存下来。"""
        store = state["store"]
        fn_name = ((tool_call.get("function") or {}).get("name")) or "unknown"
        inv_tool = self._l1_start(
            store, ctx, "tool", fn_name,
            parent_id=state["inv_model"] or state["inv_channel"],
            summary="客户端请求工具",
        )
        self._l3(store, "tool_args", tool_call, invocation_id=inv_tool)
        try:
            store.finish_invocation(inv_tool, status="requested")
        except Exception:
            pass

    def _finish_invocation(self, store, inv_id, status, event_id, tokens):
        """模型 / 通道 invocation 收尾；写失败也不让对话失败。"""
        if not inv_id:
            return
        try:
            store.finish_invocation(inv_id, status=status, event_id=event_id, summary=tokens)
        except Exception:
            pass

    def _complete_payload(self, ctx, out):
        """OpenAI 格式响应体。"""
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": ctx["req_model"],
            "choices": [{
                "index": 0,
                "message": out["message"],
                "finish_reason": out["finish_reason"],
            }],
            "usage": {
                "prompt_tokens": out["in"],
                "completion_tokens": out["out"],
                "total_tokens": out["in"] + out["out"],
            },
            "hub": self._hub_identity_payload(ctx),
        }

    def _gateway_stream(self, ctx):
        self._stream_prepare(ctx)
        accumulator = _StreamAccumulator()
        state = self._stream_begin(ctx)
        pump = self._stream_pump(ctx, accumulator)
        if pump["aborted"]:  # 客户端断开：不再记账，避免半截数据进库
            return
        out = self._stream_output(ctx, accumulator, pump["wrote"])
        detail_id = self._stream_audit(ctx, state, out)
        event_id = self._gateway_record(
            ctx, out["content"], out["in"], out["out"], out["tool_call_flag"],
            detail_id=detail_id or "", tool_calls=out["tool_calls"],
        )
        self._complete_invocations(ctx, state, out, event_id, record_tools=False)

    def _stream_prepare(self, ctx):
        """SSE 响应头：先发头，后面才能边生成边下推。"""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in self._hub_identity_headers(ctx).items():
            self.send_header(key, str(value))
        self.end_headers()

    def _stream_begin(self, ctx):
        """流式也要开 invocation：通道一条、人格模型一条。"""
        store = self._memory_store()
        inv_channel = self._l1_start(
            store, ctx, "channel", ctx["channel_id"],
            summary=(ctx["user_query"] or "")[:80],
            metadata={"model": ctx["req_model"], "stream": True, "archive_id": ctx.get("archive_id")},
        )
        self._l3(store, "llm_request", {
            "messages": ctx["messages"],
            "tools": ctx["extra"].get("tools"),
            "model": ctx["req_model"],
            "stream": True,
            "system_prompt": ctx["sys_prompt"],
        }, invocation_id=inv_channel)
        inv_model = self._complete_model_invocation(ctx, store, inv_channel, "人格模型流式调用")
        return {"store": store, "inv_channel": inv_channel, "inv_model": inv_model}

    def _stream_pump(self, ctx, accumulator):
        """转发上游 SSE；返回 {wrote: 是否写过内容, aborted: 是否要中止}。"""
        pump = {"wrote": False, "aborted": False}
        try:
            if ctx["compute_mgr"] and ctx["user_query"]:
                pump["wrote"] = self._stream_forward(ctx, accumulator)
            if not pump["wrote"]:
                self._stream_fallback_chunk(ctx, accumulator)
            if not accumulator.saw_done:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pump["aborted"] = True  # 客户端跑了，后面的记账也没人看
        except Exception as exc:
            print(f"[Gateway] 流式异常: {exc}", flush=True)
        return pump

    def _stream_forward(self, ctx, accumulator):
        """把上游 chunk 原样写给客户端，边写边喂累加器。"""
        active_msgs = [{"role": "system", "content": ctx["sys_prompt"]}] + [
            m for m in ctx["messages"] if m.get("role") != "system"
        ]
        wrote = False
        for chunk in ctx["compute_mgr"].stream_chat_completion(
            "persona_slot", active_msgs, current_port=PORT, timeout=180, extra=ctx["extra"]
        ):
            wrote = True
            self.wfile.write(chunk)
            self.wfile.flush()
            accumulator.feed(chunk)
        return wrote

    def _stream_fallback_chunk(self, ctx, accumulator):
        """上游一个字都没吐：推一条兜底话术，别让前端空转。"""
        fallback = self._gateway_fallback(ctx)
        accumulator.content.append(fallback)
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": ctx["req_model"],
            "choices": [{"index": 0, "delta": {"content": fallback}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream_output(self, ctx, accumulator, wrote):
        """流结束后的汇总：正文、usage、token 估算、工具名。"""
        content = "".join(accumulator.content)
        usage = accumulator.usage or {}
        p_in, p_out = self._estimate_tokens(ctx, content, usage)
        return {
            "content": content,
            "usage": usage,
            "finish_reason": accumulator.finish_reason,
            "fallback": not wrote,
            "tool_calls": _ledger_tool_calls(accumulator),
            "tool_call_flag": bool(accumulator.tool_calls),
            "in": p_in,
            "out": p_out,
        }

    def _stream_audit(self, ctx, state, out):
        """闸门留痕 + llm_response 留档（流式版）。"""
        self._gate_note(ctx, out["content"], out["tool_call_flag"])
        self._log_gate_decision(ctx, out, stream=True)
        if not state["store"]:
            return None
        return self._l3(state["store"], "llm_response", {
            "content": out["content"],
            "usage": out["usage"],
            "finish_reason": out["finish_reason"],
            "fallback": out["fallback"],
            "tool_gate": ctx.get("tool_gate"),
        }, invocation_id=state["inv_model"] or state["inv_channel"])

class _StreamAccumulator:
    """从上游 SSE 中累计文本/tool_calls/usage，用于流结束后记账与回灌"""

    def __init__(self):
        self.buf = b""
        self.content = []
        self.usage = {}
        self.finish_reason = None
        self.saw_done = False
        self.tool_calls = False
        self.tool_call_names = []

    def feed(self, chunk):
        """按行拆 SSE：一行一个 data: 载荷，喂完就丢。"""
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self._feed_line(line.strip())

    def _feed_line(self, line):
        """只认 data: 行；[DONE] 单独标记，其余解成 JSON。"""
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]":
            self.saw_done = True
            return
        obj = self._decode(data)
        if obj is not None:
            self._feed_object(obj)

    def _decode(self, data):
        """半行垃圾很常见，解不开就跳过。"""
        try:
            return json.loads(data.decode("utf-8", "ignore"))
        except Exception:
            return None

    def _feed_object(self, obj):
        """usage / finish_reason 可能出现在任意一块里。"""
        if obj.get("usage"):
            self.usage = obj["usage"]
        for choice in obj.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            self._feed_delta(choice.get("delta") or {})

    def _feed_delta(self, delta):
        """正文按块拼；tool_calls 只记名字，账本要按工具归因。"""
        if delta.get("content"):
            self.content.append(delta["content"])
        for call in delta.get("tool_calls") or []:
            self.tool_calls = True
            name = ((call or {}).get("function") or {}).get("name")
            if name and name not in self.tool_call_names:
                self.tool_call_names.append(name)


class ReusableThreadingServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


_discovery_service = None
_bridge_manager = None
_mcp_manager = None


def _get_mcp_manager():
    global _mcp_manager
    if _mcp_manager is None:
        from agent_core.mcp import MCPManager
        from agent_core.storage import get_store

        _mcp_manager = MCPManager(get_store())
    return _mcp_manager


def _get_discovery_service():
    global _discovery_service
    if _discovery_service is None:
        from agent_core.connectors import DiscoveryService
        from agent_core.storage import get_store

        _discovery_service = DiscoveryService(get_store())
    return _discovery_service


def _get_bridge_manager():
    global _bridge_manager
    if _bridge_manager is None:
        from agent_core.bridge import BridgeManager
        from agent_core.storage import get_store

        _bridge_manager = BridgeManager(get_store())
        try:
            _bridge_manager.restore()
        except Exception:
            pass
    return _bridge_manager


def _start_discovery():
    if os.getenv("HUB_DISCOVERY_DISABLED"):
        return
    try:
        interval = float(os.getenv("HUB_DISCOVERY_INTERVAL", "60"))

        def loop():
            service = _get_discovery_service()
            while True:
                try:
                    service.scan()
                except Exception:
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True, name="platform-discovery").start()
    except Exception as e:
        print(f"⚠️ 平台嗅探启动失败: {e}", flush=True)


def _start_aggregator():
    if os.getenv("HUB_AGGREGATOR_DISABLED"):
        return
    try:
        from agent_core.memory.aggregator import run_forever
        from agent_core.storage import get_store

        threading.Thread(
            target=run_forever, args=(get_store(),),
            kwargs={"interval": 30.0},
            daemon=True, name="segment-aggregator",
        ).start()
    except Exception as e:
        print(f"⚠️ 聚合器启动失败: {e}", flush=True)


def _start_memory_maintenance():
    """低峰维护：回填向量 + 把旧事件沉淀成日记；不阻塞请求。"""
    if os.getenv("HUB_MEMORY_MAINTENANCE_DISABLED"):
        return
    try:
        def loop():
            time.sleep(60)
            while True:
                try:
                    from agent_core.memory import MemoryHub
                    MemoryHub().consolidate_episodes(min_events=5, older_than_hours=24)
                except Exception:
                    pass
                try:
                    from agent_core.memory.embedding import embed_text_bytes
                    from agent_core.storage import get_store
                    store = get_store()
                    for evt in store.list_unembedded_events(200):
                        store.update_event_embedding(evt.event_id, embed_text_bytes(evt.narrative))
                except Exception:
                    pass
                time.sleep(3600)

        threading.Thread(target=loop, daemon=True, name="memory-maintenance").start()
    except Exception as e:
        print(f"⚠️ 记忆维护启动失败: {e}", flush=True)


def _warm_resource_cache():
    while True:
        try:
            backend.get_system_resources_metrics(force=True)
        except Exception:
            pass
        time.sleep(20)


def _startup_deploy_check() -> None:
    """启动后自检一次：有失败项才提醒，正常不打扰。"""
    try:
        checks = deploy_check.run_checks(port=PORT)
        summary = deploy_check.summarize(checks)
        if not summary["failed"]:
            return
        bad = [f"{item.name}: {item.detail}" for item in checks if item.status == "fail"]
        from agent_core import messages as _msgs

        _msgs.error("部署自检失败：" + "；".join(bad))
        audit_log.record("deploy_check_failed", dedupe=False, detail="；".join(bad))
    except Exception:
        pass


def _bind_host() -> str:
    """监听地址：环境变量 HUB_BIND > data/api_security.json 的 bind_host > 0.0.0.0。"""
    env = (os.getenv("HUB_BIND") or "").strip()
    if env:
        return env
    try:
        return api_security.load_config()["bind_host"]
    except Exception:
        return "0.0.0.0"


def run_server():
    audit_log.migrate_legacy()
    _start_aggregator()
    _start_memory_maintenance()
    _start_discovery()
    threading.Thread(target=_warm_resource_cache, daemon=True, name="resource-warmup").start()
    threading.Thread(target=_startup_deploy_check, daemon=True, name="deploy-check").start()
    while True:
        try:
            server = ReusableThreadingServer((_bind_host(), PORT), ConsoleHandler)
            print(f"🚀 Stellaris 控制台已启动: http://localhost:{PORT}", flush=True)
            try:
                from agent_core import messages as _msgs

                _msgs.info(f"中枢已启动 · 端口 {PORT}")
            except Exception:
                pass
            server.serve_forever()
        except Exception as e:
            print(f"⚠️ 服务异常中断: {e}，将在 1 秒后自动重启...", flush=True)
            time.sleep(1)
        except BaseException as be:
            print(f"⚠️ 捕获系统退出信号: {be}，等待 1 秒后恢复常驻...", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    run_server()
