"""Unit tests for the RobEye DataUpdateCoordinator."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.api import CannotConnect
from custom_components.rowenta_roboeye.const import (
    DATA_AREAS,
    DATA_LIVE_PARAMETERS,
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
    _extract_relocalization_position,
    _extract_localization_position,
    _extract_exploration_position,
)

from .conftest import MOCK_AREAS, MOCK_CLEANING_GRID, MOCK_STATISTICS, MOCK_STATUS


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    hass = MagicMock()
    return RobEyeCoordinator(
        hass=hass,
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )


# ── First update — all resource groups must be fetched ────────────────

@pytest.mark.asyncio
async def test_first_update_fetches_all_groups(coordinator, mock_client):
    coordinator.data = {}
    await coordinator._async_update_data()

    mock_client.get_status.assert_called_once()
    mock_client.get_live_parameters.assert_called_once()
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


# ── Position extraction helpers ───────────────────────────────────────

def test_extract_relocalization_uses_last_continuous():
    """Uses the LAST 'continuous' entry (highest rtc_time = most recent)."""
    data = {"localization_algo_input": [
        {"localization_type": "continuous",
         "rob_pose": [-139, -48, 4012],
         "rtc_time": {"year": 2026, "month": 3, "day": 18}},
        {"localization_type": "continuous",
         "rob_pose": [-661, 235, -6269],   # ← last entry, should be used
         "rtc_time": {"year": 2026, "month": 3, "day": 18}},
    ]}
    pos = _extract_relocalization_position(data)
    assert pos is not None
    assert pos["x"] == -661
    assert pos["y"] == 235
    assert pos["source"] == "relocalization"
    assert pos["is_live"] is True
    assert abs(pos["heading_deg"] - (-34.4)) < 0.2


def test_extract_relocalization_no_continuous_returns_none():
    data = {"localization_algo_input": [
        {"localization_type": "global", "rob_pose": [0, 0, 0]},
    ]}
    assert _extract_relocalization_position(data) is None


def test_extract_relocalization_empty_returns_none():
    assert _extract_relocalization_position({}) is None


def test_extract_localization_prefers_global():
    """Prefers 'global' over 'startpoint' when both present."""
    data = {"localization_algo_input": [
        {"localization_type": "startpoint", "rob_pose": [-66, 16, 1488]},
        {"localization_type": "global",     "rob_pose": [-6, -9, 5295]},
    ]}
    pos = _extract_localization_position(data)
    assert pos is not None
    assert pos["x"] == -6
    assert pos["y"] == -9
    assert pos["is_live"] is False
    assert pos["source"] == "localization"


def test_extract_localization_falls_back_to_startpoint():
    data = {"localization_algo_input": [
        {"localization_type": "startpoint", "rob_pose": [-66, 16, 1488]},
    ]}
    pos = _extract_localization_position(data)
    assert pos is not None
    assert pos["x"] == -66
    assert pos["is_live"] is False


def test_extract_localization_empty_returns_none():
    assert _extract_localization_position({}) is None


def test_extract_exploration_uses_highest_ts():
    """Uses entry with highest 'ts' value (most recent navigation decision)."""
    data = {"exploration_points": [
        {"ts": 474766129, "type": "smsu_fail_plan",
         "rob_pose": [-8, 3, 1832]},
        {"ts": 474811434, "type": "smsu_no_nearby_expl_points",
         "rob_pose": [-861, 352, -6298]},   # ← highest ts
    ]}
    pos = _extract_exploration_position(data)
    assert pos is not None
    assert pos["x"] == -861
    assert pos["y"] == 352
    assert pos["ts"] == 474811434
    assert pos["source"] == "exploration"
    assert pos["is_live"] is True
    assert pos["event_type"] == "smsu_no_nearby_expl_points"


def test_extract_exploration_empty_points_returns_none():
    assert _extract_exploration_position({"exploration_points": []}) is None
    assert _extract_exploration_position({}) is None


# ── Position tracking bug-fixes ───────────────────────────────────────

@pytest.mark.asyncio
async def test_new_session_resets_last_live_map(coordinator, mock_client):
    """_last_live_map is cleared when a new cleaning session starts so the first
    coordinator tick fetches a fresh robot position immediately instead of waiting
    up to 60 s for the idle-mode throttle to expire."""
    coordinator.data = {}
    coordinator._last_mode = "ready"
    coordinator._last_live_map = datetime.utcnow()  # simulate recent idle update

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning"}
    await coordinator._async_update_data()

    assert coordinator._last_live_map is not None  # updated by this tick
    # The reset happened before the live-map block ran, so the block was entered
    # unconditionally (is_active=True also guarantees this, but the reset ensures
    # correctness even in edge cases where mode detection is ambiguous).
    mock_client.get_live_parameters.assert_called_once()


def _make_live_map_kwargs(**overrides):
    """Return a minimal valid kwarg dict for _build_live_map_payload."""
    base = dict(
        existing={},
        live_params={},
        robot_position=None,
        seen_polygon_raw={},
        cleaning_grid={},
        feature_map={},
        tile_map={},
        areas_data={},
        seen_poly_saved_map={},
        is_active=False,
        is_live_map=False,
        map_id="3",
        operation_map_id="3",
        robot_path=[],
        last_session_grid={},
        last_session_path=[],
        last_session_outline=[],
        session_complete=False,
    )
    base.update(overrides)
    return base


def test_idle_robot_shown_at_dock_when_localization_stale():
    """When idle and dock position is known, robot is placed at the dock rather
    than at the stale last-active-cleaning position from /debug/localization."""
    stale_localization = {
        "x": 500, "y": 300, "heading_deg": 45.0,
        "source": "localization", "is_live": False,
    }
    dock = {"x": 10, "y": 20, "heading_deg": 90.0}
    feature_map = {"map": {"docking_pose": {"x": 10, "y": 20, "heading": 16380, "valid": True}}}

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=stale_localization,
        feature_map=feature_map,
    ))

    robot = payload["robot"]
    assert robot is not None
    assert robot["source"] == "dock", "idle robot should report from dock, not stale localization"
    assert robot["x"] == 10
    assert robot["y"] == 20
    assert robot["is_live"] is False


def test_idle_robot_shown_at_dock_when_no_localization():
    """When idle and robot_position is None (first start / no localization data),
    the dock position is still used so the robot shows up on the map."""
    feature_map = {"map": {"docking_pose": {"x": 5, "y": 15, "heading": 0, "valid": True}}}

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=None,
        feature_map=feature_map,
    ))

    robot = payload["robot"]
    assert robot is not None
    assert robot["source"] == "dock"
    assert robot["x"] == 5
    assert robot["y"] == 15


def test_active_robot_position_not_overridden_by_dock():
    """During active cleaning the live relocalization position is kept; dock
    position must not override it."""
    live_pos = {
        "x": 200, "y": 100, "heading_deg": 10.0,
        "source": "relocalization", "is_live": True,
    }
    feature_map = {"map": {"docking_pose": {"x": 5, "y": 15, "heading": 0, "valid": True}}}

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=live_pos,
        feature_map=feature_map,
        is_active=True,
    ))

    robot = payload["robot"]
    assert robot["source"] == "relocalization"
    assert robot["x"] == 200
