"""Unit tests for the RobEye select entities."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.select import (
    RobEyeActiveMapSelect,
    RobEyeCleaningModeSelect,
    RobEyeRoomFanSpeedSelect,
    RobEyeRoomStrategySelect,
    RobEyeStrategySelect,
    _build_room_select_entities,
)
from custom_components.rowenta_roboeye.const import (
    AREA_STATE_BLOCKING,
    FAN_SPEED_MAP,
    FAN_SPEEDS,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
    STRATEGY_LABELS,
    STRATEGY_OPTIONS,
    STRATEGY_REVERSE_MAP,
)

from .conftest import MOCK_MAPS, MOCK_STATUS, MOCK_AREAS


def _make_coordinator(data=None):
    """Return a minimal MagicMock coordinator."""
    coord = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.map_id = "3"
    coord._manual_map_id = None
    coord.data = data if data is not None else {
        "maps": dict(MOCK_MAPS),
        "map_status": {"active_map_id": 3},
    }
    # Wire available_maps and active_map_id through the real implementations
    # by delegating to a real coordinator instance when needed.
    # For simplicity, set them as plain properties on the mock.
    coord.available_maps = [
        {"map_id": "3", "display_name": "Ground Floor"},
        {"map_id": "4", "display_name": "First Floor"},
    ]
    coord.active_map_id = "3"
    # async_set_active_map must be an AsyncMock so it can be awaited
    coord.async_set_active_map = AsyncMock()
    return coord


def _entity(coord=None) -> RobEyeActiveMapSelect:
    """Create a RobEyeActiveMapSelect bypassing __init__."""
    if coord is None:
        coord = _make_coordinator()
    entity = RobEyeActiveMapSelect.__new__(RobEyeActiveMapSelect)
    # Use object.__setattr__ to bypass MagicMock's __setattr__
    # (RestoreEntity is stubbed as MagicMock in conftest.py)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "active_map_test")
    object.__setattr__(entity, "_name_to_id", {})
    return entity


# ── Options / current_option ──────────────────────────────────────────

def test_options_uses_map_names():
    """options returns human-readable map names from available_maps."""
    entity = _entity()
    opts = entity.options
    assert opts == ["Ground Floor", "First Floor"]


def test_options_falls_back_to_active_map_id_when_no_maps():
    """When available_maps is empty, options shows the current map ID."""
    coord = _make_coordinator()
    coord.available_maps = []
    coord.active_map_id = "3"
    entity = _entity(coord)
    opts = entity.options
    assert opts == ["3"]


def test_current_option_matches_active_map():
    """current_option returns the name matching active_map_id."""
    entity = _entity()
    entity._build_options()  # populate _name_to_id
    assert entity.current_option == "Ground Floor"


def test_current_option_second_map():
    """current_option returns 'First Floor' when active map is 4."""
    coord = _make_coordinator()
    coord.active_map_id = "4"
    entity = _entity(coord)
    entity._build_options()
    assert entity.current_option == "First Floor"


def test_current_option_falls_back_to_id_when_name_unknown():
    """If active map ID has no name, return the ID string directly."""
    coord = _make_coordinator()
    coord.active_map_id = "99"
    entity = _entity(coord)
    entity._build_options()
    assert entity.current_option == "99"


# ── async_select_option ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_option_resolves_name_to_id():
    """Selecting a map name resolves to the correct ID and calls coordinator."""
    coord = _make_coordinator()
    coord.async_set_active_map = AsyncMock()
    entity = _entity(coord)
    entity._build_options()

    await entity.async_select_option("First Floor")

    coord.async_set_active_map.assert_called_once_with("4")


@pytest.mark.asyncio
async def test_select_option_unknown_name_passes_raw():
    """If option string isn't a known name, it's passed as-is (raw ID)."""
    coord = _make_coordinator()
    coord.async_set_active_map = AsyncMock()
    entity = _entity(coord)
    entity._build_options()

    await entity.async_select_option("99")

    coord.async_set_active_map.assert_called_once_with("99")


# ── async_added_to_hass (state restore) ───────────────────────────────

@pytest.mark.asyncio
async def test_restore_triggers_map_switch_when_different():
    """Restoring 'First Floor' (map 4) while coordinator uses map 3 triggers
    async_set_active_map so areas and geometry are loaded immediately."""
    coord = _make_coordinator()
    # active_map_id is "3" (setup map), restored map is "4" → should switch
    coord.active_map_id = "3"
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "First Floor"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    # Stub out super() chain
    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    # Must have called async_set_active_map with the resolved map ID "4"
    coord.async_set_active_map.assert_called_once_with("4")


@pytest.mark.asyncio
async def test_restore_ignores_unavailable():
    """'unavailable' state is not restored."""
    coord = _make_coordinator()
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "unavailable"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    assert coord._manual_map_id is None


@pytest.mark.asyncio
async def test_restore_ignores_unknown():
    """'unknown' state is not restored."""
    coord = _make_coordinator()
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    assert coord._manual_map_id is None


@pytest.mark.asyncio
async def test_restore_populates_name_to_id_before_lookup():
    """async_added_to_hass resolves map name to ID even without prior _build_options().

    Regression test for bug where _name_to_id was empty at restore time,
    causing the raw display name (e.g. 'First Floor') to be stored as
    _manual_map_id instead of the numeric ID '4'.
    """
    coord = _make_coordinator()
    coord.active_map_id = "3"
    entity = _entity(coord)
    # Deliberately do NOT call entity._build_options() — simulates HA restart
    assert entity._name_to_id == {}   # confirm _name_to_id is empty

    last_state = MagicMock()
    last_state.state = "First Floor"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    # Must resolve to numeric ID "4" and call async_set_active_map (not set _manual_map_id directly)
    coord.async_set_active_map.assert_called_once_with("4")


# ══════════════════════════════════════════════════════════════════════
# RobEyeCleaningModeSelect
# ══════════════════════════════════════════════════════════════════════


def _make_mode_coordinator(status=None):
    coord = MagicMock()
    coord.device_id = "dev123"
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.status = dict(MOCK_STATUS if status is None else status)
    coord.async_send_command = AsyncMock()
    coord.client = MagicMock()
    coord.client.set_fan_speed = AsyncMock()
    return coord


def _mode_entity(coord=None) -> RobEyeCleaningModeSelect:
    if coord is None:
        coord = _make_mode_coordinator()
    entity = RobEyeCleaningModeSelect.__new__(RobEyeCleaningModeSelect)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "")
    object.__setattr__(entity, "entity_id", "")
    object.__setattr__(entity, "_last_known", None)
    object.__setattr__(entity, "async_write_ha_state", MagicMock())
    RobEyeCleaningModeSelect.__init__(entity, coord)
    return entity


def test_cleaning_mode_unique_id():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)
    assert entity._attr_unique_id == "cleaning_mode_dev123"


def test_cleaning_mode_current_option_maps_raw():
    # MOCK_STATUS has cleaning_parameter_set=2 → "eco"
    entity = _mode_entity()
    assert entity.current_option == FAN_SPEED_MAP["2"]  # "eco"


def test_cleaning_mode_current_option_falls_back_to_last_known():
    coord = _make_mode_coordinator(status={"cleaning_parameter_set": None})
    entity = _mode_entity(coord)
    entity._last_known = "high"
    assert entity.current_option == "high"


def test_cleaning_mode_current_option_none_when_no_data():
    coord = _make_mode_coordinator(status={})
    entity = _mode_entity(coord)
    assert entity.current_option is None


@pytest.mark.asyncio
async def test_cleaning_mode_select_option_sends_command():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)
    await entity.async_select_option("high")
    coord.async_send_command.assert_called_once()
    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["cleaning_parameter_set"] == "3"


@pytest.mark.asyncio
async def test_cleaning_mode_select_option_updates_last_known():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)
    await entity.async_select_option("silent")
    assert entity._last_known == "silent"


@pytest.mark.asyncio
async def test_cleaning_mode_select_unknown_does_not_call():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)
    await entity.async_select_option("turbo")
    coord.async_send_command.assert_not_called()


@pytest.mark.asyncio
async def test_cleaning_mode_restore_valid_state():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)

    last_state = MagicMock()
    last_state.state = "eco"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._last_known == "eco"


@pytest.mark.asyncio
async def test_cleaning_mode_restore_invalid_state_ignored():
    coord = _make_mode_coordinator()
    entity = _mode_entity(coord)

    last_state = MagicMock()
    last_state.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._last_known is None


# ══════════════════════════════════════════════════════════════════════
# RobEyeStrategySelect
# ══════════════════════════════════════════════════════════════════════


def _make_strategy_coordinator(cleaning_strategy=STRATEGY_DEFAULT, last_non_deep=STRATEGY_DEFAULT):
    coord = MagicMock()
    coord.device_id = "dev123"
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.cleaning_strategy = cleaning_strategy
    coord.last_non_deep_strategy = last_non_deep
    return coord


def _strategy_entity(coord=None) -> RobEyeStrategySelect:
    if coord is None:
        coord = _make_strategy_coordinator()
    entity = RobEyeStrategySelect.__new__(RobEyeStrategySelect)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "")
    object.__setattr__(entity, "entity_id", "")
    object.__setattr__(entity, "async_write_ha_state", MagicMock())
    RobEyeStrategySelect.__init__(entity, coord)
    return entity


def test_strategy_unique_id():
    entity = _strategy_entity()
    assert entity._attr_unique_id == "cleaning_strategy_dev123"


def test_strategy_current_option_default():
    coord = _make_strategy_coordinator(STRATEGY_DEFAULT)
    entity = _strategy_entity(coord)
    assert entity.current_option == STRATEGY_LABELS[STRATEGY_DEFAULT]


def test_strategy_current_option_normal():
    coord = _make_strategy_coordinator("1")
    entity = _strategy_entity(coord)
    assert entity.current_option == "Normal"


def test_strategy_current_option_walls_corners():
    coord = _make_strategy_coordinator("2")
    entity = _strategy_entity(coord)
    assert entity.current_option == "Walls & Corners"


def test_strategy_current_option_deep_returns_last_non_deep():
    """When strategy is DEEP (controlled by switch), select shows last non-deep value."""
    coord = _make_strategy_coordinator(STRATEGY_DEEP, last_non_deep="1")  # Normal
    entity = _strategy_entity(coord)
    assert entity.current_option == "Normal"


@pytest.mark.asyncio
async def test_strategy_select_option_sets_coordinator():
    coord = _make_strategy_coordinator()
    entity = _strategy_entity(coord)
    entity.async_write_ha_state = MagicMock()
    await entity.async_select_option("Normal")
    assert coord.cleaning_strategy == "1"
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_strategy_select_option_updates_last_non_deep():
    coord = _make_strategy_coordinator()
    entity = _strategy_entity(coord)
    entity.async_write_ha_state = MagicMock()
    await entity.async_select_option("Walls & Corners")
    # last_non_deep_strategy is now on the coordinator, stored as raw API value "2"
    assert coord.last_non_deep_strategy == "2"


@pytest.mark.asyncio
async def test_strategy_select_unknown_does_not_change():
    coord = _make_strategy_coordinator()
    entity = _strategy_entity(coord)
    original_strategy = coord.cleaning_strategy
    entity.async_write_ha_state = MagicMock()
    await entity.async_select_option("Turbo Max")
    assert coord.cleaning_strategy == original_strategy
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_restore_valid_non_deep():
    coord = _make_strategy_coordinator()
    entity = _strategy_entity(coord)

    last_state = MagicMock()
    last_state.state = "Normal"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    # Both the active strategy and the pre-deep bookmark must be set on coordinator
    assert coord.cleaning_strategy == "1"
    assert coord.last_non_deep_strategy == "1"


@pytest.mark.asyncio
async def test_strategy_restore_deep_state_ignored():
    """Restoring 'Deep' is ignored — deep is controlled by the switch."""
    coord = _make_strategy_coordinator()
    entity = _strategy_entity(coord)

    last_state = MagicMock()
    last_state.state = "Deep"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    original_strategy = coord.cleaning_strategy
    await entity.async_added_to_hass()
    # Deep should not be restored via the strategy select
    assert coord.cleaning_strategy == original_strategy


# ══════════════════════════════════════════════════════════════════════
# RobEyeRoomFanSpeedSelect
# ══════════════════════════════════════════════════════════════════════


def _make_room_coordinator(active_map_id="3"):
    coord = MagicMock()
    coord.device_id = "dev123"
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.active_map_id = active_map_id
    return coord


def _room_fan_entity(coord=None, area_id="3", room_name="Bedroom", map_id="3"):
    if coord is None:
        coord = _make_room_coordinator(active_map_id=map_id)
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entity = RobEyeRoomFanSpeedSelect.__new__(RobEyeRoomFanSpeedSelect)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "")
    object.__setattr__(entity, "entity_id", "")
    object.__setattr__(entity, "async_write_ha_state", MagicMock())
    RobEyeRoomFanSpeedSelect.__init__(entity, coord, entry, area_id, room_name)
    return entity


def test_room_fan_speed_unique_id():
    entity = _room_fan_entity(area_id="5", map_id="3")
    assert "room_fan_speed_map3_5" in entity._attr_unique_id


def test_room_fan_speed_name():
    entity = _room_fan_entity(room_name="Kitchen")
    assert entity._attr_name == "Kitchen Fan Speed"


def test_room_fan_speed_entity_id():
    coord = _make_room_coordinator(active_map_id="3")
    coord.device_id = "mydev"
    entity = _room_fan_entity(coord=coord, area_id="7", map_id="3")
    assert entity.entity_id == "select.mydev_map3_room_7_fan_speed"


def test_room_fan_speed_default_is_normal():
    entity = _room_fan_entity()
    assert entity.current_option == "normal"


def test_room_fan_speed_available_same_map():
    coord = _make_room_coordinator(active_map_id="3")
    entity = _room_fan_entity(coord=coord, map_id="3")
    assert entity.available is True


def test_room_fan_speed_unavailable_different_map():
    # Create entity while map is "3" (sets _map_id = "3"),
    # then switch coordinator to map "4" → unavailable.
    coord = _make_room_coordinator(active_map_id="3")
    entity = _room_fan_entity(coord=coord)
    coord.active_map_id = "4"
    assert entity.available is False


@pytest.mark.asyncio
async def test_room_fan_speed_select_valid_option():
    entity = _room_fan_entity()
    await entity.async_select_option("high")
    assert entity.current_option == "high"
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_room_fan_speed_select_invalid_option_ignored():
    entity = _room_fan_entity()
    entity.async_write_ha_state = MagicMock()
    await entity.async_select_option("turbo")
    assert entity.current_option == "normal"
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_room_fan_speed_restore_valid():
    entity = _room_fan_entity()
    last_state = MagicMock()
    last_state.state = "eco"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._selected == "eco"


@pytest.mark.asyncio
async def test_room_fan_speed_restore_invalid_ignored():
    entity = _room_fan_entity()
    last_state = MagicMock()
    last_state.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._selected == "normal"


# ══════════════════════════════════════════════════════════════════════
# RobEyeRoomStrategySelect
# ══════════════════════════════════════════════════════════════════════


def _room_strategy_entity(coord=None, area_id="3", room_name="Bedroom", map_id="3"):
    if coord is None:
        coord = _make_room_coordinator(active_map_id=map_id)
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entity = RobEyeRoomStrategySelect.__new__(RobEyeRoomStrategySelect)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "")
    object.__setattr__(entity, "entity_id", "")
    object.__setattr__(entity, "async_write_ha_state", MagicMock())
    RobEyeRoomStrategySelect.__init__(entity, coord, entry, area_id, room_name)
    return entity


def test_room_strategy_unique_id():
    entity = _room_strategy_entity(area_id="5", map_id="3")
    assert "room_strategy_map3_5" in entity._attr_unique_id


def test_room_strategy_name():
    entity = _room_strategy_entity(room_name="Office")
    assert entity._attr_name == "Office Strategy"


def test_room_strategy_entity_id():
    coord = _make_room_coordinator(active_map_id="3")
    coord.device_id = "mydev"
    entity = _room_strategy_entity(coord=coord, area_id="8", map_id="3")
    assert entity.entity_id == "select.mydev_map3_room_8_strategy"


def test_room_strategy_default_is_default_label():
    entity = _room_strategy_entity()
    assert entity.current_option == STRATEGY_LABELS[STRATEGY_DEFAULT]


def test_room_strategy_available_same_map():
    coord = _make_room_coordinator(active_map_id="3")
    entity = _room_strategy_entity(coord=coord, map_id="3")
    assert entity.available is True


def test_room_strategy_unavailable_different_map():
    # Create entity while map is "3" (sets _map_id = "3"),
    # then switch coordinator to map "4" → unavailable.
    coord = _make_room_coordinator(active_map_id="3")
    entity = _room_strategy_entity(coord=coord)
    coord.active_map_id = "4"
    assert entity.available is False


@pytest.mark.asyncio
async def test_room_strategy_select_valid_option():
    entity = _room_strategy_entity()
    await entity.async_select_option("Normal")
    assert entity.current_option == "Normal"
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_room_strategy_select_invalid_option_ignored():
    entity = _room_strategy_entity()
    entity.async_write_ha_state = MagicMock()
    await entity.async_select_option("Turbo")
    assert entity.current_option == STRATEGY_LABELS[STRATEGY_DEFAULT]
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_room_strategy_restore_valid():
    entity = _room_strategy_entity()
    last_state = MagicMock()
    last_state.state = "Walls & Corners"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._selected == "Walls & Corners"


@pytest.mark.asyncio
async def test_room_strategy_restore_invalid_ignored():
    entity = _room_strategy_entity()
    last_state = MagicMock()
    last_state.state = "Turbo"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()
    assert entity._selected == STRATEGY_LABELS[STRATEGY_DEFAULT]


# ══════════════════════════════════════════════════════════════════════
# _build_room_select_entities
# ══════════════════════════════════════════════════════════════════════


def _make_select_coordinator(active_map_id="3"):
    coord = MagicMock()
    coord.device_id = "dev123"
    coord.active_map_id = active_map_id
    coord.areas_map_id = active_map_id
    coord.areas = list(MOCK_AREAS["areas"])
    return coord


def test_build_room_selects_basic():
    coord = _make_select_coordinator()
    entry = MagicMock()
    entry.entry_id = "test"
    entities, ids = _build_room_select_entities(coord, entry, coord.areas, set())
    # 2 named rooms → 2 fan speed + 2 strategy = 4 entities
    assert len(entities) == 4


def test_build_room_selects_skips_no_metadata():
    coord = _make_select_coordinator()
    entry = MagicMock()
    entry.entry_id = "test"
    entities, ids = _build_room_select_entities(coord, entry, coord.areas, set())
    # area id=99 (no metadata) must be absent
    area_ids = {getattr(e, "_area_id", None) for e in entities}
    assert "99" not in area_ids


def test_build_room_selects_skips_already_known():
    coord = _make_select_coordinator()
    entry = MagicMock()
    entry.entry_id = "test"
    already_known = {("3", 3)}  # Bedroom already registered
    entities, ids = _build_room_select_entities(coord, entry, coord.areas, already_known)
    # Only Kitchen → 1 fan speed + 1 strategy = 2
    assert len(entities) == 2


def test_build_room_selects_skips_blocking():
    areas = [
        {"id": 5, "area_meta_data": '{"name": "Garage"}', "area_state": AREA_STATE_BLOCKING},
        {"id": 6, "area_meta_data": '{"name": "Lounge"}'},
    ]
    coord = _make_select_coordinator()
    coord.areas = areas
    entry = MagicMock()
    entry.entry_id = "test"
    entities, ids = _build_room_select_entities(coord, entry, areas, set())
    assert len(entities) == 2  # only Lounge gets fan speed + strategy


def test_build_room_selects_stale_map_guard():
    coord = _make_select_coordinator(active_map_id="3")
    coord.areas_map_id = "4"  # stale
    entry = MagicMock()
    entry.entry_id = "test"
    entities, ids = _build_room_select_entities(coord, entry, coord.areas, set())
    assert entities == []
