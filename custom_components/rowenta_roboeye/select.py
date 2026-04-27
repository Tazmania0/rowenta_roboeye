"""Select entities for the Rowenta RobEye integration.

Global select:
  RobEyeCleaningModeSelect  — sets the vacuum's active fan speed

Per-room selects (one per discovered area):
  RobEyeRoomFanSpeedSelect  — stores desired fan speed for that room locally.
  Added dynamically via SIGNAL_AREAS_UPDATED without reload.
"""

from __future__ import annotations

import json

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    AREA_STATE_BLOCKING,
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SIGNAL_AREAS_UPDATED,
    SIGNAL_MAPS_UPDATED,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
    STRATEGY_LABELS,
    STRATEGY_NORMAL,
    STRATEGY_OPTIONS,
    STRATEGY_REVERSE_MAP,
    STRATEGY_WALLS_CORNERS,
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
    entities: list[SelectEntity] = [
        RobEyeCleaningModeSelect(coordinator),
        RobEyeStrategySelect(coordinator),
        RobEyeActiveMapSelect(coordinator),
    ]

    # Per-map entity tracking: map_id -> {area_id -> [entities]}
    # Entities for inactive maps stay registered but show as unavailable.
    # This avoids the async_remove/async_add race that caused duplicate-ID errors
    # when switching maps: we never remove+recreate entities for map switches.
    known_entities_by_map: dict[str, dict] = {}

    _active = coordinator.active_map_id
    if coordinator.areas_map_id == _active:
        # Purge registry entries for areas that no longer exist on the active map.
        current_area_ids: set = {
            a.get("id")
            for a in coordinator.areas
            if a.get("id") is not None and _parse_select_area_name(a)
        }
        async_remove_stale_room_entities(
            hass, config_entry, coordinator, "select", current_area_ids
        )

        initial_selects, initial_by_area = _build_room_select_entities(
            coordinator, config_entry, coordinator.areas, set()
        )
        if initial_by_area:
            known_entities_by_map[_active] = initial_by_area
        entities.extend(initial_selects)

    entities.extend(
        _register_stub_room_selects_from_registry(
            hass, config_entry, coordinator, known_entities_by_map
        )
    )

    async_add_entities(entities)

    @callback
    def _async_on_areas_updated() -> None:
        if coordinator.areas_map_id != coordinator.active_map_id:
            LOGGER.debug("select: areas fetched for wrong map, skipping update")
            return

        active_map = coordinator.active_map_id
        map_entities = known_entities_by_map.setdefault(active_map, {})

        current_ids: set = {
            str(area_id)
            for area in coordinator.areas
            if (area_id := area.get("id")) is not None
            and _parse_select_area_name(area)
        }

        stale_ids = set(map_entities.keys()) - current_ids
        for area_id in stale_ids:
            for entity in map_entities.pop(area_id):
                LOGGER.debug("select: removing deleted-area select area_id=%s", area_id)
                if entity.registry_entry:
                    from homeassistant.helpers import entity_registry as er
                    er.async_get(hass).async_remove(entity.entity_id)
                else:
                    hass.async_create_task(entity.async_remove())

        new_entities, new_by_area = _build_room_select_entities(
            coordinator, config_entry, coordinator.areas, set(map_entities.keys())
        )
        if new_entities:
            LOGGER.debug("select: adding %d new room selects", len(new_entities))
            map_entities.update(new_by_area)
            async_add_entities(new_entities)

    @callback
    def _async_on_maps_updated(deleted_map_ids: set[str]) -> None:
        removed = async_remove_entities_for_deleted_maps(
            hass, config_entry, "select", deleted_map_ids
        )
        for map_id, area_id in removed:
            known_entities_by_map.get(map_id, {}).pop(area_id, None)
        for map_id in deleted_map_ids:
            known_entities_by_map.pop(map_id, None)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _async_on_areas_updated,
        )
    )
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_MAPS_UPDATED}_{config_entry.entry_id}",
            _async_on_maps_updated,
        )
    )


