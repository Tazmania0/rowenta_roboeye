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
import time as _time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, RobEyeApiClient
from .const import (
    AREA_STATE_BLOCKING,
    AREA_TYPE_AVOIDANCE,
    DATA_AREAS,
    DATA_SENSOR_VALUES,
    DATA_SENSOR_VALUES_PARSED,
    DATA_AREAS_SAVED_MAP,
    DATA_CLEANING_GRID,
    DATA_EXPLORATION,
    DATA_FEATURE_MAP,
    DATA_LIVE_MAP,
    DATA_LIVE_PARAMETERS,
    DATA_MAP_STATUS,
    DATA_PERMANENT_STATISTICS,
    DATA_RELOCALIZATION,
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
    SCAN_INTERVAL_GYRO,
    SCAN_INTERVAL_IDLE_LOC,
    SCAN_INTERVAL_ROBOT_INFO,
    SCAN_INTERVAL_SENSOR_HW,
    SCAN_INTERVAL_SLAM,
    SCAN_INTERVAL_STATUS,
    SCAN_INTERVAL_STATISTICS,
    SIGNAL_AREAS_UPDATED,
    UPDATE_INTERVAL_CLEANING,
    UPDATE_INTERVAL_IDLE,
)


MAX_PATH_POINTS = 2000
MIN_MOVE_UNITS  = 5   # ~1 cm threshold before appending a new path point
# Keep old underscore-prefixed names for any external references
_MAX_PATH_POINTS = MAX_PATH_POINTS
_MIN_MOVE_UNITS  = MIN_MOVE_UNITS


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
            update_interval=UPDATE_INTERVAL_IDLE,
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

        # ── Timing trackers ──────────────────────────────────────────────
        self._last_slam:          datetime | None = None
        self._last_sensor_hw:     datetime | None = None
        self._last_brush_check:   datetime | None = None
        self._last_status_check:  datetime | None = None
        self._last_idle_loc:      datetime | None = None

        # ── Dead-reckoning state ─────────────────────────────────────────
        # Level 1: SLAM anchor — last absolute fix from exploration/relocalization
        self._slam_anchor:        dict | None = None
        self._slam_anchor_ts:     float = 0.0

        # Level 2: Odometry accumulator — sum of rel_movements since last anchor
        self._odom_dx:            float = 0.0
        self._odom_dy:            float = 0.0

        # Track known area IDs so we can detect additions/removals without reload
        self._known_area_ids: set = set()

        # Deep clean mode — toggled by switch entity, read by all clean operations
        self.deep_clean_enabled: bool = False

        # Brush stuck notification state tracking
        self._brush_left_notified: bool = False
        self._brush_right_notified: bool = False

        # ── Session lifecycle ────────────────────────────────────────────
        self._last_mode:          str = ""
        self._operation_map_id:   str = map_id
        self._robot_path:         list[tuple[float, float]] = []
        self._last_session_grid:  dict = {}
        self._last_session_path:  list = []
        self._last_session_outline: list = []
        self._session_complete:   bool = False

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

    # ── Core update ───────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.utcnow()
        data: dict[str, Any] = dict(self.data or {})

        try:
            # ── Always: status (every coordinator tick) ───────────────────
            data[DATA_STATUS] = await self.client.get_status()
            mode  = data[DATA_STATUS].get("mode", "")
            is_active = mode in (MODE_CLEANING, MODE_GO_HOME)

            # ── Dynamically adjust coordinator tick rate ──────────────────
            target_interval = (
                UPDATE_INTERVAL_CLEANING if is_active else UPDATE_INTERVAL_IDLE
            )
            if self.update_interval != target_interval:
                self.update_interval = target_interval
                LOGGER.debug("Coordinator interval → %ds", target_interval.total_seconds())

            # ── Session lifecycle ─────────────────────────────────────────
            if mode == MODE_CLEANING and self._last_mode != MODE_CLEANING:
                # Cleaning just started — reset all session state
                self._slam_anchor        = None
                self._slam_anchor_ts     = 0.0
                self._odom_dx            = 0.0
                self._odom_dy            = 0.0
                self._robot_path         = []
                self._last_session_grid  = {}
                self._last_session_path  = []
                self._last_session_outline = []
                self._session_complete   = False
                self._last_slam          = None
                LOGGER.debug("Cleaning session started — state reset")

            was_cleaning = self._last_mode == MODE_CLEANING
            now_idle     = not is_active
            if was_cleaning and now_idle and not self._session_complete:
                self._last_session_grid    = dict(data.get(DATA_CLEANING_GRID, {}))
                self._last_session_path    = list(self._robot_path)
                self._last_session_outline = _parse_live_outline(
                    data.get(DATA_SEEN_POLYGON, {})
                )
                self._session_complete  = True
                LOGGER.info("Session complete — %d path points frozen", len(self._robot_path))
            self._last_mode = mode

            # ══════════════════════════════════════════════════════════════
            # CLEANING BUCKETS
            # ══════════════════════════════════════════════════════════════
            if is_active:

                # ── GYRO BUCKET (every 500 ms): heading + odometry ────────
                # Runs on every coordinator tick (tick = 500ms during cleaning).
                # Fetches sensor_values for gyro z-rate + odometry delta only.
                gyro_due = (self._last_sensor_hw is None or
                            (now - self._last_sensor_hw).total_seconds() >=
                            SCAN_INTERVAL_GYRO)
                if gyro_due:
                    try:
                        raw_sv = await self.client.get_sensor_values()
                        parsed = _parse_sensor_values(raw_sv)
                        data[DATA_SENSOR_VALUES_PARSED] = parsed
                        self._update_odometry(parsed)
                    except CannotConnect:
                        LOGGER.debug("get_sensor_values unavailable")
                    self._last_sensor_hw = now

                # ── STATUS BUCKET (every 2 s): mode + battery ─────────────
                status_due = (self._last_status_check is None or
                              (now - self._last_status_check).total_seconds() >=
                              SCAN_INTERVAL_STATUS)
                if status_due:
                    self._last_status_check = now

                # ── BRUSH STUCK (every 10 s): gpio stuck flags ─────────────
                brush_due = (self._last_brush_check is None or
                             (now - self._last_brush_check).total_seconds() >=
                             SCAN_INTERVAL_SENSOR_HW)
                if brush_due:
                    parsed_sv = data.get(DATA_SENSOR_VALUES_PARSED, {})
                    if parsed_sv:
                        self._fire_brush_alerts(parsed_sv)
                    self._last_brush_check = now

                # ── SLAM BUCKET (every 5 s): position + map data ───────────
                slam_due = (self._last_slam is None or
                            (now - self._last_slam).total_seconds() >=
                            SCAN_INTERVAL_SLAM)
                if slam_due:
                    # Map session detection
                    try:
                        map_status = await self.client.get_map_status()
                        data[DATA_MAP_STATUS] = map_status
                        self._operation_map_id = str(
                            map_status.get("operation_map_id", self.map_id)
                        )
                    except CannotConnect:
                        LOGGER.debug("get_map_status unavailable")

                    is_live_map = (self._operation_map_id != self.map_id)

                    # SLAM position
                    new_slam: dict | None = None
                    if is_live_map:
                        try:
                            exploration = await self.client.get_exploration()
                            data[DATA_EXPLORATION] = exploration
                            new_slam = _extract_exploration_position(exploration)
                        except CannotConnect:
                            LOGGER.debug("get_exploration unavailable")
                    else:
                        try:
                            reloc = await self.client.get_relocalization()
                            data[DATA_RELOCALIZATION] = reloc
                            new_slam = _extract_relocalization_position(reloc)
                        except CannotConnect:
                            LOGGER.debug("get_relocalization unavailable")

                    if new_slam:
                        self._set_slam_anchor(new_slam)

                    # Cleaned area polygon
                    try:
                        data[DATA_SEEN_POLYGON] = await self.client.get_seen_polygon()
                    except CannotConnect:
                        LOGGER.debug("get_seen_polygon unavailable")

                    # Cleaning grid
                    try:
                        grid = await self.client.get_cleaning_grid_map()
                        data[DATA_CLEANING_GRID] = grid
                    except CannotConnect:
                        LOGGER.debug("get_cleaning_grid_map unavailable")

                    self._last_slam = now

            # ══════════════════════════════════════════════════════════════
            # IDLE BUCKET
            # ══════════════════════════════════════════════════════════════
            else:
                # Stale localization — only useful for last-known position display
                loc_due = (self._last_idle_loc is None or
                           (now - self._last_idle_loc) >=
                           timedelta(seconds=SCAN_INTERVAL_IDLE_LOC))
                if loc_due:
                    try:
                        loc = await self.client.get_localization()
                        idle_pos = _extract_localization_position(loc)
                        if idle_pos and not self._slam_anchor:
                            self._slam_anchor = idle_pos
                    except CannotConnect:
                        LOGGER.debug("get_localization unavailable")
                    self._last_idle_loc = now

            # ── Accumulate robot path ─────────────────────────────────────
            display_pos = self._get_display_position()
            if is_active and display_pos:
                pt = (display_pos["x"], display_pos["y"])
                if (not self._robot_path or
                        (pt[0] - self._robot_path[-1][0]) ** 2 +
                        (pt[1] - self._robot_path[-1][1]) ** 2 >= MIN_MOVE_UNITS ** 2):
                    self._robot_path.append(pt)
                    if len(self._robot_path) > MAX_PATH_POINTS:
                        self._robot_path = self._robot_path[-MAX_PATH_POINTS:]

            # ── Build live_map payload ────────────────────────────────────
            data[DATA_LIVE_MAP] = _build_live_map_payload(
                existing         = data.get(DATA_LIVE_MAP, {}),
                robot_position   = display_pos,
                seen_polygon_raw = data.get(DATA_SEEN_POLYGON, {}),
                cleaning_grid    = (data.get(DATA_CLEANING_GRID, {})
                                    if is_active else self._last_session_grid),
                robot_path       = list(self._robot_path),
                session_complete = self._session_complete,
                is_active        = is_active,
                is_live_map      = self._operation_map_id != self.map_id,
                operation_map_id = self._operation_map_id,
                map_id           = self.map_id,
            )

            # ══════════════════════════════════════════════════════════════
            # SLOW BUCKETS (both active and idle)
            # ══════════════════════════════════════════════════════════════

            # ── Every 300 s: areas + schedule + sensor status ─────────────
            areas_due = (self._last_areas is None or
                         (now - self._last_areas) >=
                         timedelta(seconds=SCAN_INTERVAL_AREAS))
            if areas_due:
                new_areas = await self.client.get_areas(self.map_id)
                data[DATA_AREAS] = new_areas
                try:
                    data[DATA_SCHEDULE] = await self.client.get_schedule()
                except CannotConnect:
                    LOGGER.debug("get_schedule unavailable")
                try:
                    data[DATA_SENSOR_STATUS] = await self.client.get_sensor_status()
                except CannotConnect:
                    LOGGER.debug("get_sensor_status unavailable")
                try:
                    data[DATA_ROBOT_FLAGS] = await self.client.get_robot_flags()
                except CannotConnect:
                    LOGGER.debug("get_robot_flags unavailable")
                try:
                    polygons = await self.client.get_n_n_polygons()
                    floor_plan = _extract_floor_plan(polygons, new_areas)
                    lm = dict(data.get(DATA_LIVE_MAP, {}))
                    lm["floor_plan"]        = floor_plan
                    lm["coordinate_bounds"] = _compute_bounds(floor_plan)
                    data[DATA_LIVE_MAP] = lm
                except CannotConnect:
                    LOGGER.debug("get_n_n_polygons unavailable")
                self._last_areas = now
                self._check_for_new_areas(new_areas)

            # ── Every 600 s: statistics ───────────────────────────────────
            stats_due = (self._last_statistics is None or
                         (now - self._last_statistics) >=
                         timedelta(seconds=SCAN_INTERVAL_STATISTICS))
            if stats_due:
                data[DATA_STATISTICS] = await self.client.get_statistics()
                try:
                    data[DATA_PERMANENT_STATISTICS] = (
                        await self.client.get_permanent_statistics()
                    )
                except CannotConnect:
                    pass
                self._last_statistics = now

            # ── Every 3600 s: robot identity ──────────────────────────────
            info_due = (self._last_robot_info is None or
                        (now - self._last_robot_info) >=
                        timedelta(seconds=SCAN_INTERVAL_ROBOT_INFO))
            if info_due:
                robot_id    = await self.client.get_robot_id()
                wifi_status = await self.client.get_wifi_status()
                protocol    = await self.client.get_protocol_version()
                data[DATA_ROBOT_INFO] = {
                    "robot_id":         robot_id,
                    "wifi_status":      wifi_status,
                    "protocol_version": protocol,
                }
                self._last_robot_info = now

            self._consecutive_failures = 0
            return data

        except CannotConnect as err:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_POLL_FAILURES:
                raise UpdateFailed(f"RobEye API unreachable: {err}") from err
            LOGGER.warning("RobEye poll failed (%d/%d): %s",
                           self._consecutive_failures, MAX_POLL_FAILURES, err)
            return data

    # ── Dead-reckoning helpers ────────────────────────────────────────

    def _set_slam_anchor(self, pos: dict) -> None:
        """Set a new SLAM ground-truth position and reset odometry accumulator."""
        self._slam_anchor    = pos
        self._slam_anchor_ts = _time.monotonic()
        self._odom_dx        = 0.0
        self._odom_dy        = 0.0

    def _update_odometry(self, parsed: dict) -> None:
        """Accumulate odometry deltas from sensor_values motion_odometry data.

        Each measurement in the API covers 10ms. API returns the last 5 (50ms).
        We sum them to get total displacement since this poll.
        Units: API units (1 unit = 2mm = 0.2cm).
        """
        dx = parsed.get("odometry_dx", 0)
        dy = parsed.get("odometry_dy", 0)
        if dx != 0 or dy != 0:
            self._odom_dx += dx
            self._odom_dy += dy

    def _get_display_position(self) -> dict | None:
        """Return best current position estimate for UI display.

        Combines the last SLAM anchor with accumulated odometry delta.
        Falls back to SLAM anchor alone if no odometry available.
        """
        if not self._slam_anchor:
            return None
        age_s = _time.monotonic() - self._slam_anchor_ts
        return {
            "x":           self._slam_anchor["x"] + self._odom_dx,
            "y":           self._slam_anchor["y"] + self._odom_dy,
            "heading_deg": self._slam_anchor["heading_deg"],
            "source":      self._slam_anchor.get("source", "slam"),
            "is_live":     True,
            "slam_age_s":  round(age_s, 1),
        }

    # ── Brush alert helper ────────────────────────────────────────────

    def _fire_brush_alerts(self, parsed: dict) -> None:
        """Fire persistent HA notification when a brush becomes stuck.
        Clears the notification when the brush is no longer stuck.
        """
        for side, descriptor in [
            ("Left",  "side_brush_left_stuck"),
            ("Right", "side_brush_right_stuck"),
        ]:
            stuck = parsed.get(f"gpio__{descriptor}") == "active"
            attr  = f"_brush_{descriptor}_notified"
            was   = getattr(self, attr, False)

            if stuck and not was:
                self.hass.components.persistent_notification.async_create(
                    f"\u26a0\ufe0f {side} side brush is stuck or wrapped on the "
                    "Rowenta Xplorer 120. Please check and clean it.",
                    title="Rowenta \u2014 Brush Alert",
                    notification_id=f"rowenta_brush_{descriptor}",
                )
                setattr(self, attr, True)
            elif not stuck and was:
                self.hass.components.persistent_notification.async_dismiss(
                    notification_id=f"rowenta_brush_{descriptor}",
                )
                setattr(self, attr, False)

    # ── Dynamic area discovery ────────────────────────────────────────

    def _check_for_new_areas(self, areas_blob: dict[str, Any]) -> None:
        """Compare fresh areas to known set.

        When any new area IDs appear (or disappear), dispatch
        SIGNAL_AREAS_UPDATED so platform listeners can add/remove entities
        without a full integration reload.
        """
        areas = (
            areas_blob.get("areas", []) if isinstance(areas_blob, dict) else []
        )
        current_ids: set = {
            a.get("id") for a in areas if a.get("id") is not None
        }

        if not self._known_area_ids:
            self._known_area_ids = current_ids
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
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_AREAS_UPDATED}_{self.config_entry.entry_id}",
            )

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
        return (self.data or {}).get(DATA_SENSOR_VALUES_PARSED, {})

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


