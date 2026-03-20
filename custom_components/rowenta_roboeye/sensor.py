"""Sensor entities for the Rowenta Xplorer 120 (RobEye) integration.

Architecture
------------
Static sensors are declared via RobEyeSensorDescription tuples and
instantiated generically at setup time — they never change.

Per-room sensors are generated dynamically from /get/areas.  The platform
registers a dispatcher listener on SIGNAL_AREAS_UPDATED_{entry_id}.  When
the coordinator detects new areas it fires that signal and the listener
calls async_add_entities with only the NEW room entities.  Stale entities
for rooms that no longer exist are disabled via the entity registry.

This means dashboard cards for new rooms appear immediately after the next
/get/areas poll WITHOUT requiring an integration reload.

Phase-2 live map sensor
-----------------------
sensor.xplorer120_live_map transports floor_plan, cleaned_area and
robot_position as attributes so the Lovelace SVG card can subscribe via
WebSocket without polling the vacuum API directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfArea,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, FAN_SPEED_MAP, LOGGER, SIGNAL_AREAS_UPDATED

_DOW_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


# ── Extended descriptor ───────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class RobEyeSensorDescription(SensorEntityDescription):
    """SensorEntityDescription extended with a value-extraction callable."""

    value_fn: Callable[[RobEyeCoordinator], Any] = field(default=lambda _c: None)


# Translation key → entity_id suffix (only overrides where key ≠ slugified name)
_SENSOR_ENTITY_ID_SUFFIX: dict[str, str] = {
    "charging":                       "charging_status",
    "fan_speed_label":                "fan_speed",
    "total_area_cleaned":             "total_cleaned_area",
    "total_number_of_cleaning_runs":  "total_cleaning_runs",
    "wifi_rssi":                      "wi_fi_signal_strength",
    "wifi_ssid":                      "wi_fi_network",
    "protocol_version":               "firmware_version",
    "robot_serial":                   "serial_number",
    "sensor_cliff_status":            "cliff_sensor",
    "sensor_bump_status":             "bump_sensor",
    "sensor_wheel_drop_status":       "wheel_drop_sensor",
}


# ── Sensor catalogues ─────────────────────────────────────────────────

STATUS_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: int(c.status.get("battery_level", 0)),
    ),
    RobEyeSensorDescription(
        key="mode",
        translation_key="mode",
        icon="mdi:robot-vacuum",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.status.get("mode"),
    ),
    RobEyeSensorDescription(
        key="charging",
        translation_key="charging",
        icon="mdi:battery-charging",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.status.get("charging"),
    ),
    RobEyeSensorDescription(
        key="fan_speed_label",
        translation_key="fan_speed_label",
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: FAN_SPEED_MAP.get(
            str(c.status.get("cleaning_parameter_set", "")), "unknown"
        ),
    ),
)

LIVE_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="current_area_cleaned",
        translation_key="current_area_cleaned",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:texture-box",
        entity_registry_enabled_default=False,
        value_fn=lambda c: _safe_round(
            c.live_parameters.get("area_cleaned"), divisor=10000, precision=1
        ),  # API = cm²
    ),
    RobEyeSensorDescription(
        key="current_cleaning_time",
        translation_key="current_cleaning_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
        entity_registry_enabled_default=False,
        value_fn=lambda c: _safe_round(
            c.live_parameters.get("cleaning_time"), divisor=60, precision=1
        ),
    ),
)

STATISTICS_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="total_distance_driven",
        translation_key="total_distance_driven",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda c: round(
            c.statistics.get("total_distance_driven", 0) / 100, 1
        ),
    ),
    RobEyeSensorDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda c: round(
            c.statistics.get("total_cleaning_time", 0) / 60, 1
        ),
    ),
    RobEyeSensorDescription(
        key="total_area_cleaned",
        translation_key="total_area_cleaned",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda c: round(
            c.statistics.get("total_area_cleaned", 0) / 100, 1  # API = 0.01 cm² units
        ),
    ),
    RobEyeSensorDescription(
        key="total_number_of_cleaning_runs",
        translation_key="total_number_of_cleaning_runs",
        native_unit_of_measurement="runs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.statistics.get("total_number_of_cleaning_runs"),
    ),
)

ROBOT_INFO_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.robot_info.get("wifi_status", {}).get("rssi"),
    ),
    RobEyeSensorDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.robot_info.get("wifi_status", {}).get("ssid"),
    ),
    RobEyeSensorDescription(
        key="protocol_version",
        translation_key="protocol_version",
        icon="mdi:tag-text",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.robot_info.get("protocol_version", {}).get("version"),
    ),
    RobEyeSensorDescription(
        key="robot_serial",
        translation_key="robot_serial",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.robot_info.get("robot_id", {}).get("serial_number")
        or c.robot_info.get("robot_id", {}).get("robot_id"),
    ),
)

SENSOR_VALUES_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="main_brush_current_ma",
        translation_key="main_brush_current_ma",
        native_unit_of_measurement="mA",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_values_parsed.get("current_sensor__main_brush"),
    ),
    RobEyeSensorDescription(
        key="side_brush_left_current_ma",
        translation_key="side_brush_left_current_ma",
        native_unit_of_measurement="mA",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_values_parsed.get("current_sensor__side_brush_left"),
    ),
    RobEyeSensorDescription(
        key="side_brush_right_current_ma",
        translation_key="side_brush_right_current_ma",
        native_unit_of_measurement="mA",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_values_parsed.get("current_sensor__side_brush_right"),
    ),
)

SENSOR_HEALTH_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="sensor_cliff_status",
        translation_key="sensor_cliff_status",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_status.get("cliff_sensor"),
    ),
    RobEyeSensorDescription(
        key="sensor_bump_status",
        translation_key="sensor_bump_status",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_status.get("bump_sensor"),
    ),
    RobEyeSensorDescription(
        key="sensor_wheel_drop_status",
        translation_key="sensor_wheel_drop_status",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_status.get("wheel_drop"),
    ),
)

ALL_STATIC_SENSORS: tuple[tuple[RobEyeSensorDescription, ...], ...] = (
    STATUS_SENSORS,
    LIVE_SENSORS,
    STATISTICS_SENSORS,
    ROBOT_INFO_SENSORS,
    SENSOR_HEALTH_SENSORS,
    SENSOR_VALUES_SENSORS,
)


# ── Platform setup ────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up static and dynamic (per-room) sensor entities."""
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # ── Static sensors ────────────────────────────────────────────────
    entities: list[SensorEntity] = []
    for group in ALL_STATIC_SENSORS:
        for description in group:
            entities.append(RobEyeStaticSensor(coordinator, description))

    # Phase-2 live map sensor
    entities.append(RobEyeLiveMapSensor(coordinator))

    # Schedule sensor
    entities.append(RobEyeScheduleSensor(coordinator))

    # ── Per-room sensors (from current area list) ─────────────────────
    known_ids: set = set()
    room_entities, new_ids = _build_room_sensor_entities(
        coordinator, config_entry, coordinator.areas, known_ids
    )
    entities.extend(room_entities)
    known_ids.update(new_ids)

    async_add_entities(entities)

    # ── Dynamic listener: add entities when new rooms appear ──────────
    @callback
    def _async_on_areas_updated() -> None:
        """Called by the coordinator when the area set changes."""
        new_entities, new_area_ids = _build_room_sensor_entities(
            coordinator, config_entry, coordinator.areas, known_ids
        )
        if new_entities:
            LOGGER.debug("sensor: adding %d new room entities", len(new_entities))
            async_add_entities(new_entities)
            known_ids.update(new_area_ids)

        # Disable entities for areas that have disappeared from the API
        _async_remove_stale_room_entities(
            hass, config_entry, coordinator, "sensor", known_ids
        )

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _async_on_areas_updated,
        )
    )


