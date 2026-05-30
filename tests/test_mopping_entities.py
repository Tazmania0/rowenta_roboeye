"""Tests for mopping entities and coordinator re-auth handling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.api import AuthFailed
from custom_components.rowenta_roboeye.binary_sensor import (
    RowentaWaterPumpFaultSensor,
    RowentaWaterTankEmptySensor,
    RowentaWaterTankSensor,
)
from custom_components.rowenta_roboeye.const import (
    PUMP_VOLUME_HIGH,
    PUMP_VOLUME_NONE,
    PUMP_VOLUME_OPTIONS,
)
from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator
from custom_components.rowenta_roboeye.select import RobEyePumpVolumeSelect
from custom_components.rowenta_roboeye.switch import RobEyeWetCleanSwitch


def _make_coordinator():
    coord = MagicMock()
    coord.device_id = "dev1"
    coord.pump_volume = PUMP_VOLUME_NONE
    coord.wet_clean_active = False
    coord.water_tank_attached = False
    coord.water_tank_empty = False
    coord.water_pump_fault = False
    coord.client = MagicMock()
    coord.client.set_pump_volume = AsyncMock()
    coord.client.set_wet_clean = AsyncMock()
    coord.async_send_command = AsyncMock()
    return coord


def _attach(entity):
    entity.async_write_ha_state = MagicMock()
    return entity


# ── Pump volume select ────────────────────────────────────────────────

def test_pump_volume_unique_id():
    coord = _make_coordinator()
    entity = RobEyePumpVolumeSelect(coord)
    assert entity._attr_unique_id == "pump_volume_dev1"
    assert entity._attr_options == PUMP_VOLUME_OPTIONS


def test_pump_volume_current_option():
    coord = _make_coordinator()
    coord.pump_volume = PUMP_VOLUME_HIGH
    entity = RobEyePumpVolumeSelect(coord)
    assert entity.current_option == PUMP_VOLUME_HIGH


def test_pump_volume_current_option_invalid_falls_back():
    coord = _make_coordinator()
    coord.pump_volume = "garbage"
    entity = RobEyePumpVolumeSelect(coord)
    assert entity.current_option == PUMP_VOLUME_NONE


@pytest.mark.asyncio
async def test_pump_volume_select_option():
    coord = _make_coordinator()
    entity = _attach(RobEyePumpVolumeSelect(coord))
    await entity.async_select_option(PUMP_VOLUME_HIGH)
    assert coord.pump_volume == PUMP_VOLUME_HIGH
    coord.async_send_command.assert_awaited_once()
    args, kwargs = coord.async_send_command.call_args
    assert args[0] is coord.client.set_pump_volume
    assert kwargs["mode"] == PUMP_VOLUME_HIGH


@pytest.mark.asyncio
async def test_pump_volume_rejects_unknown_option():
    coord = _make_coordinator()
    entity = _attach(RobEyePumpVolumeSelect(coord))
    await entity.async_select_option("turbo")
    coord.async_send_command.assert_not_called()


# ── Wet-clean switch ──────────────────────────────────────────────────

def test_wet_clean_is_on_reads_coordinator():
    coord = _make_coordinator()
    entity = RobEyeWetCleanSwitch(coord)
    assert entity.is_on is False
    coord.wet_clean_active = True
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_wet_clean_turn_on():
    coord = _make_coordinator()
    entity = _attach(RobEyeWetCleanSwitch(coord))
    await entity.async_turn_on()
    assert coord.wet_clean_active is True
    args, _ = coord.async_send_command.call_args
    assert args[0] is coord.client.set_wet_clean
    assert args[1] is True


@pytest.mark.asyncio
async def test_wet_clean_turn_off():
    coord = _make_coordinator()
    coord.wet_clean_active = True
    entity = _attach(RobEyeWetCleanSwitch(coord))
    await entity.async_turn_off()
    assert coord.wet_clean_active is False
    args, _ = coord.async_send_command.call_args
    assert args[1] is False


# ── Water-tank binary sensors ─────────────────────────────────────────

def test_water_tank_sensor_is_on():
    coord = _make_coordinator()
    coord.water_tank_attached = True
    assert RowentaWaterTankSensor(coord).is_on is True


def test_water_tank_empty_sensor_is_on():
    coord = _make_coordinator()
    coord.water_tank_empty = True
    assert RowentaWaterTankEmptySensor(coord).is_on is True


def test_water_pump_fault_sensor_is_on():
    coord = _make_coordinator()
    coord.water_pump_fault = True
    assert RowentaWaterPumpFaultSensor(coord).is_on is True


# ── Coordinator re-auth handling ──────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_failed_raises_config_entry_auth_failed(mock_client, mock_config_entry):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    coord = RobEyeCoordinator(
        hass=MagicMock(),
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    coord._is_live_map_enabled = lambda: True
    mock_client.get_status.side_effect = AuthFailed("locked")
    coord.data = {}
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()
