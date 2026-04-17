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
    AREA_STATE_BLOCKING,
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SIGNAL_AREAS_UPDATED,
    SIGNAL_ROOM_SELECTION_CHANGED,
    STRATEGY_DEEP,
    STRATEGY_REVERSE_MAP,
    STRATEGY_DEFAULT,
    room_selection_entity_id,
)
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


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

    # Initial room buttons
    known_entities: dict = {}
    initial_buttons, initial_ids = _build_room_button_entities(
        coordinator, config_entry, coordinator.areas, set()
    )
    for entity, area_id in zip(initial_buttons, initial_ids):
        known_entities[area_id] = entity
    entities.extend(initial_buttons)
    async_add_entities(entities)

    # Dynamic listener
    @callback
    def _async_on_areas_updated() -> None:
        if coordinator.areas_map_id != coordinator.active_map_id:
            LOGGER.debug("button: areas fetched for wrong map, skipping update")
            return

        current_ids: set = {
            area_id
            for area in coordinator.areas
            if (area_id := area.get("id")) is not None
            and area.get("area_meta_data", "")
            and _parse_area_name(area)
        }

        stale_ids = set(known_entities.keys()) - current_ids
        for area_id in stale_ids:
            entity = known_entities.pop(area_id)
            LOGGER.debug("button: removing stale room button area_id=%s", area_id)
            hass.async_create_task(entity.async_remove())

        new_entities, new_area_ids = _build_room_button_entities(
            coordinator, config_entry, coordinator.areas, set(known_entities.keys())
        )
        if new_entities:
            LOGGER.debug("button: adding %d new room buttons", len(new_entities))
            for entity, area_id in zip(new_entities, new_area_ids):
                known_entities[area_id] = entity
            async_add_entities(new_entities)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _async_on_areas_updated,
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
    areas: list,
    already_known: set,
) -> tuple[list, list]:
    new_entities = []
    new_ids: list = []
    _map = coordinator.active_map_id
    # Guard: skip if areas data was fetched for a different map (stale-signal race).
    if coordinator.areas_map_id != _map:
        return new_entities, new_ids
    for area in areas:
        area_id = area.get("id")
        if area_id is None or area_id in already_known:
            continue
        room_name = _parse_area_name(area)
        if not room_name:
            continue
        # Skip areas disabled for cleaning in the RobEye app
        if area.get("area_state") == AREA_STATE_BLOCKING:
            continue
        new_entities.append(
            RobEyeRoomCleanButton(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=str(area_id),
                room_name=room_name,
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
        await self.coordinator.async_send_command(self.coordinator.client.stop)


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
            if a.get("id") is not None and a.get("area_state") != AREA_STATE_BLOCKING
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
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        _map = coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"clean_room_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"Clean {room_name}"
        self._attr_icon = "mdi:broom"
        _dev = coordinator.device_id
        self.entity_id = f"button.{_dev}_map{_map}_clean_room_{area_id}"
        self._fan_speed_select_id = f"select.{_dev}_map{_map}_room_{area_id}_fan_speed"
        self._strategy_select_id = f"select.{_dev}_map{_map}_room_{area_id}_strategy"
        self._deep_clean_switch_id = f"switch.{_dev}_map{_map}_room_{area_id}_deep_clean"

    @property
    def available(self) -> bool:
        return super().available and self._map_id == self.coordinator.active_map_id

    async def async_press(self) -> None:
        LOGGER.debug("button: clean room %s", self._area_id)
        fan_speed_label = self._get_room_fan_speed()
        raw = FAN_SPEED_REVERSE_MAP.get(fan_speed_label, "2")
        map_id: str = self.coordinator.active_map_id
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