# ── Sensor entity classes ─────────────────────────────────────────────

class RobEyeStaticSensor(RobEyeEntity, SensorEntity):
    """A sensor backed by a static RobEyeSensorDescription."""

    entity_description: RobEyeSensorDescription

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        description: RobEyeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{description.key}_{coordinator.device_id}"
        suffix = _SENSOR_ENTITY_ID_SUFFIX.get(description.key, description.key)
        self.entity_id = f"sensor.{coordinator.device_id}_{suffix}"

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator)
        except Exception:  # noqa: BLE001
            return None


class RobEyeLiveMapSensor(RobEyeEntity, SensorEntity):
    """Transport sensor for Phase-2 SVG map card.

    State mirrors the vacuum activity.  Rich map data (floor plan, cleaned
    area, robot position) is exposed as entity attributes so the Lovelace
    card can subscribe via hass.connection.subscribeEntities — no direct
    HTTP polling from the browser.
    """

    _attr_icon = "mdi:map"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"live_map_{coordinator.device_id}"
        self._attr_name = "Live Map"
        self.entity_id = f"sensor.{coordinator.device_id}_live_map"

    @property
    def native_value(self) -> str:
        from .const import MODE_CLEANING, MODE_GO_HOME
        mode = self.coordinator.status.get("mode", "")
        if mode == MODE_CLEANING:
            live_map = self.coordinator.live_map
            # Exploring new map (no saved map rooms available yet)
            if live_map.get("is_live_map"):
                return "exploring"
            # Distinguish mapping (no area data yet) from cleaning
            rooms = live_map.get("rooms", [])
            live_outline = live_map.get("live_outline", [])
            if not rooms and live_outline:
                return "mapping"
            return "cleaning"
        if mode == MODE_GO_HOME:
            return "returning"
        if self.coordinator.session_complete:
            return "session_complete"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.live_map)


