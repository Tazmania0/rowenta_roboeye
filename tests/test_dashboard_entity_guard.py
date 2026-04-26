"""Tests for the dashboard room-entity existence guard.

Regression: switching maps sometimes produced a dashboard that referenced
per-room entity IDs for the new map before the platform entity-add tasks
had registered those entities in hass.states.  Lovelace then rendered
"unavailable" cards until the next save.

_room_entities_registered defers the save until every per-room entity that
the dashboard config actually references exists in hass.states, including
sensors (which are added by a separate platform listener and can lag
behind button/select/switch).  Earlier revisions probed only three of the
nine per-room entity types and would let the dashboard save with sensor
cards rendering "unavailable".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.const import room_selection_entity_id
from custom_components.rowenta_roboeye.dashboard import (
    RobEyeDashboardManager,
    _room_entities_registered,
)


def _all_room_eids(device_id: str, map_id: str, rid) -> set[str]:
    """Return the full set of per-room entity_ids _room_entities_registered probes."""
    m = f"map{map_id}_"
    return {
        f"sensor.{device_id}_{m}room_{rid}_last_cleaned",
        f"sensor.{device_id}_{m}room_{rid}_cleanings",
        f"sensor.{device_id}_{m}room_{rid}_area",
        f"sensor.{device_id}_{m}room_{rid}_avg_clean_time",
        f"button.{device_id}_{m}clean_room_{rid}",
        f"select.{device_id}_{m}room_{rid}_fan_speed",
        f"select.{device_id}_{m}room_{rid}_strategy",
        f"switch.{device_id}_{m}room_{rid}_deep_clean",
        room_selection_entity_id(device_id, map_id, str(rid)),
    }


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


def test_registered_returns_false_when_only_room_selection_switch_present():
    """Selection switch alone must not satisfy the guard.

    Earlier revisions checked only the room-selection switch (a proxy
    that is added in the same batch as the deep-clean switch); when
    sensor / button / fan-speed entities lagged behind, the dashboard
    saved with those cards rendering unavailable.
    """
    sel = room_selection_entity_id("dev", "3", "5")
    hass = _make_hass_with_states({sel})
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_false_when_only_button_select_switch_present():
    """The pre-fix guard checked only button + fan-speed select + deep-clean
    switch.  With sensors still missing, that subset must NOT pass the new
    guard — otherwise the dashboard saves with sensor cards unavailable."""
    m = "map3_"
    legacy_subset = {
        f"button.dev_{m}clean_room_5",
        f"select.dev_{m}room_5_fan_speed",
        f"switch.dev_{m}room_5_deep_clean",
    }
    hass = _make_hass_with_states(legacy_subset)
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_true_when_all_room_entities_exist():
    """Every per-room entity type the dashboard references is present → save allowed."""
    eids_5 = _all_room_eids("dev", "3", 5)
    eids_6 = _all_room_eids("dev", "3", 6)
    hass = _make_hass_with_states(eids_5 | eids_6)
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


def test_registered_returns_false_when_one_sensor_missing():
    """Even a single missing sensor defers the save.  The "unavailable
    sensor card" is exactly the bug users see in the Rooms view."""
    eids = _all_room_eids("dev", "3", 5)
    eids.discard("sensor.dev_map3_room_5_last_cleaned")
    hass = _make_hass_with_states(eids)
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_returns_false_when_second_room_partial():
    """All rooms must be fully registered — partial registration still defers."""
    eids_5 = _all_room_eids("dev", "3", 5)   # room 5 complete
    eids_6 = {f"button.dev_map3_clean_room_6"}  # room 6: only button
    hass = _make_hass_with_states(eids_5 | eids_6)
    rooms = [{"id": 5, "name": "Kitchen"}, {"id": 6, "name": "Bedroom"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is False


def test_registered_checks_correct_map_id():
    """After A→B switch, entities for map A exist but dashboard needs map B."""
    eids_a = _all_room_eids("dev", "A", 5)
    hass = _make_hass_with_states(eids_a)
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "B", rooms) is False


def test_registered_ignores_rooms_with_no_id():
    hass = _make_hass_with_states(set())
    rooms = [{"id": None, "name": "Broken"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


# ── RobEyeDashboardManager.async_update integration ───────────────────

# Use a minimal poll budget so deferred-save tests don't block real time.
_FAST_POLL_INTERVAL = 0.001
_FAST_POLL_TIMEOUT = 0.005


def _fast_poll(manager: RobEyeDashboardManager) -> None:
    """Shrink the entity-readiness poll loop to a few ms for tests."""
    manager._ENTITY_POLL_INTERVAL_S = _FAST_POLL_INTERVAL
    manager._ENTITY_POLL_TIMEOUT_S = _FAST_POLL_TIMEOUT


@pytest.mark.asyncio
async def test_async_update_defers_when_entities_missing():
    """async_update returns False without saving when room entities never appear."""
    hass = _make_hass_with_states(set())  # no entities present
    # Coordinator reports areas as ready; the active_map_id guard is the
    # only reason this returns False so we know the polling loop ran.
    coord = MagicMock(device_id="dev", _areas_ready=True, active_map_id="3")
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)
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
    """async_update continues to save when every per-room entity exists."""
    eids = _all_room_eids("dev", "3", 5)
    hass = _make_hass_with_states(eids)
    coord = MagicMock(device_id="dev", _areas_ready=True, active_map_id="3")
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)

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
    coord = MagicMock(device_id="dev", _areas_ready=False, active_map_id="3")
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)
    manager._async_get_lovelace_store = AsyncMock()

    result = await manager.async_update(
        hass=hass,
        areas=[{"id": 5, "area_meta_data": '{"name": "Kitchen"}'}],
        device_id="dev",
        active_map_id="3",
    )

    assert result is False
    manager._async_get_lovelace_store.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_aborts_when_active_map_changes_mid_wait():
    """If the user switches maps while we're polling for entity readiness,
    abort cleanly so an intermediate map's config is never saved."""
    hass = _make_hass_with_states(set())  # entities never appear
    coord = MagicMock(device_id="dev", _areas_ready=True, active_map_id="9")
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)
    manager._async_get_lovelace_store = AsyncMock()

    # active_map_id passed in is "3" but coordinator now reports "9":
    # the very first iteration of the poll loop detects the mismatch.
    result = await manager.async_update(
        hass=hass,
        areas=[{"id": 5, "area_meta_data": '{"name": "Kitchen"}'}],
        device_id="dev",
        active_map_id="3",
    )

    assert result is False
    manager._async_get_lovelace_store.assert_not_called()
    assert manager._last_hash is None


