"""Base entity for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

import re
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, MATCH_ALL
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

# Fallback: extract map_id from entity_id when unique_id is unparseable.
# Room entity IDs always contain _map{digits}_ (e.g. button.sn_map3_clean_room_7).
# Global entities (battery, mode, …) never contain this pattern.
_RE_ENTITY_ID_MAP = re.compile(r"_map(\d+)_")


def _parse_room_entity_uid(unique_id: str) -> tuple[str, str] | None:
    """Return (area_id_str, map_id_str) from a room entity unique_id, or None."""
    m = _RE_SENSOR_ROOM_UID.match(unique_id)
    if m:
        return m.group(1), m.group(2)
    m = _RE_OTHER_ROOM_UID.search(unique_id)
    if m:
        return m.group(2), m.group(1)
    return None


def _entity_id_map_segment(entity_id: str) -> str | None:
    """Return the map_id embedded in entity_id via _map{digits}_ pattern, or None.

    Used as a fallback for registry entries whose unique_id cannot be parsed by
    _parse_room_entity_uid (e.g. legacy formats from before multi-map support).
    """
    m = _RE_ENTITY_ID_MAP.search(entity_id)
    return m.group(1) if m else None


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

    This function groups ALL platform room entries by their (area_id, map_id) pair
    — across every map, not just the active one.  For each group with more than one
    entry it removes all non-canonical members.  When no canonical entry is supplied
    for a given pair (e.g. an inactive map whose entities were created in a previous
    session with a different device_id), the entry with the lowest sort order is kept
    and the rest are removed (deterministic tie-break).

    ``canonical_unique_ids`` — the set of unique_ids that are known-correct for the
    current session.  Pass the UIDs of all entity objects that were just created /
    are currently tracked in ``known_entities_by_map``.
    """
    if not canonical_unique_ids:
        return

    # Build the set of (area_id, map_id) pairs covered by canonical UIDs.
    covered: set[tuple[str, str]] = set()
    for uid in canonical_unique_ids:
        parsed = _parse_room_entity_uid(uid)
        if parsed:
            covered.add(parsed)

    ent_reg = er.async_get(hass)
    entries = [
        e
        for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if e.domain == platform
    ]

    # Group parseable room entries by (area_id, map_id).
    by_pair: dict[tuple[str, str], list] = {}
    for entry in entries:
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is not None:
            by_pair.setdefault(parsed, []).append(entry)

    # Phase 1: for pairs covered by canonical UIDs, remove every non-canonical entry.
    # This handles the common migration case where the old-device_id entry is the
    # only entry in the registry (the new entity is still being created asynchronously)
    # as well as the case where both old and new entries already exist in the registry.
    for pair, pair_entries in by_pair.items():
        if pair not in covered:
            continue
        for entry in pair_entries:
            if entry.unique_id not in canonical_unique_ids:
                LOGGER.info(
                    "RobEye: removing stale duplicate %s (uid=%s) — superseded by canonical",
                    entry.entity_id,
                    entry.unique_id,
                )
                ent_reg.async_remove(entry.entity_id)

    # Phase 2: for pairs NOT covered by canonical UIDs but with multiple entries
    # (inactive-map duplicates created with an old device_id in a previous session),
    # keep the lexicographically smallest entry and remove the rest.
    for pair, pair_entries in by_pair.items():
        if pair in covered:
            continue  # already handled in phase 1
        if len(pair_entries) <= 1:
            continue
        keeper = min(pair_entries, key=lambda e: e.unique_id)
        for entry in pair_entries:
            if entry.entity_id != keeper.entity_id:
                LOGGER.info(
                    "RobEye: removing extra duplicate %s (uid=%s) — keeping %s for %s",
                    entry.entity_id,
                    entry.unique_id,
                    keeper.entity_id,
                    pair,
                )
                ent_reg.async_remove(entry.entity_id)


