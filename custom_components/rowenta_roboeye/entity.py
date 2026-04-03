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


def async_disable_stale_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    platform: str,
    current_area_ids: set,
) -> None:
    """Disable entity registry entries for rooms deleted from the active map.

    Entities belonging to other maps are left alone so they can be re-enabled
    when the user switches back to that map.  User-disabled entities are never
    touched.
    """
    active_map_id = coordinator.active_map_id
    ent_reg = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if entry.domain != platform:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        area_id_str, entity_map_id = parsed
        if entity_map_id and entity_map_id != active_map_id:
            continue
        still_present = (
            area_id_str in current_area_ids
            or area_id_str in {str(x) for x in current_area_ids}
        )
        if not still_present:
            LOGGER.info(
                "RobEye: disabling stale room entity %s (area %s no longer on map %s)",
                entry.entity_id,
                area_id_str,
                active_map_id,
            )
            ent_reg.async_update_entity(
                entry.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )


def async_reenable_room_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    platform: str,
    current_area_ids: set,
) -> None:
    """Re-enable integration-disabled room entities whose area has reappeared.

    Inverse of async_disable_stale_room_entities.  User-disabled entities are
    intentionally left alone.
    """
    active_map_id = coordinator.active_map_id
    ent_reg = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if entry.domain != platform:
            continue
        if entry.disabled_by != er.RegistryEntryDisabler.INTEGRATION:
            continue
        parsed = _parse_room_entity_uid(entry.unique_id)
        if parsed is None:
            continue
        area_id_str, entity_map_id = parsed
        if entity_map_id and entity_map_id != active_map_id:
            continue
        returned = (
            area_id_str in current_area_ids
            or area_id_str in {str(x) for x in current_area_ids}
        )
        if returned:
            LOGGER.info(
                "RobEye: re-enabling room entity %s (area %s returned to map %s)",
                entry.entity_id,
                area_id_str,
                active_map_id,
            )
            ent_reg.async_update_entity(entry.entity_id, disabled_by=None)


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
