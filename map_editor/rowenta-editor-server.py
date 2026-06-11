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
import ipaddress
import threading
import webbrowser
import urllib.request
import urllib.error
import urllib.parse
import mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

ROBOT_PORT   = 8080
DEFAULT_PORT = 8765
HTML_FILE    = Path(__file__).parent / "rowenta-map-editor.html"
STATIC_DIR   = Path(__file__).parent

# Shared mutable config — safe because GIL + single write path
_config = {"robot_ip": "", "port": DEFAULT_PORT}

# Loopback host names accepted in the Host header (anti DNS-rebinding) and as
# the Origin host (anti cross-site request) while the server is bound locally.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Set in main(): True when bound to a loopback address.  While True the request
# guard rejects non-loopback Host headers and cross-origin requests so that no
# other LAN host or web page can drive the robot proxy.
_enforce_local = True

# Opener that refuses to follow HTTP redirects — prevents a malicious/compromised
# target from bouncing a proxied request to an arbitrary host (SSRF hardening).
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_PROXY_OPENER = urllib.request.build_opener(_NoRedirect)


def _validate_robot_ip(raw):
    """Return a normalised robot IP if it is a usable private LAN address, else None.

    Accepts only private unicast addresses.  Rejects public, loopback,
    link-local (e.g. cloud metadata 169.254.169.254), the unspecified address
    (0.0.0.0 — connecting to it targets the local host, a local-SSRF path),
    and multicast/reserved addresses.  This closes the vector where an attacker
    repoints the proxy at an arbitrary or local endpoint.

    IPv4-mapped IPv6 (e.g. ::ffff:192.168.1.50 / ::ffff:169.254.169.254) is
    normalised to its embedded IPv4 form before validation, so a mapped address
    cannot smuggle a loopback/link-local target past the checks below.
    """
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(str(raw).strip())
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if (
        not addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    ):
        return None
    return str(addr)


# Content types the proxy is willing to echo from the robot.  The robot's API
# only ever returns JSON or plain text; anything else (notably text/html) must
# not be served on the proxy's own origin, where a compromised device could
# otherwise deliver executable script.  CR/LF are stripped to block header
# injection via a malicious Content-Type value.
_SAFE_PROXY_CONTENT_TYPES = ("application/json", "text/plain")


def _safe_proxy_content_type(raw):
    base = (
        str(raw or "")
        .split(";", 1)[0]
        .replace("\r", "")
        .replace("\n", "")
        .strip()
        .lower()
    )
    if base in _SAFE_PROXY_CONTENT_TYPES:
        return base + "; charset=utf-8"
    return "text/plain; charset=utf-8"


def _host_label(header_value):
    """Extract the bare hostname from a Host/Origin header value (drop port, brackets)."""
    h = (header_value or "").strip().lower()
    if h.startswith("["):                 # [::1] or [::1]:8765
        return h[1:].split("]", 1)[0]
    if h.count(":") == 1:                 # 127.0.0.1:8765 / localhost:8765
        return h.rsplit(":", 1)[0]
    return h                              # bare hostname or raw IPv6


def _host_ok(host_header):
    """True when the Host header names a loopback host (DNS-rebinding guard)."""
    if not _enforce_local:
        return True
    return _host_label(host_header) in _LOOPBACK_HOSTS


