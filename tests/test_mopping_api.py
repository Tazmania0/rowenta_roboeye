"""Tests for mopping, map-editor, and HTTP-auth additions to the API client."""
from __future__ import annotations

import base64

import pytest

from custom_components.rowenta_roboeye.api import AuthFailed, RobEyeApiClient
from custom_components.rowenta_roboeye.const import (
    DEFAULT_AUTH_USERNAME,
    validate_http_password,
)

from .test_api import _mock_response, _patch_session


@pytest.fixture
def client():
    return RobEyeApiClient(host="192.168.1.100")


# ── Mopping endpoints ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pump_volume_settings(client):
    resp = _mock_response({"mode": "low"})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_pump_volume_settings()
    assert result == {"mode": "low"}
    assert "/get/pump_volume_settings" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_set_pump_volume(client):
    resp = _mock_response({"cmd_id": 5})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.set_pump_volume("high")
    params = sess.get.call_args[1].get("params", {})
    assert params["mode"] == "high"
    assert "/set/pump_volume_settings" in sess.get.call_args[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,expected", [(True, "true"), (False, "false")])
async def test_set_wet_clean(client, enabled, expected):
    resp = _mock_response({"cmd_id": 6})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.set_wet_clean(enabled)
    params = sess.get.call_args[1].get("params", {})
    assert params["do_wet_clean"] == expected
    assert "/set/live_parameters" in sess.get.call_args[0][0]


# ── Map editor wire formats ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_area_flat_points_and_json_meta(client):
    resp = _mock_response({"cmd_id": 7})
    p_connector, p_session, sess = _patch_session(resp)
    points = [(1, 2), (3, 4), (5, 6), (7, 8)]
    with p_connector, p_session:
        await client.add_area("3", points, name="")
    params = sess.get.call_args[1].get("params", {})
    # area_meta_data is always a JSON string, even when empty.
    assert params["area_meta_data"] == '{"name":""}'
    assert params["area_state"] == "blocking"
    # Points are flat params, not JSON.
    assert params["x1"] == 1 and params["y1"] == 2
    assert params["x4"] == 7 and params["y4"] == 8
    assert "/set/add_area" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_add_area_requires_four_points(client):
    with pytest.raises(ValueError):
        await client.add_area("3", [(1, 2), (3, 4)])


@pytest.mark.asyncio
async def test_merge_areas(client):
    resp = _mock_response({"cmd_id": 8})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.merge_areas("3", "30", "32")
    params = sess.get.call_args[1].get("params", {})
    assert params == {"map_id": "3", "area_id1": "30", "area_id2": "32"}
    assert "/set/merge_areas" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_split_area(client):
    resp = _mock_response({"cmd_id": 9})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.split_area("3", "30", 1, 2, 3, 4)
    params = sess.get.call_args[1].get("params", {})
    assert params["area_id"] == "30"
    assert (params["x1"], params["y1"], params["x2"], params["y2"]) == (1, 2, 3, 4)


@pytest.mark.asyncio
async def test_modify_map_includes_docking_pose(client):
    resp = _mock_response({"cmd_id": 10})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.modify_map("3", "Upstairs", {"x": 1})
    params = sess.get.call_args[1].get("params", {})
    assert params["name"] == "Upstairs"
    assert params["docking_pose"] == '{"x":1}'


@pytest.mark.asyncio
async def test_explore_no_params(client):
    resp = _mock_response({"cmd_id": 11})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.explore()
    assert sess.get.call_args[1].get("params") is None
    assert "/set/explore" in sess.get.call_args[0][0]


# ── HTTP Basic Auth ──────────────────────────────────────────────────

def test_no_password_no_auth_header():
    client = RobEyeApiClient(host="1.2.3.4")
    assert client._auth_header == {}


def test_password_builds_basic_auth():
    client = RobEyeApiClient(host="1.2.3.4", http_password="abcd1234", auth_username="Helios")
    expected = "Basic " + base64.b64encode(b"Helios:abcd1234").decode()
    assert client._auth_header == {"Authorization": expected}


def test_set_auth_username_rebuilds_header():
    client = RobEyeApiClient(host="1.2.3.4", http_password="abcd1234")
    # Bootstraps with the default username.
    assert DEFAULT_AUTH_USERNAME in base64.b64decode(
        client._auth_header["Authorization"].split()[1]
    ).decode()
    client.set_auth_username("Helios")
    expected = "Basic " + base64.b64encode(b"Helios:abcd1234").decode()
    assert client._auth_header == {"Authorization": expected}


def test_set_auth_username_noop_without_password():
    client = RobEyeApiClient(host="1.2.3.4")
    client.set_auth_username("Helios")
    assert client._auth_header == {}


@pytest.mark.asyncio
async def test_401_raises_auth_failed():
    client = RobEyeApiClient(host="1.2.3.4", http_password="abcd1234")
    resp = _mock_response({}, status=401)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        with pytest.raises(AuthFailed):
            await client.get_status()


# ── Password validation ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "pwd,valid",
    [
        ("", True),          # blank = no auth
        ("abcd1234", True),  # exactly 8
        ("short", False),    # 5 chars
        ("toolong99", False),  # 9 chars
        (None, True),        # treated as blank
    ],
)
def test_validate_http_password(pwd, valid):
    assert validate_http_password(pwd) is valid
