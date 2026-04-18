"""Unit tests for the RobEye DataUpdateCoordinator."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.rowenta_roboeye.api import CannotConnect
from custom_components.rowenta_roboeye.const import (
    DATA_AREAS,
    DATA_AREAS_SAVED_MAP,
    DATA_LIVE_PARAMETERS,
    DATA_ROB_POSE,
    DATA_ROBOT_INFO,
    DATA_STATISTICS,
    DATA_STATUS,
    SCAN_INTERVAL_AREAS,
    SCAN_INTERVAL_ROBOT_INFO,
    SCAN_INTERVAL_STATISTICS,
)
from custom_components.rowenta_roboeye.coordinator import (
    RobEyeCoordinator,
    _build_live_map_payload,
    _command_name,
    _extract_rob_pose,
    _parse_map_entry,
)

from .conftest import MOCK_AREAS, MOCK_CLEANING_GRID, MOCK_MAP_STATUS, MOCK_MAPS, MOCK_ROB_POSE, MOCK_STATISTICS, MOCK_STATUS


@pytest.fixture
def coordinator(mock_client, mock_config_entry):
    hass = MagicMock()
    coord = RobEyeCoordinator(
        hass=hass,
        config_entry=mock_config_entry,
        client=mock_client,
        map_id="3",
    )
    # Ensure live_map polling is always enabled in tests
    # (the entity registry lookup fails with a MagicMock hass)
    coord._is_live_map_enabled = lambda: True
    return coord


# ── First update — all resource groups must be fetched ────────────────

@pytest.mark.asyncio
async def test_first_update_fetches_all_groups(coordinator, mock_client):
    coordinator.data = {}
    await coordinator._async_update_data()

    mock_client.get_status.assert_called_once()
    mock_client.get_rob_pose.assert_called_once()
    mock_client.get_statistics.assert_called_once()
    # get_areas is called twice on first run: once for the areas block
    # (_last_areas is None) and once for the map-geometry block
    # (_last_map_geometry is None).  Both use map_id="3".
    mock_client.get_areas.assert_any_call("3")
    assert mock_client.get_areas.call_count >= 1
    mock_client.get_sensor_status.assert_called_once()
    mock_client.get_robot_id.assert_called_once()
    mock_client.get_wifi_status.assert_called_once()
    mock_client.get_protocol_version.assert_called_once()


# ── Interval gating ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_fetched_every_tick(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()

    assert mock_client.get_status.call_count == 1
    assert mock_client.get_statistics.call_count == 0
    assert mock_client.get_areas.call_count == 0
    assert mock_client.get_robot_id.call_count == 0


@pytest.mark.asyncio
async def test_areas_fetched_after_300s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()
    assert mock_client.get_areas.call_count == 1
    assert mock_client.get_statistics.call_count == 0


@pytest.mark.asyncio
async def test_statistics_fetched_after_600s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_statistics = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_STATISTICS + 1)
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block

    await coordinator._async_update_data()
    assert mock_client.get_statistics.call_count == 1
    assert mock_client.get_areas.call_count == 0


@pytest.mark.asyncio
async def test_robot_info_fetched_after_3600s(coordinator, mock_client):
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_robot_info = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_ROBOT_INFO + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_areas = datetime.utcnow()

    await coordinator._async_update_data()
    mock_client.get_robot_id.assert_called_once()
    mock_client.get_wifi_status.assert_called_once()
    mock_client.get_protocol_version.assert_called_once()


# ── Graceful degradation — optional endpoints ─────────────────────────

@pytest.mark.asyncio
async def test_rob_pose_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_rob_pose.side_effect = CannotConnect("not available")
    # Should not raise — rob_pose is best-effort
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


@pytest.mark.asyncio
async def test_live_parameters_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_live_parameters.side_effect = CannotConnect("not available")
    # Should not raise — live_parameters is best-effort
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


@pytest.mark.asyncio
async def test_sensor_status_failure_is_non_fatal(coordinator, mock_client):
    coordinator.data = {}
    mock_client.get_sensor_status.side_effect = CannotConnect("not available")
    await coordinator._async_update_data()
    assert DATA_STATUS in coordinator.data


# ── Error propagation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_failure_raises_update_failed(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_consecutive_failure_counter(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}

    for i in range(1, 5):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator._consecutive_failures == i


@pytest.mark.asyncio
async def test_failure_counter_resets_on_success(coordinator, mock_client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_client.get_status.side_effect = CannotConnect("timeout")
    coordinator.data = {}
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    mock_client.get_status.side_effect = None
    mock_client.get_status.return_value = dict(MOCK_STATUS)
    await coordinator._async_update_data()
    assert coordinator._consecutive_failures == 0


# ── async_send_command / command queue ───────────────────────────────

async def _start_worker(coordinator):
    """Start the queue worker as a real asyncio task (bypassing hass mock)."""
    task = asyncio.ensure_future(coordinator._command_queue_worker())
    return task


async def _drain_and_cancel(coordinator, task):
    """Drain the queue then cancel the worker task."""
    await coordinator._command_queue.join()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_send_command_calls_fn_and_refreshes(coordinator, mock_client):
    """Command put into queue is dispatched by the worker and triggers refresh."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 1, "status": "done", "error_code": 0}]
    }
    task = await _start_worker(coordinator)
    await coordinator.async_send_command(mock_client.go_home)
    await _drain_and_cancel(coordinator, task)
    mock_client.go_home.assert_called_once()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_send_command_passes_kwargs(coordinator, mock_client):
    """kwargs are forwarded to the command coroutine."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 1, "status": "done", "error_code": 0}]
    }
    task = await _start_worker(coordinator)
    await coordinator.async_send_command(
        mock_client.clean_all, cleaning_parameter_set="3"
    )
    await _drain_and_cancel(coordinator, task)
    mock_client.clean_all.assert_called_once_with(cleaning_parameter_set="3")


# ── _wait_for_robot_idle ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_robot_idle_returns_when_done(coordinator, mock_client):
    """_wait_for_robot_idle exits immediately when status is done."""
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 10, "status": "done", "error_code": 0}]
    }
    await coordinator._wait_for_robot_idle()
    mock_client.get_command_result.assert_called_once()


@pytest.mark.asyncio
async def test_robot_idle_returns_when_aborted(coordinator, mock_client):
    """Aborted command is terminal and should unblock queue."""
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 10, "status": "aborted", "error_code": 0}]
    }
    await coordinator._wait_for_robot_idle(cmd_id=10)
    mock_client.get_command_result.assert_called_once()


@pytest.mark.asyncio
async def test_robot_idle_polls_while_executing(coordinator, mock_client):
    """Polls until status transitions from executing to done."""
    mock_client.get_command_result.side_effect = [
        {"commands": [{"cmd_id": 10, "status": "executing", "error_code": 0}]},
        {"commands": [{"cmd_id": 10, "status": "executing", "error_code": 0}]},
        {"commands": [{"cmd_id": 10, "status": "done",      "error_code": 0}]},
    ]
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("asyncio.sleep", AsyncMock())
        await coordinator._wait_for_robot_idle()
    assert mock_client.get_command_result.call_count == 3


@pytest.mark.asyncio
async def test_robot_idle_returns_on_nonzero_error_code(coordinator, mock_client):
    """Non-zero error_code should be treated as terminal regardless of status value."""
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 10, "status": "done", "error_code": 106}]
    }
    await coordinator._wait_for_robot_idle(cmd_id=10)
    mock_client.get_command_result.assert_called_once()


@pytest.mark.asyncio
async def test_robot_idle_proceeds_on_cannot_connect(coordinator, mock_client):
    """CannotConnect is swallowed — never blocks the queue."""
    mock_client.get_command_result.side_effect = CannotConnect("timeout")
    await coordinator._wait_for_robot_idle()   # must not raise or block


@pytest.mark.asyncio
async def test_robot_idle_reads_commands_array(coordinator, mock_client):
    """Must read commands[0].status — top-level status key does not exist."""
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 5, "status": "done", "error_code": 0}]
    }
    await coordinator._wait_for_robot_idle()   # must not KeyError


@pytest.mark.asyncio
async def test_robot_idle_returns_immediately_on_empty_commands(coordinator, mock_client):
    """Empty commands array → return immediately."""
    mock_client.get_command_result.return_value = {"commands": []}
    await coordinator._wait_for_robot_idle()
    mock_client.get_command_result.assert_called_once()


# ── Queue ordering and stop-drain behaviour ───────────────────────────

@pytest.mark.asyncio
async def test_commands_serialised_in_queue_order(coordinator, mock_client):
    """Multiple commands are dispatched in press order, one at a time."""
    dispatched: list[str] = []
    coordinator.async_request_refresh = AsyncMock()
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 1, "status": "done", "error_code": 0}]
    }

    async def make_cmd(name):
        async def _cmd():
            dispatched.append(name)
        return _cmd

    cmd1 = await make_cmd("room1")
    cmd2 = await make_cmd("room2")
    cmd3 = await make_cmd("room3")

    task = await _start_worker(coordinator)
    await coordinator.async_send_command(cmd1)
    await coordinator.async_send_command(cmd2)
    await coordinator.async_send_command(cmd3)
    await _drain_and_cancel(coordinator, task)

    assert dispatched == ["room1", "room2", "room3"]


@pytest.mark.asyncio
async def test_cleaning_command_waits_for_active_mode_to_finish_before_next(
    coordinator, mock_client
):
    """Second queued clean must wait until first cleaning session actually ends."""
    coordinator.async_request_refresh = AsyncMock()
    order: list[str] = []
    cmd_ids = iter([101, 102])

    async def _clean_map(**kwargs):
        order.append(str(kwargs.get("area_ids")))
        return {"cmd_id": next(cmd_ids), "error_code": 0}

    mock_client.get_command_result.side_effect = [
        {"commands": [{"cmd_id": 101, "status": "done", "error_code": 0}]},
        {"commands": [{"cmd_id": 102, "status": "done", "error_code": 0}]},
    ]
    # Provide status side-effects for cmd1's full active-operation wait
    # (Phase1 + 3 Phase2 iterations) and enough for cmd2's Phase1 loop.
    # Phase1 for cmd2 keeps polling until the 30 s deadline; we satisfy it
    # by having it enter + exit the active mode (cleaning → ready, ready).
    mock_client.get_status.side_effect = [
        # cmd1 Phase 1: enters active
        {"mode": "cleaning"},
        # cmd1 Phase 2: cleaning → ready → ready (2 confirms)
        {"mode": "cleaning"},
        {"mode": "ready"},
        {"mode": "ready"},
        # cmd2 Phase 1: enters active then Phase 2 exits immediately
        {"mode": "cleaning"},
        {"mode": "ready"},
        {"mode": "ready"},
    ]

    coordinator._interruptible_sleep = AsyncMock()
    mock_client.clean_map.side_effect = _clean_map
    task = await _start_worker(coordinator)
    await coordinator.async_send_command(
        mock_client.clean_map, map_id="3", area_ids="3"
    )
    await coordinator.async_send_command(
        mock_client.clean_map, map_id="3", area_ids="11"
    )
    await _drain_and_cancel(coordinator, task)

    assert order == ["3", "11"]
    # Both commands ran the full active-operation wait: 4 polls for cmd1 + 3 for cmd2
    assert mock_client.get_status.call_count == 7


@pytest.mark.asyncio
async def test_stop_drains_pending_cleans(coordinator, mock_client):
    """Pressing Stop clears queued room cleans and dispatches stop in place."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 1, "status": "done", "error_code": 0}]
    }
    dispatched: list[str] = []

    async def clean_r1():
        dispatched.append("room1")

    async def clean_r2():
        dispatched.append("room2")

    # Pre-fill queue with two room cleans (worker not started yet)
    await coordinator._command_queue.put((1, 1, clean_r1, (), {}))
    await coordinator._command_queue.put((1, 2, clean_r2, (), {}))

    # Enqueue stop — must drain the pending cleans
    await coordinator.async_send_command(coordinator.client.stop)

    task = await _start_worker(coordinator)
    await _drain_and_cancel(coordinator, task)

    mock_client.stop.assert_called_once()
    mock_client.go_home.assert_not_called()
    assert "room1" not in dispatched   # drained before running
    assert "room2" not in dispatched


