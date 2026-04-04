"""Unit tests for the RobEye DataUpdateCoordinator."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.api import CannotConnect
from custom_components.rowenta_roboeye.const import (
    DATA_AREAS,
    DATA_LIVE_PARAMETERS,
    DATA_ROB_POSE,
    DATA_ROBOT_INFO,
    DATA_STATISTICS,
    DATA_STATUS,
    SCAN_INTERVAL_AREAS,
    SCAN_INTERVAL_ROBOT_INFO,
    SCAN_INTERVAL_STATISTICS,
)
from custom_components.rowenta_roboeye.coordinator import (
    RobEyeCoordinator,
    _build_live_map_payload,
    _extract_rob_pose,
    _parse_map_entry,
)

from .conftest import MOCK_AREAS, MOCK_CLEANING_GRID, MOCK_MAP_STATUS, MOCK_MAPS, MOCK_ROB_POSE, MOCK_STATISTICS, MOCK_STATUS


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    hass = MagicMock()
    coord = RobEyeCoordinator(
        hass=hass,
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    # Ensure live_map polling is always enabled in tests
    # (the entity registry lookup fails with a MagicMock hass)
    coord._is_live_map_enabled = lambda: True
    return coord


# ── First update — all resource groups must be fetched ────────────────

@pytest.mark.asyncio
async def test_first_update_fetches_all_groups(coordinator, mock_client):
    coordinator.data = {}
    await coordinator._async_update_data()

    mock_client.get_status.assert_called_once()
    mock_client.get_rob_pose.assert_called_once()
    mock_client.get_statistics.assert_called_once()
    # get_areas is called twice on first run: once for the areas block
    # (_last_areas is None) and once for the map-geometry block
    # (_last_map_geometry is None).  Both use map_id="3".
    mock_client.get_areas.assert_any_call("3")
    assert mock_client.get_areas.call_count >= 1
    mock_client.get_sensor_status.assert_called_once()
    mock_client.get_robot_id.assert_called_once()
    mock_client.get_wifi_status.assert_called_once()
    mock_client.get_protocol_version.assert_called_once()


# ── Interval gating ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_fetched_every_tick(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()

    assert mock_client.get_status.call_count == 1
    assert mock_client.get_statistics.call_count == 0
    assert mock_client.get_areas.call_count == 0
    assert mock_client.get_robot_id.call_count == 0


@pytest.mark.asyncio
async def test_areas_fetched_after_300s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()
    assert mock_client.get_areas.call_count == 1
    assert mock_client.get_statistics.call_count == 0


@pytest.mark.asyncio
async def test_statistics_fetched_after_600s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_statistics = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_STATISTICS + 1)
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()
    assert mock_client.get_statistics.call_count == 1
    assert mock_client.get_areas.call_count == 0


@pytest.mark.asyncio
async def test_robot_info_fetched_after_3600s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_robot_info = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_ROBOT_INFO + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_areas = datetime.utcnow()

    await coordinator._async_update_data()
    mock_client.get_robot_id.assert_called_once()
    mock_client.get_wifi_status.assert_called_once()
    mock_client.get_protocol_version.assert_called_once()


# ── Graceful degradation — optional endpoints ─────────────────────────

@pytest.mark.asyncio
async def test_rob_pose_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_rob_pose.side_effect = CannotConnect("not available")
    # Should not raise — rob_pose is best-effort
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


@pytest.mark.asyncio
async def test_live_parameters_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_live_parameters.side_effect = CannotConnect("not available")
    # Should not raise — live_parameters is best-effort
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


@pytest.mark.asyncio
async def test_sensor_status_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_sensor_status.side_effect = CannotConnect("not available")
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


# ── Error propagation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_failure_raises_update_failed(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_consecutive_failure_counter(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}

    for i in range(1, 5):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator._consecutive_failures == i


@pytest.mark.asyncio
async def test_failure_counter_resets_on_success(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    mock_client.get_status.side_effect = None
    mock_client.get_status.return_value = dict(MOCK_STATUS)
    await coordinator._async_update_data()
    assert coordinator._consecutive_failures == 0


# ── async_send_command ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_command_calls_fn_and_refreshes(coordinator, mock_client):
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_send_command(mock_client.go_home)
    mock_client.go_home.assert_called_once()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_send_command_passes_kwargs(coordinator, mock_client):
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_send_command(
        mock_client.clean_all, cleaning_parameter_set="3"
    )
    mock_client.clean_all.assert_called_once_with(cleaning_parameter_set="3")


# ── Convenience properties ────────────────────────────────────────────

def test_status_property(coordinator):
    coordinator.data = {DATA_STATUS: MOCK_STATUS}
    assert coordinator.status == MOCK_STATUS


def test_areas_property_unwraps_list(coordinator):
    coordinator.data = {DATA_AREAS: MOCK_AREAS}
    assert len(coordinator.areas) == 3  # includes the empty-meta area


def test_areas_property_empty_when_no_data(coordinator):
    coordinator.data = {}
    assert coordinator.areas == []


def test_live_parameters_property(coordinator):
    coordinator.data = {DATA_LIVE_PARAMETERS: {"area_cleaned": 100}}
    assert coordinator.live_parameters["area_cleaned"] == 100


def test_robot_info_property(coordinator):
    coordinator.data = {DATA_ROBOT_INFO: {"wifi_status": {"rssi": -55}}}
    assert coordinator.robot_info["wifi_status"]["rssi"] == -55


# ── Last-session / session replay ─────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_loads_last_session_grid(coordinator, mock_client):
    """On first update (geometry bucket runs), saved grid is loaded and
    session_complete becomes True."""
    coordinator.data = {}
    await coordinator._async_update_data()

    mock_client.get_cleaning_grid_map.assert_called_with(map_id="3")
    assert coordinator.last_session_grid["size_x"] == 29
    assert coordinator.session_complete is True


@pytest.mark.asyncio
async def test_new_cleaning_run_resets_session_state(coordinator, mock_client):
    """When mode transitions to CLEANING, frozen session state is cleared."""
    coordinator.data = {}
    # Pre-populate session data
    coordinator._last_session_grid = dict(MOCK_CLEANING_GRID)
    coordinator._last_session_path = [(1.0, 2.0), (3.0, 4.0)]
    coordinator._session_complete = True
    coordinator._last_mode = "ready"

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning"}
    await coordinator._async_update_data()

    assert coordinator._last_session_grid == {}
    assert coordinator._last_session_path == []
    assert coordinator._session_complete is False


@pytest.mark.asyncio
async def test_session_frozen_on_dock(coordinator, mock_client):
    """When robot docks after cleaning, session data is frozen."""
    from custom_components.rowenta_roboeye.const import DATA_CLEANING_GRID, DATA_SEEN_POLYGON

    coordinator.data = {
        DATA_CLEANING_GRID: dict(MOCK_CLEANING_GRID),
        DATA_SEEN_POLYGON: {},
    }
    coordinator._last_mode = "cleaning"
    coordinator._robot_path = [(0.0, 0.0), (10.0, 5.0)]
    coordinator._session_complete = False

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "ready"}
    await coordinator._async_update_data()

    assert coordinator._session_complete is True
    assert coordinator.last_session_grid["size_x"] == 29
    assert coordinator.last_session_path == [(0.0, 0.0), (10.0, 5.0)]


def test_session_complete_property_default(coordinator):
    assert coordinator.session_complete is False


def test_last_session_grid_property_default(coordinator):
    assert coordinator.last_session_grid == {}


# ── rob_pose extraction helper ────────────────────────────────────────

def test_extract_rob_pose_valid():
    """Confirmed live response at dock (2026-03-21)."""
    data = {
        "map_id": 3,
        "x1": -2,
        "y1": -3,
        "heading": 157,
        "valid": True,
        "is_tentative": False,
        "timestamp": 958459,
    }
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["x"] == -2
    assert pos["y"] == -3
    assert pos["heading_deg"] == 157  # already degrees — no conversion
    assert pos["is_tentative"] is False
    assert pos["timestamp"] == 958459
    assert pos["map_id"] == 3
    assert pos["source"] == "rob_pose"
    assert pos["is_live"] is True


def test_extract_rob_pose_invalid_returns_none():
    """When valid=False the robot has no position fix — return None."""
    data = {"x1": 0, "y1": 0, "heading": 0, "valid": False}
    assert _extract_rob_pose(data) is None


def test_extract_rob_pose_missing_valid_returns_none():
    """Missing valid field treated as False."""
    data = {"x1": 10, "y1": 20, "heading": 90}
    assert _extract_rob_pose(data) is None


def test_extract_rob_pose_tentative():
    """is_tentative=True is preserved in the output."""
    data = {
        "x1": 5, "y1": 10, "heading": 45,
        "valid": True, "is_tentative": True, "timestamp": 100,
    }
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["is_tentative"] is True
    assert pos["is_live"] is True


def test_extract_rob_pose_heading_no_conversion():
    """heading is already in degrees — must not be divided by 65536/360."""
    data = {"x1": 0, "y1": 0, "heading": 270, "valid": True}
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["heading_deg"] == 270  # exactly 270, not 270 / 181.6


def test_extract_rob_pose_empty_returns_none():
    assert _extract_rob_pose({}) is None


# ── Position tracking ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_session_resets_last_live_map(coordinator, mock_client):
    """_last_live_map is cleared when a new cleaning session starts."""
    coordinator.data = {}
    coordinator._last_mode = "ready"
    coordinator._last_live_map = datetime.utcnow()

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning"}
    await coordinator._async_update_data()

    assert coordinator._last_live_map is not None  # updated by this tick
    mock_client.get_rob_pose.assert_called_once()


@pytest.mark.asyncio
async def test_rob_pose_stored_in_data(coordinator, mock_client):
    """After a successful tick, DATA_ROB_POSE is present in coordinator.data."""
    coordinator.data = {}
    await coordinator._async_update_data()
    assert DATA_ROB_POSE in coordinator.data
    assert coordinator.data[DATA_ROB_POSE]["x1"] == -2


def _make_live_map_kwargs(**overrides):
    """Return a minimal valid kwarg dict for _build_live_map_payload."""
    base = dict(
        existing={},
        robot_position=None,
        seen_polygon_raw={},
        cleaning_grid={},
        feature_map={},
        tile_map={},
        areas_data={},
        seen_poly_saved_map={},
        is_active=False,
        map_id="3",
        robot_path=[],
        last_session_grid={},
        last_session_path=[],
        last_session_outline=[],
        last_session_map_id="",
        session_complete=False,
    )
    base.update(overrides)
    return base


def test_active_robot_position_in_payload():
    """During active cleaning the rob_pose position is placed in robot."""
    live_pos = {
        "x": 200, "y": 100, "heading_deg": 10.0,
        "source": "rob_pose", "is_live": True, "is_tentative": False,
    }

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=live_pos,
        is_active=True,
    ))

    robot = payload["robot"]
    assert robot is not None
    assert robot["x"] == 200
    assert robot["y"] == 100
    assert robot["heading_deg"] == 10.0
    assert robot["is_tentative"] is False


def test_tentative_position_propagated_to_payload():
    """is_tentative flag from rob_pose is forwarded to the robot dict."""
    live_pos = {
        "x": 5, "y": 10, "heading_deg": 45.0,
        "source": "rob_pose", "is_live": True, "is_tentative": True,
    }

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=live_pos,
        is_active=True,
    ))

    assert payload["robot"]["is_tentative"] is True


def test_no_robot_position_when_invalid_and_idle():
    """When robot_position is None and idle, existing robot is preserved."""
    existing_robot = {"x": 50, "y": 30, "heading_deg": 90.0, "is_tentative": False}
    payload = _build_live_map_payload(**_make_live_map_kwargs(
        existing={"robot": existing_robot},
        robot_position=None,
        is_active=False,
    ))
    # existing robot from previous tick should be kept
    assert payload["robot"] == existing_robot


# ── Multi-map support ────────────────────────────────────────────────

def test_active_map_id_falls_back_to_configured(coordinator):
    """active_map_id returns setup-time map_id when map_status not yet fetched."""
    coordinator.data = {}
    assert coordinator.active_map_id == "3"


def test_active_map_id_ignores_map_status(coordinator):
    """active_map_id intentionally ignores /get/map_status — HA must not silently
    follow the native app's floor selection.  Only the HA-stored preference or the
    setup-time map_id are used."""
    coordinator.data = {"map_status": {"active_map_id": 4, "operation_map_id": 4}}
    # map_status says "4" but _manual_map_id is None → falls back to setup map_id "3"
    assert coordinator.active_map_id == "3"


def test_active_map_id_falls_back_on_empty_string(coordinator):
    coordinator.data = {"map_status": {"active_map_id": ""}}
    assert coordinator.active_map_id == "3"  # setup-time map_id


CONFIRMED_MAPS_RESPONSE = {
    "maps": [
        {
            "map_id": 3,
            "map_meta_data": "Дружба ",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0, "cleaning_counter": 5,
                "estimated_cleaning_time": 0, "average_cleaning_time": 1800,
                "last_cleaned": {"year": 2026, "month": 3, "day": 20,
                                 "hour": 11, "min": 21, "sec": 0},
            },
        },
        {
            "map_id": 18,
            "map_meta_data": "",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0, "cleaning_counter": 0,
                "estimated_cleaning_time": 0, "average_cleaning_time": 0,
                "last_cleaned": {"year": 2001, "month": 1, "day": 1,
                                 "hour": 0, "min": 0, "sec": 0},
            },
        },
    ]
}


def test_parse_map_entry_named():
    """Named map uses map_meta_data, stripped."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][0], position=1, active_map_id="3")
    assert result["map_id"] == "3"
    assert result["display_name"] == "Дружба"
    assert result["user_name"] == "Дружба"
    assert result["is_active"] is True


