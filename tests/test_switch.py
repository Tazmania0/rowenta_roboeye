"""Unit tests for the switch platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.switch import (
    RobEyeDeepCleanSwitch,
    RobEyeRoomDeepCleanSwitch,
    RobEyeScheduleSwitch,
)
from custom_components.rowenta_roboeye.const import (
    AREA_STATE_BLOCKING,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_coordinator(device_id="dev123", cleaning_strategy=STRATEGY_DEFAULT, active_map_id="3", last_non_deep=STRATEGY_DEFAULT):
    coord = MagicMock()
    coord.device_id = device_id
    coord.cleaning_strategy = cleaning_strategy
    coord.last_non_deep_strategy = last_non_deep
    coord.active_map_id = active_map_id
    coord.areas_map_id = active_map_id
    coord.areas = []
    coord.async_send_command = AsyncMock()
    coord.client = MagicMock()
    # Simulate stable-state availability (no transition tracking needed in unit tests)
    coord.map_available_for = lambda mid: mid == coord.active_map_id
    return coord


def _make_config_entry(entry_id="test_entry"):
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _make_deep_clean_switch(coord=None):
    if coord is None:
        coord = _make_coordinator()
    sw = RobEyeDeepCleanSwitch.__new__(RobEyeDeepCleanSwitch)
    object.__setattr__(sw, "coordinator", coord)
    object.__setattr__(sw, "_attr_unique_id", "")
    object.__setattr__(sw, "entity_id", "")
    object.__setattr__(sw, "async_write_ha_state", MagicMock())
    RobEyeDeepCleanSwitch.__init__(sw, coord)
    return sw


def _make_room_switch(coord=None, area_id="3", room_name="Bedroom", map_id="3"):
    if coord is None:
        coord = _make_coordinator(active_map_id=map_id)
    entry = _make_config_entry()
    sw = RobEyeRoomDeepCleanSwitch.__new__(RobEyeRoomDeepCleanSwitch)
    object.__setattr__(sw, "coordinator", coord)
    object.__setattr__(sw, "_attr_unique_id", "")
    object.__setattr__(sw, "entity_id", "")
    object.__setattr__(sw, "async_write_ha_state", MagicMock())
    RobEyeRoomDeepCleanSwitch.__init__(sw, coord, entry, area_id, room_name)
    return sw


# ── RobEyeDeepCleanSwitch ─────────────────────────────────────────────


def test_deep_clean_switch_unique_id():
    coord = _make_coordinator(device_id="mydev")
    sw = _make_deep_clean_switch(coord)
    assert sw._attr_unique_id == "deep_clean_mode_mydev"


def test_deep_clean_switch_entity_id():
    coord = _make_coordinator(device_id="mydev")
    sw = _make_deep_clean_switch(coord)
    assert sw.entity_id == "switch.mydev_deep_clean_mode"


def test_deep_clean_is_on_when_strategy_deep():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEEP)
    sw = _make_deep_clean_switch(coord)
    assert sw.is_on is True


def test_deep_clean_is_off_when_strategy_default():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEFAULT)
    sw = _make_deep_clean_switch(coord)
    assert sw.is_on is False


def test_deep_clean_is_off_when_strategy_normal():
    coord = _make_coordinator(cleaning_strategy="1")
    sw = _make_deep_clean_switch(coord)
    assert sw.is_on is False


@pytest.mark.asyncio
async def test_deep_clean_turn_on_sets_strategy_deep():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEFAULT)
    sw = _make_deep_clean_switch(coord)
    await sw.async_turn_on()
    assert coord.cleaning_strategy == STRATEGY_DEEP
    sw.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_deep_clean_turn_off_restores_prior_strategy():
    """Turning off deep clean restores the last explicitly chosen non-deep strategy,
    NOT STRATEGY_DEFAULT — the user's prior selection must be preserved."""
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEEP, last_non_deep="2")  # Walls & Corners
    sw = _make_deep_clean_switch(coord)
    await sw.async_turn_off()
    assert coord.cleaning_strategy == "2"  # restored to Walls & Corners, not Default
    sw.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_deep_clean_restore_state_on():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEFAULT)
    sw = _make_deep_clean_switch(coord)

    last_state = MagicMock()
    last_state.state = "on"
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert coord.cleaning_strategy == STRATEGY_DEEP


