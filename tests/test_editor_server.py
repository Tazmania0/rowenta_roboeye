"""Unit tests for the map-editor proxy server's IP validation.

The server file has a hyphenated name and is not a normal importable module,
so load it by path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "map_editor" / "rowenta-editor-server.py"
)


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
