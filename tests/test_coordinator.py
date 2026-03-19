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
from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator

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
    mock_client.get_areas.assert_called_once_with("3")
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

    await coordinator._async_update_data()
    assert mock_client.get_areas.call_count == 1
    assert mock_client.get_statistics.call_count == 0


@pytest.mark.asyncio
async def test_statistics_fetched_after_600s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_statistics = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_STATISTICS + 1)
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()

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