# ── Sensor-values helpers ─────────────────────────────────────────────

def _parse_sensor_values(raw: dict) -> dict:
    """Flatten /get/sensor_values into a simple key→value dict.

    Confirmed device_descriptors from real device 2026-03-20.
    Keys produced:
      gpio__<descriptor>           → "active" | "inactive"
        descriptors: bumper_left, bumper_right, dock, dustbin,
                     drop_1..4, side_brush_left_stuck,
                     side_brush_right_stuck, wheel_switch_left/right
      current_sensor__<descriptor> → int (mA)
        descriptors: battery, fan, main_brush,
                     side_brush_left, side_brush_right,
                     wheel_left, wheel_right
      voltage_sensor__<descriptor> → int (mV)
        descriptors: battery, input
      gyro_x, gyro_y, gyro_z       → int (raw angular rate)
      odometry_dx, odometry_dy     → int (sum of rel_movement[0]/[1])
      odometry_measurements        → list of raw measurement dicts
    """
    result: dict = {}
    for device in raw.get("sensor_data", []):
        dtype = device.get("device_type", "")
        for entry in device.get("sensor_data", []):
            desc    = entry.get("device_descriptor", "")
            payload = entry.get("payload", {})
            data    = payload.get("data", {})

            if dtype == "gpio":
                result[f"gpio__{desc}"] = data.get("value", "inactive")

            elif dtype == "current_sensor":
                result[f"current_sensor__{desc}"] = data.get("current")

            elif dtype == "voltage_sensor":
                result[f"voltage_sensor__{desc}"] = data.get("voltage")

            elif dtype == "gyroscope":
                meas = data.get("measurements", [{}])
                if meas:
                    result["gyro_x"] = meas[-1].get("x", 0)
                    result["gyro_y"] = meas[-1].get("y", 0)
                    result["gyro_z"] = meas[-1].get("z", 0)

            elif dtype == "motion_odometry":
                measurements = data.get("measurements", [])
                result["odometry_measurements"] = measurements
                result["odometry_dx"] = sum(
                    m.get("rel_movement", [0, 0, 0])[0] for m in measurements
                )
                result["odometry_dy"] = sum(
                    m.get("rel_movement", [0, 0, 0])[1] for m in measurements
                )

    return result


