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
import base64
import json
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
    API_GET_PUMP_VOLUME_SETTINGS,
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
    API_GET_UXD,
    API_GET_WIFI_STATUS,
    API_SET_ADD_AREA,
    API_SET_CLEAN_ALL,
    API_SET_CLEAN_MAP,
    API_SET_CONFIRM_NOGO_AREAS,
    API_SET_DELETE_MAP,
    API_SET_EXPLORE,
    API_SET_FAN_SPEED,
    API_SET_GO_HOME,
    API_SET_LIVE_PARAMETERS,
    API_SET_MERGE_AREAS,
    API_SET_MODIFY_AREA,
    API_SET_MODIFY_MAP,
    API_SET_MODIFY_SCHEDULED_TASK,
    API_SET_PROPOSE_NOGO_AREAS,
    API_SET_PUMP_VOLUME_SETTINGS,
    API_SET_SAVE_MAP,
    API_SET_SPLIT_AREA,
    API_SET_STOP,
    API_SET_CLEAN_START_OR_CONTINUE,
    DEFAULT_AUTH_USERNAME,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    LOGGER,
    STRATEGY_DEFAULT,
)


class CannotConnect(Exception):
    """Raised when the API is unreachable, times out, or returns an HTTP error."""


class AuthFailed(Exception):
    """Raised when the robot rejects a request with HTTP 401 (lock_http enabled).

    Deliberately NOT a subclass of CannotConnect so that the coordinator's
    optional-endpoint ``except CannotConnect`` guards do not swallow it — it
    propagates to the top-level handler which converts it into
    ConfigEntryAuthFailed to trigger a Home Assistant re-auth notification.
    """


