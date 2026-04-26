"""Tests for the dashboard room-entity existence guard.

Regression: switching maps sometimes produced a dashboard that referenced
per-room entity IDs for the new map before the platform entity-add tasks
had registered those entities in hass.states.  Lovelace then rendered
"unavailable" cards until the next save.

_room_entities_registered defers the save until the expected platform
entities (button / select / switch) exist in hass.states, closing the race.
The old implementation checked input_boolean helpers which are always present
before the dashboard-save call, making the guard a no-op.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.dashboard import (
    RobEyeDashboardManager,
    _room_entities_registered,
)


def _platform_eids(device_id: str, map_id: str, rid) -> tuple[str, str, str]:
    """Return the three platform entity IDs _room_entities_registered checks."""
    m = f"map{map_id}_"
    return (
        f"button.{device_id}_{m}clean_room_{rid}",
        f"select.{device_id}_{m}room_{rid}_fan_speed",
        f"switch.{device_id}_{m}room_{rid}_deep_clean",
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
    """Platform entities not in hass.states yet — defer dashboard save."""
    hass = _make_hass_with_states(set())
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_false_when_only_input_boolean_present():
    """Input-boolean helpers being present must NOT satisfy the guard.

    This is the exact bug the fix addresses: the old code checked
    input_boolean.*_selected (created before async_create_dashboard is
    called) instead of the platform entities from async_add_entities.
    """
    from custom_components.rowenta_roboeye.const import room_selection_entity_id
    input_bool_eid = room_selection_entity_id("dev", "3", "5")
    hass = _make_hass_with_states({input_bool_eid})
    rooms = [{"id": 5, "name": "Kitchen"}]
    # The guard must still return False — input_boolean is not sufficient.
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_false_when_only_button_present():
    """All three platform types must exist — partial presence still defers."""
    btn, *_ = _platform_eids("dev", "3", 5)
    hass = _make_hass_with_states({btn})
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_true_when_all_platform_entities_exist():
    """All three platform entity types present → save is allowed."""
    eids_5 = set(_platform_eids("dev", "3", 5))
    eids_6 = set(_platform_eids("dev", "3", 6))
    hass = _make_hass_with_states(eids_5 | eids_6)
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


def test_registered_returns_false_when_second_room_platform_missing():
    """All rooms must be fully registered — partial registration still defers."""
    eids_5 = set(_platform_eids("dev", "3", 5))   # room 5 complete
    # room 6: only button present, select/switch missing
    btn_6, *_ = _platform_eids("dev", "3", 6)
    hass = _make_hass_with_states(eids_5 | {btn_6})
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_checks_correct_map_id():
    """After A→B switch, entities for map A exist but dashboard needs map B."""
    eids_a = set(_platform_eids("dev", "A", 5))
    hass = _make_hass_with_states(eids_a)
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
    """async_update continues to save when all platform room entities exist."""
    eids = set(_platform_eids("dev", "3", 5))
    hass = _make_hass_with_states(eids)
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