def _origin_ok(origin_header):
    """True when the request has no Origin or a loopback Origin (cross-site guard)."""
    if not _enforce_local or not origin_header:
        return True
    try:
        host = urllib.parse.urlparse(origin_header).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOOPBACK_HOSTS


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        path   = args[0] if args else ''
        status = args[1] if len(args) > 1 else ''
        tag = "-> robot" if ('/get/' in str(path) or '/set/' in str(path)) else "   local"
        print(f"  {tag}  {path}  [{status}]", flush=True)

    # ── Strip HA ingress prefix (e.g. /api/hassio_ingress/abc123/get/maps) ──
    def _clean_path(self, raw):
        # HA ingress injects a token path segment before our routes.
        # Normalise to bare /get/... or /set/... path.
        for marker in ('/get/', '/set/', '/js/', '/rowenta-map-editor.css', '/config'):
            idx = raw.find(marker)
            if idx >= 0:
                return raw[idx:]
        return raw

    def _guard(self):
        """Reject requests that could come from another LAN host or web page.

        Returns True when the request may proceed.  Sends a 403 and returns
        False otherwise.  Active only while the server is bound to loopback.
        """
        if not _host_ok(self.headers.get('Host')):
            self._respond(403, 'application/json',
                          json.dumps({"error": "Host not allowed"}).encode())
            return False
        if not _origin_ok(self.headers.get('Origin')):
            self._respond(403, 'application/json',
                          json.dumps({"error": "Cross-origin request rejected"}).encode())
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
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
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path   = self._clean_path(parsed.path)

        # Allow updating robot IP dynamically from the UI
        if path == '/config':
            try:
                length = int(self.headers.get('Content-Length', 0))
            except (TypeError, ValueError):
                length = 0
            # The /config payload is a tiny JSON object; reject anything large or
            # malformed instead of allocating an unbounded (or negative→EOF) read.
            if length < 0 or length > 64 * 1024:
                self._respond(400, 'application/json', json.dumps(
                    {"error": "Invalid Content-Length."}
                ).encode())
                return
            body   = self.rfile.read(length)
            try:
                data = json.loads(body)
                if 'robot_ip' in data:
                    candidate = _validate_robot_ip(data['robot_ip'])
                    if candidate is None:
                        self._respond(400, 'application/json', json.dumps(
                            {"error": "Invalid robot IP — must be a private LAN address."}
                        ).encode())
                        return
                    _config['robot_ip'] = candidate
                    print(f"  config  robot_ip updated -> {_config['robot_ip']}", flush=True)
                self._respond(200, 'application/json',
                              json.dumps({"ok": True}).encode())
            except Exception as e:
                self._respond(400, 'application/json',
                              json.dumps({"error": str(e)}).encode())
            return

        self._respond(404, 'text/plain', b'Not found')

    def do_OPTIONS(self):
        if not self._guard():
            return
        self.send_response(204)
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
        ip = _validate_robot_ip(_config.get('robot_ip', ''))
        if not ip:
            self._respond(502, 'application/json',
                          json.dumps({"error": "Robot IP not set or not a valid private LAN address. Enter it in the editor UI or pass it as a command-line argument."}).encode())
            return

        # Bracket IPv6 literals so the URL is well-formed (http://[fd00::1]:8080/…).
        host = f"[{ip}]" if ":" in ip else ip
        url = f"http://{host}:{ROBOT_PORT}{path}"
        if query:
            url += '?' + query

        try:
            req = urllib.request.Request(url)
            with _PROXY_OPENER.open(req, timeout=15) as resp:
                body  = resp.read()
                # Never echo the robot's Content-Type verbatim — constrain it to a
                # safe, non-executable allowlist (CRLF stripped) so a compromised
                # device can't serve HTML/script on the proxy origin.
                ctype = _safe_proxy_content_type(resp.headers.get('Content-Type'))
                self._respond(resp.status, ctype, body)
        except urllib.error.HTTPError as e:
            self._respond(e.code, 'application/json', e.read() or b'{}')
        except urllib.error.URLError as e:
            msg = json.dumps({"error": str(e.reason)}).encode()
            self._respond(502, 'application/json', msg)
        except Exception as e:
            self._respond(500, 'application/json',
                          json.dumps({"error": str(e)}).encode())

    def _respond(self, status, ctype, body):
        try:
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', len(body))
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('Pragma', 'no-cache')
            # Stop the browser from MIME-sniffing a proxied body into HTML/script.
            self.send_header('X-Content-Type-Options', 'nosniff')
            # No Access-Control-Allow-Origin: the UI is served same-origin via this
            # proxy, so CORS is unnecessary; a wildcard would let any web page read
            # robot data and drive the proxy cross-origin.
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers sometimes cancel an in-flight request during reload,
            # navigation, or devtools probing. Nothing is wrong with the robot
            # proxy in that case, so keep the server console quiet.
            pass


def main():
    parser = argparse.ArgumentParser(
        description='Rowenta Map Editor — local proxy server')
    parser.add_argument('robot_ip', nargs='?', default=None,
                        help='Robot IP, e.g. 192.168.1.50')
    parser.add_argument('--port', '-p', type=int, default=DEFAULT_PORT)
    parser.add_argument('--host', default='127.0.0.1',
                        help='Bind address (default 127.0.0.1). Use 0.0.0.0 only '
                             'when running behind a trusted front-end such as the '
                             'Home Assistant add-on ingress.')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()

    if args.robot_ip:
        validated = _validate_robot_ip(args.robot_ip)
        if validated is None:
            print(f"\n✗  Invalid robot IP: {args.robot_ip} "
                  "(must be a private LAN address, e.g. 192.168.1.50)\n")
            sys.exit(1)
        _config['robot_ip'] = validated

    # Enforce loopback-only request checks unless explicitly bound elsewhere.
    global _enforce_local
    try:
        _enforce_local = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        _enforce_local = args.host.strip().lower() in _LOOPBACK_HOSTS

    if not HTML_FILE.exists():
        print(f"\n✗  HTML file not found: {HTML_FILE}")
        print("   Put rowenta-map-editor.html in the same folder as this script.\n")
        sys.exit(1)

    url = f"http://localhost:{args.port}"

    print()
    print("  +----------------------------------------------+")
    print("  |  Rowenta Map Editor                          |")
    print("  +----------------------------------------------+")
    print(f"  Open:   {url}")
    if _config['robot_ip']:
        print(f"  Robot:  {_config['robot_ip']}:{ROBOT_PORT}")
    else:
        print("  Robot:  enter IP in the browser UI")
    print("  Stop:   Ctrl+C")
    if not _enforce_local:
        print()
        print(f"  ⚠  Bound to {args.host} — the editor and robot proxy are reachable")
        print("     by other hosts on this network. Only do this behind a trusted")
        print("     front-end (e.g. HA add-on ingress).")
    print()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == '__main__':
    main()
