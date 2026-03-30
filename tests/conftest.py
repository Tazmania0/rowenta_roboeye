"""Shared pytest fixtures for rowenta_roboeye tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Canonical mock payloads ───────────────────────────────────────────

MOCK_STATUS = {
    "battery_level": 85,
    "mode": "ready",
    "charging": "charging",
    "cleaning_parameter_set": 2,
}

MOCK_STATISTICS = {
    "total_distance_driven": 25000,
    "total_cleaning_time": 36000,
    "total_area_cleaned": 500000,
    "total_number_of_cleaning_runs": 42,
}

MOCK_PERMANENT_STATISTICS = {
    "total_distance_driven": 26000,
    "total_cleaning_time": 37000,
}

MOCK_AREAS = {
    "areas": [
        {
            "id": 3,
            "area_meta_data": '{"name": "Bedroom"}',
            "statistics": {
                "cleaning_counter": 12,
                "area_size": 12_000_000,
                "average_cleaning_time": 900_000,
                "last_cleaned": {"year": 2026, "month": 3, "day": 10},
            },
        },
        {
            "id": 11,
            "area_meta_data": '{"name": "Kitchen"}',
            "statistics": {
                "cleaning_counter": 20,
                "area_size": 8_000_000,
                "average_cleaning_time": 600_000,
                "last_cleaned": {"year": 2026, "month": 3, "day": 15},
            },
        },
        {
            "id": 99,
            "area_meta_data": "",  # No metadata — must be skipped
            "statistics": {},
        },
    ]
}

MOCK_WIFI_STATUS = {"ssid": "HomeNetwork", "rssi": -55, "ip": "192.168.1.100"}
MOCK_ROBOT_ID = {"serial_number": "SN123456789", "robot_id": "aicu-abc123"}
MOCK_PROTOCOL_VERSION = {"version": "2.3.1"}
MOCK_LIVE_PARAMETERS = {"area_cleaned": 50000, "cleaning_time": 720}
MOCK_SENSOR_STATUS = {"cliff_sensor": "ok", "bump_sensor": "ok", "wheel_drop": "ok"}
MOCK_ROBOT_FLAGS = {"has_mop": False, "has_camera": True}
MOCK_MAP_STATUS = {"operation_map_id": 3, "active_map_id": 3}
MOCK_MAPS = {
    "maps": [
        {
            "map_id": 3,
            "map_meta_data": "Ground Floor",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0,
                "cleaning_counter": 5,
                "estimated_cleaning_time": 0,
                "average_cleaning_time": 1800,
                "last_cleaned": {"year": 2026, "month": 3, "day": 20,
                                 "hour": 11, "min": 21, "sec": 0},
            },
        },
        {
            "map_id": 4,
            "map_meta_data": "First Floor",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0,
                "cleaning_counter": 0,
                "estimated_cleaning_time": 0,
                "average_cleaning_time": 0,
                "last_cleaned": {"year": 2001, "month": 1, "day": 1,
                                 "hour": 0, "min": 0, "sec": 0},
            },
        },
    ]
}

MOCK_LOCALIZATION = {"localization_algo_input": [
    {"localization_type": "global",
     "rob_pose": [-6, -9, 5295],
     "rtc_time": {"year": 2026, "month": 3, "day": 18}},
]}

MOCK_RELOCALIZATION = {"localization_algo_input": [
    {"localization_type": "continuous",
     "rob_pose": [-661, 235, -6269],
     "rtc_time": {"year": 2026, "month": 3, "day": 18,
                  "hour": 18, "min": 9, "sec": 10}},
]}

MOCK_EXPLORATION = {"exploration_points": [
    {"ts": 474811434, "type": "smsu_no_nearby_expl_points",
     "rob_pose": [-861, 352, -6298]},
]}

MOCK_ROB_POSE = {
    "map_id": 3,
    "x1": -2,
    "y1": -3,
    "heading": 157,
    "valid": True,
    "is_tentative": False,
    "timestamp": 958459,
}

MOCK_SENSOR_VALUES = {"sensor_data": []}

MOCK_CLEANING_GRID = {
    "map_id": 3,
    "lower_left_x": -823,
    "lower_left_y": -579,
    "size_x": 29,
    "size_y": 29,
    "resolution": 40,
    "cleaned": [
        1, 104, 5, 23, 8, 22, 8, 21, 8, 21, 8, 21, 8, 21, 8, 21,
        8, 7, 3, 11, 8, 7, 4, 10, 8, 7, 5, 1, 3, 5, 7, 8, 10, 4,
        4, 11, 10, 3, 5, 11, 18, 11, 21, 8, 21, 8, 22, 7, 23, 6,
        23, 10, 19, 12, 17, 12, 17, 12, 17, 90,
    ],
    "timestamp": 389888076,
}


@pytest.fixture
def mock_client():
    """Fully-mocked RobEyeApiClient."""
    from custom_components.rowenta_roboeye.api import RobEyeApiClient

    client = AsyncMock(spec=RobEyeApiClient)
    client.get_status.return_value = dict(MOCK_STATUS)
    client.get_statistics.return_value = dict(MOCK_STATISTICS)
    client.get_permanent_statistics.return_value = dict(MOCK_PERMANENT_STATISTICS)
    client.get_areas.return_value = dict(MOCK_AREAS)
    client.get_wifi_status.return_value = dict(MOCK_WIFI_STATUS)
    client.get_robot_id.return_value = dict(MOCK_ROBOT_ID)
    client.get_protocol_version.return_value = dict(MOCK_PROTOCOL_VERSION)
    client.get_live_parameters.return_value = dict(MOCK_LIVE_PARAMETERS)
    client.get_rob_pose.return_value = dict(MOCK_ROB_POSE)
    client.get_sensor_status.return_value = dict(MOCK_SENSOR_STATUS)
    client.get_sensor_values.return_value = dict(MOCK_SENSOR_VALUES)
    client.get_seen_polygon.return_value = {}
    client.get_feature_map.return_value = {}
    client.get_tile_map.return_value = {}
    client.get_schedule.return_value = {}
    client.get_robot_flags.return_value = dict(MOCK_ROBOT_FLAGS)
    client.get_cleaning_grid_map.return_value = dict(MOCK_CLEANING_GRID)
    client.get_map_status.return_value = dict(MOCK_MAP_STATUS)
    client.get_maps.return_value = dict(MOCK_MAPS)
    client.get_localization.return_value = dict(MOCK_LOCALIZATION)
    client.get_relocalization.return_value = dict(MOCK_RELOCALIZATION)
    client.get_exploration.return_value = dict(MOCK_EXPLORATION)
    client.test_connection.return_value = True
    client.clean_all.return_value = None
    client.clean_map.return_value = None
    client.go_home.return_value = None
    client.stop.return_value = None
    client.set_fan_speed.return_value = None
    return client


@pytest.fixture
def mock_config_entry():
    """Minimal ConfigEntry mock."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_abc123"
    entry.data = {
        "host": "192.168.1.100",
        "hostname": "xplorer120.local.",
        "map_id": "3",
    }
    return entry
