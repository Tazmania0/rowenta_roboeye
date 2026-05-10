"""Lovelace dashboard management for the Rowenta Xplorer 120 integration.

Implementation mirrors homeassistant/components/lovelace/__init__.py
_create_map_dashboard() — the canonical HA pattern for creating a
programmatic storage-mode dashboard from a custom integration.

Key facts learned from HA source (lovelace/__init__.py, current dev):

  hass.data[LOVELACE_DATA]          — LovelaceData dataclass
    .dashboards                     — dict[str|None, LovelaceConfig]
    (LOVELACE_DATA = "lovelace")    — the actual string key

  DashboardsCollection(hass)        — loads stored dashboard registry
  dashboards_collection.async_create_item({
    CONF_ALLOW_SINGLE_WORD: True,   — needed if url_path has no hyphen
    CONF_ICON: ...,
    CONF_TITLE: ...,
    CONF_URL_PATH: ...,             — must be unique; raises if duplicate
  })

  After creation, hass.data[LOVELACE_DATA].dashboards[url_path]
  is a LovelaceStorage object.

  lovelace_store.async_save(config) — writes config in-memory + to disk.
                                      Browser WebSocket gets new config
                                      immediately. No reload needed.

Change-detection:
  _last_hash tracks SHA-256 of the last-saved config so async_save()
  is only called when something actually changed — prevents spurious
  update-notifications in the UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CLEANING_MODE_ALL, DOMAIN, SCHEDULE_DAYS, room_selection_entity_id

_LOGGER = logging.getLogger(__name__)

DASHBOARD_TITLE    = "Rowenta Xplorer 120"
DASHBOARD_URL_PATH = "rowenta-xplorer120"   # contains hyphen — valid without ALLOW_SINGLE_WORD
DASHBOARD_ICON     = "mdi:robot-vacuum"
_DEV               = "rowenta_xplorer_120"

# HA lovelace constants (avoid importing from lovelace to stay compatible)
_LOVELACE_DATA_KEY = "lovelace"          # hass.data key for LovelaceData
_CONF_URL_PATH     = "url_path"
_CONF_TITLE        = "title"
_CONF_ICON         = "icon"
_CONF_REQUIRE_ADMIN    = "require_admin"
_CONF_SHOW_IN_SIDEBAR  = "show_in_sidebar"
_CONF_ALLOW_SINGLE_WORD = "allow_single_word"

_NO_MAP_GUIDANCE = (
    "## No Floor Map Found\n\n"
    "The robot has no saved floor map yet.\n\n"
    "**To create your floor map:**\n"
    "1. Open the **Rowenta RobEye** app on your smartphone\n"
    "2. Start a full **Explore** (room scan) from the app\n"
    "3. Allow the robot to complete the exploration and save the map\n\n"
    "Once the exploration is complete, this dashboard will update automatically "
    "(within 5 minutes). No reload is needed.\n\n"
    "Room-based cleaning, live map tracking, and map visualisation will be "
    "available once a floor map exists."
)


def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _room_entities_registered(
    hass: HomeAssistant,
    device_id: str,
    active_map_id: str,
    rooms: list[dict[str, Any]],
) -> bool:
    """Return True when every per-room entity referenced by the dashboard has
    been registered in hass.states (entity ID is known to HA).

    The guard prevents saving a dashboard that references entity IDs that do
    not yet exist — e.g. right after a map switch when SIGNAL_AREAS_UPDATED
    has fired but the four async_add_entities calls (sensor, button, select,
    switch) have not yet completed their entity-setup tasks.

    Entities that exist but are transiently "unavailable" (CoordinatorEntity
    initial write, RestoreEntity async_get_last_state() yield) are accepted:
    Lovelace auto-refreshes card state when the entity transitions to a live
    value, so a brief unavailable flash is far preferable to timing out and
    leaving the dashboard with the previous map's entity IDs.
    """
    if not rooms or not active_map_id:
        return True
    _m = f"map{active_map_id}_"
    for room in rooms:
        rid = room.get("id")
        if rid is None:
            continue
        eids = (
            f"sensor.{device_id}_{_m}room_{rid}_last_cleaned",
            f"sensor.{device_id}_{_m}room_{rid}_cleanings",
            f"sensor.{device_id}_{_m}room_{rid}_area",
            f"sensor.{device_id}_{_m}room_{rid}_avg_clean_time",
            f"button.{device_id}_{_m}clean_room_{rid}",
            f"select.{device_id}_{_m}room_{rid}_fan_speed",
            f"select.{device_id}_{_m}room_{rid}_strategy",
            f"switch.{device_id}_{_m}room_{rid}_deep_clean",
            room_selection_entity_id(device_id, active_map_id, str(rid)),
        )
        for eid in eids:
            if hass.states.get(eid) is None:
                return False
    return True


def _schedule_label(entry: dict[str, Any], rooms: list[dict[str, Any]]) -> str:
    """Return a human-readable label for a raw API schedule entry."""
    t = entry.get("time", {}) if isinstance(entry.get("time"), dict) else {}
    task = entry.get("task", {}) if isinstance(entry.get("task"), dict) else {}
    days_str = "/".join(
        SCHEDULE_DAYS.get(d, str(d)) for d in sorted(t.get("days_of_week", []))
    )
    time_str = f"{t.get('hour', 0):02d}:{t.get('min', 0):02d}"
    if int(task.get("cleaning_mode", CLEANING_MODE_ALL)) != CLEANING_MODE_ALL:
        area_ids = [int(a) for a in task.get("parameters", [])]
        room_map = {r["id"]: r["name"] for r in rooms}
        rooms_str = " + ".join(room_map.get(a, str(a)) for a in area_ids) or "Rooms"
    else:
        rooms_str = "All rooms"
    return f"{days_str} {time_str} — {rooms_str}"


# ── Config builder ────────────────────────────────────────────────────

def _build_config(
    hass: HomeAssistant,
    rooms: list[dict[str, Any]],
    device_id: str = _DEV,
    active_map_id: str = "",
    title: str = DASHBOARD_TITLE,
    available_maps: list[dict[str, Any]] | None = None,
    schedule_entries: list[dict[str, Any]] | None = None,
    device_info_entities: list[dict[str, Any]] | None = None,
    live_entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    has_maps = bool(available_maps)
    _d = device_id
    _m = f"map{active_map_id}_" if active_map_id else ""
    # Entity IDs — all use device_id prefix, suffixes match slugified translation names
    e_vacuum            = f"vacuum.{_d}"
    e_battery           = f"sensor.{_d}_battery_level"
    e_mode              = f"sensor.{_d}_mode"
    e_charging          = f"sensor.{_d}_charging_status"
    e_area_cleaned      = f"sensor.{_d}_current_area_cleaned"
    e_cleaning_time     = f"sensor.{_d}_current_cleaning_time"
    e_total_runs        = f"sensor.{_d}_total_cleaning_runs"
    e_total_area        = f"sensor.{_d}_total_cleaned_area"
    e_total_distance    = f"sensor.{_d}_total_distance_driven"
    e_total_time        = f"sensor.{_d}_total_cleaning_time"
    e_wifi_rssi         = f"sensor.{_d}_wi_fi_signal_strength"
    e_wifi_ssid         = f"sensor.{_d}_wi_fi_network"
    e_firmware          = f"sensor.{_d}_firmware_version"
    e_serial            = f"sensor.{_d}_serial_number"
    e_live_map          = f"sensor.{_d}_live_map"
    e_cleaning_mode     = f"select.{_d}_cleaning_mode"
    e_cleaning_strategy = f"select.{_d}_cleaning_strategy"
    e_deep_clean_switch = f"switch.{_d}_deep_clean_mode"
    e_btn_clean_all     = f"button.{_d}_clean_entire_home"
    e_btn_stop          = f"button.{_d}_stop"
    e_btn_go_home       = f"button.{_d}_return_to_base"
    e_cleaning_queue    = f"sensor.{_d}_cleaning_queue"
    e_queue_eta         = f"sensor.{_d}_queue_eta"

    # live_entities is pre-filtered by _async_update_locked to exclude disabled sensors
    if live_entities is None:
        live_entities = [
            {"entity": e_area_cleaned,  "name": "Area Cleaned"},
            {"entity": e_cleaning_time, "name": "Time Elapsed"},
        ]

    live_map_entities = [
        {"entity": e_live_map, "name": "Live Map Data"}
    ]

    device_info_entities = device_info_entities or []

    view_control: dict[str, Any] = {
        "title": "Control",
        "icon": "mdi:robot-vacuum",
        "cards": [
            {
                "type": "tile",
                "entity": e_vacuum,
                "name": "Rowenta Xplorer 120",
                "icon": "mdi:robot-vacuum",
                "features": [
                    {
                        "type": "vacuum-commands",
                        "commands": ["start_pause", "stop", "return_home"],
                    },
                ],
            },
            {
                "type": "conditional",
                "conditions": [
                    {"entity": e_vacuum, "state": "error"},
                ],
                "card": {
                    "type": "markdown",
                    "content": (
                        "⚠️ **{{ state_attr('"
                        + e_vacuum
                        + "', 'error') }}**"
                    ),
                },
            },
            {
                "type": "horizontal-stack",
                "cards": [
                    {
                        "type": "button",
                        "entity": e_btn_clean_all,
                        "name": "Clean All",
                        "icon": "mdi:robot-vacuum",
                        "show_state": False,
                        "tap_action": {"action": "toggle"},
                    },
                    {
                        "type": "button",
                        "entity": e_btn_stop,
                        "name": "Stop",
                        "icon": "mdi:stop-circle-outline",
                        "show_state": False,
                        "tap_action": {"action": "toggle"},
                    },
                    {
                        "type": "button",
                        "entity": e_btn_go_home,
                        "name": "Go Home",
                        "icon": "mdi:home-import-outline",
                        "show_state": False,
                        "tap_action": {"action": "toggle"},
                    },
                ],
            },
            {
                "type": "entities",
                "title": "Cleaning Mode",
                "entities": [
                    {
                        "entity": e_cleaning_mode,
                        "name": "Fan speed",
                    },
                    {
                        "entity": e_cleaning_strategy,
                        "name": "Cleaning strategy",
                    },
                    {
                        "entity": e_deep_clean_switch,
                        "name": "Deep clean (double pass)",
                    },
                ],
            },
            {
                "type": "entities",
                "title": "Status",
                "entities": [
                    {"entity": e_battery,  "name": "Battery"},
                    {"entity": e_mode,     "name": "Mode"},
                    {"entity": e_charging, "name": "Charging"},
                    {"entity": f"binary_sensor.{_d}_left_brush_stuck",  "name": "Left Brush"},
                    {"entity": f"binary_sensor.{_d}_right_brush_stuck", "name": "Right Brush"},
                    {"entity": f"binary_sensor.{_d}_dustbin_present",   "name": "Dustbin"},
                    *live_entities,
                    *live_map_entities,
                ],
            },
            {
                "type": "markdown",
                "title": "Cleaning Queue",
                "content": (
                    "{% set q = state_attr('"
                    + e_cleaning_queue
                    + "', 'queue') %}"
                    "{% set eta = states('"
                    + e_queue_eta
                    + "') | int(0) %}"
                    "\n{% if q and q | length > 0 %}"
                    "\n{% for item in q %}"
                    "\n{% if item.status == 'active' %}🔄"
                    "{% elif item.status == 'paused' %}⏸"
                    "{% else %}⏳{% endif %} "
                    "**{{ item.label }}**"
                    "{% if item.map_name %} *({{ item.map_name }})*{% endif %}"
                    "\n{% endfor %}"
                    "\n{% if eta > 0 %}"
                    "\n⏱ Total estimated time to finish cleaning: "
                    "{{ (eta / 60) | round(0) | int }} min"
                    "\n{% else %}"
                    "\n⏱ Total estimated time to finish cleaning: unavailable"
                    "\n{% endif %}"
                    "\n{% else %}"
                    "\n*Queue empty*"
                    "\n{% endif %}"
                ),
            },
            *([{
                "type": "entities",
                "title": "Schedule",
                "icon": "mdi:calendar-clock",
                "entities": [
                    item
                    for e in (schedule_entries or [])
                    if isinstance(e, dict) and e.get("task_id") is not None
                    for item in [
                        {"type": "section", "label": _schedule_label(e, rooms)},
                        {
                            "entity": f"switch.{_d}_schedule_{e['task_id']}",
                            "name": "Enabled",
                        },
                    ]
                ],
            }] if schedule_entries else [{
                "type": "markdown",
                "title": "Schedule",
                "content": "*No schedule configured*",
            }]),
            *([{
                "type": "entities",
                "title": "Current Floor",
                "entities": [
                    {"entity": f"sensor.{_d}_active_map",  "name": "Active Map"},
                    {"entity": f"select.{_d}_active_map",  "name": "Switch Map"},
                ],
            }] if has_maps else []),
        ],
    }

    _ROOM_TYPE_ICONS: dict[str, str] = {
        "corridor": "mdi:door-open",
        "kitchen":  "mdi:chef-hat",
        "sleeping": "mdi:bed",
        "living":   "mdi:sofa",
        "bathroom": "mdi:shower",
    }

    # ── "Multi-Room Cleaning" card — FIRST in the rooms view ─────────────
    # Single entities card with room selection toggles + Clean Selected Rooms
    # button, separated by a divider.
    _clean_sel_btn = f"button.{device_id}_clean_selected_rooms"

    # Room selection toggle rows — shown inside the top card
    _room_sel_entities = [
        {
            "entity": room_selection_entity_id(device_id, active_map_id, str(r["id"])),
            "name": r["name"],
            "icon": _ROOM_TYPE_ICONS.get(r.get("room_type", ""), "mdi:door"),
        }
        for r in rooms
    ]

    room_cards: list[dict[str, Any]] = []
    if rooms:
        room_cards.append({
            "type": "entities",
            "title": "Multi-Room Cleaning",
            "show_header_toggle": False,
            "entities": _room_sel_entities + [
                {"type": "divider"},
                {
                    "entity": _clean_sel_btn,
                    "name": "▶  Clean Selected Rooms",
                    "icon": "mdi:broom-check",
                },
            ],
        })

    for room in rooms:
        rid = room["id"]
        room_icon = _ROOM_TYPE_ICONS.get(room.get("room_type", ""), "mdi:door")
        room_cards.append({
            "type": "entities",
            "title": room["name"],
            "icon": room_icon,
            "entities": [
                {
                    "entity": f"select.{device_id}_{_m}room_{rid}_fan_speed",
                    "name": "Fan Speed",
                    "icon": "mdi:speedometer",
                },
                {
                    "entity": f"select.{device_id}_{_m}room_{rid}_strategy",
                    "name": "Strategy",
                    "icon": "mdi:layers-triple",
                },
                {
                    "entity": f"switch.{device_id}_{_m}room_{rid}_deep_clean",
                    "name": "Deep clean (overrides strategy)",
                    "icon": "mdi:robot-vacuum-variant",
                },
                {
                    "entity": f"button.{device_id}_{_m}clean_room_{rid}",
                    "name": "▶  Start Cleaning",
                    "icon": "mdi:broom",
                },
                {"type": "divider"},
                {
                    "entity": f"sensor.{device_id}_{_m}room_{rid}_last_cleaned",
                    "name": "Last Cleaned",
                    "icon": "mdi:calendar-clock",
                },
                {
                    "entity": f"sensor.{device_id}_{_m}room_{rid}_cleanings",
                    "name": "Times Cleaned",
                    "icon": "mdi:counter",
                },
                {
                    "entity": f"sensor.{device_id}_{_m}room_{rid}_area",
                    "name": "Room Area",
                    "icon": "mdi:texture-box",
                },
                {
                    "entity": f"sensor.{device_id}_{_m}room_{rid}_avg_clean_time",
                    "name": "Avg Duration",
                    "icon": "mdi:timer-outline",
                },
            ],
        })

    if not has_maps:
        _rooms_cards: list[dict[str, Any]] = [
            {"type": "markdown", "content": _NO_MAP_GUIDANCE}
        ]
    elif room_cards:
        _rooms_cards = room_cards
    else:
        _rooms_cards = [
            {
                "type": "markdown",
                "content": (
                    "## No rooms discovered\n\n"
                    "Check the **Map ID** matches the active map in the RobEye app, "
                    "then reload the integration."
                ),
            }
        ]

    view_rooms: dict[str, Any] = {
        "title": "Rooms",
        "icon": "mdi:floor-plan",
        "cards": _rooms_cards,
    }

    stats_cards: list[dict[str, Any]] = [
        {
            "type": "entities",
            "title": "Lifetime Statistics",
            "entities": [
                {"entity": e_total_runs,     "name": "Total Runs"},
                {"entity": e_total_area,     "name": "Total Area Cleaned"},
                {"entity": e_total_distance, "name": "Total Distance"},
                {"entity": e_total_time,     "name": "Total Cleaning Time"},
            ],
        },
    ]
    if device_info_entities:
        stats_cards.append({
            "type": "entities",
            "title": "Device Info",
            "entities": device_info_entities,
        })
    else:
        stats_cards.append({
            "type": "markdown",
            "title": "Device Info",
            "content": (
                "*No device info sensors are enabled.*\n\n"
                "Enable one or more diagnostic sensors (Serial Number, "
                "Firmware Version, Wi-Fi Network, Wi-Fi Signal) in "
                "Home Assistant to display device information here."
            ),
        })

    stats_cards.append({
        "type": "entities",
        "title": "Live Map",
        "entities": [
            {"entity": e_live_map, "name": "Map State"},
        ],
    })

    view_stats: dict[str, Any] = {
        "title": "Statistics",
        "icon": "mdi:chart-bar",
        "cards": stats_cards,
    }

    # ── View 4: Map ───────────────────────────────────────────────────
    if not has_maps:
        view_map: dict[str, Any] = {
            "title": "Map",
            "icon": "mdi:map",
            "cards": [{"type": "markdown", "content": _NO_MAP_GUIDANCE}],
        }
    else:
        view_map = {
            "title": "Map",
            "icon": "mdi:map",
            "cards": [
                {
                    "type": "custom:rowenta-map-card",
                    "entity": e_live_map,
                    "title": "Live Map",
                    "show_debug": True,
                }
            ],
        }
    views = [view_control, view_rooms, view_stats, view_map]

    return {
        "title": title,
        "views": views,
    }


# ── Dashboard manager ─────────────────────────────────────────────────

class RobEyeDashboardManager:
    """Manages the Rowenta dashboard lifecycle.

    One instance lives in __init__.py for the lifetime of the config entry.
    Tracks last-saved config hash to avoid spurious saves/notifications.

    For multi-device support, each manager stores its own url_path and title.
    The default device_id (_DEV) keeps backward-compatible dashboard URL.
    """

    def __init__(self, device_id: str = _DEV, friendly_name: str | None = None) -> None:
        self._last_hash: str | None = None
        self._sidebar_hidden: bool = False
        # Serializes async_update() so the dozen+ callers that can race
        # (coordinator listener, areas-changed signal, initial-dashboard
        # retry loop, options reload) never run concurrently against the
        # same Lovelace store.  Without this, two writers can both pass the
        # _room_entities_registered check, then write configs with
        # interleaved hashes — the first "bad" write stamps _last_hash and
        # the later "good" write is skipped as a no-op.
        self._save_lock = asyncio.Lock()
        # Per-device dashboard identity — URL path is always derived from device_id
        # for stability (renames don't create orphaned dashboards).
        if device_id == _DEV or not device_id:
            self._url_path = DASHBOARD_URL_PATH
        else:
            slug = device_id.replace("_", "-").replace(" ", "-").lower()
            self._url_path = f"rowenta-{slug}"
        # Title uses the friendly name if provided, otherwise fall back to defaults.
        if friendly_name:
            self._title = friendly_name
        elif device_id == _DEV or not device_id:
            self._title = DASHBOARD_TITLE
        else:
            self._title = DASHBOARD_TITLE

    def invalidate(self) -> None:
        """Force a save on the next async_update() call (e.g. after room change)."""
        self._last_hash = None

    # Internal poll cadence used by async_update() to wait for per-room
    # platform entities to settle in hass.states before saving.
    _ENTITY_POLL_INTERVAL_S = 0.2
    _ENTITY_POLL_TIMEOUT_S = 8.0

    async def async_update(
        self,
        hass: HomeAssistant,
        areas: list[dict[str, Any]],
        device_id: str = _DEV,
        active_map_id: str = "",
        friendly_name: str | None = None,
        available_maps: list[dict[str, Any]] | None = None,
        schedule_entries: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Create or update the dashboard only when config has changed.

        Serialized via self._save_lock so concurrent callers (coordinator
        listener, areas-changed signal, options reload) cannot interleave
        their writes.  Inside the lock the manager polls for the active
        map's per-room entities to appear in hass.states, then saves.

        Returns True if the dashboard is ready (saved or unchanged),
        False if the lovelace store is not yet available, the wait timed
        out, or the user switched maps mid-wait.
        """
        async with self._save_lock:
            return await self._async_update_locked(
                hass=hass,
                areas=areas,
                device_id=device_id,
                active_map_id=active_map_id,
                friendly_name=friendly_name,
                available_maps=available_maps,
                schedule_entries=schedule_entries,
            )

    async def _async_update_locked(
        self,
        hass: HomeAssistant,
        areas: list[dict[str, Any]],
        device_id: str,
        active_map_id: str,
        friendly_name: str | None,
        available_maps: list[dict[str, Any]] | None,
        schedule_entries: list[dict[str, Any]] | None,
    ) -> bool:
        # Locate the matching coordinator (if any) so we can detect a
        # mid-wait map switch and validate that areas belong to active_map_id.
        coordinator = None
        for entry_data in hass.data.get(DOMAIN, {}).values():
            if getattr(entry_data, "device_id", None) == device_id:
                coordinator = entry_data
                break

        rooms = _extract_rooms(areas)

        # Discard the rooms list when it does not belong to the currently
        # active map.  Two cases trigger this:
        #
        #   1. areas_map_id is None — areas have not yet been committed for
        #      the active map (e.g. user just switched, get_areas pending or
        #      failed transiently this tick).
        #   2. areas_map_id points at a different map — stale DATA_AREAS that
        #      escaped the start-of-tick pop in _async_update_data (can happen
        #      when the user's map switch races the in-flight tick after
        #      _current_active was already latched).
        #
        # In either case the dashboard must NOT render the stale rooms: their
        # area IDs would not match the active_map_id-prefixed entity IDs, so
        # _room_entities_registered would loop until timeout and the previous
        # save (still showing the OLD map's rooms) would persist in storage.
        # Saving with rooms=[] actively replaces that stale dashboard with a
        # transitional "No rooms discovered" view; the next successful commit
        # invalidates and re-saves with the correct rooms.
        if coordinator is not None:
            fetched_for = getattr(coordinator, "areas_map_id", None)
            if fetched_for != active_map_id:
                _LOGGER.debug(
                    "Dashboard: rooms fetched for map %s but active is %s — "
                    "rendering transitional empty rooms",
                    fetched_for, active_map_id,
                )
                rooms = []

        # Build entity lists from the entity registry (stable between ticks)
        # rather than hass.states (volatile during startup and transient failures).
        # This keeps _build_config output deterministic and prevents spurious hash changes.
        _ent_reg = er.async_get(hass)
        _INFO_LABELS = [
            (f"sensor.{device_id}_serial_number",        "Serial Number"),
            (f"sensor.{device_id}_firmware_version",     "Firmware Version"),
            (f"sensor.{device_id}_wi_fi_network",        "Wi-Fi Network"),
            (f"sensor.{device_id}_wi_fi_signal_strength","Wi-Fi Signal"),
        ]
        _device_info_entities = [
            {"entity": eid, "name": label}
            for eid, label in _INFO_LABELS
            if (_e := _ent_reg.async_get(eid)) is not None and not _e.disabled
        ]

        # current_area_cleaned and current_cleaning_time are disabled by default;
        # only include them in the status card when the user has enabled them.
        _LIVE_LABELS = [
            (f"sensor.{device_id}_current_area_cleaned", "Area Cleaned"),
            (f"sensor.{device_id}_current_cleaning_time", "Time Elapsed"),
        ]
        _live_entities = [
            {"entity": eid, "name": label}
            for eid, label in _LIVE_LABELS
            if (_e := _ent_reg.async_get(eid)) is None or not _e.disabled
        ]

        # Poll for per-room entities to appear in hass.states.  Each
        # asyncio.sleep(0) hand-off lets the platform async_add_entities
        # tasks make progress; entity setup typically completes within a
        # few hundred ms, so the 8 s timeout is comfortably above the 99th
        # percentile while still bounded.  If the user switches maps
        # mid-wait we abort so the next invocation saves the new map.
        deadline_iters = max(
            1, int(self._ENTITY_POLL_TIMEOUT_S / self._ENTITY_POLL_INTERVAL_S)
        )
        ready = False
        for _ in range(deadline_iters):
            if (
                coordinator is not None
                and coordinator.active_map_id != active_map_id
            ):
                _LOGGER.debug(
                    "Dashboard update aborted — map changed mid-wait (%s → %s)",
                    active_map_id, coordinator.active_map_id,
                )
                return False
            if _room_entities_registered(hass, device_id, active_map_id, rooms):
                ready = True
                break
            await asyncio.sleep(self._ENTITY_POLL_INTERVAL_S)

        if not ready:
            _LOGGER.debug(
                "Dashboard update deferred — room entities for map %s "
                "not registered after %.1fs",
                active_map_id, self._ENTITY_POLL_TIMEOUT_S,
            )
            return False

        title = friendly_name or self._title
        config = _build_config(hass, rooms, device_id, active_map_id=active_map_id, title=title, available_maps=available_maps, schedule_entries=schedule_entries, device_info_entities=_device_info_entities, live_entities=_live_entities)
        new_hash = _config_hash(config)

        _LOGGER.debug(
            "RobEye dashboard: async_update called, rooms=%d hash=%s last=%s",
            len(rooms), new_hash[:8], (self._last_hash or "none")[:8],
        )

        # Step 1: check whether our dashboard still exists in the registry
        lovelace_store = await self._async_get_lovelace_store(hass)

        if lovelace_store is None:
            # lovelace not ready yet (HA still starting up) — will retry next cycle
            _LOGGER.debug("RobEye dashboard: lovelace store not ready, will retry")
            return False

        # Step 2: skip save if config is identical AND dashboard exists
        if new_hash == self._last_hash:
            _LOGGER.debug("RobEye dashboard: config unchanged, skipping save")
            return True

        # Step 3: save config
        try:
            await lovelace_store.async_save(config)
            self._last_hash = new_hash
            _LOGGER.info(
                "RobEye dashboard: saved — %d rooms, hash=%s",
                len(rooms), new_hash[:8],
            )
            return True
        except Exception as err:
            self._last_hash = None   # retry next cycle
            _LOGGER.warning("RobEye dashboard: async_save() failed: %s", err)
            return False

    async def async_set_sidebar_visible(self, hass: HomeAssistant, visible: bool) -> None:
        """Show or hide the dashboard in the HA sidebar without deleting it.

        Called when the device is enabled or disabled in the device registry.
        Toggling visibility preserves all dashboard content and configuration.

        Two-step approach:
          1. Immediate: re-register the panel with show_in_sidebar=visible so the
             sidebar updates without an HA restart.
          2. Persistent: write show_in_sidebar to the Lovelace storage entry so
             that on the next HA boot the global DashboardsCollection reads the
             correct value and does not re-show a disabled device's dashboard.
        """
        self._sidebar_hidden = not visible

        # ── Step 1: immediate frontend update ────────────────────────────
        try:
            from homeassistant.components import frontend as _frontend
            _frontend.async_register_built_in_panel(
                hass,
                component_name="lovelace",
                sidebar_title=self._title,
                sidebar_icon=DASHBOARD_ICON,
                frontend_url_path=self._url_path,
                config={"mode": "storage"},
                require_admin=False,
                update=True,
                show_in_sidebar=visible,
            )
            _LOGGER.info(
                "RobEye dashboard: sidebar %s — device %s",
                "shown" if visible else "hidden",
                "enabled" if visible else "disabled",
            )
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: panel update failed: %s", err)

        # ── Step 2: persist show_in_sidebar to storage ───────────────────
        # Without this, HA restart re-reads show_in_sidebar=True from storage
        # and re-shows the panel even though the device is disabled.
        try:
            from homeassistant.components.lovelace.dashboard import DashboardsCollection
        except ImportError as err:
            _LOGGER.warning("RobEye dashboard: cannot import lovelace: %s", err)
            return

        dashboards_collection = DashboardsCollection(hass)
        try:
            await dashboards_collection.async_load()
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: async_load() failed: %s", err)
            return

        item = next(
            (i for i in dashboards_collection.async_items()
             if i.get(_CONF_URL_PATH) == self._url_path),
            None,
        )
        if item is None:
            _LOGGER.debug(
                "RobEye dashboard: item not found in storage — show_in_sidebar not persisted"
            )
            return

        try:
            await dashboards_collection.async_update_item(
                item["id"],
                {_CONF_SHOW_IN_SIDEBAR: visible},
            )
            _LOGGER.info(
                "RobEye dashboard: show_in_sidebar=%s persisted to storage", visible
            )
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: async_update_item() failed: %s", err)

    async def async_delete(self, hass: HomeAssistant) -> None:
        """Remove the dashboard from the Lovelace registry.

        Mirrors what storage_dashboard_changed (removal branch) does in
        homeassistant/components/lovelace/__init__.py:
          1. Delete from persistent storage via DashboardsCollection
          2. Remove LovelaceStorage from hass.data["lovelace"].dashboards
          3. Unregister the frontend panel so it disappears from the sidebar

        Creating a local DashboardsCollection instance only updates on-disk
        storage — it does NOT fire the global storage_dashboard_changed
        listener, so steps 2 and 3 must be performed explicitly.
        """
        try:
            from homeassistant.components.lovelace.dashboard import DashboardsCollection
            from homeassistant.components.lovelace.const import LOVELACE_DATA
        except ImportError as err:
            _LOGGER.warning("RobEye dashboard: cannot import lovelace internals: %s", err)
            return

        # Step 1 — Remove from persistent storage
        dashboards_collection = DashboardsCollection(hass)
        try:
            await dashboards_collection.async_load()
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: async_load() failed during delete: %s", err)
            return

        item = next(
            (i for i in dashboards_collection.async_items()
             if i.get(_CONF_URL_PATH) == self._url_path),
            None,
        )
        if item is not None:
            try:
                await dashboards_collection.async_delete_item(item["id"])
                _LOGGER.info("RobEye dashboard: deleted '%s' from storage", self._url_path)
            except Exception as err:
                _LOGGER.warning("RobEye dashboard: async_delete_item() failed: %s", err)
        else:
            _LOGGER.debug(
                "RobEye dashboard: '%s' not found in storage — skipping delete",
                self._url_path,
            )

        # Step 1b — Delete the dashboard content storage file.
        # DashboardsCollection.async_delete_item() only removes the registry
        # entry (lovelace_dashboards store).  The actual card config lives in a
        # separate Store keyed "lovelace.<url_path>" and must be removed
        # explicitly, otherwise .storage/lovelace.<url_path> is left behind as
        # an orphan after the device is deleted.
        try:
            from homeassistant.helpers.storage import Store as _Store
            content_store = _Store(hass, 1, f"lovelace.{self._url_path}")
            await content_store.async_remove()
            _LOGGER.info(
                "RobEye dashboard: removed content store 'lovelace.%s'",
                self._url_path,
            )
        except Exception as err:
            _LOGGER.debug(
                "RobEye dashboard: content store removal skipped: %s", err
            )

        # Step 2 — Remove from the in-memory lovelace dashboards dict
        # This is what storage_dashboard_changed (removal) normally does via
        # the global DashboardsCollection listener.
        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is not None:
            lovelace_dashboards: dict = getattr(lovelace_data, "dashboards", {})
            if self._url_path in lovelace_dashboards:
                lovelace_dashboards.pop(self._url_path, None)
                _LOGGER.info(
                    "RobEye dashboard: removed '%s' from hass.data lovelace dashboards",
                    self._url_path,
                )

        # Step 3 — Unregister the frontend panel so the sidebar entry disappears.
        # Guard against "Removing unknown panel" warnings when setup failed before
        # the panel was ever registered (e.g. first-time add that timed out).
        try:
            from homeassistant.components import frontend as _frontend
            if self._url_path in hass.data.get("frontend_panels", {}):
                _frontend.async_remove_panel(hass, self._url_path)
                _LOGGER.info(
                    "RobEye dashboard: frontend panel '%s' unregistered",
                    self._url_path,
                )
            else:
                _LOGGER.debug(
                    "RobEye dashboard: panel '%s' was not registered — skipping removal",
                    self._url_path,
                )
        except Exception as err:
            _LOGGER.debug("RobEye dashboard: panel removal skipped: %s", err)

        self._last_hash = None  # Reset so a future re-add starts fresh

    async def _async_get_lovelace_store(self, hass: HomeAssistant) -> Any | None:
        """Return the LovelaceStorage object for our dashboard.

        HA's global DashboardsCollection is created locally in lovelace/async_setup
        and is NOT stored in hass.data.  Calling async_create_item() on a *new*
        DashboardsCollection instance writes to storage but never triggers the
        global collection's storage_dashboard_changed listener — so the store
        never appears in hass.data[LOVELACE_DATA].dashboards automatically.

        Fix: after finding or creating the item in storage we manually construct
        the LovelaceStorage object and inject it into hass.data, then register
        the frontend panel — exactly what storage_dashboard_changed does.
        """
        # ── Import lovelace internals ─────────────────────────────────
        try:
            from homeassistant.components.lovelace.dashboard import (
                DashboardsCollection,
                LovelaceStorage,
            )
            from homeassistant.components.lovelace.const import LOVELACE_DATA
        except ImportError as err:
            _LOGGER.warning(
                "RobEye dashboard: cannot import lovelace internals: %s "
                "— dashboard will not be created",
                err,
            )
            return None

        # ── Get LovelaceData from hass.data ───────────────────────────
        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is None:
            _LOGGER.debug(
                "RobEye dashboard: hass.data[%r] is None — lovelace not set up yet",
                LOVELACE_DATA,
            )
            return None

        lovelace_dashboards: dict = getattr(lovelace_data, "dashboards", {})

        # ── Fast path: our dashboard already registered in hass.data ──
        if self._url_path in lovelace_dashboards:
            _LOGGER.debug(
                "RobEye dashboard: '%s' already in dashboards dict",
                self._url_path,
            )
            return lovelace_dashboards[self._url_path]

        # ── Load storage to find or create the dashboard item ─────────
        _LOGGER.info(
            "RobEye dashboard: '%s' not found in registry — creating",
            self._url_path,
        )

        dashboards_collection = DashboardsCollection(hass)
        try:
            await dashboards_collection.async_load()
        except Exception as err:
            _LOGGER.warning(
                "RobEye dashboard: DashboardsCollection.async_load() failed: %s", err
            )
            return None

        # Re-check hass.data after async_load — HA may have already registered it
        # if a reload happened between our two checks.
        if self._url_path in getattr(lovelace_data, "dashboards", {}):
            return lovelace_data.dashboards[self._url_path]

        # Find existing item in storage or create a new one
        item = next(
            (i for i in dashboards_collection.async_items()
             if i.get(_CONF_URL_PATH) == self._url_path),
            None,
        )

        if item is None:
            try:
                item = await dashboards_collection.async_create_item({
                    _CONF_URL_PATH:        self._url_path,
                    _CONF_TITLE:           self._title,
                    _CONF_ICON:            DASHBOARD_ICON,
                    _CONF_REQUIRE_ADMIN:   False,
                    _CONF_SHOW_IN_SIDEBAR: True,
                })
                _LOGGER.info("RobEye dashboard: async_create_item() succeeded")
            except Exception as err:
                _LOGGER.warning(
                    "RobEye dashboard: async_create_item() failed: %s (type=%s)",
                    err, type(err).__name__,
                )
                return None

        # ── Manually register store — mirrors storage_dashboard_changed ──
        # The global DashboardsCollection's listener (storage_dashboard_changed)
        # is the only thing that normally adds LovelaceStorage to hass.data, but
        # it only fires for the global instance.  We replicate that work here.
        store = LovelaceStorage(hass, item)
        lovelace_data.dashboards[self._url_path] = store
        _LOGGER.info(
            "RobEye dashboard: LovelaceStorage injected into hass.data — type=%s",
            type(store).__name__,
        )

        # Register the frontend panel so the dashboard appears in the sidebar,
        # but only when the device is not disabled — _sidebar_hidden is set by
        # async_set_sidebar_visible() before we reach this path on a re-create.
        if not self._sidebar_hidden:
            try:
                from homeassistant.components import frontend as _frontend
                _frontend.async_register_built_in_panel(
                    hass,
                    component_name="lovelace",
                    sidebar_title=self._title,
                    sidebar_icon=DASHBOARD_ICON,
                    frontend_url_path=self._url_path,
                    config={"mode": "storage"},
                    require_admin=False,
                    update=False,
                )
                _LOGGER.info(
                    "RobEye dashboard: frontend panel registered for '%s'",
                    self._url_path,
                )
            except Exception as err:
                # Panel may already be registered (e.g. from a previous boot that
                # persisted the entry).  Log but continue — async_save() still works.
                _LOGGER.debug(
                    "RobEye dashboard: panel registration skipped: %s", err
                )

        return store



