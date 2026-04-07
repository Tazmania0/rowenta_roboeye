"""DataUpdateCoordinator for the Rowenta Xplorer 120 integration.

Key improvements over v1:
- Tracks known_area_ids and fires SIGNAL_AREAS_UPDATED when the room set
  changes so platforms can add new entities WITHOUT a full reload.
- Adaptive live-map polling: every coordinator tick during cleaning, 60 s when idle.
- Exposes structured live_map data (floor_plan, cleaned_area, robot_position)
  for the Phase-2 SVG card via sensor.xplorer120_live_map.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, RobEyeApiClient
from .const import (
    AREA_STATE_BLOCKING,
    AREA_TYPE_AVOIDANCE,
    CMD_POLL_INTERVAL_S,
    CMD_POLL_TIMEOUT_S,
    DATA_AREAS,
    DATA_SENSOR_VALUES,
    DATA_AREAS_SAVED_MAP,
    DATA_CLEANING_GRID,
    DATA_EVENT_LOG,
    DATA_EXPLORATION,
    DATA_FEATURE_MAP,
    DATA_LIVE_MAP,
    DATA_LIVE_PARAMETERS,
    DATA_MAP_STATUS,
    DATA_MAPS,
    DATA_PERMANENT_STATISTICS,
    DATA_RELOCALIZATION,
    DATA_ROB_POSE,
    DATA_ROBOT_FLAGS,
    DATA_SCHEDULE,
    DATA_ROBOT_INFO,
    DATA_SEEN_POLYGON,
    DATA_SEEN_POLY_SAVED_MAP,
    DATA_SENSOR_STATUS,
    DATA_STATISTICS,
    DATA_STATUS,
    DATA_TILE_MAP,
    DOMAIN,
    EVENT_DUSTBIN_INSERTED,
    EVENT_DUSTBIN_MISSING,
    EVENT_ROBOT_LIFTED,
    EVENT_TYPE_LABELS,
    LOGGER,
    MAX_POLL_FAILURES,
    MODE_CLEANING,
    MODE_GO_HOME,
    QUEUE_POST_DOCK_DELAY_S,
    SCAN_INTERVAL_AREAS,
    SCAN_INTERVAL_EVENT_LOG,
    SCAN_INTERVAL_MAP_GEOMETRY,
    SCAN_INTERVAL_ROBOT_INFO,
    SCAN_INTERVAL_STATISTICS,
    SIGNAL_AREAS_UPDATED,
    STRATEGY_DEFAULT,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_CLEANING,
    UPDATE_INTERVAL_IDLE,
)


_MAX_PATH_POINTS = 2000
_MIN_MOVE_UNITS  = 5   # ~1 cm threshold before appending a new path point


class RobEyeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that merges multiple independently-timed API calls."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: RobEyeApiClient,
        map_id: str,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.map_id = map_id

        self._last_statistics: datetime | None = None
        self._last_areas: datetime | None = None
        self._last_robot_info: datetime | None = None
        self._last_live_map: datetime | None = None
        self._last_map_geometry: datetime | None = None
        self._last_schedule: datetime | None = None
        self._consecutive_failures: int = 0

        # Track known area IDs so we can detect additions/removals without reload
        self._known_area_ids: set = set()
        # Which map_id was active when DATA_AREAS was last fetched.
        # Compared by platforms before creating entities to avoid stale-signal races.
        self._areas_fetched_for_map_id: str | None = None

        # Cleaning strategy — set by strategy select or deep-clean switch; read by all clean operations
        self.cleaning_strategy: str = STRATEGY_DEFAULT

        # Last explicitly chosen non-deep strategy ("1", "2", or "4").
        # Preserved so that turning off the deep-clean switch restores the user's prior
        # strategy choice rather than resetting to STRATEGY_DEFAULT.
        # Updated by RobEyeStrategySelect whenever a non-deep option is selected or restored.
        self.last_non_deep_strategy: str = STRATEGY_DEFAULT

        # HA-preferred fan speed raw value ("1"–"4").
        # Set once from device on first setup (or state restore); thereafter HA takes precedence
        # and this value is never overwritten by coordinator polls.
        self.ha_fan_speed: str | None = None

        # Brush stuck notification state tracking
        self._brush_left_notified: bool = False
        self._brush_right_notified: bool = False

        # Live session map tracking
        self._operation_map_id: str = map_id  # current session map; changes each new clean
        self._last_active_map_id: str = map_id  # tracks floor changes
        self._manual_map_id: str | None = None  # user override via Select entity

        # Last-session replay state
        self._robot_path: list[tuple[float, float]] = []
        self._last_session_grid: dict = {}
        self._last_session_path: list = []
        self._last_session_outline: list = []
        self._last_session_map_id: str = ""  # map_id the frozen session belongs to
        self._last_mode: str = ""
        self._session_complete: bool = False
        self._last_rob_pose_ts: int = 0   # last seen /get/rob_pose timestamp value

        # Serial command queue — all commands are enqueued and dispatched one at a time.
        # PriorityQueue lets /set/stop jump ahead of normal commands.
        # Item shape: (priority, sequence, coro_func, args, kwargs)
        self._command_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._command_sequence: int = 0
        self._active_command: tuple[int, int, Any, tuple[Any, ...], dict[str, Any]] | None = None
        self._command_worker_task: asyncio.Task | None = None
        # Last dispatched cleaning command shown as "active" until robot
        # actually leaves active modes (cleaning/go_home).
        self._inflight_clean_command: tuple[Any, dict[str, Any]] | None = None

        # Event log incremental polling state
        self._last_event_log_id: int = 0
        self._last_event_log: datetime | None = None
        self._event_log_seeded: bool = False
        self._recent_events: list[dict[str, Any]] = []  # last 50 top-level events

    def _is_immediate_command(self, coro_func: Any) -> bool:
        """Return True for commands that must bypass normal queue waiting."""
        command_name = getattr(coro_func, "__name__", "")
        return (
            coro_func in (
                self.client.stop,
                self.client.go_home,
                self.client.clean_start_or_continue,
            )
            or command_name in ("stop", "go_home", "clean_start_or_continue")
        )

    def _has_immediate_command_pending(self) -> bool:
        """Return True when a high-priority command is waiting in the queue."""
        try:
            pending_items = list(self._command_queue._queue)
        except AttributeError:
            return False
        return any(priority == 0 for priority, *_rest in pending_items)

    # ── Device identifier ─────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        """Stable device identifier (serial or entry_id fallback)."""
        robot_id_data = self.robot_info.get("robot_id", {})
        serial = (
            robot_id_data.get("serial_number")
            or robot_id_data.get("robot_id")
            or robot_id_data.get("id")
        )
        if serial:
            return str(serial).lower().replace("-", "_").replace(" ", "_")
        return self.config_entry.entry_id.lower()

    # ── Active map tracking ─────────────────────────────────────────────

    @property
    def active_map_id(self) -> str:
        """Runtime map ID: manual HA selection > setup-time config map_id.

        Always follows what the user has configured in HA.  The device's own
        /get/map_status is intentionally ignored here — the native app can
        change the active floor at any time and HA must not silently follow it.
        """
        return self._manual_map_id or self.map_id

    @property
    def available_maps(self) -> list[dict]:
        """Normalised list of permanent floor maps from /get/maps.

        Each entry:
          map_id:       "3"
          display_name: "Дружба"  or  "Map 2"  (native app naming logic)
          user_name:    "Дружба"  or  ""       (raw map_meta_data, stripped)
          is_active:    True / False            (matches active_map_id)
          statistics:   {area_size, cleaning_counter, last_cleaned, ...}

        Non-permanent maps (temporary/live session maps) are excluded.
        Returns empty list until /get/maps has been fetched.
        """
        raw = (self.data or {}).get(DATA_MAPS, {})
        maps_list = raw.get("maps", []) if isinstance(raw, dict) else []
        active = self.active_map_id

        result = []
        position = 0
        for entry in maps_list:
            position += 1
            parsed = _parse_map_entry(entry, position=position, active_map_id=active)
            if parsed:
                result.append(parsed)

        return result

    # ── Active map switching ──────────────────────────────────────────

    @property
    def areas_map_id(self) -> str | None:
        """Map ID for which DATA_AREAS was last fetched.

        Platform entity-builders compare this against active_map_id to guard
        against stale-signal races when the user switches maps rapidly.
        """
        return self._areas_fetched_for_map_id

    async def async_set_active_map(self, map_id: str) -> None:
        """Override the active map and force-reload all map-dependent data."""
        self._manual_map_id = map_id
        self._last_active_map_id = map_id
        self._last_areas = None
        self._last_map_geometry = None
        self._areas_fetched_for_map_id = None  # invalidate until refresh fetches new areas
        self._known_area_ids = set()
        self._robot_path = []
        self._last_session_grid = {}
        self._last_session_path = []
        self._last_session_outline = []
        self._last_session_map_id = ""
        self._session_complete = False
        dashboard_manager = self.hass.data.get(DOMAIN, {}).get(
            f"{self.config_entry.entry_id}_dashboard"
        )
        if dashboard_manager:
            dashboard_manager.invalidate()
        await self.async_request_refresh()

    # ── Entity state helpers ───────────────────────────────────────────

    def _is_live_map_enabled(self) -> bool:
        """Return True if the live_map sensor entity is enabled in the entity registry.

        Falls back to True when the entity has not been registered yet (first startup).
        """
        ent_reg = er.async_get(self.hass)
        entity_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"live_map_{self.device_id}"
        )
        if entity_id is None:
            return True  # not yet registered — assume enabled
        entry = ent_reg.async_get(entity_id)
        return entry is None or not entry.disabled

    # ── Core update ───────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.utcnow()
        data: dict[str, Any] = dict(self.data or {})

        try:
            # ── Every 15 s: status ───────────────────────────────────
            data[DATA_STATUS] = await self.client.get_status()

            # ── Every cycle: sensor values (GPIO, dustbin, brushes) ──
            try:
                raw_sv = await self.client.get_sensor_values()
                data[DATA_SENSOR_VALUES] = raw_sv
                parsed_sv = _parse_sensor_values(raw_sv)
                data["sensor_values_parsed"] = parsed_sv
                # Fire persistent notification when a brush becomes newly stuck
                from homeassistant.components import persistent_notification
                for side, descriptor, notified_attr in (
                    ("Left",  "side_brush_left_stuck",  "_brush_left_notified"),
                    ("Right", "side_brush_right_stuck", "_brush_right_notified"),
                ):
                    stuck = _gpio(parsed_sv, descriptor) == "active"
                    was_notified = getattr(self, notified_attr)
                    if stuck and not was_notified:
                        persistent_notification.async_create(
                            self.hass,
                            (
                                f"\u26a0\ufe0f {side} side brush is stuck or wrapped. "
                                "Please check and clean it before the next run."
                            ),
                            title="Rowenta \u2014 Brush Alert",
                            notification_id=f"rowenta_brush_{descriptor}",
                        )
                        setattr(self, notified_attr, True)
                    elif not stuck:
                        setattr(self, notified_attr, False)
            except CannotConnect:
                LOGGER.debug("get_sensor_values unavailable, skipping")

            mode = data[DATA_STATUS].get("mode", "")
            is_active = mode in (MODE_CLEANING, MODE_GO_HOME)
            if not is_active:
                self._inflight_clean_command = None

            # ── Dynamically adjust polling rate ───────────────────────
            target_interval = UPDATE_INTERVAL_CLEANING if is_active else UPDATE_INTERVAL_IDLE
            if self.update_interval != target_interval:
                self.update_interval = target_interval
                LOGGER.debug(
                    "Coordinator interval → %s",
                    "5s" if is_active else "15s",
                )

            # ── Session lifecycle tracking ────────────────────────────
            was_cleaning = self._last_mode == MODE_CLEANING
            now_docked   = not is_active and mode not in (MODE_CLEANING, MODE_GO_HOME)

            if mode == MODE_CLEANING and self._last_mode != MODE_CLEANING:
                self._robot_path = []
                self._last_session_grid = {}
                self._last_session_path = []
                self._last_session_outline = []
                self._last_session_map_id = ""
                self._session_complete = False
                self._last_live_map = None
                self._last_rob_pose_ts = 0
                LOGGER.debug("Cleaning session started — state reset")

            if was_cleaning and now_docked and not self._session_complete:
                self._last_session_grid    = data.get(DATA_CLEANING_GRID, {})
                self._last_session_path    = list(self._robot_path)
                self._last_session_outline = _parse_live_outline(
                    data.get(DATA_SEEN_POLYGON, {})
                )
                self._last_session_map_id  = str(
                    self._last_session_grid.get("map_id", self.active_map_id)
                )
                self._session_complete = True
                LOGGER.info(
                    "Cleaning session complete — frozen grid=%s cells, path=%d points, map_id=%s",
                    self._last_session_grid.get("size_x", 0),
                    len(self._last_session_path),
                    self._last_session_map_id,
                )

            self._last_mode = mode

            # Effective map ID for this update cycle — always HA-configured,
            # never the device's dynamically reported active map.
            _active_map_id: str = self._manual_map_id or self.map_id

            # ── Every 600 s: saved-map geometry (walls, rooms, outline) ──
            # Fetched BEFORE the live-map block so that feature_map / tile_map /
            # areas_saved_map are available the very first time _build_live_map_payload
            # is called (otherwise the map stays blank for the first 60 s after startup).
            if self._is_live_map_enabled() and (
                self._last_map_geometry is None or (
                    now - self._last_map_geometry
                ) >= timedelta(seconds=SCAN_INTERVAL_MAP_GEOMETRY)
            ):
                try:
                    data[DATA_FEATURE_MAP] = await self.client.get_feature_map(_active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_feature_map unavailable, skipping")
                try:
                    data[DATA_TILE_MAP] = await self.client.get_tile_map(_active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_tile_map unavailable, skipping")
                try:
                    data[DATA_AREAS_SAVED_MAP] = await self.client.get_areas(_active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_areas (map geometry) unavailable, skipping")
                try:
                    data[DATA_SEEN_POLY_SAVED_MAP] = await self.client.get_seen_polygon(_active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_seen_polygon (map geometry) unavailable, skipping")

                # Load last-session grid from saved map (also runs on startup)
                if not is_active:
                    try:
                        saved_grid = await self.client.get_cleaning_grid_map(
                            map_id=_active_map_id
                        )
                        if saved_grid.get("size_x", 0) > 0:
                            self._last_session_grid = saved_grid
                            self._last_session_map_id = str(
                                saved_grid.get("map_id", _active_map_id)
                            )
                            if not self._session_complete:
                                self._session_complete = True
                                LOGGER.info(
                                    "Loaded last session grid from saved map: %d×%d map_id=%s",
                                    saved_grid["size_x"],
                                    saved_grid["size_y"],
                                    self._last_session_map_id,
                                )
                    except CannotConnect:
                        LOGGER.debug("get_cleaning_grid_map saved map unavailable")

                self._last_map_geometry = now

            # ── Live-map polling ──────────────────────────────────────
            # Skipped entirely when the live_map sensor entity is disabled.
            if self._is_live_map_enabled():
                if is_active or self._last_live_map is None or (
                    now - self._last_live_map
                ) >= timedelta(seconds=60):

                    # ── POSITION BUCKET — /get/rob_pose (all states) ──────
                    robot_position: dict | None = None
                    try:
                        rob_pose_raw = await self.client.get_rob_pose()
                        data[DATA_ROB_POSE] = rob_pose_raw
                        robot_position = _extract_rob_pose(rob_pose_raw)
                    except CannotConnect:
                        LOGGER.debug("get_rob_pose unavailable")
                        cached = data.get(DATA_ROB_POSE)
                        if cached:
                            robot_position = _extract_rob_pose(cached)

                    # Staleness detection using timestamp field
                    if robot_position and robot_position.get("timestamp"):
                        new_ts = robot_position["timestamp"]
                        if new_ts == self._last_rob_pose_ts and is_active:
                            LOGGER.debug(
                                "rob_pose timestamp unchanged (%d) — position may be stale",
                                new_ts,
                            )
                        self._last_rob_pose_ts = new_ts

                    # ── LIVE MAP POLYGON BUCKET — every 5 s when cleaning ─
                    seen_polygon_raw: dict = {}
                    cleaning_grid: dict = {}

                    live_map_due = is_active and (
                        self._last_live_map is None
                        or (now - self._last_live_map).total_seconds() >= 5
                    )

                    if live_map_due:
                        try:
                            seen_polygon_raw = await self.client.get_seen_polygon()
                            data[DATA_SEEN_POLYGON] = seen_polygon_raw
                        except CannotConnect:
                            LOGGER.debug("get_seen_polygon unavailable")
                            seen_polygon_raw = data.get(DATA_SEEN_POLYGON, {})

                        try:
                            cleaning_grid = await self.client.get_cleaning_grid_map()
                            data[DATA_CLEANING_GRID] = cleaning_grid
                        except CannotConnect:
                            LOGGER.debug("get_cleaning_grid_map unavailable")
                            cleaning_grid = data.get(DATA_CLEANING_GRID, {})
                    else:
                        seen_polygon_raw = data.get(DATA_SEEN_POLYGON, {})
                        cleaning_grid = data.get(DATA_CLEANING_GRID, {})

                    # ── Accumulate robot path during cleaning ─────────────
                    if is_active and robot_position:
                        pt: tuple[float, float] = (robot_position["x"], robot_position["y"])
                        if (
                            not self._robot_path
                            or (pt[0] - self._robot_path[-1][0]) ** 2
                            + (pt[1] - self._robot_path[-1][1]) ** 2
                            >= _MIN_MOVE_UNITS ** 2
                        ):
                            self._robot_path.append(pt)
                            if len(self._robot_path) > _MAX_PATH_POINTS:
                                self._robot_path = self._robot_path[-_MAX_PATH_POINTS:]

                    data[DATA_LIVE_MAP] = _build_live_map_payload(
                        existing=data.get(DATA_LIVE_MAP, {}),
                        robot_position=robot_position,
                        seen_polygon_raw=seen_polygon_raw,
                        cleaning_grid=cleaning_grid,
                        feature_map=data.get(DATA_FEATURE_MAP, {}),
                        tile_map=data.get(DATA_TILE_MAP, {}),
                        areas_data=data.get(DATA_AREAS_SAVED_MAP, {}),
                        seen_poly_saved_map=data.get(DATA_SEEN_POLY_SAVED_MAP, {}),
                        is_active=is_active,
                        map_id=_active_map_id,
                        robot_path=self._robot_path,
                        last_session_grid=self._last_session_grid,
                        last_session_path=self._last_session_path,
                        last_session_outline=self._last_session_outline,
                        last_session_map_id=self._last_session_map_id,
                        session_complete=self._session_complete,
                    )

                    self._last_live_map = now

            # ── Every 300 s: areas + sensor status ───────────────────
            if self._last_areas is None or (
                now - self._last_areas
            ) >= timedelta(seconds=SCAN_INTERVAL_AREAS):
                # Fetch map_status and maps for informational display (map list,
                # is_active flag in available_maps).  Not used to determine
                # which map HA should display — that is always the HA-configured
                # map_id (see active_map_id property).
                try:
                    data[DATA_MAP_STATUS] = await self.client.get_map_status()
                except CannotConnect:
                    LOGGER.debug("get_map_status unavailable, skipping")

                try:
                    data[DATA_MAPS] = await self.client.get_maps()
                except CannotConnect:
                    LOGGER.debug("get_maps unavailable, skipping")

                # Always use the HA-configured map ID (manual override or setup map_id).
                _fetched_for = self._manual_map_id or self.map_id
                new_areas_blob = await self.client.get_areas(_fetched_for)
                self._areas_fetched_for_map_id = _fetched_for
                data[DATA_AREAS] = new_areas_blob

                try:
                    data[DATA_SENSOR_STATUS] = await self.client.get_sensor_status()
                except CannotConnect:
                    LOGGER.debug("get_sensor_status unavailable, skipping")
                try:
                    data[DATA_ROBOT_FLAGS] = await self.client.get_robot_flags()
                except CannotConnect:
                    LOGGER.debug("get_robot_flags unavailable, skipping")

                self._last_areas = now
                self._check_for_new_areas(new_areas_blob)

            # ── Every 600 s: lifetime statistics ─────────────────────
            if self._last_statistics is None or (
                now - self._last_statistics
            ) >= timedelta(seconds=SCAN_INTERVAL_STATISTICS):
                data[DATA_STATISTICS] = await self.client.get_statistics()
                try:
                    data[DATA_PERMANENT_STATISTICS] = (
                        await self.client.get_permanent_statistics()
                    )
                except CannotConnect:
                    LOGGER.debug("get_permanent_statistics unavailable, skipping")
                self._last_statistics = now

            # ── Every 60 s: schedule ─────────────────────────────────
            if self._last_schedule is None or (
                now - self._last_schedule
            ) >= timedelta(seconds=60):
                try:
                    data[DATA_SCHEDULE] = await self.client.get_schedule()
                except CannotConnect:
                    LOGGER.debug("get_schedule unavailable, skipping")
                self._last_schedule = now

            # ── Every 30 s: incremental event log ────────────────────
            if self._last_event_log is None or (
                now - self._last_event_log
            ) >= timedelta(seconds=SCAN_INTERVAL_EVENT_LOG):
                try:
                    log_data = await self.client.get_event_log(
                        last_id=self._last_event_log_id
                    )
                    new_events = (
                        log_data.get("robot_events", [])
                        if isinstance(log_data, dict)
                        else []
                    )
                    if new_events:
                        self._last_event_log_id = new_events[-1]["id"]
                        # First fetch may contain historical entries from before HA
                        # boot; use it to seed the cursor without surfacing stale
                        # notifications/logs.
                        if self._event_log_seeded:
                            self._process_new_events(new_events)
                        else:
                            self._event_log_seeded = True
                except CannotConnect:
                    LOGGER.debug("get_event_log unavailable, skipping")
                self._last_event_log = now

            # ── Every 3600 s: robot identity / wifi ──────────────────
            if self._last_robot_info is None or (
                now - self._last_robot_info
            ) >= timedelta(seconds=SCAN_INTERVAL_ROBOT_INFO):
                robot_info: dict[str, Any] = {}
                for fetch, key in (
                    (self.client.get_robot_id, "robot_id"),
                    (self.client.get_wifi_status, "wifi_status"),
                    (self.client.get_protocol_version, "protocol_version"),
                ):
                    try:
                        robot_info[key] = await fetch()
                    except CannotConnect:
                        LOGGER.debug("%s unavailable, skipping", key)
                data[DATA_ROBOT_INFO] = robot_info
                self._last_robot_info = now

            self._consecutive_failures = 0
            return data

        except CannotConnect as err:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_POLL_FAILURES:
                LOGGER.warning(
                    "Rowenta RobEye: %d consecutive poll failures — "
                    "check connectivity or update IP via Options Flow",
                    self._consecutive_failures,
                )
            raise UpdateFailed(f"RobEye API error: {err}") from err

    # ── Dynamic area discovery ────────────────────────────────────────

    def _check_for_new_areas(self, areas_blob: dict[str, Any]) -> None:
        """Compare fresh areas to known set.

        When any new area IDs appear (or disappear), dispatch
        SIGNAL_AREAS_UPDATED so platform listeners can add/remove entities
        without a full integration reload.

        The signal is deferred via loop.call_soon so it fires AFTER
        _async_update_data returns and self.data is updated.  Without this
        deferral all platform callbacks would read the stale self.data
        (old map areas) and create entities for the wrong map.
        """
        areas = (
            areas_blob.get("areas", []) if isinstance(areas_blob, dict) else []
        )
        current_ids: set = {
            a.get("id") for a in areas if a.get("id") is not None
        }

        signal = f"{SIGNAL_AREAS_UPDATED}_{self.config_entry.entry_id}"

        if not self._known_area_ids:
            self._known_area_ids = current_ids
            # Fire signal so platforms create entities for the new map's rooms.
            # This handles the map-switch case where async_set_active_map() resets
            # _known_area_ids to set() — the first new-areas response must signal.
            if current_ids:
                LOGGER.debug(
                    "RobEye: initial/post-switch areas loaded (%d areas), signalling platforms",
                    len(current_ids),
                )
                self.hass.loop.call_soon(async_dispatcher_send, self.hass, signal)
            return

        if current_ids != self._known_area_ids:
            new_ids = current_ids - self._known_area_ids
            removed_ids = self._known_area_ids - current_ids
            LOGGER.info(
                "RobEye: area set changed — new=%s removed=%s, signalling platforms",
                new_ids,
                removed_ids,
            )
            self._known_area_ids = current_ids
            self.hass.loop.call_soon(async_dispatcher_send, self.hass, signal)

    # ── Convenience properties ────────────────────────────────────────

    @property
    def status(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_STATUS, {})

    @property
    def statistics(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_STATISTICS, {})

    @property
    def permanent_statistics(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_PERMANENT_STATISTICS, {})

    @property
    def areas(self) -> list[dict[str, Any]]:
        blob = (self.data or {}).get(DATA_AREAS, {})
        return blob.get("areas", []) if isinstance(blob, dict) else []

    @property
    def live_parameters(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_LIVE_PARAMETERS, {})

    @property
    def seen_polygon(self) -> list:
        return (self.data or {}).get(DATA_SEEN_POLYGON, [])

    @property
    def live_map(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_LIVE_MAP, {})

    @property
    def sensor_status(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_SENSOR_STATUS, {})

    @property
    def robot_flags(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_ROBOT_FLAGS, {})

    @property
    def sensor_values_parsed(self) -> dict[str, Any]:
        return (self.data or {}).get("sensor_values_parsed", {})

    @property
    def schedule(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_SCHEDULE, {})

    @property
    def robot_info(self) -> dict[str, Any]:
        return (self.data or {}).get(DATA_ROBOT_INFO, {})

    @property
    def last_session_grid(self) -> dict:
        return self._last_session_grid

    @property
    def last_session_path(self) -> list:
        return self._last_session_path

    @property
    def last_session_outline(self) -> list:
        return self._last_session_outline

    @property
    def session_complete(self) -> bool:
        return self._session_complete

    # ── Command queue ─────────────────────────────────────────────────

    def async_start_command_worker(self) -> None:
        """Start the background command queue worker. Call once after setup."""
        if self._command_worker_task is None or self._command_worker_task.done():
            self._command_worker_task = self.hass.async_create_background_task(
                self._command_queue_worker(),
                name=f"rowenta_roboeye_{self.config_entry.entry_id}_cmd_worker",
            )

    async def _command_queue_worker(self) -> None:
        """Process commands from the queue one at a time.

        Each item: (priority, coro_func, args, kwargs).

        Every /set/ response returns a cmd_id (success) or error_code (failure).
        The worker captures cmd_id immediately and uses it to poll
        /get/command_result for that exact command, rather than assuming the
        most recent entry is the one it sent.

        On error (non-zero error_code): logs and moves to next command.
        """
        while True:
            _priority, _seq, coro_func, args, kwargs = await self._command_queue.get()
            self._active_command = (_priority, _seq, coro_func, args, kwargs)
            command_name = getattr(coro_func, "__name__", "")
            is_cleaning_dispatch = (
                coro_func in (
                    self.client.clean_map,
                    self.client.clean_all,
                    self.client.clean_start_or_continue,
                )
                or command_name in ("clean_map", "clean_all", "clean_start_or_continue")
            )
            is_return_or_stop = (
                coro_func in (self.client.stop, self.client.go_home)
                or command_name in ("stop", "go_home")
            )
            if is_cleaning_dispatch:
                self._inflight_clean_command = (coro_func, dict(kwargs))
            elif is_return_or_stop:
                self._inflight_clean_command = None
            if hasattr(self, "async_update_listeners"):
                self.async_update_listeners()
            try:
                LOGGER.debug("RobEye queue: dispatching %s", coro_func.__name__)
                response = await coro_func(*args, **kwargs)

                # Capture cmd_id from the /set/ response for precise tracking.
                # All /set/ methods return dict with cmd_id on success.
                dispatched_cmd_id: int | None = None
                if isinstance(response, dict):
                    error_code = response.get("error_code", 0)
                    if error_code != 0:
                        LOGGER.error(
                            "RobEye command %s failed: error_code=%s tag=%s",
                            coro_func.__name__,
                            error_code,
                            response.get("error_tag", ""),
                        )
                        continue   # skip polling — command was rejected
                    dispatched_cmd_id = response.get("cmd_id")
                    LOGGER.debug(
                        "RobEye queue: cmd_id=%s dispatched", dispatched_cmd_id
                    )

                await self._wait_for_robot_idle(cmd_id=dispatched_cmd_id)
                if is_cleaning_dispatch or coro_func is self.client.go_home or command_name == "go_home":
                    await self._wait_for_active_operation_end()
                    await asyncio.sleep(QUEUE_POST_DOCK_DELAY_S)
                await self.async_request_refresh()
            except CannotConnect as err:
                LOGGER.error("RobEye command failed: %s", err)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                LOGGER.error("RobEye command worker error: %s", err)
            finally:
                self._active_command = None
                self._command_queue.task_done()
                # Notify listeners (e.g. queue status sensor) of state change
                if hasattr(self, "async_update_listeners"):
                    self.async_update_listeners()

    async def _wait_for_robot_idle(self, cmd_id: int | None = None) -> None:
        """Poll /get/command_result until the dispatched command finishes.

        Confirmed response shape (2026-04-05):
          {"commands": [{"cmd_id": N, "status": "executing"/"done"/"error"}]}

        If cmd_id is provided (captured from the /set/ response), polls for
        that exact command — eliminates ambiguity if commands overlap.
        If cmd_id is None, falls back to checking whether any command is executing.

        Polls every CMD_POLL_INTERVAL_S (5s) for up to CMD_POLL_TIMEOUT_S (30s).
        Robot operations (return to dock) take 10-15s.
        Proceeds on CannotConnect or timeout — never blocks the queue forever.
        """
        deadline = asyncio.get_event_loop().time() + CMD_POLL_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            try:
                result = await self.client.get_command_result()
                commands = result.get("commands", [])
                if not commands:
                    return

                if cmd_id is not None:
                    # Precise: find our exact cmd_id in the results
                    for cmd in commands:
                        if cmd.get("cmd_id") == cmd_id:
                            if cmd.get("status") != "executing":
                                return   # our command finished
                            break   # still executing — keep polling
                    else:
                        return   # cmd_id no longer in results — completed
                else:
                    # Fallback: check whether any command is still executing
                    if commands[0].get("status", "done") != "executing":
                        return
            except CannotConnect:
                return
            await asyncio.sleep(CMD_POLL_INTERVAL_S)
        LOGGER.warning(
            "RobEye: cmd_id=%s did not complete within %ds — moving to next",
            cmd_id, CMD_POLL_TIMEOUT_S,
        )

    async def _wait_for_active_operation_end(self) -> None:
        """Wait until robot leaves active modes before dispatching next queued command.

        /get/command_result confirms command acceptance/completion, but the robot can
        continue physically cleaning for much longer. For cleaning/go-home operations,
        keep the queue blocked until /get/status mode is no longer cleaning/go_home.

        This poll intentionally has no hard timeout because cleaning duration depends
        on home size. Connectivity errors are treated as transient; the queue retries.
        """
        while True:
            try:
                status = await self.client.get_status()
                mode = status.get("mode", "")
                if mode not in (MODE_CLEANING, MODE_GO_HOME):
                    return
                if self._has_immediate_command_pending():
                    LOGGER.debug(
                        "RobEye: immediate command pending; interrupting active-operation wait"
                    )
                    return
            except CannotConnect:
                LOGGER.debug(
                    "RobEye: get_status failed while waiting for active operation end; retrying"
                )
            await asyncio.sleep(CMD_POLL_INTERVAL_S)

    async def async_send_command(self, coro_func, *args: Any, **kwargs: Any) -> None:
        """Enqueue a command for serial dispatch by _command_queue_worker.

        Commands execute in the order they are enqueued — one at a time.
        The worker polls /get/command_result between each command so the
        robot fully finishes before the next command is sent.

        /set/stop is dispatched as-is (hard stop in place).
        /set/go_home drains pending queued work and runs next.
        /set/clean_start_or_continue is also prioritised to run next.
        """
        is_stop = (coro_func is self.client.stop or getattr(coro_func, "__name__", "") == "stop")

        is_go_home = (
            coro_func is self.client.go_home
            or getattr(coro_func, "__name__", "") == "go_home"
        )
        is_immediate = self._is_immediate_command(coro_func)

        if is_stop or is_go_home:
            # Drain queued commands — emergency controls cancel pending work
            while not self._command_queue.empty():
                try:
                    self._command_queue.get_nowait()
                    self._command_queue.task_done()
                except asyncio.QueueEmpty:
                    break

        self._command_sequence += 1
        await self._command_queue.put(
            (0 if is_immediate else 1, self._command_sequence, coro_func, args, kwargs)
        )
        if hasattr(self, "async_update_listeners"):
            self.async_update_listeners()

    async def async_remove_queued_command(self, pending_index: int = 0) -> bool:
        """Remove one pending queue entry by index.

        Indexing is zero-based and applies to pending commands only (never the
        currently active command). Returns True when an entry is removed.
        """
        try:
            pending_items = sorted(list(self._command_queue._queue))
        except AttributeError:
            return False

        if pending_index < 0 or pending_index >= len(pending_items):
            return False

        target = pending_items[pending_index]
        removed = False
        rebuilt_items = []
        for item in pending_items:
            if not removed and item == target:
                removed = True
                continue
            rebuilt_items.append(item)

        if not removed:
            return False

        while not self._command_queue.empty():
            try:
                self._command_queue.get_nowait()
                self._command_queue.task_done()
            except asyncio.QueueEmpty:
                break

        for item in rebuilt_items:
            await self._command_queue.put(item)

        if hasattr(self, "async_update_listeners"):
            self.async_update_listeners()
        return True

    # ── Queue status ──────────────────────────────────────────────────

    @property
    def command_queue_items(self) -> list[dict[str, Any]]:
        """Return a snapshot of pending queue items for dashboard display.

        Each item: {"status": "active"|"pending", "label": str, "map_name": str}
        First item is the one currently being dispatched by the worker.
        Remaining items are waiting.

        Used by the queue_status sensor for dashboard rendering.
        """
        try:
            # PriorityQueue stores a heap; sort to present true dispatch order.
            pending_items = sorted(list(self._command_queue._queue))
        except AttributeError:
            return []

        active_item: dict[str, str] | None = None
        if self._active_command is not None:
            _priority, _seq, coro_func, args, kwargs = self._active_command
            active_item = {
                "status": "active",
                "label": self._describe_command_for_display(coro_func, kwargs),
                "map_name": self._resolve_map_name(str(kwargs.get("map_id", ""))),
            }
        elif self._inflight_clean_command is not None:
            coro_func, kwargs = self._inflight_clean_command
            active_item = {
                "status": "active",
                "label": self._describe_command_for_display(coro_func, kwargs),
                "map_name": self._resolve_map_name(str(kwargs.get("map_id", ""))),
            }
        else:
            active_item = self._parsed_current_session_item()

        result = []
        if active_item is not None:
            result.append(active_item)

        for idx, (_priority, _seq, coro_func, args, kwargs) in enumerate(pending_items):
            result.append({
                "status": "pending" if active_item is not None or idx > 0 else "active",
                "label": self._describe_command_for_display(coro_func, kwargs),
                "map_name": self._resolve_map_name(str(kwargs.get("map_id", ""))),
            })
        return result

    @property
    def queue_eta_seconds(self) -> int | None:
        """Estimated seconds to complete all queued cleaning commands.

        Sums estimated_cleaning_time from /get/areas for each queued room.
        Returns None if no area data or no clean commands queued.

        estimated_cleaning_time in /get/areas is in milliseconds.
        """
        try:
            pending_items = sorted(list(self._command_queue._queue))
        except AttributeError:
            return None

        items = []
        if self._active_command is not None:
            items.append(self._active_command)
        items.extend(pending_items)

        total_ms = 0
        found = False
        for _priority, _seq, coro_func, args, kwargs in items:
            name = getattr(coro_func, "__name__", "")
            area_ids_str = str(kwargs.get("area_ids", ""))
            if name not in ("clean_map", "clean_all"):
                continue
            found = True
            if not area_ids_str:
                continue
            for area_id_s in area_ids_str.split(","):
                try:
                    area_id = int(area_id_s.strip())
                except ValueError:
                    continue
                for area in self.areas:
                    if area.get("id") == area_id:
                        stats = area.get("statistics", {})
                        # Use average if available, else estimated
                        t = stats.get("average_cleaning_time") or \
                            stats.get("estimated_cleaning_time", 0)
                        total_ms += t
                        break

        return int(total_ms / 1000) if found else None

    def _resolve_map_name(self, map_id: str) -> str:
        """Return display name for a map_id from available_maps."""
        for m in self.available_maps:
            if m["map_id"] == map_id:
                return m["display_name"]
        return f"Map {map_id}" if map_id else ""

    def _resolve_room_name_by_id(self, area_id: int) -> str | None:
        """Return room name for an area_id from current areas data."""
        import json as _json
        areas_sources = [self.areas]
        saved_blob = (self.data or {}).get(DATA_AREAS_SAVED_MAP, {})
        if isinstance(saved_blob, dict):
            areas_sources.append(saved_blob.get("areas", []))

        for areas in areas_sources:
            for area in areas:
                raw_id = area.get("id")
                try:
                    current_area_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if current_area_id == area_id:
                    meta_raw = area.get("area_meta_data", "")
                    if meta_raw:
                        try:
                            meta = _json.loads(meta_raw)
                            return meta.get("name", "").strip() or None
                        except Exception:  # noqa: BLE001
                            pass
        return None

    def _describe_command_for_display(self, coro_func: Any, kwargs: dict[str, Any]) -> str:
        """Return a queue label with resolved room names whenever possible."""
        name = _command_name(coro_func)
        area_ids_raw = kwargs.get("area_ids", "")
        if name == "clean_map" and area_ids_raw:
            names: list[str] = []
            for raw in str(area_ids_raw).split(","):
                try:
                    area_id = int(raw.strip())
                except ValueError:
                    continue
                room_name = self._resolve_room_name_by_id(area_id)
                names.append(room_name or f"room {area_id}")
            if names:
                return "Cleaning " + " + ".join(names)
        return _describe_command(coro_func, kwargs)

    def _parsed_current_session_item(self) -> dict[str, str] | None:
        """Return an active queue-like item for a robot-run session.

        Used when robot is already cleaning (e.g. started from native app) and
        HA queue has no active command. This keeps dashboard status coherent and
        leaves newly queued HA actions as pending.
        """
        mode = str(self.status.get("mode", ""))
        if mode not in (MODE_CLEANING, MODE_GO_HOME):
            return None

        if mode == MODE_GO_HOME:
            return {
                "status": "active",
                "label": "Return to base",
                "map_name": self._resolve_map_name(self.active_map_id),
            }

        area_ids_raw = self.status.get("area_ids")
        if isinstance(area_ids_raw, list) and area_ids_raw:
            resolved = []
            for area_id in area_ids_raw:
                try:
                    name = self._resolve_room_name_by_id(int(area_id))
                except (ValueError, TypeError):
                    continue
                if name:
                    resolved.append(name)
            if resolved:
                label = "Cleaning " + " + ".join(resolved)
            else:
                label = "Current cleaning session"
        else:
            label = "Current cleaning session"

        map_id = str(
            self.status.get("map_id")
            or self.status.get("operation_map_id")
            or self.active_map_id
        )
        return {
            "status": "active",
            "label": label,
            "map_name": self._resolve_map_name(map_id),
        }

    def _process_new_events(self, events: list[dict[str, Any]]) -> None:
        """Process new event log entries.

        - Fires persistent notifications for hardware alerts.
        - Keeps last 50 top-level events in self._recent_events for the sensor.
        - Logs top-level events (hierarchy=1) to HA logbook.
        """
        from homeassistant.components import persistent_notification

        for event in events:
            type_id = event.get("type_id")
            hierarchy = event.get("hierarchy", 0)

            # Hardware alerts → persistent notification
            if type_id == EVENT_DUSTBIN_MISSING:
                persistent_notification.async_create(
                    self.hass,
                    "The dustbin is missing. Please reinsert it before cleaning.",
                    title="Rowenta — Dustbin Missing",
                    notification_id="rowenta_dustbin_missing",
                )
            elif type_id == EVENT_DUSTBIN_INSERTED:
                persistent_notification.async_dismiss(
                    self.hass, "rowenta_dustbin_missing"
                )
            elif type_id == EVENT_ROBOT_LIFTED:
                LOGGER.warning("RobEye: robot was lifted during operation")

            # Logbook entry for top-level user actions
            if hierarchy == 1 and event.get("source_type") == "user":
                label = EVENT_TYPE_LABELS.get(type_id, event.get("type", ""))
                area_id = event.get("area_id", 0)
                map_id  = str(event.get("map_id", ""))
                detail  = ""
                if area_id:
                    detail = f" — room {area_id}"
                    room_name = self._resolve_room_name_by_id(area_id)
                    if room_name:
                        detail = f" — {room_name}"
                if map_id:
                    map_name = self._resolve_map_name(map_id)
                    if map_name:
                        detail += f" ({map_name})"
                LOGGER.info("RobEye event: %s%s", label, detail)

        # Keep last 50 top-level events for the sensor attribute
        top_level = [
            e for e in events if e.get("hierarchy", 0) == 1
        ]
        self._recent_events = (self._recent_events + top_level)[-50:]


# ── Command helpers ───────────────────────────────────────────────────

def _describe_command(coro_func: Any, kwargs: dict) -> str:
    """Return a human-readable label for a queued command coroutine."""
    name = _command_name(coro_func)
    area_ids = kwargs.get("area_ids", "")
    if name == "clean_map" and area_ids:
        return f"Cleaning room {area_ids}"
    if name == "clean_all":
        return "Cleaning entire home"
    if name == "go_home":
        return "Return to base"
    if name == "stop":
        return "Stop"
    if name == "clean_start_or_continue":
        return "Resume"
    return name.replace("_", " ").capitalize()


def _command_name(coro_func: Any) -> str:
    """Extract a stable coroutine name from bound methods and AsyncMocks."""
    name = getattr(coro_func, "__name__", "") or getattr(
        getattr(coro_func, "__func__", None), "__name__", ""
    )
    if str(name).lower() == "asyncmock":
        mock_name = getattr(coro_func, "_mock_name", "")
        if mock_name:
            return str(mock_name)
    return str(name) or "command"


# ── Map helpers ───────────────────────────────────────────────────────

def _parse_map_entry(
    raw: dict,
    position: int,
    active_map_id: str = "",
) -> dict[str, Any] | None:
    """Normalise one entry from /get/maps into a standard map dict.

    Confirmed field names (2026-03-29):
      map_id          int    — numeric map identifier
      map_meta_data   str    — user name; strip(); may be ""
      permanent_flag  str    — "true" string for saved floor maps
      statistics      dict   — per-map stats

    Display name logic (matches native app):
      non-empty map_meta_data.strip() → use it
      empty                           → f"Map {position}"  (1-based)

    Returns None for non-permanent maps (permanent_flag != "true").
    """
    if not isinstance(raw, dict):
        return None

    map_id = str(raw.get("map_id", "")).strip()
    if not map_id:
        return None

    # permanent_flag is a STRING "true", not a boolean
    if str(raw.get("permanent_flag", "")).strip().lower() != "true":
        return None

    meta = str(raw.get("map_meta_data", "")).strip()
    display_name = meta if meta else f"Map {position}"
    is_active = (map_id == str(active_map_id)) if active_map_id else False

    stats_raw = raw.get("statistics", {}) or {}
    lc = stats_raw.get("last_cleaned", {}) or {}
    never = lc.get("year", 2001) <= 2001
    last_cleaned_str = (
        None if never
        else f"{lc['year']}-{lc['month']:02d}-{lc['day']:02d}"
    )

    return {
        "map_id":       map_id,
        "display_name": display_name,
        "user_name":    meta,           # raw user name; "" if not set
        "is_active":    is_active,
        "statistics": {
            "area_size":               stats_raw.get("area_size", 0),
            "cleaning_counter":        stats_raw.get("cleaning_counter", 0),
            "estimated_cleaning_time": stats_raw.get("estimated_cleaning_time", 0),
            "average_cleaning_time":   stats_raw.get("average_cleaning_time", 0),
            "last_cleaned":            last_cleaned_str,
        },
    }


# ── Sensor-values helpers ─────────────────────────────────────────────

def _parse_sensor_values(raw: dict) -> dict:
    """Flatten sensor_values into a simple dtype__descriptor → value dict.

    GPIO entries carry value='active'|'inactive'.
    current_sensor entries carry current (mA integer).
    voltage_sensor entries carry voltage (mV integer).
    """
    result: dict = {}
    for device in raw.get("sensor_data", []):
        dtype = device.get("device_type", "")
        for entry in device.get("sensor_data", []):
            desc = entry.get("device_descriptor", "")
            data = entry.get("payload", {}).get("data", {})
            key = f"{dtype}__{desc}"
            if "value" in data:
                result[key] = data["value"]
            elif "current" in data:
                result[key] = data["current"]
            elif "voltage" in data:
                result[key] = data["voltage"]
    return result


def _gpio(parsed: dict, descriptor: str) -> str:
    """Return the GPIO value ('active'|'inactive') for a descriptor."""
    return parsed.get(f"gpio__{descriptor}", "inactive")


def _current_ma(parsed: dict, descriptor: str) -> int | None:
    """Return the current-sensor reading in mA, or None if unavailable."""
    return parsed.get(f"current_sensor__{descriptor}")


# ── Live-map helpers ──────────────────────────────────────────────────

_ROOM_COLORS = [
    "#4A90D9", "#E67E22", "#2ECC71", "#9B59B6", "#E74C3C",
    "#1ABC9C", "#F39C12", "#3498DB", "#D35400", "#27AE60",
    "#8E44AD", "#C0392B", "#16A085", "#F1C40F", "#2980B9",
    "#E91E63", "#00BCD4",
]

_HEADING_SCALE = 65536 / 360  # raw heading units per degree


# ── Position extraction helpers ───────────────────────────────────────

def _extract_relocalization_position(reloc: dict) -> dict | None:
    """Extract robot position from /debug/relocalization response.

    Used during cleaning of the saved map (map_id=3).
    Returns multiple 'continuous' entries — use the LAST one (highest rtc_time).
    rob_pose = [x_units, y_units, heading_raw]; 1 unit = 2 mm.
    """
    entries = reloc.get("localization_algo_input", [])
    entry = next(
        (e for e in reversed(entries)
         if e.get("localization_type") == "continuous"),
        None,
    )
    if not entry or "rob_pose" not in entry:
        return None
    rob_pose = entry["rob_pose"]
    if not isinstance(rob_pose, list) or len(rob_pose) < 2:
        return None
    x, y = rob_pose[0], rob_pose[1]
    h = rob_pose[2] if len(rob_pose) > 2 else 0
    return {
        "x":           x,
        "y":           y,
        "heading_deg": round(h / _HEADING_SCALE, 1),
        "source":      "relocalization",
        "is_live":     True,
    }


def _extract_localization_position(loc: dict) -> dict | None:
    """Extract robot position from /debug/localization response.

    Used when robot is idle (mode='ready'/'charging').
    Prefers 'global' entry over 'startpoint'.
    Data may be hours old — shown as dimmed 'last known' position.
    """
    entries = loc.get("localization_algo_input", [])
    entry = next(
        (e for e in entries if e.get("localization_type") == "global"),
        None,
    ) or next(
        (e for e in entries if e.get("localization_type") == "startpoint"),
        None,
    )
    if not entry or "rob_pose" not in entry:
        return None
    rob_pose = entry["rob_pose"]
    if not isinstance(rob_pose, list) or len(rob_pose) < 2:
        return None
    x, y = rob_pose[0], rob_pose[1]
    h = rob_pose[2] if len(rob_pose) > 2 else 0
    return {
        "x":           x,
        "y":           y,
        "heading_deg": round(h / _HEADING_SCALE, 1),
        "source":      "localization",
        "is_live":     False,  # stale — shown dimmed in card
    }


def _extract_exploration_position(exploration: dict) -> dict | None:
    """Extract robot position from /debug/exploration response.

    Used during new-map exploration sessions (operation_map_id != saved map_id).
    Uses entry with highest 'ts' (most recent navigation decision).
    Coordinates are in the LIVE MAP's own coordinate system (not map 3).
    """
    points = exploration.get("exploration_points", [])
    if not points:
        return None
    last = max(points, key=lambda p: p.get("ts", 0))
    rob_pose = last.get("rob_pose")
    if not rob_pose or len(rob_pose) < 2:
        return None
    x, y = rob_pose[0], rob_pose[1]
    h = rob_pose[2] if len(rob_pose) > 2 else 0
    return {
        "x":           x,
        "y":           y,
        "heading_deg": round(h / _HEADING_SCALE, 1),
        "source":      "exploration",
        "is_live":     True,
        "ts":          last.get("ts"),
        "event_type":  last.get("type", ""),
    }


def _extract_rob_pose(rob_pose: dict) -> dict | None:
    """Extract robot position from /get/rob_pose response.

    Returns None if valid=False (robot has no position fix).
    heading is already in degrees — no scale conversion required.

    Confirmed fields (2026-03-21 live at dock):
        x1=-2, y1=-3, heading=157, valid=true, is_tentative=false

    Coordinate system: API units. 1 unit = 2 mm = 0.2 cm.
    Dock is at approximately (90, 13) API units.
    """
    if not rob_pose.get("valid", False):
        return None

    return {
        "x":           rob_pose["x1"],
        "y":           rob_pose["y1"],
        "heading_deg": rob_pose["heading"],       # already degrees, no conversion
        "is_tentative": rob_pose.get("is_tentative", False),
        "timestamp":   rob_pose.get("timestamp"), # monotonic counter for staleness
        "map_id":      rob_pose.get("map_id"),
        "source":      "rob_pose",
        "is_live":     True,
    }


def _is_real_room(area: dict) -> bool:
    """Return True if this area is a real named room, not a redundant auto-segment.

    All three conditions must hold:
      1. area_state == "clean"       — user has confirmed/named this area
      2. room_type  != "none"        — robot classified it as a real room type
      3. area_meta_data is non-empty — user has given it a name
    Redundant areas (auto-segmented sub-partitions) always have area_state
    "inactive", room_type "none", and empty area_meta_data.
    """
    return (
        area.get("area_state", "inactive") == "clean"
        and area.get("room_type", "none") != "none"
        and bool(area.get("area_meta_data", ""))
    )


def _calc_area_m2(points: list[dict]) -> float:
    """Shoelace formula for polygon area in m².

    Scale: 1 API unit = 2 mm = 0.2 cm.
    area_m2 = area_units² × (0.2 cm/unit)² / 10 000 cm²/m²
            = area_units² × 4e-6
    NOTE: The API's area_size field is always 2× the real value — ignore it.
    """
    pts = [(p["x"], p["y"]) for p in points]
    n = len(pts)
    if n < 3:
        return 0.0
    a = abs(
        sum(
            pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
            for i in range(n)
        )
    ) / 2
    return round(a * 4e-6, 1)  # units² → m²  (1 unit = 2 mm = 0.2 cm)


def _parse_live_outline(seen_polygon_raw: dict) -> list:
    """Extract the outer boundary polygon from a /get/seen_polygon response."""
    polygons = (
        seen_polygon_raw.get("seen_polygon", {}).get("polygons")
        or seen_polygon_raw.get("polygons")
        or []
    )
    for poly in polygons:
        segs = poly.get("segments", [])
        if segs:
            pts = [[s["x1"], s["y1"]] for s in segs]
            pts.append([segs[-1]["x2"], segs[-1]["y2"]])
            return pts
    return []


def _classify_areas(areas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split areas into (rooms, avoidance_zones).

    Avoidance zones: area_state == "blocking" or area_type == "to_be_cleaned".
    Everything else is treated as a room (including redundant inactive segments).
    """
    rooms: list[dict] = []
    avoidance: list[dict] = []
    for area in areas:
        if (
            area.get("area_state") == AREA_STATE_BLOCKING
            or area.get("area_type") == AREA_TYPE_AVOIDANCE
        ):
            avoidance.append(area)
        else:
            rooms.append(area)
    return rooms, avoidance


