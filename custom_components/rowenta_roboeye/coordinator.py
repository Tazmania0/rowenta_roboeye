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
    DATA_LIVE_MAP,
    DATA_LIVE_PARAMETERS,
    DATA_PERMANENT_STATISTICS,
    DATA_ROBOT_FLAGS,
    DATA_ROBOT_INFO,
    DATA_CLEANING_GRID,
    DATA_SEEN_POLYGON,
    DATA_SENSOR_STATUS,
    DATA_STATISTICS,
    DATA_STATUS,
    DOMAIN,
    LOGGER,
    MAX_POLL_FAILURES,
    MODE_CLEANING,
    MODE_GO_HOME,
    SCAN_INTERVAL_AREAS,
    SCAN_INTERVAL_ROBOT_INFO,
    SCAN_INTERVAL_STATISTICS,
    SIGNAL_AREAS_UPDATED,
    UPDATE_INTERVAL,
)


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
        self._consecutive_failures: int = 0

        # Track known area IDs so we can detect additions/removals without reload
        self._known_area_ids: set = set()

        # Deep clean mode — toggled by switch entity, read by all clean operations
        self.deep_clean_enabled: bool = False

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
                    is_active=is_active,
                    map_id=self.map_id,
                )
                self._last_live_map = now

            # ── Every 300 s: areas + sensor status + floor plan ──────
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

                # Floor-plan polygons — fetched from static map, available any time
                try:
                    polygons = await self.client.get_n_n_polygons()
                    LOGGER.debug(
                        "n_n_polygons raw response: type=%s keys=%s snippet=%s",
                        type(polygons).__name__,
                        list(polygons.keys()) if isinstance(polygons, dict) else "list",
                        str(polygons)[:300],
                    )
                    floor_plan = _extract_floor_plan(polygons, new_areas_blob)
                    LOGGER.debug("n_n_polygons parsed: %d room(s)", len(floor_plan))
                    if DATA_LIVE_MAP not in data:
                        data[DATA_LIVE_MAP] = {}
                    data[DATA_LIVE_MAP] = dict(data[DATA_LIVE_MAP])
                    data[DATA_LIVE_MAP]["floor_plan"] = floor_plan
                    data[DATA_LIVE_MAP]["raw_n_n_polygons"] = polygons
                    bounds = _compute_bounds(floor_plan)
                    if bounds:
                        data[DATA_LIVE_MAP]["coordinate_bounds"] = bounds
                except CannotConnect:
                    LOGGER.debug("get_n_n_polygons unavailable, skipping")

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

    # ── Command helper ────────────────────────────────────────────────

    async def async_send_command(self, coro_func, *args: Any, **kwargs: Any) -> None:
        await coro_func(*args, **kwargs)
        await self.async_request_refresh()


# ── Live-map helpers ──────────────────────────────────────────────────

def _build_live_map_payload(
    existing: dict[str, Any],
    live_params: dict[str, Any],
    localization: dict[str, Any],
    seen_polygon_raw: dict[str, Any],
    cleaning_grid: dict[str, Any],
    is_active: bool,
    map_id: str,
) -> dict[str, Any]:
    """Compose the live_map attribute dict for the SVG card sensor.

    Confirmed data sources from real API responses:
      - debug/localization: { localization_algo_input: [{ rob_pose: [x_mm, y_mm, heading_mrad] }] }
      - seen_polygon:        { map_id, polygons: [{ segments: [{x1,y1,x2,y2}] }] }
      - cleaning_grid_map:   RLE occupancy grid { lower_left_x/y, size_x/y, resolution, cleaned:[] }
      - live_parameters:     behaviour config only — no position data
    """
    # ── Robot position ────────────────────────────────────────────────
    # localization_algo_input[0].rob_pose = [x_mm, y_mm, heading_milliradians]
    robot_position = None
    if localization:
        entries = localization.get("localization_algo_input", [])
        rob_pose = None
        for entry in entries:
            rp = entry.get("rob_pose")
            if isinstance(rp, list) and len(rp) >= 2:
                rob_pose = rp
                break
        if rob_pose is not None:
            import math as _math
            x_mm         = rob_pose[0]
            y_mm         = rob_pose[1]
            heading_mrad = rob_pose[2] if len(rob_pose) > 2 else 0
            heading_deg  = _math.degrees(heading_mrad / 1000.0)
            robot_position = {"x": x_mm, "y": y_mm, "heading_deg": heading_deg}
            LOGGER.debug("Robot position: x=%s y=%s heading=%.1f°", x_mm, y_mm, heading_deg)
        else:
            LOGGER.debug(
                "localization received but rob_pose not found. Keys: %s",
                list(localization.keys()),
            )

    # ── Cleaned area polygon ──────────────────────────────────────────
    # seen_polygon format: { polygons: [{ segments: [{x1,y1,x2,y2}] }] }
    cleaned_polygon: list[list[float]] = []
    if seen_polygon_raw and is_active:
        for poly in seen_polygon_raw.get("polygons", []):
            segments = poly.get("segments", [])
            if segments:
                pts = [[s["x1"], s["y1"]] for s in segments if "x1" in s and "y1" in s]
                if pts:
                    last = segments[-1]
                    pts.append([last.get("x2", pts[0][0]), last.get("y2", pts[0][1])])
                    # Snap to axis-aligned bounding rectangle for square outline
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    cleaned_polygon = [
                        [min(xs), min(ys)],
                        [max(xs), min(ys)],
                        [max(xs), max(ys)],
                        [min(xs), max(ys)],
                    ]
                    break

    # Preserve last known cleaned_area and cleaning_grid when idle so the
    # map outline persists between cleaning runs.
    if not cleaned_polygon:
        cleaned_polygon = existing.get("cleaned_area", [])

    effective_grid = cleaning_grid if is_active else existing.get("cleaning_grid", {})

    # ── Coordinate bounds ─────────────────────────────────────────────
    # Priority: floor_plan bounds → seen_polygon extent → cleaning_grid extent
    coord_bounds = existing.get("coordinate_bounds")
    if not coord_bounds and cleaned_polygon:
        xs = [p[0] for p in cleaned_polygon]
        ys = [p[1] for p in cleaned_polygon]
        pad = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.1 or 200
        coord_bounds = {
            "min_x": min(xs) - pad, "max_x": max(xs) + pad,
            "min_y": min(ys) - pad, "max_y": max(ys) + pad,
        }
    if not coord_bounds and effective_grid:
        res = effective_grid.get("resolution", 1) or 1
        llx = effective_grid.get("lower_left_x", 0)
        lly = effective_grid.get("lower_left_y", 0)
        w = effective_grid.get("size_x", 0) * res
        h = effective_grid.get("size_y", 0) * res
        if w > 0 and h > 0:
            coord_bounds = {
                "min_x": llx, "max_x": llx + w,
                "min_y": lly, "max_y": lly + h,
            }

    return {
        "floor_plan":        existing.get("floor_plan", []),
        "coordinate_bounds": coord_bounds,
        "cleaned_area":      cleaned_polygon,
        "cleaning_grid":     effective_grid,
        "robot_position":    robot_position,
        "map_id":            map_id,
        # Raw API responses for diagnostic card
        "raw_live_parameters": live_params,
        "raw_localization":    localization,
        "raw_seen_polygon":    seen_polygon_raw,
        "raw_n_n_polygons":    existing.get("raw_n_n_polygons"),
    }


