"""Switch entities for the Rowenta Xplorer 120.

RobEyeDeepCleanSwitch
  Global deep clean toggle. When ON, sets coordinator.cleaning_strategy to
  STRATEGY_DEEP (mode 3 = double/triple pass). When OFF, resets to
  STRATEGY_DEFAULT (mode 4 = robot decides). Stored locally, no API call.
  Kept for backwards compatibility; the strategy select exposes all four modes.

RobEyeRoomDeepCleanSwitch
  Per-room deep clean toggle. When ON, that room's clean uses STRATEGY_DEEP
  regardless of the global strategy. Falls back to coordinator.cleaning_strategy
  when OFF.

RobEyeScheduleSwitch
  Per-schedule enable/disable toggle. Writes via /set/modify_scheduled_task —
  bypasses asyncio.Queue (settings write, not a motion command).
"""

from __future__ import annotations

import json as _json
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    AREA_STATE_BLOCKING,
    CLEANING_MODE_ALL,
    DOMAIN,
    FAN_SPEED_LABELS,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SCHEDULE_DAYS,
    SIGNAL_AREAS_UPDATED,
    SIGNAL_MAPS_UPDATED,
    SIGNAL_ROOM_SELECTION_CHANGED,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
    room_selection_entity_id,
)
from .coordinator import RobEyeCoordinator
from .entity import (
    RobEyeEntity,
    async_remove_entities_for_deleted_maps,
    async_remove_stale_room_entities,
    find_room_registry_records,
    pick_room_name_from_records,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list = [RobEyeDeepCleanSwitch(coordinator)]
    # Per-map entity tracking: map_id -> {area_id -> [entities]}
    known_entities_by_map: dict[str, dict] = {}
    known_task_ids: set[int] = set()

    def _parse_switch_area_name(area: dict) -> str:
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            return ""
        try:
            meta = _json.loads(meta_raw)
        except Exception:
            return ""
        return meta.get("name", "").strip()

    def _room_switches(areas: list, already_known: set) -> tuple[list, dict]:
        new_entities: list = []
        by_area: dict = {}
        _map = coordinator.active_map_id
        if coordinator.areas_map_id != _map:
            return new_entities, by_area
        for area in areas:
            area_id = area.get("id")
            if area_id is None:
                continue
            area_id = str(area_id)
            if area_id in already_known:
                continue
            room_name = _parse_switch_area_name(area)
            if not room_name:
                continue
            if area.get("area_state") == AREA_STATE_BLOCKING:
                continue
            entities_for_area = [
                RobEyeRoomDeepCleanSwitch(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    area_id=area_id,
                    room_name=room_name,
                ),
                RobEyeRoomSelectSwitch(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    area_id=area_id,
                    room_name=room_name,
                ),
            ]
            new_entities.extend(entities_for_area)
            by_area[area_id] = entities_for_area
        return new_entities, by_area

    def _schedule_switches(already_known: set[int]) -> tuple[list, set[int]]:
        new_entities: list = []
        new_ids: set[int] = set()
        for item in coordinator.schedule.get("schedule", []):
            if not isinstance(item, dict):
                continue
            task_id = item.get("task_id")
            if task_id is None or int(task_id) in already_known:
                continue
            new_entities.append(RobEyeScheduleSwitch(coordinator, int(task_id)))
            new_ids.add(int(task_id))
        return new_entities, new_ids

    _active = coordinator.active_map_id
    if coordinator.areas_map_id == _active:
        # Purge registry entries for areas that no longer exist on the active map.
        current_area_ids: set = {
            a.get("id")
            for a in coordinator.areas
            if a.get("id") is not None and _parse_switch_area_name(a)
        }
        async_remove_stale_room_entities(
            hass, config_entry, coordinator, "switch", current_area_ids
        )

        initial_switches, initial_by_area = _room_switches(coordinator.areas, set())
        if initial_by_area:
            known_entities_by_map[_active] = initial_by_area
        entities.extend(initial_switches)

    entities.extend(
        _register_stub_room_switches_from_registry(
            hass, config_entry, coordinator, known_entities_by_map
        )
    )

    schedule_switches, new_task_ids = _schedule_switches(known_task_ids)
    entities.extend(schedule_switches)
    known_task_ids.update(new_task_ids)

    async_add_entities(entities)

    @callback
    def _on_areas_updated() -> None:
        if coordinator.areas_map_id != coordinator.active_map_id:
            LOGGER.debug("switch: areas fetched for wrong map, skipping update")
            return

        active_map = coordinator.active_map_id
        map_entities = known_entities_by_map.setdefault(active_map, {})

        current_ids: set = {
            str(area_id)
            for area in coordinator.areas
            if (area_id := area.get("id")) is not None
            and _parse_switch_area_name(area)
        }

        stale_ids = set(map_entities.keys()) - current_ids
        for area_id in stale_ids:
            for entity in map_entities.pop(area_id):
                LOGGER.debug("switch: removing deleted-area switch area_id=%s", area_id)
                if entity.registry_entry:
                    from homeassistant.helpers import entity_registry as er
                    er.async_get(hass).async_remove(entity.entity_id)
                else:
                    hass.async_create_task(entity.async_remove())

        new_entities, new_by_area = _room_switches(coordinator.areas, set(map_entities.keys()))
        if new_entities:
            map_entities.update(new_by_area)
            async_add_entities(new_entities)

    @callback
    def _on_coordinator_updated() -> None:
        new_entities, new_task_ids = _schedule_switches(known_task_ids)
        if new_entities:
            async_add_entities(new_entities)
            known_task_ids.update(new_task_ids)

    @callback
    def _on_maps_updated(deleted_map_ids: set[str]) -> None:
        removed = async_remove_entities_for_deleted_maps(
            hass, config_entry, "switch", deleted_map_ids
        )
        for map_id, area_id in removed:
            known_entities_by_map.get(map_id, {}).pop(area_id, None)
        for map_id in deleted_map_ids:
            known_entities_by_map.pop(map_id, None)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _on_areas_updated,
        )
    )
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_MAPS_UPDATED}_{config_entry.entry_id}",
            _on_maps_updated,
        )
    )
    config_entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_updated))