# ── Helpers ───────────────────────────────────────────────────────────

def _extract_rooms(areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = []
    for area in areas:
        meta_raw = area.get("area_meta_data", "")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except Exception:
            continue
        name = meta.get("name", "").strip()
        if not name:
            continue
        rooms.append({"id": area["id"], "name": name, "room_type": area.get("room_type", "")})
    return rooms


# ── Public entry point ────────────────────────────────────────────────

async def async_create_dashboard(
    hass: HomeAssistant,
    areas: list[dict[str, Any]],
    robot_info: dict[str, Any] | None = None,
    manager: "RobEyeDashboardManager | None" = None,
    device_id: str = _DEV,
    active_map_id: str = "",
    friendly_name: str | None = None,
    available_maps: list[dict[str, Any]] | None = None,
    schedule_entries: list[dict[str, Any]] | None = None,
) -> bool:
    """Create or update the dashboard. Idempotent — safe to call repeatedly.

    Returns True if the dashboard was saved (or was already up-to-date),
    False if the lovelace store was not available or the save failed.
    """
    _mgr = manager or RobEyeDashboardManager(device_id=device_id, friendly_name=friendly_name)
    return await _mgr.async_update(hass, areas, device_id, active_map_id=active_map_id, friendly_name=friendly_name, available_maps=available_maps, schedule_entries=schedule_entries)
