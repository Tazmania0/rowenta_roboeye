"""Switch entities for the Rowenta Xplorer 120.

RobEyeDeepCleanSwitch
  Global deep clean toggle. When ON, sets coordinator.cleaning_strategy to
  STRATEGY_DEEP (mode 3 = double/triple pass). When OFF, resets to
  STRATEGY_DEFAULT (mode 4 = robot decides). Stored locally, no API call.
  Kept for backwards compatibility; the strategy select exposes all four modes.
  Per-room strategy is now controlled by RobEyeRoomStrategySelect in select.py.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, LOGGER, STRATEGY_DEFAULT, STRATEGY_DEEP
from .coordinator import RobEyeCoordinator
from .entity import RobEyeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RobEyeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([RobEyeDeepCleanSwitch(coordinator)])


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