def test_parse_map_entry_unnamed_uses_position():
    """Unnamed map uses 1-based position, NOT map_id."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][1], position=2, active_map_id="3")
    assert result["display_name"] == "Map 2"
    assert result["map_id"] == "18"        # map_id is still 18
    assert result["is_active"] is False


def test_parse_map_entry_strips_whitespace():
    raw = {"map_id": 3, "map_meta_data": "  Дружба  ", "permanent_flag": "true", "statistics": {}}
    result = _parse_map_entry(raw, position=1)
    assert result["display_name"] == "Дружба"


def test_parse_map_entry_permanent_flag_is_string_not_bool():
    """permanent_flag is the string "true", not Python True."""
    raw = {"map_id": 5, "map_meta_data": "Test", "permanent_flag": "true", "statistics": {}}
    result = _parse_map_entry(raw, position=1)
    assert result is not None


def test_parse_map_entry_skips_non_permanent():
    raw = {"map_id": 99, "map_meta_data": "", "permanent_flag": "false", "statistics": {}}
    assert _parse_map_entry(raw, position=1) is None


def test_parse_map_entry_last_cleaned_sentinel_2001():
    """year 2001 → never cleaned → last_cleaned is None."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][1], position=2)
    assert result["statistics"]["last_cleaned"] is None


