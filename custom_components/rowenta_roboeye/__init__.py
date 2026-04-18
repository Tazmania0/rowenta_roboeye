"""Rowenta Xplorer 120 (RobEye) Home Assistant integration."""

from __future__ import annotations

import asyncio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, CoreState, EVENT_HOMEASSISTANT_STARTED, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .api import RobEyeApiClient
from .const import AREA_STATE_BLOCKING, CONF_MAP_ID, CONF_NAME, DEFAULT_DEVICE_NAME, DEFAULT_MAP_ID, DOMAIN, LOGGER, PLATFORMS, SIGNAL_AREAS_UPDATED, VERSION, room_selection_entity_id
from .coordinator import RobEyeCoordinator
from .dashboard import RobEyeDashboardManager, async_create_dashboard
from .frontend import JSModuleRegistration


def _schedule_for_map(
    raw: list[dict] | None,
    active_map_id: str | None,
) -> list[dict] | None:
    """Return only schedule entries that belong to the active map.

    Entries without a map_id are kept (legacy / single-map robots).
    Returns None when the filtered result is empty so callers can treat
    absence and empty-list the same way.
    """
    if not raw:
        return None
    if not active_map_id:
        return raw or None
    filtered = [
        e for e in raw
        if not str(e.get("task", {}).get("map_id", "")).strip()
        or str(e.get("task", {}).get("map_id", "")).strip() == active_map_id
    ]
    return filtered or None


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


