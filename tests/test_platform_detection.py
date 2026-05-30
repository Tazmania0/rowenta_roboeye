"""Tests for AICU hardware-platform detection and mopping capability flags."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.rowenta_roboeye.const import (
    FLAG_MISSING_WATER_PUMP,
    FLAG_STUCK_WATER_PUMP,
    FLAG_WATER_TANK_EMPTY,
    FLAG_WATER_TANK_INSERTED,
    PLATFORM_C5,
    PLATFORM_HELIOS,
    PLATFORM_L6,
    PLATFORM_L7,
    PLATFORM_LEGACY,
    PLATFORM_RC100,
    PLATFORM_UNKNOWN,
    PUMP_VOLUME_HIGH,
    PUMP_VOLUME_NONE,
)
from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    coord = RobEyeCoordinator(
        hass=MagicMock(),
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    coord._is_live_map_enabled = lambda: True
    return coord


def _detect(coordinator, unique_id, name):
    coordinator._detect_platform({"unique_id": unique_id, "name": name})
    return coordinator


# ── parseRobotType mapping ────────────────────────────────────────────

def test_detect_legacy(coordinator):
    """Serie 120 — unique_id not prefixed with aicu-."""
    _detect(coordinator, "SER120-abc123", "X-Plorer 120")
    assert coordinator.platform == PLATFORM_LEGACY
    assert not coordinator.has_wet_support
    assert not coordinator.has_short_carpet_support


def test_detect_helios(coordinator):
    _detect(coordinator, "aicu-abc123", "Helios Pro")
    assert coordinator.platform == PLATFORM_HELIOS
    assert coordinator.has_wet_support
    assert coordinator.has_short_carpet_support


def test_detect_l6_agon(coordinator):
    _detect(coordinator, "aicu-xyz", "Agon")
    assert coordinator.platform == PLATFORM_L6
    assert coordinator.has_wet_support
    assert not coordinator.has_short_carpet_support


def test_detect_l6_hy100(coordinator):
    _detect(coordinator, "aicu-xyz", "HY100 Series")
    assert coordinator.platform == PLATFORM_L6
    assert coordinator.has_wet_support


def test_detect_l7_agonoa(coordinator):
    _detect(coordinator, "aicu-xyz", "Agonoa")
    assert coordinator.platform == PLATFORM_L7
    assert coordinator.has_wet_support


def test_detect_rc100(coordinator):
    _detect(coordinator, "aicu-xyz", "RC100")
    assert coordinator.platform == PLATFORM_RC100
    assert not coordinator.has_wet_support


def test_detect_c5_chronos(coordinator):
    _detect(coordinator, "aicu-xyz", "Chronos20")
    assert coordinator.platform == PLATFORM_C5
    assert not coordinator.has_wet_support


def test_detect_unknown_aicu(coordinator):
    """Unknown AICU sub-type — wet support defaults OFF (safe)."""
    _detect(coordinator, "aicu-newmodel", "Serie 375")
    assert coordinator.platform == PLATFORM_UNKNOWN
    assert not coordinator.has_wet_support


# ── Detection wiring through the real update path ─────────────────────

@pytest.mark.asyncio
async def test_detection_runs_during_update(coordinator, mock_client):
    """The mock device (aicu- + name 'Madeleine120') resolves to UNKNOWN/no-wet,
    so a Serie-120-class robot never sprouts mopping entities."""
    coordinator.data = {}
    await coordinator._async_update_data()
    assert coordinator._platform_detected
    assert coordinator.platform == PLATFORM_UNKNOWN
    assert not coordinator.has_wet_support
    # No wet endpoints were polled.
    mock_client.get_pump_volume_settings.assert_not_called()


@pytest.mark.asyncio
async def test_wet_data_fetched_for_wet_model(coordinator, mock_client):
    """A Helios robot triggers pump-volume polling on the background refresh."""
    mock_client.get_robot_id.return_value = {
        "unique_id": "aicu-helios1",
        "name": "Helios",
    }
    mock_client.get_pump_volume_settings.return_value = {"mode": PUMP_VOLUME_HIGH}
    mock_client.get_uxd.return_value = {"do_wet_clean": True}
    mock_client.get_robot_flags.return_value = {
        "notification": [FLAG_WATER_TANK_INSERTED]
    }
    coordinator.data = {}
    await coordinator._async_update_data()
    assert coordinator.has_wet_support
    assert coordinator.pump_volume == PUMP_VOLUME_HIGH
    assert coordinator.wet_clean_active is True
    assert coordinator.water_tank_attached is True


# ── Water-flag parsing ────────────────────────────────────────────────

def test_parse_water_flags_notification_list(coordinator):
    coordinator._parse_water_flags(
        {"notification": [FLAG_WATER_TANK_EMPTY, FLAG_STUCK_WATER_PUMP]}
    )
    assert coordinator.water_tank_empty is True
    assert coordinator.water_pump_fault is True
    assert coordinator.water_tank_attached is False


def test_parse_water_flags_dict_fallback(coordinator):
    coordinator._parse_water_flags(
        {FLAG_WATER_TANK_INSERTED: True, FLAG_MISSING_WATER_PUMP: True}
    )
    assert coordinator.water_tank_attached is True
    assert coordinator.water_pump_fault is True


def test_parse_water_flags_ignores_non_dict(coordinator):
    coordinator.water_tank_attached = True
    coordinator._parse_water_flags(None)
    # Unchanged — no crash on bad payload.
    assert coordinator.water_tank_attached is True


def test_pump_volume_defaults_none(coordinator):
    assert coordinator.pump_volume == PUMP_VOLUME_NONE
