"""Tests for the dashboard room-entity existence guard.

Regression: switching maps sometimes produced a dashboard that referenced
per-room entity IDs for the new map before the platform entity-add tasks
had registered those entities in hass.states.  Lovelace then rendered
"unavailable" cards until the next save.

_room_entities_registered defers the save until the expected entities
exist, closing the race.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.const import room_selection_entity_id
from custom_components.rowenta_roboeye.dashboard import (
    RobEyeDashboardManager,
    _room_entities_registered,
)


def _make_hass_with_states(present_entity_ids: set[str]) -> MagicMock:
    hass = MagicMock()
    hass.data = {}

    def _states_get(entity_id: str):
        if entity_id in present_entity_ids:
            state = MagicMock()
            state.state = "off"
            return state
        return None

    hass.states.get = _states_get
    return hass


# ── _room_entities_registered ────────────────────────────────────────

def test_registered_returns_true_when_no_rooms():
    hass = _make_hass_with_states(set())
    assert _room_entities_registered(hass, "dev", "3", []) is True


def test_registered_returns_true_when_no_active_map():
    hass = _make_hass_with_states(set())
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "", rooms) is True


def test_registered_returns_false_when_entity_missing():
    """Entity not in hass.states yet — defer dashboard save."""
    hass = _make_hass_with_states(set())
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_true_when_all_entities_exist():
    eid_5 = room_selection_entity_id("dev", "3", "5")
    eid_6 = room_selection_entity_id("dev", "3", "6")
    hass = _make_hass_with_states({eid_5, eid_6})
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


def test_registered_returns_false_when_some_entities_missing():
    """Partial registration — still defer; the batch is added atomically later."""
    eid_5 = room_selection_entity_id("dev", "3", "5")  # only 5 present
    hass = _make_hass_with_states({eid_5})
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_checks_correct_map_id():
    """After A→B switch, entities for map A exist but dashboard needs map B."""
    eid_a = room_selection_entity_id("dev", "A", "5")
    hass = _make_hass_with_states({eid_a})
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "B", rooms) is False


def test_registered_ignores_rooms_with_no_id():
    hass = _make_hass_with_states(set())
    rooms = [{"id": None, "name": "Broken"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


# ── RobEyeDashboardManager.async_update integration ───────────────────

@pytest.mark.asyncio
async def test_async_update_defers_when_entities_missing():
    """async_update returns False without saving when room entities don't exist."""
    hass = _make_hass_with_states(set())  # no entities present
    # Simulate a coordinator that reports areas as ready (no map-switch guard hit)
    hass.data = {"rowenta_roboeye": {"entry1": MagicMock(device_id="dev", _areas_ready=True)}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    manager._async_get_lovelace_store = AsyncMock()  # must NOT be called

    areas = [
        {"id": 5, "area_meta_data": '{"name": "Kitchen"}'},
    ]

    result = await manager.async_update(
        hass=hass,
        areas=areas,
        device_id="dev",
        active_map_id="3",
    )

    assert result is False
    manager._async_get_lovelace_store.assert_not_called()
    assert manager._last_hash is None  # no save happened


@pytest.mark.asyncio
async def test_async_update_proceeds_when_entities_present():
    """async_update continues to save when all room entities exist."""
    eid = room_selection_entity_id("dev", "3", "5")
    hass = _make_hass_with_states({eid})
    hass.data = {"rowenta_roboeye": {"entry1": MagicMock(device_id="dev", _areas_ready=True)}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")

    mock_store = AsyncMock()
    mock_store.async_save = AsyncMock()
    manager._async_get_lovelace_store = AsyncMock(return_value=mock_store)

    areas = [
        {"id": 5, "area_meta_data": '{"name": "Kitchen"}'},
    ]

    result = await manager.async_update(
        hass=hass,
        areas=areas,
        device_id="dev",
        active_map_id="3",
    )

    assert result is True
    manager._async_get_lovelace_store.assert_called_once()
    mock_store.async_save.assert_called_once()


@pytest.mark.asyncio
async def test_async_update_still_respects_areas_ready_guard():
    """When _areas_ready is False the earlier guard still short-circuits."""
    hass = _make_hass_with_states(set())
    hass.data = {"rowenta_roboeye": {"entry1": MagicMock(device_id="dev", _areas_ready=False)}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    manager._async_get_lovelace_store = AsyncMock()

    result = await manager.async_update(
        hass=hass,
        areas=[{"id": 5, "area_meta_data": '{"name": "Kitchen"}'}],
        device_id="dev",
        active_map_id="3",
    )

    assert result is False
    manager._async_get_lovelace_store.assert_not_called()
