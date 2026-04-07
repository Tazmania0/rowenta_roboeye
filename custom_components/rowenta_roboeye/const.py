"""Constants for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "rowenta_roboeye"
VERSION = "1.0.0"
LOGGER = logging.getLogger(__package__)

PLATFORMS: list[Platform] = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
]

# ── Coordinator timing ────────────────────────────────────────────────
UPDATE_INTERVAL_CLEANING = timedelta(seconds=5)   # rob_pose + status during cleaning
UPDATE_INTERVAL_IDLE     = timedelta(seconds=15)  # status only when idle

# Keep UPDATE_INTERVAL as an alias for the idle interval (used by coordinator init)
UPDATE_INTERVAL = UPDATE_INTERVAL_IDLE

SCAN_INTERVAL_ROB_POSE   = 5     # s — /get/rob_pose (cleaning only; idle uses 15 s)
SCAN_INTERVAL_STATUS     = 5     # s — /get/status
SCAN_INTERVAL_STATISTICS = 600
SCAN_INTERVAL_AREAS = 300
SCAN_INTERVAL_ROBOT_INFO = 3600

# Command result polling — used by _wait_for_robot_idle after each queued command
CMD_POLL_INTERVAL_S = 5.0    # seconds between /get/command_result polls
CMD_POLL_TIMEOUT_S  = 30.0   # max wait per command before moving to next
QUEUE_POST_DOCK_DELAY_S = 8.0  # short settle delay after dock before next queued clean

# ── Config entry keys ─────────────────────────────────────────────────
CONF_MAP_ID = "map_id"
CONF_HOSTNAME = "hostname"
CONF_NAME = "name"
DEFAULT_DEVICE_NAME = "Rowenta Xplorer 120"

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
API_GET_ROB_POSE = "/get/rob_pose"
# API_GET_ROOMS = "/get/rooms"  # returns unknown_request on Xplorer 120 firmware — do not call
API_GET_PRODUCT_FEATURE_SET = "/get/product_feature_set"
API_GET_SAFETY_MCU_FIRMWARE = "/get/safety_mcu_firmware_version"
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
API_SET_CLEAN_START_OR_CONTINUE = "/set/clean_start_or_continue"
API_SET_FAN_SPEED = "/set/switch_cleaning_parameter_set"
API_SET_MODIFY_AREA = "/set/modify_area"  # write per-room fan speed / strategy to robot map

# ── HA service names ──────────────────────────────────────────────────
SERVICE_CLEAN_ROOM = "clean_room"
SERVICE_REMOVE_QUEUE_ENTRY = "remove_queue_entry"

# ── cleaning_strategy_mode values ────────────────────────────────────
# Values confirmed from RobEye web UI HTML source (option tags).
STRATEGY_DEFAULT       = "4"   # robot chooses strategy automatically
STRATEGY_NORMAL        = "1"
STRATEGY_WALLS_CORNERS = "2"
STRATEGY_DEEP          = "3"   # double/triple pass

# Human-readable labels for the strategy select entity
STRATEGY_LABELS: dict[str, str] = {
    STRATEGY_DEFAULT:       "Default",
    STRATEGY_NORMAL:        "Normal",
    STRATEGY_WALLS_CORNERS: "Walls & Corners",
    STRATEGY_DEEP:          "Deep",
}
STRATEGY_OPTIONS: list[str] = [
    STRATEGY_LABELS[STRATEGY_DEFAULT],
    STRATEGY_LABELS[STRATEGY_NORMAL],
    STRATEGY_LABELS[STRATEGY_WALLS_CORNERS],
]
# Reverse map: label → API value
STRATEGY_REVERSE_MAP: dict[str, str] = {v: k for k, v in STRATEGY_LABELS.items()}

# ── API mode strings ──────────────────────────────────────────────────
MODE_CLEANING = "cleaning"
MODE_READY = "ready"
MODE_GO_HOME = "go_home"
MODE_NOT_READY = "not_ready"

# ── API charging strings ──────────────────────────────────────────────
CHARGING_CHARGING = "charging"
CHARGING_CONNECTED = "connected"
CHARGING_UNCONNECTED = "unconnected"

# ── Area type / state constants ───────────────────────────────────────
AREA_TYPE_ROOM      = "room"
AREA_TYPE_AVOIDANCE = "to_be_cleaned"
AREA_STATE_CLEAN    = "clean"
AREA_STATE_INACTIVE = "inactive"
AREA_STATE_BLOCKING = "blocking"

# ── Fan speed mapping  API value (str) -> human label ─────────────────
FAN_SPEED_MAP: dict[str, str] = {
    "1": "normal",
    "2": "eco",
    "3": "high",
    "4": "silent",
}
FAN_SPEED_REVERSE_MAP: dict[str, str] = {v: k for k, v in FAN_SPEED_MAP.items()}
FAN_SPEEDS: list[str] = list(FAN_SPEED_MAP.values())

# ── Coordinator data keys ─────────────────────────────────────────────
DATA_SENSOR_VALUES = "sensor_values"
DATA_STATUS = "status"
DATA_STATISTICS = "statistics"
DATA_PERMANENT_STATISTICS = "permanent_statistics"
DATA_AREAS = "areas"
DATA_ROBOT_INFO = "robot_info"
DATA_LIVE_PARAMETERS = "live_parameters"
DATA_SENSOR_STATUS = "sensor_status"
DATA_ROBOT_FLAGS = "robot_flags"
DATA_ROB_POSE = "rob_pose"
DATA_ROOMS = "rooms"
DATA_SEEN_POLYGON = "seen_polygon"
DATA_LIVE_MAP = "live_map"
DATA_CLEANING_GRID = "cleaning_grid_map"
DATA_FEATURE_MAP = "feature_map"
DATA_TILE_MAP = "tile_map"
DATA_TOPO_MAP = "topo_map"
DATA_AREAS_SAVED_MAP = "areas_saved_map"
DATA_SEEN_POLY_SAVED_MAP = "seen_poly_saved_map"
DATA_MAP_STATUS = "map_status"
DATA_MAPS = "maps"                       # /get/maps full response
DATA_ACTIVE_MAP_ID = "active_map_id"     # resolved from /get/map_status
DATA_EXPLORATION = "exploration"
DATA_RELOCALIZATION = "relocalization"
DATA_SCHEDULE = "schedule"

# ── Schedule ──────────────────────────────────────────────────────────
CLEANING_MODE_ALL   = 1   # clean_all (whole home)
CLEANING_MODE_ROOMS = 2   # clean_map (specific rooms)

SCHEDULE_DAYS: dict[int, str] = {
    1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
    5: "Fri", 6: "Sat", 7: "Sun",
}
SCHEDULE_DAYS_FULL: dict[int, str] = {
    1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
    5: "Friday",  6: "Saturday", 7: "Sunday",
}
# 0 = per-room default (each room uses its own cleaning_parameter_set)
FAN_SPEED_LABELS: dict[int, str] = {
    0: "default",
    1: "normal",
    2: "eco",
    3: "high",
    4: "silent",
}

# ── Saved map ID ──────────────────────────────────────────────────────
SAVED_MAP_ID = "3"  # permanent saved map; all rooms are on this map

# ── Map-geometry refresh interval ─────────────────────────────────────
SCAN_INTERVAL_MAP_GEOMETRY = 600

# ── Event log polling ─────────────────────────────────────────────────
SCAN_INTERVAL_EVENT_LOG = 30   # seconds between /get/event_log polls
DATA_EVENT_LOG = "event_log"

# ── Event type IDs confirmed 2026-04-05 ───────────────────────────────
EVENT_TYPE_CLEAN_AREA_STARTED    = 1010
EVENT_TYPE_GO_HOME_STARTED       = 1030
EVENT_TYPE_GO_HOME_SUCCEEDED     = 1031
EVENT_TYPE_GO_HOME_INTERRUPTED   = 1032
EVENT_TYPE_LOCALIZE_STARTED      = 1050
EVENT_TYPE_LOCALIZE_SUCCEEDED    = 1051
EVENT_TYPE_LOCALIZE_INTERRUPTED  = 1052
EVENT_TYPE_CLEAN_MAP_STARTED     = 1110
EVENT_TYPE_CLEAN_MAP_INTERRUPTED = 1112
EVENT_TYPE_UNDOCKING_STARTED     = 1140
EVENT_ROBOT_LIFTED               = 2010
EVENT_ROBOT_SETBACK              = 2011
EVENT_DUSTBIN_MISSING            = 2030
EVENT_DUSTBIN_INSERTED           = 2031

# Human-readable labels for HA logbook and sensor display
EVENT_TYPE_LABELS: dict[int, str] = {
    1010: "Started cleaning room",
    1030: "Returning to dock",
    1031: "Docked",
    1032: "Docking interrupted",
    1050: "Localizing",
    1051: "Localized",
    1052: "Localization failed",
    1110: "Room clean started",
    1112: "Room clean interrupted",
    1140: "Undocking",
    2010: "Robot lifted",
    2011: "Robot set back down",
    2030: "Dustbin removed",
    2031: "Dustbin inserted",
}

# ── Resilience ────────────────────────────────────────────────────────
MAX_POLL_FAILURES = 3

# ── Dynamic entity discovery signal ──────────────────────────────────
SIGNAL_AREAS_UPDATED = f"{DOMAIN}_areas_updated"