@pytest.mark.asyncio
async def test_go_home_drains_pending_cleans(coordinator, mock_client):
    """Pressing Return to Dock clears queued room cleans and go_home runs next."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 1, "status": "done", "error_code": 0}]
    }
    dispatched: list[str] = []

    async def clean_r1():
        dispatched.append("room1")

    async def clean_r2():
        dispatched.append("room2")

    await coordinator._command_queue.put((1, 1, clean_r1, (), {}))
    await coordinator._command_queue.put((1, 2, clean_r2, (), {}))

    await coordinator.async_send_command(coordinator.client.go_home)

    task = await _start_worker(coordinator)
    await _drain_and_cancel(coordinator, task)

    mock_client.go_home.assert_called_once()
    assert "room1" not in dispatched
    assert "room2" not in dispatched


@pytest.mark.asyncio
async def test_immediate_command_interrupts_active_operation_wait(coordinator, mock_client):
    """Queued immediate command should break active-operation wait loop promptly.

    Phase 1 of _wait_for_active_operation_end checks _has_immediate_command_pending()
    at the START of each iteration, before the get_status call.  So when a priority-0
    command is already in the queue the function returns without polling at all.
    """
    mock_client.get_status.return_value = {"mode": "cleaning"}
    coordinator._interruptible_sleep = AsyncMock()
    await coordinator._command_queue.put((0, 1, coordinator.client.stop, (), {}))
    await coordinator._wait_for_active_operation_end()
    mock_client.get_status.assert_not_called()


# ── Convenience properties ────────────────────────────────────────────

def test_status_property(coordinator):
    coordinator.data = {DATA_STATUS: MOCK_STATUS}
    assert coordinator.status == MOCK_STATUS


def test_areas_property_unwraps_list(coordinator):
    coordinator.data = {DATA_AREAS: MOCK_AREAS}
    assert len(coordinator.areas) == 3  # includes the empty-meta area


def test_areas_property_empty_when_no_data(coordinator):
    coordinator.data = {}
    assert coordinator.areas == []


def test_resolve_room_name_by_id_falls_back_to_saved_map_areas(coordinator):
    coordinator.data = {
        DATA_AREAS: {"areas": []},
        DATA_AREAS_SAVED_MAP: {
            "areas": [{"id": 42, "area_meta_data": '{"name":"Office"}'}]
        },
    }
    assert coordinator._resolve_room_name_by_id(42) == "Office"


def test_resolve_room_name_by_id_handles_string_area_ids(coordinator):
    coordinator.data = {
        DATA_AREAS: {"areas": [{"id": "31", "area_meta_data": '{"name":"Детска"}'}]},
    }
    assert coordinator._resolve_room_name_by_id(31) == "Детска"


def test_live_parameters_property(coordinator):
    coordinator.data = {DATA_LIVE_PARAMETERS: {"area_cleaned": 100}}
    assert coordinator.live_parameters["area_cleaned"] == 100


def test_command_queue_items_keeps_inflight_clean_visible(coordinator, mock_client):
    coordinator._inflight_clean_command = (
        mock_client.clean_map,
        {"map_id": "3", "area_ids": "11"},
        127,
    )
    items = coordinator.command_queue_items
    assert len(items) == 1
    assert items[0]["status"] == "active"
    assert items[0]["label"] == "Cleaning room 11"
    assert items[0]["cmd_id"] == 127


def test_command_queue_items_shows_paused_session_and_pending_jobs(coordinator, mock_client):
    """While paused, the display must show the paused room AND any drained
    _paused_jobs as pending so the user sees the queue is intact."""
    coordinator._paused_clean_command = (
        mock_client.clean_map,
        {"map_id": "3", "area_ids": "31"},
        127,
    )
    # Job that was pending when stop was pressed → drained into _paused_jobs
    coordinator._paused_jobs = [
        (1, 2, mock_client.clean_map, (), {"map_id": "3", "area_ids": "32"})
    ]

    items = coordinator.command_queue_items
    assert len(items) == 2
    assert items[0]["status"] == "paused"
    assert items[0]["label"] == "Cleaning room 31"
    assert items[0]["cmd_id"] == 127
    assert items[1]["status"] == "pending"
    assert items[1]["label"] == "Cleaning room 32"


def test_command_queue_items_shows_external_active_session_and_keeps_ha_pending(
    coordinator, mock_client
):
    coordinator.data = {DATA_STATUS: {"mode": "cleaning"}}
    coordinator._command_queue.put_nowait(
        (1, 1, mock_client.clean_map, (), {"map_id": "3", "area_ids": "3"})
    )

    items = coordinator.command_queue_items
    assert len(items) == 2
    assert items[0]["status"] == "active"
    assert items[0]["label"] == "Current cleaning session"
    assert items[1]["status"] == "pending"
    assert items[1]["label"] == "Cleaning room 3"


def test_command_queue_items_resolves_pending_room_names(coordinator, mock_client):
    coordinator.data = {
        DATA_AREAS: {"areas": [{"id": "31", "area_meta_data": '{"name":"Детска"}'}]},
    }
    coordinator._command_queue.put_nowait(
        (1, 1, mock_client.clean_map, (), {"map_id": "3", "area_ids": "31"})
    )
    items = coordinator.command_queue_items
    assert len(items) == 1
    assert items[0]["label"] == "Cleaning Детска"


def test_parsed_current_session_resolves_room_names_from_status_area_ids(coordinator):
    coordinator.data = {
        DATA_STATUS: {"mode": "cleaning", "area_ids": [3]},
        DATA_AREAS: {"areas": [{"id": 3, "area_meta_data": '{"name":"Bedroom"}'}]},
    }
    item = coordinator._parsed_current_session_item()
    assert item is not None
    assert item["label"] == "Cleaning Bedroom"


def test_robot_info_property(coordinator):
    coordinator.data = {DATA_ROBOT_INFO: {"wifi_status": {"rssi": -55}}}
    assert coordinator.robot_info["wifi_status"]["rssi"] == -55


# ── Last-session / session replay ─────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_loads_last_session_grid(coordinator, mock_client):
    """On first update (geometry bucket runs), saved grid is loaded and
    session_complete becomes True."""
    coordinator.data = {}
    await coordinator._async_update_data()

    mock_client.get_cleaning_grid_map.assert_called_with(map_id="3")
    assert coordinator.last_session_grid["size_x"] == 29
    assert coordinator.session_complete is True


@pytest.mark.asyncio
async def test_new_cleaning_run_resets_session_state(coordinator, mock_client):
    """When mode transitions to CLEANING, frozen session state is cleared."""
    coordinator.data = {}
    # Pre-populate session data
    coordinator._last_session_grid = dict(MOCK_CLEANING_GRID)
    coordinator._last_session_path = [(1.0, 2.0), (3.0, 4.0)]
    coordinator._session_complete = True
    coordinator._last_mode = "ready"

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning"}
    await coordinator._async_update_data()

    assert coordinator._last_session_grid == {}
    assert coordinator._last_session_path == []
    assert coordinator._session_complete is False


@pytest.mark.asyncio
async def test_session_frozen_on_dock(coordinator, mock_client):
    """When robot docks after cleaning, session data is frozen."""
    from custom_components.rowenta_roboeye.const import DATA_CLEANING_GRID, DATA_SEEN_POLYGON

    coordinator.data = {
        DATA_CLEANING_GRID: dict(MOCK_CLEANING_GRID),
        DATA_SEEN_POLYGON: {},
    }
    coordinator._last_mode = "cleaning"
    coordinator._robot_path = [(0.0, 0.0), (10.0, 5.0)]
    coordinator._session_complete = False

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "ready"}
    await coordinator._async_update_data()

    assert coordinator._session_complete is True
    assert coordinator.last_session_grid["size_x"] == 29
    assert coordinator.last_session_path == [(0.0, 0.0), (10.0, 5.0)]


def test_session_complete_property_default(coordinator):
    assert coordinator.session_complete is False


def test_last_session_grid_property_default(coordinator):
    assert coordinator.last_session_grid == {}


# ── rob_pose extraction helper ────────────────────────────────────────

def test_extract_rob_pose_valid():
    """Confirmed live response at dock (2026-03-21)."""
    data = {
        "map_id": 3,
        "x1": -2,
        "y1": -3,
        "heading": 157,
        "valid": True,
        "is_tentative": False,
        "timestamp": 958459,
    }
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["x"] == -2
    assert pos["y"] == -3
    assert pos["heading_deg"] == 157  # already degrees — no conversion
    assert pos["is_tentative"] is False
    assert pos["timestamp"] == 958459
    assert pos["map_id"] == 3
    assert pos["source"] == "rob_pose"
    assert pos["is_live"] is True


def test_extract_rob_pose_invalid_returns_none():
    """When valid=False the robot has no position fix — return None."""
    data = {"x1": 0, "y1": 0, "heading": 0, "valid": False}
    assert _extract_rob_pose(data) is None


def test_extract_rob_pose_missing_valid_returns_none():
    """Missing valid field treated as False."""
    data = {"x1": 10, "y1": 20, "heading": 90}
    assert _extract_rob_pose(data) is None


def test_extract_rob_pose_tentative():
    """is_tentative=True is preserved in the output."""
    data = {
        "x1": 5, "y1": 10, "heading": 45,
        "valid": True, "is_tentative": True, "timestamp": 100,
    }
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["is_tentative"] is True
    assert pos["is_live"] is True


def test_extract_rob_pose_heading_no_conversion():
    """heading is already in degrees — must not be divided by 65536/360."""
    data = {"x1": 0, "y1": 0, "heading": 270, "valid": True}
    pos = _extract_rob_pose(data)
    assert pos is not None
    assert pos["heading_deg"] == 270  # exactly 270, not 270 / 181.6


def test_extract_rob_pose_empty_returns_none():
    assert _extract_rob_pose({}) is None


# ── Position tracking ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_session_resets_last_live_map(coordinator, mock_client):
    """_last_live_map is cleared when a new cleaning session starts."""
    coordinator.data = {}
    coordinator._last_mode = "ready"
    coordinator._last_live_map = datetime.utcnow()

    mock_client.get_status.return_value = {**MOCK_STATUS, "mode": "cleaning"}
    await coordinator._async_update_data()

    assert coordinator._last_live_map is not None  # updated by this tick
    mock_client.get_rob_pose.assert_called_once()


@pytest.mark.asyncio
async def test_rob_pose_stored_in_data(coordinator, mock_client):
    """After a successful tick, DATA_ROB_POSE is present in coordinator.data."""
    coordinator.data = {}
    await coordinator._async_update_data()
    assert DATA_ROB_POSE in coordinator.data
    assert coordinator.data[DATA_ROB_POSE]["x1"] == -2


def _make_live_map_kwargs(**overrides):
    """Return a minimal valid kwarg dict for _build_live_map_payload."""
    base = dict(
        existing={},
        robot_position=None,
        seen_polygon_raw={},
        cleaning_grid={},
        feature_map={},
        tile_map={},
        areas_data={},
        seen_poly_saved_map={},
        is_active=False,
        map_id="3",
        robot_path=[],
        last_session_grid={},
        last_session_path=[],
        last_session_outline=[],
        last_session_map_id="",
        session_complete=False,
    )
    base.update(overrides)
    return base


def test_active_robot_position_in_payload():
    """During active cleaning the rob_pose position is placed in robot."""
    live_pos = {
        "x": 200, "y": 100, "heading_deg": 10.0,
        "source": "rob_pose", "is_live": True, "is_tentative": False,
    }

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=live_pos,
        is_active=True,
    ))

    robot = payload["robot"]
    assert robot is not None
    assert robot["x"] == 200
    assert robot["y"] == 100
    assert robot["heading_deg"] == 10.0
    assert robot["is_tentative"] is False


def test_tentative_position_propagated_to_payload():
    """is_tentative flag from rob_pose is forwarded to the robot dict."""
    live_pos = {
        "x": 5, "y": 10, "heading_deg": 45.0,
        "source": "rob_pose", "is_live": True, "is_tentative": True,
    }

    payload = _build_live_map_payload(**_make_live_map_kwargs(
        robot_position=live_pos,
        is_active=True,
    ))

    assert payload["robot"]["is_tentative"] is True


def test_no_robot_position_when_invalid_and_idle():
    """When robot_position is None and idle, existing robot is preserved."""
    existing_robot = {"x": 50, "y": 30, "heading_deg": 90.0, "is_tentative": False}
    payload = _build_live_map_payload(**_make_live_map_kwargs(
        existing={"robot": existing_robot},
        robot_position=None,
        is_active=False,
    ))
    # existing robot from previous tick should be kept
    assert payload["robot"] == existing_robot


# ── Multi-map support ────────────────────────────────────────────────

def test_active_map_id_falls_back_to_configured(coordinator):
    """active_map_id returns setup-time map_id when map_status not yet fetched."""
    coordinator.data = {}
    assert coordinator.active_map_id == "3"


def test_active_map_id_ignores_map_status(coordinator):
    """active_map_id intentionally ignores /get/map_status — HA must not silently
    follow the native app's floor selection.  Only the HA-stored preference or the
    setup-time map_id are used."""
    coordinator.data = {"map_status": {"active_map_id": 4, "operation_map_id": 4}}
    # map_status says "4" but _manual_map_id is None → falls back to setup map_id "3"
    assert coordinator.active_map_id == "3"


def test_active_map_id_falls_back_on_empty_string(coordinator):
    coordinator.data = {"map_status": {"active_map_id": ""}}
    assert coordinator.active_map_id == "3"  # setup-time map_id


CONFIRMED_MAPS_RESPONSE = {
    "maps": [
        {
            "map_id": 3,
            "map_meta_data": "Дружба ",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0, "cleaning_counter": 5,
                "estimated_cleaning_time": 0, "average_cleaning_time": 1800,
                "last_cleaned": {"year": 2026, "month": 3, "day": 20,
                                 "hour": 11, "min": 21, "sec": 0},
            },
        },
        {
            "map_id": 18,
            "map_meta_data": "",
            "permanent_flag": "true",
            "statistics": {
                "area_size": 0, "cleaning_counter": 0,
                "estimated_cleaning_time": 0, "average_cleaning_time": 0,
                "last_cleaned": {"year": 2001, "month": 1, "day": 1,
                                 "hour": 0, "min": 0, "sec": 0},
            },
        },
    ]
}


def test_parse_map_entry_named():
    """Named map uses map_meta_data, stripped."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][0], position=1, active_map_id="3")
    assert result["map_id"] == "3"
    assert result["display_name"] == "Дружба"
    assert result["user_name"] == "Дружба"
    assert result["is_active"] is True


