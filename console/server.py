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
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import backend

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("CONSOLE_PORT", 18890))
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


def _header_text(value) -> str:
    """HTTP 头只保证 ASCII；中文名称由客户端 URL 编码，这里还原。"""
    text = str(value or "")
    if "%" in text:
        try:
            return unquote(text)
        except Exception:
            return text
    return text


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

from agent_core.auth import AuthManager
from agent_core.clients import client_registry

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

    def _send_json(self, data, status=200, extra_headers=None):
        if int(status or 200) >= 500:
            try:
                from agent_core import messages as _msgs

                detail = data.get("error") if isinstance(data, dict) else ""
                _msgs.error(f"{self.path.split('?')[0]} -> {status} {detail or ''}".strip())
            except Exception:
                pass
        origin = self.headers.get('Origin', '*')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', origin if origin != '*' else '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Session-Token, X-Channel, X-Channel-Type, X-User-Id, X-User-Name')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        origin = self.headers.get('Origin', '*')
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', origin if origin != '*' else '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Session-Token, X-Channel, X-Channel-Type, X-User-Id, X-User-Name')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.startswith((
            '/api/connection', '/api/modules', '/api/hosts', '/api/contexts',
            '/api/components', '/api/services', '/api/mcp', '/api/tool-permissions',
            '/api/bridges', '/api/plugins', '/api/execution_ai', '/qq/events',
        )):
            return self._send_json({'error': '连接管理已移除'}, status=404)

        # 1. 登录页面与免鉴权静态资源
        if path in ('/login', '/login.html'):
            login_file = os.path.join(STATIC_DIR, 'login.html')
            if os.path.exists(login_file):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                with open(login_file, 'rb') as f:
                    self.wfile.write(f.read())
                return
        elif path == '/api/auth/status':
            return self._send_json(auth_mgr.get_status(self._get_token()))

                # 1.1 静态资源服务 (css, js, 图标等)
        if path.startswith('/static/'):
            rel_path = path[len('/static/'):].lstrip('/')
            file_path = os.path.join(STATIC_DIR, 'static', rel_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                content_types = {
                    '.css': 'text/css; charset=utf-8',
                    '.js': 'application/javascript; charset=utf-8',
                    '.json': 'application/json; charset=utf-8',
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.svg': 'image/svg+xml',
                    '.ico': 'image/x-icon'
                }
                ctype = content_types.get(ext, 'application/octet-stream')
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        # 2. 根路由 /：若未登录则展示登录页，已登录则呈现控制台
        if path in ('/', '/index.html'):
            if not self._is_authenticated():
                login_file = os.path.join(STATIC_DIR, 'login.html')
                if os.path.exists(login_file):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    with open(login_file, 'rb') as f:
                        self.wfile.write(f.read())
                    return
            html_file = os.path.join(STATIC_DIR, 'index.html')
            if os.path.exists(html_file):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                self.end_headers()
                with open(html_file, 'r', encoding='utf-8') as f:
                    html = f.read()
                asset_version = _asset_version()
                html = re.sub(r'\?v=\d+', f'?v={asset_version}', html)
                self.wfile.write(html.encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()
            return

        # 3. 大模型兼容网关 /v1 豁免 Web 鉴权
        if path.startswith('/v1/') or path.startswith('/api/v1/'):
            if path in ('/v1/models', '/api/v1/models'):
                self._maybe_auto_provision()
                return self._send_json(backend.get_universal_models_list())

        # 3.1 档案接口（插件/外部工具用，独立 Key 鉴权）
        if path.startswith('/hub/v1/archives'):
            return self._handle_archive_get()

        # 3.2 直连服务注册表
        if path == '/hub/v1/services':
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            return self._send_json({'object': 'list', 'data': ServiceRegistry(get_store()).list()})

        if path.startswith('/hub/v1/services/'):
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            service_id = path.rstrip('/').rsplit('/', 1)[-1]
            service = ServiceRegistry(get_store()).get(service_id)
            if not service:
                return self._hub_error('not_found')
            return self._send_json(service)

        # 4. 其他所有 /api/ 端点实施登录保护
        if path.startswith('/api/'):
            if not self._is_authenticated():
                return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)

        # 4.1 控制台档案页（同源，无客户端 Key）
        if path.startswith('/api/archives'):
            return self._handle_console_archive_get()

        # 4.2 平台自动发现（连接器只读扫描）
        if path == '/api/connectors':
            svc = _get_discovery_service()
            return self._send_json({'status': svc.status()})

        # 4.3 动态插件注册表
        if path == '/api/plugins':
            from agent_core.plugins import PluginRegistry
            from agent_core.storage import get_store

            return self._send_json({
                'object': 'list',
                'data': PluginRegistry(get_store()).list(),
            })

        if path == '/api/services':
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            return self._send_json({
                'object': 'list',
                'data': ServiceRegistry(get_store()).list(),
            })

        if path == '/api/knowledge':
            from agent_core.knowledge import KnowledgeBase
            from agent_core.storage import get_store

            return self._send_json({
                'object': 'list',
                'data': KnowledgeBase(get_store()).list(),
            })

        if path == '/api/execution_ai':
            from agent_core.execution.settings import public_settings

            return self._send_json({'ok': True, **public_settings()})

        if path == '/hub/v1/knowledge/search':
            _, err = self._hub_client(require_admin=False)
            if err:
                return self._hub_error(err)
            from agent_core.knowledge import KnowledgeBase
            from agent_core.storage import get_store

            qs = parse_qs(urlparse(self.path).query)
            query = (qs.get('q') or qs.get('query') or [''])[0]
            limit = int((qs.get('limit') or ['3'])[0] or 3)
            return self._send_json({
                'object': 'list',
                'data': KnowledgeBase(get_store()).search(query, limit=limit),
            })

        if path == '/api/mcp/servers':
            return self._send_json({'object': 'list', 'data': _get_mcp_manager().list()})

        if path == '/api/bridges':
            return self._send_json({'status': _get_bridge_manager().status()})

        if path == '/api/contexts':
            from agent_core.storage import get_store
            qs = parse_qs(urlparse(self.path).query)
            store = get_store()
            contexts = store.list_contexts(
                platform=(qs.get('platform') or [None])[0],
                status=(qs.get('status') or [None])[0],
                host_id=(qs.get('host_id') or [None])[0],
            )
            binding_map = {
                (b['platform'], b['external_id']): b for b in store.list_bindings()
            }
            for item in contexts:
                for field in ('evidence', 'metadata'):
                    if isinstance(item.get(field), str):
                        fallback = '[]' if field == 'evidence' else '{}'
                        try:
                            item[field] = json.loads(item[field] or fallback)
                        except Exception:
                            item[field] = [] if field == 'evidence' else {}
                item['binding'] = binding_map.get((item['platform'], item['external_id']))
            return self._send_json({'object': 'list', 'data': contexts})

        if path == '/api/bindings':
            from agent_core.storage import get_store
            bindings = get_store().list_bindings()
            return self._send_json({'object': 'list', 'data': bindings})

        if path == '/api/overview':
            data = {
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
                "compute_config": backend.get_compute_config()
            }
            self._send_json(data)
        elif path == '/api/system/resources':
            self._send_json(backend.get_system_resources_metrics())
        elif path == '/api/timeline':
            self._send_json(backend.get_timeline_events(limit=60))
        elif path == '/api/persona':
            self._send_json(backend.get_current_persona())
        elif path == '/api/persona/rules':
            self._send_json(backend.get_persona_rules())
        elif path == '/api/models/sync_upstream':
            res = backend.sync_models_from_upstream()
            self._send_json(res)
        elif path == '/api/sniff_key':
            qs = parse_qs(urlparse(self.path).query)
            key = (qs.get('key') or qs.get('api_key') or [''])[0]
            self._send_json(backend.handle_sniff_key({"key": key}))
        elif path == '/api/models_config':
            self._send_json(backend.get_models_config())
        elif path == '/api/modules/custom':
            from console.modules.module_manager import get_custom_module_configs

            configs = get_custom_module_configs() or {}
            self._send_json({'data': configs.get('custom_modules', [])})
        elif path == '/api/components':
            from agent_core.storage import get_store

            self._send_json({'data': get_store().list_components()})
        elif path == '/api/connection/tree':
            from agent_core.connection import build_connection_tree
            from agent_core.storage import get_store
            from console.modules.module_manager import get_custom_module_configs

            customs = (get_custom_module_configs() or {}).get('custom_modules') or []
            self._send_json(build_connection_tree(
                get_store(),
                customs=customs,
                mcp_sessions=_get_mcp_manager().list(),
            ))
        elif path == '/api/hosts':
            from agent_core.storage import get_store

            self._send_json({'data': get_store().list_hosts()})
        elif path == '/api/modules':
            self._send_json(backend.get_modules_overview())
        elif path == '/api/modules/topology':
            self._send_json(backend.get_topology_overview())
        elif path == '/api/modules/permissions':
            self._send_json(backend.get_module_permissions())
        elif path == '/api/tool-permissions':
            self._send_json({
                'ok': False,
                'deprecated': True,
                'error': '已废弃，请使用 /api/tools（工具页）',
            }, status=410)
        elif path == '/api/compute/config':
            self._send_json(backend.get_compute_config())
        elif path == '/api/ledger':
            # 独立账单流水端点：返回完整 token_analytics，含 recent_ledger + credits + today/overall 统计
            self._send_json(backend.get_token_analytics())
        elif path == '/api/tokens':
            # 轻量统计端点：只查账本，不碰时间轴/模块/资源，保证首屏数字快速出现
            self._send_json(backend.get_token_analytics())
        elif path == '/api/access_policy':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.access_policy import load_policy
            self._send_json({"ok": True, "policy": load_policy()})
        elif path == '/api/memory/facts':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.memory.facts import FactStore
            qs = parse_qs(urlparse(self.path).query)
            subject_id = (qs.get('subject_id') or [''])[0].strip()
            as_of = (qs.get('as_of') or [''])[0].strip()
            if subject_id and as_of:
                try:
                    at_time = float(as_of)
                except ValueError:
                    return self._send_json({'ok': False, 'error': 'as_of 必须是 unix 时间戳'}, status=400)
                facts = FactStore().facts_at(subject_id, at_time)
                return self._send_json({'ok': True, 'facts': facts})
            facts = FactStore().list_facts(
                subject_id=subject_id,
                status=(qs.get('status') or ['active'])[0].strip() or 'active',
                query=(qs.get('q') or [''])[0].strip(),
                limit=int((qs.get('limit') or ['200'])[0] or 200),
            )
            self._send_json({'ok': True, 'facts': facts})
        elif path == '/api/memory/graph':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.memory.facts import FactStore
            qs = parse_qs(urlparse(self.path).query)
            subject_id = (qs.get('subject_id') or [''])[0].strip()
            if not subject_id:
                return self._send_json({'ok': False, 'error': 'subject_id 必填'}, status=400)
            self._send_json({'ok': True, **FactStore().graph(subject_id)})
        elif path in ('/v1/models', '/api/v1/models'):
            self._send_json(backend.get_universal_models_list())
        elif path in ('/api/tools/shelves', '/api/tools/shelves/status'):
            self._send_json(self._tool_shelves_status())
        elif path == '/api/identity/scan':
            self._send_json(self._identity_scan())
        elif path in ('/api/affection', '/api/affection/overview'):
            self._send_json(self._affection_overview())
        elif path == '/api/logs':
            self._send_json(self._logs_payload())
        elif path == '/api/logs/alerts':
            self._send_json(self._logs_alerts())
        elif path == '/api/tools':
            self._send_json(self._tools_admin_list())
        elif path == '/api/tools/policies':
            self._send_json(self._tools_policies())
        elif path == '/api/messages':
            try:
                from agent_core import messages as _msgs

                self._send_json({"ok": True, **_msgs.snapshot()})
            except Exception as exc:
                self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            self.send_response(404)
            self.end_headers()

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
        """P4 薄壳清洗接口：给外部薄壳 MCP 服务回灌清洗后的工具结果。

        入参：tool / args / raw / user_query
        出参：text / truncated / stats
        鉴权：带客户端 Key 就校验；没带 Key 时沿用"clients.json 不存在则不校验"的旧行为。
        """
        if not isinstance(body, dict):
            return self._send_json({'ok': False, 'error': 'body 必须是 JSON 对象'}, status=400)
        tool = str(body.get('tool') or '').strip()
        if not tool:
            return self._send_json({'ok': False, 'error': 'tool 必填'}, status=400)
        raw = body.get('raw')
        if raw is None:
            return self._send_json({'ok': False, 'error': 'raw 必填'}, status=400)
        if not isinstance(raw, str):
            try:
                raw = json.dumps(raw, ensure_ascii=False)
            except Exception:
                raw = str(raw)

        # 鉴权：有 Key 校验 Key；无 Key 且客户端注册表未启用校验时放行
        token = self.headers.get('Authorization', '')
        token = token[7:].strip() if token.lower().startswith('bearer ') else (
            self.headers.get('X-Hub-Key') or ''
        )
        if token:
            auth = client_registry.resolve(token)
            if auth.error:
                return self._send_json({'ok': False, 'error': auth.error}, status=401)
        elif client_registry.require_key:
            return self._send_json({'ok': False, 'error': 'missing_key'}, status=401)

        if tool_gate is None:
            return self._send_json({'ok': True, 'text': raw, 'truncated': False,
                                    'stats': {'tool': tool, 'passthrough': True}})
        try:
            comp_cfg = tool_gate.cfg.compression()
            if not comp_cfg.get('enabled') or tool in (comp_cfg.get('skip_tools') or []):
                return self._send_json({'ok': True, 'text': raw, 'truncated': False,
                                        'stats': {'tool': tool, 'passthrough': True}})

            from agent_core.tool_shelves.classifier import complete_once
            from agent_core.tool_shelves.cleaner import compress_tool_result

            def _complete(system, user, cfg):
                return complete_once(
                    system, user, tool_gate.cfg.get(),
                    slot=str(cfg.get('slot') or 'logic_slot'),
                    timeout_ms=cfg.get('timeout_ms'),
                    temperature=cfg.get('temperature'),
                    max_tokens=cfg.get('max_tokens'),
                )

            result = compress_tool_result(
                tool, raw, comp_cfg,
                args=body.get('args'),
                user_query=str(body.get('user_query') or ''),
                complete_once=_complete,
            )
            stats = result.as_dict()
            try:
                from agent_core.tool_shelves.metrics import metrics as shelf_metrics
                shelf_metrics.record_compression([stats])
            except Exception:
                pass
            return self._send_json({
                'ok': True,
                'text': result.text,
                'truncated': bool(result.truncated),
                'stats': stats,
            })
        except Exception as exc:
            print(f"[ToolShelves] 清洗接口异常，原文放行: {exc}", flush=True)
            return self._send_json({'ok': True, 'text': raw, 'truncated': False,
                                    'stats': {'tool': tool, 'error': str(exc)[:120]}})

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

        # 工具货架：热重载配置 / 清空统计（登录由下方 /api/ 统一校验）
        if path == '/api/tools/shelves/reload':
            if not self._is_authenticated():
                return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
            if tool_gate is None:
                return self._send_json({'ok': False, 'error': 'tool gate 未初始化'}, status=500)
            tool_gate.cfg.reload(force=True)
            return self._send_json({'ok': True, 'enabled': tool_gate.cfg.enabled(),
                                    'config_error': tool_gate.cfg.load_error()})
        if path == '/api/tools/shelves/metrics/reset':
            if not self._is_authenticated():
                return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)
            try:
                from agent_core.tool_shelves.metrics import metrics as shelf_metrics
                shelf_metrics.reset()
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=500)
            return self._send_json({'ok': True})

        # P4 薄壳 MCP：外部薄壳把工具生肉交过来，用同一套清洗逻辑压缩。
        # 放在 body 解析之后：这里既不要求 Web 登录（薄壳在容器里），也不用 Key 拦截（走接口内校验）。
        if path == '/api/tools/clean':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                clean_body = json.loads(post_data.decode('utf-8')) if post_data else {}
            except Exception:
                return self._send_json({'ok': False, 'error': 'Invalid JSON'}, status=400)
            return self._handle_tool_clean(clean_body)

        # 工具设置：改一个工具的模式/提权
        if path == '/api/tools':
            content_length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(content_length)
            try:
                tool_body = json.loads(raw.decode('utf-8')) if raw else {}
            except Exception:
                return self._send_json({'ok': False, 'error': 'Invalid JSON'}, status=400)
            return self._tools_admin_update(tool_body)

        # 档案级工具策略
        if path in ('/api/tools/policy', '/api/tools/policy/delete'):
            content_length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(content_length)
            try:
                pol_body = json.loads(raw.decode('utf-8')) if raw else {}
            except Exception:
                return self._send_json({'ok': False, 'error': 'Invalid JSON'}, status=400)
            if path.endswith('/delete'):
                return self._send_json(self._tools_policy_delete(pol_body))
            return self._send_json(self._tools_policy_update(pol_body))

        # 身份映射：批量把 UID 绑到档案（写白名单 + 请求解析用的绑定）
        if path == '/api/identity/bind':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                bind_body = json.loads(post_data.decode('utf-8')) if post_data else {}
            except Exception:
                return self._send_json({'ok': False, 'error': 'Invalid JSON'}, status=400)
            return self._identity_bind(bind_body)

        # 身份映射：隐藏 / 显示 UID
        if path == '/api/identity/hide':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                hide_body = json.loads(post_data.decode('utf-8')) if post_data else {}
            except Exception:
                return self._send_json({'ok': False, 'error': 'Invalid JSON'}, status=400)
            return self._identity_hide(hide_body)

        if path.startswith((
            '/api/connection', '/api/modules', '/api/hosts', '/api/contexts',
            '/api/components', '/api/services', '/api/mcp', '/api/tool-permissions',
            '/api/bridges', '/api/plugins', '/api/execution_ai', '/qq/events',
        )):
            return self._send_json({'error': '连接管理已移除'}, status=404)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            return self._send_json({"error": "Invalid JSON"}, status=400)

        # NapCat / OneBot 11 HTTP 上报（不经过 AstrBot）
        if path == '/qq/events':
            from agent_core.onebot_service import OneBotService

            expected = OneBotService().token
            auth = str(self.headers.get('Authorization') or '')
            if auth.lower().startswith('bearer '):
                auth = auth[7:].strip()
            if expected and auth != expected:
                return self._send_json({'ok': False, 'error': 'invalid token'}, status=401)
            from agent_core.connectors.onebot_events import get_responder

            threading.Thread(
                target=get_responder().handle,
                args=(body,),
                daemon=True,
                name='onebot-event',
            ).start()
            return self._send_json({'ok': True})

        if path == '/api/ledger/clear':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            try:
                from console.modules.common import get_db_connection
                from console.modules.model_service import reset_credits_and_balances

                conn = get_db_connection()
                count = conn.execute("DELETE FROM token_usage").rowcount
                conn.commit()
                conn.close()

                # 清空账本时同步归零所有金额：额度池 + 凭证余额
                reset_credits_and_balances()

                return self._send_json({
                    "ok": True,
                    "cleared": int(count or 0),
                    "balances_reset": True,
                })
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, status=500)

        if path == '/api/access_policy':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.access_policy import save_policy
            policy = save_policy(body if isinstance(body, dict) else {})
            return self._send_json({"ok": True, "policy": policy})

        if path == '/api/memory/facts/rollback':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            fact_id = str(body.get('fact_id') or '').strip()
            if not fact_id:
                return self._send_json({'ok': False, 'error': 'fact_id 必填'}, status=400)
            from agent_core.memory.facts import FactStore
            fact = FactStore().rollback(fact_id)
            return self._send_json({'ok': bool(fact), 'fact': fact})

        if path == '/api/memory/facts':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.memory.facts import FactStore
            store = FactStore()
            fact_id = str(body.get('fact_id') or '').strip()
            if fact_id:
                value = str(body.get('value') or '').strip()
                if not value:
                    return self._send_json({'ok': False, 'error': 'value 必填'}, status=400)
                fact = store.update_value(fact_id, value)
                return self._send_json({'ok': bool(fact), 'fact': fact})
            subject_id = str(body.get('subject_id') or '').strip()
            predicate = str(body.get('predicate') or '').strip()
            value = str(body.get('value') or '').strip()
            if not subject_id or not predicate or not value:
                return self._send_json(
                    {'ok': False, 'error': 'subject_id/predicate/value 必填'}, status=400
                )
            fact = store.add(subject_id, predicate, value)
            return self._send_json({'ok': bool(fact), 'fact': fact})

        # 1. 认证鉴权公共 API
        if path == '/api/auth/login':
            username = body.get('username', '').strip()
            password = body.get('password', '')
            remember = body.get('remember', True)
            if auth_mgr.verify_credentials(username, password):
                token = auth_mgr.create_session(username, duration_days=7 if remember else 1)
                max_age = 604800 if remember else 86400
                origin = self.headers.get('Origin', '*')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Set-Cookie', f'anan_session={token}; Path=/; SameSite=Lax; Max-Age={max_age}')
                self.send_header('Access-Control-Allow-Origin', origin if origin != '*' else '*')
                self.send_header('Access-Control-Allow-Credentials', 'true')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "token": token, "username": username}, ensure_ascii=False).encode('utf-8'))
                return
            else:
                return self._send_json({"success": False, "error": "用户名或安全密码不正确"}, status=401)
        elif path == '/api/auth/logout':
            token = self._get_token()
            auth_mgr.revoke_session(token)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Set-Cookie', 'anan_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}, ensure_ascii=False).encode('utf-8'))
            return
        elif path == '/api/auth/change_password':
            if not self._is_authenticated():
                return self._send_json({"error": "未登录"}, status=401)
            old_p = body.get('old_password', '')
            new_p = body.get('new_password', '')
            ok, msg = auth_mgr.change_password(old_p, new_p)
            return self._send_json({"success": ok, "message": msg}, status=200 if ok else 400)

        # 2.0 直连服务协议
        if path == '/hub/v1/services/register':
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            try:
                service = ServiceRegistry(get_store()).register(body)
            except Exception:
                return self._hub_error('bad_request', status=400)
            return self._send_json(service, status=201)

        if path.startswith('/hub/v1/services/') and '/tools/' in path:
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
            parts = path.strip('/').split('/')
            # hub v1 services {id} tools {name}
            if len(parts) < 6:
                return self._hub_error('bad_request')
            service_id, tool = parts[3], parts[5]
            args = body.get('args') if isinstance(body.get('args'), dict) else body
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            try:
                result = ServiceRegistry(get_store()).call_tool(service_id, tool, args)
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=400)
            return self._send_json(result)

        # 2. 档案接口
        if path.startswith('/hub/v1/archives'):
            return self._handle_archive_post(path, body)
        if path.startswith('/api/archives'):
            return self._handle_console_archive_post(path, body)

        # 2.1 平台自动发现与绑定
        if path == '/api/connectors/scan':
            return self._send_json(_get_discovery_service().scan())

        if path == '/api/plugins/scan':
            from agent_core.plugins import PluginRegistry
            from agent_core.storage import get_store

            roots = body.get('roots') or None
            return self._send_json(PluginRegistry(get_store()).scan(roots))

        if path == '/api/plugins':
            from agent_core.plugins import PluginRegistry
            from agent_core.storage import get_store

            reg = PluginRegistry(get_store())
            target = str(body.get('path') or body.get('root_path') or '').strip()
            if target:
                plugin = reg.register_path(target, name=str(body.get('name') or ''))
                if not plugin:
                    return self._send_json({'error': '无法识别该路径'}, status=400)
                return self._send_json(plugin, status=201)
            name = str(body.get('name') or '').strip()
            protocol = str(body.get('protocol') or '').strip()
            spec = body.get('spec') if isinstance(body.get('spec'), dict) else None
            if not name or not protocol:
                return self._send_json({'error': 'name/protocol 必填，或提供 path'}, status=400)
            return self._send_json(
                reg.register_manual(name, target, protocol, spec), status=201
            )

        if path == '/api/services':
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            try:
                service = ServiceRegistry(get_store()).register(body)
            except Exception as exc:
                return self._send_json({'error': str(exc)}, status=400)
            return self._send_json(service, status=201)

        if path == '/hub/v1/knowledge/ingest':
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

        if path == '/api/mcp/connect':
            try:
                return self._send_json(_get_mcp_manager().connect(body), status=201)
            except Exception as exc:
                return self._send_json({'error': str(exc)}, status=400)

        if path.startswith('/api/mcp/') and '/tools/' in path:
            parts = path.strip('/').split('/')
            if len(parts) < 5:
                return self._send_json({'error': '路径不完整'}, status=400)
            name, tool = parts[2], parts[4]
            args = body.get('args') if isinstance(body.get('args'), dict) else body
            try:
                return self._send_json({
                    'ok': True,
                    'result': _get_mcp_manager().call_tool(name, tool, args),
                })
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=400)

        if path in ('/api/bridges/infer', '/api/bridges/attach'):
            import asyncio

            from agent_core.bridge import BridgeSpec, BridgeSpecInferer

            target = str(body.get('target') or body.get('path') or body.get('url') or '').strip()
            kind = str(body.get('kind') or '').strip()
            extra_options = body.get('options') if isinstance(body.get('options'), dict) else None
            if not target:
                return self._send_json({'error': 'target 必填'}, status=400)
            try:
                result = asyncio.run(BridgeSpecInferer().infer(target, kind, extra_options))
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=500)
            if body.get('start') and result.get('ok'):
                try:
                    started = _get_bridge_manager().start(BridgeSpec.from_dict(result['spec']))
                    result['started'] = started
                except Exception as exc:
                    result['started'] = None
                    result['start_error'] = str(exc)
            return self._send_json(result)

        if path == '/api/adapters/analyze':
            from agent_core.source import SourceAnalyzer

            repo = str(body.get('repo_path') or body.get('path') or '').strip()
            if not repo:
                return self._send_json({'ok': False, 'error': 'repo_path 必填'}, status=400)
            return self._send_json(SourceAnalyzer().analyze(repo))

        if path in ('/api/adapters/generate', '/api/adapters/attach'):
            import asyncio

            from agent_core.source import SourceSynthesizer

            repo = str(body.get('repo_path') or body.get('path') or '').strip()
            if not repo:
                return self._send_json({'ok': False, 'error': 'repo_path 必填'}, status=400)
            try:
                result = asyncio.run(SourceSynthesizer().generate(repo))
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=500)
            if path.endswith('/attach') and body.get('start'):
                from agent_core.bridge import BridgeSpec

                started = []
                for item in result.get('specs') or []:
                    if not item.get('ok') or not item.get('spec'):
                        continue
                    try:
                        started.append(_get_bridge_manager().start(
                            BridgeSpec.from_dict(item['spec'])
                        ))
                    except Exception as exc:
                        started.append({'ok': False, 'error': str(exc)})
                result['started'] = started
            return self._send_json(result)

        if path == '/api/bridges/batch':
            import asyncio

            from agent_core.bridge import BridgeSpec, BridgeSpecInferer

            raw_targets = body.get('targets') or body.get('target') or []
            if isinstance(raw_targets, str):
                raw_targets = [x.strip() for x in re.split(r"[\n,，;；]+", raw_targets) if x.strip()]
            targets = [str(x).strip() for x in raw_targets if str(x).strip()][:20]
            if not targets:
                return self._send_json({'error': 'targets 必填'}, status=400)
            kind = str(body.get('kind') or '').strip()
            auto_start = bool(body.get('start'))
            inferer = BridgeSpecInferer()
            results = []
            for target in targets:
                try:
                    item = asyncio.run(inferer.infer(target, kind))
                except Exception as exc:
                    item = {'ok': False, 'error': str(exc), 'preview': []}
                record = {
                    'target': target,
                    'ok': bool(item.get('ok')),
                    'kind': (item.get('spec') or {}).get('kind'),
                    'preview_count': len(item.get('preview') or []),
                    'error': item.get('error') or '',
                }
                if auto_start and item.get('ok'):
                    try:
                        record['started'] = _get_bridge_manager().start(
                            BridgeSpec.from_dict(item['spec'])
                        )
                    except Exception as exc:
                        record['started'] = None
                        record['start_error'] = str(exc)
                results.append(record)
            return self._send_json({'count': len(results), 'results': results})

        if path == '/api/bridges/start':
            from agent_core.bridge import BridgeSpec

            spec = BridgeSpec.from_dict(body)
            if not spec.source_id or not spec.kind:
                return self._send_json({'error': 'source_id/kind 必填'}, status=400)
            try:
                return self._send_json(_get_bridge_manager().start(spec))
            except Exception as exc:
                return self._send_json({'error': str(exc)}, status=400)

        if path == '/api/bridges/stop':
            source_id = str(body.get('source_id') or '').strip()
            if not source_id:
                return self._send_json({'error': 'source_id 必填'}, status=400)
            return self._send_json(_get_bridge_manager().stop(source_id))

        if path == '/api/contexts/match':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.connectors import match_contexts
            from agent_core.storage import get_store

            names = body.get('names') or body.get('name') or []
            uids = body.get('uids') or body.get('uid') or []
            platform = str(body.get('platform') or '').strip() or None
            module_id = str(body.get('module_id') or '').strip()
            store = get_store()
            if module_id:
                host = next(
                    (h for h in store.list_hosts() if h.get('host_id') == module_id), None
                )
                if not host:
                    return self._send_json({'object': 'list', 'data': []}, status=404)
                platform = platform or str(host.get('platform') or '') or None
            am = self._archive_manager()
            contexts = store.list_contexts(status='active')
            if module_id:
                contexts = [
                    c for c in contexts
                    if str(c.get('host_id') or '') == module_id
                    or module_id.lower() in str(c.get('source') or '').lower()
                    or (platform and c.get('platform') == platform)
                ]
            archive_names = {
                a['archive_id']: (a.get('display_name') or '')
                for a in am.list_archives()
            }
            binding_map = {
                (b['platform'], b['external_id']): b for b in store.list_bindings()
            }
            for ctx in contexts:
                aliases = []
                binding = binding_map.get((ctx['platform'], ctx['external_id']))
                if binding:
                    archive_name = archive_names.get(binding.get('archive_id'))
                    if archive_name:
                        aliases.append(archive_name)
                ctx['aliases'] = aliases
            matches = match_contexts(contexts, names, uids, platform)
            return self._send_json({'object': 'list', 'data': matches})

        if path == '/api/contexts/ai_match':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            import asyncio

            from agent_core.connectors.ai_matcher import AIMatcher
            from agent_core.storage import get_store

            names = body.get('names') or body.get('name') or []
            uids = body.get('uids') or body.get('uid') or []
            platform = str(body.get('platform') or '').strip()
            module_id = str(body.get('module_id') or '').strip()
            store = get_store()
            roots = None
            if module_id:
                host = next(
                    (h for h in store.list_hosts() if h.get('host_id') == module_id), None
                )
                if not host:
                    return self._send_json(
                        {'ok': False, 'error': '模块不存在', 'matches': []}, status=404
                    )
                root = str(host.get('root') or '').strip()
                if root:
                    roots = [Path(root)]
                if not platform:
                    platform = str(host.get('platform') or '').strip()
            try:
                result = asyncio.run(
                    AIMatcher(store=store, roots=roots).match(names, uids, platform)
                )
            except Exception as exc:
                return self._send_json(
                    {'ok': False, 'error': f'执行 AI 失败: {exc}', 'matches': []}, status=500
                )
            if result.get('ok'):
                for item in result.get('matches', []):
                    external_id = str(item.get('external_id') or '').strip()
                    if not external_id:
                        continue
                    store.upsert_context(
                        str(item.get('platform') or 'other'),
                        external_id,
                        external_type=str(item.get('external_type') or ''),
                        display_name=str(item.get('display_name') or ''),
                        source='execution_ai',
                        evidence=list(item.get('evidence') or []),
                        metadata={'ai_match': True, 'module_id': module_id},
                        host_id=module_id,
                    )
            return self._send_json(result)

        if path == '/api/contexts/ai_scan':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            import asyncio

            from agent_core.connectors.ai_matcher import AIMatcher
            from agent_core.storage import get_store

            module_id = str(body.get('module_id') or '').strip()
            if not module_id:
                return self._send_json(
                    {'ok': False, 'error': 'module_id 必填', 'users': []}, status=400
                )
            store = get_store()
            host = next(
                (h for h in store.list_hosts() if h.get('host_id') == module_id), None
            )
            if not host:
                return self._send_json(
                    {'ok': False, 'error': '模块不存在', 'users': []}, status=404
                )
            root = str(host.get('root') or '').strip()
            host_root = Path(root) if root else None
            roots = [host_root] if host_root else []
            # 通用适配：把该模块下注册过的插件目录都纳入扫描范围
            for plugin in store.list_plugins():
                raw_plugin_root = str(plugin.get('root_path') or '').strip()
                if not raw_plugin_root:
                    continue
                plugin_root = Path(raw_plugin_root)
                if not plugin_root.exists():
                    continue
                if host_root and (plugin_root == host_root or host_root in plugin_root.parents):
                    roots.append(plugin_root)
            deduped_roots = []
            for item in roots:
                try:
                    resolved = item.resolve()
                except OSError:
                    continue
                if resolved not in deduped_roots:
                    deduped_roots.append(resolved)
            platform = str(host.get('platform') or '').strip()

            # 模块自己的公开清单优先：module.json 有身份就不需要 AI 猜
            from agent_core.connectors.module_manifest import find_manifest
            manifest = find_manifest(module_id, str(host.get('root') or ''), str(PROJECT_ROOT))
            if manifest:
                manifest_data = manifest['data']
                account = manifest_data.get('account') if isinstance(manifest_data.get('account'), dict) else {}
                uid = str(account.get('uid') or manifest_data.get('uid') or '').strip()
                manifest_platform = str(
                    account.get('platform') or manifest_data.get('platform') or platform or 'other'
                ).strip()
                display_name = str(
                    account.get('name') or manifest_data.get('name') or uid
                ).strip()
                existing = next(
                    (
                        c for c in store.list_contexts()
                        if c.get('platform') == manifest_platform
                        and str(c.get('external_id') or '') == uid
                    ),
                    None,
                )
                if existing and str(existing.get('status') or '') == 'ignored':
                    return self._send_json({
                        'ok': True,
                        'source': 'module_manifest',
                        'ignored': True,
                        'users': [],
                    })
                context_id = store.upsert_context(
                    manifest_platform,
                    uid,
                    external_type='host',
                    display_name=display_name,
                    source='module_manifest',
                    evidence=[manifest['path']],
                    metadata={
                        'module_id': module_id,
                        'module_manifest': True,
                        'manifest_path': manifest['path'],
                        'capabilities': manifest_data.get('capabilities') or [],
                        'events': manifest_data.get('events') or [],
                    },
                    host_id=module_id,
                )
                return self._send_json({
                    'ok': True,
                    'source': 'module_manifest',
                    'users': [{
                        'uid': uid,
                        'external_id': uid,
                        'platform': manifest_platform,
                        'name': display_name,
                        'person_uid': uid,
                        'external_type': 'host',
                        'source': 'module_manifest',
                        'context_id': context_id,
                        'evidence': [manifest['path']],
                        'metadata': {
                            'module_id': module_id,
                            'module_manifest': True,
                            'capabilities': manifest_data.get('capabilities') or [],
                            'events': manifest_data.get('events') or [],
                        },
                    }],
                })

            # 没有宿主目录的独立模块（例如 B站模块）：用已有档案身份补信息卡
            if not deduped_roots:
                users = []
                seen = set()
                existing_by_key = {
                    (c.get('platform'), str(c.get('external_id') or '')): c
                    for c in store.list_contexts()
                }
                for archive in store.list_archives():
                    archive_id = str(archive.get('archive_id') or '')
                    archive_name = str(archive.get('display_name') or '')
                    for ident in store.list_identities(archive_id):
                        ident_platform = str(ident.get('platform') or '')
                        uid = str(ident.get('platform_user_id') or '').strip()
                        if not uid or (platform and ident_platform != platform):
                            continue
                        key = (ident_platform, uid)
                        if key in seen:
                            continue
                        seen.add(key)
                        existing = existing_by_key.get(key)
                        if existing and str(existing.get('status') or '') == 'ignored':
                            continue
                        display_name = str(ident.get('display_name') or archive_name or uid)
                        context_id = store.upsert_context(
                            ident_platform,
                            uid,
                            external_type='host',
                            display_name=display_name,
                            source='archive_identity',
                            evidence=['archive_identities'],
                            metadata={
                                'module_id': module_id,
                                'archive_identity': True,
                                'archive_id': archive_id,
                            },
                            host_id=module_id,
                        )
                        users.append({
                            'uid': uid,
                            'external_id': uid,
                            'platform': ident_platform,
                            'name': display_name,
                            'person_uid': uid,
                            'external_type': 'host',
                            'source': 'archive_identities',
                            'context_id': context_id,
                            'evidence': ['archive_identities'],
                            'metadata': {
                                'module_id': module_id,
                                'archive_identity': True,
                                'archive_id': archive_id,
                            },
                        })
                return self._send_json({
                    'ok': True,
                    'users': users,
                    'source': 'archive_identities',
                })

            try:
                result = asyncio.run(
                    AIMatcher(store=store, roots=deduped_roots).scan_all(platform)
                )
            except Exception as exc:
                return self._send_json(
                    {'ok': False, 'error': f'执行 AI 失败: {exc}', 'users': []}, status=500
                )
            contexts = store.list_contexts()
            by_key = {
                (c.get('platform'), c.get('external_id')): c for c in contexts
            }
            users = []
            for user in result.get('users') or []:
                uid = str(user.get('uid') or user.get('external_id') or '').strip()
                if not uid:
                    continue
                user_platform = str(user.get('platform') or platform or 'other')
                existing = by_key.get((user_platform, uid))
                if existing is None:
                    existing = next(
                        (
                            c for c in contexts
                            if c.get('platform') == user_platform
                            and str(c.get('external_id') or '').endswith(':' + uid)
                        ),
                        None,
                    )
                if existing and str(existing.get('status') or '') == 'ignored':
                    continue
                external_id = str((existing or {}).get('external_id') or uid)
                person_uid = str(user.get('person_uid') or ('' if user.get('secondary') else uid))
                external_type = str(
                    user.get('external_type')
                    or (existing or {}).get('external_type')
                    or ''
                )
                evidence = list(user.get('evidence') or [])
                metadata = {
                    'module_id': module_id,
                    'ai_scan': True,
                    'person_uid': person_uid,
                    'secondary': bool(user.get('secondary')),
                    'plugin': str(user.get('plugin') or ''),
                    'scan_source': str(user.get('source') or ''),
                    'extra': user.get('extra') if isinstance(user.get('extra'), dict) else {},
                    'evidence': evidence[:8],
                }
                context_id = store.upsert_context(
                    user_platform,
                    external_id,
                    external_type=external_type,
                    display_name=str(user.get('name') or ''),
                    source='execution_ai',
                    evidence=evidence,
                    metadata=metadata,
                    host_id=module_id,
                )
                user['external_id'] = external_id
                user['context_id'] = context_id
                user['metadata'] = metadata
                users.append(user)
            result['users'] = users
            return self._send_json(result)

        if path == '/api/contexts/status':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.storage import get_store

            context_id = str(body.get('context_id') or '').strip()
            status = str(body.get('status') or 'active').strip().lower()
            if not context_id or status not in ('active', 'ignored', 'stale'):
                return self._send_json(
                    {'ok': False, 'error': 'context_id 或 status 不合法'}, status=400
                )
            get_store().set_context_status(context_id, status)
            return self._send_json({'ok': True, 'context_id': context_id, 'status': status})

        if path == '/api/contexts/bind':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
            from agent_core.storage import get_store

            platform = str(body.get('platform') or '').strip()
            uid = str(body.get('uid') or body.get('external_id') or '').strip()
            archive_id = str(body.get('archive_id') or '').strip()
            module_id = str(body.get('module_id') or '').strip()
            if not platform or not uid or not archive_id:
                return self._send_json(
                    {'ok': False, 'error': 'platform/uid/archive_id 必填'}, status=400
                )
            store = get_store()
            if not store.get_archive(archive_id):
                return self._send_json({'ok': False, 'error': '档案不存在'}, status=404)
            context_id = store.upsert_context(
                platform, uid,
                external_type='',
                display_name=str(body.get('name') or ''),
                source='console_match',
                metadata={
                    'person_uid': uid,
                    'module_id': module_id,
                    'matched': True,
                },
                host_id=module_id,
            )
            try:
                confidence = float(body.get('confidence') or 1.0)
            except (TypeError, ValueError):
                confidence = 1.0
            binding_id = store.bind_context(
                platform, uid, archive_id,
                confidence=max(0.0, min(1.0, confidence)),
                verified_by='console_match',
            )
            return self._send_json({
                'ok': True, 'context_id': context_id, 'binding_id': binding_id,
            })

        if path == '/api/bindings':
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
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

        # 2. 大模型 API 网关 /v1 豁免 Web 登录
        if path == '/v1/chat/completions':
            pass
        elif path.startswith('/api/'):
            # 3. 控制台管理 API 验证登录
            if not self._is_authenticated():
                return self._send_json({"error": "未登录或登录已过期", "require_login": True}, status=401)

        if path == '/api/persona':
            if any(key in body for key in ('name', 'persona', 'dialogue_examples', 'other')):
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
                res = backend.save_persona_profile(fields)
            else:
                prompt = body.get('system_prompt', '')
                if not prompt:
                    return self._send_json({"error": "Prompt cannot be empty"}, status=400)
                res = backend.save_persona(prompt)
            self._send_json(res)
        elif path == '/api/persona/rules':
            res = backend.save_persona_rules(body)
            self._send_json(res)
        elif path == '/api/models/sync_upstream':
            res = backend.sync_models_from_upstream()
            self._send_json(res)
        elif path == '/api/models_config':
            res = backend.save_models_config(body)
            self._send_json(res)
        elif path == '/api/credits':
            res = backend.save_credits(body)
            self._send_json(res)
        elif path == '/api/modules/toggle':
            res = backend.toggle_module_active(body.get('module_id'), body.get('is_active', True))
            self._send_json(res)
        elif path == '/api/modules/category':
            res = backend.update_module_custom_category(body.get('module_id'), body.get('category'))
            self._send_json(res)
        elif path == '/api/modules/import':
            res = backend.import_custom_module(body)
            self._send_json(res)
        elif path == '/api/hosts/resolve':
            from agent_core.module_resolver import resolve_module

            from agent_core.storage import get_store

            try:
                result = resolve_module(
                    get_store(),
                    module_root=str(body.get('module_root') or body.get('host_root') or '').strip() or None,
                    hints=body.get('hints') if isinstance(body.get('hints'), dict) else {},
                    archive_id=str(body.get('archive_id') or '').strip() or None,
                )
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=400)
            self._send_json(result)
        elif path == '/api/modules/delete':
            res = backend.delete_custom_module(body.get('module_id'))
            self._send_json(res)
        elif path == '/api/modules/permissions':
            res = backend.save_module_permissions(body)
            self._send_json(res)
        elif path == '/api/tool-permissions':
            self._send_json({
                'ok': False,
                'deprecated': True,
                'error': '已废弃，请使用 /api/tools（工具页）',
            }, status=410)
        elif path == '/api/compute/config':
            res = backend.save_compute_config(body)
            self._send_json(res)
        elif path == '/api/compute/test':
            res = backend.test_compute_provider(body.get('provider_key', ''))
            self._send_json(res)
        elif path == '/api/connection/bind':
            from agent_core.storage import get_store

            store = get_store()
            module_id = str(body.get('module_id') or '').strip()
            archive_id = str(body.get('archive_id') or '').strip()
            host = next((h for h in store.list_hosts() if h.get('host_id') == module_id), None)
            if not host:
                return self._send_json({'ok': False, 'error': '模块不存在'}, status=404)
            platform = str(host.get('platform') or '').strip()
            external_id = str(host.get('internal_uid') or '').strip()
            if not platform or not external_id:
                return self._send_json(
                    {'ok': False, 'error': '模块缺少平台或内部 UID，请先识别/扫描'}, status=400
                )
            context_id = store.upsert_context(
                platform, external_id,
                external_type='host',
                display_name=str(host.get('name') or module_id),
                source='module_binding',
                evidence=['console_identity'],
                metadata={'host_id': module_id},
                host_id=module_id,
            )
            if not archive_id:
                store.unbind_context(platform, external_id)
                return self._send_json({'ok': True, 'context_id': context_id, 'binding': None})
            if not store.get_archive(archive_id):
                return self._send_json({'ok': False, 'error': '档案不存在'}, status=404)
            binding_id = store.bind_context(
                platform, external_id, archive_id,
                confidence=1.0, verified_by='console',
            )
            return self._send_json({'ok': True, 'context_id': context_id, 'binding_id': binding_id})
        elif path == '/api/execution_ai':
            from agent_core.execution.settings import save_settings

            try:
                saved = save_settings(
                    base_url=body.get('base_url'),
                    model=body.get('model'),
                    api_key=body.get('api_key'),
                    clear_api_key=bool(body.get('clear_api_key')),
                )
            except Exception as exc:
                return self._send_json({'ok': False, 'error': f'保存失败: {exc}'}, status=500)
            self._send_json({'ok': True, **saved})
        elif path == '/api/execution_ai/test':
            import asyncio

            from agent_core.execution.settings import test_connection

            try:
                result = asyncio.run(test_connection(
                    base_url=body.get('base_url'),
                    model=body.get('model'),
                    api_key=body.get('api_key'),
                ))
            except Exception as exc:
                return self._send_json({'ok': False, 'error': str(exc)}, status=500)
            self._send_json(result)
        elif path == '/api/modules/probe':
            mod_id = body.get('module_id')
            if mod_id:
                res = backend.probe_single_module(mod_id)
            else:
                res = backend.probe_all_modules()
            self._send_json(res)
        elif path == '/api/modules/topology/save':
            res = backend.save_topology_overview(body.get('nodes', {}), body.get('positions', {}))
            self._send_json(res)
        elif path == '/api/modules/topology/validate':
            res = backend.validate_topology_overview(body.get('relations', None))
            self._send_json(res)
        elif path in ('/v1/chat/completions', '/api/v1/chat/completions'):
            self._handle_universal_chat_completions(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self):
        path = self.path.split('?')[0]
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
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
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        if path.startswith('/api/'):
            if not self._is_authenticated():
                return self._send_json(
                    {"error": "未登录或登录已过期", "require_login": True}, status=401
                )
        if path.startswith('/hub/v1/services/'):
            _, err = self._hub_client(require_admin=True)
            if err:
                return self._hub_error(err)
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            service_id = path.rstrip('/').rsplit('/', 1)[-1]
            removed = ServiceRegistry(get_store()).delete(service_id)
            return self._send_json({'ok': True, 'removed': removed})

        if path == '/api/memory/facts':
            qs = parse_qs(parsed.query)
            fact_id = (qs.get('fact_id') or [''])[0].strip()
            if not fact_id:
                return self._send_json({'error': 'fact_id 必填'}, status=400)
            from agent_core.memory.facts import FactStore
            removed = FactStore().delete(fact_id)
            return self._send_json({'ok': True, 'removed': removed})

        # 档案自定义设定：删除
        if path.endswith('/settings') and '/api/archives/' in path:
            qs = parse_qs(parsed.query)
            setting_id = (qs.get('setting_id') or [''])[0].strip()
            if not setting_id:
                return self._send_json({'error': 'setting_id 必填'}, status=400)
            archive_id = path.rsplit('/', 2)[-2]
            from agent_core.profile import ProfileManager
            from agent_core.storage import get_store

            removed = ProfileManager(get_store()).delete_setting(archive_id, setting_id)
            return self._send_json({'ok': True, 'removed': removed})

        if path == '/api/bindings':
            qs = parse_qs(parsed.query)
            platform = (qs.get('platform') or [''])[0].strip()
            external_id = (qs.get('external_id') or [''])[0].strip()
            if not platform or not external_id:
                return self._send_json({'error': 'platform/external_id 必填'}, status=400)
            from agent_core.storage import get_store
            removed = get_store().unbind_context(platform, external_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path == '/api/bridges/spec':
            qs = parse_qs(parsed.query)
            source_id = (qs.get('source_id') or [''])[0].strip()
            if not source_id:
                return self._send_json({'error': 'source_id 必填'}, status=400)
            from agent_core.storage import get_store
            manager = _get_bridge_manager()
            try:
                manager.stop(source_id)
            except Exception:
                pass
            removed = get_store().delete_bridge_spec(source_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path.startswith('/api/plugins/'):
            from agent_core.plugins import PluginRegistry
            from agent_core.storage import get_store

            plugin_id = path.rsplit('/', 1)[-1]
            removed = PluginRegistry(get_store()).delete(plugin_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path.startswith('/api/services/'):
            from agent_core.services import ServiceRegistry

            from agent_core.storage import get_store

            service_id = path.rsplit('/', 1)[-1]
            removed = ServiceRegistry(get_store()).delete(service_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path.startswith('/api/knowledge/'):
            from agent_core.knowledge import KnowledgeBase
            from agent_core.storage import get_store

            source_id = path.rsplit('/', 1)[-1]
            removed = KnowledgeBase(get_store()).delete(source_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path.startswith('/api/components/'):
            from agent_core.storage import get_store

            component_id = path.rsplit('/', 1)[-1]
            removed = get_store().delete_component(component_id)
            return self._send_json({'ok': True, 'removed': removed})
        if path.startswith('/api/mcp/'):
            name = path.rsplit('/', 1)[-1]
            return self._send_json(_get_mcp_manager().close(name))
        self.send_response(404)
        self.end_headers()

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
        am = self._archive_manager()
        path = path.rstrip('/')

        if path == '/hub/v1/archives':
            platform = str(body.get('platform') or '').strip()
            uid = str(body.get('user_id') or body.get('platform_user_id') or '').strip()
            role = str(body.get('role') or 'guest').strip().lower()
            if role not in ('admin', 'guest'):
                role = 'guest'

            # 不填平台账号 → 创建一个尚未绑定平台的人物档案
            if not platform and not uid:
                display_name = str(body.get('display_name') or '').strip()
                if not display_name:
                    return self._hub_error('bad_request')
                archive = am.create_person(display_name, role=role)
                return self._send_json(self._archive_view(am, archive), status=201)

            if not platform or not uid:
                return self._hub_error('bad_request')
            existing = am.resolve(platform, uid, create=False)
            if existing:
                return self._send_json({
                    'error': {'message': '该平台账号已有档案', 'code': 'conflict'},
                    'archive': self._archive_view(am, existing),
                }, status=409)
            archive = am.create_user(
                platform, uid,
                display_name=body.get('display_name'),
                role=role,
            )
            return self._send_json(self._archive_view(am, archive), status=201)

        if path.endswith('/settings'):
            archive_id = path.rsplit('/', 2)[-2]
            if not am.get(archive_id):
                return self._hub_error('not_found')
            from agent_core.profile import ProfileManager

            pm = ProfileManager(am.store)

            # 大文本框整段保存：每行一条「标签：内容」，没有冒号就整行当内容
            if 'text' in body:
                text = str(body.get('text') or '')
                items = []
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    label, content = '', line
                    for sep in ('：', ':'):
                        if sep in line:
                            head, tail = line.split(sep, 1)
                            if head.strip() and len(head.strip()) <= 20:
                                label, content = head.strip(), tail.strip()
                            break
                    items.append((label or '设定', content))
                for old in pm.list_settings(archive_id):
                    pm.delete_setting(archive_id, old['setting_id'])
                saved = []
                for label, content in items:
                    saved.append(pm.upsert_setting(archive_id, label, content))
                return self._send_json({'ok': True, 'settings': saved, 'count': len(saved)})

            label = str(body.get('label') or '').strip()
            content = str(body.get('content') or '').strip()
            if not label:
                return self._send_json(
                    {'error': {'message': 'label 必填', 'code': 'bad_request'}}, status=400
                )
            try:
                setting = pm.upsert_setting(
                    archive_id,
                    label,
                    content,
                    setting_id=str(body.get('setting_id') or '').strip(),
                    sort_order=body.get('sort_order'),
                )
            except ValueError as exc:
                return self._send_json(
                    {'error': {'message': str(exc), 'code': 'bad_request'}}, status=400
                )
            return self._send_json({'ok': True, 'setting': setting})

        if path.endswith('/identities'):
            parts = path.split('/')
            archive_id = parts[-2] if len(parts) >= 2 else ''
            archive = am.get(archive_id)
            if not archive:
                return self._hub_error('not_found')
            platform = str(body.get('platform') or '').strip()
            uid = str(body.get('user_id') or body.get('platform_user_id') or '').strip()
            if not platform or not uid:
                return self._hub_error('bad_request')
            existing = am.resolve(platform, uid, create=False)
            if existing and existing['archive_id'] != archive_id:
                return self._hub_error('conflict')
            am.link(
                archive_id, platform, uid,
                display_name=body.get('display_name'),
                is_primary=bool(body.get('is_primary')),
            )
            return self._send_json(self._archive_view(am, am.get(archive_id)))

        return self._hub_error('not_found')

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

    def _handle_universal_chat_completions_legacy(self, body):
        """通用 OpenAI 兼容接口：通用接入 + 身份认主 + TimelineHub记忆与好感 + 算力推导 + 黄金法则后处理"""
        messages = body.get('messages', [])
        req_model = body.get('model') or 'gemini-3.8-flash'

        # 1. 识别来源通道
        channel_id = self.headers.get('X-Channel') or 'universal_proxy'
        channel_type = str(self.headers.get('X-Channel-Type') or '').strip().lower()

        # 2. 提取用户原始输入与干净文本脱水
        user_query = ""
        for m in reversed(messages):
            if m.get('role') == 'user':
                c = m.get('content')
                if isinstance(c, str):
                    user_query = c
                elif isinstance(c, list):
                    for sub in c:
                        if isinstance(sub, dict) and sub.get('type') == 'text':
                            user_query = sub.get('text', '')
                break

        raw_user_id = str(body.get('user') or self.headers.get('X-User-Id') or self.headers.get('X-Actor-Id') or '').strip()
        if not raw_user_id and user_query:
            sniff_id = re.search(r"\((\d{5,12})\):", user_query)
            if sniff_id:
                raw_user_id = sniff_id.group(1)

        if user_query:
            clean_q = re.sub(r"^\[.*?\]\s*", "", user_query)
            clean_q = re.sub(r"^.*?\(\d{5,12}\):\s*", "", clean_q).strip()
            if clean_q:
                user_query = clean_q

        # 3. 通用实体身份解析 (统一认主与访客识别)
        from agent_core.identity_binding import infer_platform
        platform_name = (
            self.headers.get('X-Platform')
            or str(body.get('platform') or '')
            or infer_platform(channel_id, self.headers.get('X-External-Id') or '')
            or 'web'
        )
        display_name = _header_text(self.headers.get('X-User-Name'))

        from agent_core.identity import IdentityResolver
        actor_id, actor_name, role_type = IdentityResolver.resolve_actor(platform_name, raw_user_id or "master", display_name)

        # 检查通道权限
        perms = backend.get_module_permissions().get(channel_id, {
            "allow_recall_memory": True,
            "allow_ingest_timeline": True
        })

        # 4. 接入时间轴记忆中枢 (TimelineHub) 提取动态好感度与精准记忆
        master_facts = []
        recalled_memories = []
        knowledge_chunks = []
        current_affection = None
        try:
            from agent_core.timeline_hub import TimelineHub
            th = TimelineHub()
            current_affection = th.get_user_affection(actor_id)
            if actor_id == "master":
                # 统一从 user_facts 读（档案维度），不再读 user_profiles.facts 的旧缓存
                try:
                    from agent_core.profile import ProfileManager
                    from agent_core.storage import get_store

                    master_facts = ProfileManager(get_store()).get_facts_for_prompt(
                        "master", limit=8
                    )
                except Exception:
                    prof = th.get_user_profile("master")
                    if prof:
                        master_facts = prof.get("facts", [])
            if perms.get("allow_recall_memory", True) and len(user_query) >= 2:
                group_chat = channel_type == "group" or "group" in channel_id.lower()
                master_private = (not group_chat and str(actor_id) == "master")
                recalls = th.recall_timeline(
                    actor_id=actor_id, query=user_query,
                    limit=(8 if (group_chat or master_private) else 2),
                    include_all_actors=master_private,
                    channel_id=channel_id,
                    include_channel=group_chat,
                    exclude_group=(not group_chat and not master_private),
                )
                if recalls:
                    recalled_memories = [f"[{r.event.time_str}] {r.event.narrative}" for r in recalls]
        except Exception:
            pass

        # 统一知识库：所有宿主通过 /v1 自动共享
        try:
            from agent_core.knowledge import KnowledgeBase
            from agent_core.storage import get_store

            for hit in KnowledgeBase(get_store()).search(user_query, limit=3):
                content = str(hit.get("content") or "").strip()
                if content:
                    knowledge_chunks.append(
                        f"[{hit.get('title') or '知识库'}] {content[:600]}"
                    )
        except Exception:
            pass

        # 5. 组装完整认知提示词 (基底人设 + 亲疏好感温差 + 长期偏好 + 时间轴召回)
        sys_prompt = ""
        try:
            from agent_core.persona import PersonaManager
            pm = PersonaManager()
            if pm.get_base_prompt():
                sys_prompt = pm.build_system_context(
                    actor_id=actor_id,
                    actor_name=actor_name,
                    role_type=role_type,
                    affection=current_affection,
                    master_facts=master_facts,
                    recalled_memories=recalled_memories,
                    knowledge=knowledge_chunks
                )
        except Exception:
            pass

        if not sys_prompt:
            persona_info = backend.get_current_persona()
            sys_prompt = persona_info.get('system_prompt', '')

        # 6. 双池算力调度检测与真实大模型调用
        is_logic_task = False
        compute_mgr = None
        try:
            from agent_core.compute_providers import compute_manager
            compute_mgr = compute_manager
            is_logic_task = compute_mgr.is_logic_heavy_task(user_query)
        except Exception:
            pass

        real_reply = None
        actual_p_in = 0
        actual_p_out = 0

        if compute_mgr and user_query:
            if is_logic_task:
                worker_msgs = [
                    {"role": "system", "content": "You are a top-tier software engineer and logical reasoning engine. Solve the problem directly, rigorously and clearly. Output clean markdown and code with zero fluff."},
                    {"role": "user", "content": user_query}
                ]
                worker_out, win, wout = compute_mgr.chat_completion("logic_slot", worker_msgs, current_port=PORT)
                if worker_out:
                    synthesis_query = f"幕后工兵已推导解决完毕，技术产物与结论如下：\n{worker_out}\n\n请以{_persona_name()}特有的从容、通透随性、至善而微厌世的口吻，把结论和改动交付给{_master_name()}（句末绝不带句号，纯口语）。"
                    synth_msgs = [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": synthesis_query}
                    ]
                    synth_out, sin, sout = compute_mgr.chat_completion("persona_slot", synth_msgs, current_port=PORT)
                    real_reply = synth_out or worker_out
                    actual_p_in = win + sin
                    actual_p_out = wout + sout
            else:
                active_msgs = [{"role": "system", "content": sys_prompt}] + [m for m in messages if m.get("role") != "system"]
                direct_out, din, dout = compute_mgr.chat_completion("persona_slot", active_msgs, current_port=PORT)
                if direct_out:
                    real_reply = direct_out
                    actual_p_in = din
                    actual_p_out = dout

        if real_reply:
            sample_reply = real_reply
        else:
            if is_logic_task:
                sample_reply = f"刚才把这个问题让幕后工兵跑了一遍推导，核心原因抓到了：关于「{user_query[:25]}」，改法和逻辑已经整理好，自己拿去看~"
            elif "你好" in user_query or "在吗" in user_query:
                sample_reply = "在呢，今天有什么想和我聊聊的吗"
            elif "谁" in user_query:
                sample_reply = "我是你的私人专属智能体内核，无论在哪里交互，我都始终是同一个我"
            else:
                sample_reply = f"关于你说的「{user_query[:20]}」，我已经同步到中枢记忆库了，随时可以继续往下聊"

        # 7. 经过代码防护罩的口语黄金法则后处理
        cleaned_reply = backend.clean_persona_reply(sample_reply)

        # 8. Token 统一记账与自传回灌
        p_in = actual_p_in or max(10, len(user_query) * 2 + len(sys_prompt) // 4)
        p_out = actual_p_out or max(5, len(cleaned_reply) * 2)
        backend.record_token_usage_event(req_model, p_in, p_out, source=channel_id)

        if perms.get("allow_ingest_timeline", True) and user_query:
            if is_logic_task:
                narrative_log = f"{actor_name}提出了复杂逻辑任务；{_persona_name()}调度幕后工兵推导并转述：「{cleaned_reply[:50]}」"
            else:
                narrative_log = f"{actor_name}说：「{user_query[:50]}」；{_persona_name()}回答：「{cleaned_reply[:50]}」"
            backend.ingest_timeline_event(
                source=channel_id,
                actor_name=actor_name,
                narrative=narrative_log,
                payload={
                    "actor_id": actor_id,
                    "importance": 2.0 if actor_id == "master" else 1.0,
                    "channel": channel_id,
                },
            )

        # 9. 返回标准 OpenAI 协议响应 (支持 SSE 流式与完整 JSON)
        is_stream = bool(body.get('stream', False))
        if is_stream:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            chunk_data = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": cleaned_reply},
                        "finish_reason": None
                    }
                ]
            }
            self.wfile.write(f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n".encode('utf-8'))
            self.wfile.flush()

            chunk_stop = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }
                ]
            }
            self.wfile.write(f"data: {json.dumps(chunk_stop, ensure_ascii=False)}\n\n".encode('utf-8'))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": cleaned_reply
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": p_in,
                "completion_tokens": p_out,
                "total_tokens": p_in + p_out
            }
        }
        self._send_json(resp)

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
        """取最后一条 user 消息的纯文本。"""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
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
        """向量召回 + FTS5 精确子串补充。"""
        from agent_core.memory.embedding import embed_text_list

        group_chat = channel_type == "group" or "group" in channel_id.lower()
        is_master = str(actor_id) in ("master", "master")
        recalls = timeline_hub.recall_timeline(
            actor_id=actor_id, query=user_query, person_id=person_id,
            query_embedding=embed_text_list(user_query),
            limit=(8 if (group_chat or is_master) else 2),
            include_all_actors=False, channel_id=channel_id,
            include_channel=(group_chat and not is_master),
            exclude_group=(not group_chat and not is_master),
        )
        lines = self._format_recall_lines(timeline_hub, recalls)
        lines.extend(self._recall_exact_hits(timeline_hub, user_query, person_id, recalls))
        return lines

    def _format_recall_lines(self, timeline_hub, recalls):
        """前两条带上原话，后面只给摘要。"""
        lines = []
        for index, recall in enumerate(recalls):
            line = f"[{recall.event.time_str}] {recall.event.narrative}"
            if index < 2 and getattr(recall.event, "detail_id", ""):
                original = self._read_recall_detail(timeline_hub, recall.event.detail_id)
                if original:
                    line += f"\n原话：{str(original)[:280]}"
            lines.append(line)
        return lines

    def _read_recall_detail(self, timeline_hub, detail_id):
        try:
            row = timeline_hub.store.get_detail(detail_id)
            data = json.loads(row["content"]) if row and row.get("content") else {}
            message = data.get("message") or {}
            return message.get("content") or data.get("content") or ""
        except Exception:
            return ""

    def _recall_exact_hits(self, timeline_hub, user_query, person_id, recalls):
        """FTS5 兜底：人名、标题、暗号这类精确词，向量召回容易漏。"""
        try:
            from agent_core.memory.facts import FactStore

            seen_ids = {item.event.event_id for item in recalls}
            rows = FactStore(timeline_hub.store).search_episodes(user_query, person_id=person_id, limit=4)
            return [
                f"[{row.get('time_str') or ''}] {row.get('narrative') or ''}（精确命中）"
                for row in rows if row["event_id"] not in seen_ids
            ]
        except Exception:
            return []

    def _gateway_context(self, body):
        request = self._parse_gateway_request(body)
        messages = request["messages"]
        req_model = request["model"]
        stream = request["stream"]
        trace_id = request["trace_id"]
        profile, auth_error = self._authenticate_gateway_client()
        if auth_error:
            return {"error": auth_error}
        client_id = profile.id
        channel_id, channel_type = self._resolve_channel_ids(profile)

        # 陌生模块首次通过 /v1 接入：后台登记为模块，并抓一次内部信息卡/二级插件
        self._maybe_auto_provision()

        user_query = self._strip_query_prefix(self._extract_user_query(messages))
        raw_user_id = self._extract_raw_user_id(body, user_query)
        platform_name = self._resolve_platform_name(body, channel_id)
        display_name = _header_text(self.headers.get("X-User-Name"))

        from agent_core.access_policy import load_policy
        access_policy = load_policy()
        actor_id, actor_name, role_type = self._resolve_gateway_actor(
            profile, platform_name, raw_user_id, display_name
        )
        perms = self._resolve_channel_perms(profile, channel_id)


        identity = self._resolve_archive_identity(
            body, actor_id, platform_name, raw_user_id, profile, perms, access_policy
        )
        if identity.get("error"):
            return {"error": identity["error"]}

        actor_id = identity["actor_id"]
        actor_name = identity["actor_name"]
        role_type = identity["role_type"]
        perms = identity["perms"]
        person_id = identity["person_id"]
        archive_id = identity["archive_id"]
        is_guest = identity["is_guest"]
        bound_archive = identity["bound_archive"]
        current_affection = identity["affection"]
        master_facts = identity["master_facts"]

        recalled_memories = []
        try:
            from agent_core.timeline_hub import TimelineHub
            th = TimelineHub()
            if not is_guest and perms.get("allow_recall_memory", True) and len(user_query) >= 2:
                recalled_memories = self._collect_recall_memories(
                    th, user_query, actor_id, person_id, channel_id, channel_type
                )
        except Exception:
            pass

        # 档案映射已在召回前完成；这里只记录命中方式
        bound_by = "default"
        if bound_archive:
            archive_id = bound_archive
            bound_by = "binding"
        elif actor_id == "master":
            bound_by = "master"
        elif archive_id:
            bound_by = "actor"

        # 事实层优先：确定的事实不靠语义碰运气；关系图补一跳
        graph_lines = []
        if not is_guest:
            try:
                from agent_core.memory.facts import FactStore
                fact_store = FactStore()
                fact_lines = fact_store.prompt_lines(person_id, user_query, limit=8)
                if fact_lines:
                    master_facts = list(master_facts or []) + fact_lines
                graph = fact_store.graph(person_id)
                for edge in graph.get("edges") or []:
                    graph_lines.append(
                        f"{actor_name} --{edge.get('predicate') or '关系'}--> "
                        f"{edge.get('object_value') or ''}"
                    )
            except Exception:
                pass

        # AI 人格只走「人格设定」的全局人设卡；档案不再承载人设
        sys_prompt = ""
        try:
            from agent_core.persona import PersonaManager
            pm = PersonaManager()
            if pm.get_base_prompt():
                sys_prompt = pm.build_system_context(
                    actor_id=actor_id, actor_name=actor_name, role_type=role_type,
                    affection=current_affection, master_facts=master_facts,
                    recalled_memories=recalled_memories,
                    relations=graph_lines,
                )
        except Exception:
            pass
        if not sys_prompt:
            sys_prompt = backend.get_current_persona().get("system_prompt", "")

        compute_mgr = None
        is_logic_task = False
        actual_model = ""
        try:
            from agent_core.compute_providers import compute_manager
            compute_mgr = compute_manager
            is_logic_task = compute_mgr.is_logic_heavy_task(user_query)
            actual_model = compute_mgr.resolve_model("persona_slot")
        except Exception:
            pass

        extra = {k: body[k] for k in self.PASSTHROUGH_FIELDS if body.get(k) is not None}

        # 工具按需加载：只把选中大类的工具交给主力 AI；判定无需工具时
        # tools / tool_choice / parallel_tool_calls 三个字段整段删除。
        decision = None
        tools_all = list(body.get("tools") or [])
        if tool_gate is not None and extra.get("tools"):
            try:
                # 权限硬拒绝：先按客户端把宿主机等敏感工具摘掉，再走分类裁剪
                denied = tool_gate.client_denied(
                    extra["tools"], client_id, getattr(profile, "tool_permissions", None)
                )
                if denied:
                    extra["tools"] = [
                        t for t in extra["tools"]
                        if ((t.get("function") or {}).get("name") or t.get("name")) not in denied
                    ]
                    if not extra["tools"]:
                        for key in ("tools", "tool_choice", "parallel_tool_calls"):
                            extra.pop(key, None)

                decision = tool_gate.decide(
                    messages,
                    extra.get("tools"),
                    channel_id=channel_id,
                    actor_id=actor_id,
                    client_id=client_id,
                    platform=platform_name,
                    channel_type=channel_type,
                    client_perms=getattr(profile, "tool_permissions", None),
                    # 档案级工具策略要的是本轮解析出的档案 ID，ctx 此时还没构建
                    archive_id=str(archive_id or ""),
                )
                if denied and not decision.applied:
                    decision.applied = True
                    decision.payload_tools = list(extra.get("tools") or [])
                    decision.tools_after = len(decision.payload_tools)
                    decision.drop_tool_fields = decision.tools_after == 0
                extra = tool_gate.apply(decision, extra)
                if decision.applied and decision.tools_after < decision.tools_before:
                    # 裁剪后某些客户端会带 tool_choice:"auto"，上游可能 400，统一剥掉
                    extra = tool_gate.strip_choice_fields(extra)
                tool_gate.remember(decision.session_key, messages)
            except Exception as _gate_err:
                print(f"[ToolShelves] 闸门跳过，全量透传: {_gate_err}", flush=True)
                decision = None

        # P3 结果压缩：工具刚返回的生肉在送进主力 AI 前先由执行 AI 清洗
        compress_info = None
        if tool_gate is not None and any(
            isinstance(m, dict) and m.get("role") == "tool" for m in messages
        ):
            try:
                comp_cfg = tool_gate.cfg.compression()
                if comp_cfg.get("enabled"):
                    from agent_core.tool_shelves.classifier import complete_once
                    from agent_core.tool_shelves.cleaner import compress_messages
                    from agent_core.tool_shelves.metrics import metrics as shelf_metrics

                    def _complete(system, user, cfg):
                        return complete_once(
                            system, user, tool_gate.cfg.get(),
                            slot=str(cfg.get("slot") or "logic_slot"),
                            timeout_ms=cfg.get("timeout_ms"),
                            temperature=cfg.get("temperature"),
                            max_tokens=cfg.get("max_tokens"),
                        )

                    messages, comp_records = compress_messages(
                        messages, comp_cfg, user_query=user_query, complete_once=_complete,
                    )
                    if comp_records:
                        compress_info = {
                            "tools": len(comp_records),
                            "raw_chars": sum(int(r.get("raw_chars") or 0) for r in comp_records),
                            "clean_chars": sum(int(r.get("clean_chars") or 0) for r in comp_records),
                            "truncated": sum(1 for r in comp_records if r.get("truncated")),
                            "model_calls": sum(1 for r in comp_records if r.get("model")),
                            "errors": sum(1 for r in comp_records if r.get("error")),
                            "records": comp_records,
                        }
                        shelf_metrics.record_compression(comp_records)
            except Exception as _comp_err:
                print(f"[ToolShelves] 结果压缩跳过，原文透传: {_comp_err}", flush=True)
                compress_info = None

        return {
            "body": body, "messages": messages, "req_model": req_model,
            "actual_model": actual_model,
            "channel_id": channel_id, "channel_type": channel_type, "stream": stream, "user_query": user_query,
            "actor_id": actor_id, "actor_name": actor_name, "role_type": role_type,
            "is_guest": is_guest,
            "perms": perms, "sys_prompt": sys_prompt, "compute_mgr": compute_mgr,
            "is_logic_task": is_logic_task, "extra": extra,
            "has_tools": bool(extra.get("tools")),
            "tools_all": tools_all,
            "tool_gate": decision.as_dict() if decision is not None else None,
            "tool_gate_state": {"decision": decision},
            "tool_compression": compress_info,
            "client_id": client_id,
            "trace_id": trace_id,
            "archive_id": archive_id,
            "person_id": person_id,
            "platform": platform_name,
            "sender_uid": raw_user_id,
            "affection": current_affection,
            "bound_by": bound_by,
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
        # 把本轮实际调用的工具名记进账本，方便按「通道 + 工具」归因
        names = []
        for _tc in (tool_calls or []):
            _fn = ((_tc or {}).get("function") or {}).get("name")
            if _fn and _fn not in names:
                names.append(str(_fn))
        tools = ",".join(names)
        if tools:
            has_tool_calls = True
        try:
            backend.record_token_usage_event(
                ctx.get("actual_model") or ctx["req_model"],
                p_in, p_out, source=ctx["channel_id"], tools=tools,
            )
        except Exception:
            pass
        if has_tool_calls or not content or not ctx["user_query"]:
            return None
        if not ctx["perms"].get("allow_ingest_timeline", True):
            return None
        event_id = None
        try:
            if ctx["is_logic_task"]:
                narrative = f"{ctx['actor_name']}提出了复杂逻辑任务；{_persona_name()}调度幕后工兵推导并转述：「{content[:50]}」"
            else:
                narrative = f"{ctx['actor_name']}说：「{ctx['user_query'][:50]}」；{_persona_name()}回答：「{content[:50]}」"
            evt = backend.ingest_timeline_event(
                source=ctx["channel_id"],
                actor_name=ctx["actor_name"],
                narrative=narrative,
                payload={
                    "actor_id": ctx["actor_id"],
                    "archive_id": ctx.get("archive_id"),
                    "person_id": ctx.get("person_id") or ctx["actor_id"],
                    "importance": 2.0 if ctx["actor_id"] == "master" else 1.0,
                    "channel": ctx["channel_id"],
                    "detail_id": detail_id,
                },
                session_window_seconds=0,
            )
            event_id = evt if isinstance(evt, str) else None
            if event_id and not ctx.get("is_guest"):
                try:
                    from agent_core.memory.facts import FactStore
                    store_facts = FactStore()
                    store_facts.index_event(event_id, narrative)
                    person_id = ctx.get("person_id") or ctx["actor_id"]
                    # 统一写入 user_facts（source=ai_extract）；不再写没人读的 memory_facts
                    stored = store_facts.store_extracted(
                        person_id,
                        ctx["user_query"],
                        use_ai=False,
                        source="ai_extract",
                    )
                    if not stored:
                        def _ai_extract():
                            try:
                                def _usage(p_in, p_out):
                                    backend.record_token_usage_event(
                                        "memory.fact_extraction",
                                        p_in,
                                        p_out,
                                        source="memory.facts",
                                    )

                                FactStore().store_extracted(
                                    person_id,
                                    ctx["user_query"],
                                    use_ai=True,
                                    source="ai_extract",
                                    usage_cb=_usage,
                                )
                            except Exception:
                                pass

                        threading.Thread(target=_ai_extract, daemon=True).start()

                    # 好感度自动评估（方案 A）：未定给初始值，已定给 delta
                    def _eval_affection():
                        try:
                            from agent_core.profile import ProfileManager
                            from agent_core.profile.affection_ai import (
                                estimate_affection_delta,
                                estimate_initial_affection,
                            )
                            from agent_core.storage import get_store

                            pm = ProfileManager(get_store())
                            current = pm.get_affection(person_id)
                            user_text = ctx.get("user_query") or ""
                            if current is None:
                                value = estimate_initial_affection(user_text, content)
                                if value is not None:
                                    pm.set_affection(
                                        person_id, value,
                                        reason="首次交流，AI 给出初始好感",
                                    )
                                return
                            result = estimate_affection_delta(user_text, content, int(current))
                            if result:
                                delta, reason = result
                                pm.update_affection(person_id, delta, reason)
                        except Exception:
                            pass

                    threading.Thread(target=_eval_affection, daemon=True).start()
                except Exception:
                    pass
        except Exception:
            pass
        return event_id

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
        message = None
        finish_reason = "stop"
        usage = {}
        compute_mgr = ctx["compute_mgr"]

        store = self._memory_store()
        inv_channel = self._l1_start(
            store, ctx, "channel", ctx["channel_id"],
            summary=(ctx["user_query"] or "")[:80],
            metadata={"model": ctx["req_model"], "stream": False, "archive_id": ctx.get("archive_id")},
        )
        # 记忆库要留"实际发给模型的东西"：system prompt 是中枢重建的，
        # 和 AstrBot 原样发来的 ctx["messages"] 里的 system 不是一回事
        self._l3(store, "llm_request", {
            "messages": ctx["messages"],
            "system_prompt": ctx["sys_prompt"],
            "tools": ctx["extra"].get("tools"),
            "model": ctx["req_model"],
        }, invocation_id=inv_channel)
        inv_model = None
        if store and compute_mgr and ctx["user_query"]:
            inv_model = self._l1_start(
                store, ctx, "model", "llm.persona",
                parent_id=inv_channel, summary="人格模型调用",
            )

        if compute_mgr and ctx["user_query"]:
            if ctx["is_logic_task"] and not ctx["has_tools"]:
                worker_msgs = [
                    {"role": "system", "content": "You are a top-tier software engineer and logical reasoning engine. Solve the problem directly, rigorously and clearly. Output clean markdown and code with zero fluff."},
                    {"role": "user", "content": ctx["user_query"]},
                ]
                worker_out, win, wout = compute_mgr.chat_completion(
                    "logic_slot", worker_msgs, current_port=PORT, timeout=90
                )
                if worker_out:
                    try:
                        backend.record_token_usage_event(
                            compute_mgr.resolve_model("logic_slot") or "logic_slot",
                            win, wout, source=ctx["channel_id"],
                        )
                    except Exception:
                        pass
                    synth_query = (
                        f"幕后工兵已推导解决完毕，技术产物与结论如下：\n{worker_out}\n\n"
                        f"请以{_persona_name()}特有的从容、通透随性、至善而微厌世的口吻，把结论和改动交付给{_master_name()}（句末绝不带句号，纯口语）。"
                    )
                    synth_out, sin, sout = compute_mgr.chat_completion(
                        "persona_slot",
                        [{"role": "system", "content": ctx["sys_prompt"]},
                         {"role": "user", "content": synth_query}],
                        current_port=PORT, timeout=90,
                    )
                    text = synth_out or worker_out
                    message = {"role": "assistant", "content": text}
                    usage = {"prompt_tokens": sin, "completion_tokens": sout}
            else:
                active_msgs = [{"role": "system", "content": ctx["sys_prompt"]}] + [
                    m for m in ctx["messages"] if m.get("role") != "system"
                ]
                extra = dict(ctx.get("extra") or {})
                # 工具由 AstrBot 负责：透传 tools，模型返回 tool_calls，AstrBot 执行
                message, finish_reason, usage, used_model = self._persona_request(
                    ctx, active_msgs, extra
                )
                # enforce_soft：裁剪后拿到空响应（既无正文也无 tool_calls）时，
                # 用上游全量工具重试一次，避免误裁把动作卡死。
                if (
                    message is not None
                    and not (message.get("content") or "").strip()
                    and not message.get("tool_calls")
                    and self._gate_auto_expand_allowed(ctx, self._gate_live(ctx))
                ):
                    self._gate_expand(ctx)
                    message, finish_reason, usage, used_model = self._persona_request(
                        ctx, active_msgs, dict(ctx.get("extra") or {})
                    )
                if used_model:
                    ctx["actual_model"] = used_model

        used_fallback = message is None
        if message is None:
            message = {"role": "assistant", "content": self._gateway_fallback(ctx)}

        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        if content and not tool_calls:
            content = backend.clean_persona_reply(content)

        out_message = {"role": "assistant", "content": content}
        if tool_calls:
            out_message["tool_calls"] = tool_calls
        if message.get("refusal"):
            out_message["refusal"] = message["refusal"]

        p_in = int(usage.get("prompt_tokens") or max(10, len(ctx["user_query"]) * 2 + len(ctx["sys_prompt"]) // 4))
        p_out = int(usage.get("completion_tokens") or max(5, len(content) // 2))

        # 工具货架：记录回复上下文、误裁统计与逐轮明细
        self._gate_note(ctx, content, tool_calls)
        gate_info = ctx.get("tool_gate")
        if gate_info:
            self._gate_log(ctx, dict(
                gate_info,
                trace=ctx.get("trace_id"),
                channel=ctx.get("channel_id"),
                actor=ctx.get("actor_id"),
                platform=ctx.get("platform"),
                tool_calls=[((tc.get("function") or {}).get("name")) for tc in (tool_calls or [])],
                expanded=bool(ctx.get("tool_expanded")),
                compression=ctx.get("tool_compression"),
            ))

        detail_id = None
        if store:
            detail_id = self._l3(store, "llm_response", {
                "message": out_message,
                "usage": usage,
                "finish_reason": finish_reason,
                "fallback": used_fallback,
                "tool_gate": gate_info,
            }, invocation_id=inv_model or inv_channel)
        event_id = self._gateway_record(
            ctx, content, p_in, p_out, bool(tool_calls), detail_id=detail_id or "",
            tool_calls=tool_calls,
        )

        if store:
            for tc in tool_calls or []:
                fn_name = ((tc.get("function") or {}).get("name")) or "unknown"
                inv_tool = self._l1_start(
                    store, ctx, "tool", fn_name,
                    parent_id=inv_model or inv_channel,
                    summary="客户端请求工具",
                )
                self._l3(store, "tool_args", tc, invocation_id=inv_tool)
                try:
                    store.finish_invocation(inv_tool, status="requested")
                except Exception:
                    pass

            status = "fallback" if used_fallback else "ok"
            for inv in (inv_model, inv_channel):
                if not inv:
                    continue
                try:
                    store.finish_invocation(
                        inv, status=status, event_id=event_id,
                        summary=f"{p_in}+{p_out} tokens",
                    )
                except Exception:
                    pass

        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": ctx["req_model"],
            "choices": [{
                "index": 0,
                "message": out_message,
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": p_in,
                "completion_tokens": p_out,
                "total_tokens": p_in + p_out,
            },
            "hub": self._hub_identity_payload(ctx),
        }
        self._send_json(resp, extra_headers=self._hub_identity_headers(ctx))

    def _gateway_stream(self, ctx):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in self._hub_identity_headers(ctx).items():
            self.send_header(key, str(value))
        self.end_headers()

        accum = _StreamAccumulator()
        wrote = False

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
        inv_model = None
        if store and ctx["compute_mgr"] and ctx["user_query"]:
            inv_model = self._l1_start(
                store, ctx, "model", "llm.persona",
                parent_id=inv_channel, summary="人格模型流式调用",
            )
        try:
            if ctx["compute_mgr"] and ctx["user_query"]:
                active_msgs = [{"role": "system", "content": ctx["sys_prompt"]}] + [
                    m for m in ctx["messages"] if m.get("role") != "system"
                ]
                for chunk in ctx["compute_mgr"].stream_chat_completion(
                    "persona_slot", active_msgs, current_port=PORT, timeout=180, extra=ctx["extra"]
                ):
                    wrote = True
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    accum.feed(chunk)

            if not wrote:
                fallback = self._gateway_fallback(ctx)
                accum.content.append(fallback)
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": ctx["req_model"],
                    "choices": [{"index": 0, "delta": {"content": fallback}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

            if not accum.saw_done:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            print(f"[Gateway] 流式异常: {e}", flush=True)

        content = "".join(accum.content)
        usage = accum.usage or {}
        p_in = int(usage.get("prompt_tokens") or max(10, len(ctx["user_query"]) * 2 + len(ctx["sys_prompt"]) // 4))
        p_out = int(usage.get("completion_tokens") or max(5, len(content) // 2))

        # 工具货架：流式路径同样记录上下文与决策
        self._gate_note(ctx, content, accum.tool_calls)
        gate_info = ctx.get("tool_gate")
        if gate_info:
            self._gate_log(ctx, dict(
                gate_info,
                trace=ctx.get("trace_id"),
                channel=ctx.get("channel_id"),
                actor=ctx.get("actor_id"),
                platform=ctx.get("platform"),
                stream=True,
                expanded=bool(ctx.get("tool_expanded")),
                compression=ctx.get("tool_compression"),
            ))

        detail_id = None
        if store:
            detail_id = self._l3(store, "llm_response", {
                "content": content,
                "usage": usage,
                "finish_reason": accum.finish_reason,
                "fallback": not wrote,
                "tool_gate": gate_info,
            }, invocation_id=inv_model or inv_channel)
        event_id = self._gateway_record(
            ctx, content, p_in, p_out, accum.tool_calls, detail_id=detail_id or "",
            tool_calls=accum.tool_calls,
        )

        if store:
            status = "ok" if wrote else "fallback"
            for inv in (inv_model, inv_channel):
                if not inv:
                    continue
                try:
                    store.finish_invocation(
                        inv, status=status, event_id=event_id,
                        summary=f"{p_in}+{p_out} tokens",
                    )
                except Exception:
                    pass


class _StreamAccumulator:
    """从上游 SSE 中累计文本/tool_calls/usage，用于流结束后记账与回灌"""

    def __init__(self):
        self.buf = b""
        self.content = []
        self.usage = {}
        self.finish_reason = None
        self.saw_done = False
        self.tool_calls = False

    def feed(self, chunk):
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                self.saw_done = True
                continue
            try:
                obj = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            if obj.get("usage"):
                self.usage = obj["usage"]
            for choice in obj.get("choices") or []:
                if choice.get("finish_reason"):
                    self.finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    self.content.append(delta["content"])
                if delta.get("tool_calls"):
                    self.tool_calls = True


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


def run_server():
    _start_aggregator()
    _start_memory_maintenance()
    _start_discovery()
    threading.Thread(target=_warm_resource_cache, daemon=True, name="resource-warmup").start()
    while True:
        try:
            server = ReusableThreadingServer(('0.0.0.0', PORT), ConsoleHandler)
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