_ROOM_SWITCH_NAME_SUFFIXES = (" Deep Clean", " Selected")


def _register_stub_room_switches_from_registry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    known_entities_by_map: dict[str, dict],
) -> list:
    """Re-claim per-room switch registry entries belonging to inactive maps.

    Schedule switches have a different unique_id format and are skipped by
    ``find_room_registry_records``' regex.
    """
    records = find_room_registry_records(hass, config_entry, "switch")
    if not records:
        return []

    by_room: dict[tuple[str, str], list] = {}
    for rec in records:
        if rec.area_id in known_entities_by_map.get(rec.map_id, {}):
            continue
        by_room.setdefault((rec.map_id, rec.area_id), []).append(rec)

    stubs: list = []
    for (map_id, area_id), recs in by_room.items():
        room_name = (
            pick_room_name_from_records(recs, _ROOM_SWITCH_NAME_SUFFIXES)
            or f"Room {area_id}"
        )
        pair = [
            RobEyeRoomDeepCleanSwitch(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
                map_id=map_id,
            ),
            RobEyeRoomSelectSwitch(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
                map_id=map_id,
            ),
        ]
        stubs.extend(pair)
        known_entities_by_map.setdefault(map_id, {})[area_id] = pair

    if stubs:
        LOGGER.debug(
            "switch: re-claiming %d stub switches for inactive maps from registry",
            len(stubs),
        )
    return stubs


