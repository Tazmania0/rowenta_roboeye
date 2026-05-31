"""Tests for the snapshot-model map-switch and areas-cache architecture."""
from __future__ import annotations

from datetime import datetime, timedelta
from homeassistant.util import dt as dt_util
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.coordinator import AreaSnapshot, RobEyeCoordinator
from custom_components.rowenta_roboeye.const import (
    DATA_AREAS,
    DATA_STATUS,
    DATA_STATISTICS,
    SCAN_INTERVAL_BACKGROUND,
)

from .conftest import MOCK_AREAS, MOCK_MAPS, MOCK_STATUS, MOCK_STATISTICS


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_status.return_value = dict(MOCK_STATUS)
    client.get_statistics.return_value = dict(MOCK_STATISTICS)
    client.get_permanent_statistics.return_value = {}
    client.get_areas.return_value = dict(MOCK_AREAS)
    client.get_wifi_status.return_value = {}
    client.get_robot_id.return_value = {}
    client.get_protocol_version.return_value = {}
    client.get_live_parameters.return_value = {}
    client.get_rob_pose.return_value = {"valid": False}
    client.get_sensor_status.return_value = {}
    client.get_sensor_values.return_value = {}
    client.get_seen_polygon.return_value = {}
    client.get_feature_map.return_value = {}
    client.get_tile_map.return_value = {}
    client.get_schedule.return_value = {}
    client.get_robot_flags.return_value = {}
    client.get_cleaning_grid_map.return_value = {}
    client.get_map_status.return_value = {}
    client.get_maps.return_value = dict(MOCK_MAPS)
    client.get_localization.return_value = {}
    client.get_relocalization.return_value = {}
    client.get_exploration.return_value = {}
    client.get_cleaning_parameter_set.return_value = {}
    return client


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"host": "192.168.1.100", "map_id": "3", "serial": "SN001"}
    entry.options = {}
    return entry


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    hass = MagicMock()
    coord = RobEyeCoordinator(
        hass=hass,
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    coord._is_live_map_enabled = lambda: False
    return coord


# ── active_map_id initial state ────────────────────────────────────────────────


def test_active_map_id_starts_at_setup_map(coordinator):
    """active_map_id is initialised to the setup-time map_id."""
    assert coordinator.active_map_id == "3"
    assert coordinator._active_map_id == "3"
    assert coordinator.committed_active_map_id == "3"  # deprecated alias


def test_areas_for_empty_before_first_fetch(coordinator):
    """areas_for() returns [] before the first /get/areas call."""
    assert coordinator.areas_for("3") == []
    assert coordinator.areas_for("99") == []


# ── async_set_active_map ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_active_map_immediately_changes_active_map_id(coordinator):
    """async_set_active_map immediately flips active_map_id regardless of cache state."""
    await coordinator.async_set_active_map("57")

    assert coordinator._active_map_id == "57"
    assert coordinator.active_map_id == "57"
    assert coordinator.committed_active_map_id == "57"


@pytest.mark.asyncio
async def test_set_active_map_resets_session_state(coordinator):
    """async_set_active_map resets geometry/live-map/session state."""
    coordinator._last_map_geometry = dt_util.utcnow()
    coordinator._last_live_map = dt_util.utcnow()
    coordinator._robot_path = [(1.0, 2.0)]
    coordinator._session_complete = True

    await coordinator.async_set_active_map("57")

    assert coordinator._last_map_geometry is None
    assert coordinator._last_live_map is None
    assert coordinator._robot_path == []
    assert coordinator._session_complete is False


@pytest.mark.asyncio
async def test_set_active_map_noop_when_already_active(coordinator):
    """async_set_active_map is a no-op when switching to the already-active map."""
    coordinator.async_request_refresh = AsyncMock()
    coordinator._active_map_id = "3"

    await coordinator.async_set_active_map("3")

    coordinator.async_request_refresh.assert_not_called()


# ── Per-map areas cache (_areas_snapshot) ────────────────────────────────────


def test_areas_cached_per_map_independently(coordinator):
    """_areas_snapshot stores results for multiple maps independently."""
    snap3 = AreaSnapshot.from_blob(MOCK_AREAS)
    map7_blob = {"areas": [{"id": 77, "area_meta_data": '{"name":"Garage"}'}]}
    snap7 = AreaSnapshot.from_blob(map7_blob)

    coordinator._areas_snapshot["3"] = snap3
    coordinator._areas_snapshot["7"] = snap7

    assert coordinator.areas_for("3") == MOCK_AREAS.get("areas", [])
    assert coordinator.areas_for("7") == map7_blob.get("areas", [])
    assert coordinator.areas_for("99") == []


def test_areas_property_reads_from_active_map(coordinator):
    """coordinator.areas returns areas for the active map, not any other."""
    snap3 = AreaSnapshot.from_blob(MOCK_AREAS)
    map7_blob = {"areas": [{"id": 77, "area_meta_data": '{"name":"Garage"}'}]}
    snap7 = AreaSnapshot.from_blob(map7_blob)

    coordinator._areas_snapshot["3"] = snap3
    coordinator._areas_snapshot["7"] = snap7
    coordinator._active_map_id = "3"

    assert coordinator.areas == MOCK_AREAS.get("areas", [])

    coordinator._active_map_id = "7"
    assert coordinator.areas == map7_blob.get("areas", [])


# ── Entity availability gate ──────────────────────────────────────────────────


def test_entity_available_check_uses_active_map_id():
    """The entity availability pattern: self._map_id == coordinator.active_map_id."""
    coord = MagicMock()
    coord.active_map_id = "3"
    coord.committed_active_map_id = "3"

    entity_map_id = "3"
    assert entity_map_id == coord.active_map_id  # available

    coord.active_map_id = "7"
    assert entity_map_id != coord.active_map_id  # unavailable


# ── async_load_all_map_areas ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_all_map_areas_populates_snapshot(coordinator, mock_client):
    """async_load_all_map_areas fetches areas for every permanent map and populates _areas_snapshot."""
    mock_client.get_areas.return_value = MOCK_AREAS

    await coordinator.async_load_all_map_areas()

    permanent_maps = [
        str(e.get("map_id"))
        for e in MOCK_MAPS.get("maps", [])
        if str(e.get("permanent_flag", "")).strip().lower() == "true"
    ]
    for mid in permanent_maps:
        assert mid in coordinator._areas_snapshot, f"map {mid} areas not cached"


@pytest.mark.asyncio
async def test_load_all_map_areas_skips_empty_areas(coordinator, mock_client):
    """async_load_all_map_areas skips maps that return empty areas."""
    mock_client.get_areas.return_value = {"areas": []}

    await coordinator.async_load_all_map_areas()

    assert len(coordinator._areas_snapshot) == 0


# ── invalidate_areas_cache ────────────────────────────────────────────────────


def test_invalidate_areas_cache_clears_background_fetch_timer(coordinator):
    """invalidate_areas_cache() clears _last_background_fetch so the next poll re-fetches."""
    coordinator._last_background_fetch = dt_util.utcnow()

    coordinator.invalidate_areas_cache()

    assert coordinator._last_background_fetch is None


def test_invalidate_areas_cache_forces_next_refresh(coordinator):
    """After invalidate_areas_cache(), _last_background_fetch is None — the next poll triggers background refresh."""
    coordinator._last_background_fetch = dt_util.utcnow() - timedelta(seconds=10)
    coordinator.invalidate_areas_cache()

    assert coordinator._last_background_fetch is None


# ── Active map stability across polls ────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_map_id_not_changed_by_poll(coordinator, mock_client):
    """active_map_id is never changed by coordinator polling; only async_set_active_map can change it."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_background_fetch = None  # trigger background refresh
    coordinator._last_statistics = dt_util.utcnow()
    coordinator._last_robot_info = dt_util.utcnow()
    coordinator._last_map_geometry = dt_util.utcnow()

    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    assert coordinator.active_map_id == "3"  # unchanged; device-reported map is ignored


@pytest.mark.asyncio
async def test_areas_populated_for_permanent_maps_after_poll(coordinator, mock_client):
    """After a background refresh, _areas_snapshot is populated for permanent maps."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS}
    coordinator._last_background_fetch = None  # trigger background refresh
    coordinator._last_statistics = dt_util.utcnow()
    coordinator._last_robot_info = dt_util.utcnow()
    coordinator._last_map_geometry = dt_util.utcnow()

    mock_client.get_areas.return_value = MOCK_AREAS

    await coordinator._async_update_data()

    assert coordinator.areas_for("3") != []


def test_setup_map_id_stable_regardless_of_map_status(coordinator):
    """coordinator.map_id always reflects the setup-time map_id."""
    coordinator.data = {"map_status": {"active_map_id": 4, "operation_map_id": 4}}
    assert coordinator.map_id == "3"
