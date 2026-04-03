"""Unit tests for the switch platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.switch import (
    RobEyeDeepCleanSwitch,
    RobEyeRoomDeepCleanSwitch,
)
from custom_components.rowenta_roboeye.const import (
    AREA_STATE_BLOCKING,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_coordinator(device_id="dev123", cleaning_strategy=STRATEGY_DEFAULT, active_map_id="3"):
    coord = MagicMock()
    coord.device_id = device_id
    coord.cleaning_strategy = cleaning_strategy
    coord.active_map_id = active_map_id
    coord.areas_map_id = active_map_id
    coord.areas = []
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
async def test_deep_clean_turn_off_sets_strategy_default():
    coord = _make_coordinator(cleaning_strategy=STRATEGY_DEEP)
    sw = _make_deep_clean_switch(coord)
    await sw.async_turn_off()
    assert coord.cleaning_strategy == STRATEGY_DEFAULT
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
    sw = _make_room_switch()
    await sw.async_turn_on()
    assert sw.is_on is True
    sw.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_room_switch_turn_off():
    sw = _make_room_switch()
    sw._is_on = True
    await sw.async_turn_off()
    assert sw.is_on is False
    sw.async_write_ha_state.assert_called_once()


def test_room_switch_available_same_map():
    coord = _make_coordinator(active_map_id="3")
    sw = _make_room_switch(coord=coord, map_id="3")
    # _map_id matches active_map_id → available (CoordinatorEntity stub returns True)
    assert sw.available is True


def test_room_switch_unavailable_different_map():
    # Create switch while map is "3" (sets _map_id = "3"),
    # then switch coordinator to map "4" → unavailable.
    coord = _make_coordinator(active_map_id="3")
    sw = _make_room_switch(coord=coord)
    coord.active_map_id = "4"
    assert sw.available is False


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
