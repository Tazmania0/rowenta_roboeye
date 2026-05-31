"""Unit tests for integration setup/teardown lifecycle (async_unload_entry)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.rowenta_roboeye import (
    _async_initial_dashboard,
    _async_update_listener,
    _schedule_for_map,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.rowenta_roboeye.const import DOMAIN, safe_int
from homeassistant.const import CONF_HOST
from custom_components.rowenta_roboeye.frontend import (
    _read_module_version,
    _version_from_url,
)


# ── _schedule_for_map (active-map schedule filtering) ──────────────────

def _sched(map_id):
    return {"task": {"map_id": map_id}}


def test_schedule_for_map_none_and_empty():
    assert _schedule_for_map(None, "3") is None
    assert _schedule_for_map([], "3") is None


def test_schedule_for_map_no_active_map_returns_all():
    rows = [_sched("3"), _sched("4")]
    assert _schedule_for_map(rows, "") == rows


def test_schedule_for_map_filters_foreign_maps():
    rows = [_sched("3"), _sched("4"), _sched("3")]
    out = _schedule_for_map(rows, "3")
    assert out == [_sched("3"), _sched("3")]


def test_schedule_for_map_keeps_entries_without_map_id():
    # Legacy/single-map robots: entries with no map_id are always kept.
    rows = [{"task": {}}, _sched("4")]
    assert _schedule_for_map(rows, "3") == [{"task": {}}]


def test_schedule_for_map_all_foreign_returns_none():
    assert _schedule_for_map([_sched("4")], "3") is None


# ── _async_update_listener (host-change reload gating) ─────────────────

@pytest.mark.asyncio
async def test_update_listener_skips_reload_when_host_unchanged():
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    coord = MagicMock()
    coord.client._host = "192.168.1.50"
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "192.168.1.50"}
    hass.data = {DOMAIN: {"e1": coord}}

    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_update_listener_reloads_when_host_changes():
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    coord = MagicMock()
    coord.client._host = "192.168.1.50"
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "192.168.1.99"}   # changed
    hass.data = {DOMAIN: {"e1": coord}}

    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with("e1")


@pytest.mark.asyncio
async def test_update_listener_reloads_when_no_coordinator():
    """No coordinator yet (e.g. first setup) → reload proceeds."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "192.168.1.50"}
    hass.data = {DOMAIN: {}}

    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with("e1")


# ── _async_initial_dashboard (abort when entry no longer loaded) ───────

@pytest.mark.asyncio
async def test_initial_dashboard_aborts_when_entry_not_loaded():
    """If the entry was unloaded before the first attempt, no dashboard write
    is made."""
    from custom_components.rowenta_roboeye import CoreState
    import custom_components.rowenta_roboeye as init_mod

    hass = MagicMock()
    hass.state = CoreState.running          # skip the wait-for-HA-started branch
    entry = MagicMock()
    entry.state = "not_loaded_sentinel"     # not in (LOADED, SETUP_IN_PROGRESS)
    coordinator = MagicMock()
    manager = MagicMock()

    create = AsyncMock(return_value=True)
    with patch.object(init_mod, "async_create_dashboard", create):
        await _async_initial_dashboard(hass, entry, coordinator, manager, "Robot")

    create.assert_not_called()


def test_safe_int_coerces_and_defaults():
    assert safe_int("3") == 3
    assert safe_int(5) == 5
    assert safe_int("") == 0
    assert safe_int(None) == 0
    assert safe_int("abc") == 0
    assert safe_int("", 9) == 9
    assert safe_int(None, -1) == -1


def test_read_module_version_extracts_card_version():
    """The cache-bust version is read from the card's own const VERSION."""
    ver = _read_module_version("rowenta-map-card.js", "1.0.0")
    # Real card declares a 2.x version; must not fall back to the integration one.
    assert ver != "1.0.0"
    assert ver.split(".")[0].isdigit()


