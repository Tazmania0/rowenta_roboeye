#!/usr/bin/env python3
"""
Rowenta Map Editor — Proxy Server
==================================
Usage (standalone):
  python3 rowenta-editor-server.py [robot-ip] [--port 8765]

Usage (HA add-on):  launched automatically by run.sh

Serves the editor HTML and proxies /get/* and /set/* to the robot.
No third-party packages — stdlib only (Python 3.6+).

Modes:
  • PROXY_MODE  (localhost)  — full proxy, IP set by arg or UI
  • INGRESS_MODE (HA add-on) — same, but strips the HA ingress path prefix
                               and robot IP can be updated live via /config POST
"""

import sys
import os
import json
import argparse
import threading
import webbrowser
import urllib.request
import urllib.error
import urllib.parse
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

ROBOT_PORT   = 8080
DEFAULT_PORT = 8765
HTML_FILE    = Path(__file__).parent / "rowenta-map-editor.html"
STATIC_DIR   = Path(__file__).parent

# Shared mutable config — safe because GIL + single write path
_config = {"robot_ip": "", "port": DEFAULT_PORT}


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        path   = args[0] if args else ''
        status = args[1] if len(args) > 1 else ''
        tag = "→ robot" if ('/get/' in str(path) or '/set/' in str(path)) else "  local"
        print(f"  {tag}  {path}  [{status}]", flush=True)

    # ── Strip HA ingress prefix (e.g. /api/hassio_ingress/abc123/get/maps) ──
    def _clean_path(self, raw):
        # HA ingress injects a token path segment before our routes.
        # Normalise to bare /get/... or /set/... path.
        for marker in ('/get/', '/set/', '/config', '/js/', '/rowenta-map-editor.css'):
            idx = raw.find(marker)
            if idx >= 0:
                return raw[idx:]
        return raw

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = self._clean_path(parsed.path)
        query  = parsed.query

        if path == '/' or path.endswith('.html'):
            self._serve_editor()
            return

        if path == '/config':
            body = json.dumps({
                "robot_ip": _config["robot_ip"],
                "proxy_mode": True,
            }).encode()
            self._respond(200, 'application/json', body)
            return

        if path.startswith('/get/') or path.startswith('/set/'):
            self._proxy(path, query)
            return

        # Serve static assets (CSS, JS modules)
        if path.endswith('.css') or path.endswith('.js'):
            self._serve_static(path)
            return

        self._respond(404, 'text/plain', b'Not found')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = self._clean_path(parsed.path)

        # Allow updating robot IP dynamically from the UI
        if path == '/config':
            length = int(self.headers.get('Content-Length', 0))
            body   = self.rfile.read(length)
            try:
                data = json.loads(body)
                if 'robot_ip' in data:
                    _config['robot_ip'] = data['robot_ip'].strip()
                    print(f"  config  robot_ip updated → {_config['robot_ip']}", flush=True)
                self._respond(200, 'application/json',
                              json.dumps({"ok": True}).encode())
            except Exception as e:
                self._respond(400, 'application/json',
                              json.dumps({"error": str(e)}).encode())
            return

        self._respond(404, 'text/plain', b'Not found')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _serve_editor(self):
        if not HTML_FILE.exists():
            self._respond(404, 'text/plain',
                          f"Not found: {HTML_FILE}".encode())
            return
        body = HTML_FILE.read_bytes()
        self._respond(200, 'text/html; charset=utf-8', body)

    def _serve_static(self, path):
        # Resolve relative to STATIC_DIR, prevent directory traversal
        rel = path.lstrip('/')
        file_path = (STATIC_DIR / rel).resolve()
        try:
            file_path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._respond(403, 'text/plain', b'Forbidden')
            return
        if not file_path.exists() or not file_path.is_file():
            self._respond(404, 'text/plain', f'Not found: {rel}'.encode())
            return
        ctype = mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream'
        body = file_path.read_bytes()
        self._respond(200, ctype, body)

    def _proxy(self, path, query):
        ip = _config.get('robot_ip', '').strip()
        if not ip:
            self._respond(502, 'application/json',
                          json.dumps({"error": "Robot IP not set. Enter it in the editor UI or pass it as a command-line argument."}).encode())
            return

        url = f"http://{ip}:{ROBOT_PORT}{path}"
        if query:
            url += '?' + query

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body  = resp.read()
                ctype = resp.headers.get('Content-Type', 'application/json')
                self._respond(resp.status, ctype, body)
        except urllib.error.HTTPError as e:
            self._respond(e.code, 'application/json', e.read() or b'{}')
        except urllib.error.URLError as e:
            msg = json.dumps({"error": str(e.reason)}).encode()
            self._respond(502, 'application/json', msg)
        except Exception as e:
            self._respond(500, 'application/json',
                          json.dumps({"error": str(e)}).encode())

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _respond(self, status, ctype, body):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(
        description='Rowenta Map Editor — local proxy server')
    parser.add_argument('robot_ip', nargs='?', default=None,
                        help='Robot IP, e.g. 192.168.1.50')
    parser.add_argument('--port', '-p', type=int, default=DEFAULT_PORT)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()

    if args.robot_ip:
        _config['robot_ip'] = args.robot_ip

    if not HTML_FILE.exists():
        print(f"\n✗  HTML file not found: {HTML_FILE}")
        print("   Put rowenta-map-editor.html in the same folder as this script.\n")
        sys.exit(1)

    url = f"http://localhost:{args.port}"

    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║  Rowenta Map Editor                          ║")
    print("  ╚══════════════════════════════════════════════╝")
    print(f"  Open:   {url}")
    if _config['robot_ip']:
        print(f"  Robot:  {_config['robot_ip']}:{ROBOT_PORT}")
    else:
        print(f"  Robot:  — enter IP in the browser UI —")
    print("  Stop:   Ctrl+C")
    print()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server = HTTPServer(('', args.port), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == '__main__':
    main()