def test_parse_map_entry_real_last_cleaned():
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][0], position=1)
    assert result["statistics"]["last_cleaned"] == "2026-03-20"


def test_available_maps_parses_dict_entries(coordinator):
    coordinator.data = {"maps": dict(MOCK_MAPS)}
    maps = coordinator.available_maps
    assert len(maps) == 2
    assert maps[0]["map_id"] == "3"
    assert maps[0]["display_name"] == "Ground Floor"
    assert maps[0]["user_name"] == "Ground Floor"
    assert maps[1]["map_id"] == "4"
    assert maps[1]["display_name"] == "First Floor"


def test_available_maps_skips_non_permanent(coordinator):
    coordinator.data = {"maps": {"maps": [
        {"map_id": 3, "map_meta_data": "Floor", "permanent_flag": "true", "statistics": {}},
        {"map_id": 99, "map_meta_data": "", "permanent_flag": "false", "statistics": {}},
    ]}}
    maps = coordinator.available_maps
    assert len(maps) == 1
    assert maps[0]["map_id"] == "3"


def test_available_maps_unnamed_uses_position(coordinator):
    coordinator.data = {"maps": {"maps": [
        {"map_id": 18, "map_meta_data": "", "permanent_flag": "true", "statistics": {}},
    ]}}
    maps = coordinator.available_maps
    assert maps[0]["display_name"] == "Map 1"
    assert maps[0]["map_id"] == "18"


