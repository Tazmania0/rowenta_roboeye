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
    API_GET_PERMANENT_STATISTICS,
    API_GET_POINTS_OF_INTEREST,
    API_GET_PROTOCOL_VERSION,
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
    API_SET_STOP,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    LOGGER,
)


class CannotConnect(Exception):
    """Raised when the API is unreachable, times out, or returns an HTTP error."""


class RobEyeApiClient:
    """Async client for the RobEye local HTTP REST API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_PORT,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._session = session
        self._port = port
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    # ── Internal ──────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"http://{self._host}:{self._port}{path}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request and return parsed JSON.

        Raises CannotConnect on network errors, timeouts, or HTTP errors.
        """
        url = self._url(path)
        try:
            async with self._session.get(
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

    async def get_maps(self) -> dict[str, Any]:
        """GET /get/maps — list of available floor-plan maps."""
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
        """GET /get/command_result — outcome of the last issued command."""
        return await self._get(API_GET_COMMAND_RESULT)

    async def get_task_history(self) -> dict[str, Any]:
        """GET /get/task_history — list of past cleaning sessions."""
        return await self._get(API_GET_TASK_HISTORY)

    async def get_event_log(self) -> dict[str, Any]:
        """GET /get/event_log — robot event log (errors, state changes)."""
        return await self._get(API_GET_EVENT_LOG)

    # ── Map geometry (on-demand / low-frequency) ─────────────────────

    async def get_cleaning_grid_map(self) -> dict[str, Any]:
        """GET /get/cleaning_grid_map — binary occupancy grid map data."""
        return await self._get(API_GET_CLEANING_GRID_MAP)

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
        """GET /debug/localization — real-time robot x/y position and heading."""
        return await self._get(API_DEBUG_LOCALIZATION)

    async def get_points_of_interest(self) -> dict[str, Any]:
        """GET /get/points_of_interest — saved POI and charging-dock locations."""
        return await self._get(API_GET_POINTS_OF_INTEREST)

    # ── Commands ──────────────────────────────────────────────────────

    async def clean_all(
        self, cleaning_parameter_set: str, deep_clean: bool = False
    ) -> None:
        """Start a full-home clean at the specified fan speed.

        Args:
            cleaning_parameter_set: Fan-speed API value "1"–"4".
            deep_clean: True = cleaning_strategy_mode=2 (deep/double pass),
                        False = cleaning_strategy_mode=1 (normal).
        """
        await self._get(
            API_SET_CLEAN_ALL,
            params={
                "cleaning_parameter_set": cleaning_parameter_set,
                "cleaning_strategy_mode": "2" if deep_clean else "1",
            },
        )

    async def clean_map(
        self,
        map_id: str,
        area_ids: str,
        cleaning_parameter_set: str,
        deep_clean: bool = False,
    ) -> None:
        """Start a room-targeted clean.

        Args:
            map_id: Map identifier (e.g. "3").
            area_ids: Comma-separated area IDs (e.g. "2,11").
            cleaning_parameter_set: Fan-speed API value "1"–"4".
            deep_clean: True = cleaning_strategy_mode=2 (deep/double pass).
        """
        await self._get(
            API_SET_CLEAN_MAP,
            params={
                "map_id": map_id,
                "area_ids": area_ids,
                "cleaning_parameter_set": cleaning_parameter_set,
                "cleaning_strategy_mode": "2" if deep_clean else "1",
            },
        )

    async def go_home(self) -> None:
        """Return the vacuum to its dock."""
        await self._get(API_SET_GO_HOME)

    async def stop(self) -> None:
        """Stop the vacuum immediately."""
        await self._get(API_SET_STOP)

    async def set_fan_speed(self, cleaning_parameter_set: str) -> None:
        """Change fan speed without starting a new clean."""
        await self._get(
            API_SET_FAN_SPEED,
            params={"cleaning_parameter_set": cleaning_parameter_set},
        )

    # ── Config-flow connection test ───────────────────────────────────

    async def test_connection(self) -> bool:
        """Return True if the API responds to /get/status; raise CannotConnect otherwise."""
        await self.get_status()
        return True