def test_parse_map_entry_unnamed_uses_position():
    """Unnamed map uses 1-based position, NOT map_id."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][1], position=2, active_map_id="3")
    assert result["display_name"] == "Map 2"
    assert result["map_id"] == "18"        # map_id is still 18
    assert result["is_active"] is False


def test_parse_map_entry_strips_whitespace():
    raw = {"map_id": 3, "map_meta_data": "  Дружба  ", "permanent_flag": "true", "statistics": {}}
    result = _parse_map_entry(raw, position=1)
    assert result["display_name"] == "Дружба"


def test_parse_map_entry_permanent_flag_is_string_not_bool():
    """permanent_flag is the string "true", not Python True."""
    raw = {"map_id": 5, "map_meta_data": "Test", "permanent_flag": "true", "statistics": {}}
    result = _parse_map_entry(raw, position=1)
    assert result is not None


def test_parse_map_entry_skips_non_permanent():
    raw = {"map_id": 99, "map_meta_data": "", "permanent_flag": "false", "statistics": {}}
    assert _parse_map_entry(raw, position=1) is None


def test_parse_map_entry_last_cleaned_sentinel_2001():
    """year 2001 → never cleaned → last_cleaned is None."""
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][1], position=2)
    assert result["statistics"]["last_cleaned"] is None


def test_parse_map_entry_real_last_cleaned():
    result = _parse_map_entry(CONFIRMED_MAPS_RESPONSE["maps"][0], position=1)
    assert result["statistics"]["last_cleaned"] == "2026-03-20"


def test_available_maps_parses_dict_entries(coordinator):
    coordinator.data = {"maps": dict(MOCK_MAPS)}
    maps = coordinator.available_maps
    assert len(maps) == 2
    assert maps[0]["map_id"] == "3"
    assert maps[0]["display_name"] == "Ground Floor"
    assert maps[0]["user_name"] == "Ground Floor"
    assert maps[1]["map_id"] == "4"
    assert maps[1]["display_name"] == "First Floor"


def test_available_maps_skips_non_permanent(coordinator):
    coordinator.data = {"maps": {"maps": [
        {"map_id": 3, "map_meta_data": "Floor", "permanent_flag": "true", "statistics": {}},
        {"map_id": 99, "map_meta_data": "", "permanent_flag": "false", "statistics": {}},
    ]}}
    maps = coordinator.available_maps
    assert len(maps) == 1
    assert maps[0]["map_id"] == "3"


def test_available_maps_unnamed_uses_position(coordinator):
    coordinator.data = {"maps": {"maps": [
        {"map_id": 18, "map_meta_data": "", "permanent_flag": "true", "statistics": {}},
    ]}}
    maps = coordinator.available_maps
    assert maps[0]["display_name"] == "Map 1"
    assert maps[0]["map_id"] == "18"


def test_available_maps_full(coordinator):
    coordinator.data = {
        "map_status": {"active_map_id": 3},
        "maps": CONFIRMED_MAPS_RESPONSE,
    }
    maps = coordinator.available_maps
    assert len(maps) == 2
    assert maps[0]["display_name"] == "Дружба"
    assert maps[0]["is_active"] is True
    assert maps[1]["display_name"] == "Map 2"
    assert maps[1]["is_active"] is False


def test_available_maps_empty_when_no_data(coordinator):
    coordinator.data = {}
    assert coordinator.available_maps == []


@pytest.mark.asyncio
async def test_device_floor_change_does_not_reset_areas(coordinator, mock_client):
    """When the device reports a different map via map_status, HA ignores it.
    Area state must NOT be reset — only async_set_active_map (user action) does that."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_active_map_id = "3"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {3, 11}
    coordinator._robot_path = [(0.0, 0.0)]
    coordinator._session_complete = True

    # Device reports a different map — HA must NOT follow it automatically
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # active_map_id stays at setup map_id "3" regardless of what device reports
    assert coordinator.active_map_id == "3"
    # Areas/session state must NOT have been reset by the device-reported change
    assert coordinator._robot_path != []
    assert coordinator._session_complete is True