def test_available_maps_full(coordinator):
    coordinator.data = {
        "map_status": {"active_map_id": 3},
        "maps": CONFIRMED_MAPS_RESPONSE,
    }
    maps = coordinator.available_maps
    assert len(maps) == 2
    assert maps[0]["display_name"] == "Дружба"
    assert maps[0]["is_active"] is True
    assert maps[1]["display_name"] == "Map 2"
    assert maps[1]["is_active"] is False


def test_available_maps_empty_when_no_data(coordinator):
    coordinator.data = {}
    assert coordinator.available_maps == []


@pytest.mark.asyncio
async def test_device_floor_change_does_not_reset_areas(coordinator, mock_client):
    """When the device reports a different map via map_status, HA ignores it.
    Area state must NOT be reset — only async_set_active_map (user action) does that."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_active_map_id = "3"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {3, 11}
    coordinator._robot_path = [(0.0, 0.0)]
    coordinator._session_complete = True

    # Device reports a different map — HA must NOT follow it automatically
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # active_map_id stays at setup map_id "3" regardless of what device reports
    assert coordinator.active_map_id == "3"
    # Areas/session state must NOT have been reset by the device-reported change
    assert coordinator._robot_path != []
    assert coordinator._session_complete is True


@pytest.mark.asyncio
async def test_maps_and_map_status_fetched_in_areas_bucket(coordinator, mock_client):
    """get_maps and get_map_status are called in the 300s areas bucket."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()

    await coordinator._async_update_data()

    mock_client.get_maps.assert_called_once()
    mock_client.get_map_status.assert_called_once()
    assert "maps" in coordinator.data
    assert "map_status" in coordinator.data