@pytest.mark.asyncio
async def test_deep_clean_restore_state_off_does_not_change():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEFAULT)
    sw = _make_deep_clean_switch(coord)

    last_state = MagicMock()
    last_state.state = "off"
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    # "off" state: strategy should not be overridden to DEEP
    assert coord.cleaning_strategy == STRATEGY_DEFAULT


@pytest.mark.asyncio
async def test_deep_clean_restore_no_prior_state():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEFAULT)
    sw = _make_deep_clean_switch(coord)
    sw.async_get_last_state = AsyncMock(return_value=None)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert coord.cleaning_strategy == STRATEGY_DEFAULT


# ── RobEyeRoomDeepCleanSwitch ─────────────────────────────────────────


def test_room_switch_unique_id():
    sw = _make_room_switch(area_id="5", room_name="Office", map_id="3")
    assert "room_deep_clean_map3_5" in sw._attr_unique_id
    assert sw.coordinator.device_id in sw._attr_unique_id


def test_room_switch_name():
    sw = _make_room_switch(room_name="Kitchen")
    assert sw._attr_name == "Kitchen Deep Clean"


def test_room_switch_entity_id():
    coord = _make_coordinator(device_id="mydev", active_map_id="3")
    sw = _make_room_switch(coord=coord, area_id="7", map_id="3")
    assert sw.entity_id == "switch.mydev_map3_room_7_deep_clean"


def test_room_switch_is_off_by_default():
    sw = _make_room_switch()
    assert sw.is_on is False


@pytest.mark.asyncio
async def test_room_switch_turn_on():
    """Turning ON sends strategy_mode='deep' plus cleaning_parameter_set.

    Both parameters are always included so the firmware never resets the
    omitted one to a default value.
    """
    coord = _make_coordinator()
    coord.hass.states.get.return_value = None  # no fan-speed select state → fallback "1"
    sw = _make_room_switch(coord=coord, area_id="3")
    await sw.async_turn_on()
    assert sw.is_on is True
    sw.async_write_ha_state.assert_called_once()
    coord.async_send_command.assert_called_once_with(
        coord.client.modify_area,
        map_id=coord.active_map_id,
        area_id="3",
        cleaning_parameter_set="1",  # fallback when fan-speed select not found
        strategy_mode="deep",
    )


@pytest.mark.asyncio
async def test_room_switch_turn_off():
    coord = _make_coordinator()
    coord.hass.states.get.return_value = None
    sw = _make_room_switch(coord=coord, area_id="3")
    sw._is_on = True
    await sw.async_turn_off()
    assert sw.is_on is False
    sw.async_write_ha_state.assert_called_once()
    coord.async_send_command.assert_called_once_with(
        coord.client.modify_area,
        map_id=coord.active_map_id,
        area_id="3",
        cleaning_parameter_set="1",
        strategy_mode="normal",
    )


@pytest.mark.asyncio
async def test_room_switch_turn_on_preserves_fan_speed():
    """Fan speed from the per-room fan-speed select HA state is preserved on toggle."""
    coord = _make_coordinator()
    fan_state = MagicMock()
    fan_state.state = "eco"
    coord.hass.states.get.return_value = fan_state
    sw = _make_room_switch(coord=coord, area_id="3")
    await sw.async_turn_on()
    coord.async_send_command.assert_called_once_with(
        coord.client.modify_area,
        map_id=coord.active_map_id,
        area_id="3",
        cleaning_parameter_set="2",  # "eco" → "2"
        strategy_mode="deep",
    )


@pytest.mark.asyncio
async def test_room_switch_turn_on_preserves_fan_speed_from_areas():
    """When fan-speed select has no HA state, falls back to coordinator.areas."""
    coord = _make_coordinator()
    coord.hass.states.get.return_value = None  # no HA state
    coord.areas = [{"id": 3, "cleaning_parameter_set": 3, "strategy_mode": "normal"}]
    sw = _make_room_switch(coord=coord, area_id="3")
    await sw.async_turn_on()
    coord.async_send_command.assert_called_once_with(
        coord.client.modify_area,
        map_id=coord.active_map_id,
        area_id="3",
        cleaning_parameter_set="3",  # from coordinator.areas
        strategy_mode="deep",
    )


def test_room_switch_available_same_map():
    coord = _make_coordinator(active_map_id="3")
    sw = _make_room_switch(coord=coord, map_id="3")
    # _map_id matches active_map_id → available (CoordinatorEntity stub returns True)
    assert sw.available is True


