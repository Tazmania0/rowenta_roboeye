"""Base entity for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class RoomRegistryRecord:
    """A per-room registry entry parsed from its unique_id."""

    map_id: str
    area_id: str
    unique_id: str
    entity_id: str
    original_name: str | None


def find_room_registry_records(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
) -> list[RoomRegistryRecord]:
    """Return room-entity registry records for this config entry on a platform."""
    ent_reg = er.async_get(hass)
    records: list[RoomRegistryRecord] = []
    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        area_id_str, map_id_str = parsed
        records.append(
            RoomRegistryRecord(
                map_id=map_id_str,
                area_id=area_id_str,
                unique_id=entry.unique_id,
                entity_id=entry.entity_id,
                original_name=entry.original_name,
            )
        )
    return records


def strip_known_suffix(name: str, suffixes: tuple[str, ...]) -> str:
    """Return ``name`` with the first matching suffix stripped, else ``""``."""
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return ""


def pick_room_name_from_records(
    records: list[RoomRegistryRecord],
    suffixes: tuple[str, ...],
) -> str:
    """Return the room name recovered from any record with a known suffix.

    Falls back to the first record's ``original_name`` (without stripping)
    when none of them have a recognisable suffix — handles entries where
    the user has customised the name in the registry.
    """
    for rec in records:
        stripped = strip_known_suffix(rec.original_name or "", suffixes)
        if stripped:
            return stripped
    if records and records[0].original_name:
        return records[0].original_name
    return ""


def async_remove_entities_for_deleted_maps(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
    deleted_map_ids: set[str],
) -> set[tuple[str, str]]:
    """Remove entity registry entries for maps that no longer exist on the device.

    When the user deletes a floor map from the robot's native app, all HA
    entities stamped with that map_id become permanent orphans.  This function
    removes them from the registry outright so they disappear from the UI.

    Returns the set of ``(map_id, area_id_str)`` tuples that were removed so
    callers can evict them from their ``known_ids`` closures.
    """
    if not deleted_map_ids:
        return set()

    ent_reg = er.async_get(hass)
    removed: set = set()

    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        area_id_str, entity_map_id = parsed
        if entity_map_id in deleted_map_ids:
            LOGGER.info(
                "RobEye: removing entity %s — map %s was deleted from device",
                entry.entity_id,
                entity_map_id,
            )
            ent_reg.async_remove(entry.entity_id)
            removed.add((entity_map_id, area_id_str))

    return removed


def async_remove_duplicate_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
    canonical_unique_ids: set[str],
) -> None:
    """Remove registry entries that are stale duplicates of currently-loaded entities.

    When device_id changes between HA restarts (e.g. serial number becomes
    available after first-boot fallback to entry_id), every room entity gets a
    new unique_id while the old registry entries linger.  Those orphans remain
    attributed to this integration and appear as unavailable duplicates in the UI.

    This function removes any registry entry whose unique_id is NOT in
    ``canonical_unique_ids`` but whose (area_id, map_id) IS represented by a
    canonical entry — i.e. it has been superseded by a freshly-created entity.
    """
    if not canonical_unique_ids:
        return

    covered: set[tuple[str, str]] = set()
    for uid in canonical_unique_ids:
        parsed = _parse_room_entity_uid(uid)
        if parsed:
            covered.add(parsed)

    if not covered:
        return

    ent_reg = er.async_get(hass)
    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        if entry.unique_id in canonical_unique_ids:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed and parsed in covered:
            LOGGER.info(
                "RobEye: removing stale duplicate %s (uid=%s) — superseded by current entity",
                entry.entity_id,
                entry.unique_id,
            )
            ent_reg.async_remove(entry.entity_id)


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