def _extract_floor_plan(
    polygons_response: Any,
    areas_blob: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert /get/n_n_polygons + areas into room polygon objects for SVG card.

    Tries two formats:
      1. { polygons: [{ id?, segments: [{x1,y1,x2,y2}] }] }  — same as seen_polygon
      2. List of { id?, polygon: [[x,y],...] }

    Returns: [{ id, name, polygon: [[x,y],...], center: {x,y} }]
    """
    if not polygons_response or not isinstance(polygons_response, (dict, list)):
        return []

    # Build area_id → room name lookup
    areas = areas_blob.get("areas", []) if isinstance(areas_blob, dict) else []
    name_by_id: dict = {}
    for a in areas:
        meta_raw = a.get("area_meta_data", "")
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                name_by_id[a["id"]] = meta.get("name", f"Room {a['id']}")
            except Exception:
                pass

    def _axis_aligned_rect(pts: list) -> list:
        """Return the 4 corners of the axis-aligned bounding box (rectangle)."""
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return [
            [min_x, min_y],
            [max_x, min_y],
            [max_x, max_y],
            [min_x, max_y],
        ]

    def _make_room(area_id: Any, pts: list) -> dict[str, Any]:
        rect = _axis_aligned_rect(pts)
        if not rect:
            rect = pts
        xs = [p[0] for p in rect]
        ys = [p[1] for p in rect]
        return {
            "id":      area_id,
            "name":    name_by_id.get(area_id, f"Room {area_id}"),
            "polygon": rect,
            "center":  {"x": sum(xs) / len(xs), "y": sum(ys) / len(ys)},
        }

    result: list[dict[str, Any]] = []

    # Format 1: dict with "polygons" key (top-level or nested under "map")
    raw_polys = None
    if isinstance(polygons_response, dict):
        raw_polys = (
            polygons_response.get("polygons")
            or (polygons_response.get("map") or {}).get("polygons")
        )
    if raw_polys:
        for i, poly in enumerate(raw_polys):
            if not isinstance(poly, dict):
                continue
            area_id  = poly.get("id") or poly.get("area_id") or i
            segments = poly.get("segments", [])
            if segments:
                pts = []
                for s in segments:
                    if "x1" in s:
                        pts.append([s["x1"], s["y1"]])
                    if "x2" in s:
                        pts.append([s["x2"], s["y2"]])
            else:
                raw_pts = poly.get("polygon") or poly.get("points") or []
                pts = [
                    [p[0], p[1]] if isinstance(p, (list, tuple))
                    else [p.get("x", 0), p.get("y", 0)]
                    for p in raw_pts
                ]
            if pts:
                result.append(_make_room(area_id, pts))
        if result:
            return result

    # Format 2: bare list
    raw_list = polygons_response if isinstance(polygons_response, list) else []
    for i, poly in enumerate(raw_list):
        if not isinstance(poly, dict):
            continue
        area_id = poly.get("id") or poly.get("area_id") or i
        raw_pts = poly.get("polygon") or poly.get("points") or []
        pts = [
            [p[0], p[1]] if isinstance(p, (list, tuple))
            else [p.get("x", 0), p.get("y", 0)]
            for p in raw_pts
        ]
        if pts:
            result.append(_make_room(area_id, pts))

    return result


def _compute_bounds(floor_plan: list[dict[str, Any]]) -> dict[str, float] | None:
    """Compute bounding box of all polygon points."""
    all_x: list[float] = []
    all_y: list[float] = []
    for room in floor_plan:
        for pt in room.get("polygon", []):
            all_x.append(pt[0])
            all_y.append(pt[1])
    if not all_x:
        return None
    return {
        "min_x": min(all_x), "max_x": max(all_x),
        "min_y": min(all_y), "max_y": max(all_y),
    }
