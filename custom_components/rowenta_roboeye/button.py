"""Button entities for the Rowenta Xplorer 120.

Dynamic room clean buttons are added without reload via SIGNAL_AREAS_UPDATED.
"""

from __future__ import annotations

import json

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SIGNAL_AREAS_UPDATED,
    STRATEGY_DEEP,
)
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[ButtonEntity] = [
        RobEyeGoHomeButton(coordinator),
        RobEyeStopButton(coordinator),
        RobEyeCleanAllButton(coordinator),
    ]

    # Initial room buttons
    known_ids: set = set()
    room_buttons, new_ids = _build_room_button_entities(
        coordinator, config_entry, coordinator.areas, known_ids
    )
    entities.extend(room_buttons)
    known_ids.update(new_ids)
    async_add_entities(entities)

    # Dynamic listener
    @callback
    def _async_on_areas_updated() -> None:
        new_entities, new_area_ids = _build_room_button_entities(
            coordinator, config_entry, coordinator.areas, known_ids
        )
        if new_entities:
            LOGGER.debug("button: adding %d new room buttons", len(new_entities))
            async_add_entities(new_entities)
            known_ids.update(new_area_ids)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _async_on_areas_updated,
        )
    )


def _build_room_button_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    areas: list,
    already_known: set,
) -> tuple[list, set]:
    new_entities = []
    new_ids: set = set()
    _map = coordinator.active_map_id
    for area in areas:
        area_id = area.get("id")
        if area_id is None or (_map, area_id) in already_known:
            continue
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        room_name = meta.get("name", "").strip()
        if not room_name:
            continue
        new_entities.append(
            RobEyeRoomCleanButton(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=str(area_id),
                room_name=room_name,
            )
        )

        new_ids.add((_map, area_id))
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
        raw = str(self.coordinator.status.get("cleaning_parameter_set", "2"))
        await self.coordinator.async_send_command(
            self.coordinator.client.clean_all,
            cleaning_parameter_set=raw,
            strategy_mode=self.coordinator.cleaning_strategy,
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
        self._attr_unique_id = f"clean_room_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"Clean {room_name}"
        self._attr_icon = "mdi:broom"
        _dev = coordinator.device_id
        self.entity_id = f"button.{_dev}_map{_map}_clean_room_{area_id}"
        self._fan_speed_select_id = f"select.{_dev}_map{_map}_room_{area_id}_fan_speed"
        # Per-room deep clean switch entity id
        self._deep_clean_switch_id = f"switch.{_dev}_map{_map}_room_{area_id}_deep_clean"

    async def async_press(self) -> None:
        LOGGER.debug("button: clean room %s", self._area_id)
        fan_speed_label = self._get_room_fan_speed()
        raw = FAN_SPEED_REVERSE_MAP.get(fan_speed_label, "2")
        map_id: str = self.coordinator.active_map_id
        # Per-room deep-clean switch forces STRATEGY_DEEP for this room only;
        # otherwise fall back to the global strategy set by the strategy select.
        room_switch = self.coordinator.hass.states.get(self._deep_clean_switch_id)
        strategy_mode = (
            STRATEGY_DEEP
            if room_switch is not None and room_switch.state == "on"
            else self.coordinator.cleaning_strategy
        )
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