@pytest.mark.asyncio
async def test_maps_and_map_status_fetched_in_areas_bucket(coordinator, mock_client):
    """get_maps and get_map_status are called in the 300s areas bucket."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()

    await coordinator._async_update_data()

    mock_client.get_maps.assert_called_once()
    mock_client.get_map_status.assert_called_once()
    assert "maps" in coordinator.data
    assert "map_status" in coordinator.data


# ── Manual map override (async_set_active_map) ────────────────────────

def test_active_map_id_manual_override_wins(coordinator):
    """_manual_map_id takes priority over /get/map_status."""
    coordinator.data = {"map_status": {"active_map_id": 3, "operation_map_id": 3}}
    coordinator._manual_map_id = "57"
    assert coordinator.active_map_id == "57"


def test_active_map_id_no_override_uses_setup_map_id(coordinator):
    """Without manual override, active_map_id returns the setup-time map_id.
    map_status is intentionally ignored — HA must not silently follow the native app."""
    coordinator.data = {"map_status": {"active_map_id": 4, "operation_map_id": 4}}
    coordinator._manual_map_id = None
    assert coordinator.active_map_id == "3"  # setup-time map_id, not map_status


@pytest.mark.asyncio
async def test_async_set_active_map_updates_state(coordinator):
    """async_set_active_map sets override, resets area/geometry timestamps."""
    coordinator.data = {}
    coordinator._last_areas = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {3, 11}
    coordinator._robot_path = [(1.0, 2.0)]
    coordinator._session_complete = True
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_active_map("57")

    assert coordinator._manual_map_id == "57"
    assert coordinator._last_active_map_id == "57"
    assert coordinator._last_areas is None
    assert coordinator._last_map_geometry is None
    assert coordinator._known_area_ids == set()
    assert coordinator._robot_path == []
    assert coordinator._session_complete is False
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_set_active_map_invalidates_dashboard(coordinator):
    """async_set_active_map calls dashboard_manager.invalidate() when present."""
    coordinator.data = {}
    coordinator.async_request_refresh = AsyncMock()

    dashboard_manager = MagicMock()
    coordinator.hass.data = {
        "rowenta_roboeye": {
            f"{coordinator.config_entry.entry_id}_dashboard": dashboard_manager
        }
    }

    await coordinator.async_set_active_map("4")

    dashboard_manager.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_manual_override_persists_across_poll(coordinator, mock_client):
    """Manual map override persists across coordinator polls regardless of
    what map_status the device reports.  HA never silently follows the native app."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._manual_map_id = "57"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()

    # Device reports a completely different map
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # Manual override must never be cleared by a poll cycle
    assert coordinator._manual_map_id == "57"
    assert coordinator.active_map_id == "57"


