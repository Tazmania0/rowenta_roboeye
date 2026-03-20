"""Unit tests for the RobEye API client (api.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.rowenta_roboeye.api import CannotConnect, RobEyeApiClient


def _mock_response(json_data: dict, status: int = 200) -> AsyncMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.fixture
def session():
    return MagicMock(spec=aiohttp.ClientSession)


@pytest.fixture
def client(session):
    return RobEyeApiClient(host="192.168.1.100", session=session)


# ── URL construction ──────────────────────────────────────────────────

def test_url_format(client):
    assert client._url("/get/status") == "http://192.168.1.100:8080/get/status"


# ── GET endpoints — success paths ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_status(client, session):
    payload = {"battery_level": 80, "mode": "ready", "charging": "charging", "cleaning_parameter_set": 2}
    session.get.return_value = _mock_response(payload)
    result = await client.get_status()
    assert result["battery_level"] == 80
    assert "/get/status" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_statistics(client, session):
    payload = {"total_number_of_cleaning_runs": 10}
    session.get.return_value = _mock_response(payload)
    result = await client.get_statistics()
    assert result["total_number_of_cleaning_runs"] == 10


@pytest.mark.asyncio
async def test_get_permanent_statistics(client, session):
    payload = {"total_distance_driven": 5000}
    session.get.return_value = _mock_response(payload)
    result = await client.get_permanent_statistics()
    assert result["total_distance_driven"] == 5000
    assert "/get/permanent_statistics" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_areas_passes_map_id(client, session):
    session.get.return_value = _mock_response({"areas": []})
    await client.get_areas("3")
    params = session.get.call_args[1].get("params", {})
    assert params.get("map_id") == "3"


@pytest.mark.asyncio
async def test_get_wifi_status(client, session):
    payload = {"ssid": "MyNet", "rssi": -60}
    session.get.return_value = _mock_response(payload)
    result = await client.get_wifi_status()
    assert result["ssid"] == "MyNet"
    assert "/get/wifi_status" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_robot_id(client, session):
    payload = {"serial_number": "SN001"}
    session.get.return_value = _mock_response(payload)
    result = await client.get_robot_id()
    assert result["serial_number"] == "SN001"
    assert "/get/robot_id" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_protocol_version(client, session):
    payload = {"version": "2.3.1"}
    session.get.return_value = _mock_response(payload)
    result = await client.get_protocol_version()
    assert result["version"] == "2.3.1"


@pytest.mark.asyncio
async def test_get_live_parameters(client, session):
    payload = {"area_cleaned": 10000, "cleaning_time": 300}
    session.get.return_value = _mock_response(payload)
    result = await client.get_live_parameters()
    assert result["area_cleaned"] == 10000
    assert "/get/live_parameters" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_sensor_status(client, session):
    payload = {"cliff_sensor": "ok", "bump_sensor": "ok"}
    session.get.return_value = _mock_response(payload)
    result = await client.get_sensor_status()
    assert result["cliff_sensor"] == "ok"
    assert "/get/sensor_status" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_robot_flags(client, session):
    payload = {"has_mop": False}
    session.get.return_value = _mock_response(payload)
    result = await client.get_robot_flags()
    assert result["has_mop"] is False
    assert "/get/robot_flags" in session.get.call_args[0][0]


# ── Failure paths ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_raises_cannot_connect(client, session):
    session.get.side_effect = asyncio.TimeoutError
    with pytest.raises(CannotConnect):
        await client.get_status()


@pytest.mark.asyncio
async def test_client_error_raises_cannot_connect(client, session):
    session.get.side_effect = aiohttp.ClientError("refused")
    with pytest.raises(CannotConnect):
        await client.get_status()


# ── SET commands ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_all(client, session):
    session.get.return_value = _mock_response({})
    await client.clean_all(cleaning_parameter_set="2")
    params = session.get.call_args[1].get("params", {})
    assert params["cleaning_parameter_set"] == "2"
    assert params["cleaning_strategy_mode"] == "1"
    assert "/set/clean_all" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_clean_map(client, session):
    session.get.return_value = _mock_response({})
    await client.clean_map(map_id="3", area_ids="2,11", cleaning_parameter_set="3")
    params = session.get.call_args[1].get("params", {})
    assert params["map_id"] == "3"
    assert params["area_ids"] == "2,11"
    assert params["cleaning_parameter_set"] == "3"
    assert params["cleaning_strategy_mode"] == "1"


@pytest.mark.asyncio
async def test_go_home(client, session):
    session.get.return_value = _mock_response({})
    await client.go_home()
    assert "/set/go_home" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_stop(client, session):
    session.get.return_value = _mock_response({})
    await client.stop()
    assert "/set/stop" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_set_fan_speed(client, session):
    session.get.return_value = _mock_response({})
    await client.set_fan_speed("3")
    params = session.get.call_args[1].get("params", {})
    assert params["cleaning_parameter_set"] == "3"
    assert "/set/switch_cleaning_parameter_set" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_test_connection_success(client, session):
    session.get.return_value = _mock_response({"battery_level": 100})
    assert await client.test_connection() is True


@pytest.mark.asyncio
async def test_test_connection_failure(client, session):
    session.get.side_effect = asyncio.TimeoutError
    with pytest.raises(CannotConnect):
        await client.test_connection()


# ── Position / debug endpoints ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_relocalization(client, session):
    payload = {"localization_algo_input": [
        {"localization_type": "continuous",
         "rob_pose": [-661, 235, -6269],
         "rtc_time": {"year": 2026, "month": 3, "day": 18,
                      "hour": 18, "min": 9, "sec": 10}}
    ]}
    session.get.return_value = _mock_response(payload)
    result = await client.get_relocalization()
    assert result["localization_algo_input"][0]["localization_type"] == "continuous"
    assert "/debug/relocalization" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_exploration(client, session):
    payload = {"exploration_points": [
        {"ts": 474811434, "type": "smsu_no_nearby_expl_points",
         "rob_pose": [-861, 352, -6298]}
    ]}
    session.get.return_value = _mock_response(payload)
    result = await client.get_exploration()
    assert result["exploration_points"][0]["ts"] == 474811434
    assert "/debug/exploration" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_get_map_status(client, session):
    payload = {"operation_map_id": 57, "active_map_id": 3}
    session.get.return_value = _mock_response(payload)
    result = await client.get_map_status()
    assert result["operation_map_id"] == 57
    assert "/get/map_status" in session.get.call_args[0][0]
