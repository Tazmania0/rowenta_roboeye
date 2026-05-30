"""Unit tests for integration setup/teardown lifecycle (async_unload_entry)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye import async_unload_entry
from custom_components.rowenta_roboeye.const import DOMAIN


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