def _parse_select_area_name(area: dict) -> str:
    """Return the room name from area_meta_data, or empty string."""
    meta_raw = area.get("area_meta_data", "")
    if not meta_raw:
        return ""
    try:
        meta = json.loads(meta_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    return meta.get("name", "").strip()


_ROOM_SELECT_NAME_SUFFIXES = (" Fan Speed", " Strategy")


def _register_stub_room_selects_from_registry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    known_entities_by_map: dict[str, dict],
) -> list:
    """Re-claim select registry entries belonging to inactive maps."""
    records = find_room_registry_records(hass, config_entry, "select")
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
            pick_room_name_from_records(recs, _ROOM_SELECT_NAME_SUFFIXES)
            or f"Room {area_id}"
        )
        pair = [
            RobEyeRoomFanSpeedSelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
                map_id=map_id,
            ),
            RobEyeRoomStrategySelect(
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
            "select: re-claiming %d stub selects for inactive maps from registry",
            len(stubs),
        )
    return stubs


def _build_room_select_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    areas: list,
    already_known: set,
) -> tuple[list, dict]:
    new_entities = []
    by_area: dict = {}
    _map = coordinator.active_map_id
    # Guard: skip if areas data was fetched for a different map (stale-signal race).
    if coordinator.areas_map_id != _map:
        return new_entities, by_area
    for area in areas:
        area_id = area.get("id")
        if area_id is None:
            continue
        area_id = str(area_id)
        if area_id in already_known:
            continue
        room_name = _parse_select_area_name(area)
        if not room_name:
            continue
        # Skip areas disabled for cleaning in the RobEye app
        if area.get("area_state") == AREA_STATE_BLOCKING:
            continue
        entities_for_area = [
            RobEyeRoomFanSpeedSelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
            ),
            RobEyeRoomStrategySelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
            ),
        ]
        new_entities.extend(entities_for_area)
        by_area[area_id] = entities_for_area
    return new_entities, by_area


# ── Global fan speed select ───────────────────────────────────────────

class RobEyeCleaningModeSelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Controls the vacuum's active fan speed."""

    _attr_translation_key = "cleaning_mode"
    _attr_icon = "mdi:speedometer"
    _attr_options = FAN_SPEEDS

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"cleaning_mode_{coordinator.device_id}"
        self.entity_id = f"select.{coordinator.device_id}_cleaning_mode"
        self._last_known: str | None = None

    @property
    def current_option(self) -> str | None:
        # Once HA has a stored preference (_last_known), always return it.
        # The device's reported fan speed is only used to populate the initial
        # value on first setup (when no prior HA state exists).
        if self._last_known is not None:
            return self._last_known
        raw = str(self.coordinator.status.get("cleaning_parameter_set", ""))
        live = FAN_SPEED_MAP.get(raw)
        if live is not None:
            self._last_known = live
            self.coordinator.ha_fan_speed = raw
        return live

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in FAN_SPEEDS:
                self._last_known = last_state.state
                raw = FAN_SPEED_REVERSE_MAP.get(last_state.state)
                if raw:
                    self.coordinator.ha_fan_speed = raw

    async def async_select_option(self, option: str) -> None:
        raw = FAN_SPEED_REVERSE_MAP.get(option)
        if raw is None:
            LOGGER.warning("Unknown cleaning mode: %s", option)
            return
        self._last_known = option
        self.coordinator.ha_fan_speed = raw
        await self.coordinator.async_send_command(
            self.coordinator.client.set_fan_speed,
            cleaning_parameter_set=raw,
        )


# ── Per-room fan speed select ─────────────────────────────────────────

class RobEyeRoomFanSpeedSelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Per-room fan speed select.

    Bidirectional sync: changes are written to the robot immediately via
    modify_area; the coordinator's 300 s areas poll reads the robot's stored
    value back, so external changes made in the native app are reflected in HA.

    _last_robot_raw guards against mid-cycle overwrites: the entity only updates
    _selected when the robot's reported value actually changes, so a user's
    just-made HA choice is never clobbered by the stale pre-poll cache.
    """

    _attr_icon = "mdi:speedometer"
    _attr_options = FAN_SPEEDS

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
        self._room_name = room_name
        _map = map_id if map_id is not None else coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_fan_speed_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"{room_name} Fan Speed"
        self.entity_id = f"select.{coordinator.device_id}_map{_map}_room_{area_id}_fan_speed"
        self._selected: str = "normal"
        self._last_robot_raw: str | None = None  # last cleaning_parameter_set read from robot

    @property
    def available(self) -> bool:
        return self.coordinator.map_available_for(self._map_id) and super().available

    @property
    def current_option(self) -> str:
        return self._selected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in FAN_SPEEDS:
                self._selected = last_state.state
                # Only record the robot's current value as baseline when it
                # matches the restored HA state.  If they differ (native app
                # changed the setting while HA was offline), leave
                # _last_robot_raw as None so _handle_coordinator_update syncs
                # from the robot on the first poll.
                for area in self.coordinator.areas:
                    if str(area.get("id", "")) == self._area_id:
                        raw = area.get("cleaning_parameter_set")
                        if raw is not None and FAN_SPEED_MAP.get(str(raw)) == last_state.state:
                            self._last_robot_raw = str(raw)
                        break
                return
        # Seed from robot's stored value on first run (no prior HA state).
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                raw = area.get("cleaning_parameter_set")
                if raw is not None and str(raw) in FAN_SPEED_MAP:
                    self._last_robot_raw = str(raw)
                    self._selected = FAN_SPEED_MAP[str(raw)]
                    LOGGER.debug(
                        "Room %s fan speed seeded from robot: %s",
                        self._area_id, self._selected,
                    )
                break

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync fan speed from robot on every areas refresh.

        coordinator.areas is re-fetched every 300 s. Between refreshes the cache
        holds the same data, so _last_robot_raw stays equal to the cached value
        and this method is a no-op — meaning a user's just-made HA selection is
        never overwritten by the stale pre-poll cache.
        Once the areas poll returns a changed value (e.g. the native app set a
        different speed), _last_robot_raw differs → _selected is updated.
        """
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                raw = area.get("cleaning_parameter_set")
                raw_str = str(raw) if raw is not None else None
                if raw_str is not None and raw_str != self._last_robot_raw and raw_str in FAN_SPEED_MAP:
                    self._last_robot_raw = raw_str
                    self._selected = FAN_SPEED_MAP[raw_str]
                    LOGGER.debug(
                        "Room %s fan speed updated from robot: %s",
                        self._area_id, self._selected,
                    )
                break
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        if option not in FAN_SPEEDS:
            LOGGER.warning("Unknown fan speed for room %s: %s", self._room_name, option)
            return
        self._selected = option
        self.async_write_ha_state()
        # Persist the new fan speed to the robot's saved map immediately.
        # Always include strategy_mode so the firmware doesn't reset it to a
        # default value when only cleaning_parameter_set is provided.
        raw = FAN_SPEED_REVERSE_MAP.get(option)
        if raw:
            # Read strategy from the per-room deep-clean switch's current HA state
            # (optimistically updated) rather than stale coordinator.areas cache.
            switch_eid = (
                f"switch.{self.coordinator.device_id}"
                f"_map{self._map_id}_room_{self._area_id}_deep_clean"
            )
            switch_state = self.coordinator.hass.states.get(switch_eid)
            if switch_state is not None and switch_state.state == "on":
                current_strategy = "deep"
            else:
                current_strategy = "normal"
            await self.coordinator.async_send_command(
                self.coordinator.client.modify_area,
                map_id=self.coordinator.active_map_id,
                area_id=self._area_id,
                cleaning_parameter_set=raw,
                strategy_mode=current_strategy,
            )


