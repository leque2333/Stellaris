# -*- coding: utf-8 -*-
"""最小 OpenAI 兼容上游，用于网关回归测试（不依赖真实 Key）"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send_json({"object": "list", "data": [
                {"id": "mock-model", "object": "model", "owned_by": "mock"}
            ]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            base = {
                "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                "created": 1700000000, "model": body.get("model", "mock-model"),
            }
            for piece in ["流", "式", "测", "试"]:
                chunk = dict(base)
                chunk["choices"] = [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            final = dict(base)
            final["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            final["usage"] = {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
            self.wfile.write(f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        tools = body.get("tools") or []
        content = (
            f"echo_tools={bool(tools)};"
            f"temp={body.get('temperature')};"
            f"max={body.get('max_tokens')};"
            f"stop={body.get('stop')}"
        )
        message = {"role": "assistant", "content": content}
        finish = "stop"
        if tools:
            message["tool_calls"] = [{
                "id": "call_mock_1",
                "type": "function",
                "function": {"name": "mock_tool", "arguments": "{\"x\": 1}"},
            }]
            finish = "tool_calls"
        self._send_json({
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 1700000000,
            "model": body.get("model", "mock-model"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28},
        })


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]
