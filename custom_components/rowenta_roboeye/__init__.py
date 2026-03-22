"""Rowenta Xplorer 120 (RobEye) Home Assistant integration."""

from __future__ import annotations

import asyncio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, CoreState, EVENT_HOMEASSISTANT_STARTED, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .api import RobEyeApiClient
from .const import CONF_MAP_ID, DOMAIN, LOGGER, PLATFORMS, SIGNAL_AREAS_UPDATED, VERSION
from .coordinator import RobEyeCoordinator
from .dashboard import RobEyeDashboardManager, async_create_dashboard
from .frontend import JSModuleRegistration


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register frontend resources once per integration load.

    Must be in async_setup (not async_setup_entry) so registration
    happens once regardless of how many config entries exist.
    """
    async def _register_frontend(_event: object = None) -> None:
        reg = JSModuleRegistration(hass, VERSION)
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

    # Launch dashboard creation in the background so setup returns immediately.
    # The helper retries with increasing delays; last resort: request HA restart.
    hass.async_create_task(
        _async_initial_dashboard(hass, config_entry, coordinator, dashboard_manager),
        eager_start=False,
    )

    # Regenerate dashboard on every coordinator update.
    # async_create_dashboard is a no-op when config hasn't changed (hash-based dedup).
    @callback
    def _schedule_dashboard_regen(*_args: object) -> None:
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
        coordinator.async_add_listener(_schedule_dashboard_regen)
    )

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


_DASHBOARD_RETRY_DELAYS = (0, 2, 5, 15, 30)  # seconds between attempts


async def _async_initial_dashboard(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    dashboard_manager: RobEyeDashboardManager,
) -> None:
    """Create the dashboard in the background with retries.

    Tries up to len(_DASHBOARD_RETRY_DELAYS) times with increasing sleep
    delays between attempts.  If all attempts fail, requests an HA restart
    as a last resort so the next boot picks up the persisted registry entry.
    """
    for attempt, delay in enumerate(_DASHBOARD_RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)

        # Abort if the entry was removed while we were waiting
        if config_entry.state not in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_IN_PROGRESS,
        ):
            LOGGER.debug("RobEye: entry no longer loaded — cancelling dashboard init")
            return

        success = await async_create_dashboard(
            hass,
            coordinator.areas,
            coordinator.robot_info,
            manager=dashboard_manager,
            device_id=coordinator.device_id,
        )

        if success:
            # Ensure sidebar visibility is restored (may have been hidden on disable)
            await dashboard_manager.async_set_sidebar_visible(hass, True)
            LOGGER.info("RobEye: dashboard ready (attempt %d)", attempt)
            return

        LOGGER.warning(
            "RobEye: dashboard creation attempt %d/%d failed",
            attempt, len(_DASHBOARD_RETRY_DELAYS),
        )

    # All retries exhausted — notify the user that a manual restart is needed.
    # We do NOT restart automatically; that must be user-approved.
    LOGGER.error(
        "RobEye: dashboard could not be created after %d attempts — "
        "restart required (user action needed)",
        len(_DASHBOARD_RETRY_DELAYS),
    )
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "Rowenta RobEye — Restart Required",
            "message": (
                "The Rowenta dashboard could not be created automatically "
                f"after {len(_DASHBOARD_RETRY_DELAYS)} attempts.\n\n"
                "Please **restart Home Assistant** to complete the setup."
            ),
            "notification_id": "rowenta_roboeye_restart_required",
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    # Grab the dashboard manager before platforms are torn down.
    dashboard_manager: RobEyeDashboardManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_dashboard"
    )

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop(f"{entry.entry_id}_dashboard", None)

        # HA sets entry.disabled_by before calling async_unload_entry when a
        # device or entry is disabled — this is the reliable signal that we
        # should hide the dashboard (not just a reload or HA shutdown).
        # We hide instead of delete to preserve user customizations.
        if dashboard_manager is not None and entry.disabled_by is not None:
            LOGGER.info(
                "RobEye: entry disabled (%s) — hiding dashboard",
                entry.disabled_by,
            )
            await dashboard_manager.async_set_sidebar_visible(hass, False)

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
