"""Unit tests for the button platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from custom_components.rowenta_roboeye.button import (
    RobEyeCleanAllButton,
    RobEyeCleanSelectedButton,
    RobEyeGoHomeButton,
    RobEyeRoomCleanButton,
    RobEyeStopButton,
    _build_room_button_entities,
)
from custom_components.rowenta_roboeye.const import (
    AREA_STATE_BLOCKING,
    FAN_SPEED_REVERSE_MAP,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
    STRATEGY_REVERSE_MAP,
    room_selection_entity_id,
)

from .conftest import MOCK_AREAS, MOCK_STATUS


# ── Helpers ───────────────────────────────────────────────────────────


def _make_coordinator(status=None, areas=None, device_id="dev123", active_map_id="3"):
    coord = MagicMock()
    coord.device_id = device_id
    coord.active_map_id = active_map_id
    coord.areas_map_id = active_map_id
    coord.areas = list((areas or MOCK_AREAS)["areas"])
    coord.status = dict(status or MOCK_STATUS)
    coord.cleaning_strategy = STRATEGY_DEFAULT
    # ha_fan_speed must be None (not a MagicMock) so the fallback to
    # status["cleaning_parameter_set"] is exercised when no HA preference exists.
    coord.ha_fan_speed = None
    coord.async_send_command = AsyncMock()
    coord.client = MagicMock()
    coord.client.go_home = AsyncMock()
    coord.client.stop = AsyncMock()
    coord.client.clean_all = AsyncMock()
    coord.client.clean_map = AsyncMock()
    coord.hass = MagicMock()
    coord.hass.states = MagicMock()
    coord.hass.states.get = MagicMock(return_value=None)
    coord.hass.services = MagicMock()
    coord.hass.services.async_call = AsyncMock()
    return coord


def _make_config_entry(entry_id="test_entry"):
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _make_button(cls, coord=None):
    """Instantiate a button class bypassing HA framework."""
    if coord is None:
        coord = _make_coordinator()
    btn = cls.__new__(cls)
    object.__setattr__(btn, "coordinator", coord)
    object.__setattr__(btn, "_attr_unique_id", "")
    object.__setattr__(btn, "entity_id", "")
    object.__setattr__(btn, "async_write_ha_state", MagicMock())
    cls.__init__(btn, coord)
    return btn


# ── RobEyeGoHomeButton ────────────────────────────────────────────────


def test_go_home_unique_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeGoHomeButton, coord)
    assert btn._attr_unique_id == "go_home_abc"


def test_go_home_entity_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeGoHomeButton, coord)
    assert btn.entity_id == "button.abc_return_to_base"


@pytest.mark.asyncio
async def test_go_home_press_calls_go_home():
    coord = _make_coordinator()
    btn = _make_button(RobEyeGoHomeButton, coord)
    await btn.async_press()
    coord.async_send_command.assert_called_once_with(coord.client.go_home)


# ── RobEyeStopButton ──────────────────────────────────────────────────


def test_stop_unique_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeStopButton, coord)
    assert btn._attr_unique_id == "stop_abc"


def test_stop_entity_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeStopButton, coord)
    assert btn.entity_id == "button.abc_stop"


@pytest.mark.asyncio
async def test_stop_press_calls_stop():
    coord = _make_coordinator()
    btn = _make_button(RobEyeStopButton, coord)
    await btn.async_press()
    coord.async_send_command.assert_called_once_with(coord.client.stop)


# ── RobEyeCleanAllButton ──────────────────────────────────────────────


def test_clean_all_unique_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeCleanAllButton, coord)
    assert btn._attr_unique_id == "clean_all_abc"


def test_clean_all_entity_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_button(RobEyeCleanAllButton, coord)
    assert btn.entity_id == "button.abc_clean_entire_home"


@pytest.mark.asyncio
async def test_clean_all_press_uses_fan_speed_and_strategy():
    coord = _make_coordinator(status={"cleaning_parameter_set": 3})
    coord.cleaning_strategy = STRATEGY_DEFAULT
    btn = _make_button(RobEyeCleanAllButton, coord)
    await btn.async_press()

    coord.async_send_command.assert_called_once()
    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["cleaning_parameter_set"] == "3"
    assert kwargs["strategy_mode"] == STRATEGY_DEFAULT


@pytest.mark.asyncio
async def test_clean_all_press_uses_deep_strategy():
    coord = _make_coordinator()
    coord.cleaning_strategy = STRATEGY_DEEP
    btn = _make_button(RobEyeCleanAllButton, coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["strategy_mode"] == STRATEGY_DEEP


# ── RobEyeRoomCleanButton ─────────────────────────────────────────────


def _make_room_button(coord=None, area_id="3", room_name="Bedroom", map_id="3"):
    if coord is None:
        coord = _make_coordinator(active_map_id=map_id)
    entry = _make_config_entry()
    btn = RobEyeRoomCleanButton.__new__(RobEyeRoomCleanButton)
    object.__setattr__(btn, "coordinator", coord)
    object.__setattr__(btn, "_attr_unique_id", "")
    object.__setattr__(btn, "entity_id", "")
    object.__setattr__(btn, "async_write_ha_state", MagicMock())
    RobEyeRoomCleanButton.__init__(btn, coord, entry, area_id, room_name)
    return btn


def test_room_button_unique_id():
    btn = _make_room_button(area_id="3", room_name="Bedroom", map_id="3")
    assert "clean_room_map3_3" in btn._attr_unique_id
    assert "dev123" in btn._attr_unique_id or btn._attr_unique_id.endswith(btn.coordinator.device_id)


def test_room_button_name():
    btn = _make_room_button(room_name="Kitchen")
    assert btn._attr_name == "Clean Kitchen"


def test_room_button_available_same_map():
    coord = _make_coordinator(active_map_id="3")
    btn = _make_room_button(coord=coord, map_id="3")
    # CoordinatorEntity stub returns available=True by default
    assert btn.available is True


def test_room_button_stays_available_on_map_switch():
    # Entities no longer become unavailable on map switch — they remain
    # available (showing stale data) until explicitly removed by
    # _async_on_areas_updated.  This prevents the "unavailable" flash in the
    # Lovelace dashboard during the transition window.
    coord = _make_coordinator(active_map_id="3")
    btn = _make_room_button(coord=coord)
    coord.active_map_id = "4"
    assert btn.available is True


@pytest.mark.asyncio
async def test_room_button_press_no_state_uses_global_fan_speed():
    coord = _make_coordinator(status={"cleaning_parameter_set": 2})
    # states.get returns None (no per-room select state)
    coord.hass.states.get.return_value = None
    btn = _make_room_button(coord=coord, area_id="11")
    await btn.async_press()

    coord.async_send_command.assert_called_once()
    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["area_ids"] == "11"
    assert kwargs["cleaning_parameter_set"] == FAN_SPEED_REVERSE_MAP["eco"]  # "2"


@pytest.mark.asyncio
async def test_room_button_press_uses_room_fan_speed_state():
    coord = _make_coordinator(status={"cleaning_parameter_set": 2})

    # Per-room fan speed select is set to "high"
    fan_state = MagicMock()
    fan_state.state = "high"
    switch_state = MagicMock()
    switch_state.state = "off"

    def _get_state(entity_id):
        if "fan_speed" in entity_id:
            return fan_state
        if "deep_clean" in entity_id:
            return switch_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_room_button(coord=coord, area_id="3")
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["cleaning_parameter_set"] == "3"  # high


@pytest.mark.asyncio
async def test_room_button_press_deep_clean_switch_overrides_strategy():
    coord = _make_coordinator()
    coord.cleaning_strategy = STRATEGY_DEFAULT

    deep_state = MagicMock()
    deep_state.state = "on"
    strategy_state = MagicMock()
    strategy_state.state = "Normal"

    def _get_state(entity_id):
        if "deep_clean" in entity_id:
            return deep_state
        if "strategy" in entity_id:
            return strategy_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_room_button(coord=coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["strategy_mode"] == STRATEGY_DEEP


@pytest.mark.asyncio
async def test_room_button_press_uses_room_strategy_when_deep_off():
    coord = _make_coordinator()
    coord.cleaning_strategy = STRATEGY_DEFAULT

    deep_state = MagicMock()
    deep_state.state = "off"
    strategy_state = MagicMock()
    strategy_state.state = "Normal"

    def _get_state(entity_id):
        if "deep_clean" in entity_id:
            return deep_state
        if "strategy" in entity_id:
            return strategy_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_room_button(coord=coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["strategy_mode"] == STRATEGY_REVERSE_MAP["Normal"]


@pytest.mark.asyncio
async def test_room_button_press_falls_back_to_global_strategy():
    coord = _make_coordinator()
    coord.cleaning_strategy = STRATEGY_DEFAULT
    # No state for deep_clean or strategy selects
    coord.hass.states.get.return_value = None
    btn = _make_room_button(coord=coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["strategy_mode"] == STRATEGY_DEFAULT


# ── _build_room_button_entities ───────────────────────────────────────


def test_build_room_button_entities_basic():
    coord = _make_coordinator()
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, coord.areas, set())
    # MOCK_AREAS has 2 named rooms (Bedroom id=3, Kitchen id=11) and 1 no-metadata (id=99)
    assert len(entities) == 2
    names = {e._attr_name for e in entities}
    assert "Clean Bedroom" in names
    assert "Clean Kitchen" in names


def test_build_room_button_entities_skips_no_metadata():
    coord = _make_coordinator()
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, coord.areas, set())
    # area id=99 has empty area_meta_data — must be skipped
    entity_ids = {str(e._area_id) for e in entities}
    assert "99" not in entity_ids


def test_build_room_button_entities_skips_already_known():
    coord = _make_coordinator()
    entry = _make_config_entry()
    already_known = {3}  # area_id already added
    entities, ids = _build_room_button_entities(coord, entry, coord.areas, already_known)
    # Only Kitchen (id=11) should be returned
    assert len(entities) == 1
    assert entities[0]._attr_name == "Clean Kitchen"


def test_build_room_button_entities_skips_blocking_areas():
    areas = [
        {"id": 5, "area_meta_data": '{"name": "Garage"}', "area_state": AREA_STATE_BLOCKING},
        {"id": 6, "area_meta_data": '{"name": "Lounge"}'},
    ]
    coord = _make_coordinator(areas={"areas": areas})
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, areas, set())
    assert len(entities) == 1
    assert entities[0]._attr_name == "Clean Lounge"


def test_build_room_button_entities_stale_map_guard():
    """Returns nothing when areas were fetched for a different map."""
    coord = _make_coordinator(active_map_id="3")
    coord.areas_map_id = "4"  # stale
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, coord.areas, set())
    assert entities == []
    assert ids == []


def test_build_room_button_entities_skips_invalid_json_meta():
    areas = [
        {"id": 7, "area_meta_data": "not valid json"},
        {"id": 8, "area_meta_data": '{"name": "Study"}'},
    ]
    coord = _make_coordinator(areas={"areas": areas})
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, areas, set())
    assert len(entities) == 1
    assert entities[0]._attr_name == "Clean Study"


def test_build_room_button_entities_skips_empty_name():
    areas = [
        {"id": 9, "area_meta_data": '{"name": "  "}'},
        {"id": 10, "area_meta_data": '{"name": "Hall"}'},
    ]
    coord = _make_coordinator(areas={"areas": areas})
    entry = _make_config_entry()
    entities, ids = _build_room_button_entities(coord, entry, areas, set())
    assert len(entities) == 1
    assert entities[0]._attr_name == "Clean Hall"


# ── RobEyeCleanSelectedButton ─────────────────────────────────────────


def _make_selected_button(coord=None):
    if coord is None:
        coord = _make_coordinator()
    btn = RobEyeCleanSelectedButton.__new__(RobEyeCleanSelectedButton)
    object.__setattr__(btn, "coordinator", coord)
    object.__setattr__(btn, "_attr_unique_id", "")
    object.__setattr__(btn, "entity_id", "")
    object.__setattr__(btn, "async_write_ha_state", MagicMock())
    RobEyeCleanSelectedButton.__init__(btn, coord)
    return btn


def test_clean_selected_unique_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_selected_button(coord)
    assert btn._attr_unique_id == "clean_selected_abc"


def test_clean_selected_entity_id():
    coord = _make_coordinator(device_id="abc")
    btn = _make_selected_button(coord)
    assert btn.entity_id == "button.abc_clean_selected_rooms"


def test_clean_selected_unavailable_when_no_rooms_selected():
    """Button is unavailable when no input_booleans are on."""
    coord = _make_coordinator()
    coord.hass.states.get.return_value = None
    btn = _make_selected_button(coord)
    assert btn.available is False


def test_clean_selected_available_when_one_room_selected():
    """Button is available when at least one input_boolean is on."""
    coord = _make_coordinator()
    device_id = coord.device_id
    map_id = coord.active_map_id

    on_state = MagicMock()
    on_state.state = "on"

    def _get_state(eid):
        eid_30 = room_selection_entity_id(device_id, map_id, "3")
        if eid == eid_30:
            return on_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_selected_button(coord)
    assert btn.available is True


@pytest.mark.asyncio
async def test_clean_selected_no_rooms_selected_does_nothing():
    """async_press does nothing when no rooms are selected."""
    coord = _make_coordinator()
    coord.hass.states.get.return_value = None
    btn = _make_selected_button(coord)
    await btn.async_press()
    coord.async_send_command.assert_not_called()


@pytest.mark.asyncio
async def test_clean_selected_sends_combined_area_ids():
    """Selected rooms are sent as comma-separated area_ids in one command."""
    coord = _make_coordinator()
    device_id = coord.device_id
    map_id = coord.active_map_id

    on_state = MagicMock()
    on_state.state = "on"

    def _get_state(eid):
        eid_3 = room_selection_entity_id(device_id, map_id, "3")
        eid_11 = room_selection_entity_id(device_id, map_id, "11")
        if eid in (eid_3, eid_11):
            return on_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_selected_button(coord)
    await btn.async_press()

    coord.async_send_command.assert_called_once()
    kwargs = coord.async_send_command.call_args[1]
    sent_ids = set(kwargs["area_ids"].split(","))
    assert sent_ids == {"3", "11"}


@pytest.mark.asyncio
async def test_clean_selected_uses_most_intensive_fan_speed():
    """Fan speed escalates to the most intensive selected room."""
    coord = _make_coordinator(status={"cleaning_parameter_set": 2})
    device_id = coord.device_id
    map_id = coord.active_map_id
    _m = f"map{map_id}_"

    on_state = MagicMock()
    on_state.state = "on"
    eco_state = MagicMock()
    eco_state.state = "eco"
    high_state = MagicMock()
    high_state.state = "high"
    off_state = MagicMock()
    off_state.state = "off"

    def _get_state(eid):
        eid_3 = room_selection_entity_id(device_id, map_id, "3")
        eid_11 = room_selection_entity_id(device_id, map_id, "11")
        if eid in (eid_3, eid_11):
            return on_state
        if eid == f"select.{device_id}_{_m}room_3_fan_speed":
            return eco_state
        if eid == f"select.{device_id}_{_m}room_11_fan_speed":
            return high_state
        if "deep_clean" in eid:
            return off_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_selected_button(coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["cleaning_parameter_set"] == "3"  # high


@pytest.mark.asyncio
async def test_clean_selected_deep_clean_switch_overrides_strategy():
    """Deep-clean switch on one selected room forces STRATEGY_DEEP."""
    coord = _make_coordinator()
    device_id = coord.device_id
    map_id = coord.active_map_id
    _m = f"map{map_id}_"

    on_state = MagicMock()
    on_state.state = "on"
    off_state = MagicMock()
    off_state.state = "off"

    def _get_state(eid):
        eid_3 = room_selection_entity_id(device_id, map_id, "3")
        if eid == eid_3:
            return on_state
        if eid == f"switch.{device_id}_{_m}room_3_deep_clean":
            return on_state
        if "deep_clean" in eid:
            return off_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_selected_button(coord)
    await btn.async_press()

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["strategy_mode"] == STRATEGY_DEEP


@pytest.mark.asyncio
async def test_clean_selected_resets_booleans_after_press():
    """All selection booleans are turned off after pressing clean."""
    coord = _make_coordinator()
    device_id = coord.device_id
    map_id = coord.active_map_id

    on_state = MagicMock()
    on_state.state = "on"
    off_state = MagicMock()
    off_state.state = "off"

    eid_3 = room_selection_entity_id(device_id, map_id, "3")

    def _get_state(eid):
        if eid == eid_3:
            return on_state
        if "deep_clean" in eid or "fan_speed" in eid or "strategy" in eid:
            return off_state
        return None

    coord.hass.states.get.side_effect = _get_state
    btn = _make_selected_button(coord)
    await btn.async_press()

    # switch.turn_off should have been called for the selected room
    coord.hass.services.async_call.assert_called_once_with(
        "switch", "turn_off", {"entity_id": eid_3}, blocking=False
    )
