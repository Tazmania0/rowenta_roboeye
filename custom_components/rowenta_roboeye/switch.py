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
"""

from __future__ import annotations

import json as _json

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, LOGGER, SIGNAL_AREAS_UPDATED, STRATEGY_DEFAULT, STRATEGY_DEEP
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list = [RobEyeDeepCleanSwitch(coordinator)]
    known_ids: set = set()

    def _room_switches(areas: list, already_known: set) -> tuple[list, set]:
        new_entities: list = []
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
                meta = _json.loads(meta_raw)
            except Exception:
                continue
            room_name = meta.get("name", "").strip()
            if not room_name:
                continue
            new_entities.append(
                RobEyeRoomDeepCleanSwitch(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    area_id=str(area_id),
                    room_name=room_name,
                )
            )
            new_ids.add((_map, area_id))
        return new_entities, new_ids

    room_switches, new_ids = _room_switches(coordinator.areas, known_ids)
    entities.extend(room_switches)
    known_ids.update(new_ids)
    async_add_entities(entities)

    @callback
    def _on_areas_updated() -> None:
        new_entities, new_area_ids = _room_switches(coordinator.areas, known_ids)
        if new_entities:
            async_add_entities(new_entities)
            known_ids.update(new_area_ids)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _on_areas_updated,
        )
    )


class RobEyeDeepCleanSwitch(RobEyeEntity, SwitchEntity, RestoreEntity):
    """Global toggle: ON = STRATEGY_DEEP, OFF = STRATEGY_DEFAULT.

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
        self.coordinator.cleaning_strategy = STRATEGY_DEFAULT
        self.async_write_ha_state()


class RobEyeRoomDeepCleanSwitch(RobEyeEntity, SwitchEntity, RestoreEntity):
    """Per-room deep clean toggle.

    entity_id is forced to switch.rowenta_xplorer_120_room_{id}_deep_clean
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
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        _map = coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_deep_clean_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = room_name + " Deep Clean"
        self.entity_id = f"switch.{coordinator.device_id}_map{_map}_room_{area_id}_deep_clean"
        self._is_on: bool = False

    @property
    def available(self) -> bool:
        return super().available and self._map_id == self.coordinator.active_map_id

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._is_on = last.state == "on"

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = False
        self.async_write_ha_state()