# ── Active map select ─────────────────────────────────────────────────

class RobEyeActiveMapSelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Selects the floor map whose areas and geometry the integration loads."""

    _attr_translation_key = "active_map"
    _attr_icon = "mdi:map"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"active_map_{coordinator.device_id}"
        self.entity_id = f"select.{coordinator.device_id}_active_map"
        self._name_to_id: dict[str, str] = {}

    def _build_options(self) -> list[str]:
        maps = self.coordinator.available_maps
        self._name_to_id = {m["display_name"]: m["map_id"] for m in maps}
        opts = list(self._name_to_id.keys())
        return opts if opts else [self.coordinator.active_map_id]

    @property
    def options(self) -> list[str]:
        return self._build_options()

    @property
    def current_option(self) -> str | None:
        # Always rebuild _name_to_id here: HA calls current_option before options
        # in state assembly, so _name_to_id could be stale and cause a flicker.
        self._build_options()
        active_id = self.coordinator.active_map_id
        for name, map_id in self._name_to_id.items():
            if map_id == active_id:
                return name
        return active_id or None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._build_options()  # populate _name_to_id before reading last state
        if (last_state := await self.async_get_last_state()) is not None:
            state = last_state.state
            if state not in ("unknown", "unavailable"):
                map_id = self._name_to_id.get(state, state)
                if map_id != self.coordinator.active_map_id:
                    # The restored map differs from the one used during the first
                    # coordinator refresh (which ran before any entity was loaded).
                    # Force a full map switch now so areas, geometry, and live map
                    # are fetched for the correct map immediately rather than after
                    # the next 300-s / 600-s polling cycle.
                    LOGGER.debug(
                        "Active map restored to %s (was %s) — forcing immediate switch",
                        map_id,
                        self.coordinator.active_map_id,
                    )
                    await self.coordinator.async_set_active_map(map_id)
                else:
                    self.coordinator._manual_map_id = map_id

    async def async_select_option(self, option: str) -> None:
        map_id = self._name_to_id.get(option, option)
        # Optimistically write the new map to the coordinator and push state to
        # HA immediately.  This eliminates the 1-2 s "flicker" where the selector
        # briefly reverts to the previous map while waiting for the next
        # coordinator refresh cycle to propagate _manual_map_id.
        self.coordinator._manual_map_id = map_id
        self.async_write_ha_state()
        await self.coordinator.async_set_active_map(map_id)


# ── Cleaning strategy select ──────────────────────────────────────────

class RobEyeStrategySelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Four-mode cleaning strategy selector.

    Options (confirmed from RobEye web UI HTML source, 2026-03-30):
      Default       (4) — robot chooses strategy automatically
      Normal        (1) — single-pass clean
      Walls & Corners (2) — focus on edges
      Deep          (3) — double/triple pass

    Syncs with the global deep-clean switch: turning the switch ON sets this
    select to "Deep"; the switch reflects "on" whenever this select is "Deep".
    """

    _attr_translation_key = "cleaning_strategy"
    _attr_icon = "mdi:layers-triple"
    _attr_options = STRATEGY_OPTIONS

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"cleaning_strategy_{coordinator.device_id}"
        self.entity_id = f"select.{coordinator.device_id}_cleaning_strategy"

    @property
    def current_option(self) -> str:
        strategy = self.coordinator.cleaning_strategy
        if strategy == STRATEGY_DEEP:
            # Deep is driven by the switch; show the last explicitly chosen non-deep option
            return STRATEGY_LABELS.get(
                self.coordinator.last_non_deep_strategy,
                STRATEGY_LABELS[STRATEGY_DEFAULT],
            )
        return STRATEGY_LABELS.get(strategy, STRATEGY_LABELS[STRATEGY_DEFAULT])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            api_val = STRATEGY_REVERSE_MAP.get(last_state.state)
            if api_val is not None and api_val != STRATEGY_DEEP:
                # Restore both the active strategy and the pre-deep bookmark so
                # turning the deep-clean switch off returns here, not to Default.
                self.coordinator.cleaning_strategy = api_val
                self.coordinator.last_non_deep_strategy = api_val

    async def async_select_option(self, option: str) -> None:
        api_val = STRATEGY_REVERSE_MAP.get(option)
        if api_val is None:
            LOGGER.warning("Unknown cleaning strategy option: %s", option)
            return
        self.coordinator.cleaning_strategy = api_val
        self.coordinator.last_non_deep_strategy = api_val
        self.async_write_ha_state()