@pytest.mark.asyncio
async def test_async_update_serializes_concurrent_callers():
    """Two concurrent async_update calls must serialize on the manager's lock,
    so neither overlaps the other's save (eliminates the racing-callsite bug)."""
    import asyncio as _asyncio

    eids = _all_room_eids("dev", "3", 5)
    hass = _make_hass_with_states(eids)
    coord = MagicMock(device_id="dev", _areas_ready=True, active_map_id="3")
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)

    in_flight = 0
    max_in_flight = 0
    save_called = 0

    async def _fake_save(_config):
        nonlocal in_flight, max_in_flight, save_called
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await _asyncio.sleep(0)  # yield so the second caller can race in
        save_called += 1
        in_flight -= 1

    mock_store = AsyncMock()
    mock_store.async_save = _fake_save
    manager._async_get_lovelace_store = AsyncMock(return_value=mock_store)

    areas = [{"id": 5, "area_meta_data": '{"name": "Kitchen"}'}]

    # Force two distinct configs by alternating titles so the hash check
    # doesn't dedupe the second save into a no-op.
    a, b = await _asyncio.gather(
        manager.async_update(
            hass=hass, areas=areas, device_id="dev",
            active_map_id="3", friendly_name="A",
        ),
        manager.async_update(
            hass=hass, areas=areas, device_id="dev",
            active_map_id="3", friendly_name="B",
        ),
    )

    assert (a, b) == (True, True)
    assert save_called == 2
    assert max_in_flight == 1, "lock must serialize concurrent saves"
