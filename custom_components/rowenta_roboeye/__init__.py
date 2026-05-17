"""Rowenta Xplorer 120 (RobEye) Home Assistant integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, CoreState, EVENT_HOMEASSISTANT_STARTED, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import RobEyeApiClient
from .const import AREA_STATE_BLOCKING, CONF_LAST_ACTIVE_MAP, CONF_MAP_ID, CONF_NAME, CONF_SERIAL, DEFAULT_DEVICE_NAME, DEFAULT_MAP_ID, DOMAIN, LOGGER, PLATFORMS, SIGNAL_ACTIVE_MAP_CHANGED, SIGNAL_AREAS_UPDATED, SIGNAL_MAPS_UPDATED, VERSION, room_selection_entity_id
from .coordinator import RobEyeCoordinator
from .dashboard import RobEyeDashboardManager, async_create_dashboard
from .frontend import JSModuleRegistration


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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
    # CONF_LAST_ACTIVE_MAP is written silently whenever the user switches maps via
    # the Select entity.  It takes priority over CONF_MAP_ID so the coordinator
    # starts with the correct map immediately — preventing the sensor from writing
    # the CONF_MAP_ID name as its initial state only to flip to the restored map a
    # tick later, which produced a spurious "Active map changed to X" logbook entry
    # on every HA restart.
    map_id: str = (
        config_entry.data.get(CONF_LAST_ACTIVE_MAP)
        or config_entry.data.get(CONF_MAP_ID, DEFAULT_MAP_ID)
    )
    friendly_name: str = config_entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME)

    client = RobEyeApiClient(host=host)

    coordinator = RobEyeCoordinator(
        hass=hass,
        config_entry=config_entry,
        client=client,
        map_id=map_id,
    )
    await coordinator.async_config_entry_first_refresh()

    # Pre-fetch areas for every permanent map so all per-map entities can be
    # created at setup time.  Errors on individual maps are non-fatal.
    await coordinator.async_load_all_map_areas()

    # Create input_boolean selection entities for all discovered rooms
    await _async_sync_room_selection_booleans(hass, coordinator)

    # Persist the resolved device_id to config entry data so that:
    #  - async_remove_entry can reconstruct the correct dashboard URL path.
    #  - CONF_SERIAL is available on the next HA restart, giving every entity
    #    the same unique_id suffix from day one (upgrade path for installs that
    #    pre-date the config-flow serial fetch).
    # Must happen BEFORE add_update_listener is registered to avoid a reload loop.
    _device_id = coordinator.device_id
    _serial = coordinator._stable_device_id  # already normalised; "" if unknown
    _data_patch: dict = {}
    if config_entry.data.get("_device_id") != _device_id:
        _data_patch["_device_id"] = _device_id
    if _serial and config_entry.data.get(CONF_SERIAL) != _serial:
        _data_patch[CONF_SERIAL] = _serial
    if _data_patch:
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, **_data_patch},
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
    # Use async_create_background_task (not async_create_task) so HA does NOT
    # track this task during startup — the task waits for EVENT_HOMEASSISTANT_STARTED
    # before doing real work, and async_create_task would cause a deadlock where
    # HA blocks on this task while this task blocks waiting for HA to start.
    _dashboard_init_task = hass.async_create_background_task(
        _async_initial_dashboard(hass, config_entry, coordinator, dashboard_manager, friendly_name),
        name="rowenta_roboeye_dashboard_init",
        eager_start=False,
    )

    @callback
    def _cancel_dashboard_init() -> None:
        if not _dashboard_init_task.done():
            _dashboard_init_task.cancel()

    config_entry.async_on_unload(_cancel_dashboard_init)

    # ── Shared dashboard rebuild helper ───────────────────────────────────
    # Single code path that constructs the rebuild kwargs, preventing the two
    # trigger paths (areas-changed, active-map-changed, maps-changed).

    def _dashboard_rebuild_kwargs() -> dict:
        """Read everything fresh from the coordinator at call time.

        Called at each retry attempt so a state change landing mid-rebuild
        produces correct YAML on the retry.
        """
        return dict(
            hass=hass,
            areas=coordinator.areas,
            robot_info=coordinator.robot_info,
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

    async def _rebuild_dashboard_safe() -> None:
        """Rebuild dashboard with bounded retry (2 / 4 / 8 / 16 s)."""
        for attempt in range(4):
            if config_entry.state not in (
                ConfigEntryState.LOADED,
                ConfigEntryState.SETUP_IN_PROGRESS,
            ):
                return
            success = await async_create_dashboard(**_dashboard_rebuild_kwargs())
            if success:
                return
            await asyncio.sleep(2.0 * (2 ** attempt))
        LOGGER.warning(
            "RobEye: dashboard rebuild for map %s failed after 4 attempts",
            coordinator.active_map_id,
        )

    # ── Trigger 1: snapshot diff — areas changed for some map ────────────
    @callback
    def _on_areas_updated(map_id: str) -> None:
        """A map's areas changed (rename / add / remove).

        Inactive maps: platform listeners update entities silently; no YAML
        rebuild required.  Active map: rebuild YAML so rooms view reflects
        new state.
        """
        if map_id != coordinator.active_map_id:
            return
        LOGGER.debug(
            "Active map %s areas changed — rebuilding dashboard", map_id,
        )
        dashboard_manager.invalidate()
        # Sync room-selection helpers independently (non-blocking).
        hass.async_create_task(
            _async_sync_room_selection_booleans(hass, coordinator)
        )
        hass.async_create_task(_rebuild_dashboard_safe())

    # ── Trigger 2: user selected a different map ─────────────────────────
    @callback
    def _on_active_map_changed(map_id: str) -> None:
        """User switched map. Entities for all maps exist; availability
        already flipped via async_update_listeners() inside
        async_set_active_map. We only need to rebuild the YAML once."""
        LOGGER.debug(
            "Active map changed to %s — rebuilding dashboard", map_id,
        )
        dashboard_manager.invalidate()
        hass.async_create_task(_rebuild_dashboard_safe())

    # ── Trigger 3: map added or removed on the device ────────────────────
    @callback
    def _on_maps_updated(payload) -> None:
        added = payload.get("added", set()) if isinstance(payload, dict) else set()
        removed = payload.get("removed", set()) if isinstance(payload, dict) else payload
        if added:
            LOGGER.info("Maps added: %s", added)
        if removed:
            LOGGER.info("Maps removed: %s", removed)
        dashboard_manager.invalidate()
        hass.async_create_task(_rebuild_dashboard_safe())

    # ── Trigger 4: schedule-only change (no area / map change) ───────────
    # Schedule updates that don't accompany an area change still need to
    # refresh the Control view.  Hash the *filtered* schedule (keyed by
    # active map) so that a map switch alone — which changes the filtered
    # subset but not the raw blob — also triggers a rebuild.
    def _current_sched_key() -> str:
        raw = coordinator.schedule.get("schedule") or []
        filtered = _schedule_for_map(raw, coordinator.active_map_id) or []
        payload = {"map": coordinator.active_map_id, "rows": filtered}
        try:
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
        except Exception:
            return ""

    _last_sched_hash = _current_sched_key()

    @callback
    def _on_coord_update() -> None:
        """Fires on every coordinator listener tick; only acts when the
        filtered schedule (or active-map) hash differs from last build."""
        nonlocal _last_sched_hash
        sig = _current_sched_key()
        if sig == _last_sched_hash and dashboard_manager._last_hash is not None:
            return
        if dashboard_manager._save_lock.locked():
            return
        _last_sched_hash = sig
        hass.async_create_task(_rebuild_dashboard_safe())

    config_entry.async_on_unload(
        coordinator.async_add_listener(_on_coord_update)
    )

    # Wire the three dispatcher signals
    for _sig, _handler in (
        (SIGNAL_AREAS_UPDATED,      _on_areas_updated),
        (SIGNAL_ACTIVE_MAP_CHANGED, _on_active_map_changed),
        (SIGNAL_MAPS_UPDATED,       _on_maps_updated),
    ):
        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"{_sig}_{config_entry.entry_id}",
                _handler,
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
    # On a cold HA boot the integration sets up before Lovelace is fully
    # initialised, so hass.data[LOVELACE_DATA] is not yet populated and
    # _async_get_lovelace_store returns None on every attempt.  Wait for
    # EVENT_HOMEASSISTANT_STARTED before starting the retry loop so that
    # all 5 attempts are spent on real failures, not a timing race.
    if hass.state != CoreState.running:
        _ha_started = asyncio.Event()

        @callback
        def _on_ha_started(_event: object = None) -> None:
            _ha_started.set()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)
        await _ha_started.wait()

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
    """Handle config entry update.

    Reloads the integration when host changes (new API endpoint needed).
    Silently skips reload for data-only writes that do not affect connectivity —
    specifically CONF_LAST_ACTIVE_MAP updates written by the map Select entity
    and serial/device-id caching written by async_setup_entry.  Those writes
    must not trigger a reload loop.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if coordinator is not None:
        if coordinator.client._host == config_entry.data.get(CONF_HOST):
            LOGGER.debug(
                "_async_update_listener: host unchanged — skipping reload for %s",
                config_entry.entry_id,
            )
            return
    LOGGER.debug("_async_update_listener: reloading %s", config_entry.entry_id)
    await hass.config_entries.async_reload(config_entry.entry_id)