def _gpio(parsed: dict, descriptor: str) -> str:
    """Return the GPIO value ('active'|'inactive') for a descriptor."""
    return parsed.get(f"gpio__{descriptor}", "inactive")


def _current_ma(parsed: dict, descriptor: str) -> int | None:
    """Return the current-sensor reading in mA, or None if unavailable."""
    return parsed.get(f"current_sensor__{descriptor}")


def _extract_floor_plan(polygons: dict, areas: dict) -> dict:
    """Extract static floor plan from /get/n_n_polygons + /get/areas.

    Returns a dict with rooms, avoidance_zones, walls, dock, outline —
    the parts that change only every 300s (areas poll interval).
    """
    room_areas, avoidance_areas = _classify_areas(areas.get("areas", []))

    rooms: list[dict] = []
    for idx, area in enumerate(room_areas):
        try:
            meta = json.loads(area.get("area_meta_data", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        name = meta.get("name") or f"Room {area.get('id', idx)}"
        raw_pts = area.get("points", [])
        pts = [[p["x"], p["y"]] for p in raw_pts]
        rooms.append({
            "id":         area.get("id"),
            "name":       name,
            "room_type":  area.get("room_type", "none"),
            "area_state": area.get("area_state", "inactive"),
            "polygon":    pts,
            "color":      _ROOM_COLORS[idx % len(_ROOM_COLORS)],
            "redundant":  not _is_real_room(area),
            "area_m2":    _calc_area_m2(raw_pts),
        })

    avoidance_zones: list[dict] = []
    for area in avoidance_areas:
        raw_pts = area.get("points", [])
        avoidance_zones.append({
            "id":      area.get("id"),
            "polygon": [[p["x"], p["y"]] for p in raw_pts],
        })

    # Walls and dock from n_n_polygons map data (same structure as feature_map)
    map_data = polygons.get("map", {})
    walls = [
        [ln["x1"], ln["y1"], ln["x2"], ln["y2"]]
        for ln in map_data.get("lines", [])
    ]
    dock_raw = map_data.get("docking_pose") or {}
    dock: dict | None = None
    if dock_raw and str(dock_raw.get("valid", "")).lower() in ("true", "1", True):
        dock = {
            "x":           dock_raw["x"],
            "y":           dock_raw["y"],
            "heading_deg": round(dock_raw.get("heading", 0) / _HEADING_SCALE, 1),
        }

    # Outline from n_n_polygons polygon list
    outline: list = []
    for poly in (polygons.get("polygons") or []):
        segs = poly.get("segments", [])
        if segs:
            outline = [[s["x1"], s["y1"]] for s in segs]
            outline.append([segs[-1]["x2"], segs[-1]["y2"]])
            break

    return {
        "rooms":           rooms,
        "avoidance_zones": avoidance_zones,
        "walls":           walls,
        "dock":            dock,
        "outline":         outline,
    }


def _compute_bounds(floor_plan: dict) -> dict:
    """Compute coordinate bounds from a floor plan dict."""
    all_pts: list = []
    for r in floor_plan.get("rooms", []):
        all_pts.extend(r.get("polygon", []))
    for z in floor_plan.get("avoidance_zones", []):
        all_pts.extend(z.get("polygon", []))
    all_pts.extend(floor_plan.get("outline", []))
    if floor_plan.get("dock"):
        d = floor_plan["dock"]
        all_pts.append([d["x"], d["y"]])
    if not all_pts:
        return {"min_x": -800, "max_x": 2400, "min_y": -1300, "max_y": 1400}
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    return {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}


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
    robot_position: dict | None = None,
    seen_polygon_raw: dict[str, Any] | None = None,
    cleaning_grid: dict[str, Any] | None = None,
    is_active: bool = False,
    is_live_map: bool = False,
    map_id: str = "",
    operation_map_id: str = "",
    robot_path: list | None = None,
    session_complete: bool = False,
    # Legacy params kept for backward compatibility with existing tests:
    live_params: dict[str, Any] | None = None,
    feature_map: dict[str, Any] | None = None,
    tile_map: dict[str, Any] | None = None,
    areas_data: dict[str, Any] | None = None,
    seen_poly_saved_map: dict[str, Any] | None = None,
    last_session_grid: dict | None = None,
    last_session_path: list | None = None,
    last_session_outline: list | None = None,
) -> dict[str, Any]:
    """Compose the live_map attribute dict for the SVG card sensor.

    Schema (used by rowenta-map-card.js):
      map_id, is_active, rooms, outline, walls, dock, robot,
      live_outline, bounds, scale

    Static floor plan data (rooms, walls, dock, outline) is carried forward
    from `existing` when not provided via legacy params. The areas_due block
    populates these via _extract_floor_plan / _compute_bounds.
    """
    if seen_polygon_raw is None:
        seen_polygon_raw = {}
    if cleaning_grid is None:
        cleaning_grid = {}
    if robot_path is None:
        robot_path = []

    # ── Rooms and avoidance zones ─────────────────────────────────────
    if areas_data is not None:
        # Legacy path: explicit areas_data provided (used by tests)
        room_areas, avoidance_areas = _classify_areas(areas_data.get("areas", []))
        rooms: list[dict[str, Any]] = []
        for idx, area in enumerate(room_areas):
            try:
                meta = json.loads(area.get("area_meta_data", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            name = meta.get("name") or f"Room {area.get('id', idx)}"
            raw_pts = area.get("points", [])
            pts = [[p["x"], p["y"]] for p in raw_pts]
            rooms.append({
                "id": area.get("id"),
                "name": name,
                "room_type": area.get("room_type", "none"),
                "area_state": area.get("area_state", "inactive"),
                "polygon": pts,
                "color": _ROOM_COLORS[idx % len(_ROOM_COLORS)],
                "redundant": not _is_real_room(area),
                "area_m2": _calc_area_m2(raw_pts),
            })
        avoidance_zones: list[dict[str, Any]] = []
        for area in avoidance_areas:
            raw_pts = area.get("points", [])
            avoidance_zones.append({
                "id": area.get("id"),
                "polygon": [[p["x"], p["y"]] for p in raw_pts],
            })
    else:
        # New path: carry forward from existing floor plan data
        rooms = list(existing.get("rooms", []))
        avoidance_zones = list(existing.get("avoidance_zones", []))

    # ── Outline ───────────────────────────────────────────────────────
    if seen_poly_saved_map is not None:
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
        if not outline and tile_map:
            outline = [[p["x"], p["y"]] for p in (tile_map or {}).get("outline", [])]
    else:
        outline = list(existing.get("outline", []))

    # ── Walls ─────────────────────────────────────────────────────────
    if feature_map is not None:
        walls = [
            [ln["x1"], ln["y1"], ln["x2"], ln["y2"]]
            for ln in feature_map.get("map", {}).get("lines", [])
        ]
    else:
        walls = list(existing.get("walls", []))

    # ── Dock ──────────────────────────────────────────────────────────
    if feature_map is not None or tile_map is not None:
        dock_raw = (
            (feature_map or {}).get("map", {}).get("docking_pose")
            or (tile_map or {}).get("map", {}).get("docking_pose")
            or {}
        )
        dock: dict[str, Any] | None = None
        if dock_raw and str(dock_raw.get("valid", "")).lower() in ("true", "1", True):
            dock = {
                "x": dock_raw["x"],
                "y": dock_raw["y"],
                "heading_deg": round(dock_raw.get("heading", 0) / _HEADING_SCALE, 1),
            }
    else:
        dock = existing.get("dock")

    # ── Robot live position ───────────────────────────────────────────
    robot: dict[str, Any] | None = robot_position

    # When idle, override stale localization with dock position so the robot
    # icon shows at the charger rather than at the last cleaning position.
    if not is_active and dock and (robot is None or not robot.get("is_live", True)):
        robot = {**dock, "source": "dock", "is_live": False}

    # ── Live / session outline ────────────────────────────────────────
    if is_active:
        live_outline: list[list[int]] = _parse_live_outline(seen_polygon_raw)
    elif session_complete:
        live_outline = list(last_session_outline or existing.get("live_outline", []))
    else:
        live_outline = []

    # ── Display grid and path ─────────────────────────────────────────
    # In the new design, the caller pre-selects active vs session grid/path.
    # In the legacy path (tests), use last_session_* when not active.
    if is_active:
        display_grid = (
            cleaning_grid
            if isinstance(cleaning_grid, dict) and cleaning_grid.get("size_x", 0) > 0
            else {}
        )
        display_path = list(robot_path)
    elif last_session_grid is not None:
        # Legacy path: explicit session data provided
        display_grid = last_session_grid
        display_path = list(last_session_path or [])
    else:
        # New path: cleaning_grid and robot_path already pre-selected by caller
        display_grid = cleaning_grid if isinstance(cleaning_grid, dict) else {}
        display_path = list(robot_path)

    # ── Bounding box ─────────────────────────────────────────────────
    all_pts: list[list[int]] = (
        [pt for r in rooms for pt in r["polygon"]]
        + [pt for z in avoidance_zones for pt in z["polygon"]]
        + outline
        + live_outline
        + ([[dock["x"], dock["y"]]] if dock else [])
    )
    g = display_grid
    if isinstance(g, dict) and g.get("size_x", 0) > 0:
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
        "map_id":           map_id or existing.get("map_id", ""),
        "is_active":        is_active,
        "is_live_map":      is_live_map,
        "operation_map_id": operation_map_id or existing.get("operation_map_id", ""),
        "rooms":            rooms,
        "avoidance_zones":  avoidance_zones,
        "outline":          outline,
        "walls":            walls,
        "dock":             dock,
        "robot":            robot,
        "live_outline":     live_outline,
        "bounds":           bounds,
        "scale":            "mm",  # 1 API unit = 2 mm
        "cleaning_grid":    display_grid,
        "robot_path":       display_path,
        "session_complete": session_complete,
    }
