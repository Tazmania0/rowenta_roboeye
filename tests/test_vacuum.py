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
    coord.async_send_command = AsyncMock()
    coord.client = MagicMock()
    coord.client.clean_all = AsyncMock()
    coord.client.clean_map = AsyncMock()
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
    vac.async_write_ha_state = lambda: None
    return vac, coord


# ── State machine ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,charging,expected_attr", [
    ("cleaning",  "unconnected", "CLEANING"),
    ("ready",     "charging",    "DOCKED"),
    ("ready",     "connected",   "DOCKED"),
    ("ready",     "unconnected", "IDLE"),
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
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    await vac.async_stop()
    coord.async_send_command.assert_called_once_with(coord.client.stop)


# ── Service: return_to_base ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_return_to_base():
    vac, coord = _make_vacuum({"mode": "cleaning", "charging": "unconnected", "battery_level": 80, "cleaning_parameter_set": 2})
    await vac.async_return_to_base()
    coord.async_send_command.assert_called_once_with(coord.client.go_home)


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
