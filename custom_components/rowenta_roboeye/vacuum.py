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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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
    SERVICE_REMOVE_QUEUE_ENTRY,
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

REMOVE_QUEUE_ENTRY_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("pending_index", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
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
    platform.async_register_entity_service(
        SERVICE_REMOVE_QUEUE_ENTRY,
        REMOVE_QUEUE_ENTRY_SCHEMA,
        "_async_remove_queue_entry",
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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose error conditions and recharge-mid-clean status."""
        attrs: dict[str, Any] = {}
        if self._error_status is not None:
            attrs["error"] = self._error_status
        if self.coordinator.is_recharging_mid_clean:
            attrs["status"] = "recharging_mid_clean"
            attrs["battery_level"] = self.coordinator.status.get("battery_level")
        return attrs or None

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

        # ── Fix B: Recharge-and-continue (check BEFORE normal cleaning) ──
        elif mode == MODE_CLEANING and charging == CHARGING_CHARGING:
            # Firmware-initiated recharge-and-continue.
            # Robot is docked charging mid-clean session.
            # cmd_id stays "executing" throughout (observed: ~100 min).
            # Confirmed: mode=cleaning+charging=charging is the unique signature.
            # Map to RETURNING as nearest HA standard state.
            self._attr_activity = VacuumActivity.RETURNING
            self._error_status = None

        else:
            self._error_status = None
            if mode == MODE_CLEANING:
                self._attr_activity = VacuumActivity.CLEANING
            elif mode == MODE_GO_HOME:
                self._attr_activity = VacuumActivity.RETURNING
            elif mode == MODE_READY and charging in (CHARGING_CHARGING, CHARGING_CONNECTED):
                self._attr_activity = VacuumActivity.DOCKED
            elif self.coordinator.is_paused or (
                mode == MODE_READY and charging == CHARGING_UNCONNECTED
            ):
                # coordinator.is_paused: stopped via HA pause button (queue drain happened)
                # mode=ready+unconnected: stopped via native app or external source
                self._attr_activity = VacuumActivity.PAUSED
            else:
                self._attr_activity = VacuumActivity.IDLE

        self.async_write_ha_state()

    # ── Standard vacuum services ──────────────────────────────────────

    async def async_start(self, **kwargs: Any) -> None:
        """Start a new clean, resume a paused clean, or recover from error.

        PAUSED  → clean_start_or_continue with saved fan speed.
                  Re-queues any jobs pending when pause was pressed.
        ERROR   → clean_start_or_continue (firmware decides if recoverable:
                  brush stuck → accepts; dustbin missing → rejects, error persists)
        DOCKED / IDLE / other → clean_all (fresh whole-home clean)

        /set/clean_continue is deprecated — never call it.

        Suppressed during recharge-and-continue (Fix B): the firmware will
        automatically resume when charging is sufficient. Sending a new clean
        command at this point would be skipped (action_skipped info=5).
        """
        # ── Fix B: suppress during recharge-and-continue ─────────────
        if self.coordinator.is_recharging_mid_clean:
            LOGGER.info(
                "RobEye: recharge-and-continue in progress — ignoring start command"
            )
            return

        if self._attr_activity in (VacuumActivity.PAUSED, VacuumActivity.ERROR):
            LOGGER.debug(
                "async_start: resume/recover via clean_start_or_continue "
                "(activity=%s is_paused=%s)",
                self._attr_activity, self.coordinator.is_paused,
            )
            # Use fan speed that was active when paused; fall back to current
            fan_speed = (
                self.coordinator.paused_fan_speed
                or self.coordinator.ha_fan_speed
                or FAN_SPEED_REVERSE_MAP.get(self._attr_fan_speed or "normal", "2")
            )
            await self.coordinator.async_send_command(
                self.coordinator.client.clean_start_or_continue,
                label="clean_start_or_continue",
                cleaning_parameter_set=fan_speed,
            )
        else:
            LOGGER.debug("async_start: starting new clean (activity=%s)", self._attr_activity)
            raw = self.coordinator.ha_fan_speed or FAN_SPEED_REVERSE_MAP.get(
                self._attr_fan_speed or "normal", "2"
            )
            await self.coordinator.async_send_command(
                self.coordinator.client.clean_all,
                label="clean_all",
                cleaning_parameter_set=raw,
                strategy_mode=self.coordinator.cleaning_strategy,
            )

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop current job; advance to next queued job if any, otherwise go home.

        Sends stop (priority 0 — immediate). async_send_command(stop) drains any
        pending queue items into _paused_jobs. If there are saved jobs, they are
        re-enqueued so the robot continues with the next task. If the queue was
        empty the robot is sent home instead.
        Use async_pause to stop-in-place with the option to resume the same job.
        """
        LOGGER.debug("async_stop: stop current job")
        await self.coordinator.async_send_command(
            self.coordinator.client.stop,
            label="stop(advance)",
        )
        if self.coordinator._paused_jobs:
            LOGGER.debug("async_stop: pending jobs found — advancing to next")
            await self.coordinator.async_advance_to_next_job()
        else:
            LOGGER.debug("async_stop: no pending jobs — going home")
            await self.coordinator.async_send_command(
                self.coordinator.client.go_home,
                label="go_home",
            )

    async def async_pause(self, **kwargs: Any) -> None:
        """Pause cleaning — stop in place, save pending jobs for resume.

        Coordinator drains the queue into _paused_jobs and sets _is_paused=True.
        Resume (async_start from PAUSED) re-enqueues them after clean_start_or_continue.
        """
        LOGGER.debug("async_pause: stopping in place, saving queue for resume")
        await self.coordinator.async_send_command(
            self.coordinator.client.stop,
            label="stop(pause)",
        )

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Stop any active operation and return to dock.

        Discards paused jobs (go_home path clears _paused_jobs — full stop, no resume).
        If cleaning/paused/error: stop first, then dock.
        If already docked/idle: dock directly.
        """
        LOGGER.debug("async_return_to_base (activity=%s)", self._attr_activity)
        if self._attr_activity in (
            VacuumActivity.CLEANING,
            VacuumActivity.PAUSED,
            VacuumActivity.ERROR,
        ):
            LOGGER.debug("async_return_to_base: stopping before go_home")
            await self.coordinator.async_send_command(
                self.coordinator.client.stop,
                label="stop(return_to_base)",
            )
        await self.coordinator.async_send_command(
            self.coordinator.client.go_home,
            label="go_home",
        )

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Change the fan / suction intensity."""
        LOGGER.debug("async_set_fan_speed to %s", fan_speed)
        raw = FAN_SPEED_REVERSE_MAP.get(fan_speed)
        if raw is None:
            LOGGER.warning("Unknown fan speed: %s", fan_speed)
            return
        self.coordinator.ha_fan_speed = raw
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
            label=f"clean_map(areas={area_ids_str})",
            map_id=map_id,
            area_ids=area_ids_str,
            cleaning_parameter_set=raw,
            strategy_mode=strategy,
        )

    async def _async_remove_queue_entry(self, pending_index: int = 0) -> None:
        """Service handler for rowenta_roboeye.remove_queue_entry."""
        removed = await self.coordinator.async_remove_queued_command(
            pending_index=pending_index
        )
        if not removed:
            LOGGER.warning(
                "remove_queue_entry: no pending queue entry at index %s",
                pending_index,
            )
