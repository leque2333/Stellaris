# -*- coding: utf-8 -*-
"""
Agent Hub Console HTTP Server
极简高性能控制台服务与标准 OpenAI 兼容投射网关 (/v1)
"""

import os
import sys
import json
import time
import uuid
import re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import backend

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("CONSOLE_PORT", 18890))
os.environ["CONSOLE_PORT"] = str(PORT)
STATIC_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(STATIC_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class ConsoleHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Channel, X-User-Id, X-User-Name')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Channel, X-User-Id, X-User-Name')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            html_file = os.path.join(STATIC_DIR, 'index.html')
            if os.path.exists(html_file):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                with open(html_file, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        elif path == '/api/overview':
            data = {
                "summary": backend.get_system_summary(),
                "current_persona": backend.get_current_persona(),
                "presets": backend.get_preset_personas(),
                "tokens": backend.get_token_analytics(),
                "models_config": backend.get_models_config(),
                "timeline": backend.get_timeline_events(limit=20),
                "modules": backend.get_modules_overview(),
                "modules_permissions": backend.get_module_permissions(),
                "compute_config": backend.get_compute_config()
            }
            self._send_json(data)
        elif path == '/api/timeline':
            self._send_json(backend.get_timeline_events(limit=50))
        elif path == '/api/persona':
            self._send_json(backend.get_current_persona())
        elif path == '/api/models/sync_upstream':
            res = backend.sync_models_from_upstream()
            self._send_json(res)
        elif path == '/api/models_config':
            self._send_json(backend.get_models_config())
        elif path == '/api/modules':
            self._send_json(backend.get_modules_overview())
        elif path == '/api/modules/topology':
            self._send_json(backend.get_topology_overview())
        elif path == '/api/modules/permissions':
            self._send_json(backend.get_module_permissions())
        elif path == '/api/compute/config':
            self._send_json(backend.get_compute_config())
        elif path in ('/v1/models', '/api/v1/models'):
            self._send_json(backend.get_universal_models_list())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            return self._send_json({"error": "Invalid JSON"}, status=400)

        if path == '/api/persona':
            prompt = body.get('system_prompt', '')
            if not prompt:
                return self._send_json({"error": "Prompt cannot be empty"}, status=400)
            res = backend.save_persona(prompt)
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
        elif path == '/api/modules/delete':
            res = backend.delete_custom_module(body.get('module_id'))
            self._send_json(res)
        elif path == '/api/modules/permissions':
            res = backend.save_module_permissions(body)
            self._send_json(res)
        elif path == '/api/compute/config':
            res = backend.save_compute_config(body)
            self._send_json(res)
        elif path == '/api/compute/test':
            res = backend.test_compute_provider(body.get('provider_key', ''))
            self._send_json(res)
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

    def _handle_universal_chat_completions(self, body):
        """通用 OpenAI 兼容接口：通用接入 + 身份认主 + TimelineHub记忆与好感 + 算力推导 + 黄金法则后处理"""
        messages = body.get('messages', [])
        req_model = body.get('model') or 'gemini-3.8-flash'
        
        # 1. 识别来源通道
        channel_id = self.headers.get('X-Channel') or 'universal_proxy'

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
        platform_name = self.headers.get('X-Platform') or 'web'
        display_name = self.headers.get('X-User-Name') or ''

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
        current_affection = 90 if actor_id == "master" else 50
        try:
            from agent_core.timeline_hub import TimelineHub
            th = TimelineHub()
            current_affection = th.get_user_affection(actor_id)
            if actor_id == "master":
                prof = th.get_user_profile("master")
                if prof:
                    master_facts = prof.get("facts", [])
            if perms.get("allow_recall_memory", True) and len(user_query) >= 2:
                recalls = th.recall_timeline(actor_id=actor_id, query=user_query, limit=2)
                if recalls:
                    recalled_memories = [f"[{r.event.time_str}] {r.event.narrative}" for r in recalls]
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
                    recalled_memories=recalled_memories
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
                    synthesis_query = f"幕后工兵已推导解决完毕，技术产物与结论如下：\n{worker_out}\n\n请以助手特有的从容、通透随性、至善而微厌世的口吻，把结论和改动交付给主人（句末绝不带句号，纯口语）。"
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
        backend.record_token_usage_event(channel_id, req_model, p_in, p_out, duration_ms=120)

        if perms.get("allow_ingest_timeline", True) and user_query:
            if is_logic_task:
                narrative_log = f"{actor_name}提出了复杂逻辑任务；助手调度幕后工兵推导并转述：「{cleaned_reply[:50]}」"
            else:
                narrative_log = f"{actor_name}说：「{user_query[:50]}」；助手回答：「{cleaned_reply[:50]}」"
            backend.ingest_timeline_event(
                source_module=channel_id,
                actor_name=actor_name,
                narrative=narrative_log,
                actor_id=actor_id,
                importance=2.0 if actor_id == "master" else 1.0
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


class ReusableThreadingServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server():
    while True:
        try:
            server = ReusableThreadingServer(('0.0.0.0', PORT), ConsoleHandler)
            print(f"🚀 控制台中枢已启动: http://localhost:{PORT}", flush=True)
            server.serve_forever()
        except Exception as e:
            print(f"⚠️ 服务异常中断: {e}，将在 1 秒后自动重启...", flush=True)
            time.sleep(1)
        except BaseException as be:
            print(f"⚠️ 捕获系统退出信号: {be}，等待 1 秒后恢复常驻...", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    run_server()
