"""DataUpdateCoordinator for the Rowenta Xplorer 120 integration.

Key improvements over v1:
- Tracks known_area_ids and fires SIGNAL_AREAS_UPDATED when the room set
  changes so platforms can add new entities WITHOUT a full reload.
- Adaptive live-map polling: every coordinator tick during cleaning, 60 s when idle.
- Exposes structured live_map data (floor_plan, cleaned_area, robot_position)
  for the Phase-2 SVG card via sensor.xplorer120_live_map.
"""

from __future__ import annotations

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
    DATA_AREAS,
    DATA_SENSOR_VALUES,
    DATA_AREAS_SAVED_MAP,
    DATA_CLEANING_GRID,
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
    LOGGER,
    MAX_POLL_FAILURES,
    MODE_CLEANING,
    MODE_GO_HOME,
    SCAN_INTERVAL_AREAS,
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

        # Cleaning strategy — set by strategy select or deep-clean switch; read by all clean operations
        self.cleaning_strategy: str = STRATEGY_DEFAULT

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
        self._last_mode: str = ""
        self._session_complete: bool = False
        self._last_rob_pose_ts: int = 0   # last seen /get/rob_pose timestamp value

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
        """Runtime map ID: manual override > /get/map_status > setup-time map_id."""
        if self._manual_map_id is not None:
            return self._manual_map_id
        map_status = (self.data or {}).get(DATA_MAP_STATUS, {})
        active = str(map_status.get("active_map_id", "")).strip()
        return active if active else self.map_id

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

    async def async_set_active_map(self, map_id: str) -> None:
        """Override the active map and force-reload all map-dependent data."""
        self._manual_map_id = map_id
        self._last_active_map_id = map_id
        self._last_areas = None
        self._last_map_geometry = None
        self._known_area_ids = set()
        self._robot_path = []
        self._last_session_grid = {}
        self._last_session_path = []
        self._last_session_outline = []
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
                self._session_complete = True
                LOGGER.info(
                    "Cleaning session complete — frozen grid=%s cells, path=%d points",
                    self._last_session_grid.get("size_x", 0),
                    len(self._last_session_path),
                )

            self._last_mode = mode

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
                    data[DATA_FEATURE_MAP] = await self.client.get_feature_map(self.active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_feature_map unavailable, skipping")
                try:
                    data[DATA_TILE_MAP] = await self.client.get_tile_map(self.active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_tile_map unavailable, skipping")
                try:
                    data[DATA_AREAS_SAVED_MAP] = await self.client.get_areas(self.active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_areas (map geometry) unavailable, skipping")
                try:
                    data[DATA_SEEN_POLY_SAVED_MAP] = await self.client.get_seen_polygon(self.active_map_id)
                except CannotConnect:
                    LOGGER.debug("get_seen_polygon (map geometry) unavailable, skipping")

                # Load last-session grid from saved map (also runs on startup)
                if not is_active:
                    try:
                        saved_grid = await self.client.get_cleaning_grid_map(
                            map_id=self.active_map_id
                        )
                        if saved_grid.get("size_x", 0) > 0:
                            self._last_session_grid = saved_grid
                            if not self._session_complete:
                                self._session_complete = True
                                LOGGER.info(
                                    "Loaded last session grid from saved map: %d×%d",
                                    saved_grid["size_x"],
                                    saved_grid["size_y"],
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
                        map_id=self.active_map_id,
                        robot_path=self._robot_path,
                        last_session_grid=self._last_session_grid,
                        last_session_path=self._last_session_path,
                        last_session_outline=self._last_session_outline,
                        session_complete=self._session_complete,
                    )

                    self._last_live_map = now

            # ── Every 300 s: areas + sensor status ───────────────────
            if self._last_areas is None or (
                now - self._last_areas
            ) >= timedelta(seconds=SCAN_INTERVAL_AREAS):
                new_areas_blob = await self.client.get_areas(self.active_map_id)
                data[DATA_AREAS] = new_areas_blob

                try:
                    data[DATA_SENSOR_STATUS] = await self.client.get_sensor_status()
                except CannotConnect:
                    LOGGER.debug("get_sensor_status unavailable, skipping")
                try:
                    data[DATA_ROBOT_FLAGS] = await self.client.get_robot_flags()
                except CannotConnect:
                    LOGGER.debug("get_robot_flags unavailable, skipping")

                # Fetch available maps list
                try:
                    data[DATA_MAPS] = await self.client.get_maps()
                except CannotConnect:
                    LOGGER.debug("get_maps unavailable, skipping")

                # Fetch active map status — drives active_map_id property
                try:
                    data[DATA_MAP_STATUS] = await self.client.get_map_status()
                except CannotConnect:
                    LOGGER.debug("get_map_status unavailable, skipping")

                # Detect active map change (floor switch) — reset area/session state
                new_active_map = (
                    str((data.get(DATA_MAP_STATUS) or {}).get("active_map_id", "")).strip()
                    or self.map_id
                )
                if new_active_map != self._last_active_map_id:
                    prev_map_id = self._last_active_map_id
                    self._last_active_map_id = new_active_map
                    if self._manual_map_id is not None:
                        # User has an active manual map selection — respect it and
                        # do not auto-follow the robot's reported floor.  Only update
                        # the tracking variable so the next robot-initiated change is
                        # still detected correctly.
                        LOGGER.debug(
                            "Robot reports map %s but user override %s is active — "
                            "keeping manual selection",
                            new_active_map,
                            self._manual_map_id,
                        )
                    else:
                        LOGGER.info(
                            "Active map changed: %s -> %s — reloading areas",
                            prev_map_id,
                            new_active_map,
                        )
                        self._last_areas = None
                        self._last_map_geometry = None
                        self._known_area_ids = set()
                        self._robot_path = []
                        self._last_session_grid = {}
                        self._last_session_path = []
                        self._last_session_outline = []
                        self._session_complete = False
                        dashboard_manager = self.hass.data.get(DOMAIN, {}).get(
                            f"{self.config_entry.entry_id}_dashboard"
                        )
                        if dashboard_manager:
                            dashboard_manager.invalidate()

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

    # ── Command helper ────────────────────────────────────────────────

    async def async_send_command(self, coro_func, *args: Any, **kwargs: Any) -> None:
        await coro_func(*args, **kwargs)
        await self.async_request_refresh()


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
    if is_active:
        live_outline: list[list[int]] = _parse_live_outline(seen_polygon_raw)
    elif session_complete:
        live_outline = list(last_session_outline)
    else:
        live_outline = []

    # ── Select display grid and path (live during cleaning, frozen after) ──
    if is_active:
        display_grid = cleaning_grid if isinstance(cleaning_grid, dict) and cleaning_grid.get("size_x", 0) > 0 else {}
        display_path = list(robot_path)
    else:
        display_grid = last_session_grid
        display_path = last_session_path

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
