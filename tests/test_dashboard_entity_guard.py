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
    _extract_rooms,
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


def _make_hass_with_states_and_extra(
    present_entity_ids: set[str],
    unavailable_entity_ids: set[str],
    unknown_entity_ids: set[str] | None = None,
) -> MagicMock:
    """Build a hass mock where some entities are "unavailable" or "unknown"."""
    unknown_entity_ids = unknown_entity_ids or set()
    hass = MagicMock()
    hass.data = {}

    def _states_get(entity_id: str):
        if entity_id in unavailable_entity_ids:
            state = MagicMock()
            state.state = "unavailable"
            return state
        if entity_id in unknown_entity_ids:
            state = MagicMock()
            state.state = "unknown"
            return state
        if entity_id in present_entity_ids:
            state = MagicMock()
            state.state = "off"
            return state
        return None

    hass.states.get = _states_get
    return hass


def test_registered_returns_true_when_entity_is_unavailable():
    """Entities that exist in hass.states with state='unavailable' are treated
    as registered — only a missing entity (state is None) defers the save.

    Blocking on transient 'unavailable' caused the 8-second poll to time out
    when the coordinator hadn't yet completed its first tick for the new map,
    leaving the dashboard with the PREVIOUS map's entity IDs.  Stale entity
    IDs are far worse than a brief unavailable flash: Lovelace auto-refreshes
    each card when the entity transitions to a live value, so the flash is
    invisible in practice.
    """
    eids = _all_room_eids("dev", "3", 5)
    unavailable_eid = "switch.dev_map3_room_5_deep_clean"
    present = eids - {unavailable_eid}
    hass = _make_hass_with_states_and_extra(present, {unavailable_eid})
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


def test_registered_returns_true_when_sensor_is_unavailable():
    """A sensor in 'unavailable' state must not defer the save.

    Same rationale as test_registered_returns_true_when_entity_is_unavailable:
    blocking on unavailability caused timeouts that left the Rooms view showing
    the wrong map's sensor entity IDs.
    """
    eids = _all_room_eids("dev", "3", 5)
    unavailable_eid = "sensor.dev_map3_room_5_last_cleaned"
    present = eids - {unavailable_eid}
    hass = _make_hass_with_states_and_extra(present, {unavailable_eid})
    rooms = [{"id": 5, "name": "Kitchen"}]
    assert _room_entities_registered(hass, "dev", "3", rooms) is True


def test_registered_returns_true_when_button_is_unknown():
    """Button entities always have state='unknown' — this must not block the guard.

    Buttons have no persistent state in HA; their state is always 'unknown'.
    The guard must distinguish 'unknown' (normal for buttons) from
    'unavailable' (coordinator failure) and allow 'unknown' through.
    """
    eids = _all_room_eids("dev", "3", 5)
    button_eid = "button.dev_map3_clean_room_5"
    present = eids - {button_eid}
    hass = _make_hass_with_states_and_extra(present, set(), unknown_entity_ids={button_eid})
    rooms = [{"id": 5, "name": "Kitchen"}]
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
    # Coordinator reports areas as ready and matching the active map; the
    # active_map_id guard is the only reason this returns False so we know
    # the polling loop ran.
    coord = MagicMock(
        device_id="dev",
        _areas_ready=True,
        active_map_id="3",
        areas_map_id="3",
    )
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
    coord = MagicMock(
        device_id="dev",
        _areas_ready=True,
        active_map_id="3",
        areas_map_id="3",
    )
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
async def test_async_update_clears_rooms_when_areas_map_id_mismatches():
    """When areas_map_id != active_map_id the dashboard saves with an empty
    rooms list rather than rendering the previous map's stale rooms.

    Regression: after a user-initiated map switch, coordinator.areas can
    momentarily hold the old map's areas (when get_areas for the new map
    fails this tick or when the start-of-tick pop missed due to a race).
    Previously the dashboard returned False and the storage retained the
    PREVIOUSLY saved dashboard, so the user kept seeing the old map's rooms
    while the active-map dropdown already showed the new map.  The new
    behaviour writes a transitional "No rooms discovered" config so the
    UI clears immediately; the next successful commit refills it.
    """
    # Entities for OLD map A exist; NO entities exist for the active map B.
    # Without the rooms-clearing logic the dashboard would build with map A
    # rooms and time out waiting for the (non-existent) map B entities.
    hass = _make_hass_with_states(_all_room_eids("dev", "A", 5))
    coord = MagicMock(
        device_id="dev",
        _areas_ready=False,         # transition in progress
        active_map_id="B",          # user just switched to B
        areas_map_id=None,          # B's areas not yet committed
    )
    hass.data = {"rowenta_roboeye": {"entry1": coord}}

    manager = RobEyeDashboardManager(device_id="dev", friendly_name="Test")
    _fast_poll(manager)

    mock_store = AsyncMock()
    saved_configs: list[dict] = []
    async def _save(cfg):
        saved_configs.append(cfg)
    mock_store.async_save = _save
    manager._async_get_lovelace_store = AsyncMock(return_value=mock_store)

    # areas passed in carries the OLD map A rooms (the inconsistent snapshot).
    result = await manager.async_update(
        hass=hass,
        areas=[{"id": 5, "area_meta_data": '{"name": "Kitchen"}'}],
        device_id="dev",
        active_map_id="B",
    )

    assert result is True, "save must proceed (with empty rooms) to clear stale UI"
    assert len(saved_configs) == 1, "transitional dashboard must be written to storage"
    # The Rooms view must NOT contain the stale "Kitchen" room card.
    rooms_view = next(v for v in saved_configs[0]["views"] if v["title"] == "Rooms")
    rendered_titles = [c.get("title", "") for c in rooms_view["cards"]]
    assert "Kitchen" not in rendered_titles, (
        "Rooms view must not render rooms whose areas were fetched for a different map"
    )


