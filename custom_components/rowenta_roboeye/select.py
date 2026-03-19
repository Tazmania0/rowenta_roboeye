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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, FAN_SPEED_MAP, FAN_SPEED_REVERSE_MAP, FAN_SPEEDS, LOGGER, SIGNAL_AREAS_UPDATED
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SelectEntity] = [RobEyeCleaningModeSelect(coordinator)]

    known_ids: set = set()
    room_selects, new_ids = _build_room_select_entities(
        coordinator, config_entry, coordinator.areas, known_ids
    )
    entities.extend(room_selects)
    known_ids.update(new_ids)
    async_add_entities(entities)

    @callback
    def _async_on_areas_updated() -> None:
        new_entities, new_area_ids = _build_room_select_entities(
            coordinator, config_entry, coordinator.areas, known_ids
        )
        if new_entities:
            LOGGER.debug("select: adding %d new room fan speed selects", len(new_entities))
            async_add_entities(new_entities)
            known_ids.update(new_area_ids)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _async_on_areas_updated,
        )
    )


def _build_room_select_entities(
    coordinator: RobEyeCoordinator,
    config_entry: ConfigEntry,
    areas: list,
    already_known: set,
) -> tuple[list, set]:
    new_entities = []
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
            continue
        room_name = meta.get("name", "").strip()
        if not room_name:
            continue
        new_entities.append(
            RobEyeRoomFanSpeedSelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=str(area_id),
                room_name=room_name,
            )
        )
        new_ids.add(area_id)
    return new_entities, new_ids


# ── Global fan speed select ───────────────────────────────────────────

class RobEyeCleaningModeSelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Controls the vacuum's active fan speed."""

    _attr_translation_key = "cleaning_mode"
    _attr_icon = "mdi:speedometer"
    _attr_options = FAN_SPEEDS

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"cleaning_mode_{coordinator.device_id}"
        self._last_known: str | None = None

    @property
    def current_option(self) -> str | None:
        raw = str(self.coordinator.status.get("cleaning_parameter_set", ""))
        live = FAN_SPEED_MAP.get(raw)
        if live is not None:
            self._last_known = live
        return live or self._last_known

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in FAN_SPEEDS:
                self._last_known = last_state.state

    async def async_select_option(self, option: str) -> None:
        raw = FAN_SPEED_REVERSE_MAP.get(option)
        if raw is None:
            LOGGER.warning("Unknown cleaning mode: %s", option)
            return
        self._last_known = option
        await self.coordinator.async_send_command(
            self.coordinator.client.set_fan_speed,
            cleaning_parameter_set=raw,
        )


# ── Per-room fan speed select ─────────────────────────────────────────

class RobEyeRoomFanSpeedSelect(RobEyeEntity, SelectEntity, RestoreEntity):
    """Stores the desired fan speed for a single room (local only)."""

    _attr_icon = "mdi:speedometer"
    _attr_options = FAN_SPEEDS

    def __init__(
        self,
        coordinator: RobEyeCoordinator,
        config_entry: ConfigEntry,
        area_id: str,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        self._room_name = room_name
        self._attr_unique_id = f"room_fan_speed_{area_id}_{coordinator.device_id}"
        self._attr_name = f"{room_name} Fan Speed"
        self._selected: str = "normal"

    @property
    def current_option(self) -> str:
        return self._selected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in FAN_SPEEDS:
                self._selected = last_state.state

    async def async_select_option(self, option: str) -> None:
        if option not in FAN_SPEEDS:
            LOGGER.warning("Unknown fan speed for room %s: %s", self._room_name, option)
            return
        self._selected = option
        self.async_write_ha_state()