# ── Per-room strategy select ──────────────────────────────────────────

class RobEyeRoomStrategySelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Stores the desired cleaning strategy for a single room (local only).

    Options are Default, Normal, and Walls & Corners. Deep strategy is not
    offered here; use the per-room deep clean switch for that.
    When the room deep clean switch is ON it takes precedence over this select.
    Entity is unavailable while its map is not the active map.
    """

    _attr_icon = "mdi:layers-triple"
    _attr_options = STRATEGY_OPTIONS

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
        self._room_name = room_name
        _map = map_id if map_id is not None else coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_strategy_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"{room_name} Strategy"
        self.entity_id = f"select.{coordinator.device_id}_map{_map}_room_{area_id}_strategy"
        self._selected: str = STRATEGY_LABELS[STRATEGY_DEFAULT]

    @property
    def available(self) -> bool:
        return self.coordinator.map_available_for(self._map_id) and super().available

    @property
    def current_option(self) -> str:
        return self._selected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in STRATEGY_OPTIONS:
                self._selected = last_state.state
                return
        # Seed from robot's stored strategy_mode on first run.
        # "deep" is owned by the per-room deep-clean switch — not shown here.
        _robot_to_label: dict[str, str] = {
            "normal":        STRATEGY_LABELS[STRATEGY_NORMAL],
            "walls_corners": STRATEGY_LABELS[STRATEGY_WALLS_CORNERS],
        }
        for area in self.coordinator.areas:
            if str(area.get("id", "")) == self._area_id:
                robot_val = str(area.get("strategy_mode", "")).lower()
                label = _robot_to_label.get(robot_val)
                if label is not None:
                    self._selected = label
                    LOGGER.debug(
                        "Room %s strategy seeded from robot: %s",
                        self._area_id, self._selected,
                    )
                # "deep" and unknown values → keep default label
                break

    async def async_select_option(self, option: str) -> None:
        if option not in STRATEGY_OPTIONS:
            LOGGER.warning("Unknown strategy for room %s: %s", self._room_name, option)
            return
        self._selected = option
        self.async_write_ha_state()
        # Robot only accepts "normal" or "deep" for strategy_mode.
        # All three non-deep options (Default, Normal, Walls & Corners) → "normal".
        # Always include cleaning_parameter_set so the firmware doesn't reset it.
        fan_eid = (
            f"select.{self.coordinator.device_id}"
            f"_map{self._map_id}_room_{self._area_id}_fan_speed"
        )
        fan_state = self.coordinator.hass.states.get(fan_eid)
        if fan_state is not None and fan_state.state in FAN_SPEEDS:
            current_cps = FAN_SPEED_REVERSE_MAP.get(fan_state.state, "1")
        else:
            current_cps = "1"
            for area in self.coordinator.areas:
                if str(area.get("id", "")) == self._area_id:
                    cps = area.get("cleaning_parameter_set")
                    if cps is not None:
                        current_cps = str(cps)
                    break
        await self.coordinator.async_send_command(
            self.coordinator.client.modify_area,
            map_id=self.coordinator.active_map_id,
            area_id=self._area_id,
            cleaning_parameter_set=current_cps,
            strategy_mode="normal",
        )
