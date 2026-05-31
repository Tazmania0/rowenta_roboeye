"""Tests for the maintenance tracking system."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rowenta_roboeye.const import (
    DROP_SENSOR_CLEAN_M2,
    DUSTBIN_CLEAN_HOURS,
    DUSTBIN_CLEAN_M2,
    MAIN_BRUSH_REPLACE_HOURS,
    MAINTENANCE_NOTIFICATION_PREFIX,
    MAINTENANCE_WARN_PCT,
)
from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator
from custom_components.rowenta_roboeye.maintenance_store import (
    DEFAULT_DATA,
    MaintenanceStore,
)


@pytest.fixture(autouse=True)
def _clear_store_backing():
    """Reset the in-memory Store backing between tests."""
    from homeassistant.helpers.storage import Store

    Store._backing.clear()
    yield
    Store._backing.clear()


def _store(**data) -> MaintenanceStore:
    """Build a MaintenanceStore with given `_data` without touching HA Store."""
    s = MaintenanceStore.__new__(MaintenanceStore)
    s._data = {**dict(DEFAULT_DATA), **data}
    return s


# ── Pure calculation logic ─────────────────────────────────────────────


def test_runtime_calculation():
    """Delta correctly computes hours used since last reset."""
    store = _store(main_brush_replace_baseline_s=36000)
    # current = 108000s (30h), baseline = 36000s (10h) → 20h since replacement
    assert store.runtime_since_replace_h("main_brush", 108000) == pytest.approx(20.0)


def test_area_calculation():
    """Area delta correctly computed in m² (mm² raw / 1e6)."""
    store = _store(dustbin_clean_baseline_mm2=50_000_000)
    # 65 m² total, 50 m² baseline → 15 m² since last empty
    assert store.area_since_clean_m2("dustbin", 65_000_000) == pytest.approx(15.0)


def test_negative_delta_clamped_to_zero():
    """A baseline higher than the current total never yields a negative value."""
    store = _store(main_brush_replace_baseline_s=100000)
    assert store.runtime_since_replace_h("main_brush", 0) == 0.0


def test_dustbin_triggers_on_area():
    """Dustbin alert fires when the area threshold is reached."""
    store = _store(dustbin_clean_baseline_mm2=0, dustbin_clean_baseline_s=0)
    assert store.area_since_clean_m2("dustbin", 16_000_000) >= DUSTBIN_CLEAN_M2


def test_dustbin_triggers_on_time():
    """Dustbin alert fires on time even when area is below threshold."""
    store = _store(dustbin_clean_baseline_mm2=0, dustbin_clean_baseline_s=0)
    assert store.runtime_since_clean_h("dustbin", 9000) >= DUSTBIN_CLEAN_HOURS  # 2.5h
    assert store.area_since_clean_m2("dustbin", 5_000_000) < DUSTBIN_CLEAN_M2


def test_replacement_warns_at_80pct():
    """Warning threshold is reached at 80% of the replacement limit."""
    store = _store(main_brush_replace_baseline_s=0)
    hours = store.runtime_since_replace_h("main_brush", 116 * 3600)  # 116h ≈ 82.8%
    assert hours >= MAIN_BRUSH_REPLACE_HOURS * MAINTENANCE_WARN_PCT / 100
    assert hours < MAIN_BRUSH_REPLACE_HOURS


def test_reset_clears_due_state():
    """Resetting a counter clears the 'due' state (delta back to zero)."""
    store = _store(main_brush_replace_baseline_s=0)
    assert store.runtime_since_replace_h("main_brush", 145 * 3600) > MAIN_BRUSH_REPLACE_HOURS
    store._data["main_brush_replace_baseline_s"] = 145 * 3600
    assert store.runtime_since_replace_h("main_brush", 145 * 3600) == 0.0


# ── async_reset behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_reset_replace_sets_time_baseline():
    store = MaintenanceStore(MagicMock(), "SER-1")
    await store.async_load()
    await store.async_reset("main_brush_replace", current_total_s=99000, current_total_mm2=0)
    assert store.get("main_brush_replace_baseline_s") == 99000
    assert store.last_reset_iso("main_brush_replace") is not None


@pytest.mark.asyncio
async def test_async_reset_dustbin_sets_both_baselines():
    store = MaintenanceStore(MagicMock(), "SER-1")
    await store.async_load()
    await store.async_reset("dustbin_clean", current_total_s=7000, current_total_mm2=4_000_000)
    assert store.get("dustbin_clean_baseline_s") == 7000
    assert store.get("dustbin_clean_baseline_mm2") == 4_000_000


@pytest.mark.asyncio
async def test_async_reset_clean_sets_area_baseline():
    store = MaintenanceStore(MagicMock(), "SER-1")
    await store.async_load()
    await store.async_reset("filter_clean", current_total_s=0, current_total_mm2=12_000_000)
    assert store.get("filter_clean_baseline_mm2") == 12_000_000


# ── Storage persistence ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_storage_survives_reload():
    """Counter persists across store recreation (simulates remove + re-add)."""
    store1 = MaintenanceStore(MagicMock(), "robot-abc")
    await store1.async_load()
    await store1.async_reset("main_brush_replace", current_total_s=36000, current_total_mm2=0)

    store2 = MaintenanceStore(MagicMock(), "robot-abc")
    await store2.async_load()
    assert store2.get("main_brush_replace_baseline_s") == 36000


@pytest.mark.asyncio
async def test_load_seeds_missing_default_keys():
    """Older stores missing new keys get defaults backfilled on load."""
    from homeassistant.helpers.storage import Store

    Store._backing["rowenta_roboeye.maintenance.robot_x"] = {"main_brush_replace_baseline_s": 5}
    store = MaintenanceStore(MagicMock(), "robot-x")
    await store.async_load()
    assert store.get("main_brush_replace_baseline_s") == 5
    assert store.get("filter_clean_baseline_mm2") == 0
    assert store.last_reset_iso("anything") is None


def test_storage_key_is_stable():
    """Storage key is derived from the unique_id, not the config entry id."""
    s = MaintenanceStore(MagicMock(), "SER120-abc123")
    assert "SER120_abc123" in s._store.key
    assert "config_entry" not in s._store.key.lower()


# ── Coordinator notification check ─────────────────────────────────────


def _fake_coordinator(store, perm_stats, has_wet=False):
    return SimpleNamespace(
        maintenance=store,
        hass=MagicMock(),
        has_wet_support=has_wet,
        permanent_statistics=perm_stats,
    )


@pytest.mark.asyncio
async def test_check_notifications_fires_on_due(monkeypatch):
    """A crossed threshold fires a persistent notification with a stable id."""
    from homeassistant.components import persistent_notification

    created = []
    monkeypatch.setattr(
        persistent_notification, "async_create",
        lambda hass, message, **kw: created.append((message, kw.get("notification_id"))),
    )
    store = _store(main_brush_replace_baseline_s=0)
    perm = {"total_cleaning_time": 200 * 3600, "total_area_cleaned": 0}
    fake = _fake_coordinator(store, perm)

    await RobEyeCoordinator._check_maintenance_notifications(fake, perm)

    ids = [nid for _msg, nid in created]
    assert f"{MAINTENANCE_NOTIFICATION_PREFIX}main_brush_replace" in ids


@pytest.mark.asyncio
async def test_check_notifications_noop_without_store(monkeypatch):
    """No notifications fire when the maintenance store is unavailable."""
    from homeassistant.components import persistent_notification

    created = []
    monkeypatch.setattr(
        persistent_notification, "async_create",
        lambda *a, **k: created.append(a),
    )
    fake = _fake_coordinator(None, {"total_cleaning_time": 9_999_999})
    await RobEyeCoordinator._check_maintenance_notifications(fake, None)
    assert created == []


@pytest.mark.asyncio
async def test_check_notifications_mop_only_when_wet(monkeypatch):
    """Mop pad notification only fires for wet-capable robots."""
    from homeassistant.components import persistent_notification

    created = []
    monkeypatch.setattr(
        persistent_notification, "async_create",
        lambda hass, message, **kw: created.append(kw.get("notification_id")),
    )
    store = _store(mop_pad_replace_baseline_s=0)
    perm = {"total_cleaning_time": 200 * 3600, "total_area_cleaned": 0}

    await RobEyeCoordinator._check_maintenance_notifications(
        _fake_coordinator(store, perm, has_wet=False), perm
    )
    assert f"{MAINTENANCE_NOTIFICATION_PREFIX}mop_pad_replace" not in created

    created.clear()
    await RobEyeCoordinator._check_maintenance_notifications(
        _fake_coordinator(store, perm, has_wet=True), perm
    )
    assert f"{MAINTENANCE_NOTIFICATION_PREFIX}mop_pad_replace" in created


# ── Entity builders ────────────────────────────────────────────────────


def _builder_coordinator(has_wet=False):
    return SimpleNamespace(
        has_wet_support=has_wet,
        device_id="ser120",
        maintenance=None,
        permanent_statistics={},
    )


def test_sensor_builder_excludes_mop_for_dry_models():
    from custom_components.rowenta_roboeye.sensor import build_maintenance_sensors

    sensors = build_maintenance_sensors(_builder_coordinator())
    uids = [s._attr_unique_id for s in sensors]
    assert uids  # something was built
    assert not any("mop_pad" in u for u in uids)


def test_binary_and_button_builders_exclude_mop():
    from custom_components.rowenta_roboeye.binary_sensor import (
        build_maintenance_due_sensors,
    )
    from custom_components.rowenta_roboeye.button import build_maintenance_buttons

    c = _builder_coordinator()
    bin_uids = [e._attr_unique_id for e in build_maintenance_due_sensors(c)]
    btn_uids = [e._attr_unique_id for e in build_maintenance_buttons(c)]
    assert bin_uids and btn_uids
    assert not any("mop_pad" in u for u in bin_uids)
    assert not any("mop_pad" in u for u in btn_uids)


def test_builders_include_mop_when_wet_supported():
    """Mop-pad entities are created when the robot has a (passive) mop pad."""
    from custom_components.rowenta_roboeye.binary_sensor import (
        build_maintenance_due_sensors,
    )
    from custom_components.rowenta_roboeye.button import build_maintenance_buttons
    from custom_components.rowenta_roboeye.sensor import build_maintenance_sensors

    c = _builder_coordinator(has_wet=True)
    sensor_uids = [e._attr_unique_id for e in build_maintenance_sensors(c)]
    bin_uids = [e._attr_unique_id for e in build_maintenance_due_sensors(c)]
    btn_uids = [e._attr_unique_id for e in build_maintenance_buttons(c)]
    assert any("mop_pad" in u for u in sensor_uids)
    assert any("mop_pad" in u for u in bin_uids)
    assert any("mop_pad" in u for u in btn_uids)


def test_serie_120_has_mop_pad_support():
    """The Serie 120 reports mop-pad (passive wet) support for maintenance."""
    coordinator = RobEyeCoordinator.__new__(RobEyeCoordinator)
    assert coordinator.has_wet_support is True
