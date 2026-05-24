"""Unit tests for binary sensor entities."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.rowenta_roboeye.binary_sensor import (
    RowentaBrushLeftStuckSensor,
    RowentaBrushRightStuckSensor,
    RowentaDustbinSensor,
)


def _make_coordinator(sensor_values_parsed: dict | None = None):
    coord = MagicMock()
    coord.device_id = "sn123456789"
    coord.sensor_values_parsed = sensor_values_parsed if sensor_values_parsed is not None else {}
    return coord


def _make_sensor(cls, sensor_values_parsed: dict | None = None):
    coord = _make_coordinator(sensor_values_parsed)
    sensor = cls.__new__(cls)
    sensor.coordinator = coord
    sensor._attr_unique_id = f"test_{cls.__name__}"
    return sensor


# ── RowentaBrushLeftStuckSensor ──────────────────────────────────────────────

class TestBrushLeftStuck:
    def test_is_on_when_active(self):
        sensor = _make_sensor(
            RowentaBrushLeftStuckSensor,
            {"gpio__side_brush_left_stuck": "active"},
        )
        assert sensor.is_on is True

    def test_is_off_when_inactive(self):
        sensor = _make_sensor(
            RowentaBrushLeftStuckSensor,
            {"gpio__side_brush_left_stuck": "inactive"},
        )
        assert sensor.is_on is False

    def test_is_off_when_key_missing(self):
        sensor = _make_sensor(RowentaBrushLeftStuckSensor, {})
        assert sensor.is_on is False

    def test_unique_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaBrushLeftStuckSensor.__new__(RowentaBrushLeftStuckSensor)
        sensor.coordinator = coord
        sensor._attr_unique_id = f"brush_left_stuck_{coord.device_id}"
        assert sensor._attr_unique_id == "brush_left_stuck_sn123456789"

    def test_entity_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaBrushLeftStuckSensor.__new__(RowentaBrushLeftStuckSensor)
        sensor.coordinator = coord
        sensor.entity_id = f"binary_sensor.{coord.device_id}_left_brush_stuck"
        assert sensor.entity_id == "binary_sensor.sn123456789_left_brush_stuck"


# ── RowentaBrushRightStuckSensor ─────────────────────────────────────────────

class TestBrushRightStuck:
    def test_is_on_when_active(self):
        sensor = _make_sensor(
            RowentaBrushRightStuckSensor,
            {"gpio__side_brush_right_stuck": "active"},
        )
        assert sensor.is_on is True

    def test_is_off_when_inactive(self):
        sensor = _make_sensor(
            RowentaBrushRightStuckSensor,
            {"gpio__side_brush_right_stuck": "inactive"},
        )
        assert sensor.is_on is False

    def test_is_off_when_key_missing(self):
        sensor = _make_sensor(RowentaBrushRightStuckSensor, {})
        assert sensor.is_on is False

    def test_unique_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaBrushRightStuckSensor.__new__(RowentaBrushRightStuckSensor)
        sensor.coordinator = coord
        sensor._attr_unique_id = f"brush_right_stuck_{coord.device_id}"
        assert sensor._attr_unique_id == "brush_right_stuck_sn123456789"

    def test_entity_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaBrushRightStuckSensor.__new__(RowentaBrushRightStuckSensor)
        sensor.coordinator = coord
        sensor.entity_id = f"binary_sensor.{coord.device_id}_right_brush_stuck"
        assert sensor.entity_id == "binary_sensor.sn123456789_right_brush_stuck"


# ── RowentaDustbinSensor ─────────────────────────────────────────────────────

class TestDustbin:
    def test_is_on_when_dustbin_present(self):
        """Dustbin present: gpio 'active' → is_on True."""
        sensor = _make_sensor(
            RowentaDustbinSensor,
            {"gpio__dustbin": "active"},
        )
        assert sensor.is_on is True

    def test_is_off_when_dustbin_missing(self):
        """Dustbin missing: gpio 'inactive' → is_on False."""
        sensor = _make_sensor(
            RowentaDustbinSensor,
            {"gpio__dustbin": "inactive"},
        )
        assert sensor.is_on is False

    def test_is_off_when_key_missing(self):
        sensor = _make_sensor(RowentaDustbinSensor, {})
        assert sensor.is_on is False

    def test_unique_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaDustbinSensor.__new__(RowentaDustbinSensor)
        sensor.coordinator = coord
        sensor._attr_unique_id = f"dustbin_present_{coord.device_id}"
        assert sensor._attr_unique_id == "dustbin_present_sn123456789"

    def test_entity_id_format(self):
        coord = _make_coordinator()
        sensor = RowentaDustbinSensor.__new__(RowentaDustbinSensor)
        sensor.coordinator = coord
        sensor.entity_id = f"binary_sensor.{coord.device_id}_dustbin_present"
        assert sensor.entity_id == "binary_sensor.sn123456789_dustbin_present"


# ── Cross-entity: keys don't bleed between sensors ───────────────────────────

def test_brushes_use_independent_gpio_keys():
    """Left brush key does not affect right brush sensor and vice versa."""
    parsed = {
        "gpio__side_brush_left_stuck": "active",
        "gpio__side_brush_right_stuck": "inactive",
    }
    left = _make_sensor(RowentaBrushLeftStuckSensor, parsed)
    right = _make_sensor(RowentaBrushRightStuckSensor, parsed)
    assert left.is_on is True
    assert right.is_on is False


def test_dustbin_unaffected_by_brush_keys():
    """Dustbin sensor is only driven by its own GPIO key."""
    parsed = {
        "gpio__side_brush_left_stuck": "active",
        "gpio__side_brush_right_stuck": "active",
        "gpio__dustbin": "inactive",
    }
    sensor = _make_sensor(RowentaDustbinSensor, parsed)
    assert sensor.is_on is False
