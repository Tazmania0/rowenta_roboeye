"""Rowenta Xplorer 120 (RobEye) Home Assistant integration."""

from __future__ import annotations

import asyncio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import Event, HomeAssistant, CoreState, EVENT_HOMEASSISTANT_STARTED, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later, async_track_device_registry_updated_event
from homeassistant.helpers.typing import ConfigType

from .api import RobEyeApiClient
from .const import CONF_MAP_ID, DOMAIN, LOGGER, PLATFORMS, SIGNAL_AREAS_UPDATED, VERSION
from .coordinator import RobEyeCoordinator
from .dashboard import RobEyeDashboardManager, async_create_dashboard
from .frontend import JSModuleRegistration


def _is_device_disabled(hass: HomeAssistant, integration_device_id: str) -> bool:
    """Return True if the HA device for this integration is disabled."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, integration_device_id)})
    return device is not None and device.disabled_by is not None


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

    # Hide/show dashboard sidebar when the device is disabled/enabled in HA.
    # Use async_track_device_registry_updated_event (the HA-recommended API) so
    # events are pre-filtered to our specific device — no manual device_id check
    # needed.  The event action is "update" (not "updated") per the HA device
    # registry TypedDict definition.
    _dev_registry = dr.async_get(hass)
    _ha_device = _dev_registry.async_get_device(
        identifiers={(DOMAIN, coordinator.device_id)}
    )

    if _ha_device is not None:
        @callback
        def _on_device_registry_updated(event: Event) -> None:
            LOGGER.debug(
                "RobEye: device-registry event received — action=%r changes=%r",
                event.data.get("action"),
                list(event.data.get("changes", {}).keys()),
            )
            if event.data.get("action") != "update":
                LOGGER.debug(
                    "RobEye: ignoring device-registry event — action %r is not 'update'",
                    event.data.get("action"),
                )
                return
            if "disabled_by" not in event.data.get("changes", {}):
                LOGGER.debug(
                    "RobEye: ignoring device-registry event — 'disabled_by' not in changes %r",
                    list(event.data.get("changes", {}).keys()),
                )
                return
            device = dr.async_get(hass).async_get_device(
                identifiers={(DOMAIN, coordinator.device_id)}
            )
            if device is None:
                LOGGER.warning(
                    "RobEye: device-registry event for '%s' — device no longer found, skipping sidebar update",
                    coordinator.device_id,
                )
                return
            visible = device.disabled_by is None
            LOGGER.warning(
                "RobEye: device %s — updating dashboard sidebar visibility"
                " (handled by device-registry event listener)",
                "enabled" if visible else "disabled",
            )
            hass.async_create_task(
                dashboard_manager.async_set_sidebar_visible(hass, visible)
            )

        config_entry.async_on_unload(
            async_track_device_registry_updated_event(
                hass, _ha_device.id, _on_device_registry_updated
            )
        )
    else:
        LOGGER.warning(
            "RobEye: HA device not found for '%s' — sidebar hide/show on disable will not work",
            coordinator.device_id,
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
            LOGGER.info("RobEye: dashboard ready (attempt %d)", attempt)
            # If the device was already disabled before this boot, hide the panel now.
            if _is_device_disabled(hass, coordinator.device_id):
                await dashboard_manager.async_set_sidebar_visible(hass, False)
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
    # Grab references before platforms are torn down so we can hide the
    # dashboard sidebar if the device was disabled (HA unloads the config
    # entry when a device is disabled, which cancels the device-registry
    # event listener before it can fire — so we must act here instead).
    coordinator: RobEyeCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
    dashboard_manager: RobEyeDashboardManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_dashboard"
    )

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN].pop(f"{entry.entry_id}_dashboard", None)

        if (
            coordinator is not None
            and dashboard_manager is not None
            and _is_device_disabled(hass, coordinator.device_id)
        ):
            LOGGER.debug(
                "RobEye: device is disabled — hiding dashboard sidebar on entry unload"
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
