"""Base entity for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RobEyeCoordinator


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
