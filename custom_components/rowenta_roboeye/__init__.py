"""Rowenta Xplorer 120 (RobEye) Home Assistant integration."""

from __future__ import annotations

__version__ = "1.3.6"

import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, CoreState, EVENT_HOMEASSISTANT_STARTED, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType

from .api import RobEyeApiClient
from .const import CONF_MAP_ID, DOMAIN, LOGGER, PLATFORMS, SIGNAL_AREAS_UPDATED
from .coordinator import RobEyeCoordinator
from .dashboard import RobEyeDashboardManager, async_create_dashboard
from .frontend import JSModuleRegistration


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register frontend resources once per integration load.

    Must be in async_setup (not async_setup_entry) so registration
    happens once regardless of how many config entries exist.
    """
    async def _register_frontend(_event: object = None) -> None:
        reg = JSModuleRegistration(hass, __version__)
        await reg.async_register()

    if hass.state == CoreState.running:
        await _register_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_frontend)

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Rowenta RobEye from a config entry."""
    host: str = config_entry.data[CONF_HOST]
    map_id: str = config_entry.data[CONF_MAP_ID]

    client = RobEyeApiClient(host=host)

    coordinator = RobEyeCoordinator(
        hass=hass,
        config_entry=config_entry,
        client=client,
        map_id=map_id,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # One manager per config entry — holds hash + dashboard object reference.
    # Stored in hass.data so async_remove_entry can call async_delete() on it.
    dashboard_manager = RobEyeDashboardManager()
    hass.data[DOMAIN][f"{config_entry.entry_id}_dashboard"] = dashboard_manager

    # Initial dashboard write
    await async_create_dashboard(
        hass, coordinator.areas, coordinator.robot_info,
        manager=dashboard_manager, device_id=coordinator.device_id,
    )

    # Debounced dashboard regeneration on every coordinator update
    _cancel_pending: list[asyncio.TimerHandle | None] = [None]

    @callback
    def _schedule_dashboard_regen(*_args: object) -> None:
        if _cancel_pending[0] is not None:
            _cancel_pending[0]()
            _cancel_pending[0] = None

        @callback
        def _do_regen(_now: object) -> None:
            _cancel_pending[0] = None
            hass.async_create_task(
                async_create_dashboard(
                    hass,
                    coordinator.areas,
                    coordinator.robot_info,
                    manager=dashboard_manager,
                    device_id=coordinator.device_id,
                )
            )

        _cancel_pending[0] = async_call_later(hass, 5, _do_regen)

    cancel_coordinator_listener = coordinator.async_add_listener(
        _schedule_dashboard_regen
    )
    config_entry.async_on_unload(cancel_coordinator_listener)

    # When rooms change, regenerate dashboard immediately
    @callback
    def _on_areas_changed() -> None:
        LOGGER.debug("Areas changed — immediate dashboard regen")
        dashboard_manager.invalidate()
        hass.async_create_task(
            async_create_dashboard(
                hass,
                coordinator.areas,
                coordinator.robot_info,
                manager=dashboard_manager,
                device_id=coordinator.device_id,
            )
        )

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_AREAS_UPDATED}_{config_entry.entry_id}",
            _on_areas_changed,
        )
    )

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN].pop(f"{entry.entry_id}_dashboard", None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the Lovelace dashboard when the integration is removed."""
    manager = hass.data.get(DOMAIN, {}).pop(f"{entry.entry_id}_dashboard", None)
    if manager is None:
        # Entry was already unloaded — create a temporary manager just for deletion
        manager = RobEyeDashboardManager()
    await manager.async_delete(hass)


async def _async_update_listener(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Handle options update."""
    LOGGER.debug("_async_update_listener: reloading %s", config_entry.entry_id)
    await hass.config_entries.async_reload(config_entry.entry_id)
