"""Binary sensor entities for the Rowenta Xplorer 120 (RobEye) integration.

Entities
--------
RowentaBrushLeftStuckSensor  — BinarySensorDeviceClass.PROBLEM
    On when side_brush_left_stuck GPIO reads 'active' (brush stuck).
    Off when brush is free (OK).
RowentaBrushRightStuckSensor — BinarySensorDeviceClass.PROBLEM
    On when side_brush_right_stuck GPIO reads 'active' (brush stuck).
    Off when brush is free (OK).
RowentaDustbinSensor         — no device class (uses translation states: Missing/Present)
    On (Missing) when dustbin GPIO reads 'inactive' (dustbin is missing).
    Off (Present) when dustbin is present.

All three are EntityCategory.DIAGNOSTIC and read from
coordinator.data["sensor_values_parsed"], which is populated every 300 s
by the coordinator's sensor_values fetch.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([
        RowentaBrushLeftStuckSensor(coordinator),
        RowentaBrushRightStuckSensor(coordinator),
        RowentaDustbinSensor(coordinator),
    ])


class RowentaBrushLeftStuckSensor(RobEyeEntity, BinarySensorEntity):
    """Binary sensor: left side brush stuck or free.

    is_on = True  → brush is stuck  (GPIO 'active')
    is_on = False → brush is OK     (GPIO 'inactive')
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "brush_left"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"brush_left_stuck_{coordinator.device_id}"
        self.entity_id = f"binary_sensor.{coordinator.device_id}_left_brush_stuck"

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.sensor_values_parsed.get("gpio__side_brush_left_stuck")
            == "active"
        )


class RowentaBrushRightStuckSensor(RobEyeEntity, BinarySensorEntity):
    """Binary sensor: right side brush stuck or free.

    is_on = True  → brush is stuck  (GPIO 'active')
    is_on = False → brush is OK     (GPIO 'inactive')
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "brush_right"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"brush_right_stuck_{coordinator.device_id}"
        self.entity_id = f"binary_sensor.{coordinator.device_id}_right_brush_stuck"

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.sensor_values_parsed.get("gpio__side_brush_right_stuck")
            == "active"
        )


class RowentaDustbinSensor(RobEyeEntity, BinarySensorEntity):
    """Binary sensor: dustbin present or missing.

    The dustbin GPIO is active-high: the circuit reads 'active' when the
    dustbin is physically seated and 'inactive' when it is removed.

    is_on = True  → dustbin present (GPIO 'active')   → state: Present
    is_on = False → dustbin missing (GPIO 'inactive')  → state: Missing

    No device_class so HA uses the translation strings (Present / Missing)
    rather than the built-in PROBLEM class labels (Problem / OK).
    Icon is defined per-state in icons.json.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "dustbin"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"dustbin_present_{coordinator.device_id}"
        self.entity_id = f"binary_sensor.{coordinator.device_id}_dustbin_present"

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.sensor_values_parsed.get("gpio__dustbin") == "active"
        )