# ── Manual map override (async_set_active_map) ────────────────────────

def test_active_map_id_manual_override_wins(coordinator):
    """_manual_map_id takes priority over /get/map_status."""
    coordinator.data = {"map_status": {"active_map_id": 3, "operation_map_id": 3}}
    coordinator._manual_map_id = "57"
    assert coordinator.active_map_id == "57"


def test_active_map_id_no_override_uses_setup_map_id(coordinator):
    """Without manual override, active_map_id returns the setup-time map_id.
    map_status is intentionally ignored — HA must not silently follow the native app."""
    coordinator.data = {"map_status": {"active_map_id": 4, "operation_map_id": 4}}
    coordinator._manual_map_id = None
    assert coordinator.active_map_id == "3"  # setup-time map_id, not map_status


@pytest.mark.asyncio
async def test_async_set_active_map_updates_state(coordinator):
    """async_set_active_map sets override, resets area/geometry timestamps."""
    coordinator.data = {}
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {3, 11}
    coordinator._robot_path = [(1.0, 2.0)]
    coordinator._session_complete = True
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_active_map("57")

    assert coordinator._manual_map_id == "57"
    assert coordinator._last_active_map_id == "57"
    assert coordinator._last_areas is None
    assert coordinator._last_map_geometry is None
    assert coordinator._known_area_ids == set()
    assert coordinator._robot_path == []
    assert coordinator._session_complete is False
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_set_active_map_invalidates_dashboard(coordinator):
    """async_set_active_map calls dashboard_manager.invalidate() when present."""
    coordinator.data = {}
    coordinator.async_request_refresh = AsyncMock()

    dashboard_manager = MagicMock()
    coordinator.hass.data = {
        "rowenta_roboeye": {
            f"{coordinator.config_entry.entry_id}_dashboard": dashboard_manager
        }
    }

    await coordinator.async_set_active_map("4")

    dashboard_manager.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_manual_override_persists_across_poll(coordinator, mock_client):
    """Manual map override persists across coordinator polls regardless of
    what map_status the device reports.  HA never silently follows the native app."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._manual_map_id = "57"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()

    # Device reports a completely different map
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # Manual override must never be cleared by a poll cycle
    assert coordinator._manual_map_id == "57"
    assert coordinator.active_map_id == "57"


@pytest.mark.asyncio
async def test_areas_fetched_for_ha_configured_map(coordinator, mock_client):
    """Areas are always fetched for the HA-configured map, never the device's map_status."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._manual_map_id = None          # no user override → uses setup map_id "3"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {1, 2, 3}

    # Device reports map 4, but HA should fetch areas for map "3" (setup map)
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # Areas must have been fetched for the HA setup map_id ("3"), not device's "4"
    mock_client.get_areas.assert_called_with("3")
    assert coordinator.areas_map_id == "3"


