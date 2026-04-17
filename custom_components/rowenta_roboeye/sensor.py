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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AREA_STATE_BLOCKING,
    CLEANING_MODE_ALL,
    CLEANING_MODE_ROOMS,
    DOMAIN,
    EVENT_TYPE_LABELS,
    FAN_SPEED_LABELS,
    FAN_SPEED_MAP,
    LOGGER,
    SCHEDULE_DAYS,
    SCHEDULE_DAYS_FULL,
    SIGNAL_AREAS_UPDATED,
    room_selection_entity_id,
)
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


# ── Helpers ───────────────────────────────────────────────────────────

def _resolve_active_map_name(coordinator: RobEyeCoordinator) -> str | None:
    """Return the display name of the currently active map.

    Confirmed naming (2026-03-29):
      non-empty map_meta_data → user name e.g. "Дружба"
      empty map_meta_data     → "Map {N}"  (1-based position in list)

    Returns None until /get/map_status has been fetched at least once.
    """
    active_id = coordinator.active_map_id
    if not active_id:
        return None

    for m in coordinator.available_maps:
        if m["map_id"] == active_id:
            return m["display_name"]

    # /get/maps not yet fetched — bare fallback
    return f"Map {active_id}"


# ── Sensor catalogues ─────────────────────────────────────────────────

STATUS_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
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
    RobEyeSensorDescription(
        key="active_map",
        translation_key="active_map",
        icon="mdi:layers",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        value_fn=_resolve_active_map_name,
    ),
    RobEyeSensorDescription(
        key="queue_eta",
        translation_key="queue_eta",
        icon="mdi:timer-outline",
        native_unit_of_measurement="s",
        device_class=SensorDeviceClass.DURATION,
        entity_registry_enabled_default=True,
        value_fn=lambda c: c.queue_eta_seconds,
    ),
    RobEyeSensorDescription(
        key="last_event",
        translation_key="last_event",
        icon="mdi:history",
        entity_registry_enabled_default=True,
        value_fn=lambda c: (
            EVENT_TYPE_LABELS.get(
                c._recent_events[-1]["type_id"],
                c._recent_events[-1].get("type", ""),
            ) if c._recent_events else None
        ),
    ),
)

