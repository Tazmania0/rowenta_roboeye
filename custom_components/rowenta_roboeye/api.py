"""Async HTTP client for the Rowenta RobEye local REST API (port 8080).

All network I/O is isolated here.  Entities and the coordinator never
call HTTP directly — they go through this client only.

Discovered endpoints (from ApolloLogs / network capture):
  GET /get/status               battery, mode, charging, fan speed
  GET /get/statistics           lifetime totals (distance, time, area, runs)
  GET /get/permanent_statistics alternative lifetime stats endpoint
  GET /get/areas                room array with statistics and metadata
  GET /get/maps                 available floor-plan map list
  GET /get/map_status           active map metadata
  GET /get/robot_id             serial / unique identifier
  GET /get/wifi_status          SSID, RSSI, IP
  GET /get/protocol_version     firmware / protocol version string
  GET /get/robot_flags          capability / feature flag bitmask
  GET /get/sensor_status        cliff / bump / wheel-drop sensor health
  GET /get/sensor_values        raw ADC sensor readings
  GET /get/live_parameters      real-time position + coverage stats
  GET /get/cleaning_parameter_set  active fan-speed profile object
  GET /get/schedule             cleaning schedule configuration
  GET /get/command_result       result of last issued command
  GET /get/task_history         list of past cleaning sessions
  GET /get/event_log            robot event log entries
  GET /get/cleaning_grid_map    occupancy grid (binary map image data)
  GET /get/topo_map             topological navigation map
  GET /get/feature_map          visual feature map
  GET /get/tile_map             tile-based map representation
  GET /get/seen_polygon         already-explored polygon
  GET /get/n_n_polygons         node-to-node navigation polygons
  GET /get/points_of_interest   saved POI / charging-dock locations

  GET /set/clean_all            start whole-home clean
  GET /set/clean_map            start room-targeted clean
  GET /set/go_home              return to dock
  GET /set/stop                 stop immediately
  GET /set/switch_cleaning_parameter_set  change fan speed
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import (
    API_GET_AREAS,
    API_GET_CLEANING_GRID_MAP,
    API_GET_CLEANING_PARAMETER_SET,
    API_GET_COMMAND_RESULT,
    API_GET_EVENT_LOG,
    API_GET_FEATURE_MAP,
    API_GET_LIVE_PARAMETERS,
    API_GET_MAP_STATUS,
    API_GET_MAPS,
    API_GET_N_N_POLYGONS,
    API_DEBUG_LOCALIZATION,
    API_DEBUG_RELOCALIZATION,
    API_DEBUG_EXPLORATION,
    API_GET_PERMANENT_STATISTICS,
    API_GET_POINTS_OF_INTEREST,
    API_GET_PRODUCT_FEATURE_SET,
    API_GET_PROTOCOL_VERSION,
    API_GET_ROB_POSE,
    API_GET_ROBOT_FLAGS,
    API_GET_ROBOT_ID,
    API_GET_SCHEDULE,
    API_GET_SEEN_POLYGON,
    API_GET_SENSOR_STATUS,
    API_GET_SENSOR_VALUES,
    API_GET_STATISTICS,
    API_GET_STATUS,
    API_GET_TASK_HISTORY,
    API_GET_TILE_MAP,
    API_GET_TOPO_MAP,
    API_GET_WIFI_STATUS,
    API_SET_CLEAN_ALL,
    API_SET_CLEAN_MAP,
    API_SET_FAN_SPEED,
    API_SET_GO_HOME,
    API_SET_MODIFY_AREA,
    API_SET_STOP,
    API_SET_CLEAN_START_OR_CONTINUE,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    LOGGER,
    STRATEGY_DEFAULT,
)


class CannotConnect(Exception):
    """Raised when the API is unreachable, times out, or returns an HTTP error."""


class RobEyeApiClient:
    """Async client for the RobEye local HTTP REST API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession | None = None,
        port: int = DEFAULT_PORT,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    # ── Internal ──────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"http://{self._host}:{self._port}{path}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request and return parsed JSON.

        A fresh TCP connection is created for every request and closed immediately
        after the response is received.  This prevents HA from holding a persistent
        keep-alive connection that would block the native Rowenta app from connecting
        to the robot's embedded HTTP server.

        Raises CannotConnect on network errors, timeouts, or HTTP errors.
        """
        url = self._url(path)
        try:
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    url, params=params, timeout=self._timeout
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(
                f"Error communicating with RobEye API at {url}: {err}"
            ) from err

    # ── Core status (polled every 15 s) ───────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        """GET /get/status — battery %, mode, charging, fan-speed raw."""
        return await self._get(API_GET_STATUS)

    async def get_live_parameters(self) -> dict[str, Any]:
        """GET /get/live_parameters — real-time position and coverage counters."""
        return await self._get(API_GET_LIVE_PARAMETERS)

    async def get_rob_pose(self) -> dict[str, Any]:
        """GET /get/rob_pose — real-time robot position for all states.

        Confirmed response (at dock, 2026-03-21):
            {
              "map_id":       3,
              "x1":          -2,
              "y1":          -3,
              "heading":      157,
              "valid":        true,
              "is_tentative": false,
              "timestamp":    958459
            }

        Fields:
            x1, y1        — position in API units. 1 unit = 2 mm = 0.2 cm.
            heading       — degrees (0–360), already converted. No 65536-scale here.
            valid         — false if robot has no position fix.
            is_tentative  — true if position is a rough initial estimate.
            map_id        — which map's coordinate system this uses.
            timestamp     — monotonic uptime counter; unchanged means stale position.

        Works in ALL robot states: docked, idle, cleaning, returning home.
        Replaces: /debug/localization, /debug/relocalization, /debug/exploration.

        Discovered in Rowenta Robots APK v9.5.1-RC1 (2026-03-21).
        Confirmed live at dock: x1=-2, y1=-3, heading=157, valid=true.
        """
        return await self._get(API_GET_ROB_POSE)

    # ── Statistics (polled every 600 s) ───────────────────────────────

    async def get_statistics(self) -> dict[str, Any]:
        """GET /get/statistics — lifetime totals: distance, time, area, runs."""
        return await self._get(API_GET_STATISTICS)

    async def get_permanent_statistics(self) -> dict[str, Any]:
        """GET /get/permanent_statistics — alternative / complementary lifetime stats."""
        return await self._get(API_GET_PERMANENT_STATISTICS)

    # ── Area / map data (polled every 300 s) ─────────────────────────

    async def get_areas(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/areas[?map_id=X] — room objects with statistics and area_meta_data."""
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_AREAS, params=params)

    async def modify_area(
        self,
        map_id: str,
        area_id: str,
        cleaning_parameter_set: str | None = None,
        strategy_mode: str | None = None,
    ) -> dict[str, Any]:
        """GET /set/modify_area — write per-room fan speed and/or strategy to robot map.

        Confirmed working (2026-04-05):
          /set/modify_area?map_id=3&area_id=3&cleaning_parameter_set=3&strategy_mode=deep

        cleaning_parameter_set: 1=Normal, 2=Eco, 3=High, 4=Silent
        strategy_mode: only "normal" or "deep" accepted by firmware — anything else
                       returns a parameter error.

        Omit any parameter you do not want to change.
        Changes persist to the robot's saved map immediately.
        """
        params: dict[str, str] = {"map_id": map_id, "area_id": area_id}
        if cleaning_parameter_set is not None:
            params["cleaning_parameter_set"] = cleaning_parameter_set
        if strategy_mode is not None:
            params["strategy_mode"] = strategy_mode
        return await self._get(API_SET_MODIFY_AREA, params=params)

    async def get_maps(self) -> dict[str, Any]:
        """GET /get/maps — list of all saved floor-plan maps.

        Confirmed response (live device 2026-03-29):
        {
          "maps": [
            {
              "map_id": 3,
              "map_meta_data": "Дружба ",    ← user name; strip(); may be ""
              "permanent_flag": "true",        ← STRING not boolean
              "statistics": {
                "area_size": 0,
                "cleaning_counter": 0,
                "estimated_cleaning_time": 0,
                "average_cleaning_time": 0,
                "last_cleaned": {"year": 2001, "month": 1, "day": 1,
                                 "hour": 0, "min": 0, "sec": 0}
              }
            },
            ...
          ]
        }

        Display name rules (matches native app):
          non-empty map_meta_data.strip() → use it           e.g. "Дружба"
          empty map_meta_data             → "Map {N}"        N = 1-based position

        permanent_flag == "true" (string) marks saved floor maps.
        Non-permanent entries (temporary/live maps) must be skipped.
        year 2001 in last_cleaned is the firmware sentinel for never cleaned.
        """
        return await self._get(API_GET_MAPS)

    async def get_map_status(self) -> dict[str, Any]:
        """GET /get/map_status — active map metadata (id, name, creation time)."""
        return await self._get(API_GET_MAP_STATUS)

    # ── Robot identity (polled every 3600 s) ─────────────────────────

    async def get_robot_id(self) -> dict[str, Any]:
        """GET /get/robot_id — serial number and unique device identifier."""
        return await self._get(API_GET_ROBOT_ID)

    async def get_wifi_status(self) -> dict[str, Any]:
        """GET /get/wifi_status — SSID, RSSI, IP address."""
        return await self._get(API_GET_WIFI_STATUS)

    async def get_protocol_version(self) -> dict[str, Any]:
        """GET /get/protocol_version — firmware / API protocol version string."""
        return await self._get(API_GET_PROTOCOL_VERSION)

    # ── Diagnostics (polled every 300 s alongside areas) ─────────────

    async def get_robot_flags(self) -> dict[str, Any]:
        """GET /get/robot_flags — capability and feature-flag bitmask."""
        return await self._get(API_GET_ROBOT_FLAGS)

    async def get_sensor_status(self) -> dict[str, Any]:
        """GET /get/sensor_status — cliff, bump, and wheel-drop sensor health."""
        return await self._get(API_GET_SENSOR_STATUS)

    async def get_sensor_values(self) -> dict[str, Any]:
        """GET /get/sensor_values — raw ADC readings from all physical sensors."""
        return await self._get(API_GET_SENSOR_VALUES)

    # ── Configuration (polled every 600 s) ───────────────────────────

    async def get_cleaning_parameter_set(self) -> dict[str, Any]:
        """GET /get/cleaning_parameter_set — full fan-speed profile object."""
        return await self._get(API_GET_CLEANING_PARAMETER_SET)

    async def get_schedule(self) -> dict[str, Any]:
        """GET /get/schedule — configured cleaning schedule."""
        return await self._get(API_GET_SCHEDULE)

    # ── Operational history (polled every 600 s) ─────────────────────

    async def get_command_result(self) -> dict[str, Any]:
        """GET /get/command_result — status of the last command sent to the robot.

        Confirmed response (2026-04-05):
          {"commands": [{"cmd_id": 154, "status": "executing", "error_code": 0}]}

        IMPORTANT: "commands" is an ARRAY. Read commands[0]["status"].
        Do NOT read response["status"] — that key does not exist.

        status: "executing" | "done" | "error" | "aborted"
        cmd_id matches "id" in /get/ui_cmd_log.
        """
        return await self._get(API_GET_COMMAND_RESULT)

    async def get_task_history(self) -> dict[str, Any]:
        """GET /get/task_history — list of past cleaning sessions."""
        return await self._get(API_GET_TASK_HISTORY)

    async def get_event_log(self, last_id: int = 0) -> dict[str, Any]:
        """GET /get/event_log?last_id=N — incremental robot event log.

        Confirmed response (2026-04-05):
        {"robot_events": [
          {"id":9, "type":"action_started", "type_id":1110,
           "timestamp":{"year":2026,"month":4,"day":5,"hour":16,"min":6,"sec":0},
           "current_status":"clean_map_areas", "map_id":18, "area_id":25,
           "source_type":"user", "source_id":2, "hierarchy":1, "info":0},
          ...
        ]}

        Returns only events with id > last_id. Pass last_id=0 for all events.
        Poll incrementally: store the last seen id and pass it on next call.

        hierarchy=1 = top-level actions (clean, go_home)
        hierarchy=2 = sub-steps (localize, undocking, clean_area)
        source_type="user" = from app/HA; "operation_unit" = hardware sensor

        Event type IDs: see EVENT_TYPE_* constants in const.py
        """
        return await self._get(API_GET_EVENT_LOG, params={"last_id": last_id})

    # ── Map geometry (on-demand / low-frequency) ─────────────────────

    async def get_cleaning_grid_map(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/cleaning_grid_map[?map_id=X] — occupancy grid map data.

        Without map_id: live session grid (only during active cleaning).
        With map_id=3:  last completed session grid (persists after dock).
        """
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_CLEANING_GRID_MAP, params=params)

    async def get_topo_map(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/topo_map[?map_id=X] — topological navigation map."""
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_TOPO_MAP, params=params)

    async def get_feature_map(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/feature_map[?map_id=X] — structural wall lines + docking pose."""
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_FEATURE_MAP, params=params)

    async def get_tile_map(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/tile_map[?map_id=X] — area IDs, wall lines, outline polygon, docking pose."""
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_TILE_MAP, params=params)

    async def get_seen_polygon(self, map_id: str | None = None) -> dict[str, Any]:
        """GET /get/seen_polygon[?map_id=X] — outer boundary of explored area."""
        params = {"map_id": map_id} if map_id is not None else None
        return await self._get(API_GET_SEEN_POLYGON, params=params)

    async def get_n_n_polygons(self) -> dict[str, Any]:
        """GET /get/n_n_polygons — node-to-node navigation polygons."""
        return await self._get(API_GET_N_N_POLYGONS)

    async def get_localization(self) -> dict[str, Any]:
        """GET /debug/localization — snapshot localization (startpoint/global entries).

        Use when the robot is idle/docked. Returns entries with localization_type
        "startpoint" and "global" — these are computed at session start, not live.
        """
        return await self._get(API_DEBUG_LOCALIZATION)

    async def get_relocalization(self) -> dict[str, Any]:
        """GET /debug/relocalization — continuous position tracking during cleaning.

        Use when the robot is actively cleaning. Returns multiple entries with
        localization_type "continuous" that update every few seconds.
        The LAST entry (highest rtc_time) has the most recent rob_pose [x, y, heading_raw].
        heading_raw is in units where 65536 == 360°.
        """
        return await self._get(API_DEBUG_RELOCALIZATION)

    async def get_exploration(self) -> dict[str, Any]:
        """GET /debug/exploration — live position for new-map exploration sessions.

        Use when operation_map_id != saved_map_id (robot is building a new live map).
        Returns exploration_points list, each with rob_pose = [x, y, heading_raw].
        Use entry with highest 'ts' value for most recent position.
        Coordinates are in the LIVE MAP's own coordinate system (not map 3).
        Confirmed from live map 57 session 2026-03-20.
        """
        return await self._get(API_DEBUG_EXPLORATION)

    async def get_points_of_interest(self) -> dict[str, Any]:
        """GET /get/points_of_interest — saved POI and charging-dock locations."""
        return await self._get(API_GET_POINTS_OF_INTEREST)

    async def get_rooms(self) -> dict[str, Any]:
        """GET /get/rooms — returns unknown_request on Xplorer 120 firmware.

        Confirmed dead on live device (2026-03-30). Do not call in polling loops.
        Use /get/areas (get_areas) as the sole room-data source.
        """
        raise NotImplementedError("/get/rooms is unsupported on Xplorer 120 firmware")

    async def get_product_feature_set(self) -> dict[str, Any]:
        """GET /get/product_feature_set — device-level capability flags.

        Discovered in APK v9.5.1-RC1. Expected to expose deep_clean availability
        and other per-device features. Format unconfirmed.
        """
        return await self._get(API_GET_PRODUCT_FEATURE_SET)

    # ── Commands ──────────────────────────────────────────────────────

    async def clean_all(
        self,
        cleaning_parameter_set: str,
        strategy_mode: str = STRATEGY_DEFAULT,
    ) -> dict[str, Any]:
        """Start a full-home clean at the specified fan speed.

        Returns dict with cmd_id on success or error_code on failure.

        Args:
            cleaning_parameter_set: Fan-speed API value "1"–"4".
            strategy_mode: One of the STRATEGY_* constants (default "4" = robot decides).
                           "1"=Normal, "2"=Walls & Corners, "3"=Deep, "4"=Default.
        """
        return await self._get(
            API_SET_CLEAN_ALL,
            params={
                "cleaning_parameter_set": cleaning_parameter_set,
                "cleaning_strategy_mode": strategy_mode,
            },
        )

    async def clean_start_or_continue(self) -> dict[str, Any]:
        """GET /set/clean_start_or_continue — resume interrupted clean or recover from error.

        Confirmed from RobEye web UI log (live device, 2026-03-29):
          stop → clean_start_or_continue → {"cmd_id":68,"status":"executing"}

        Resumes from current position. Does NOT reset to dock (unlike clean_all).
        No parameters — robot uses its existing task context.

        Recovery by error type:
          brush stuck     → firmware accepts, cleaning resumes
          dustbin missing → firmware rejects, error persists (correct)
          not_ready       → firmware decides

        /set/clean_continue is deprecated (error 106) — never use it.
        """
        return await self._get(API_SET_CLEAN_START_OR_CONTINUE)

    async def clean_map(
        self,
        map_id: str,
        area_ids: str,
        cleaning_parameter_set: str,
        strategy_mode: str = STRATEGY_DEFAULT,
    ) -> dict[str, Any]:
        """Start a room-targeted clean.

        Returns dict with cmd_id on success or error_code on failure.

        Args:
            map_id: Map identifier (e.g. "3").
            area_ids: Comma-separated area IDs (e.g. "2,11").
            cleaning_parameter_set: Fan-speed API value "1"–"4".
            strategy_mode: One of the STRATEGY_* constants (default "4" = robot decides).
        """
        return await self._get(
            API_SET_CLEAN_MAP,
            params={
                "map_id": map_id,
                "area_ids": area_ids,
                "cleaning_parameter_set": cleaning_parameter_set,
                "cleaning_strategy_mode": strategy_mode,
            },
        )

    async def go_home(self) -> dict[str, Any]:
        """Return the vacuum to its dock."""
        return await self._get(API_SET_GO_HOME)

    async def stop(self) -> dict[str, Any]:
        """Stop the vacuum immediately."""
        return await self._get(API_SET_STOP)

    async def set_fan_speed(self, cleaning_parameter_set: str) -> dict[str, Any]:
        """Change fan speed without starting a new clean."""
        return await self._get(
            API_SET_FAN_SPEED,
            params={"cleaning_parameter_set": cleaning_parameter_set},
        )

    # ── Config-flow connection test ───────────────────────────────────

    async def test_connection(self) -> bool:
        """Return True if the API responds to /get/status; raise CannotConnect otherwise."""
        await self.get_status()
        return True