@pytest.mark.asyncio
async def test_areas_fetched_for_ha_configured_map(coordinator, mock_client):
    """Areas are always fetched for the HA-configured map, never the device's map_status."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS, DATA_AREAS: MOCK_AREAS}
    coordinator._manual_map_id = None          # no user override → uses setup map_id "3"
    coordinator._last_areas = datetime.utcnow() - timedelta(seconds=SCAN_INTERVAL_AREAS + 1)
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()
    coordinator._known_area_ids = {1, 2, 3}

    # Device reports map 4, but HA should fetch areas for map "3" (setup map)
    mock_client.get_map_status.return_value = {"active_map_id": 4, "operation_map_id": 4}

    await coordinator._async_update_data()

    # Areas must have been fetched for the HA setup map_id ("3"), not device's "4"
    mock_client.get_areas.assert_called_with("3")
    assert coordinator.areas_map_id == "3"


@pytest.mark.asyncio
async def test_areas_discarded_when_map_switched_mid_fetch(coordinator, mock_client):
    """Regression: if the user switches maps while get_areas() is in flight,
    the stale response must be discarded so the next refresh re-fetches for
    the new map.  Without this guard, _last_areas would be marked fresh and
    room entities for the new map would never be created (dashboard shows
    "unavailable" until the 300 s interval elapses)."""
    coordinator.data = {DATA_STATUS: MOCK_STATUS, DATA_STATISTICS: MOCK_STATISTICS}
    coordinator._manual_map_id = None  # start on setup map "3"
    coordinator._last_areas = None     # force areas bucket to run
    coordinator._last_statistics = datetime.utcnow()
    coordinator._last_robot_info = datetime.utcnow()
    coordinator._last_map_geometry = datetime.utcnow()  # suppress geometry block
    coordinator._known_area_ids = set()
    coordinator._areas_fetched_for_map_id = None

    # Simulate user flipping maps during the get_areas() HTTP await.
    async def _switch_during_fetch(*_args, **_kwargs):
        coordinator._manual_map_id = "57"
        return dict(MOCK_AREAS)

    mock_client.get_areas.side_effect = _switch_during_fetch

    await coordinator._async_update_data()

    # Stale response must have been discarded entirely.
    assert coordinator._last_areas is None, (
        "stale fetch must not update _last_areas — next refresh needs to re-fetch"
    )
    assert coordinator._areas_fetched_for_map_id is None
    assert DATA_AREAS not in coordinator.data or coordinator.data.get(DATA_AREAS) != MOCK_AREAS
    # _known_area_ids untouched → no stale-map signal was dispatched.
    assert coordinator._known_area_ids == set()


# ── _check_for_new_areas signal behaviour ────────────────────────────────────

def test_check_for_new_areas_signals_on_first_areas(coordinator):
    """Signal fires (via call_soon) when _known_area_ids is empty and areas arrive."""
    from custom_components.rowenta_roboeye.coordinator import async_dispatcher_send

    coordinator._known_area_ids = set()  # simulate post-map-switch reset
    areas_blob = {
        "areas": [
            {"id": 5, "area_meta_data": '{"name":"Kitchen"}'},
            {"id": 6, "area_meta_data": '{"name":"Bedroom"}'},
        ]
    }

    coordinator._check_for_new_areas(areas_blob)

    # Signal is deferred through loop.call_soon so that self.data is updated
    # before callbacks run.  Verify call_soon was invoked with the correct args.
    expected_signal = f"rowenta_roboeye_areas_updated_{coordinator.config_entry.entry_id}"
    coordinator.hass.loop.call_soon.assert_called_once_with(
        async_dispatcher_send, coordinator.hass, expected_signal
    )
    assert coordinator._known_area_ids == {5, 6}


def test_check_for_new_areas_no_signal_when_empty_response(coordinator):
    """No signal when _known_area_ids is empty but API returns empty areas."""
    coordinator._known_area_ids = set()

    coordinator._check_for_new_areas({"areas": []})

    coordinator.hass.loop.call_soon.assert_not_called()


def test_check_for_new_areas_signals_on_change(coordinator):
    """Signal fires (via call_soon) when area set differs from known set."""
    from custom_components.rowenta_roboeye.coordinator import async_dispatcher_send

    coordinator._known_area_ids = {5}  # previously had area 5
    areas_blob = {"areas": [{"id": 5}, {"id": 7}]}  # now area 7 added

    coordinator._check_for_new_areas(areas_blob)

    coordinator.hass.loop.call_soon.assert_called_once()
    call_args = coordinator.hass.loop.call_soon.call_args
    assert call_args[0][0] is async_dispatcher_send
    assert coordinator._known_area_ids == {5, 7}


def test_check_for_new_areas_no_signal_when_unchanged(coordinator):
    """No signal when area set is identical to known set."""
    coordinator._known_area_ids = {5, 6}
    areas_blob = {"areas": [{"id": 5}, {"id": 6}]}

    coordinator._check_for_new_areas(areas_blob)

    coordinator.hass.loop.call_soon.assert_not_called()


# ── Pause / Resume — command queue drain and restore ──────────────────

@pytest.mark.asyncio
async def test_stop_drains_queue_to_paused_jobs(coordinator, mock_client):
    """Stop saves pending clean commands to _paused_jobs instead of discarding."""
    # Pre-fill queue with a clean_map job (worker not started)
    coordinator._command_sequence = 0
    await coordinator._command_queue.put(
        (1, 1, coordinator.client.clean_map, (),
         {"map_id": "3", "area_ids": "30", "cleaning_parameter_set": "2",
          "strategy_mode": "4"})
    )
    assert coordinator._command_queue.qsize() == 1

    # Enqueue stop — should drain clean_map into _paused_jobs
    await coordinator.async_send_command(
        coordinator.client.stop, label="stop(pause)"
    )

    assert coordinator._is_paused is True
    assert len(coordinator._paused_jobs) == 1
    # The saved job should be the clean_map we pre-filled
    _p, _s, saved_func, _a, _kw = coordinator._paused_jobs[0]
    assert saved_func is coordinator.client.clean_map
    # Queue now only has the stop command
    assert coordinator._command_queue.qsize() == 1


@pytest.mark.asyncio
async def test_stop_sets_paused_fan_speed(coordinator, mock_client):
    """Fan speed is captured from coordinator.ha_fan_speed when stop is pressed."""
    coordinator.ha_fan_speed = "3"
    await coordinator.async_send_command(coordinator.client.stop, label="stop(pause)")
    assert coordinator._paused_fan_speed == "3"


@pytest.mark.asyncio
async def test_resume_clears_pause_state_and_re_enqueues(coordinator, mock_client):
    """clean_start_or_continue clears _is_paused and re-enqueues saved jobs.

    With no _paused_clean_command (e.g. error recovery / external pause), the
    legacy path is used: clean_start_or_continue is dispatched followed by
    the re-enqueued saved jobs.
    """
    # Set up paused state with one saved job
    coordinator._is_paused = True
    coordinator._paused_jobs = [
        (1, 99, coordinator.client.clean_map, (),
         {"map_id": "3", "area_ids": "32", "cleaning_parameter_set": "2"})
    ]

    await coordinator.async_send_command(
        coordinator.client.clean_start_or_continue,
        label="clean_start_or_continue",
        cleaning_parameter_set="2",
    )

    assert coordinator._is_paused is False
    assert coordinator._paused_jobs == []
    # Queue: resume command + re-enqueued clean_map
    assert coordinator._command_queue.qsize() == 2


@pytest.mark.asyncio
async def test_resume_after_ha_pause_redispatches_clean_map(coordinator, mock_client):
    """After HA-initiated pause (_paused_clean_command set), resume must
    re-dispatch the original clean_map and re-enqueue saved jobs after it.

    Regression test for: pressing resume on the vacuum card after pausing
    a multi-room queue caused the robot to skip the paused room and start
    the next pending room. clean_start_or_continue is unreliable after
    /set/stop because the firmware may have abandoned the cleaning session.
    """
    # Simulate state right after async_pause has run:
    coordinator._is_paused = True
    coordinator._paused_clean_command = (
        coordinator.client.clean_map,
        {"map_id": "3", "area_ids": "31",
         "cleaning_parameter_set": "2", "strategy_mode": "4"},
        42,
    )
    # Коридор was pending when stop was pressed → drained to _paused_jobs
    coordinator._paused_jobs = [
        (1, 99, coordinator.client.clean_map, (),
         {"map_id": "3", "area_ids": "32",
          "cleaning_parameter_set": "2", "strategy_mode": "4"})
    ]
    coordinator._paused_fan_speed = "2"

    await coordinator.async_send_command(
        coordinator.client.clean_start_or_continue,
        label="clean_start_or_continue",
        cleaning_parameter_set="2",
    )

    # Pause state fully cleared
    assert coordinator._is_paused is False
    assert coordinator._paused_jobs == []
    assert coordinator._paused_clean_command is None
    assert coordinator._paused_fan_speed is None

    # Queue must contain the re-dispatched clean_map for the paused room
    # FOLLOWED by the previously saved job — and NO clean_start_or_continue.
    queue_items = sorted(list(coordinator._command_queue._queue))
    assert len(queue_items) == 2
    func_names = [_command_name(item[2]) for item in queue_items]
    assert func_names == ["clean_map", "clean_map"]
    # The re-dispatched paused room (Кухня) must come first
    assert queue_items[0][4]["area_ids"] == "31"
    # Followed by the saved pending job (Коридор)
    assert queue_items[1][4]["area_ids"] == "32"

    # clean_start_or_continue must NOT have been enqueued
    assert all(
        _command_name(item[2]) != "clean_start_or_continue"
        for item in queue_items
    )


@pytest.mark.asyncio
async def test_go_home_discards_paused_jobs(coordinator, mock_client):
    """go_home clears both the queue and _paused_jobs — full stop, no resume."""
    coordinator._is_paused = True
    coordinator._paused_jobs = [
        (1, 99, coordinator.client.clean_map, (), {"map_id": "3", "area_ids": "30"})
    ]
    # Pre-fill queue with a pending command too
    await coordinator._command_queue.put(
        (1, 100, coordinator.client.clean_map, (), {"map_id": "3", "area_ids": "32"})
    )

    await coordinator.async_send_command(coordinator.client.go_home, label="go_home")

    assert coordinator._paused_jobs == []
    assert coordinator._is_paused is False
    # Queue should only have go_home
    assert coordinator._command_queue.qsize() == 1


@pytest.mark.asyncio
async def test_is_paused_property(coordinator, mock_client):
    """is_paused property reflects _is_paused."""
    assert coordinator.is_paused is False
    coordinator._is_paused = True
    assert coordinator.is_paused is True


@pytest.mark.asyncio
async def test_paused_fan_speed_property(coordinator, mock_client):
    """paused_fan_speed property reflects _paused_fan_speed."""
    assert coordinator.paused_fan_speed is None
    coordinator._paused_fan_speed = "3"
    assert coordinator.paused_fan_speed == "3"


@pytest.mark.asyncio
async def test_wait_for_robot_idle_treats_aborted_as_done(coordinator, mock_client):
    """'aborted' is a terminal status — must not keep polling."""
    mock_client.get_command_result.return_value = {
        "commands": [
            {"cmd_id": 127, "status": "aborted", "error_code": 0},
            {"cmd_id": 128, "status": "done", "error_code": 0},
        ]
    }
    await coordinator._wait_for_robot_idle(cmd_id=127)
    # Should return after a single poll — aborted is terminal
    mock_client.get_command_result.assert_called_once()


@pytest.mark.asyncio
async def test_stop_does_not_drain_existing_stop_from_queue(coordinator, mock_client):
    """A stop already in the queue should not be saved to _paused_jobs."""
    # Put a stop in the queue
    coordinator._command_sequence = 5
    await coordinator._command_queue.put(
        (0, 5, coordinator.client.stop, (), {})
    )
    # Enqueue another stop — the existing stop should be discarded, not saved
    await coordinator.async_send_command(coordinator.client.stop, label="stop(pause)")

    # _paused_jobs should NOT contain the earlier stop
    assert all(
        getattr(item[2], "__name__", "") != "stop"
        for item in coordinator._paused_jobs
    )


@pytest.mark.asyncio
async def test_resume_worker_dispatches_paused_clean_map(coordinator, mock_client):
    """After HA stop+resume the worker must dispatch the original clean_map.

    Regression test for: pressing resume on the vacuum card after pausing a
    multi-room queue caused the robot to skip the paused room and start the
    next pending room. The fix re-dispatches the paused clean_map directly
    instead of relying on /set/clean_start_or_continue, which is unreliable
    after /set/stop.
    """
    coordinator.async_request_refresh = AsyncMock()

    # The re-dispatched clean_map returns a fresh cmd_id
    mock_client.clean_map.return_value = {"cmd_id": 67, "error_code": 0}
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 67, "status": "done", "error_code": 0}]
    }
    # Robot returns to ready quickly so the worker exits its wait loop
    mock_client.get_status.return_value = {"mode": "ready"}

    # Simulate state after stop was pressed: original clean_map context saved in paused slot
    coordinator._paused_clean_command = (
        mock_client.clean_map,
        {"map_id": "3", "area_ids": "11",
         "cleaning_parameter_set": "2", "strategy_mode": "4"},
        42,  # stale cmd_id from the aborted clean_map
    )
    coordinator._is_paused = True

    await coordinator.async_send_command(
        mock_client.clean_start_or_continue,
        cleaning_parameter_set="2",
    )

    # Pause state cleared at enqueue time
    assert coordinator._paused_clean_command is None
    assert coordinator._is_paused is False

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("asyncio.sleep", AsyncMock())
        task = await _start_worker(coordinator)
        await _drain_and_cancel(coordinator, task)

    # Worker must have dispatched clean_map (not clean_start_or_continue)
    mock_client.clean_map.assert_called_once()
    mock_client.clean_start_or_continue.assert_not_called()
    # And it must use the original room kwargs
    call_kwargs = mock_client.clean_map.call_args.kwargs
    assert call_kwargs.get("area_ids") == "11"


# ── Fix B: is_recharging_mid_clean ────────────────────────────────────

def test_is_recharging_mid_clean_true(coordinator):
    """mode=cleaning + charging=charging → recharge-and-continue detected."""
    coordinator.data = {DATA_STATUS: {
        "mode": "cleaning", "charging": "charging", "battery_level": 19
    }}
    assert coordinator.is_recharging_mid_clean is True


def test_is_recharging_mid_clean_false_normal_cleaning(coordinator):
    """Normal cleaning (unconnected) must NOT be flagged as recharging."""
    coordinator.data = {DATA_STATUS: {
        "mode": "cleaning", "charging": "unconnected", "battery_level": 80
    }}
    assert coordinator.is_recharging_mid_clean is False


def test_is_recharging_mid_clean_false_docked(coordinator):
    """Normal docked state must NOT be flagged as recharging mid-clean."""
    coordinator.data = {DATA_STATUS: {
        "mode": "ready", "charging": "charging", "battery_level": 50
    }}
    assert coordinator.is_recharging_mid_clean is False


@pytest.mark.asyncio
async def test_wait_for_robot_idle_extends_during_recharge(coordinator, mock_client):
    """_wait_for_robot_idle resets its deadline and polls slowly during recharge.

    Three iterations: first two simulate recharge state (coordinator.data shows
    mode=cleaning+charging=charging), the third shows normal cleaning so the
    recharge branch exits and normal cmd_id polling resumes.
    The command result returns "done" immediately so the function returns.
    """
    poll_count = 0

    original_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        nonlocal poll_count
        poll_count += 1
        # After two slow recharge polls, flip coordinator to normal cleaning
        if poll_count >= 2:
            coordinator.data = {DATA_STATUS: {
                "mode": "cleaning", "charging": "unconnected", "battery_level": 60
            }}

    coordinator.data = {DATA_STATUS: {
        "mode": "cleaning", "charging": "charging", "battery_level": 25
    }}
    mock_client.get_command_result.return_value = {
        "commands": [{"cmd_id": 212, "status": "done", "error_code": 0}]
    }

    import unittest.mock as _mock
    with _mock.patch("asyncio.sleep", fake_sleep):
        await coordinator._wait_for_robot_idle(cmd_id=212)

    # Recharge branch polled at least once before falling through to cmd_id check
    assert poll_count >= 1


# ── Fix A: modify_area / set_fan_speed bypass queue ───────────────────

@pytest.mark.asyncio
async def test_modify_area_bypasses_queue(coordinator, mock_client):
    """modify_area fires immediately, never enters the serial command queue."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.modify_area.return_value = {"cmd_id": 99, "error_code": 0}
    # Give modify_area the right __name__
    mock_client.modify_area.__name__ = "modify_area"

    await coordinator.async_send_command(
        mock_client.modify_area,
        map_id="3", area_id="10", cleaning_parameter_set="2",
    )

    mock_client.modify_area.assert_called_once_with(
        map_id="3", area_id="10", cleaning_parameter_set="2"
    )
    # Queue must remain empty — nothing was enqueued
    assert coordinator._command_queue.empty()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_set_fan_speed_bypasses_queue(coordinator, mock_client):
    """set_fan_speed fires immediately, never enters the serial command queue."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.set_fan_speed.return_value = {"cmd_id": 100, "error_code": 0}
    mock_client.set_fan_speed.__name__ = "set_fan_speed"

    await coordinator.async_send_command(
        mock_client.set_fan_speed, cleaning_parameter_set="3"
    )

    mock_client.set_fan_speed.assert_called_once_with(cleaning_parameter_set="3")
    assert coordinator._command_queue.empty()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_modify_area_error_logged_not_raised(coordinator, mock_client):
    """Non-zero error_code from modify_area is logged, not raised."""
    coordinator.async_request_refresh = AsyncMock()
    mock_client.modify_area.return_value = {"cmd_id": 0, "error_code": 5, "error_tag": "busy"}
    mock_client.modify_area.__name__ = "modify_area"

    # Must not raise
    await coordinator.async_send_command(mock_client.modify_area, area_id="10")
    mock_client.modify_area.assert_called_once()


# ── Fix C: queue_eta_seconds sums ALL queued jobs ─────────────────────

@pytest.mark.asyncio
async def test_queue_eta_sums_all_queued_jobs(coordinator):
    """The same 3-room job queued twice gives 2× the single-job ETA (no dedup)."""
    import json as _json

    coordinator.data = {
        DATA_STATUS: {"mode": "ready", "charging": "charging"},
        DATA_AREAS: {"areas": [
            {"id": 30, "area_meta_data": _json.dumps({"name": "hall"}),
             "statistics": {"average_cleaning_time": 420000}},   # 420 s
            {"id": 31, "area_meta_data": _json.dumps({"name": "office"}),
             "statistics": {"average_cleaning_time": 360000}},   # 360 s
            {"id": 32, "area_meta_data": _json.dumps({"name": "bedroom"}),
             "statistics": {"average_cleaning_time": 480000}},   # 480 s
        ]},
    }

    async def fake_clean_map(**kwargs):
        return {"cmd_id": 1}

    fake_clean_map.__name__ = "clean_map"

    coordinator._command_queue.put_nowait((1, 1, fake_clean_map, (), {"area_ids": "30,31,32"}))
    coordinator._command_queue.put_nowait((1, 2, fake_clean_map, (), {"area_ids": "30,31,32"}))

    eta = coordinator.queue_eta_seconds
    # 2 jobs × (420 + 360 + 480) s = 2 × 1260 = 2520 s
    assert eta == 2520


@pytest.mark.asyncio
async def test_queue_eta_none_during_recharge(coordinator):
    """ETA returns None during recharge-and-continue — time is indeterminate."""
    coordinator.data = {DATA_STATUS: {
        "mode": "cleaning", "charging": "charging", "battery_level": 22
    }}
    assert coordinator.queue_eta_seconds is None


@pytest.mark.asyncio
async def test_queue_eta_different_rooms_per_job(coordinator):
    """Each job's rooms are counted independently — no cross-job deduplication."""
    import json as _json

    coordinator.data = {
        DATA_STATUS: {"mode": "ready", "charging": "charging"},
        DATA_AREAS: {"areas": [
            {"id": 1, "area_meta_data": _json.dumps({"name": "a"}),
             "statistics": {"average_cleaning_time": 600000}},   # 600 s
            {"id": 2, "area_meta_data": _json.dumps({"name": "b"}),
             "statistics": {"average_cleaning_time": 300000}},   # 300 s
        ]},
    }

    async def job1(**kwargs): return {"cmd_id": 1}
    async def job2(**kwargs): return {"cmd_id": 2}
    job1.__name__ = "clean_map"
    job2.__name__ = "clean_map"

    coordinator._command_queue.put_nowait((1, 1, job1, (), {"area_ids": "1"}))
    coordinator._command_queue.put_nowait((1, 2, job2, (), {"area_ids": "2"}))

    eta = coordinator.queue_eta_seconds
    # 600 (job1 room1) + 300 (job2 room2) = 900 s
    assert eta == 900
