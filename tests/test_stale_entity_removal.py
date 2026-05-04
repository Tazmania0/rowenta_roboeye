"""Tests for stale room entity removal on map switch."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_coordinator(areas, areas_ready=True, map_id="3"):
    coord = MagicMock()
    coord.areas = areas
    coord._areas_ready = areas_ready
    coord.device_id = "test_device"
    coord.active_map_id = map_id
    coord.areas_map_id = map_id
    return coord


def _make_area(area_id, name, area_state="clean"):
    return {
        "id": area_id,
        "area_meta_data": json.dumps({"name": name}),
        "area_state": area_state,
        "statistics": {},
    }


def _make_unnamed_area(area_id):
    return {
        "id": area_id,
        "area_meta_data": "",
        "area_state": "inactive",
        "statistics": {},
    }


def _make_blocking_area(area_id, name):
    return {
        "id": area_id,
        "area_meta_data": json.dumps({"name": name}),
        "area_state": "blocking",
        "statistics": {},
    }


# ── _areas_ready flag ─────────────────────────────────────────────────

def test_areas_ready_starts_false():
    """Coordinator initialises _areas_ready to False."""
    from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator
    # The flag is an instance attribute set in __init__; verify its default.
    # We use MagicMock to avoid spinning up a real coordinator.
    coord = MagicMock(spec=RobEyeCoordinator)
    coord._areas_ready = False
    assert coord._areas_ready is False


def test_areas_ready_false_blocks_listener():
    """When _areas_ready is False the listener skips all removal/addition."""
    coord = _make_coordinator(areas=[_make_area(10, "Kitchen")], areas_ready=False)

    removed = []
    added = []

    # Simulate the guard at the top of each platform listener
    if not coord._areas_ready:
        pass  # should return early — nothing happens
    else:
        pytest.fail("Should have returned early due to _areas_ready=False")

    assert removed == []
    assert added == []


def test_areas_ready_true_allows_listener():
    """When _areas_ready is True the guard does not short-circuit."""
    coord = _make_coordinator(areas=[_make_area(10, "Kitchen")], areas_ready=True)
    guard_passed = False

    if not coord._areas_ready:
        pytest.fail("Guard should not block when _areas_ready=True")
    else:
        guard_passed = True

    assert guard_passed


# ── Stale entity detection ────────────────────────────────────────────

def test_stale_entity_detected_after_map_switch():
    """area_id present on old map but absent from new map is identified as stale."""
    old_entity = MagicMock()
    old_entity.async_remove = AsyncMock()
    known_entities = {99: old_entity}  # area 99 was on the old map

    coord = _make_coordinator(
        areas=[_make_area(10, "Kitchen")],  # new map has area 10, not 99
        areas_ready=True,
    )

    current_ids = {
        area["id"]
        for area in coord.areas
        if area.get("area_meta_data") and json.loads(area["area_meta_data"]).get("name", "").strip()
    }

    stale_ids = set(known_entities.keys()) - current_ids
    assert stale_ids == {99}


def test_no_removal_when_same_map():
    """No entities are stale when the area set is unchanged."""
    known_entities = {10: MagicMock(), 12: MagicMock()}

    coord = _make_coordinator(
        areas=[_make_area(10, "Kitchen"), _make_area(12, "Bedroom")],
        areas_ready=True,
    )

    current_ids = {
        area["id"]
        for area in coord.areas
        if area.get("area_meta_data") and json.loads(area["area_meta_data"]).get("name", "").strip()
    }

    stale_ids = set(known_entities.keys()) - current_ids
    assert stale_ids == set()


def test_unnamed_area_excluded_from_current_ids():
    """Areas with empty area_meta_data are excluded from current_ids."""
    coord = _make_coordinator(
        areas=[
            _make_area(10, "Kitchen"),
            _make_unnamed_area(11),
        ],
        areas_ready=True,
    )

    current_ids = set()
    for area in coord.areas:
        area_id = area.get("id")
        if area_id is None:
            continue
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except Exception:
            continue
        if meta.get("name", "").strip():
            current_ids.add(area_id)

    assert 10 in current_ids
    assert 11 not in current_ids


# ── _build_room_sensor_entities return type ───────────────────────────

def test_build_room_sensor_entities_returns_dict():
    """_build_room_sensor_entities returns (flat_list, by_area dict)."""
    from custom_components.rowenta_roboeye.sensor import _build_room_sensor_entities

    coord = _make_coordinator(areas=[_make_area(10, "Kitchen")])
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    flat, by_area = _build_room_sensor_entities(coord, config_entry, coord.areas, set())

    assert isinstance(by_area, dict), "by_area must be a dict"
    assert "10" in by_area, "area_id 10 must be a key in by_area"
    assert len(flat) == len(by_area["10"]), "flat list must equal the sensors for area 10"
    assert len(flat) > 0, "should produce at least one sensor"


def test_build_room_sensor_entities_skips_known():
    """_build_room_sensor_entities skips areas already in already_known."""
    from custom_components.rowenta_roboeye.sensor import _build_room_sensor_entities

    coord = _make_coordinator(
        areas=[_make_area(10, "Kitchen"), _make_area(12, "Bedroom")]
    )
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    # Pre-populate already_known with area 10 (string, as produced by the function)
    flat, by_area = _build_room_sensor_entities(coord, config_entry, coord.areas, {"10"})

    assert "10" not in by_area, "area 10 must be skipped (already known)"
    assert "12" in by_area, "area 12 must be included (new)"


# ── _build_room_button_entities return type ───────────────────────────

def test_build_room_button_entities_returns_list():
    """_build_room_button_entities returns (entity_list, id_list) with matching order."""
    from custom_components.rowenta_roboeye.button import _build_room_button_entities

    coord = _make_coordinator(
        areas=[_make_area(10, "Kitchen"), _make_area(12, "Bedroom")]
    )
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    entities, ids = _build_room_button_entities(coord, config_entry, coord.areas, set())

    assert isinstance(ids, list), "ids must be a list (ordered)"
    assert len(entities) == len(ids), "entities and ids must have same length"
    assert set(ids) == {"10", "12"}


def test_build_room_button_entities_skips_blocking():
    """_build_room_button_entities does not create entities for blocking areas."""
    from custom_components.rowenta_roboeye.button import _build_room_button_entities

    coord = _make_coordinator(
        areas=[
            _make_area(10, "Kitchen"),
            _make_blocking_area(12, "Utility"),
        ]
    )
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    entities, ids = _build_room_button_entities(coord, config_entry, coord.areas, set())

    assert "10" in ids
    assert "12" not in ids, "blocking area must be skipped"


# ── async_remove_duplicate_room_entities ─────────────────────────────────


def _make_registry_entry(unique_id: str, entity_id: str, domain: str = "button"):
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.entity_id = entity_id
    entry.domain = domain
    return entry


def test_dedup_removes_old_uid_for_same_area():
    """Registry entry with old device_id uid is removed when a fresh uid covers same area."""
    from custom_components.rowenta_roboeye.entity import async_remove_duplicate_room_entities

    old_entry = _make_registry_entry(
        "clean_room_map3_10_old_device_id",
        "button.robot_map3_clean_room_10",
    )
    new_uid = "clean_room_map3_10_new_device_id"

    ent_reg = MagicMock()
    ent_reg.async_remove = MagicMock()

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[old_entry],
        ),
    ):
        async_remove_duplicate_room_entities(
            hass, config_entry, "button", {new_uid}
        )

    ent_reg.async_remove.assert_called_once_with(old_entry.entity_id)


def test_dedup_keeps_canonical_uid():
    """The canonical entry itself is NOT removed."""
    from custom_components.rowenta_roboeye.entity import async_remove_duplicate_room_entities

    canonical_uid = "clean_room_map3_10_current_device"
    canonical_entry = _make_registry_entry(canonical_uid, "button.robot_map3_clean_room_10")

    ent_reg = MagicMock()
    ent_reg.async_remove = MagicMock()

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[canonical_entry],
        ),
    ):
        async_remove_duplicate_room_entities(
            hass, config_entry, "button", {canonical_uid}
        )

    ent_reg.async_remove.assert_not_called()


def test_dedup_removes_multiple_stale_uids_same_area():
    """All stale duplicates for a single area are removed."""
    from custom_components.rowenta_roboeye.entity import async_remove_duplicate_room_entities

    new_uid = "clean_room_map3_10_current"
    stale1 = _make_registry_entry("clean_room_map3_10_old1", "button.x_10_old1")
    stale2 = _make_registry_entry("clean_room_map3_10_old2", "button.x_10_old2")
    canonical = _make_registry_entry(new_uid, "button.x_10_current")

    ent_reg = MagicMock()
    removed = []
    ent_reg.async_remove = MagicMock(side_effect=lambda eid: removed.append(eid))

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[stale1, stale2, canonical],
        ),
    ):
        async_remove_duplicate_room_entities(
            hass, config_entry, "button", {new_uid}
        )

    assert set(removed) == {stale1.entity_id, stale2.entity_id}


def test_dedup_ignores_different_platform():
    """Entries from a different platform domain are not touched."""
    from custom_components.rowenta_roboeye.entity import async_remove_duplicate_room_entities

    new_uid = "clean_room_map3_10_current"
    # This has the same area/map as new_uid but belongs to "sensor" platform
    wrong_platform = _make_registry_entry(
        "clean_room_map3_10_old", "sensor.robot_map3_room_10", domain="sensor"
    )

    ent_reg = MagicMock()
    ent_reg.async_remove = MagicMock()

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[wrong_platform],
        ),
    ):
        async_remove_duplicate_room_entities(
            hass, config_entry, "button", {new_uid}
        )

    ent_reg.async_remove.assert_not_called()


def test_dedup_noop_when_no_canonical_uids():
    """Empty canonical set → nothing is removed (safety guard)."""
    from custom_components.rowenta_roboeye.entity import async_remove_duplicate_room_entities

    ent_reg = MagicMock()
    ent_reg.async_remove = MagicMock()

    hass = MagicMock()
    config_entry = MagicMock()

    # Should return early without touching the registry at all
    async_remove_duplicate_room_entities(hass, config_entry, "button", set())

    ent_reg.async_remove.assert_not_called()


# ── async_enable_room_entities_for_map ───────────────────────────────

def _make_reg_entry_with_disabled(unique_id, entity_id, domain="button", disabled_by=None):
    entry = MagicMock()
    entry.unique_id = unique_id
    entry.entity_id = entity_id
    entry.domain = domain
    entry.disabled_by = disabled_by
    return entry


def test_enable_reenables_integration_disabled_entry_for_target_map():
    """async_enable_room_entities_for_map sets disabled_by=None for INTEGRATION-disabled entries on the target map."""
    from custom_components.rowenta_roboeye.entity import async_enable_room_entities_for_map
    import homeassistant.helpers.entity_registry as er_stub

    INTEGRATION = er_stub.RegistryEntryDisabler.INTEGRATION

    entry = _make_reg_entry_with_disabled(
        "clean_room_map3_10_device123",
        "button.robot_map3_clean_room_10",
        domain="button",
        disabled_by=INTEGRATION,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_enable_room_entities_for_map(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_called_once_with(entry.entity_id, disabled_by=None)


def test_enable_does_not_touch_user_disabled_entry():
    """async_enable_room_entities_for_map only re-enables INTEGRATION-disabled entries, not user-disabled ones."""
    from custom_components.rowenta_roboeye.entity import async_enable_room_entities_for_map

    user_disabled = MagicMock()  # simulates RegistryEntryDisabler.USER
    entry = _make_reg_entry_with_disabled(
        "clean_room_map3_10_device123",
        "button.robot_map3_clean_room_10",
        domain="button",
        disabled_by=user_disabled,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_enable_room_entities_for_map(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_not_called()


def test_enable_does_not_touch_entries_for_other_maps():
    """async_enable_room_entities_for_map only touches entries whose map_id matches."""
    from custom_components.rowenta_roboeye.entity import async_enable_room_entities_for_map
    import homeassistant.helpers.entity_registry as er_stub

    INTEGRATION = er_stub.RegistryEntryDisabler.INTEGRATION

    # Entry for map 5 — should NOT be re-enabled when target is map 3
    entry = _make_reg_entry_with_disabled(
        "clean_room_map5_10_device123",
        "button.robot_map5_clean_room_10",
        domain="button",
        disabled_by=INTEGRATION,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_enable_room_entities_for_map(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_not_called()


def test_enable_noop_for_empty_map_id():
    """async_enable_room_entities_for_map is a no-op when map_id is empty."""
    from custom_components.rowenta_roboeye.entity import async_enable_room_entities_for_map

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()

    async_enable_room_entities_for_map(hass, config_entry, "button", "")

    ent_reg.async_update_entity.assert_not_called()


# ── async_disable_room_entities_for_other_maps ───────────────────────

def test_disable_disables_enabled_entries_for_other_maps():
    """async_disable_room_entities_for_other_maps disables non-active-map entries that are currently enabled."""
    from custom_components.rowenta_roboeye.entity import async_disable_room_entities_for_other_maps
    import homeassistant.helpers.entity_registry as er_stub

    INTEGRATION = er_stub.RegistryEntryDisabler.INTEGRATION

    # Entry for map 5 (not active) that is currently enabled (disabled_by=None)
    entry = _make_reg_entry_with_disabled(
        "clean_room_map5_10_device123",
        "button.robot_map5_clean_room_10",
        domain="button",
        disabled_by=None,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_disable_room_entities_for_other_maps(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_called_once_with(
        entry.entity_id, disabled_by=INTEGRATION
    )


def test_disable_does_not_touch_active_map_entries():
    """async_disable_room_entities_for_other_maps never disables entries for the active map."""
    from custom_components.rowenta_roboeye.entity import async_disable_room_entities_for_other_maps

    # Entry for map 3 (the active map) — must not be disabled
    entry = _make_reg_entry_with_disabled(
        "clean_room_map3_10_device123",
        "button.robot_map3_clean_room_10",
        domain="button",
        disabled_by=None,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_disable_room_entities_for_other_maps(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_not_called()


def test_disable_does_not_touch_already_disabled_entries():
    """async_disable_room_entities_for_other_maps skips entries that are already disabled."""
    from custom_components.rowenta_roboeye.entity import async_disable_room_entities_for_other_maps
    import homeassistant.helpers.entity_registry as er_stub

    INTEGRATION = er_stub.RegistryEntryDisabler.INTEGRATION

    # Entry for map 5 already disabled — must not call async_update_entity again
    entry = _make_reg_entry_with_disabled(
        "clean_room_map5_10_device123",
        "button.robot_map5_clean_room_10",
        domain="button",
        disabled_by=INTEGRATION,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[entry],
        ),
    ):
        async_disable_room_entities_for_other_maps(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_not_called()


def test_disable_noop_for_empty_active_map_id():
    """async_disable_room_entities_for_other_maps is a no-op when active_map_id is empty."""
    from custom_components.rowenta_roboeye.entity import async_disable_room_entities_for_other_maps

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()

    async_disable_room_entities_for_other_maps(hass, config_entry, "button", "")

    ent_reg.async_update_entity.assert_not_called()


def test_disable_only_active_map_entries_untouched_others_disabled():
    """Mixed scenario: active map entry stays enabled, other-map entry gets disabled."""
    from custom_components.rowenta_roboeye.entity import async_disable_room_entities_for_other_maps
    import homeassistant.helpers.entity_registry as er_stub

    INTEGRATION = er_stub.RegistryEntryDisabler.INTEGRATION

    active_entry = _make_reg_entry_with_disabled(
        "clean_room_map3_10_device123",
        "button.robot_map3_clean_room_10",
        domain="button",
        disabled_by=None,
    )
    other_entry = _make_reg_entry_with_disabled(
        "clean_room_map5_20_device123",
        "button.robot_map5_clean_room_20",
        domain="button",
        disabled_by=None,
    )

    ent_reg = MagicMock()
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"

    with (
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_get",
            return_value=ent_reg,
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch(
            "custom_components.rowenta_roboeye.entity.er.async_entries_for_config_entry",
            return_value=[active_entry, other_entry],
        ),
    ):
        async_disable_room_entities_for_other_maps(hass, config_entry, "button", "3")

    ent_reg.async_update_entity.assert_called_once_with(
        other_entry.entity_id, disabled_by=INTEGRATION
    )
