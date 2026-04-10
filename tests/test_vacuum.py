"""Unit tests for the vacuum entity — state machine, service dispatch, clean_room."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.vacuum import RobEyeVacuumEntity


def _make_vacuum(status: dict):
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = {"host": "192.168.1.1", "map_id": "3"}

    coord = MagicMock()
    coord.status = status
    coord.config_entry = entry
    coord.active_map_id = "3"
    coord.ha_fan_speed = None  # explicit None so fallback to status value works
    coord.is_paused = False    # pause state owned by coordinator, not entity
    coord.paused_fan_speed = None
    coord.async_send_command = AsyncMock()
    coord.client = MagicMock()
    coord.client.clean_all = AsyncMock()
    coord.client.clean_map = AsyncMock()
    coord.client.clean_start_or_continue = AsyncMock()
    coord.client.stop = AsyncMock()
    coord.client.go_home = AsyncMock()
    coord.client.set_fan_speed = AsyncMock()

    vac = RobEyeVacuumEntity.__new__(RobEyeVacuumEntity)
    vac.coordinator = coord
    vac._attr_unique_id = "test_vac"
    vac._attr_device_info = {}
    vac._attr_fan_speed = None
    vac._attr_battery_level = None
    vac._attr_activity = None
    vac._error_status = None
    vac.async_write_ha_state = lambda: None
    return vac, coord


# ── State machine ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,charging,expected_attr", [
    ("cleaning",  "unconnected", "CLEANING"),
    ("ready",     "charging",    "DOCKED"),
    ("ready",     "connected",   "DOCKED"),
    ("ready",     "unconnected", "PAUSED"),
    ("go_home",   "unconnected", "RETURNING"),
    ("unknown",   "unconnected", "IDLE"),
])
def test_state_machine(mode, charging, expected_attr):
    # The implementation uses VacuumActivity.CLEANING etc. (attribute access),
    # NOT VacuumActivity("cleaning") (calling the mock). Compare via the same
    # attribute so both sides reference the same MagicMock child object.
    from homeassistant.components.vacuum import VacuumActivity
    vac, _ = _make_vacuum({"mode": mode, "charging": charging, "battery_level": 80, "cleaning_parameter_set": 2})
    vac._handle_coordinator_update()
    assert vac._attr_activity is getattr(VacuumActivity, expected_attr)


# ── Fan speed mapping ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    # API mapping: "1"=normal, "2"=eco, "3"=high, "4"=silent (fixed in e4a2c74)
    (1, "normal"), (2, "eco"), (3, "high"), (4, "silent"),
])
def test_fan_speed_mapped(raw, expected):
    vac, _ = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": raw})
    vac._handle_coordinator_update()
    assert vac._attr_fan_speed == expected


# ── Service: start ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_start_uses_current_fan_speed():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_fan_speed = "normal"
    await vac.async_start()
    coord.async_send_command.assert_called_once()
    # "normal" → FAN_SPEED_REVERSE_MAP["normal"] = "1"
    assert coord.async_send_command.call_args[1]["cleaning_parameter_set"] == "1"


@pytest.mark.asyncio
async def test_async_start_defaults_to_normal_if_no_fan_speed():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_fan_speed = None
    await vac.async_start()
    # Default "normal" → "1"
    assert coord.async_send_command.call_args[1]["cleaning_parameter_set"] == "1"


# ── Service: stop ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_stop():
    """async_stop sends stop then go_home (full stop — discards paused jobs)."""
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    await vac.async_stop()
    assert coord.async_send_command.call_count == 2
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


# ── Service: return_to_base ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_return_to_base():
    """From CLEANING state: stop first, then go_home (two enqueued commands)."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.CLEANING
    await vac.async_return_to_base()
    assert coord.async_send_command.call_count == 2
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


@pytest.mark.asyncio
async def test_return_to_base_from_cleaning_stops_first():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.CLEANING
    await vac.async_return_to_base()
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


@pytest.mark.asyncio
async def test_return_to_base_from_paused_stops_first():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.PAUSED
    await vac.async_return_to_base()
    assert coord.async_send_command.call_count == 2
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


@pytest.mark.asyncio
async def test_return_to_base_from_error_stops_first():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "not_ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.ERROR
    await vac.async_return_to_base()
    assert coord.async_send_command.call_count == 2
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


@pytest.mark.asyncio
async def test_return_to_base_from_docked_skips_stop():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.DOCKED
    await vac.async_return_to_base()
    assert coord.async_send_command.call_count == 1
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.go_home


# ── Service: set_fan_speed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_set_fan_speed_high():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    await vac.async_set_fan_speed("high")
    assert coord.async_send_command.call_args[1]["cleaning_parameter_set"] == "3"


@pytest.mark.asyncio
async def test_async_set_fan_speed_unknown_does_nothing():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    await vac.async_set_fan_speed("turbo_ultra_boost")
    coord.async_send_command.assert_not_called()


# ── Service: clean_room ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_room_single_room():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "docked", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_fan_speed = "normal"
    await vac._async_clean_room(room_ids=["3"])

    coord.async_send_command.assert_called_once()
    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["map_id"] == "3"
    assert kwargs["area_ids"] == "3"
    assert kwargs["cleaning_parameter_set"] == "1"  # "normal" → "1"


