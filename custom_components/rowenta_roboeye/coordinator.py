"""DataUpdateCoordinator for the Rowenta Xplorer 120 integration.

Key improvements over v1:
- Tracks known_area_ids and fires SIGNAL_AREAS_UPDATED when the room set
  changes so platforms can add new entities WITHOUT a full reload.
- Adaptive live-map polling: 5 s during cleaning, 60 s when idle.
- Exposes structured live_map data (floor_plan, cleaned_area, robot_position)
  for the Phase-2 SVG card via sensor.xplorer120_live_map.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, RobEyeApiClient
from .const import (
    DATA_AREAS,
    DATA_AREAS_SAVED_MAP,
    DATA_CLEANING_GRID,
    DATA_FEATURE_MAP,
    DATA_LIVE_MAP,
    DATA_LIVE_PARAMETERS,
    DATA_PERMANENT_STATISTICS,
    DATA_ROBOT_FLAGS,
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
    UPDATE_INTERVAL,
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
        self._consecutive_failures: int = 0

        # Track known area IDs so we can detect additions/removals without reload
        self._known_area_ids: set = set()

        # Deep clean mode — toggled by switch entity, read by all clean operations
        self.deep_clean_enabled: bool = False

        # Last-session replay state
        self._robot_path: list[tuple[float, float]] = []
        self._last_session_grid: dict = {}
        self._last_session_path: list = []
        self._last_session_outline: list = []
        self._last_mode: str = ""
        self._session_complete: bool = False

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
            # ── Every 15 s: status ───────────────────────────────────
            data[DATA_STATUS] = await self.client.get_status()

            mode = data[DATA_STATUS].get("mode", "")
            is_active = mode in (MODE_CLEANING, MODE_GO_HOME)

            # ── Session lifecycle tracking ────────────────────────────
            was_cleaning = self._last_mode == MODE_CLEANING
            now_docked   = not is_active and mode not in (MODE_CLEANING, MODE_GO_HOME)

            if mode == MODE_CLEANING and self._last_mode != MODE_CLEANING:
                self._robot_path = []
                self._last_session_grid = {}
                self._last_session_path = []
                self._last_session_outline = []
                self._session_complete = False
                LOGGER.debug("New cleaning session — path and grid reset")

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

            # ── Adaptive live-map polling: 5 s active / 60 s idle ────
            live_map_interval = 5 if is_active else 60
            if self._last_live_map is None or (
                now - self._last_live_map
            ) >= timedelta(seconds=live_map_interval):
                live_params: dict[str, Any] = {}

                try:
                    live_params = await self.client.get_live_parameters()
                    data[DATA_LIVE_PARAMETERS] = live_params
                except CannotConnect:
                    LOGGER.debug("get_live_parameters unavailable, skipping")

                cleaning_grid: dict = {}
                seen_polygon_raw: dict = {}
                localization: dict = {}
                if is_active:
                    try:
                        seen_polygon_raw = await self.client.get_seen_polygon()
                        data[DATA_SEEN_POLYGON] = seen_polygon_raw
                    except CannotConnect:
                        LOGGER.debug("get_seen_polygon unavailable, skipping")
                    try:
                        cleaning_grid = await self.client.get_cleaning_grid_map()
                        data[DATA_CLEANING_GRID] = cleaning_grid
                    except CannotConnect:
                        LOGGER.debug("get_cleaning_grid_map unavailable, skipping")
                    try:
                        localization = await self.client.get_localization()
                    except CannotConnect:
                        LOGGER.debug("get_localization unavailable, skipping")
                else:
                    data[DATA_SEEN_POLYGON] = {}
                    data[DATA_CLEANING_GRID] = {}

                data[DATA_LIVE_MAP] = _build_live_map_payload(
                    existing=data.get(DATA_LIVE_MAP, {}),
                    live_params=live_params,
                    localization=localization,
                    seen_polygon_raw=seen_polygon_raw,
                    cleaning_grid=cleaning_grid,
                    feature_map=data.get(DATA_FEATURE_MAP, {}),
                    tile_map=data.get(DATA_TILE_MAP, {}),
                    areas_data=data.get(DATA_AREAS_SAVED_MAP, {}),
                    seen_poly_saved_map=data.get(DATA_SEEN_POLY_SAVED_MAP, {}),
                    is_active=is_active,
                    map_id=self.map_id,
                    robot_path=self._robot_path,
                    last_session_grid=self._last_session_grid,
                    last_session_path=self._last_session_path,
                    last_session_outline=self._last_session_outline,
                    session_complete=self._session_complete,
                )

                # ── Accumulate robot path during cleaning ─────────────
                if mode == MODE_CLEANING:
                    robot = data[DATA_LIVE_MAP].get("robot")
                    if robot:
                        pt: tuple[float, float] = (robot["x"], robot["y"])
                        if (
                            not self._robot_path
                            or (pt[0] - self._robot_path[-1][0]) ** 2
                            + (pt[1] - self._robot_path[-1][1]) ** 2
                            >= _MIN_MOVE_UNITS ** 2
                        ):
                            self._robot_path.append(pt)
                            if len(self._robot_path) > _MAX_PATH_POINTS:
                                self._robot_path = self._robot_path[-_MAX_PATH_POINTS:]

                self._last_live_map = now

            # ── Every 300 s: areas + sensor status ───────────────────
            if self._last_areas is None or (
                now - self._last_areas
            ) >= timedelta(seconds=SCAN_INTERVAL_AREAS):
                new_areas_blob = await self.client.get_areas(self.map_id)
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

            # ── Every 600 s: saved-map geometry (walls, rooms, outline) ──
            if self._last_map_geometry is None or (
                now - self._last_map_geometry
            ) >= timedelta(seconds=SCAN_INTERVAL_MAP_GEOMETRY):
                try:
                    data[DATA_FEATURE_MAP] = await self.client.get_feature_map(self.map_id)
                except CannotConnect:
                    LOGGER.debug("get_feature_map unavailable, skipping")
                try:
                    data[DATA_TILE_MAP] = await self.client.get_tile_map(self.map_id)
                except CannotConnect:
                    LOGGER.debug("get_tile_map unavailable, skipping")
                try:
                    data[DATA_AREAS_SAVED_MAP] = await self.client.get_areas(self.map_id)
                except CannotConnect:
                    LOGGER.debug("get_areas (map geometry) unavailable, skipping")
                try:
                    data[DATA_SEEN_POLY_SAVED_MAP] = await self.client.get_seen_polygon(self.map_id)
                except CannotConnect:
                    LOGGER.debug("get_seen_polygon (map geometry) unavailable, skipping")

                # Load last-session grid from saved map (also runs on startup)
                if not is_active:
                    try:
                        saved_grid = await self.client.get_cleaning_grid_map(
                            map_id=self.map_id
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


# ── Live-map helpers ──────────────────────────────────────────────────

_ROOM_COLORS = [
    "#4A90D9", "#E67E22", "#2ECC71", "#9B59B6", "#E74C3C",
    "#1ABC9C", "#F39C12", "#3498DB", "#D35400", "#27AE60",
    "#8E44AD", "#C0392B", "#16A085", "#F1C40F", "#2980B9",
    "#E91E63", "#00BCD4",
]

_HEADING_SCALE = 65536 / 360  # raw heading units per degree


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


def _build_live_map_payload(
    existing: dict[str, Any],
    live_params: dict[str, Any],
    localization: dict[str, Any],
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

    Schema (used by rowenta-map-card.js):
      map_id, is_active, rooms, outline, walls, dock, robot,
      live_outline, bounds, scale
    """
    import math as _math

    # ── Rooms (from /get/areas?map_id) ───────────────────────────────
    rooms: list[dict[str, Any]] = []
    for idx, area in enumerate(areas_data.get("areas", [])):
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
    # Supports two localization response formats:
    #   1. /debug/localization: { localization_algo_input: [{ rob_pose: [x, y, heading_mrad] }] }
    #   2. Generic: { position: { x, y, heading } } or { pose: { x, y, angle } }
    robot: dict[str, Any] | None = None
    if is_active and localization:
        # Format 1: /debug/localization
        entries = localization.get("localization_algo_input", [])
        rob_pose = None
        for entry in entries:
            rp = entry.get("rob_pose")
            if isinstance(rp, list) and len(rp) >= 2:
                rob_pose = rp
                break
        if rob_pose is not None:
            heading_mrad = rob_pose[2] if len(rob_pose) > 2 else 0
            heading_deg = _math.degrees(heading_mrad / 1000.0)
            robot = {
                "x": rob_pose[0],
                "y": rob_pose[1],
                "heading_deg": round(heading_deg, 1),
            }
        else:
            # Format 2: generic position/pose dict
            loc = localization.get("position", {}) or localization.get("pose", {})
            if loc:
                h = loc.get("heading", loc.get("angle", 0))
                robot = {
                    "x": loc.get("x", 0),
                    "y": loc.get("y", 0),
                    "heading_deg": round(h / _HEADING_SCALE, 1),
                }

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