async def _async_sync_room_selection_booleans(
    hass: HomeAssistant,
    coordinator: RobEyeCoordinator,
) -> None:
    """Create or remove input_boolean entities for room selection.

    Creates one input_boolean per named, non-blocking room on the active map.
    Removes stale ones when rooms change (map switch, area rename).

    Entity IDs follow the pattern:
      input_boolean.{device_id}_map{map_id}_room_{area_id}_selected
    """
    import json as _json

    device_id = coordinator.device_id
    map_id = coordinator.active_map_id
    areas = coordinator.areas

    # Collect all named, non-blocking rooms
    desired: dict[str, str] = {}  # entity_id → friendly_name
    for area in areas:
        area_id = area.get("id")
        if area_id is None:
            continue
        if area.get("area_state") == AREA_STATE_BLOCKING:
            continue
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            continue
        try:
            meta = _json.loads(meta_raw)
        except Exception:
            continue
        name = meta.get("name", "").strip()
        if not name:
            continue
        eid = room_selection_entity_id(device_id, map_id, str(area_id))
        desired[eid] = f"Select {name}"

    # Use the input_boolean component's storage to create/remove
    component = hass.data.get("input_boolean")
    if component is None:
        LOGGER.debug("input_boolean component not loaded — skipping room selection setup")
        return

    # Get existing selection entity IDs for this device+map
    existing_ids = {
        eid for eid in hass.states.async_entity_ids("input_boolean")
        if eid.startswith(f"input_boolean.{device_id}_map{map_id}_room_")
        and eid.endswith("_selected")
    }

    # Create missing ones
    for eid, friendly_name in desired.items():
        if eid not in existing_ids:
            try:
                item_id = eid.replace("input_boolean.", "")
                await component.async_add_item({
                    "name": friendly_name,
                    "id": item_id,
                    "initial": False,
                    "icon": "mdi:checkbox-marked-circle-outline",
                })
                LOGGER.debug("Created room selection boolean: %s", eid)
            except Exception as err:
                LOGGER.warning("Could not create %s: %s", eid, err)

    # Remove stale ones (rooms removed or map switched)
    stale = existing_ids - set(desired.keys())
    for eid in stale:
        try:
            item_id = eid.replace("input_boolean.", "")
            await component.async_remove_item(item_id)
            LOGGER.debug("Removed stale room selection boolean: %s", eid)
        except Exception as err:
            LOGGER.debug("Could not remove %s: %s", eid, err)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Rowenta RobEye from a config entry."""
    host: str = config_entry.data[CONF_HOST]
    map_id: str = config_entry.data.get(CONF_MAP_ID, DEFAULT_MAP_ID)
    friendly_name: str = config_entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME)

    client = RobEyeApiClient(host=host)

    coordinator = RobEyeCoordinator(
        hass=hass,
        config_entry=config_entry,
        client=client,
        map_id=map_id,
    )
    await coordinator.async_config_entry_first_refresh()

    # Create input_boolean selection entities for all discovered rooms
    await _async_sync_room_selection_booleans(hass, coordinator)

    # Persist the resolved device_id (serial-based) to config entry data so that
    # async_remove_entry can reconstruct the correct dashboard URL path even when
    # hass.data is empty (e.g. setup failed on a later HA restart before removal).
    # Must be done BEFORE add_update_listener is registered to avoid a reload loop.
    _device_id = coordinator.device_id
    if config_entry.data.get("_device_id") != _device_id:
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, "_device_id": _device_id},
        )

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    coordinator.async_start_command_worker()

    # One manager per config entry — holds hash + dashboard object reference.
    # Stored in hass.data so async_remove_entry can call async_delete() on it.
    dashboard_manager = RobEyeDashboardManager(device_id=coordinator.device_id, friendly_name=friendly_name)
    hass.data[DOMAIN][f"{config_entry.entry_id}_dashboard"] = dashboard_manager

    # Launch dashboard creation in the background so setup returns immediately.
    # The helper retries with increasing delays; last resort: request HA restart.
    hass.async_create_task(
        _async_initial_dashboard(hass, config_entry, coordinator, dashboard_manager, friendly_name),
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
                active_map_id=coordinator.active_map_id,
                friendly_name=friendly_name,
                available_maps=coordinator.available_maps,
                schedule_entries=_schedule_for_map(
                    coordinator.schedule.get("schedule"),
                    coordinator.active_map_id,
                ),
            )
        )

    config_entry.async_on_unload(
        coordinator.async_add_listener(_schedule_dashboard_regen)
    )

    # When rooms change, regenerate dashboard immediately and sync selection booleans
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
                active_map_id=coordinator.active_map_id,
                friendly_name=friendly_name,
                available_maps=coordinator.available_maps,
                schedule_entries=_schedule_for_map(
                    coordinator.schedule.get("schedule"),
                    coordinator.active_map_id,
                ),
            )
        )
        hass.async_create_task(
            _async_sync_room_selection_booleans(hass, coordinator)
        )

        # After a deliberate map switch the first dashboard save may race ahead of
        # entity state initialisation, leaving room entities showing "unavailable"
        # briefly.  Schedule a second verification pass 5 s later: if areas still
        # look wrong we force a coordinator refresh; either way we re-render the
        # dashboard so Lovelace picks up the now-stable entity states.
        if getattr(coordinator, "_post_switch_verify_pending", False):
            coordinator._post_switch_verify_pending = False
            LOGGER.debug("RobEye: post-map-switch detected — scheduling 5 s verification")
            hass.async_create_task(
                _async_post_switch_verify(
                    hass, config_entry, coordinator, dashboard_manager, friendly_name
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
    friendly_name: str = DEFAULT_DEVICE_NAME,
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
            active_map_id=coordinator.active_map_id,
            friendly_name=friendly_name,
            available_maps=coordinator.available_maps,
            schedule_entries=_schedule_for_map(
                coordinator.schedule.get("schedule"),
                coordinator.active_map_id,
            ),
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


async def _async_post_switch_verify(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RobEyeCoordinator,
    dashboard_manager: RobEyeDashboardManager,
    friendly_name: str,
) -> None:
    """Secondary check run 5 s after a map switch to fix stale "unavailable" states.

    The first dashboard save races against async entity initialisation — the
    Lovelace config is correct but entities haven't written their state yet.
    Waiting 5 s lets every platform task complete.  We then:
      1. Force a coordinator refresh when areas look stale (handles the robot
         taking more than one poll cycle to return the new map's room data).
      2. Unconditionally invalidate + regenerate the dashboard so Lovelace
         re-reads entity states that are now fully initialised.
    The hash guard inside async_create_dashboard makes step 2 a no-op when the
    room config is already correct.
    """
    await asyncio.sleep(5)

    if config_entry.state not in (
        ConfigEntryState.LOADED,
        ConfigEntryState.SETUP_IN_PROGRESS,
    ):
        return

    areas_stale = (
        not getattr(coordinator, "_areas_ready", True)
        or coordinator.areas_map_id != coordinator.active_map_id
        or not coordinator.areas
    )

    if areas_stale:
        LOGGER.debug(
            "RobEye: post-switch verify — areas stale (ready=%s map_id areas=%s active=%s), forcing refresh",
            getattr(coordinator, "_areas_ready", "?"),
            coordinator.areas_map_id,
            coordinator.active_map_id,
        )
        await coordinator.async_request_refresh()

    dashboard_manager.invalidate()
    await async_create_dashboard(
        hass,
        coordinator.areas,
        coordinator.robot_info,
        manager=dashboard_manager,
        device_id=coordinator.device_id,
        active_map_id=coordinator.active_map_id,
        friendly_name=friendly_name,
        available_maps=coordinator.available_maps,
        schedule_entries=_schedule_for_map(
            coordinator.schedule.get("schedule"),
            coordinator.active_map_id,
        ),
    )
    LOGGER.debug("RobEye: post-switch dashboard verification complete")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    # Grab the dashboard manager before platforms are torn down.
    dashboard_manager: RobEyeDashboardManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_dashboard"
    )

    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator and coordinator._command_worker_task:
        coordinator._command_worker_task.cancel()

    # Remove room selection input_booleans for this entry
    component = hass.data.get("input_boolean")
    if component:
        device_id = entry.data.get("_device_id") or entry.entry_id.lower()
        for eid in list(hass.states.async_entity_ids("input_boolean")):
            if f"_{device_id}_map" in eid and eid.endswith("_selected"):
                try:
                    await component.async_remove_item(eid.replace("input_boolean.", ""))
                except Exception:
                    pass

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Do NOT pop the dashboard manager here.  async_remove_entry (called
        # immediately after on full removal) needs it to find the correct
        # per-device dashboard URL path.  On reload, async_setup_entry
        # overwrites it with a fresh manager.

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
        # HA was restarted before removal — reconstruct from config entry data.
        # Use the persisted _device_id (written by async_setup_entry after the
        # first successful coordinator refresh) so the dashboard URL path matches
        # the one that was actually created.  Fall back to entry_id only when
        # _device_id was never stored (e.g. the entry never set up successfully).
        device_id = entry.data.get("_device_id") or entry.entry_id.lower()
        friendly_name = entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME)
        manager = RobEyeDashboardManager(device_id=device_id, friendly_name=friendly_name)
    await manager.async_delete(hass)


async def _async_update_listener(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Handle options update."""
    LOGGER.debug("_async_update_listener: reloading %s", config_entry.entry_id)
    await hass.config_entries.async_reload(config_entry.entry_id)
