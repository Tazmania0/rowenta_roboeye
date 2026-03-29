"""Unit tests for the RobEye select entities."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.select import RobEyeActiveMapSelect

from .conftest import MOCK_MAPS


def _make_coordinator(data=None):
    """Return a minimal MagicMock coordinator."""
    coord = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.map_id = "3"
    coord._manual_map_id = None
    coord.data = data if data is not None else {
        "maps": dict(MOCK_MAPS),
        "map_status": {"active_map_id": 3},
    }
    # Wire available_maps and active_map_id through the real implementations
    # by delegating to a real coordinator instance when needed.
    # For simplicity, set them as plain properties on the mock.
    coord.available_maps = [
        {"map_id": "3", "name": "Ground Floor"},
        {"map_id": "4", "name": "First Floor"},
    ]
    coord.active_map_id = "3"
    return coord


def _entity(coord=None) -> RobEyeActiveMapSelect:
    """Create a RobEyeActiveMapSelect bypassing __init__."""
    if coord is None:
        coord = _make_coordinator()
    entity = RobEyeActiveMapSelect.__new__(RobEyeActiveMapSelect)
    # Use object.__setattr__ to bypass MagicMock's __setattr__
    # (RestoreEntity is stubbed as MagicMock in conftest.py)
    object.__setattr__(entity, "coordinator", coord)
    object.__setattr__(entity, "_attr_unique_id", "active_map_test")
    object.__setattr__(entity, "_name_to_id", {})
    return entity


# ── Options / current_option ──────────────────────────────────────────

def test_options_uses_map_names():
    """options returns human-readable map names from available_maps."""
    entity = _entity()
    opts = entity.options
    assert opts == ["Ground Floor", "First Floor"]


def test_options_falls_back_to_active_map_id_when_no_maps():
    """When available_maps is empty, options shows the current map ID."""
    coord = _make_coordinator()
    coord.available_maps = []
    coord.active_map_id = "3"
    entity = _entity(coord)
    opts = entity.options
    assert opts == ["3"]


def test_current_option_matches_active_map():
    """current_option returns the name matching active_map_id."""
    entity = _entity()
    entity._build_options()  # populate _name_to_id
    assert entity.current_option == "Ground Floor"


def test_current_option_second_map():
    """current_option returns 'First Floor' when active map is 4."""
    coord = _make_coordinator()
    coord.active_map_id = "4"
    entity = _entity(coord)
    entity._build_options()
    assert entity.current_option == "First Floor"


def test_current_option_falls_back_to_id_when_name_unknown():
    """If active map ID has no name, return the ID string directly."""
    coord = _make_coordinator()
    coord.active_map_id = "99"
    entity = _entity(coord)
    entity._build_options()
    assert entity.current_option == "99"


# ── async_select_option ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_option_resolves_name_to_id():
    """Selecting a map name resolves to the correct ID and calls coordinator."""
    coord = _make_coordinator()
    coord.async_set_active_map = AsyncMock()
    entity = _entity(coord)
    entity._build_options()

    await entity.async_select_option("First Floor")

    coord.async_set_active_map.assert_called_once_with("4")


@pytest.mark.asyncio
async def test_select_option_unknown_name_passes_raw():
    """If option string isn't a known name, it's passed as-is (raw ID)."""
    coord = _make_coordinator()
    coord.async_set_active_map = AsyncMock()
    entity = _entity(coord)
    entity._build_options()

    await entity.async_select_option("99")

    coord.async_set_active_map.assert_called_once_with("99")


# ── async_added_to_hass (state restore) ───────────────────────────────

@pytest.mark.asyncio
async def test_restore_sets_manual_map_id_by_name():
    """Restoring 'First Floor' sets _manual_map_id to its ID '4'."""
    coord = _make_coordinator()
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "First Floor"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    # Stub out super() chain
    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    assert coord._manual_map_id == "4"


@pytest.mark.asyncio
async def test_restore_ignores_unavailable():
    """'unavailable' state is not restored."""
    coord = _make_coordinator()
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "unavailable"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    assert coord._manual_map_id is None


@pytest.mark.asyncio
async def test_restore_ignores_unknown():
    """'unknown' state is not restored."""
    coord = _make_coordinator()
    entity = _entity(coord)
    entity._build_options()

    last_state = MagicMock()
    last_state.state = "unknown"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    assert coord._manual_map_id is None


@pytest.mark.asyncio
async def test_restore_populates_name_to_id_before_lookup():
    """async_added_to_hass resolves map name to ID even without prior _build_options().

    Regression test for bug where _name_to_id was empty at restore time,
    causing the raw display name (e.g. 'First Floor') to be stored as
    _manual_map_id instead of the numeric ID '4'.
    """
    coord = _make_coordinator()
    entity = _entity(coord)
    # Deliberately do NOT call entity._build_options() — simulates HA restart
    assert entity._name_to_id == {}   # confirm _name_to_id is empty

    last_state = MagicMock()
    last_state.state = "First Floor"
    entity.async_get_last_state = AsyncMock(return_value=last_state)

    from homeassistant.helpers.restore_state import RestoreEntity
    RestoreEntity.async_added_to_hass = AsyncMock()

    await entity.async_added_to_hass()

    # Must resolve to numeric ID, not the raw display name string
    assert coord._manual_map_id == "4"