def _build_live_map_payload(
    existing: dict[str, Any],
    robot_position: dict | None,
    seen_polygon_raw: dict[str, Any],
    cleaning_grid: dict[str, Any],
    feature_map: dict[str, Any],
    tile_map: dict[str, Any],
    areas_data: dict[str, Any],
    seen_poly_saved_map: dict[str, Any],
    is_active: bool,
    map_id: str,
    robot_path: list,
    last_session_grid: dict,
    last_session_path: list,
    last_session_outline: list,
    last_session_map_id: str,
    session_complete: bool,
) -> dict[str, Any]:
    """Compose the live_map attribute dict for the SVG card sensor.

    robot_position comes from /get/rob_pose — valid in all states.
    is_tentative flag on robot_position drives dimmed rendering in the card.

    Schema (used by rowenta-map-card.js):
      map_id, is_active, rooms, outline, walls, dock, robot,
      live_outline, bounds, scale
    """
    # ── Classify areas: rooms vs avoidance zones ──────────────────────
    room_areas, avoidance_areas = _classify_areas(areas_data.get("areas", []))

    # ── Rooms (from /get/areas?map_id) ───────────────────────────────
    rooms: list[dict[str, Any]] = []
    for idx, area in enumerate(room_areas):
        try:
            meta = json.loads(area.get("area_meta_data", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        name = meta.get("name") or f"Room {area.get('id', idx)}"
        raw_pts = area.get("points", [])
        pts = [[p["x"], p["y"]] for p in raw_pts]
        redundant = not _is_real_room(area)
        rooms.append({
            "id": area.get("id"),
            "name": name,
            "room_type": area.get("room_type", "none"),
            "area_state": area.get("area_state", "inactive"),
            "polygon": pts,
            "color": _ROOM_COLORS[idx % len(_ROOM_COLORS)],
            "redundant": redundant,
            "area_m2": _calc_area_m2(raw_pts),
        })

    # ── Avoidance zones (area_state="blocking" / area_type="to_be_cleaned") ──
    avoidance_zones: list[dict[str, Any]] = []
    for area in avoidance_areas:
        raw_pts = area.get("points", [])
        pts = [[p["x"], p["y"]] for p in raw_pts]
        avoidance_zones.append({
            "id": area.get("id"),
            "polygon": pts,
        })

    # ── Outline (saved-map boundary from /get/seen_polygon?map_id) ───
    outline: list[list[int]] = []
    sp_polygons = (
        seen_poly_saved_map.get("seen_polygon", {}).get("polygons")
        or seen_poly_saved_map.get("polygons")
        or []
    )
    for poly in sp_polygons:
        segs = poly.get("segments", [])
        if segs:
            outline = [[s["x1"], s["y1"]] for s in segs]
            outline.append([segs[-1]["x2"], segs[-1]["y2"]])
            break

    # Fallback: tile_map outline polygon
    if not outline:
        outline = [[p["x"], p["y"]] for p in tile_map.get("outline", [])]

    # ── Walls (from /get/feature_map?map_id) ─────────────────────────
    walls = [
        [ln["x1"], ln["y1"], ln["x2"], ln["y2"]]
        for ln in feature_map.get("map", {}).get("lines", [])
    ]

    # ── Dock ─────────────────────────────────────────────────────────
    dock_raw = (
        feature_map.get("map", {}).get("docking_pose")
        or tile_map.get("map", {}).get("docking_pose")
        or {}
    )
    dock: dict[str, Any] | None = None
    if dock_raw and str(dock_raw.get("valid", "")).lower() in ("true", "1", True):
        dock = {
            "x": dock_raw["x"],
            "y": dock_raw["y"],
            "heading_deg": round(dock_raw.get("heading", 0) / _HEADING_SCALE, 1),
        }

    # ── Robot live position ───────────────────────────────────────────
    # From /get/rob_pose — valid in all states (docked, idle, cleaning, returning).
    # is_tentative=True means rough initial estimate → show dimmed in card.
    # When valid=False, _extract_rob_pose returns None; keep last known position.
    if robot_position:
        robot: dict[str, Any] | None = {
            "x":           robot_position["x"],
            "y":           robot_position["y"],
            "heading_deg": robot_position["heading_deg"],
            "is_tentative": robot_position.get("is_tentative", False),
            "source":      robot_position.get("source", "rob_pose"),
            "is_live":     robot_position.get("is_live", True),
        }
    elif not is_active:
        # No valid rob_pose — keep last known position from previous tick
        robot = existing.get("robot")
    else:
        robot = None

    # ── Live / session outline ────────────────────────────────────────
    session_matches_map = (
        session_complete
        and last_session_map_id != ""
        and str(last_session_map_id) == str(map_id)
    )
    if is_active:
        live_outline: list[list[int]] = _parse_live_outline(seen_polygon_raw)
    elif session_matches_map:
        live_outline = list(last_session_outline)
    else:
        live_outline = []

    # ── Select display grid and path (live during cleaning, frozen after) ──
    if is_active:
        display_grid = cleaning_grid if isinstance(cleaning_grid, dict) and cleaning_grid.get("size_x", 0) > 0 else {}
        display_path = list(robot_path)
    elif session_matches_map:
        display_grid = last_session_grid
        display_path = last_session_path
    else:
        display_grid = {}
        display_path = []

    # ── Bounding box ─────────────────────────────────────────────────
    all_pts: list[list[int]] = (
        [pt for r in rooms for pt in r["polygon"]]
        + [pt for z in avoidance_zones for pt in z["polygon"]]
        + outline
        + live_outline
        + ([[dock["x"], dock["y"]]] if dock else [])
    )
    # Include grid extents in bounds
    g = display_grid
    if g.get("size_x", 0) > 0:
        gx0 = g["lower_left_x"]
        gy0 = g["lower_left_y"]
        gx1 = gx0 + g["size_x"] * g["resolution"]
        gy1 = gy0 + g["size_y"] * g["resolution"]
        all_pts += [[gx0, gy0], [gx1, gy1]]

    if all_pts:
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        bounds: dict[str, int] = {
            "min_x": min(xs), "max_x": max(xs),
            "min_y": min(ys), "max_y": max(ys),
        }
    else:
        bounds = existing.get("bounds", {"min_x": -800, "max_x": 2400, "min_y": -1300, "max_y": 1400})

    return {
        "map_id": map_id,
        "is_active": is_active,
        "rooms": rooms,
        "avoidance_zones": avoidance_zones,
        "outline": outline,
        "walls": walls,
        "dock": dock,
        "robot": robot,
        "live_outline": live_outline,
        "bounds": bounds,
        "scale": "mm",  # 1 API unit = 2 mm
        "cleaning_grid": display_grid,
        "robot_path": display_path,
        "session_complete": session_complete,
    }
