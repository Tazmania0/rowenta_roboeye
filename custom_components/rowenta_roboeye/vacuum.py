"""Vacuum entity for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.helpers.entity_platform as ep

from .const import (
    CHARGING_CHARGING,
    CHARGING_CONNECTED,
    CHARGING_UNCONNECTED,
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    MODE_CLEANING,
    MODE_GO_HOME,
    MODE_NOT_READY,
    MODE_READY,
    SERVICE_CLEAN_ROOM,
)
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity

SUPPORTED_FEATURES = (
    VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.START
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.FAN_SPEED
)

# Entity service schema for rowenta_roboeye.clean_room
# Must use make_entity_service_schema — bare vol.Schema is rejected by HA
CLEAN_ROOM_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("room_ids"): vol.All(
            cv.ensure_list, [vol.Coerce(str)]
        ),
        vol.Optional("fan_speed"): vol.In(FAN_SPEEDS),
        vol.Optional("deep_clean", default=False): cv.boolean,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuum entity and register the clean_room service."""
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    vacuum = RobEyeVacuumEntity(coordinator)
    async_add_entities([vacuum])

    platform = ep.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CLEAN_ROOM,
        CLEAN_ROOM_SCHEMA,
        "_async_clean_room",
    )



class RobEyeVacuumEntity(RobEyeEntity, StateVacuumEntity):
    """Representation of the Rowenta Xplorer 120 vacuum cleaner."""

    _attr_supported_features = SUPPORTED_FEATURES
    _attr_fan_speed_list = FAN_SPEEDS
    _attr_name = None  # Device name is used as entity name

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.device_id
        self.entity_id = f"vacuum.{coordinator.device_id}"
        self._error_status: str | None = None
        self._is_paused: bool = False

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the specific error condition when in ERROR state."""
        if self._error_status is not None:
            return {"error": self._error_status}
        return None

    # ── Coordinator update handler ────────────────────────────────────

    @callback
    def _handle_coordinator_update(self) -> None:
        """Process fresh data from the coordinator."""
        status = self.coordinator.status

        # Fan speed
        raw = str(status.get("cleaning_parameter_set", ""))
        self._attr_fan_speed = FAN_SPEED_MAP.get(raw)

        # State machine
        mode = status.get("mode", "")
        charging = status.get("charging", "")
        sv = self.coordinator.sensor_values_parsed

        # Collect specific hardware fault conditions
        _error_conditions: list[str] = []
        if sv.get("gpio__dustbin") == "inactive":
            _error_conditions.append("Dustbin missing")
        if sv.get("gpio__side_brush_left_stuck") == "active":
            _error_conditions.append("Left brush stuck")
        if sv.get("gpio__side_brush_right_stuck") == "active":
            _error_conditions.append("Right brush stuck")

        _hardware_error = bool(_error_conditions) or mode == MODE_NOT_READY

        if _hardware_error:
            self._attr_activity = VacuumActivity.ERROR
            self._error_status = ", ".join(_error_conditions) if _error_conditions else "Not ready"
        else:
            self._error_status = None
            if mode == MODE_CLEANING:
                self._is_paused = False
                self._attr_activity = VacuumActivity.CLEANING
            elif mode == MODE_READY and charging in (CHARGING_CHARGING, CHARGING_CONNECTED):
                self._is_paused = False
                self._attr_activity = VacuumActivity.DOCKED
            elif mode == MODE_READY and charging == CHARGING_UNCONNECTED:
                self._attr_activity = VacuumActivity.IDLE
            elif mode == MODE_GO_HOME:
                self._is_paused = False
                self._attr_activity = VacuumActivity.RETURNING
            else:
                self._attr_activity = VacuumActivity.IDLE

        self.async_write_ha_state()

    # ── Standard vacuum services ──────────────────────────────────────

    async def async_start(self, **kwargs: Any) -> None:
        """Start a full-home clean at the current fan speed."""
        LOGGER.debug("async_start strategy=%s", self.coordinator.cleaning_strategy)
        self._is_paused = False
        raw = FAN_SPEED_REVERSE_MAP.get(self._attr_fan_speed or "normal", "2")
        await self.coordinator.async_send_command(
            self.coordinator.client.clean_all,
            cleaning_parameter_set=raw,
            strategy_mode=self.coordinator.cleaning_strategy,
        )

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum immediately."""
        LOGGER.debug("async_stop")
        self._is_paused = False
        await self.coordinator.async_send_command(self.coordinator.client.stop)

    async def async_pause(self, **kwargs: Any) -> None:
        """Pause the vacuum (no native pause — sends stop and tracks paused state)."""
        LOGGER.debug("async_pause")
        self._is_paused = True
        await self.coordinator.async_send_command(self.coordinator.client.stop)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to its dock."""
        LOGGER.debug("async_return_to_base")
        await self.coordinator.async_send_command(self.coordinator.client.go_home)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Change the fan / suction intensity."""
        LOGGER.debug("async_set_fan_speed to %s", fan_speed)
        raw = FAN_SPEED_REVERSE_MAP.get(fan_speed)
        if raw is None:
            LOGGER.warning("Unknown fan speed: %s", fan_speed)
            return
        await self.coordinator.async_send_command(
            self.coordinator.client.set_fan_speed,
            cleaning_parameter_set=raw,
        )

    # ── Custom service: clean_room ────────────────────────────────────

    async def _async_clean_room(
        self,
        room_ids: list[str],
        fan_speed: str | None = None,
        deep_clean: bool = False,
    ) -> None:
        """Service handler for rowenta_roboeye.clean_room.

        Args:
            room_ids: List of area IDs to clean (strings or ints, joined as comma list).
            fan_speed: Optional fan speed override; uses current speed if omitted.
            deep_clean: When True, forces deep-clean strategy for this run only.
        """
        from .const import STRATEGY_DEEP

        LOGGER.debug("clean_room: room_ids=%s fan_speed=%s deep_clean=%s", room_ids, fan_speed, deep_clean)
        area_ids_str = ",".join(str(r) for r in room_ids)
        map_id: str = self.coordinator.active_map_id

        if fan_speed is not None:
            raw = FAN_SPEED_REVERSE_MAP.get(fan_speed, "2")
        else:
            raw = FAN_SPEED_REVERSE_MAP.get(self._attr_fan_speed or "normal", "2")

        strategy = STRATEGY_DEEP if deep_clean else self.coordinator.cleaning_strategy

        await self.coordinator.async_send_command(
            self.coordinator.client.clean_map,
            map_id=map_id,
            area_ids=area_ids_str,
            cleaning_parameter_set=raw,
            strategy_mode=strategy,
        )
