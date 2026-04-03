"""Base entity for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .coordinator import RobEyeCoordinator

# --- Room entity unique_id parsers ---
# Sensor format:            room_{area_id}_map{map_id}_{suffix}_{device_id}
# Button/Select/Switch fmt: {prefix}_map{map_id}_{area_id}_{device_id}
_RE_SENSOR_ROOM_UID = re.compile(r"^room_(\d+)_map(\w+?)_")
_RE_OTHER_ROOM_UID = re.compile(r"_map(\w+?)_(\d+)_")


def _parse_room_entity_uid(unique_id: str) -> tuple[str, str] | None:
    """Return (area_id_str, map_id_str) from a room entity unique_id, or None."""
    m = _RE_SENSOR_ROOM_UID.match(unique_id)
    if m:
        return m.group(1), m.group(2)
    m = _RE_OTHER_ROOM_UID.search(unique_id)
    if m:
        return m.group(2), m.group(1)
    return None


def async_remove_stale_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    platform: str,
    current_area_ids: set,
) -> set:
    """Permanently remove entity registry entries for areas deleted from the active map.

    When a user redraws/splits/merges rooms on the robot, the old area IDs are
    gone for good and HA would show those entities as unavailable orphans.  This
    function deletes them outright so they no longer appear in the UI.

    Entities belonging to *other* maps are left untouched — they are simply
    unavailable while that map is not active and come back when the user switches.

    A guard is applied: if ``current_area_ids`` is empty (likely an API error
    returning a blank list) the deletion is skipped to avoid wiping all entities
    on a transient network failure.

    Returns the set of ``(map_id, area_id_str)`` tuples that were removed so the
    caller can evict them from its ``known_ids`` closure and allow fresh entity
    creation if the same area ID ever reappears.
    """
    if not current_area_ids:
        return set()

    active_map_id = coordinator.active_map_id
    current_strs = {str(x) for x in current_area_ids}
    ent_reg = er.async_get(hass)
    removed: set = set()

    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        area_id_str, entity_map_id = parsed
        # Only act on entities for the currently active map; other maps are
        # handled separately (their entities become unavailable during a map
        # switch and are restored when the user switches back).
        if entity_map_id and entity_map_id != active_map_id:
            continue
        if area_id_str not in current_strs:
            LOGGER.info(
                "RobEye: removing orphaned room entity %s (area %s no longer exists on map %s)",
                entry.entity_id,
                area_id_str,
                active_map_id,
            )
            ent_reg.async_remove(entry.entity_id)
            removed.add((active_map_id, area_id_str))

    return removed


class RobEyeEntity(CoordinatorEntity[RobEyeCoordinator]):
    """Base class for all RobEye entities.

    Device identifier is based on coordinator.device_id (robot serial when
    available, entry_id fallback) so all entities are grouped under the
    correct device regardless of how HA assigns config entry IDs.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            manufacturer="Rowenta / SEB",
            name="Rowenta Xplorer 120",
            model="Xplorer 120",
        )
