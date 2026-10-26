import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import backend

PORT = 8890
STATIC_DIR = os.path.dirname(__file__)

class ConsoleHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/' or path == '/index.html':
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
                "patrol_config": backend.get_patrol_config(),
                "tokens": backend.get_token_analytics(),
                "timeline": backend.get_timeline_events(limit=20)
            }
            self._send_json(data)
        elif path == '/api/timeline':
            self._send_json(backend.get_timeline_events(limit=50))
        elif path == '/api/persona':
            self._send_json(backend.get_current_persona())
        elif path == '/api/patrol_config':
            self._send_json(backend.get_patrol_config())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception as e:
            return self._send_json({"error": "Invalid JSON"}, status=400)

        if path == '/api/persona':
            prompt = body.get('system_prompt', '')
            if not prompt:
                return self._send_json({"error": "Prompt cannot be empty"}, status=400)
            res = backend.save_persona(prompt)
            self._send_json(res)
        elif path == '/api/patrol_config':
            res = backend.save_patrol_config(body)
            self._send_json(res)
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    server = HTTPServer(('0.0.0.0', PORT), ConsoleHandler)
    print(f"🚀 控制台中枢已启动: http://localhost:{PORT}")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
