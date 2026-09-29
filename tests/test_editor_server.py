"""Unit tests for the map-editor proxy server's IP validation.

The server file has a hyphenated name and is not a normal importable module,
so load it by path.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "map_editor" / "rowenta-editor-server.py"
)
_LAUNCHER_PATH = _SERVER_PATH.with_name("launch-rowenta-editor.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("rowenta_editor_server", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def server():
    return _load_server()


@pytest.mark.parametrize("ip", [
    "192.168.1.50",
    "10.0.0.5",
    "172.16.0.1",
])
def test_validate_accepts_private_lan(server, ip):
    assert server._validate_robot_ip(ip) == ip


@pytest.mark.parametrize("ip", [
    "0.0.0.0",            # unspecified — connecting targets localhost (local SSRF)
    "127.0.0.1",          # loopback
    "169.254.169.254",    # link-local / cloud metadata
    "8.8.8.8",            # public
    "224.0.0.1",          # multicast
    "240.0.0.1",          # reserved
    "not-an-ip",
    "",
    None,
    "::1",                          # IPv6 loopback
    "::ffff:127.0.0.1",             # IPv4-mapped loopback
    "::ffff:169.254.169.254",       # IPv4-mapped link-local (cloud metadata)
    "::ffff:8.8.8.8",               # IPv4-mapped public
    "2001:4860:4860::8888",         # public IPv6
])
def test_validate_rejects_unusable(server, ip):
    assert server._validate_robot_ip(ip) is None


@pytest.mark.parametrize("ip,expected", [
    ("::ffff:192.168.1.50", "192.168.1.50"),   # mapped private → normalised to IPv4
    ("fd00::1", "fd00::1"),                      # native private (ULA) IPv6
])
def test_validate_accepts_ipv6_private(server, ip, expected):
    assert server._validate_robot_ip(ip) == expected


@pytest.mark.parametrize("port", [1, 8080, 9080, 65535, "9080"])
def test_validate_robot_port_accepts_range(server, port):
    assert server._validate_robot_port(port) == int(port)


@pytest.mark.parametrize("port", [0, 65536, -1, "9080.5", "abc", True, 9080.5, None])
def test_validate_robot_port_rejects_invalid(server, port):
    assert server._validate_robot_port(port) is None


@pytest.fixture
def editor_http(server):
    previous = dict(server._config)
    server._config.update(robot_ip="192.168.1.50", robot_port=8080)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", server
    finally:
        http.shutdown()
        worker.join(timeout=5)
        http.server_close()
        server._config.clear()
        server._config.update(previous)


def _post_config(base, data):
    req = Request(
        base + "/config",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urlopen(req, timeout=5)


def test_editor_config_updates_robot_port_and_proxy_target(editor_http):
    base, server = editor_http
    with _post_config(base, {"robot_ip": "192.168.1.51", "robot_port": 9080}) as response:
        assert json.load(response) == {"ok": True}
    with urlopen(base + "/config", timeout=5) as response:
        config = json.load(response)
    assert config["robot_ip"] == "192.168.1.51"
    assert config["robot_port"] == 9080

    robot_response = MagicMock()
    robot_response.__enter__.return_value = robot_response
    robot_response.status = 200
    robot_response.read.return_value = b"{}"
    robot_response.headers.get.return_value = "application/json"
    with patch.object(server._PROXY_OPENER, "open", return_value=robot_response) as opener:
        with urlopen(base + "/get/status", timeout=5) as response:
            assert response.read() == b"{}"
    assert opener.call_args.args[0].full_url == "http://192.168.1.51:9080/get/status"


def test_editor_config_rejects_bad_port_without_changing_endpoint(editor_http):
    base, server = editor_http
    with pytest.raises(HTTPError) as error:
        _post_config(base, {"robot_ip": "192.168.1.51", "robot_port": 65536})
    assert error.value.code == 400
    assert server._config["robot_ip"] == "192.168.1.50"
    assert server._config["robot_port"] == 8080


def test_launcher_forwards_distinct_editor_and_robot_ports():
    spec = importlib.util.spec_from_file_location("rowenta_editor_launcher", _LAUNCHER_PATH)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    with patch.object(launcher.subprocess, "call", return_value=0) as call:
        assert launcher._run_cli([
            "192.168.1.50", "--port", "9000", "--robot-port", "9080", "--no-browser",
        ]) == 0
    command = call.call_args.args[0]
    assert command[command.index("--port") + 1] == "9000"
    assert command[command.index("--robot-port") + 1] == "9080"