def test_room_switch_unavailable_on_map_switch():
    # Entity becomes unavailable when active map changes away from its map.
    coord = _make_coordinator(active_map_id="3")
    sw = _make_room_switch(coord=coord)
    assert sw.available is True
    coord.active_map_id = "4"
    assert sw.available is False
    coord.active_map_id = "3"
    assert sw.available is True


@pytest.mark.asyncio
async def test_room_switch_restore_state_on():
    sw = _make_room_switch()

    last_state = MagicMock()
    last_state.state = "on"
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert sw._is_on is True


@pytest.mark.asyncio
async def test_room_switch_restore_state_off():
    sw = _make_room_switch()
    sw._is_on = True  # pre-set True so we can confirm restore resets it

    last_state = MagicMock()
    last_state.state = "off"
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert sw._is_on is False


@pytest.mark.asyncio
async def test_room_switch_restore_no_prior_state():
    sw = _make_room_switch()
    sw.async_get_last_state = AsyncMock(return_value=None)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert sw._is_on is False


@pytest.mark.asyncio
async def test_room_switch_seeded_deep_from_robot():
    """strategy_mode='deep' in areas seeds the switch ON on first run."""
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "area_type": "room", "area_state": "clean",
                    "area_meta_data": '{"name":"Bedroom"}',
                    "cleaning_parameter_set": 3, "strategy_mode": "deep"}]
    sw = _make_room_switch(coord=coord, area_id="3")
    sw.async_get_last_state = AsyncMock(return_value=None)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert sw._is_on is True
    assert sw._last_robot_strategy == "deep"


@pytest.mark.asyncio
async def test_room_switch_seeded_normal_stays_off():
    """strategy_mode='normal' on first run leaves switch OFF."""
    coord = _make_coordinator()
    coord.areas = [{"id": 2, "area_type": "room", "area_state": "clean",
                    "area_meta_data": '{"name":"Living"}',
                    "cleaning_parameter_set": 1, "strategy_mode": "normal"}]
    sw = _make_room_switch(coord=coord, area_id="2")
    sw.async_get_last_state = AsyncMock(return_value=None)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()
    assert sw._is_on is False
    assert sw._last_robot_strategy == "normal"


@pytest.mark.asyncio
async def test_room_switch_restore_detects_offline_native_app_change():
    """Native app turned off deep while HA was offline → first poll syncs it.

    If the restored HA state (ON) differs from the current robot value ("normal"),
    _last_robot_strategy must be left None so _handle_coordinator_update detects
    the change and turns the switch OFF on the first areas refresh.
    """
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "strategy_mode": "normal"}]  # robot says normal now
    sw = _make_room_switch(coord=coord, area_id="3")

    last_state = MagicMock()
    last_state.state = "on"  # HA last remembered deep=ON
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()

    # Baseline must be None so the first poll will detect the mismatch
    assert sw._last_robot_strategy is None
    assert sw._is_on is True  # restored, not yet synced

    # First coordinator update syncs from robot
    sw._handle_coordinator_update()
    assert sw._is_on is False
    assert sw._last_robot_strategy == "normal"


@pytest.mark.asyncio
async def test_room_switch_restore_baseline_set_when_matching():
    """When restored HA state matches robot, baseline is set immediately.

    No unnecessary update fires on the first areas refresh.
    """
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "strategy_mode": "deep"}]
    sw = _make_room_switch(coord=coord, area_id="3")

    last_state = MagicMock()
    last_state.state = "on"
    sw.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await sw.async_added_to_hass()

    assert sw._last_robot_strategy == "deep"
    assert sw._is_on is True

    # First coordinator update: robot still says "deep" → no change
    sw._handle_coordinator_update()
    assert sw._is_on is True


def test_room_switch_coordinator_update_syncs_deep():
    """Native app sets deep → switch turns ON on next areas refresh."""
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "strategy_mode": "normal"}]
    sw = _make_room_switch(coord=coord, area_id="3")
    sw._last_robot_strategy = "normal"
    sw._is_on = False

    coord.areas = [{"id": 3, "strategy_mode": "deep"}]
    sw._handle_coordinator_update()

    assert sw._is_on is True
    assert sw._last_robot_strategy == "deep"


def test_room_switch_coordinator_update_normal_clears_switch():
    """Native app turns off deep → switch turns OFF on next areas refresh.

    When _last_robot_strategy was "deep" and the robot now reports "normal",
    this is an unambiguous change — the switch must reflect it.
    """
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "strategy_mode": "deep"}]
    sw = _make_room_switch(coord=coord, area_id="3")
    sw._last_robot_strategy = "deep"
    sw._is_on = True

    coord.areas = [{"id": 3, "strategy_mode": "normal"}]
    sw._handle_coordinator_update()

    assert sw._is_on is False
    assert sw._last_robot_strategy == "normal"


