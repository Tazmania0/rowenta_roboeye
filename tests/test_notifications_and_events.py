"""Tests for two previously-uncovered coordinator paths:

T1 — incremental event-log processing (_process_new_events + cursor seeding).
T2 — brush-stuck / dustbin persistent-notification firing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.rowenta_roboeye.const import (
    DATA_STATUS,
    EVENT_DUSTBIN_INSERTED,
    EVENT_DUSTBIN_MISSING,
    EVENT_ROBOT_LIFTED,
)
from custom_components.rowenta_roboeye.coordinator import RobEyeCoordinator

from .conftest import MOCK_STATUS, make_sensor_values


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    coord = RobEyeCoordinator(
        hass=MagicMock(),
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    coord._is_live_map_enabled = lambda: False  # keep live-map polling out of the way
    return coord


# ══════════════════════════════════════════════════════════════════════
# T1 — Event log processing
# ══════════════════════════════════════════════════════════════════════

# The coordinator imports persistent_notification locally
# (`from homeassistant.components import persistent_notification`), so patch the
# stub module object that lives in sys.modules rather than a coordinator global.
PN = "homeassistant.components.persistent_notification"


def _evt(eid, type_id, *, hierarchy=1, source_type="user", **extra):
    return {"id": eid, "type_id": type_id, "hierarchy": hierarchy,
            "source_type": source_type, **extra}


@pytest.mark.asyncio
async def test_event_log_first_fetch_seeds_cursor_without_processing(coordinator, mock_client):
    """The very first fetch seeds the cursor but must NOT surface historical events."""
    mock_client.get_event_log.return_value = {
        "robot_events": [_evt(10, EVENT_DUSTBIN_MISSING), _evt(11, EVENT_ROBOT_LIFTED)]
    }
    with patch.object(coordinator, "_process_new_events") as proc, \
         patch(PN):
        await coordinator._async_update_data()

    proc.assert_not_called()                 # history not surfaced
    assert coordinator._event_log_seeded is True
    assert coordinator._last_event_log_id == 11   # cursor advanced to newest id


@pytest.mark.asyncio
async def test_event_log_second_fetch_processes_new_events(coordinator, mock_client):
    """After seeding, subsequent new events are processed and advance the cursor."""
    coordinator._event_log_seeded = True
    coordinator._last_event_log_id = 11
    mock_client.get_event_log.return_value = {
        "robot_events": [_evt(12, EVENT_DUSTBIN_MISSING)]
    }
    with patch.object(coordinator, "_process_new_events") as proc, \
         patch(PN):
        await coordinator._async_update_data()

    proc.assert_called_once()
    assert proc.call_args[0][0][0]["id"] == 12
    assert coordinator._last_event_log_id == 12
    # the cursor was passed to the client so only new events are fetched
    assert mock_client.get_event_log.call_args.kwargs["last_id"] == 11


@pytest.mark.asyncio
async def test_event_log_empty_response_keeps_cursor(coordinator, mock_client):
    coordinator._event_log_seeded = True
    coordinator._last_event_log_id = 11
    mock_client.get_event_log.return_value = {"robot_events": []}
    with patch.object(coordinator, "_process_new_events") as proc, patch(PN):
        await coordinator._async_update_data()
    proc.assert_not_called()
    assert coordinator._last_event_log_id == 11


def test_process_dustbin_missing_creates_notification(coordinator):
    with patch(PN) as pn:
        coordinator._process_new_events([_evt(1, EVENT_DUSTBIN_MISSING)])
    pn.async_create.assert_called_once()
    assert pn.async_create.call_args.kwargs["notification_id"] == "rowenta_dustbin_missing"


def test_process_dustbin_inserted_dismisses_notification(coordinator):
    with patch(PN) as pn:
        coordinator._process_new_events([_evt(2, EVENT_DUSTBIN_INSERTED)])
    pn.async_dismiss.assert_called_once_with(coordinator.hass, "rowenta_dustbin_missing")


def test_process_keeps_only_top_level_events_capped_at_50(coordinator):
    # 60 top-level + interleaved child events; only top-level kept, capped to 50.
    events = []
    for i in range(60):
        events.append(_evt(i, EVENT_ROBOT_LIFTED, hierarchy=1))
        events.append(_evt(1000 + i, EVENT_ROBOT_LIFTED, hierarchy=2))  # child — dropped
    with patch(PN):
        coordinator._process_new_events(events)
    assert len(coordinator._recent_events) == 50
    assert all(e["hierarchy"] == 1 for e in coordinator._recent_events)
    # newest retained (FIFO drop of oldest)
    assert coordinator._recent_events[-1]["id"] == 59


# ══════════════════════════════════════════════════════════════════════
# T2 — Brush-stuck persistent notifications
# ══════════════════════════════════════════════════════════════════════

async def _run_with_sensor_values(coordinator, mock_client, **gpio):
    mock_client.get_sensor_values.return_value = make_sensor_values(**gpio)
    await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_brush_stuck_fires_notification_once(coordinator, mock_client):
    """Notification fires on transition to stuck, and not again while still stuck."""
    with patch(PN) as pn:
        await _run_with_sensor_values(coordinator, mock_client, side_brush_left_stuck="active")
        assert pn.async_create.call_count == 1
        assert coordinator._brush_left_notified is True
        # Still stuck on the next poll — must NOT re-notify.
        await _run_with_sensor_values(coordinator, mock_client, side_brush_left_stuck="active")
        assert pn.async_create.call_count == 1


@pytest.mark.asyncio
async def test_brush_flag_clears_when_unstuck(coordinator, mock_client):
    """When the brush returns to normal the notified flag clears (so it can re-fire)."""
    with patch(PN) as pn:
        await _run_with_sensor_values(coordinator, mock_client, side_brush_left_stuck="active")
        assert coordinator._brush_left_notified is True
        await _run_with_sensor_values(coordinator, mock_client, side_brush_left_stuck="inactive")
        assert coordinator._brush_left_notified is False
        # Re-stuck → fires again.
        await _run_with_sensor_values(coordinator, mock_client, side_brush_left_stuck="active")
        assert pn.async_create.call_count == 2


@pytest.mark.asyncio
async def test_brush_left_and_right_independent(coordinator, mock_client):
    with patch(PN) as pn:
        await _run_with_sensor_values(
            coordinator, mock_client,
            side_brush_left_stuck="active", side_brush_right_stuck="inactive",
        )
        assert coordinator._brush_left_notified is True
        assert coordinator._brush_right_notified is False
        assert pn.async_create.call_count == 1
        ids = {c.kwargs["notification_id"] for c in pn.async_create.call_args_list}
        assert ids == {"rowenta_brush_side_brush_left_stuck"}


@pytest.mark.asyncio
async def test_no_brush_notification_when_not_stuck(coordinator, mock_client):
    with patch(PN) as pn:
        await _run_with_sensor_values(
            coordinator, mock_client,
            side_brush_left_stuck="inactive", side_brush_right_stuck="inactive",
        )
        pn.async_create.assert_not_called()
