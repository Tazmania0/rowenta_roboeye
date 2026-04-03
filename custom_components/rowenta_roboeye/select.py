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

from .const import (
    AREA_STATE_BLOCKING,
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    FAN_SPEEDS,
    LOGGER,
    SIGNAL_AREAS_UPDATED,
    STRATEGY_DEFAULT,
    STRATEGY_DEEP,
    STRATEGY_LABELS,
    STRATEGY_OPTIONS,
    STRATEGY_REVERSE_MAP,
)
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity, async_disable_stale_room_entities, async_reenable_room_entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SelectEntity] = [
        RobEyeCleaningModeSelect(coordinator),
        RobEyeStrategySelect(coordinator),
        RobEyeActiveMapSelect(coordinator),
    ]

    known_ids: set = set()
    room_selects, new_ids = _build_room_select_entities(
        coordinator, config_entry, coordinator.areas, known_ids
    )
    entities.extend(room_selects)
    known_ids.update(new_ids)
    async_add_entities(entities)

    @callback
    def _async_on_areas_updated() -> None:
        current_area_ids = {a.get("id") for a in coordinator.areas if a.get("id") is not None}
        async_reenable_room_entities(hass, config_entry, coordinator, "select", current_area_ids)
        new_entities, new_area_ids = _build_room_select_entities(
            coordinator, config_entry, coordinator.areas, known_ids
        )
        if new_entities:
            LOGGER.debug("select: adding %d new room fan speed selects", len(new_entities))
            async_add_entities(new_entities)
            known_ids.update(new_area_ids)
        async_disable_stale_room_entities(hass, config_entry, coordinator, "select", current_area_ids)

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
    _map = coordinator.active_map_id
    # Guard: skip if areas data was fetched for a different map (stale-signal race).
    if coordinator.areas_map_id != _map:
        return new_entities, new_ids
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
        # Skip areas disabled for cleaning in the RobEye app
        if area.get("area_state") == AREA_STATE_BLOCKING:
            continue
        new_entities.append(
            RobEyeRoomFanSpeedSelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=str(area_id),
                room_name=room_name,
            )
        )
        new_entities.append(
            RobEyeRoomStrategySelect(
                coordinator=coordinator,
                config_entry=config_entry,
                area_id=str(area_id),
                room_name=room_name,
            )
        )
        new_ids.add((_map, area_id))
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
        self.entity_id = f"select.{coordinator.device_id}_cleaning_mode"
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
        _map = coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_fan_speed_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"{room_name} Fan Speed"
        self.entity_id = f"select.{coordinator.device_id}_map{_map}_room_{area_id}_fan_speed"
        self._selected: str = "normal"

    @property
    def available(self) -> bool:
        return super().available and self._map_id == self.coordinator.active_map_id

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
                self.coordinator._manual_map_id = map_id

    async def async_select_option(self, option: str) -> None:
        map_id = self._name_to_id.get(option, option)
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
        self._last_non_deep: str = STRATEGY_LABELS[STRATEGY_DEFAULT]

    @property
    def current_option(self) -> str:
        strategy = self.coordinator.cleaning_strategy
        if strategy == STRATEGY_DEEP:
            # Deep is controlled by the switch; keep select showing last non-deep choice
            return self._last_non_deep
        label = STRATEGY_LABELS.get(strategy, STRATEGY_LABELS[STRATEGY_DEFAULT])
        self._last_non_deep = label
        return label

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            api_val = STRATEGY_REVERSE_MAP.get(last_state.state)
            if api_val is not None and api_val != STRATEGY_DEEP:
                self._last_non_deep = last_state.state
                self.coordinator.cleaning_strategy = api_val

    async def async_select_option(self, option: str) -> None:
        api_val = STRATEGY_REVERSE_MAP.get(option)
        if api_val is None:
            LOGGER.warning("Unknown cleaning strategy option: %s", option)
            return
        self._last_non_deep = option
        self.coordinator.cleaning_strategy = api_val
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
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        self._room_name = room_name
        _map = coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_strategy_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = f"{room_name} Strategy"
        self.entity_id = f"select.{coordinator.device_id}_map{_map}_room_{area_id}_strategy"
        self._selected: str = STRATEGY_LABELS[STRATEGY_DEFAULT]

    @property
    def available(self) -> bool:
        return super().available and self._map_id == self.coordinator.active_map_id

    @property
    def current_option(self) -> str:
        return self._selected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in STRATEGY_OPTIONS:
                self._selected = last_state.state

    async def async_select_option(self, option: str) -> None:
        if option not in STRATEGY_OPTIONS:
            LOGGER.warning("Unknown strategy for room %s: %s", self._room_name, option)
            return
        self._selected = option
        self.async_write_ha_state()