def test_room_switch_coordinator_update_no_change_no_overwrite():
    """Mid-cycle: robot still reports 'normal', user just turned ON in HA — not reverted."""
    coord = _make_coordinator()
    coord.areas = [{"id": 3, "strategy_mode": "normal"}]
    sw = _make_room_switch(coord=coord, area_id="3")
    sw._last_robot_strategy = "normal"   # baseline
    sw._is_on = True                     # user just toggled in HA (write-back pending)

    sw._handle_coordinator_update()      # stale cache still shows "normal"

    assert sw._is_on is True  # must NOT be reverted


# ── RobEyeScheduleSwitch ──────────────────────────────────────────────

_SCHED_ENTRY = {
    "task_id": 2,
    "time": {"days_of_week": [7], "hour": 20, "min": 36, "sec": 0},
    "enabled": 1,
    "task": {"map_id": 3, "cleaning_parameter_set": 0,
             "cleaning_mode": 2, "parameters": [3, 10]},
}


def _make_schedule_coord(enabled: int = 1):
    entry = {**_SCHED_ENTRY, "enabled": enabled}
    coord = MagicMock()
    coord.device_id = "SN123456"
    coord.schedule = {"schedule": [entry]}
    coord.areas = []
    coord.client.set_schedule_enabled = AsyncMock(return_value={"cmd_id": 215})
    coord.async_request_refresh = AsyncMock()
    coord.invalidate_schedule_cache = MagicMock()
    coord.async_send_command = MagicMock()
    return coord, entry


def _make_schedule_switch(enabled: int = 1):
    coord, _ = _make_schedule_coord(enabled)
    sw = RobEyeScheduleSwitch.__new__(RobEyeScheduleSwitch)
    object.__setattr__(sw, "coordinator", coord)
    object.__setattr__(sw, "_attr_unique_id", "")
    object.__setattr__(sw, "entity_id", "")
    object.__setattr__(sw, "async_write_ha_state", MagicMock())
    RobEyeScheduleSwitch.__init__(sw, coord, task_id=2)
    return sw, coord


def test_schedule_switch_unique_id():
    sw, _ = _make_schedule_switch()
    assert sw._attr_unique_id == "schedule_2_SN123456"


def test_schedule_switch_entity_id():
    sw, _ = _make_schedule_switch()
    assert sw.entity_id == "switch.SN123456_schedule_2"


def test_schedule_switch_is_on():
    sw, _ = _make_schedule_switch(enabled=1)
    assert sw.is_on is True


def test_schedule_switch_is_off():
    sw, _ = _make_schedule_switch(enabled=0)
    assert sw.is_on is False


def test_schedule_switch_name_contains_time_and_day():
    sw, _ = _make_schedule_switch()
    assert "20:36" in sw.name
    assert "Sun" in sw.name


def test_schedule_switch_icon_on():
    sw, _ = _make_schedule_switch(enabled=1)
    assert sw.icon == "mdi:calendar-clock"


def test_schedule_switch_icon_off():
    sw, _ = _make_schedule_switch(enabled=0)
    assert sw.icon == "mdi:calendar-remove"


def test_schedule_switch_extra_attrs():
    sw, _ = _make_schedule_switch()
    attrs = sw.extra_state_attributes
    assert attrs["task_id"] == 2
    assert attrs["time"] == "20:36"
    assert attrs["area_ids"] == [3, 10]
    assert attrs["fan_speed"] == "default"
    assert attrs["fan_raw"] == 0
    assert attrs["map_id"] == 3