def test_read_module_version_falls_back_when_missing():
    assert _read_module_version("does-not-exist.js", "9.9.9") == "9.9.9"


def test_version_from_url_parses_query():
    assert _version_from_url("/rowenta_roboeye/x.js?v=2.7.6") == "2.7.6"
    assert _version_from_url("/rowenta_roboeye/x.js?a=1&v=3.0") == "3.0"


def test_version_from_url_handles_missing_query():
    # Old naive split('?v=')[-1] returned the whole URL here — must be None now.
    assert _version_from_url("/rowenta_roboeye/x.js") is None


@pytest.mark.asyncio
async def test_setup_checks_maintenance_notifications_after_store_load():
    """Setup re-checks maintenance due state after the persistent store loads."""
    import custom_components.rowenta_roboeye as init_mod

    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_update_entry = MagicMock()

    def _background_task(coro, **_kwargs):
        coro.close()
        task = MagicMock()
        task.done.return_value = True
        return task

    hass.async_create_background_task = _background_task

    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.data = {CONF_HOST: "192.168.1.100", "map_id": "3"}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())

    coord = MagicMock()
    coord.async_config_entry_first_refresh = AsyncMock()
    coord.async_load_all_map_areas = AsyncMock()
    coord.async_init_maintenance = AsyncMock()
    coord._check_maintenance_notifications = AsyncMock()
    coord.async_start_command_worker = MagicMock()
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    coord.device_id = "ser120"
    coord._stable_device_id = "ser120"
    coord.areas = []
    coord.robot_info = {}
    coord.active_map_id = "3"
    coord.available_maps = []
    coord.schedule = {}

    with patch.object(init_mod, "RobEyeCoordinator", return_value=coord):
        assert await async_setup_entry(hass, entry) is True

    coord.async_init_maintenance.assert_awaited_once()
    coord._check_maintenance_notifications.assert_awaited_once_with()


def _make_hass(unload_ok: bool):
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=unload_ok)
    return hass


def _make_entry(entry_id: str = "abc123"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.disabled_by = None
    return entry


async def _make_worker_task():
    """A real, long-running task standing in for the command-queue worker."""
    async def _run():
        await asyncio.sleep(3600)

    return asyncio.ensure_future(_run())


@pytest.mark.asyncio
async def test_unload_cancels_and_awaits_worker_after_successful_unload():
    """On clean unload the worker is cancelled, awaited, and the entry popped."""
    hass = _make_hass(unload_ok=True)
    entry = _make_entry()

    worker = await _make_worker_task()
    coordinator = MagicMock()
    coordinator._command_worker_task = worker
    hass.data[DOMAIN][entry.entry_id] = coordinator

    result = await async_unload_entry(hass, entry)

    assert result is True
    # Worker was cancelled and has actually finished (awaited to completion).
    assert worker.cancelled() or worker.done()
    # Coordinator removed from hass.data.
    assert entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_unload_keeps_worker_running_when_platform_unload_fails():
    """If platform unload fails the entry stays loaded — worker is left alive."""
    hass = _make_hass(unload_ok=False)
    entry = _make_entry()

    worker = await _make_worker_task()
    coordinator = MagicMock()
    coordinator._command_worker_task = worker
    hass.data[DOMAIN][entry.entry_id] = coordinator

    result = await async_unload_entry(hass, entry)

    assert result is False
    # Worker untouched; coordinator still registered.
    assert not worker.done()
    assert hass.data[DOMAIN][entry.entry_id] is coordinator

    worker.cancel()


@pytest.mark.asyncio
async def test_unload_succeeds_when_no_worker_task():
    """Unload must not blow up when the worker was never started."""
    hass = _make_hass(unload_ok=True)
    entry = _make_entry()

    coordinator = MagicMock()
    coordinator._command_worker_task = None
    hass.data[DOMAIN][entry.entry_id] = coordinator

    result = await async_unload_entry(hass, entry)

    assert result is True
    assert entry.entry_id not in hass.data[DOMAIN]
