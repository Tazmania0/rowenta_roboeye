"""Unit tests for config flow — manual setup, mDNS discovery, options flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.rowenta_roboeye.api import CannotConnect
from custom_components.rowenta_roboeye.config_flow import RobEyeConfigFlow, RobEyeOptionsFlow, _parse_maps
from custom_components.rowenta_roboeye.const import DEFAULT_MAP_ID

from .conftest import MOCK_MAPS


# Confirmed live-device response for _parse_maps tests
_CONFIRMED_MAPS = {
    "maps": [
        {
            "map_id": 3,
            "map_meta_data": "Дружба ",
            "permanent_flag": "true",
            "statistics": {},
        },
        {
            "map_id": 18,
            "map_meta_data": "",
            "permanent_flag": "true",
            "statistics": {},
        },
    ]
}


def _make_flow():
    flow = RobEyeConfigFlow.__new__(RobEyeConfigFlow)
    flow.hass = MagicMock()
    flow._host = ""
    flow._hostname = ""
    flow._map_id = DEFAULT_MAP_ID
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    return flow


# ── Manual setup — happy path ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_step_success():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock()):
        result = await flow.async_step_user(
            {"host": "192.168.1.50", "map_id": "3"}
        )

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.50"
    assert result["data"]["map_id"] == "3"


@pytest.mark.asyncio
async def test_user_step_cannot_connect():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=CannotConnect)):
        result = await flow.async_step_user(
            {"host": "192.168.1.50", "map_id": "3"}
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_step_unknown_error():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=Exception("boom"))):
        result = await flow.async_step_user(
            {"host": "192.168.1.50", "map_id": "3"}
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_user_step_shows_form_when_no_input():
    flow = _make_flow()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


# ── mDNS / Zeroconf discovery ─────────────────────────────────────────

def _mock_zeroconf_info(host="192.168.1.100", hostname="xplorer120.local."):
    info = MagicMock()
    info.host = host
    info.hostname = hostname
    return info


@pytest.mark.asyncio
async def test_zeroconf_happy_path():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock()):
        result = await flow.async_step_zeroconf(_mock_zeroconf_info())

    # Should advance to confirmation form
    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"


@pytest.mark.asyncio
async def test_zeroconf_cannot_connect_aborts():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=CannotConnect)):
        result = await flow.async_step_zeroconf(_mock_zeroconf_info())

    assert result["type"] == "abort"
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_zeroconf_confirm_creates_entry():
    flow = _make_flow()
    flow._host = "192.168.1.100"
    flow._hostname = "xplorer120.local."

    result = await flow.async_step_zeroconf_confirm(user_input={})

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.100"
    assert result["data"]["hostname"] == "xplorer120.local."
    assert result["data"]["map_id"] == DEFAULT_MAP_ID


@pytest.mark.asyncio
async def test_zeroconf_confirm_shows_form_without_input():
    flow = _make_flow()
    flow._host = "192.168.1.100"

    result = await flow.async_step_zeroconf_confirm(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"


@pytest.mark.asyncio
async def test_zeroconf_updates_ip_for_existing_hostname():
    """If mDNS re-announces same hostname with new IP, entry IP is updated silently."""
    flow = _make_flow()
    flow._abort_if_unique_id_configured = MagicMock(side_effect=Exception("already_configured"))

    with patch.object(flow, "_test_connection", new=AsyncMock()):
        try:
            await flow.async_step_zeroconf(_mock_zeroconf_info(host="192.168.1.200"))
        except Exception:
            pass

    # _abort_if_unique_id_configured was called with updates dict containing new IP
    call_kwargs = flow._abort_if_unique_id_configured.call_args[1]
    assert call_kwargs["updates"]["host"] == "192.168.1.200"


# ── Options flow ──────────────────────────────────────────────────────

def _make_options_flow(host="192.168.1.100", map_id="3"):
    entry = MagicMock()
    entry.data = {"host": host, "hostname": "xplorer120.local.", "map_id": map_id}
    flow = RobEyeOptionsFlow.__new__(RobEyeOptionsFlow)
    flow._config_entry = entry
    flow.hass = MagicMock()
    return flow


@pytest.mark.asyncio
async def test_options_flow_happy_path():
    flow = _make_options_flow()

    with patch(
        "custom_components.rowenta_roboeye.config_flow.RobEyeApiClient"
    ) as MockClient:
        MockClient.return_value.test_connection = AsyncMock(return_value=True)
        result = await flow.async_step_init({"host": "192.168.1.101", "map_id": "5"})

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.101"
    assert result["data"]["map_id"] == "5"


@pytest.mark.asyncio
async def test_options_flow_cannot_connect():
    flow = _make_options_flow()

    with patch(
        "custom_components.rowenta_roboeye.config_flow.RobEyeApiClient"
    ) as MockClient:
        MockClient.return_value.test_connection = AsyncMock(
            side_effect=CannotConnect
        )
        result = await flow.async_step_init({"host": "10.0.0.1", "map_id": "3"})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_options_flow_shows_form_without_input():
    flow = _make_options_flow()
    result = await flow.async_step_init(None)
    assert result["type"] == "form"


# ── _parse_maps helper ────────────────────────────────────────────────

def test_parse_maps_named_active():
    result = _parse_maps(_CONFIRMED_MAPS, active_map_id="3")
    assert result[0]["id"] == "3"
    assert result[0]["label"] == "Дружба ✓"


def test_parse_maps_unnamed_position_label():
    result = _parse_maps(_CONFIRMED_MAPS, active_map_id="3")
    assert result[1]["id"] == "18"
    assert result[1]["label"] == "Map 2"    # position 2, not "Map 18"


def test_parse_maps_no_active_no_checkmark():
    result = _parse_maps(_CONFIRMED_MAPS, active_map_id="")
    assert result[0]["label"] == "Дружба"
    assert "✓" not in result[0]["label"]


def test_parse_maps_permanent_flag_string():
    """Must not fail when permanent_flag is the string "true"."""
    result = _parse_maps(_CONFIRMED_MAPS)
    assert len(result) == 2


def test_parse_maps_skips_non_permanent():
    data = {"maps": [
        {"map_id": 3, "map_meta_data": "Floor", "permanent_flag": "true"},
        {"map_id": 99, "map_meta_data": "Temp", "permanent_flag": "false"},
    ]}
    result = _parse_maps(data)
    assert len(result) == 1
    assert result[0]["id"] == "3"


def test_parse_maps_empty_falls_back():
    result = _parse_maps({})
    assert len(result) == 1
    assert result[0]["id"] == DEFAULT_MAP_ID


def test_parse_maps_mock_maps_fixture():
    """MOCK_MAPS confirmed format parses to named maps."""
    result = _parse_maps(MOCK_MAPS)
    assert len(result) == 2
    assert result[0]["id"] == "3"
    assert result[0]["label"] == "Ground Floor"
    assert result[1]["id"] == "4"
    assert result[1]["label"] == "First Floor"
