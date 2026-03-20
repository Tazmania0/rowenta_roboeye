"""Constants for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "rowenta_roboeye"
LOGGER = logging.getLogger(__package__)

PLATFORMS: list[Platform] = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
]

# ── Coordinator timing ────────────────────────────────────────────────
UPDATE_INTERVAL = timedelta(seconds=15)
SCAN_INTERVAL_STATISTICS = 600
SCAN_INTERVAL_AREAS = 300
SCAN_INTERVAL_ROBOT_INFO = 3600

# ── Config entry keys ─────────────────────────────────────────────────
CONF_MAP_ID = "map_id"
CONF_HOSTNAME = "hostname"

# ── API transport ─────────────────────────────────────────────────────
DEFAULT_PORT = 8080
DEFAULT_MAP_ID = "3"
DEFAULT_TIMEOUT = 10

# ── GET endpoints ─────────────────────────────────────────────────────
API_GET_STATUS = "/get/status"
API_GET_STATISTICS = "/get/statistics"
API_GET_PERMANENT_STATISTICS = "/get/permanent_statistics"
API_GET_AREAS = "/get/areas"
API_GET_MAPS = "/get/maps"
API_GET_MAP_STATUS = "/get/map_status"
API_GET_ROBOT_ID = "/get/robot_id"
API_GET_WIFI_STATUS = "/get/wifi_status"
API_GET_PROTOCOL_VERSION = "/get/protocol_version"
API_GET_ROBOT_FLAGS = "/get/robot_flags"
API_GET_SENSOR_STATUS = "/get/sensor_status"
API_GET_SENSOR_VALUES = "/get/sensor_values"
API_GET_LIVE_PARAMETERS = "/get/live_parameters"
API_GET_CLEANING_PARAMETER_SET = "/get/cleaning_parameter_set"
API_GET_SCHEDULE = "/get/schedule"
API_GET_COMMAND_RESULT = "/get/command_result"
API_GET_TASK_HISTORY = "/get/task_history"
API_GET_EVENT_LOG = "/get/event_log"
API_GET_CLEANING_GRID_MAP = "/get/cleaning_grid_map"
API_GET_TOPO_MAP = "/get/topo_map"
API_GET_FEATURE_MAP = "/get/feature_map"
API_GET_TILE_MAP = "/get/tile_map"
API_GET_SEEN_POLYGON = "/get/seen_polygon"
API_GET_N_N_POLYGONS = "/get/n_n_polygons"
API_GET_POINTS_OF_INTEREST = "/get/points_of_interest"
API_DEBUG_LOCALIZATION = "/debug/localization"
API_DEBUG_RELOCALIZATION = "/debug/relocalization"
API_DEBUG_EXPLORATION = "/debug/exploration"
API_DEBUG_SMSC = "/debug/smsc"

# ── SET / command endpoints ───────────────────────────────────────────
API_SET_CLEAN_ALL = "/set/clean_all"
API_SET_CLEAN_MAP = "/set/clean_map"
API_SET_GO_HOME = "/set/go_home"
API_SET_STOP = "/set/stop"
API_SET_FAN_SPEED = "/set/switch_cleaning_parameter_set"

# ── HA service names ──────────────────────────────────────────────────
SERVICE_CLEAN_ROOM = "clean_room"

# ── cleaning_strategy_mode values ────────────────────────────────────
STRATEGY_NORMAL = "1"
STRATEGY_DEEP = "2"

# ── API mode strings ──────────────────────────────────────────────────
MODE_CLEANING = "cleaning"
MODE_READY = "ready"
MODE_GO_HOME = "go_home"

# ── API charging strings ──────────────────────────────────────────────
CHARGING_CHARGING = "charging"
CHARGING_CONNECTED = "connected"
CHARGING_UNCONNECTED = "unconnected"

# ── Fan speed mapping  API value (str) -> human label ─────────────────
FAN_SPEED_MAP: dict[str, str] = {
    "1": "eco",
    "2": "normal",
    "3": "high",
    "4": "silent",
}
FAN_SPEED_REVERSE_MAP: dict[str, str] = {v: k for k, v in FAN_SPEED_MAP.items()}
FAN_SPEEDS: list[str] = list(FAN_SPEED_MAP.values())

# ── Coordinator data keys ─────────────────────────────────────────────
DATA_STATUS = "status"
DATA_STATISTICS = "statistics"
DATA_PERMANENT_STATISTICS = "permanent_statistics"
DATA_AREAS = "areas"
DATA_ROBOT_INFO = "robot_info"
DATA_LIVE_PARAMETERS = "live_parameters"
DATA_SENSOR_STATUS = "sensor_status"
DATA_ROBOT_FLAGS = "robot_flags"
DATA_SEEN_POLYGON = "seen_polygon"
DATA_LIVE_MAP = "live_map"
DATA_CLEANING_GRID = "cleaning_grid_map"
DATA_FEATURE_MAP = "feature_map"
DATA_TILE_MAP = "tile_map"
DATA_TOPO_MAP = "topo_map"
DATA_AREAS_SAVED_MAP = "areas_saved_map"
DATA_SEEN_POLY_SAVED_MAP = "seen_poly_saved_map"
DATA_MAP_STATUS = "map_status"
DATA_EXPLORATION = "exploration"
DATA_RELOCALIZATION = "relocalization"

# ── Saved map ID ──────────────────────────────────────────────────────
SAVED_MAP_ID = "3"  # permanent saved map; all rooms are on this map

# ── Map-geometry refresh interval ─────────────────────────────────────
SCAN_INTERVAL_MAP_GEOMETRY = 600

# ── Resilience ────────────────────────────────────────────────────────
MAX_POLL_FAILURES = 3

# ── Dynamic entity discovery signal ──────────────────────────────────
SIGNAL_AREAS_UPDATED = f"{DOMAIN}_areas_updated"