class RobEyeApiClient:
    """Async client for the RobEye local HTTP REST API."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: int = DEFAULT_TIMEOUT,
        http_password: str = "",
        auth_username: str = DEFAULT_AUTH_USERNAME,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        # Optional HTTP Basic Auth (AICU models with lock_http enabled).
        # Empty password ⇒ no Authorization header is ever sent (the common case).
        self._http_password = (http_password or "").strip()
        self._auth_username = auth_username or DEFAULT_AUTH_USERNAME
        self._auth_header: dict[str, str] = self._build_auth_header()

    # ── HTTP auth ─────────────────────────────────────────────────────

    def _build_auth_header(self) -> dict[str, str]:
        """Return the Basic-Auth header dict, or {} when no password is set."""
        if not self._http_password:
            return {}
        creds = base64.b64encode(
            f"{self._auth_username}:{self._http_password}".encode()
        ).decode()
        return {"Authorization": f"Basic {creds}"}

    def set_auth_username(self, username: str | None) -> None:
        """Update the Basic-Auth username (the robot's reported name).

        No-op when no password is configured or the username is unchanged.
        The robot's name is only known after the first successful /get/robot_id,
        so the client bootstraps with DEFAULT_AUTH_USERNAME and is refined here.
        """
        if not self._http_password or not username:
            return
        if username != self._auth_username:
            self._auth_username = username
            self._auth_header = self._build_auth_header()

    # ── Internal ──────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"http://{self._host}:{self._port}{path}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request and return parsed JSON.

        A fresh TCP connection is created for every request and closed immediately
        after the response is received.  This prevents HA from holding a persistent
        keep-alive connection that would block the native Rowenta app from connecting
        to the robot's embedded HTTP server.

        When an HTTP password is configured (AICU lock_http), an Authorization
        header is attached and a 401 response raises AuthFailed.

        Raises CannotConnect on network errors, timeouts, or HTTP errors.
        """
        url = self._url(path)
        try:
            connector = aiohttp.TCPConnector(force_close=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                    headers=self._auth_header or None,
                ) as resp:
                    if resp.status == 401:
                        raise AuthFailed(
                            "Robot requires an HTTP password (lock_http is enabled). "
                            "Enter the 8-character code from the QR sticker on your "
                            "robot in the integration options."
                        )
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

    # ── Mopping / wet clean (AICU Helios/L6/L7 only) ──────────────────
    # All wire formats from APK DEX analysis; unconfirmed on live hardware.
    # Callers must gate these behind coordinator.has_wet_support.

    async def get_pump_volume_settings(self) -> dict[str, Any]:
        """GET /get/pump_volume_settings — current water-flow mode.

        Expected: {"mode": "none"|"low"|"medium"|"high"|"auto"}
        """
        return await self._get(API_GET_PUMP_VOLUME_SETTINGS)

    async def set_pump_volume(self, mode: str) -> dict[str, Any]:
        """GET /set/pump_volume_settings?mode=... — set water-flow level.

        Settings write — bypasses the serial command queue (see
        IMMEDIATE_COMMAND_NAMES). mode ∈ {none, low, medium, high, auto}.
        """
        return await self._get(API_SET_PUMP_VOLUME_SETTINGS, params={"mode": mode})

    async def set_wet_clean(self, enabled: bool) -> dict[str, Any]:
        """GET /set/live_parameters?do_wet_clean=true|false — toggle wet mopping.

        Live parameter write — bypasses the serial command queue.
        """
        return await self._get(
            API_SET_LIVE_PARAMETERS,
            params={"do_wet_clean": "true" if enabled else "false"},
        )

    async def get_uxd(self) -> dict[str, Any]:
        """GET /get/uxd — live UX data; carries do_wet_clean (shape unconfirmed)."""
        return await self._get(API_GET_UXD)

    # ── Map editor (confirmed wire formats; not yet surfaced as entities) ──
    # area_meta_data is ALWAYS a JSON string, even when empty: '{"name":""}'.
    # Polygon points are individual flat params x1..x4 / y1..y4 — never JSON.

    async def add_area(
        self,
        map_id: str,
        points: list[tuple[float, float]],
        *,
        area_type: str = "to_be_cleaned",
        area_state: str = "blocking",
        name: str = "",
        cleaning_parameter_set: int = 0,
        strategy_mode: str = "normal",
        floor_type: str = "none",
        room_type: str = "none",
    ) -> dict[str, Any]:
        """GET /set/add_area — add a blocking zone or spot area to a saved map.

        ``points`` must be exactly four (x, y) corners. They are sent as flat
        params x1,y1 … x4,y4. A blocking zone uses area_state="blocking"; a spot
        area uses area_state="clean" with cleaning_parameter_set=1.
        """
        if len(points) != 4:
            raise ValueError("add_area requires exactly 4 polygon points")
        params: dict[str, Any] = {
            "map_id": map_id,
            "area_type": area_type,
            "area_state": area_state,
            "area_meta_data": json.dumps({"name": name}, separators=(",", ":")),
            "cleaning_parameter_set": cleaning_parameter_set,
            "floor_type": floor_type,
            "room_type": room_type,
            "strategy_mode": strategy_mode,
        }
        for idx, (x, y) in enumerate(points, start=1):
            params[f"x{idx}"] = x
            params[f"y{idx}"] = y
        return await self._get(API_SET_ADD_AREA, params=params)

    async def merge_areas(self, map_id: str, area_id1: str, area_id2: str) -> dict[str, Any]:
        """GET /set/merge_areas?map_id=N&area_id1=A&area_id2=B — merge two rooms."""
        return await self._get(
            API_SET_MERGE_AREAS,
            params={"map_id": map_id, "area_id1": area_id1, "area_id2": area_id2},
        )

    async def split_area(
        self,
        map_id: str,
        area_id: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> dict[str, Any]:
        """GET /set/split_area — split a room along the given boundary line."""
        return await self._get(
            API_SET_SPLIT_AREA,
            params={
                "map_id": map_id,
                "area_id": area_id,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
            },
        )

    async def save_map(self, map_id: str) -> dict[str, Any]:
        """GET /set/save_map?map_id=N — persist an explored map (poll ≤60 s)."""
        return await self._get(API_SET_SAVE_MAP, params={"map_id": map_id})

    async def delete_map(self, map_id: str) -> dict[str, Any]:
        """GET /set/delete_map?map_id=N — delete a saved floor map."""
        return await self._get(API_SET_DELETE_MAP, params={"map_id": map_id})

    async def modify_map(
        self, map_id: str, name: str, docking_pose: Any
    ) -> dict[str, Any]:
        """GET /set/modify_map — rename a map.

        docking_pose MUST always be included; omitting it resets the dock to the
        map origin. Pass the existing docking_pose JSON (string or object).
        """
        pose = docking_pose if isinstance(docking_pose, str) else json.dumps(
            docking_pose, separators=(",", ":")
        )
        return await self._get(
            API_SET_MODIFY_MAP,
            params={"map_id": map_id, "name": name, "docking_pose": pose},
        )

    async def explore(self) -> dict[str, Any]:
        """GET /set/explore — start a new-map exploration. Takes zero params."""
        return await self._get(API_SET_EXPLORE)

    async def propose_nogo_areas(self, map_id: str) -> dict[str, Any]:
        """GET /set/propose_nogo_areas?map_id=N — ask the robot to propose no-go zones."""
        return await self._get(API_SET_PROPOSE_NOGO_AREAS, params={"map_id": map_id})

    async def confirm_nogo_areas(
        self,
        map_id: str,
        confirmed_ids: list[str] | None = None,
        declined_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """GET /set/confirm_nogo_areas — accept/decline proposed no-go zones."""
        return await self._get(
            API_SET_CONFIRM_NOGO_AREAS,
            params={
                "map_id": map_id,
                "confirmed_ids": json.dumps(confirmed_ids or [], separators=(",", ":")),
                "declined_ids": json.dumps(declined_ids or [], separators=(",", ":")),
            },
        )

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

    async def clean_start_or_continue(
        self,
        cleaning_parameter_set: str | None = None,
    ) -> dict[str, Any]:
        """GET /set/clean_start_or_continue — resume interrupted clean or recover from error.

        Confirmed from RobEye web UI log (live device, 2026-03-29):
          stop → clean_start_or_continue → {"cmd_id":68,"status":"executing"}

        Confirmed (2026-04-07): returns a NEW cmd_id distinct from the original job's
        cmd_id (which transitions to 'aborted' on stop). The resumed session runs under
        the new cmd_id.

        Resumes from current position. Does NOT reset to dock (unlike clean_all).
        Pass cleaning_parameter_set to preserve fan speed through pause/resume.

        Recovery by error type:
          brush stuck     → firmware accepts, cleaning resumes
          dustbin missing → firmware rejects, error persists (correct)
          not_ready       → firmware decides

        /set/clean_continue is deprecated (error 106) — never use it.
        """
        params: dict[str, str] = {}
        if cleaning_parameter_set is not None:
            params["cleaning_parameter_set"] = cleaning_parameter_set
        return await self._get(API_SET_CLEAN_START_OR_CONTINUE, params=params or None)

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

    async def set_schedule_enabled(self, task_id: int, enabled: bool) -> dict[str, Any]:
        """Enable or disable a schedule task.

        Confirmed 2026-04-16: GET /set/modify_scheduled_task?task_id=2&enabled=1 → {"cmd_id":215}
        IMPORTANT: Do NOT route through coordinator.async_send_command() — call directly,
        then async_request_refresh(). Settings write, not a motion command.
        """
        return await self._get(
            API_SET_MODIFY_SCHEDULED_TASK,
            params={"task_id": task_id, "enabled": int(enabled)},
        )

    # ── Config-flow connection test ───────────────────────────────────

    async def test_connection(self) -> bool:
        """Return True if the API responds to /get/status; raise CannotConnect otherwise."""
        await self.get_status()
        return True
