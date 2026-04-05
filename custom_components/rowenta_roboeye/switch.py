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

from .const import AREA_STATE_BLOCKING, DOMAIN, LOGGER, SIGNAL_AREAS_UPDATED, STRATEGY_DEFAULT, STRATEGY_DEEP
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity, async_remove_stale_room_entities


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
                meta = _json.loads(meta_raw)
            except Exception:
                continue
            room_name = meta.get("name", "").strip()
            if not room_name:
                continue
            # Skip areas disabled for cleaning in the RobEye app
            if area.get("area_state") == AREA_STATE_BLOCKING:
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
        current_area_ids = {a.get("id") for a in coordinator.areas if a.get("id") is not None}
        removed = async_remove_stale_room_entities(hass, config_entry, coordinator, "switch", current_area_ids)
        known_ids.difference_update(removed)
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
    ) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        _map = coordinator.active_map_id
        self._map_id = _map
        self._attr_unique_id = f"room_deep_clean_map{_map}_{area_id}_{coordinator.device_id}"
        self._attr_name = room_name + " Deep Clean"
        self.entity_id = f"switch.{coordinator.device_id}_map{_map}_room_{area_id}_deep_clean"
        self._is_on: bool = False
        self._last_robot_strategy: str | None = None  # last strategy_mode read from robot

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

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = True
        self.async_write_ha_state()
        await self.coordinator.async_send_command(
            self.coordinator.client.modify_area,
            map_id=self.coordinator.active_map_id,
            area_id=self._area_id,
            strategy_mode="deep",
        )

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        self._is_on = False
        self.async_write_ha_state()
        # "normal" is the only non-deep value the robot accepts.
        # The strategy select holds the granular choice (Default/Normal/Walls&Corners).
        await self.coordinator.async_send_command(
            self.coordinator.client.modify_area,
            map_id=self.coordinator.active_map_id,
            area_id=self._area_id,
            strategy_mode="normal",
        )
