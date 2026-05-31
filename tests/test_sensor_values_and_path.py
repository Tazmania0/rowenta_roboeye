"""T3 — sensor_values GPIO/current parsing.
T5 — live-map robot-path accumulation (distance dedup + point cap).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.rowenta_roboeye.const import DATA_STATUS
from custom_components.rowenta_roboeye.coordinator import (
    RobEyeCoordinator,
    _current_ma,
    _gpio,
    _parse_sensor_values,
    _MAX_PATH_POINTS,
    _MIN_MOVE_UNITS,
)

from .conftest import MOCK_STATUS, make_sensor_values


# ══════════════════════════════════════════════════════════════════════
# T3 — sensor_values parsing
# ══════════════════════════════════════════════════════════════════════

def test_parse_sensor_values_flattens_gpio_current_voltage():
    raw = {
        "sensor_data": [
            {
                "device_type": "gpio",
                "sensor_data": [
                    {"device_descriptor": "side_brush_left_stuck",
                     "payload": {"data": {"value": "active"}}},
                    {"device_descriptor": "dustbin_present",
                     "payload": {"data": {"value": "inactive"}}},
                ],
            },
            {
                "device_type": "current_sensor",
                "sensor_data": [
                    {"device_descriptor": "main_brush",
                     "payload": {"data": {"current": 320}}},
                ],
            },
            {
                "device_type": "voltage_sensor",
                "sensor_data": [
                    {"device_descriptor": "battery",
                     "payload": {"data": {"voltage": 16800}}},
                ],
            },
        ]
    }
    parsed = _parse_sensor_values(raw)
    assert parsed["gpio__side_brush_left_stuck"] == "active"
    assert parsed["gpio__dustbin_present"] == "inactive"
    assert parsed["current_sensor__main_brush"] == 320
    assert parsed["voltage_sensor__battery"] == 16800


def test_parse_sensor_values_empty_is_safe():
    assert _parse_sensor_values({}) == {}
    assert _parse_sensor_values({"sensor_data": []}) == {}


def test_gpio_helper_defaults_to_inactive():
    parsed = {"gpio__x": "active"}
    assert _gpio(parsed, "x") == "active"
    assert _gpio(parsed, "missing") == "inactive"   # default when absent


def test_current_ma_helper_returns_none_when_absent():
    parsed = {"current_sensor__main_brush": 320}
    assert _current_ma(parsed, "main_brush") == 320
    assert _current_ma(parsed, "side_brush") is None


def test_make_sensor_values_roundtrips_through_parser():
    """The test helper produces payloads the real parser understands."""
    parsed = _parse_sensor_values(make_sensor_values(side_brush_right_stuck="active"))
    assert parsed["gpio__side_brush_right_stuck"] == "active"


# ══════════════════════════════════════════════════════════════════════
# T5 — robot-path accumulation
# ══════════════════════════════════════════════════════════════════════

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


async def _tick_with_pose(coordinator, mock_client, x, y):
    """Run one update cycle in cleaning mode with the given rob_pose."""
    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning",
                                           "charging": "unconnected"}
    mock_client.get_rob_pose.return_value = {
        "valid": True, "x1": x, "y1": y, "heading": 90, "timestamp": x + y,
        "map_id": 3,
    }
    await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_path_skips_sub_threshold_moves(coordinator, mock_client):
    """A move smaller than _MIN_MOVE_UNITS is not appended as a new point."""
    await _tick_with_pose(coordinator, mock_client, 0, 0)
    assert len(coordinator._robot_path) == 1
    # Move 1 unit (< 5) — below the dedup threshold, not appended.
    await _tick_with_pose(coordinator, mock_client, 1, 0)
    assert len(coordinator._robot_path) == 1
    # Move well beyond the threshold — appended.
    await _tick_with_pose(coordinator, mock_client, 0, _MIN_MOVE_UNITS + 10)
    assert len(coordinator._robot_path) == 2


@pytest.mark.asyncio
async def test_path_capped_at_max_points(coordinator, mock_client):
    """The path list is trimmed to _MAX_PATH_POINTS (keeping the newest)."""
    # Pre-fill just under the cap so we can observe the trim.
    coordinator._robot_path = [(i * 100, 0) for i in range(_MAX_PATH_POINTS)]
    # Force cleaning state without resetting the path (last_mode already cleaning).
    coordinator._last_mode = "cleaning"
    await _tick_with_pose(coordinator, mock_client, 999999, 999999)
    assert len(coordinator._robot_path) == _MAX_PATH_POINTS
    # Newest point retained at the tail.
    assert coordinator._robot_path[-1] == (999999, 999999)