@pytest.mark.asyncio
async def test_schedule_switch_turn_on_bypasses_queue():
    """Must call client directly — never async_send_command."""
    sw, coord = _make_schedule_switch(enabled=0)
    await sw.async_turn_on()
    coord.client.set_schedule_enabled.assert_awaited_once_with(2, True)
    coord.invalidate_schedule_cache.assert_called_once()
    coord.async_request_refresh.assert_awaited_once()
    coord.async_send_command.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_switch_turn_off_bypasses_queue():
    sw, coord = _make_schedule_switch(enabled=1)
    await sw.async_turn_off()
    coord.client.set_schedule_enabled.assert_awaited_once_with(2, False)
    coord.invalidate_schedule_cache.assert_called_once()
    coord.async_request_refresh.assert_awaited_once()
    coord.async_send_command.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_switch_api_error_does_not_raise():
    """Failed toggle must log and not propagate; refresh and cache-invalidation must not be called."""
    sw, coord = _make_schedule_switch()
    coord.client.set_schedule_enabled.side_effect = Exception("timeout")
    await sw.async_turn_on()  # must not raise
    coord.invalidate_schedule_cache.assert_not_called()
    coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_switch_invalidates_cache_before_refresh():
    """Cache must be invalidated before the refresh so the refresh re-fetches schedule."""
    call_order: list[str] = []
    sw, coord = _make_schedule_switch(enabled=0)
    coord.invalidate_schedule_cache.side_effect = lambda: call_order.append("invalidate")
    coord.async_request_refresh.side_effect = lambda: call_order.append("refresh") or __import__("asyncio").coroutine(lambda: None)()

    # Use an async side_effect for async_request_refresh
    async def _refresh():
        call_order.append("refresh")
    coord.async_request_refresh.side_effect = _refresh

    await sw.async_turn_on()
    assert call_order == ["invalidate", "refresh"]


@pytest.mark.asyncio
async def test_schedule_switch_optimistic_on_turn_on():
    """is_on must return True immediately after turn_on, before coordinator refreshes."""
    sw, coord = _make_schedule_switch(enabled=0)
    assert sw.is_on is False
    # Don't await — check state after the optimistic write but before refresh resolves.
    # We trigger the full flow and inspect the intermediate _optimistic_enabled value.
    coord.async_request_refresh = AsyncMock()
    await sw.async_turn_on()
    # After a successful call the optimistic state is still set until coordinator confirms.
    # (coordinator mock doesn't fire _handle_coordinator_update, so it stays set.)
    assert sw._optimistic_enabled is True
    assert sw.is_on is True


@pytest.mark.asyncio
async def test_schedule_switch_optimistic_on_turn_off():
    """is_on must return False immediately after turn_off."""
    sw, coord = _make_schedule_switch(enabled=1)
    assert sw.is_on is True
    coord.async_request_refresh = AsyncMock()
    await sw.async_turn_off()
    assert sw._optimistic_enabled is False
    assert sw.is_on is False


@pytest.mark.asyncio
async def test_schedule_switch_api_error_reverts_optimistic():
    """Failed toggle must revert optimistic state so the real value is shown."""
    sw, coord = _make_schedule_switch(enabled=0)
    coord.client.set_schedule_enabled.side_effect = Exception("timeout")
    await sw.async_turn_on()
    assert sw._optimistic_enabled is None
    # is_on should fall back to coordinator data (enabled=0 → False)
    assert sw.is_on is False


def test_schedule_switch_handle_coordinator_update_clears_optimistic_when_confirmed():
    """_handle_coordinator_update clears optimistic once robot reports matching state."""
    sw, coord = _make_schedule_switch(enabled=0)
    sw._optimistic_enabled = True
    # Simulate coordinator data now showing enabled=1 (robot confirmed).
    coord.schedule = {"schedule": [{**_SCHED_ENTRY, "enabled": 1}]}
    sw._handle_coordinator_update()
    assert sw._optimistic_enabled is None


def test_schedule_switch_handle_coordinator_update_keeps_optimistic_while_pending():
    """_handle_coordinator_update must NOT clear optimistic while robot still shows old value."""
    sw, coord = _make_schedule_switch(enabled=0)
    sw._optimistic_enabled = True
    # Robot hasn't updated yet — still reports enabled=0.
    coord.schedule = {"schedule": [{**_SCHED_ENTRY, "enabled": 0}]}
    sw._handle_coordinator_update()
    assert sw._optimistic_enabled is True
    # is_on still reads from optimistic.
    assert sw.is_on is True


def test_schedule_switch_missing_entry_returns_safe_defaults():
    """When task_id not in schedule data, is_on is False, name is fallback."""
    coord, _ = _make_schedule_coord()
    coord.schedule = {"schedule": []}  # entry removed
    sw = RobEyeScheduleSwitch.__new__(RobEyeScheduleSwitch)
    object.__setattr__(sw, "coordinator", coord)
    object.__setattr__(sw, "_attr_unique_id", "")
    object.__setattr__(sw, "entity_id", "")
    RobEyeScheduleSwitch.__init__(sw, coord, task_id=2)
    assert sw.is_on is False
    assert sw.name == "Schedule 2"
    assert sw.extra_state_attributes == {}