class RobEyeScheduleSensor(RobEyeEntity, SensorEntity):
    """Sensor that exposes the robot's cleaning schedule as attributes.

    State: number of active schedules (or 0 when none).
    Attribute 'schedules': list of parsed schedule dicts consumed by the
    dashboard Jinja2 markdown card.
    """

    _attr_icon = "mdi:calendar-clock"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"schedule_{coordinator.device_id}"
        self._attr_name = "Schedule"
        self.entity_id = f"sensor.{coordinator.device_id}_schedule"

    @property
    def native_value(self) -> int:
        return len(self._parsed_schedules())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"schedules": self._parsed_schedules()}

    def _parsed_schedules(self) -> list[dict[str, Any]]:
        raw_list = self.coordinator.schedule.get("schedule", [])
        if not isinstance(raw_list, list):
            return []
        parsed: list[dict[str, Any]] = []
        for item in raw_list:
            time_block = item.get("time", {})
            task_block = item.get("task", {})
            days_of_week = time_block.get("days_of_week", [])
            days = [_DOW_NAMES.get(d, str(d)) for d in days_of_week]
            hour = time_block.get("hour", 0)
            minute = time_block.get("min", 0)
            time_str = f"{hour:02d}:{minute:02d}"
            fan_raw = int(task_block.get("cleaning_parameter_set", 0))
            fan_speed = FAN_SPEED_MAP.get(str(fan_raw), "")
            room_ids = task_block.get("parameters", [])
            if room_ids:
                rooms_str = ", ".join(
                    _room_name_for_id(self.coordinator, int(r)) for r in room_ids
                )
            else:
                rooms_str = "All Rooms"
            parsed.append({
                "enabled": bool(item.get("enabled", 0)),
                "days": days,
                "time": time_str,
                "rooms_str": rooms_str,
                "fan_raw": fan_raw,
                "fan_speed": fan_speed,
            })
        return parsed


def _room_name_for_id(coordinator: RobEyeCoordinator, area_id: int) -> str:
    """Return the human-readable room name for an area_id, or the id as fallback."""
    for area in coordinator.areas:
        if area.get("id") == area_id:
            meta_raw = area.get("area_meta_data", "")
            if meta_raw:
                try:
                    meta = json.loads(meta_raw)
                    name = meta.get("name", "").strip()
                    if name:
                        return name
                except (json.JSONDecodeError, TypeError):
                    pass
            return f"Room {area_id}"
    return f"Room {area_id}"


class RobEyeRoomSensor(RobEyeEntity, SensorEntity):
    """A dynamically-generated sensor for one discovered room."""

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        config_entry: ConfigEntry,
        area_id: int | str,
        unique_suffix: str,
        display_name: str,
        value_fn: Callable[[RobEyeCoordinator], Any],
        native_unit: str | None = None,
        icon: str | None = None,
        forced_entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._value_fn = value_fn
        self._attr_unique_id = (
            f"room_{area_id}_{unique_suffix}_{coordinator.device_id}"
        )
        # Use the human-readable room name directly — this is what HA displays.
        # unique_id is stable (area_id-based) so entity_id doesn't change.
        self._attr_name = display_name
        if forced_entity_id is not None:
            self.entity_id = forced_entity_id
        self._attr_native_unit_of_measurement = native_unit
        self._attr_icon = icon
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        try:
            return self._value_fn(self.coordinator)
        except Exception:  # noqa: BLE001
            return None


