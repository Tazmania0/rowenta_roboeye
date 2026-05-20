"""Button entities for the Rowenta Xplorer 120.

Dynamic room clean buttons are added without reload via SIGNAL_AREAS_UPDATED.
"""

from __future__ import annotations

import json

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AREA_STATES_SKIP,
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SIGNAL_AREAS_UPDATED,
    SIGNAL_MAPS_UPDATED,
    SIGNAL_ROOM_SELECTION_CHANGED,
    STRATEGY_DEEP,
    STRATEGY_REVERSE_MAP,
    STRATEGY_DEFAULT,
    room_selection_entity_id,
)
from .coordinator import RobEyeCoordinator
from .entity import (
    RobEyeEntity,
    async_enable_all_room_entities,
    async_remove_duplicate_room_entities,
    async_remove_entities_for_deleted_maps,
    async_remove_stale_room_entities,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[ButtonEntity] = [
        RobEyeGoHomeButton(coordinator),
        RobEyeStopButton(coordinator),
        RobEyeCleanAllButton(coordinator),
        RobEyeCleanSelectedButton(coordinator),
    ]

    # Per-map entity tracking: map_id -> {area_id -> entity}
    known_entities_by_map: dict[str, dict] = {}
    # Tracks last-known area names per map to detect renames: map_id -> {area_id -> name}.
    known_area_names_by_map: dict[str, dict[str, str]] = {}

    # Migration: re-enable any entities that were disabled by the old disable/enable model.
    async_enable_all_room_entities(hass, config_entry, "button")

    # Build entities for every map we have areas for (active + all inactive).
    for map_id in list(coordinator._areas_snapshot.keys()):
        areas_list = coordinator.areas_for(map_id)
        if not areas_list:
            continue
        current_area_ids: set = {
            str(a.get("id"))
            for a in areas_list
            if a.get("id") is not None and _parse_area_name(a)
        }
        if map_id == coordinator.active_map_id:
            async_remove_stale_room_entities(
                hass, config_entry, coordinator, "button", current_area_ids
            )
        new_buttons, new_ids = _build_room_button_entities(
            coordinator, config_entry, map_id, areas_list, set()
        )
        if new_ids:
            known_entities_by_map[map_id] = dict(zip(new_ids, new_buttons))
        known_area_names_by_map[map_id] = {
            str(a.get("id")): _parse_area_name(a)
            for a in areas_list
            if a.get("id") is not None
        }
        entities.extend(new_buttons)

    room_uids = {
        e._attr_unique_id
        for e in entities
        if isinstance(e, RobEyeRoomCleanButton) and e._attr_unique_id
    }
    async_remove_duplicate_room_entities(hass, config_entry, "button", room_uids)

    async_add_entities(entities)

    # Dynamic listener — receives map_id of the changed map
    @callback
    def _async_on_areas_updated(map_id: str) -> None:
        areas_list = coordinator.areas_for(map_id)
        if not areas_list:
            return

        map_entities = known_entities_by_map.setdefault(map_id, {})

        current_id_to_name: dict[str, str] = {
            str(area_id): _parse_area_name(area)
            for area in areas_list
            if (area_id := area.get("id")) is not None
            and _parse_area_name(area)
        }
        current_ids: set = set(current_id_to_name.keys())

        # Detect renamed areas (same area_id, different name) — treat as stale
        # so the entity is removed and re-created with the updated room name.
        old_names = known_area_names_by_map.get(map_id, {})
        renamed_ids = {
            aid for aid in current_ids
            if aid in map_entities and old_names.get(aid) != current_id_to_name[aid]
        }
        known_area_names_by_map[map_id] = current_id_to_name

        if map_id == coordinator.active_map_id:
            async_remove_stale_room_entities(
                hass, config_entry, coordinator, "button", current_ids
            )

        stale_ids = (set(map_entities.keys()) - current_ids) | renamed_ids
        from homeassistant.helpers import entity_registry as er
        _ent_reg = er.async_get(hass)
        for area_id in stale_ids:
            entity = map_entities.pop(area_id)
            LOGGER.debug("button: removing deleted-area button area_id=%s", area_id)
            if entity.registry_entry and _ent_reg.async_get(entity.entity_id):
                _ent_reg.async_remove(entity.entity_id)
            else:
                hass.async_create_task(entity.async_remove())

        new_entities, new_area_ids = _build_room_button_entities(
            coordinator, config_entry, map_id, areas_list, set(map_entities.keys())
        )
        if new_entities:
            LOGGER.debug("button: adding %d new room buttons for map %s", len(new_entities), map_id)
            for entity, area_id in zip(new_entities, new_area_ids):
                map_entities[area_id] = entity
            async_add_entities(new_entities)

        canonical_uids: set[str] = {
            e._attr_unique_id
            for e in map_entities.values()
            if hasattr(e, "_attr_unique_id") and e._attr_unique_id
        }
        if canonical_uids:
            async_remove_duplicate_room_entities(hass, config_entry, "button", canonical_uids)

    @callback
    def _async_on_maps_updated(payload) -> None:
        deleted_map_ids = payload.get("removed", set()) if isinstance(payload, dict) else payload
        removed = async_remove_entities_for_deleted_maps(
            hass, config_entry, "button", deleted_map_ids
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


def _parse_area_name(area: dict) -> str:
    """Return the room name from area_meta_data, or empty string."""
    meta_raw = area.get("area_meta_data", "")
    if not meta_raw:
        return ""
    try:
        meta = json.loads(meta_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    return meta.get("name", "").strip()


def _build_room_button_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    map_id: str,
    areas: list,
    already_known: set,
) -> tuple[list, list]:
    new_entities = []
    new_ids: list = []
    for area in areas:
        area_id = area.get("id")
        if area_id is None:
            continue
        area_id = str(area_id)
        if area_id in already_known:
            continue
        room_name = _parse_area_name(area)
        if not room_name:
            continue
        if area.get("area_state") in AREA_STATES_SKIP:
            continue
        new_entities.append(
            RobEyeRoomCleanButton(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=area_id,
                room_name=room_name,
                map_id=map_id,
            )
        )
        new_ids.append(area_id)
    return new_entities, new_ids


class RobEyeBaseButton(RobEyeEntity, ButtonEntity):
    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)


class RobEyeGoHomeButton(RobEyeBaseButton):
    _attr_translation_key = "go_home"
    _attr_icon = "mdi:home-import-outline"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"go_home_{coordinator.device_id}"
        self.entity_id = f"button.{coordinator.device_id}_return_to_base"

    async def async_press(self) -> None:
        await self.coordinator.async_send_command(self.coordinator.client.go_home)


class RobEyeStopButton(RobEyeBaseButton):
    _attr_translation_key = "stop"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"stop_{coordinator.device_id}"
        self.entity_id = f"button.{coordinator.device_id}_stop"

    async def async_press(self) -> None:
        await self.coordinator.async_send_command(
            self.coordinator.client.stop,
            label="stop(advance)",
        )
        if self.coordinator._paused_jobs:
            await self.coordinator.async_advance_to_next_job()
        else:
            await self.coordinator.async_send_command(
                self.coordinator.client.go_home,
                label="go_home",
            )


class RobEyeCleanAllButton(RobEyeBaseButton):
    _attr_translation_key = "clean_all"
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"clean_all_{coordinator.device_id}"
        self.entity_id = f"button.{coordinator.device_id}_clean_entire_home"

    async def async_press(self) -> None:
        # Use the HA-preferred fan speed (set by the global fan speed select or
        # async_set_fan_speed on the vacuum entity). Fall back to the device's
        # current value only when HA has never stored a preference.
        raw = self.coordinator.ha_fan_speed or str(
            self.coordinator.status.get("cleaning_parameter_set", "2")
        )
        strategy = self.coordinator.cleaning_strategy

        # Build area list for the active map, skipping rooms disabled in the app.
        # Using clean_map (instead of clean_all) means:
        #  - Rooms marked blocking in the RobEye app are excluded.
        #  - Per-room fan-speed and strategy selects in HA are read for each room;
        #    because the API accepts a single value for all rooms in one call,
        #    we compute the effective global setting here and apply it uniformly.
        areas = [
            a for a in self.coordinator.areas
            if a.get("id") is not None and a.get("area_state") not in AREA_STATES_SKIP
        ]

        if areas:
            # Collect per-room strategy/fan-speed from HA entities.
            dev = self.coordinator.device_id
            map_id = self.coordinator.active_map_id
            hass = self.coordinator.hass

            for area in areas:
                area_id = str(area.get("id"))
                # Per-room deep-clean switch overrides everything for that room.
                switch_id = f"switch.{dev}_map{map_id}_room_{area_id}_deep_clean"
                switch_state = hass.states.get(switch_id)
                if switch_state is not None and switch_state.state == "on":
                    strategy = STRATEGY_DEEP
                    break  # one deep-clean room → deep for the whole run

            # If no room forced deep-clean, check per-room strategy selects.
            if strategy != STRATEGY_DEEP:
                for area in areas:
                    area_id = str(area.get("id"))
                    sel_id = f"select.{dev}_map{map_id}_room_{area_id}_strategy"
                    sel_state = hass.states.get(sel_id)
                    if sel_state is not None and sel_state.state in STRATEGY_REVERSE_MAP:
                        room_strategy = STRATEGY_REVERSE_MAP[sel_state.state]
                        # Escalate to the most intensive strategy found.
                        if room_strategy != STRATEGY_DEFAULT and strategy == STRATEGY_DEFAULT:
                            strategy = room_strategy

            # Per-room fan speed: use the most intensive speed found across rooms.
            _speed_order = {"silent": 0, "eco": 1, "normal": 2, "high": 3}
            best_raw = raw
            for area in areas:
                area_id = str(area.get("id"))
                fan_sel_id = f"select.{dev}_map{map_id}_room_{area_id}_fan_speed"
                fan_state = hass.states.get(fan_sel_id)
                if fan_state is not None and fan_state.state in FAN_SPEEDS:
                    candidate_raw = FAN_SPEED_REVERSE_MAP.get(fan_state.state, raw)
                    if _speed_order.get(
                        FAN_SPEED_MAP.get(candidate_raw, ""), -1
                    ) > _speed_order.get(FAN_SPEED_MAP.get(best_raw, ""), -1):
                        best_raw = candidate_raw
            raw = best_raw

            area_ids_str = ",".join(str(a["id"]) for a in areas)
            LOGGER.debug(
                "clean_all: using clean_map with %d areas, fan=%s, strategy=%s",
                len(areas), raw, strategy,
            )
            await self.coordinator.async_send_command(
                self.coordinator.client.clean_map,
                map_id=map_id,
                area_ids=area_ids_str,
                cleaning_parameter_set=raw,
                strategy_mode=strategy,
            )
        else:
            # No area data yet (first boot) — fall back to clean_all.
            LOGGER.debug("clean_all: no area data, falling back to clean_all endpoint")
            await self.coordinator.async_send_command(
                self.coordinator.client.clean_all,
                cleaning_parameter_set=raw,
                strategy_mode=strategy,
            )



class RobEyeRoomCleanButton(RobEyeBaseButton):
    """Clean button for one room."""

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        config_entry: ConfigEntry,
        area_id: str,
        room_name: str,
        map_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        self._map_id = map_id
        self._attr_unique_id = f"clean_room_map{map_id}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"Clean {room_name}"
        self._attr_icon = "mdi:broom"
        _dev = coordinator.device_id
        self.entity_id = f"button.{_dev}_map{map_id}_clean_room_{area_id}"
        self._fan_speed_select_id = f"select.{_dev}_map{map_id}_room_{area_id}_fan_speed"
        self._strategy_select_id = f"select.{_dev}_map{map_id}_room_{area_id}_strategy"
        self._deep_clean_switch_id = f"switch.{_dev}_map{map_id}_room_{area_id}_deep_clean"

    @property
    def available(self) -> bool:
        return self._map_id == self.coordinator.active_map_id and super().available

    async def async_press(self) -> None:
        LOGGER.debug("button: clean room %s", self._area_id)
        fan_speed_label = self._get_room_fan_speed()
        raw = FAN_SPEED_REVERSE_MAP.get(fan_speed_label, "2")
        map_id: str = self._map_id
        # Per-room deep-clean switch forces STRATEGY_DEEP for this room;
        # otherwise use the per-room strategy select, falling back to global.
        room_switch = self.coordinator.hass.states.get(self._deep_clean_switch_id)
        if room_switch is not None and room_switch.state == "on":
            strategy_mode = STRATEGY_DEEP
        else:
            strategy_mode = self._get_room_strategy()
        await self.coordinator.async_send_command(
            self.coordinator.client.clean_map,
            map_id=map_id,
            area_ids=self._area_id,
            cleaning_parameter_set=raw,
            strategy_mode=strategy_mode,
        )

    def _get_room_fan_speed(self) -> str:
        state = self.coordinator.hass.states.get(self._fan_speed_select_id)
        if state is not None and state.state in FAN_SPEEDS:
            return state.state
        raw = str(self.coordinator.status.get("cleaning_parameter_set", "2"))
        return FAN_SPEED_MAP.get(raw, "normal")

    def _get_room_strategy(self) -> str:
        state = self.coordinator.hass.states.get(self._strategy_select_id)
        if state is not None and state.state in STRATEGY_REVERSE_MAP:
            return STRATEGY_REVERSE_MAP[state.state]
        return self.coordinator.cleaning_strategy


class RobEyeCleanSelectedButton(RobEyeBaseButton):
    """Clean all currently selected rooms in a single clean_map call.

    Reads input_boolean.{device_id}_map{map_id}_room_{area_id}_selected
    for all rooms. Sends area_ids as comma-separated list.
    Resolves fan speed and strategy across selected rooms (most intensive wins).
    Resets all selection booleans to off after pressing.

    Disabled (unavailable) when no rooms are selected.
    """

    _attr_icon = "mdi:broom-check"
    _attr_translation_key = "clean_selected"

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"clean_selected_{coordinator.device_id}"
        self.entity_id = f"button.{coordinator.device_id}_clean_selected_rooms"
        self._attr_name = "Clean Selected Rooms"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entry_id = self.coordinator.config_entry.entry_id
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_ROOM_SELECTION_CHANGED}_{entry_id}",
                self.async_write_ha_state,
            )
        )

    @property
    def available(self) -> bool:
        """Available only when at least one room is selected."""
        if not super().available:
            return False
        return len(self._get_selected_area_ids()) > 0

    def _get_selected_area_ids(self) -> list[str]:
        """Return area_ids of all selected rooms on the active map."""
        device_id = self.coordinator.device_id
        map_id = self.coordinator.active_map_id
        selected = []
        for area in self.coordinator.areas:
            area_id = area.get("id")
            if area_id is None:
                continue
            eid = room_selection_entity_id(device_id, map_id, str(area_id))
            state = self.coordinator.hass.states.get(eid)
            if state is not None and state.state == "on":
                selected.append(str(area_id))
        return selected

    async def async_press(self) -> None:
        selected_ids = self._get_selected_area_ids()
        if not selected_ids:
            LOGGER.warning("clean_selected: no rooms selected, ignoring")
            return

        LOGGER.debug("clean_selected: area_ids=%s", selected_ids)
        device_id = self.coordinator.device_id
        map_id = self.coordinator.active_map_id
        hass = self.coordinator.hass

        # Resolve fan speed — use most intensive across selected rooms
        _speed_order = {"silent": 0, "eco": 1, "normal": 2, "high": 3}
        best_raw = self.coordinator.ha_fan_speed or str(
            self.coordinator.status.get("cleaning_parameter_set", "2")
        )
        strategy = self.coordinator.cleaning_strategy

        _m = f"map{map_id}_"
        for area_id in selected_ids:
            # Per-room deep-clean switch overrides everything for that room
            switch_id = f"switch.{device_id}_{_m}room_{area_id}_deep_clean"
            switch_state = hass.states.get(switch_id)
            if switch_state is not None and switch_state.state == "on":
                strategy = STRATEGY_DEEP

            # Per-room fan speed: escalate to the most intensive found
            fan_sel_id = f"select.{device_id}_{_m}room_{area_id}_fan_speed"
            fan_state = hass.states.get(fan_sel_id)
            if fan_state is not None and fan_state.state in FAN_SPEEDS:
                candidate = FAN_SPEED_REVERSE_MAP.get(fan_state.state, best_raw)
                if _speed_order.get(
                    FAN_SPEED_MAP.get(candidate, ""), -1
                ) > _speed_order.get(FAN_SPEED_MAP.get(best_raw, ""), -1):
                    best_raw = candidate

            # Per-room strategy (only if deep not already locked in)
            if strategy != STRATEGY_DEEP:
                strat_sel_id = f"select.{device_id}_{_m}room_{area_id}_strategy"
                strat_state = hass.states.get(strat_sel_id)
                if strat_state is not None and strat_state.state in STRATEGY_REVERSE_MAP:
                    room_strat = STRATEGY_REVERSE_MAP[strat_state.state]
                    if room_strat != STRATEGY_DEFAULT and strategy == STRATEGY_DEFAULT:
                        strategy = room_strat

        area_ids_str = ",".join(selected_ids)
        await self.coordinator.async_send_command(
            self.coordinator.client.clean_map,
            map_id=map_id,
            area_ids=area_ids_str,
            cleaning_parameter_set=best_raw,
            strategy_mode=strategy,
        )

        # Reset all selection switches after enqueuing the clean command
        for area_id in selected_ids:
            eid = room_selection_entity_id(device_id, map_id, area_id)
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": eid}, blocking=False
            )