@pytest.mark.asyncio
async def test_async_update_aborts_when_active_map_changes_mid_wait():
    """If the user switches maps while we're polling for entity readiness,
    abort cleanly so an intermediate map's config is never saved."""
    # areas_map_id matches the requested active_map_id ("3") so the rooms
    # list is preserved for the readiness poll — coordinator.active_map_id
    # changing mid-wait to "9" is the only thing that triggers the abort.
    hass = _make_hass_with_states(set())  # entities never appear
    coord = MagicMock(
        device_id="dev",
        _areas_ready=True,
        active_map_id="9",
        areas_map_id="3",
    )
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
    coord = MagicMock(
        device_id="dev",
        _areas_ready=True,
        active_map_id="3",
        areas_map_id="3",
    )
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


# ── Blocking area filter in _extract_rooms ────────────────────────────

def test_extract_rooms_excludes_blocking_areas():
    """Blocking areas (virtual walls / forbidden zones) must not appear in the
    rooms list even when they have a name.

    Root cause of the 30-60 s dashboard-delay regression: _extract_rooms used
    to include blocking areas, so _room_entities_registered waited 8 s for
    entities that the platform builders never create (they skip
    area_state == 'blocking').  Each timeout pushed the retry 15 s into the
    future via the coordinator-tick schedule.
    """
    areas = [
        {"id": 1, "area_meta_data": '{"name": "Living Room"}', "area_state": "clean"},
        {"id": 2, "area_meta_data": '{"name": "No Go Zone"}', "area_state": "blocking"},
        {"id": 3, "area_meta_data": '{"name": "Kitchen"}', "area_state": "inactive"},
    ]
    rooms = _extract_rooms(areas)
    ids = {r["id"] for r in rooms}
    assert 2 not in ids, "blocking area must be excluded"
    assert 1 in ids and 3 in ids, "non-blocking named areas must be included"


def test_registered_returns_true_when_only_normal_rooms_have_entities():
    """Guard must pass when normal-room entities exist, even if a blocking
    area's entities are absent (they are never created by the platforms).

    Without the _extract_rooms fix, _room_entities_registered would include
    the blocking area in `rooms` and loop until timeout because no entity for
    that area_id is ever written to hass.states.
    """
    # Only room 1 (clean) gets entities; room 2 is blocking → no entities.
    eids_1 = _all_room_eids("dev", "3", 1)
    hass = _make_hass_with_states(eids_1)

    # After the fix, _extract_rooms omits the blocking room (id=2).
    rooms = _extract_rooms([
        {"id": 1, "area_meta_data": '{"name": "Living Room"}', "area_state": "clean"},
        {"id": 2, "area_meta_data": '{"name": "No Go Zone"}',  "area_state": "blocking"},
    ])
    # rooms contains only room 1; _room_entities_registered must be True.
    assert _room_entities_registered(hass, "dev", "3", rooms) is True