@pytest.mark.asyncio
async def test_clean_room_multi_room():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "docked", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_fan_speed = "eco"
    await vac._async_clean_room(room_ids=["2", "11"])

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["area_ids"] == "2,11"
    assert kwargs["cleaning_parameter_set"] == "2"  # "eco" → "2"


@pytest.mark.asyncio
async def test_clean_room_fan_speed_override():
    vac, coord = _make_vacuum({"mode": "ready", "charging": "docked", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_fan_speed = "eco"
    await vac._async_clean_room(room_ids=["3"], fan_speed="high")

    kwargs = coord.async_send_command.call_args[1]
    assert kwargs["cleaning_parameter_set"] == "3"  # high overrides eco


# ── Service: pause ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_pause_calls_stop():
    """async_pause enqueues stop; coordinator sets _is_paused via async_send_command."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.CLEANING
    await vac.async_pause()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.stop


def test_state_machine_paused():
    from homeassistant.components.vacuum import VacuumActivity
    vac, _ = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac.coordinator.sensor_values_parsed = {}
    vac._handle_coordinator_update()
    assert vac._attr_activity is VacuumActivity.PAUSED


def test_state_machine_clears_paused_on_cleaning():
    from homeassistant.components.vacuum import VacuumActivity
    vac, _ = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac.coordinator.sensor_values_parsed = {}
    vac._handle_coordinator_update()
    assert vac._attr_activity is VacuumActivity.CLEANING


# ── Service: start — context-aware pause/resume ───────────────────────

@pytest.mark.asyncio
async def test_start_from_paused_calls_resume():
    """PAUSED → async_start enqueues clean_start_or_continue with fan speed."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 75, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.PAUSED
    await vac.async_start()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.clean_start_or_continue


@pytest.mark.asyncio
async def test_start_from_docked_calls_clean_all():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.DOCKED
    await vac.async_start()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.clean_all


@pytest.mark.asyncio
async def test_start_from_idle_calls_clean_all():
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "unknown", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.IDLE
    await vac.async_start()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.clean_all


@pytest.mark.asyncio
async def test_start_from_error_attempts_resume():
    """ERROR → clean_start_or_continue. Firmware decides if recoverable."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "not_ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.ERROR
    await vac.async_start()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.clean_start_or_continue
    coord.client.clean_all.assert_not_called()


@pytest.mark.asyncio
async def test_pause_calls_stop_not_go_home():
    """async_pause only enqueues stop (not go_home) — robot stays in place."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    vac._attr_activity = VacuumActivity.CLEANING
    await vac.async_pause()
    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.stop
    # go_home must NOT be called — robot should stay where it is
    assert not any(
        call[0][0] is coord.client.go_home
        for call in coord.async_send_command.call_args_list
    )


# ── Pause / Resume — coordinator-owned state ──────────────────────────

@pytest.mark.asyncio
async def test_start_from_paused_uses_saved_fan_speed():
    """Resume passes the coordinator's paused_fan_speed to clean_start_or_continue."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 75, "cleaning_parameter_set": 2})
    coord.is_paused = True
    coord.paused_fan_speed = "3"  # High — saved on pause
    vac._attr_activity = VacuumActivity.PAUSED

    await vac.async_start()

    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[0][0] is coord.client.clean_start_or_continue
    # Confirm fan speed is forwarded
    assert coord.async_send_command.call_args[1].get("cleaning_parameter_set") == "3"


@pytest.mark.asyncio
async def test_start_from_paused_falls_back_to_ha_fan_speed():
    """If paused_fan_speed is None, resume falls back to ha_fan_speed."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 75, "cleaning_parameter_set": 2})
    coord.is_paused = True
    coord.paused_fan_speed = None
    coord.ha_fan_speed = "2"  # Eco
    vac._attr_activity = VacuumActivity.PAUSED

    await vac.async_start()

    coord.async_send_command.assert_called_once()
    assert coord.async_send_command.call_args[1].get("cleaning_parameter_set") == "2"


@pytest.mark.asyncio
async def test_return_to_base_discards_paused_jobs():
    """Return to base sends stop+go_home; go_home path discards _paused_jobs."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    coord.is_paused = True
    vac._attr_activity = VacuumActivity.PAUSED

    await vac.async_return_to_base()

    assert coord.async_send_command.call_count == 2
    assert coord.async_send_command.call_args_list[0][0][0] is coord.client.stop
    assert coord.async_send_command.call_args_list[1][0][0] is coord.client.go_home


def test_coordinator_is_paused_shows_paused_state():
    """When coordinator.is_paused=True and mode=ready, entity shows PAUSED."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "ready", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    coord.is_paused = True
    coord.sensor_values_parsed = {}
    vac._handle_coordinator_update()
    assert vac._attr_activity is VacuumActivity.PAUSED


def test_coordinator_is_paused_false_and_idle_mode_shows_idle():
    """When coordinator.is_paused=False and mode is unknown, entity shows IDLE."""
    from homeassistant.components.vacuum import VacuumActivity
    vac, coord = _make_vacuum({"mode": "unknown", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    coord.is_paused = False
    coord.sensor_values_parsed = {}
    vac._handle_coordinator_update()
    assert vac._attr_activity is VacuumActivity.IDLE


def test_pause_feature_declared():
    from homeassistant.components.vacuum import VacuumEntityFeature
    vac, _ = _make_vacuum({"mode": "ready", "charging": "charging", "battery_level": 100, "cleaning_parameter_set": 2})
    assert vac._attr_supported_features & VacuumEntityFeature.PAUSE
