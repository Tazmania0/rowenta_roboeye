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
coordinator.data["sensor_values_parsed"], which is populated every coordinator
tick (5 s while cleaning, 15 s idle) by the sensor_values fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DROP_SENSOR_CLEAN_M2,
    DUSTBIN_CLEAN_HOURS,
    DUSTBIN_CLEAN_M2,
    FILTER_CLEAN_HOURS,
    FILTER_CLEAN_M2,
    MAIN_BRUSH_CLEAN_HOURS,
    MAIN_BRUSH_CLEAN_M2,
    MAIN_BRUSH_REPLACE_HOURS,
    MOP_PAD_REPLACE_HOURS,
    SIDE_BRUSH_CLEAN_HOURS,
    SIDE_BRUSH_CLEAN_M2,
    SIDE_BRUSH_REPLACE_HOURS,
)
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[BinarySensorEntity] = [
        RowentaBrushLeftStuckSensor(coordinator),
        RowentaBrushRightStuckSensor(coordinator),
        RowentaDustbinSensor(coordinator),
    ]
    entities.extend(build_maintenance_due_sensors(coordinator))
    async_add_entities(entities)


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


# ── Maintenance "due" alerts ───────────────────────────────────────────


def _due_replace(component: str, limit: float):
    return lambda c: c.maintenance.runtime_since_replace_h(
        component, c.perm_total_cleaning_time
    ) >= limit


def _due_clean(component: str, limit: float):
    return lambda c: c.maintenance.area_since_clean_m2(
        component, c.perm_total_area_cleaned
    ) >= limit


def _due_clean_or_time(component: str, area_limit: float, hour_limit: float):
    return lambda c: (
        c.maintenance.area_since_clean_m2(component, c.perm_total_area_cleaned) >= area_limit
        or c.maintenance.runtime_since_clean_h(component, c.perm_total_cleaning_time) >= hour_limit
    )


def _due_dustbin(c: RobEyeCoordinator) -> bool:
    return (
        c.maintenance.area_since_clean_m2("dustbin", c.perm_total_area_cleaned)
        >= DUSTBIN_CLEAN_M2
        or c.maintenance.runtime_since_clean_h("dustbin", c.perm_total_cleaning_time)
        >= DUSTBIN_CLEAN_HOURS
    )


@dataclass(frozen=True, kw_only=True)
class RobEyeMaintenanceDueDescription:
    """Description for a maintenance "due" binary sensor."""

    key: str               # MaintenanceStore component key
    translation_key: str
    entity_suffix: str
    icon: str
    is_due_fn: Callable[[RobEyeCoordinator], bool]
    wet_only: bool = False


MAINTENANCE_DUE_SENSORS: tuple[RobEyeMaintenanceDueDescription, ...] = (
    RobEyeMaintenanceDueDescription(
        key="main_brush_replace", translation_key="main_brush_due",
        entity_suffix="main_brush_due", icon="mdi:brush",
        is_due_fn=_due_replace("main_brush", MAIN_BRUSH_REPLACE_HOURS),
    ),
    RobEyeMaintenanceDueDescription(
        key="side_brush_replace", translation_key="side_brush_due",
        entity_suffix="side_brush_due", icon="mdi:rotate-right",
        is_due_fn=_due_replace("side_brush", SIDE_BRUSH_REPLACE_HOURS),
    ),
    RobEyeMaintenanceDueDescription(
        key="mop_pad_replace", translation_key="mop_pad_due",
        entity_suffix="mop_pad_due", icon="mdi:water",
        is_due_fn=_due_replace("mop_pad", MOP_PAD_REPLACE_HOURS),
        wet_only=True,
    ),
    RobEyeMaintenanceDueDescription(
        key="main_brush_clean", translation_key="main_brush_clean_due",
        entity_suffix="main_brush_clean_due", icon="mdi:brush",
        is_due_fn=_due_clean_or_time("main_brush", MAIN_BRUSH_CLEAN_M2, MAIN_BRUSH_CLEAN_HOURS),
    ),
    RobEyeMaintenanceDueDescription(
        key="side_brush_clean", translation_key="side_brush_clean_due",
        entity_suffix="side_brush_clean_due", icon="mdi:rotate-right",
        is_due_fn=_due_clean_or_time("side_brush", SIDE_BRUSH_CLEAN_M2, SIDE_BRUSH_CLEAN_HOURS),
    ),
    RobEyeMaintenanceDueDescription(
        key="dustbin_clean", translation_key="dustbin_empty_due",
        entity_suffix="dustbin_empty_due", icon="mdi:delete-alert",
        is_due_fn=_due_dustbin,
    ),
    RobEyeMaintenanceDueDescription(
        key="filter_clean", translation_key="filter_clean_due",
        entity_suffix="filter_clean_due", icon="mdi:air-filter",
        is_due_fn=_due_clean_or_time("filter", FILTER_CLEAN_M2, FILTER_CLEAN_HOURS),
    ),
    RobEyeMaintenanceDueDescription(
        key="drop_sensor_clean", translation_key="drop_sensor_clean_due",
        entity_suffix="drop_sensor_clean_due", icon="mdi:leak",
        is_due_fn=_due_clean("drop_sensor", DROP_SENSOR_CLEAN_M2),
    ),
)


class RobEyeMaintenanceDueSensor(RobEyeEntity, BinarySensorEntity):
    """True when a maintenance threshold has been crossed."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        description: RobEyeMaintenanceDueDescription,
    ) -> None:
        super().__init__(coordinator)
        self._desc = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon
        self._attr_unique_id = f"{description.entity_suffix}_{coordinator.device_id}"
        self.entity_id = (
            f"binary_sensor.{coordinator.device_id}_{description.entity_suffix}"
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.maintenance is not None
            and bool(self.coordinator.statistics)
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.maintenance is None:
            return False
        try:
            return bool(self._desc.is_due_fn(self.coordinator))
        except Exception:  # noqa: BLE001
            return False


def build_maintenance_due_sensors(
    coordinator: RobEyeCoordinator,
) -> list[BinarySensorEntity]:
    """Build all maintenance "due" binary sensors for the device."""
    return [
        RobEyeMaintenanceDueSensor(coordinator, desc)
        for desc in MAINTENANCE_DUE_SENSORS
        if not desc.wet_only or coordinator.has_wet_support
    ]