LIVE_SENSORS: tuple[RobEyeSensorDescription, ...] = (
    RobEyeSensorDescription(
        key="current_area_cleaned",
        translation_key="current_area_cleaned",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
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
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_values_parsed.get("current_sensor__main_brush"),
    ),
    RobEyeSensorDescription(
        key="side_brush_left_current_ma",
        translation_key="side_brush_left_current_ma",
        native_unit_of_measurement="mA",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.sensor_values_parsed.get("current_sensor__side_brush_left"),
    ),
    RobEyeSensorDescription(
        key="side_brush_right_current_ma",
        translation_key="side_brush_right_current_ma",
        native_unit_of_measurement="mA",
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
    async_add_entities: AddEntitiesCallback,
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

    # Command queue status sensor
    entities.append(RobEyeQueueStatusSensor(coordinator))

    # Selected room count sensor (used by dashboard "Clean Selected" button label)
    entities.append(RobEyeSelectedRoomCountSensor(coordinator))

    # ── Per-room sensors (from current area list) ─────────────────────
    known_sensor_map: dict = {}
    initial_sensors, initial_by_area = _build_room_sensor_entities(
        coordinator, config_entry, coordinator.areas, set()
    )
    known_sensor_map.update(initial_by_area)
    entities.extend(initial_sensors)

    async_add_entities(entities)

    # ── Dynamic listener: add/remove entities when area set changes ───
    @callback
    def _async_on_areas_updated() -> None:
        """Called by the coordinator when the area set changes."""
        if coordinator.areas_map_id != coordinator.active_map_id:
            LOGGER.debug("sensor: areas fetched for wrong map, skipping update")
            return

        current_ids: set = {
            area_id
            for area in coordinator.areas
            if (area_id := area.get("id")) is not None
            and _parse_sensor_area_name(area)
        }

        stale_ids = set(known_sensor_map.keys()) - current_ids
        for area_id in stale_ids:
            for entity in known_sensor_map.pop(area_id):
                LOGGER.debug("sensor: removing stale room sensor area_id=%s", area_id)
                hass.async_create_task(entity.async_remove())

        new_entities, new_by_area = _build_room_sensor_entities(
            coordinator, config_entry, coordinator.areas, set(known_sensor_map.keys())
        )
        if new_entities:
            LOGGER.debug("sensor: adding %d new room entities", len(new_entities))
            known_sensor_map.update(new_by_area)
            async_add_entities(new_entities)

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
        from .const import (
            CHARGING_UNCONNECTED,
            MODE_CLEANING,
            MODE_GO_HOME,
            MODE_NOT_READY,
        )
        status = self.coordinator.status
        mode = status.get("mode", "")
        charging = status.get("charging", "")

        # Error / stuck: hardware fault or robot not-ready
        sv = self.coordinator.sensor_values_parsed
        hardware_error = (
            mode == MODE_NOT_READY
            or sv.get("gpio__dustbin") == "inactive"
            or sv.get("gpio__side_brush_left_stuck") == "active"
            or sv.get("gpio__side_brush_right_stuck") == "active"
        )
        if hardware_error:
            return "error"

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
        # Paused: off dock, not cleaning, not returning home
        if charging == CHARGING_UNCONNECTED:
            return "paused"
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
        """Parse all schedule entries from /get/schedule.

        Confirmed (2026-03-29): returns ALL schedules, enabled and disabled.
        enabled is int 0/1. cleaning_mode determines mode, not parameters length.
        cleaning_parameter_set=0 means each room uses its own stored setting.
        """
        raw_list = self.coordinator.schedule.get("schedule", [])
        if not isinstance(raw_list, list):
            return []

        active_map = self.coordinator.active_map_id
        result = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue

            t    = item.get("time", {})
            task = item.get("task", {})

            days_of_week  = t.get("days_of_week", [])
            hour          = t.get("hour", 0)
            minute        = t.get("min", 0)
            cleaning_mode = int(task.get("cleaning_mode", CLEANING_MODE_ALL))
            fan_raw       = int(task.get("cleaning_parameter_set", 0))
            area_ids      = task.get("parameters", [])
            task_map_id   = str(task.get("map_id", "")).strip()

            # Only show schedules that belong to the currently active map.
            # When both ids are known and they differ, skip this entry so
            # rooms from a foreign map are never shown with wrong names.
            if task_map_id and active_map and task_map_id != active_map:
                continue

            # Resolve map display name from coordinator.available_maps
            map_name = ""
            for m in self.coordinator.available_maps:
                if m["map_id"] == task_map_id:
                    map_name = m["display_name"]
                    break
            if not map_name and task_map_id:
                map_name = f"Map {task_map_id}"

            # Mode from cleaning_mode field — NOT from parameters length
            is_all = (cleaning_mode == CLEANING_MODE_ALL)

            # Resolve room names
            rooms: list[dict[str, Any]] = []
            if not is_all:
                for aid in area_ids:
                    rooms.append({
                        "id":   int(aid),
                        "name": _room_name_for_id(self.coordinator, int(aid)),
                    })

            rooms_str = "All rooms" if is_all else " + ".join(r["name"] for r in rooms)

            result.append({
                "task_id":   item.get("task_id"),
                "enabled":   bool(int(item.get("enabled", 0))),
                "days":      [SCHEDULE_DAYS.get(d, str(d)) for d in days_of_week],
                "days_full": [SCHEDULE_DAYS_FULL.get(d, str(d)) for d in days_of_week],
                "time":      f"{hour:02d}:{minute:02d}",
                "hour":      hour,
                "minute":    minute,
                "mode":      "all" if is_all else "rooms",
                "map_id":    task_map_id,
                "map_name":  map_name,
                "rooms":     rooms,
                "rooms_str": rooms_str,
                "fan_raw":   fan_raw,
                "fan_speed": FAN_SPEED_LABELS.get(fan_raw, str(fan_raw)),
            })

        return result


class RobEyeQueueStatusSensor(RobEyeEntity, SensorEntity):
    """Shows the HA-side command queue contents.

    State: number of items in queue (0 = idle).
    Attribute 'queue': list of {status, label, map_name} dicts.
    Attribute 'recent_events': last 10 top-level robot events.
    """

    _attr_icon = "mdi:playlist-play"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"command_queue_{coordinator.device_id}"
        self._attr_name = "Cleaning Queue"
        self.entity_id = f"sensor.{coordinator.device_id}_cleaning_queue"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.command_queue_items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        events = self.coordinator._recent_events[-10:]
        recent = [
            {
                "type": EVENT_TYPE_LABELS.get(e["type_id"], e.get("type", "")),
                "time": (
                    f"{e['timestamp']['hour']:02d}:{e['timestamp']['min']:02d}"
                    if "timestamp" in e else ""
                ),
                "room": self.coordinator._resolve_room_name_by_id(e.get("area_id", 0)),
                "map":  self.coordinator._resolve_map_name(str(e.get("map_id", ""))),
            }
            for e in events
        ]
        return {
            "queue": self.coordinator.command_queue_items,
            "recent_events": recent,
        }


class RobEyeSelectedRoomCountSensor(RobEyeEntity, SensorEntity):
    """Count of currently selected rooms for multi-room cleaning.

    State: integer (0 = none selected).
    Used in dashboard template: '▶ Clean Selected ({{ states(...) }})'
    Updates on every coordinator poll (15s idle, 5s cleaning).
    """

    _attr_icon = "mdi:checkbox-multiple-marked-circle"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"selected_room_count_{coordinator.device_id}"
        self._attr_name = "Selected Room Count"
        self.entity_id = f"sensor.{coordinator.device_id}_selected_room_count"

    @property
    def native_value(self) -> int:
        device_id = self.coordinator.device_id
        map_id = self.coordinator.active_map_id
        count = 0
        for area in self.coordinator.areas:
            area_id = area.get("id")
            if area_id is None:
                continue
            eid = room_selection_entity_id(device_id, map_id, str(area_id))
            state = self.coordinator.hass.states.get(eid)
            if state is not None and state.state == "on":
                count += 1
        return count


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
        _map = coordinator.active_map_id
        self._attr_unique_id = (
            f"room_{area_id}_map{_map}_{unique_suffix}_{coordinator.device_id}"
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

def _parse_sensor_area_name(area: dict) -> str:
    """Return the room name from area_meta_data, or empty string."""
    meta_raw = area.get("area_meta_data", "")
    if not meta_raw:
        return ""
    try:
        meta = json.loads(meta_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    return meta.get("name", "").strip()


def _build_room_sensor_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    areas: list[dict[str, Any]],
    already_known: set,
) -> tuple[list[RobEyeRoomSensor], dict]:
    """Return (flat entity list, area_id→entities map) for new rooms only."""
    flat: list[RobEyeRoomSensor] = []
    by_area: dict = {}

    _map = coordinator.active_map_id
    # Guard: skip if areas data was fetched for a different map (stale-signal race).
    if coordinator.areas_map_id != _map:
        return flat, by_area
    for area in areas:
        area_id = area.get("id")
        if area_id is None or area_id in already_known:
            continue
        room_name = _parse_sensor_area_name(area)
        if not room_name:
            LOGGER.debug("Skipping area with no name: id=%s", area_id)
            continue
        # Skip areas disabled for cleaning in the RobEye app
        if area.get("area_state") == AREA_STATE_BLOCKING:
            continue
        sensors = _build_room_sensors(
            coordinator, config_entry, area_id, room_name,
            coordinator.device_id, map_id=_map,
        )
        flat.extend(sensors)
        by_area[area_id] = sensors

    return flat, by_area


def _build_room_sensors(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    area_id: int | str,
    room_name: str,
    device_id: str | None = None,
    map_id: str = "",
) -> list[RobEyeRoomSensor]:
    """Return the four per-room sensor entities for a discovered area."""

    def _stats(c: RobEyeCoordinator) -> dict[str, Any]:
        for a in c.areas:
            if a.get("id") == area_id:
                return a.get("statistics", {})
        return {}

    _dev = device_id or coordinator.device_id
    _m = f"map{map_id}_" if map_id else ""
    return [
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix=f"{_m}cleanings",
            display_name=f"{room_name} Cleanings",
            icon="mdi:counter",
            value_fn=lambda c: _stats(c).get("cleaning_counter"),
            forced_entity_id=f"sensor.{_dev}_{_m}room_{area_id}_cleanings",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix=f"{_m}area",
            display_name=f"{room_name} Area",
            native_unit=UnitOfArea.SQUARE_METERS,
            icon="mdi:texture-box",
            value_fn=lambda c: round(_stats(c).get("area_size", 0) / 500_000, 2),  # API = 0.5 mm² units
            forced_entity_id=f"sensor.{_dev}_{_m}room_{area_id}_area",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix=f"{_m}avg_time",
            display_name=f"{room_name} Avg Clean Time",
            native_unit=UnitOfTime.MINUTES,
            icon="mdi:timer-outline",
            value_fn=lambda c: round(
                _stats(c).get("average_cleaning_time", 0) / 60_000, 1
            ),
            forced_entity_id=f"sensor.{_dev}_{_m}room_{area_id}_avg_clean_time",
        ),
        RobEyeRoomSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            area_id=area_id,
            unique_suffix=f"{_m}last_cleaned",
            display_name=f"{room_name} Last Cleaned",
            icon="mdi:calendar-clock",
            value_fn=lambda c: _format_date(_stats(c).get("last_cleaned", {})),
            forced_entity_id=f"sensor.{_dev}_{_m}room_{area_id}_last_cleaned",
        ),
    ]



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
        if y <= 2001:  # sentinel value meaning "never cleaned"
            return None
        m = last_cleaned.get("month", 0)
        d = last_cleaned.get("day", 0)
        return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:  # noqa: BLE001
        return None