# ── _check_for_new_areas signal behaviour ────────────────────────────────────

def test_check_for_new_areas_signals_on_first_areas(coordinator):
    """Signal fires (via call_soon) when _known_area_ids is empty and areas arrive."""
    from custom_components.rowenta_roboeye.coordinator import async_dispatcher_send

    coordinator._known_area_ids = set()  # simulate post-map-switch reset
    areas_blob = {
        "areas": [
            {"id": 5, "area_meta_data": '{"name":"Kitchen"}'},
            {"id": 6, "area_meta_data": '{"name":"Bedroom"}'},
        ]
    }

    coordinator._check_for_new_areas(areas_blob)

    # Signal is deferred through loop.call_soon so that self.data is updated
    # before callbacks run.  Verify call_soon was invoked with the correct args.
    expected_signal = f"rowenta_roboeye_areas_updated_{coordinator.config_entry.entry_id}"
    coordinator.hass.loop.call_soon.assert_called_once_with(
        async_dispatcher_send, coordinator.hass, expected_signal
    )
    assert coordinator._known_area_ids == {5, 6}


def test_check_for_new_areas_no_signal_when_empty_response(coordinator):
    """No signal when _known_area_ids is empty but API returns empty areas."""
    coordinator._known_area_ids = set()

    coordinator._check_for_new_areas({"areas": []})

    coordinator.hass.loop.call_soon.assert_not_called()


def test_check_for_new_areas_signals_on_change(coordinator):
    """Signal fires (via call_soon) when area set differs from known set."""
    from custom_components.rowenta_roboeye.coordinator import async_dispatcher_send

    coordinator._known_area_ids = {5}  # previously had area 5
    areas_blob = {"areas": [{"id": 5}, {"id": 7}]}  # now area 7 added

    coordinator._check_for_new_areas(areas_blob)

    coordinator.hass.loop.call_soon.assert_called_once()
    call_args = coordinator.hass.loop.call_soon.call_args
    assert call_args[0][0] is async_dispatcher_send
    assert coordinator._known_area_ids == {5, 7}


def test_check_for_new_areas_no_signal_when_unchanged(coordinator):
    """No signal when area set is identical to known set."""
    coordinator._known_area_ids = {5, 6}
    areas_blob = {"areas": [{"id": 5}, {"id": 6}]}

    coordinator._check_for_new_areas(areas_blob)

    coordinator.hass.loop.call_soon.assert_not_called()