# ── Room sensor factory ───────────────────────────────────────────────

def _build_room_sensor_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    areas: list[dict[str, Any]],
    already_known: set,
) -> tuple[list[RobEyeRoomSensor], set]:
    """Return NEW room sensor entities (not already in already_known) plus their IDs."""
    new_entities: list[RobEyeRoomSensor] = []
    new_ids: set = set()

    for area in areas:
        area_id = area.get("id")
        if area_id is None or area_id in already_known:
            continue
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except (json.JSONDecodeError, TypeError):
            LOGGER.debug("Skipping area with unparseable meta_data: %s", meta_raw)
            continue
        room_name = meta.get("name", "").strip()
        if not room_name:
            continue
        new_entities.extend(
            _build_room_sensors(coordinator, config_entry, area_id, room_name, coordinator.device_id)
        )
        new_ids.add(area_id)

    return new_entities, new_ids


def _build_room_sensors(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    area_id: int | str,
    room_name: str,
    device_id: str | None = None,
) -> list[RobEyeRoomSensor]:
    """Return the four per-room sensor entities for a discovered area."""

    def _stats(c: RobEyeCoordinator) -> dict[str, Any]:
        for a in c.areas:
            if a.get("id") == area_id:
                return a.get("statistics", {})
        return {}

    _dev = device_id or coordinator.device_id
    return [
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix="cleanings",
            display_name=f"{room_name} Cleanings",
            icon="mdi:counter",
            value_fn=lambda c: _stats(c).get("cleaning_counter"),
            forced_entity_id=f"sensor.{_dev}_room_{area_id}_cleanings",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix="area",
            display_name=f"{room_name} Area",
            native_unit=UnitOfArea.SQUARE_METERS,
            icon="mdi:texture-box",
            value_fn=lambda c: round(_stats(c).get("area_size", 0) / 500_000, 2),  # API = 0.5 mm² units
            forced_entity_id=f"sensor.{_dev}_room_{area_id}_area",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix="avg_time",
            display_name=f"{room_name} Avg Clean Time",
            native_unit=UnitOfTime.MINUTES,
            icon="mdi:timer-outline",
            value_fn=lambda c: round(
                _stats(c).get("average_cleaning_time", 0) / 60_000, 1
            ),
            forced_entity_id=f"sensor.{_dev}_room_{area_id}_avg_clean_time",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix="last_cleaned",
            display_name=f"{room_name} Last Cleaned",
            icon="mdi:calendar-clock",
            value_fn=lambda c: _format_date(_stats(c).get("last_cleaned", {})),
            forced_entity_id=f"sensor.{_dev}_room_{area_id}_last_cleaned",
        ),
    ]


def _async_remove_stale_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    platform: str,
    current_area_ids: set,
) -> None:
    """Disable entity registry entries for rooms no longer in the API response."""
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    for entry in entries:
        if entry.domain != platform:
            continue
        # Room sensor unique_ids look like: room_<id>_<suffix>_<device_id>
        if not entry.unique_id.startswith("room_"):
            continue
        parts = entry.unique_id.split("_")
        if len(parts) < 3:
            continue
        try:
            area_id_str = parts[1]
            # Try int match first, then string
            matches = (
                int(area_id_str) in current_area_ids
                or area_id_str in current_area_ids
                or area_id_str in {str(x) for x in current_area_ids}
            )
            if not matches:
                LOGGER.info(
                    "RobEye: disabling stale room entity %s (area %s no longer present)",
                    entry.entity_id,
                    area_id_str,
                )
                ent_reg.async_update_entity(entry.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        except (ValueError, IndexError):
            pass


# ── Helpers ───────────────────────────────────────────────────────────

def _safe_round(
    value: Any, divisor: float = 1.0, precision: int = 1
) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / divisor, precision)
    except (TypeError, ValueError):
        return None


def _format_date(last_cleaned: dict[str, Any]) -> str | None:
    if not last_cleaned:
        return None
    try:
        y = last_cleaned.get("year", 0)
        m = last_cleaned.get("month", 0)
        d = last_cleaned.get("day", 0)
        return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:  # noqa: BLE001
        return None