def async_enable_room_entities_for_map(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
    map_id: str,
) -> None:
    """Re-enable registry entries for rooms on ``map_id`` that the integration disabled.

    Called before adding new entity objects for a map that becomes active.  Ensures
    the registry entry is enabled so that HA's async_add_entities call links the new
    object to the existing entry (rather than treating it as still-disabled).

    Entries whose unique_id cannot be parsed (legacy formats without ``_map`` in the
    unique_id) are identified via the entity_id ``_map{digits}_`` pattern instead.
    """
    if not map_id:
        return
    ent_reg = er.async_get(hass)
    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            # Fallback: derive map_id from entity_id for legacy unique_id formats.
            entity_map_id = _entity_id_map_segment(entry.entity_id)
        else:
            _, entity_map_id = parsed
        if (
            entity_map_id == map_id
            and entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
        ):
            ent_reg.async_update_entity(entry.entity_id, disabled_by=None)


def async_enable_all_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
) -> None:
    """Re-enable all room entities that were disabled by the integration (upgrade migration).

    Called once at setup time so users upgrading from the old disable/enable model
    get all their per-map room entities back as enabled. Availability is now gated
    by the `available` property comparison only.
    """
    ent_reg = er.async_get(hass)
    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        if entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            LOGGER.debug(
                "RobEye: re-enabling room entity %s (upgrade from disable/enable model)",
                entry.entity_id,
            )
            ent_reg.async_update_entity(entry.entity_id, disabled_by=None)


def async_disable_room_entities_for_other_maps(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform: str,
    active_map_id: str,
) -> None:
    """Disable registry entries for rooms that do NOT belong to ``active_map_id``.

    Inactive-map entities are disabled rather than deleted so they can be cheaply
    re-enabled when the user switches back.  Disabled entities show as '—' in the
    HA entity list (which the user explicitly expects) rather than '!' (the
    enabled-but-unavailable state that appears as a duplicate).

    Only entries whose ``disabled_by`` is currently ``None`` are changed — we never
    override entries disabled by the user or by another HA mechanism.

    Entries whose unique_id cannot be parsed (legacy formats without ``_map`` in the
    unique_id) are identified via the entity_id ``_map{digits}_`` pattern so they are
    no longer silently left enabled as persistent visible duplicates.
    """
    if not active_map_id:
        return
    ent_reg = er.async_get(hass)
    for entry in list(er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            # Fallback: derive map_id from entity_id for legacy unique_id formats.
            entity_map_id = _entity_id_map_segment(entry.entity_id)
            if entity_map_id is None:
                continue  # not a room entity — leave it alone
        else:
            _, entity_map_id = parsed
        if entity_map_id != active_map_id and entry.disabled_by is None:
            LOGGER.debug(
                "RobEye: disabling room entity %s (map %s != active %s)",
                entry.entity_id,
                entity_map_id,
                active_map_id,
            )
            ent_reg.async_update_entity(
                entry.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )


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

    active_map_id = coordinator.committed_active_map_id
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
    # Exclude all extra attributes from recorder history; core metadata
    # (state_class, unit_of_measurement, device_class, friendly_name) is
    # always kept by HA and long-term statistics continue to function normally.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: RobEyeCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        robot_id = self.coordinator.robot_info.get("robot_id", {})
        proto = self.coordinator.robot_info.get("protocol_version", {})
        serial = (
            robot_id.get("unique_id")
            or robot_id.get("serial_number")
            or robot_id.get("robot_id")
            or robot_id.get("id")
            or None
        )
        sw_version = (
            robot_id.get("firmware")
            or proto.get("version")
            or None
        )
        host = self.coordinator.config_entry.data.get(CONF_HOST)
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            manufacturer="Rowenta / SEB",
            name="Rowenta Xplorer 120",
            model="Xplorer 120",
            serial_number=serial,
            sw_version=sw_version,
            configuration_url=f"http://{host}:8080" if host else None,
        )