class RobEyeDeepCleanSwitch(RobEyeEntity, SwitchEntity, RestoreEntity):
    """Global toggle: ON = STRATEGY_DEEP, OFF = restore prior non-deep strategy.

    Kept for backwards compatibility. The strategy select entity exposes all
    four modes; this switch is the simpler ON/OFF facade over it.
    """

    _attr_translation_key = "deep_clean_mode"
    _attr_icon = "mdi:robot-vacuum-variant"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "deep_clean_mode_" + coordinator.device_id
        self.entity_id = f"switch.{coordinator.device_id}_deep_clean_mode"

    @property
    def is_on(self) -> bool:
        return self.coordinator.cleaning_strategy == STRATEGY_DEEP

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state == "on":
                self.coordinator.cleaning_strategy = STRATEGY_DEEP
            LOGGER.debug("Deep clean mode restored: %s", last.state)

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        self.coordinator.cleaning_strategy = STRATEGY_DEEP
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        # Restore the strategy that was active before deep clean was enabled,
        # not STRATEGY_DEFAULT — the user's prior choice must be preserved.
        self.coordinator.cleaning_strategy = self.coordinator.last_non_deep_strategy
        self.async_write_ha_state()


class RobEyeRoomDeepCleanSwitch(RobEyeEntity, SwitchEntity, RestoreEntity):
    """Per-room deep clean toggle.

    Bidirectional sync: toggling the switch writes strategy_mode to the robot
    immediately via modify_area ("deep" when ON, "normal" when OFF).  The
    coordinator's 300 s areas poll reads the robot's stored strategy_mode back,
    so changes made in the native app are reflected in HA.

    Only "deep" is synced from the robot — when the robot reports "normal" the
    switch turns OFF if it was ON, but the strategy select is never touched
    (non-deep mode granularity is HA-only; the robot cannot store it).

    _last_robot_strategy guards against mid-cycle overwrites using the same
    pattern as RobEyeRoomFanSpeedSelect._last_robot_raw.

    entity_id is forced to switch.{device_id}_map{map_id}_room_{area_id}_deep_clean
    so the dashboard can reference it regardless of room name language.
    Display name uses the actual (possibly Cyrillic) room name.
    """

    _attr_icon = "mdi:robot-vacuum-variant"

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        config_entry: ConfigEntry,
        area_id: str,
        room_name: str,
        map_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        _map = map_id if map_id is not None else coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_deep_clean_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = room_name + " Deep Clean"
        self.entity_id = f"switch.{coordinator.device_id}_map{_map}_room_{area_id}_deep_clean"
        self._is_on: bool = False
        self._last_robot_strategy: str | None = None  # last strategy_mode read from robot

    @property
    def available(self) -> bool:
        return self.coordinator.map_available_for(self._map_id) and super().available

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._is_on = last.state == "on"
            # Record the robot's current strategy as baseline so
            # _handle_coordinator_update knows not to overwrite the restored
            # state until the robot actually changes.
            for area in self.coordinator.areas:
                if str(area.get("id", "")) == self._area_id:
                    self._last_robot_strategy = str(area.get("strategy_mode", "") or "")
                    break
            return
        # Seed from robot's stored strategy_mode on first run (no prior HA state).
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                robot_val = str(area.get("strategy_mode", "") or "").lower()
                self._last_robot_strategy = robot_val
                self._is_on = robot_val == "deep"
                if self._is_on:
                    LOGGER.debug("Room %s deep clean seeded ON from robot", self._area_id)
                break

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync deep-clean state from robot on every areas refresh.

        Only "deep" is acted on — when the robot reports "deep" and the switch
        is currently OFF, the switch is turned ON.  When the robot reports
        "normal" the switch is never touched: all non-deep strategies read back
        as "normal" from the robot, so we cannot distinguish "native app turned
        off deep" from "was always non-deep".  The HA switch is the sole
        authority for turning deep clean OFF.

        _last_robot_strategy prevents redundant work between polls.
        """
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                robot_val = str(area.get("strategy_mode", "") or "").lower()
                if robot_val != self._last_robot_strategy:
                    self._last_robot_strategy = robot_val
                    if robot_val == "deep" and not self._is_on:
                        self._is_on = True
                        LOGGER.debug(
                            "Room %s deep clean synced ON from robot",
                            self._area_id,
                        )
                    # "normal" → leave _is_on unchanged
                break
        self.async_write_ha_state()

    def _current_room_fan_speed_raw(self) -> str:
        """Return the current raw cleaning_parameter_set for this room.

        Reads from the per-room fan-speed select's HA state (optimistic, up to date)
        and falls back to coordinator.areas (may be stale by up to 300 s).
        Always including cleaning_parameter_set prevents the firmware from resetting
        it when only strategy_mode is supplied in a partial modify_area call.
        """
        fan_eid = (
            f"select.{self.coordinator.device_id}"
            f"_map{self._map_id}_room_{self._area_id}_fan_speed"
        )
        fan_state = self.coordinator.hass.states.get(fan_eid)
        if fan_state is not None and fan_state.state in FAN_SPEEDS:
            return FAN_SPEED_REVERSE_MAP.get(fan_state.state, "1")
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                cps = area.get("cleaning_parameter_set")
                if cps is not None:
                    return str(cps)
                break
        return "1"

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = True
        self.async_write_ha_state()
        # Always include cleaning_parameter_set so the firmware doesn't reset it.
        await self.coordinator.async_send_command(
            self.coordinator.client.modify_area,
            map_id=self.coordinator.active_map_id,
            area_id=self._area_id,
            cleaning_parameter_set=self._current_room_fan_speed_raw(),
            strategy_mode="deep",
        )

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = False
        self.async_write_ha_state()
        # "normal" is the only non-deep value the robot accepts.
        # The strategy select holds the granular choice (Default/Normal/Walls&Corners).
        # Always include cleaning_parameter_set so the firmware doesn't reset it.
        await self.coordinator.async_send_command(
            self.coordinator.client.modify_area,
            map_id=self.coordinator.active_map_id,
            area_id=self._area_id,
            cleaning_parameter_set=self._current_room_fan_speed_raw(),
            strategy_mode="normal",
        )


class RobEyeRoomSelectSwitch(RobEyeEntity, SwitchEntity, RestoreEntity):
    """Per-room selection toggle for multi-room cleaning.

    Pure HA-side state — does NOT write to the robot.
    Toggled by the user in the dashboard to build a room selection set.
    Read by RobEyeCleanSelectedButton to determine which rooms to clean.
    Reset to off automatically after Clean Selected is pressed.

    Becomes unavailable when the active map changes (the selection is
    map-scoped, so stale selections from a different map must not be read).

    entity_id: switch.{device_id}_map{map_id}_room_{area_id}_selected
    """

    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        config_entry: ConfigEntry,
        area_id: str,
        room_name: str,
        map_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        _map = map_id if map_id is not None else coordinator.active_map_id
        self._map_id = _map
        self._entry_id = config_entry.entry_id
        self._attr_unique_id = (
            f"room_selected_map{_map}_{area_id}_{coordinator.device_id}"
        )
        self._attr_name = f"{room_name} Selected"
        self.entity_id = room_selection_entity_id(coordinator.device_id, _map, area_id)
        self._is_on: bool = False

    @property
    def available(self) -> bool:
        return self.coordinator.map_available_for(self._map_id) and super().available

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = True
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_SELECTION_CHANGED}_{self._entry_id}"
        )

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = False
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_SELECTION_CHANGED}_{self._entry_id}"
        )


class RobEyeScheduleSwitch(RobEyeEntity, SwitchEntity):
    """Toggle to enable/disable a cleaning schedule task.

    Writes via /set/modify_scheduled_task — bypasses asyncio.Queue.
    Same pattern as modify_area and set_fan_speed: settings write, not a motion
    command, so queuing behind active clean jobs would make the toggle unresponsive.
    """

    def __init__(self, coordinator: RobEyeCoordinator, task_id: int) -> None:
        super().__init__(coordinator)
        self._task_id = task_id
        self._attr_unique_id = f"schedule_{task_id}_{coordinator.device_id}"
        self.entity_id = f"switch.{coordinator.device_id}_schedule_{task_id}"
        # Optimistic state held between toggle and robot confirmation to prevent
        # the switch bouncing back when the coordinator refreshes before the robot
        # has processed the /set/modify_scheduled_task command.
        self._optimistic_enabled: bool | None = None

    def _get_entry(self) -> dict | None:
        for item in self.coordinator.schedule.get("schedule", []):
            if isinstance(item, dict) and item.get("task_id") == self._task_id:
                return item
        return None

    def _area_name(self, area_id: int) -> str | None:
        for area in self.coordinator.areas:
            if area.get("id") == area_id:
                raw = area.get("area_meta_data", "")
                if raw:
                    try:
                        return _json.loads(raw).get("name", "").strip() or None
                    except Exception:
                        pass
        return None

    def _build_label(self, entry: dict) -> str:
        t = entry.get("time", {})
        task = entry.get("task", {})
        days_str = "/".join(
            SCHEDULE_DAYS.get(d, str(d)) for d in sorted(t.get("days_of_week", []))
        )
        time_str = f"{t.get('hour', 0):02d}:{t.get('min', 0):02d}"
        if int(task.get("cleaning_mode", CLEANING_MODE_ALL)) == CLEANING_MODE_ALL:
            rooms_str = "All rooms"
        else:
            area_ids = task.get("parameters", [])
            rooms_str = " + ".join(
                self._area_name(int(a)) or str(a) for a in area_ids
            ) or "Rooms"
        return f"{days_str} {time_str} — {rooms_str}"

    @property
    def name(self) -> str:
        entry = self._get_entry()
        if entry is None:
            return f"Schedule {self._task_id}"
        return f"Schedule: {self._build_label(entry)}"

    @property
    def icon(self) -> str:
        return "mdi:calendar-clock" if self.is_on else "mdi:calendar-remove"

    @property
    def is_on(self) -> bool:
        if self._optimistic_enabled is not None:
            return self._optimistic_enabled
        entry = self._get_entry()
        return bool(int(entry.get("enabled", 0))) if entry else False

    @callback
    def _handle_coordinator_update(self) -> None:
        # Once the robot reports the expected enabled value, drop the optimistic
        # override so future coordinator updates drive the displayed state normally.
        if self._optimistic_enabled is not None:
            entry = self._get_entry()
            if entry is not None and bool(int(entry.get("enabled", 0))) == self._optimistic_enabled:
                self._optimistic_enabled = None
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entry = self._get_entry()
        if entry is None:
            return {}
        t = entry.get("time", {})
        task = entry.get("task", {})
        fan_raw = int(task.get("cleaning_parameter_set", 0))
        return {
            "task_id": self._task_id,
            "days": [SCHEDULE_DAYS.get(d, str(d)) for d in sorted(t.get("days_of_week", []))],
            "time": f"{t.get('hour', 0):02d}:{t.get('min', 0):02d}",
            "cleaning_mode": task.get("cleaning_mode"),
            "area_ids": task.get("parameters", []),
            "fan_speed": FAN_SPEED_LABELS.get(fan_raw, str(fan_raw)),
            "fan_raw": fan_raw,
            "map_id": task.get("map_id"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        self._optimistic_enabled = enabled
        self.async_write_ha_state()
        try:
            await self.coordinator.client.set_schedule_enabled(self._task_id, enabled)
        except Exception as err:  # noqa: BLE001
            LOGGER.error(
                "Failed to %s schedule %s: %s",
                "enable" if enabled else "disable",
                self._task_id,
                err,
            )
            self._optimistic_enabled = None
            self.async_write_ha_state()
            return
        self.coordinator.invalidate_schedule_cache()
        await self.coordinator.async_request_refresh()
