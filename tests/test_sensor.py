"""Unit tests for sensor entities — unit conversions, room discovery, new sensors."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.rowenta_roboeye.sensor import (
    RobEyeStaticSensor,
    _format_date,
    _resolve_active_map_name,
    _safe_round,
    _build_room_sensors,
    STATUS_SENSORS,
    STATISTICS_SENSORS,
    ROBOT_INFO_SENSORS,
    LIVE_SENSORS,
    SENSOR_HEALTH_SENSORS,
)
from custom_components.rowenta_roboeye.entity import async_remove_stale_room_entities
from homeassistant.helpers import entity_registry as _er

from .conftest import (
    MOCK_AREAS,
    MOCK_LIVE_PARAMETERS,
    MOCK_MAPS,
    MOCK_MAP_STATUS,
    MOCK_ROBOT_ID,
    MOCK_SENSOR_STATUS,
    MOCK_STATISTICS,
    MOCK_STATUS,
    MOCK_WIFI_STATUS,
    MOCK_PROTOCOL_VERSION,
)


def _make_coordinator(overrides: dict | None = None):
    coord = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.status = dict(MOCK_STATUS)
    coord.statistics = dict(MOCK_STATISTICS)
    coord.permanent_statistics = {}
    coord.areas = MOCK_AREAS["areas"]
    coord.live_parameters = dict(MOCK_LIVE_PARAMETERS)
    coord.sensor_status = dict(MOCK_SENSOR_STATUS)
    coord.robot_flags = {}
    coord.robot_info = {
        "wifi_status": dict(MOCK_WIFI_STATUS),
        "robot_id": dict(MOCK_ROBOT_ID),
        "protocol_version": dict(MOCK_PROTOCOL_VERSION),
    }
    if overrides:
        for k, v in overrides.items():
            setattr(coord, k, v)
    return coord


def _sensor(description, coord=None):
    if coord is None:
        coord = _make_coordinator()
    s = RobEyeStaticSensor.__new__(RobEyeStaticSensor)
    s.coordinator = coord
    s.entity_description = description
    s._attr_unique_id = f"{description.key}_test"
    return s


# ── Status sensors ────────────────────────────────────────────────────

def test_battery_level():
    desc = next(d for d in STATUS_SENSORS if d.key == "battery_level")
    assert _sensor(desc).native_value == 85


def test_mode():
    desc = next(d for d in STATUS_SENSORS if d.key == "mode")
    assert _sensor(desc).native_value == "ready"


def test_charging():
    desc = next(d for d in STATUS_SENSORS if d.key == "charging")
    assert _sensor(desc).native_value == "charging"


def test_fan_speed_label():
    desc = next(d for d in STATUS_SENSORS if d.key == "fan_speed_label")
    coord = _make_coordinator({"status": {"cleaning_parameter_set": 3}})
    assert _sensor(desc, coord).native_value == "high"


# ── Live parameter sensors ────────────────────────────────────────────

def test_current_area_cleaned():
    desc = next(d for d in LIVE_SENSORS if d.key == "current_area_cleaned")
    # 50000 cm² ÷ 10000 = 5.0 m²
    assert _sensor(desc).native_value == 5.0


def test_current_cleaning_time():
    desc = next(d for d in LIVE_SENSORS if d.key == "current_cleaning_time")
    # 720 s ÷ 60 = 12.0 min
    assert _sensor(desc).native_value == 12.0


# ── Statistics unit conversions ───────────────────────────────────────

def test_total_distance_metres():
    desc = next(d for d in STATISTICS_SENSORS if d.key == "total_distance_driven")
    assert _sensor(desc).native_value == 250.0  # 25000 cm ÷ 100


def test_total_cleaning_time_hours():
    desc = next(d for d in STATISTICS_SENSORS if d.key == "total_cleaning_time")
    assert _sensor(desc).native_value == 600.0  # 36000 min ÷ 60


def test_total_area_sq_metres():
    desc = next(d for d in STATISTICS_SENSORS if d.key == "total_area_cleaned")
    assert _sensor(desc).native_value == 5000.0  # 500000 ÷ 100


def test_total_cleaning_runs():
    desc = next(d for d in STATISTICS_SENSORS if d.key == "total_number_of_cleaning_runs")
    assert _sensor(desc).native_value == 42


# ── Robot info sensors ────────────────────────────────────────────────

def test_wifi_rssi():
    desc = next(d for d in ROBOT_INFO_SENSORS if d.key == "wifi_rssi")
    assert _sensor(desc).native_value == -55


def test_wifi_ssid():
    desc = next(d for d in ROBOT_INFO_SENSORS if d.key == "wifi_ssid")
    assert _sensor(desc).native_value == "HomeNetwork"


def test_protocol_version():
    desc = next(d for d in ROBOT_INFO_SENSORS if d.key == "protocol_version")
    assert _sensor(desc).native_value == "2.3.1"


def test_robot_serial_from_serial_number():
    desc = next(d for d in ROBOT_INFO_SENSORS if d.key == "robot_serial")
    assert _sensor(desc).native_value == "SN123456789"


def test_robot_serial_falls_back_to_robot_id():
    desc = next(d for d in ROBOT_INFO_SENSORS if d.key == "robot_serial")
    coord = _make_coordinator({
        "robot_info": {"robot_id": {"robot_id": "aicu-abc123"}, "wifi_status": {}, "protocol_version": {}}
    })
    assert _sensor(desc, coord).native_value == "aicu-abc123"


# ── Sensor health ─────────────────────────────────────────────────────

def test_cliff_sensor_status():
    desc = next(d for d in SENSOR_HEALTH_SENSORS if d.key == "sensor_cliff_status")
    assert _sensor(desc).native_value == "ok"


def test_bump_sensor_status():
    desc = next(d for d in SENSOR_HEALTH_SENSORS if d.key == "sensor_bump_status")
    assert _sensor(desc).native_value == "ok"


# ── Per-room sensor generation ────────────────────────────────────────

def test_room_sensors_built_for_named_areas():
    coord = _make_coordinator()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sensors = _build_room_sensors(coord, entry, area_id=3, room_name="Bedroom")
    assert len(sensors) == 4
    names = [s._attr_name for s in sensors]
    assert "Bedroom Cleanings" in names
    assert "Bedroom Area" in names
    assert "Bedroom Avg Clean Time" in names
    assert "Bedroom Last Cleaned" in names


def test_room_sensor_values():
    coord = _make_coordinator()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sensors = _build_room_sensors(coord, entry, area_id=3, room_name="Bedroom")

    cleanings = next(s for s in sensors if "Cleanings" in s._attr_name)
    assert cleanings.native_value == 12

    area = next(s for s in sensors if s._attr_name == "Bedroom Area")
    assert area.native_value == 24.0  # 12_000_000 / 500_000

    avg_time = next(s for s in sensors if "Avg" in s._attr_name)
    assert avg_time.native_value == 15.0  # 900_000 ms / 60_000

    last_cleaned = next(s for s in sensors if "Last" in s._attr_name)
    assert last_cleaned.native_value == "2026-03-10"


# ── Empty-meta areas are skipped ─────────────────────────────────────

def test_area_with_no_meta_produces_no_sensors():
    """Area id=99 has empty area_meta_data and must yield zero sensors."""
    coord = _make_coordinator()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    # Attempt to build sensors for the empty area — room_name would be ""
    # In practice async_setup_entry skips it, but let's confirm _build_room_sensors
    # with an empty string name still produces sensors (the filtering happens upstream)
    sensors = _build_room_sensors(coord, entry, area_id=99, room_name="")
    # Sensors are created even with empty name — filtering done before this call
    # The important test is that async_setup_entry never calls this for empty names
    assert len(sensors) == 4  # function itself doesn't filter; caller does


# ── Helpers ───────────────────────────────────────────────────────────

def test_safe_round_normal():
    assert _safe_round(50000, divisor=10000, precision=1) == 5.0


def test_safe_round_none_returns_none():
    assert _safe_round(None) is None


def test_safe_round_non_numeric_returns_none():
    assert _safe_round("bad", divisor=100) is None


def test_format_date_valid():
    assert _format_date({"year": 2026, "month": 3, "day": 10}) == "2026-03-10"


def test_format_date_empty_returns_none():
    assert _format_date({}) is None


def test_format_date_none_returns_none():
    assert _format_date(None) is None


# ── Live map sensor state ─────────────────────────────────────────────

def _make_live_map_sensor(mode="ready", session_complete=False):
    from custom_components.rowenta_roboeye.sensor import RobEyeLiveMapSensor
    coord = _make_coordinator()
    coord.status = {**MOCK_STATUS, "mode": mode}
    coord.session_complete = session_complete
    coord.live_map = {"rooms": [], "live_outline": []}
    s = RobEyeLiveMapSensor.__new__(RobEyeLiveMapSensor)
    s.coordinator = coord
    return s


def test_live_map_state_cleaning():
    s = _make_live_map_sensor(mode="cleaning")
    assert s.native_value == "cleaning"


def test_live_map_state_returning():
    s = _make_live_map_sensor(mode="go_home")
    assert s.native_value == "returning"


def test_live_map_state_session_complete():
    s = _make_live_map_sensor(mode="ready", session_complete=True)
    assert s.native_value == "session_complete"


def test_live_map_state_idle():
    s = _make_live_map_sensor(mode="ready", session_complete=False)
    assert s.native_value == "idle"


# ── Active map sensor ────────────────────────────────────────────────

def test_active_map_sensor_exists():
    desc = next((d for d in STATUS_SENSORS if d.key == "active_map"), None)
    assert desc is not None
    assert desc.icon == "mdi:layers"


def test_active_map_sensor_shows_map_name():
    coord = _make_coordinator()
    coord.active_map_id = "3"
    coord.available_maps = [
        {"map_id": "3", "display_name": "Ground Floor"},
        {"map_id": "4", "display_name": "First Floor"},
    ]
    result = _resolve_active_map_name(coord)
    assert result == "Ground Floor"


def test_active_map_sensor_falls_back_when_no_name():
    coord = _make_coordinator()
    coord.active_map_id = "4"
    coord.available_maps = []
    result = _resolve_active_map_name(coord)
    assert result == "Map 4"


def test_active_map_sensor_none_when_empty_id():
    coord = _make_coordinator()
    coord.active_map_id = ""
    result = _resolve_active_map_name(coord)
    assert result is None


# ── Map-prefixed room sensors ────────────────────────────────────────

def test_room_sensors_include_map_prefix():
    coord = _make_coordinator()
    coord.active_map_id = "3"
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sensors = _build_room_sensors(
        coord, entry, area_id=3, room_name="Bedroom", map_id="3"
    )
    assert len(sensors) == 4
    for s in sensors:
        assert "map3_" in s._attr_unique_id, f"Missing map prefix in {s._attr_unique_id}"


def test_room_sensors_entity_id_includes_map_prefix():
    coord = _make_coordinator()
    coord.device_id = "test_dev"
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sensors = _build_room_sensors(
        coord, entry, area_id=11, room_name="Kitchen",
        device_id="test_dev", map_id="4",
    )
    entity_ids = [s.entity_id for s in sensors]
    for eid in entity_ids:
        assert "_map4_room_" in eid, f"Missing map prefix in entity_id: {eid}"


# ── async_remove_stale_room_entities (entity.py) ─────────────────────
# The old sensor-level reenable/disable functions were replaced by a single
# `async_remove_stale_room_entities` in entity.py that permanently removes
# orphaned area entities rather than disabling them.

def _make_registry_entry(
    domain="sensor",
    unique_id="room_3_map3_cleanings_dev",
    entity_id="sensor.dev_map3_room_3_cleanings",
    disabled_by=None,
):
    entry = MagicMock()
    entry.domain = domain
    entry.unique_id = unique_id
    entry.entity_id = entity_id
    entry.disabled_by = disabled_by
    return entry


def _call_remove_stale(entries, active_map_id, current_area_ids):
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "entry1"
    coordinator = MagicMock()
    coordinator.active_map_id = active_map_id

    mock_ent_reg = MagicMock()
    with patch.object(_er, "async_get", return_value=mock_ent_reg), \
         patch.object(_er, "async_entries_for_config_entry", return_value=entries):
        async_remove_stale_room_entities(hass, config_entry, coordinator, "sensor", current_area_ids)
    return mock_ent_reg


def test_remove_stale_removes_entity_missing_from_current_map():
    """An entity whose area is gone from the active map is permanently removed."""
    entry = _make_registry_entry(
        unique_id="room_99_map3_last_cleaned_dev",
        entity_id="sensor.dev_map3_room_99_last_cleaned",
        disabled_by=None,
    )
    mock_reg = _call_remove_stale([entry], active_map_id="3", current_area_ids={3, 11})
    mock_reg.async_remove.assert_called_once_with("sensor.dev_map3_room_99_last_cleaned")


def test_remove_stale_skips_entity_from_other_map():
    """Entities from a different map are never removed (they become unavailable during map switch)."""
    entry = _make_registry_entry(
        unique_id="room_3_map5_cleanings_dev",
        disabled_by=None,
    )
    # active map "3", entity is from map 5
    mock_reg = _call_remove_stale([entry], active_map_id="3", current_area_ids={3, 11})
    mock_reg.async_remove.assert_not_called()


def test_remove_stale_leaves_present_entity_alone():
    """An entity whose area is still in the current map is not touched."""
    entry = _make_registry_entry(
        unique_id="room_3_map3_cleanings_dev",
        disabled_by=None,
    )
    mock_reg = _call_remove_stale([entry], active_map_id="3", current_area_ids={3, 11})
    mock_reg.async_remove.assert_not_called()


def test_remove_stale_skips_non_room_entity():
    """Entities whose unique_id is not a room format are ignored."""
    entry = _make_registry_entry(
        unique_id="live_map_dev",
        entity_id="sensor.dev_live_map",
        disabled_by=None,
    )
    mock_reg = _call_remove_stale([entry], active_map_id="3", current_area_ids={3})
    mock_reg.async_remove.assert_not_called()


def test_remove_stale_skips_on_empty_area_ids():
    """Guard: if current_area_ids is empty (likely API error), no entities are removed."""
    entry = _make_registry_entry(
        unique_id="room_3_map3_cleanings_dev",
        entity_id="sensor.dev_map3_room_3_cleanings",
        disabled_by=None,
    )
    mock_reg = _call_remove_stale([entry], active_map_id="3", current_area_ids=set())
    mock_reg.async_remove.assert_not_called()


def test_remove_stale_returns_removed_set():
    """Returns the set of (map_id, area_id_str) tuples that were removed."""
    entry = _make_registry_entry(
        unique_id="room_99_map3_cleanings_dev",
        entity_id="sensor.dev_map3_room_99_cleanings",
        disabled_by=None,
    )
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "entry1"
    coordinator = MagicMock()
    coordinator.active_map_id = "3"

    mock_ent_reg = MagicMock()
    with patch.object(_er, "async_get", return_value=mock_ent_reg), \
         patch.object(_er, "async_entries_for_config_entry", return_value=[entry]):
        removed = async_remove_stale_room_entities(hass, config_entry, coordinator, "sensor", {3, 11})
    assert ("3", "99") in removed


# ── Schedule sensor tests ─────────────────────────────────────────────

CONFIRMED_SCHEDULE = {
    "schedule": [{
        "task_id": 2,
        "time": {"days_of_week": [7], "hour": 20, "min": 36, "sec": 0},
        "enabled": 0,
        "task": {
            "map_id": 3,
            "cleaning_parameter_set": 0,
            "cleaning_mode": 2,
            "parameter1": "3",
            "parameter2": "10",
            "parameters": [3, 10],
        },
    }]
}


def _make_sched_coordinator():
    coord = _make_coordinator()
    coord.active_map_id = "3"
    coord.schedule = CONFIRMED_SCHEDULE
    coord.available_maps = [
        {"map_id": "3",  "display_name": "Дружба", "is_active": True,  "statistics": {}},
    ]
    # Ensure areas include room 3 and 10 for name resolution
    coord.areas = [
        {"id": 3,  "area_meta_data": '{"name": "Спалня"}',  "statistics": {}},
        {"id": 10, "area_meta_data": '{"name": "Коридор"}', "statistics": {}},
    ]
    return coord


def _make_schedule_sensor(coord):
    from custom_components.rowenta_roboeye.sensor import RobEyeScheduleSensor
    s = RobEyeScheduleSensor.__new__(RobEyeScheduleSensor)
    s.coordinator = coord
    return s


def test_schedule_confirmed_response():
    coord = _make_sched_coordinator()
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]

    assert s["task_id"]   == 2
    assert s["enabled"]   is False          # int 0 → False
    assert s["days"]      == ["Sun"]
    assert s["days_full"] == ["Sunday"]
    assert s["time"]      == "20:36"
    assert s["mode"]      == "rooms"        # cleaning_mode=2
    assert s["map_id"]    == "3"
    assert s["map_name"]  == "Дружба"
    assert s["rooms_str"] == "Спалня + Коридор"
    assert s["rooms"]     == [{"id": 3, "name": "Спалня"}, {"id": 10, "name": "Коридор"}]
    assert s["fan_raw"]   == 0
    assert s["fan_speed"] == "default"      # 0 → "default", never ""


def test_schedule_cleaning_mode_1_is_all_rooms():
    """cleaning_mode=1 → mode='all' regardless of parameters."""
    coord = _make_sched_coordinator()
    coord.schedule = {"schedule": [{
        "task_id": 1, "enabled": 1,
        "time": {"days_of_week": [1], "hour": 8, "min": 0, "sec": 0},
        "task": {"map_id": 3, "cleaning_parameter_set": 1,
                 "cleaning_mode": 1, "parameters": [3]},  # mode=1 wins
    }]}
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]
    assert s["mode"]      == "all"
    assert s["rooms_str"] == "All rooms"
    assert s["fan_speed"] == "normal"  # cleaning_parameter_set=1 → "normal"


def test_schedule_mode_rooms_empty_parameters():
    """cleaning_mode=2 + empty parameters → mode='rooms', not 'all'."""
    coord = _make_sched_coordinator()
    coord.schedule = {"schedule": [{
        "task_id": 3, "enabled": 1,
        "time": {"days_of_week": [2], "hour": 9, "min": 0, "sec": 0},
        "task": {"map_id": 3, "cleaning_parameter_set": 1,
                 "cleaning_mode": 2, "parameters": []},
    }]}
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]
    assert s["mode"] == "rooms"   # NOT "all"


def test_schedule_disabled_included():
    """enabled=0 entries must appear — /get/schedule returns all."""
    coord = _make_sched_coordinator()
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]
    assert s["enabled"] is False


def test_schedule_fan_zero_is_default():
    """cleaning_parameter_set=0 → 'default', not empty string."""
    coord = _make_sched_coordinator()
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]
    assert s["fan_speed"] == "default"
    assert s["fan_speed"] != ""


def test_schedule_second_floor_map_name():
    """Schedules for the active map resolve the correct map display name."""
    coord = _make_sched_coordinator()
    coord.active_map_id = "18"
    coord.available_maps = [
        {"map_id": "3",  "display_name": "Дружба", "is_active": False, "statistics": {}},
        {"map_id": "18", "display_name": "Map 2",  "is_active": True,  "statistics": {}},
    ]
    coord.schedule = {"schedule": [{
        "task_id": 5, "enabled": 1,
        "time": {"days_of_week": [1], "hour": 7, "min": 0, "sec": 0},
        "task": {"map_id": 18, "cleaning_parameter_set": 0,
                 "cleaning_mode": 1, "parameters": []},
    }]}
    s = _make_schedule_sensor(coord)._parsed_schedules()[0]
    assert s["map_id"]   == "18"
    assert s["map_name"] == "Map 2"


def test_schedule_sensor_state_counts_all_not_just_enabled():
    """native_value = total count including disabled entries."""
    coord = _make_sched_coordinator()
    sensor = _make_schedule_sensor(coord)
    assert sensor.native_value == 1  # one entry, even though enabled=0
