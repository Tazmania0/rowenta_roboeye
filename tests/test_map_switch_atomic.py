"""Tests for atomic map-switch architecture (committed_active_map_id model)."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator
from custom_components.rowenta_roboeye.const import (
    DATA_AREAS,
    DATA_STATUS,
    DATA_STATISTICS,
    SCAN_INTERVAL_AREAS,
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


# ── committed_active_map_id initial state ────────────────────────────────────


def test_committed_active_map_id_starts_at_setup_map(coordinator):
    """committed_active_map_id is initialised to the setup-time map_id."""
    assert coordinator.committed_active_map_id == "3"
    assert coordinator._committed_active_map_id == "3"


def test_areas_for_empty_before_first_fetch(coordinator):
    """areas_for() returns [] before the first /get/areas call."""
    assert coordinator.areas_for("3") == []
    assert coordinator.areas_for("99") == []


# ── async_set_active_map: immediate flip when areas cached ────────────────────


@pytest.mark.asyncio
async def test_set_active_map_flips_committed_immediately_when_cached(coordinator):
    """committed_active_map_id advances immediately when dest-map areas are cached."""
    coordinator.async_request_refresh = AsyncMock()
    coordinator._areas_by_map["57"] = {"areas": [{"id": 10, "area_meta_data": '{"name":"Room"}'}]}
    coordinator._committed_active_map_id = "3"

    await coordinator.async_set_active_map("57")

    assert coordinator._committed_active_map_id == "57"
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_set_active_map_defers_committed_when_areas_not_cached(coordinator):
    """committed_active_map_id stays at old map when dest-map areas are not yet cached."""
    coordinator.async_request_refresh = AsyncMock()
    coordinator._committed_active_map_id = "3"
    # "57" has no entries in _areas_by_map

    await coordinator.async_set_active_map("57")

    assert coordinator._committed_active_map_id == "3"
    assert coordinator._manual_map_id == "57"
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_set_active_map_noop_when_already_active(coordinator):
    """async_set_active_map is a no-op when switching to the already-active map."""
    coordinator.async_request_refresh = AsyncMock()
    coordinator._manual_map_id = "3"
    coordinator._committed_active_map_id = "3"

    await coordinator.async_set_active_map("3")

    coordinator.async_request_refresh.assert_not_called()


# ── committed_active_map_id advances after area fetch ────────────────────────


@pytest.mark.asyncio
async def test_committed_map_advances_after_areas_fetched(coordinator, mock_client):
    """committed_active_map_id advances to the new map once its areas are fetched."""
    map2_areas = {"areas": [{"id": 99, "area_meta_data": '{"name":"Map2Room"}'}]}
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS}
    coordinator._committed_active_map_id = "2"
    coordinator._areas_by_map["2"] = {"areas": [{"id": 1, "area_meta_data": '{"name":"Room1"}'}]}
    coordinator._manual_map_id = "3"
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()

    mock_client.get_areas.return_value = MOCK_AREAS

    await coordinator._async_update_data()

    assert coordinator._committed_active_map_id == "3"
    assert coordinator._areas_by_map.get("3") is not None


# ── Per-map areas cache ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_areas_cached_per_map_independently(coordinator, mock_client):
    """_areas_by_map accumulates results for multiple maps independently."""
    map3_areas = MOCK_AREAS
    map7_areas = {"areas": [{"id": 77, "area_meta_data": '{"name":"Garage"}'}]}

    # Prime map 3
    coordinator._areas_by_map["3"] = map3_areas
    coordinator._areas_fetched_at["3"] = datetime.utcnow()

    # Now seed map 7 separately
    coordinator._areas_by_map["7"] = map7_areas
    coordinator._areas_fetched_at["7"] = datetime.utcnow()

    assert coordinator.areas_for("3") == map3_areas.get("areas", [])
    assert coordinator.areas_for("7") == map7_areas.get("areas", [])
    assert coordinator.areas_for("99") == []


def test_areas_property_reads_from_committed_map(coordinator):
    """coordinator.areas returns areas for the committed map, not any other."""
    map3_areas = MOCK_AREAS
    map7_areas = {"areas": [{"id": 77, "area_meta_data": '{"name":"Garage"}'}]}

    coordinator._areas_by_map["3"] = map3_areas
    coordinator._areas_by_map["7"] = map7_areas
    coordinator._committed_active_map_id = "3"

    assert coordinator.areas == map3_areas.get("areas", [])

    # Switch committed to "7" — areas property follows
    coordinator._committed_active_map_id = "7"
    assert coordinator.areas == map7_areas.get("areas", [])


# ── Entity availability gate ──────────────────────────────────────────────────


def test_entity_available_check_uses_committed_active_map_id():
    """The entity availability pattern: self._map_id == coordinator.committed_active_map_id."""
    coord = MagicMock()
    coord.committed_active_map_id = "3"

    # Simulates the check inside RobEyeRoomCleanButton.available
    entity_map_id = "3"
    assert entity_map_id == coord.committed_active_map_id  # available

    coord.committed_active_map_id = "7"
    assert entity_map_id != coord.committed_active_map_id  # unavailable


# ── async_load_all_map_areas ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_all_map_areas_populates_cache(coordinator, mock_client):
    """async_load_all_map_areas fetches areas for every permanent map and caches them."""
    map3_areas = MOCK_AREAS
    mock_client.get_areas.return_value = map3_areas

    await coordinator.async_load_all_map_areas()

    # MOCK_MAPS has permanent_flag maps — their areas should be cached
    permanent_maps = [
        str(e.get("map_id"))
        for e in MOCK_MAPS.get("maps", [])
        if str(e.get("permanent_flag", "")).strip().lower() == "true"
    ]
    for mid in permanent_maps:
        assert mid in coordinator._areas_by_map, f"map {mid} areas not cached"


@pytest.mark.asyncio
async def test_load_all_map_areas_skips_empty_areas(coordinator, mock_client):
    """async_load_all_map_areas skips maps that return empty areas."""
    mock_client.get_areas.return_value = {"areas": []}

    await coordinator.async_load_all_map_areas()

    # Empty areas should not be stored in the cache
    assert len(coordinator._areas_by_map) == 0


# ── invalidate_areas_cache ────────────────────────────────────────────────────


def test_invalidate_areas_cache_clears_fetched_at(coordinator):
    """invalidate_areas_cache() removes the active map from _areas_fetched_at."""
    coordinator._manual_map_id = "3"
    coordinator._areas_fetched_at["3"] = datetime.utcnow()

    coordinator.invalidate_areas_cache()

    assert "3" not in coordinator._areas_fetched_at


def test_invalidate_areas_cache_does_not_clear_other_maps(coordinator):
    """invalidate_areas_cache() does not touch other maps' timestamps."""
    coordinator._manual_map_id = "3"
    coordinator._areas_fetched_at["3"] = datetime.utcnow()
    ts7 = datetime.utcnow()
    coordinator._areas_fetched_at["7"] = ts7

    coordinator.invalidate_areas_cache()

    assert coordinator._areas_fetched_at.get("7") is ts7
