"""Unit tests for the RobEye API client (api.py)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.rowenta_roboeye.api import CannotConnect, RobEyeApiClient
from custom_components.rowenta_roboeye.const import STRATEGY_DEFAULT


def _mock_response(json_data: dict, status: int = 200) -> AsyncMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(response: AsyncMock) -> MagicMock:
    """Build a mock aiohttp.ClientSession whose .get() is a proper async CM."""
    sess = MagicMock()
    sess.get.return_value = response
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


@pytest.fixture
def client():
    return RobEyeApiClient(host="192.168.1.100")


# ── Helper: patch aiohttp so no real network calls happen ────────────────

def _patch_session(response: AsyncMock):
    """Context manager that patches aiohttp so _get() uses a mock session."""
    sess = _make_mock_session(response)
    connector = MagicMock()
    return (
        patch("custom_components.rowenta_roboeye.api.aiohttp.TCPConnector", return_value=connector),
        patch("custom_components.rowenta_roboeye.api.aiohttp.ClientSession", return_value=sess),
        sess,
    )


# ── URL construction ──────────────────────────────────────────────────

def test_url_format(client):
    assert client._url("/get/status") == "http://192.168.1.100:8080/get/status"


# ── GET endpoints — success paths ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_status(client):
    payload = {"battery_level": 80, "mode": "ready", "charging": "charging", "cleaning_parameter_set": 2}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_status()
    assert result["battery_level"] == 80
    assert "/get/status" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_statistics(client):
    payload = {"total_number_of_cleaning_runs": 10}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_statistics()
    assert result["total_number_of_cleaning_runs"] == 10


@pytest.mark.asyncio
async def test_get_permanent_statistics(client):
    payload = {"total_distance_driven": 5000}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_permanent_statistics()
    assert result["total_distance_driven"] == 5000
    assert "/get/permanent_statistics" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_areas_passes_map_id(client):
    resp = _mock_response({"areas": []})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.get_areas("3")
    params = sess.get.call_args[1].get("params", {})
    assert params.get("map_id") == "3"


@pytest.mark.asyncio
async def test_get_wifi_status(client):
    payload = {"ssid": "MyNet", "rssi": -60}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_wifi_status()
    assert result["ssid"] == "MyNet"
    assert "/get/wifi_status" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_robot_id(client):
    payload = {"serial_number": "SN001"}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_robot_id()
    assert result["serial_number"] == "SN001"
    assert "/get/robot_id" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_protocol_version(client):
    payload = {"version": "2.3.1"}
    resp = _mock_response(payload)
    p_connector, p_session, _ = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_protocol_version()
    assert result["version"] == "2.3.1"


@pytest.mark.asyncio
async def test_get_live_parameters(client):
    payload = {"area_cleaned": 10000, "cleaning_time": 300}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_live_parameters()
    assert result["area_cleaned"] == 10000
    assert "/get/live_parameters" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_sensor_status(client):
    payload = {"cliff_sensor": "ok", "bump_sensor": "ok"}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_sensor_status()
    assert result["cliff_sensor"] == "ok"
    assert "/get/sensor_status" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_robot_flags(client):
    payload = {"has_mop": False}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_robot_flags()
    assert result["has_mop"] is False
    assert "/get/robot_flags" in sess.get.call_args[0][0]


# ── Failure paths ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_raises_cannot_connect(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    sess.get.side_effect = asyncio.TimeoutError
    with p_connector, p_session:
        with pytest.raises(CannotConnect):
            await client.get_status()


@pytest.mark.asyncio
async def test_client_error_raises_cannot_connect(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    sess.get.side_effect = aiohttp.ClientError("refused")
    with p_connector, p_session:
        with pytest.raises(CannotConnect):
            await client.get_status()


# ── SET commands ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_all(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.clean_all(cleaning_parameter_set="2")
    params = sess.get.call_args[1].get("params", {})
    assert params["cleaning_parameter_set"] == "2"
    # Default strategy_mode is STRATEGY_DEFAULT ("4")
    assert params["cleaning_strategy_mode"] == STRATEGY_DEFAULT
    assert "/set/clean_all" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_clean_map(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.clean_map(map_id="3", area_ids="2,11", cleaning_parameter_set="3")
    params = sess.get.call_args[1].get("params", {})
    assert params["map_id"] == "3"
    assert params["area_ids"] == "2,11"
    assert params["cleaning_parameter_set"] == "3"
    assert params["cleaning_strategy_mode"] == STRATEGY_DEFAULT
    assert "/set/clean_map" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_go_home(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.go_home()
    assert "/set/go_home" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_stop(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.stop()
    assert "/set/stop" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_set_fan_speed(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        await client.set_fan_speed("3")
    params = sess.get.call_args[1].get("params", {})
    assert params["cleaning_parameter_set"] == "3"
    assert "/set/switch_cleaning_parameter_set" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_test_connection_success(client):
    resp = _mock_response({"battery_level": 100})
    p_connector, p_session, _ = _patch_session(resp)
    with p_connector, p_session:
        assert await client.test_connection() is True


@pytest.mark.asyncio
async def test_test_connection_failure(client):
    resp = _mock_response({})
    p_connector, p_session, sess = _patch_session(resp)
    sess.get.side_effect = asyncio.TimeoutError
    with p_connector, p_session:
        with pytest.raises(CannotConnect):
            await client.test_connection()


# ── Position / debug endpoints ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_relocalization(client):
    payload = {"localization_algo_input": [
        {"localization_type": "continuous",
         "rob_pose": [-661, 235, -6269],
         "rtc_time": {"year": 2026, "month": 3, "day": 18,
                      "hour": 18, "min": 9, "sec": 10}}
    ]}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_relocalization()
    assert result["localization_algo_input"][0]["localization_type"] == "continuous"
    assert "/debug/relocalization" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_exploration(client):
    payload = {"exploration_points": [
        {"ts": 474811434, "type": "smsu_no_nearby_expl_points",
         "rob_pose": [-861, 352, -6298]}
    ]}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_exploration()
    assert result["exploration_points"][0]["ts"] == 474811434
    assert "/debug/exploration" in sess.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_map_status(client):
    payload = {"operation_map_id": 57, "active_map_id": 3}
    resp = _mock_response(payload)
    p_connector, p_session, sess = _patch_session(resp)
    with p_connector, p_session:
        result = await client.get_map_status()
    assert result["operation_map_id"] == 57
    assert "/get/map_status" in sess.get.call_args[0][0]
