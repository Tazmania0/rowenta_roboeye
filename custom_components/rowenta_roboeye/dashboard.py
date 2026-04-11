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

import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import room_selection_entity_id

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


def _available(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.state not in ("unavailable", "unknown", "")


# ── Config builder ────────────────────────────────────────────────────

def _build_config(
    hass: HomeAssistant,
    rooms: list[dict[str, Any]],
    device_id: str = _DEV,
    active_map_id: str = "",
    title: str = DASHBOARD_TITLE,
    available_maps: list[dict[str, Any]] | None = None,
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

    live_entities = [
        {"entity": e, "name": label}
        for e, label in [
            (e_area_cleaned,  "Area Cleaned"),
            (e_cleaning_time, "Time Elapsed"),
        ]
        if _available(hass, e)
    ]

    live_map_entities = [
        {"entity": e_live_map, "name": "Live Map Data"}
    ] if _available(hass, e_live_map) else []

    device_info_entities = [
        {"entity": e, "name": label}
        for e, label in [
            (e_serial,   "Serial Number"),
            (e_firmware, "Firmware Version"),
            (e_wifi_ssid,"Wi-Fi Network"),
            (e_wifi_rssi,"Wi-Fi Signal"),
        ]
        if _available(hass, e)
    ]

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
            {
                "type": "markdown",
                "title": "Schedule",
                "content": (
                    "{% set sched = state_attr('"
                    + f"sensor.{_d}_schedule"
                    + "', 'schedules') %}"
                    "\n{% if sched %}"
                    "\n{% for s in sched %}"
                    "\n{{ '✅' if s.enabled else '⬜' }} "
                    "**{{ s.days | join('/') }}** {{ s.time }}"
                    " — {{ s.rooms_str }}"
                    "{% if s.map_name %} *({{ s.map_name }})*{% endif %}"
                    "{% if s.fan_raw > 0 %} · {{ s.fan_speed }}{% endif %}"
                    "\n{% endfor %}"
                    "\n{% else %}"
                    "\n*No schedule configured*"
                    "\n{% endif %}"
                ),
            },
            *([{
                "type": "entities",
                "title": "Current Floor",
                "entities": [
                    {"entity": f"sensor.{_d}_active_map",  "name": "Active Map"},
                    {"entity": f"select.{_d}_active_map",  "name": "Switch Map"},
                ],
            }] if (has_maps and _available(hass, f"sensor.{_d}_active_map")) else []),
        ],
    }

    _ROOM_TYPE_ICONS: dict[str, str] = {
        "corridor": "mdi:door-open",
        "kitchen":  "mdi:chef-hat",
        "sleeping": "mdi:bed",
        "living":   "mdi:sofa",
        "bathroom": "mdi:shower",
    }

    room_cards: list[dict[str, Any]] = []
    for room in rooms:
        rid = room["id"]
        room_icon = _ROOM_TYPE_ICONS.get(room.get("room_type", ""), "mdi:door")
        sel_eid = room_selection_entity_id(device_id, active_map_id, str(rid))
        room_cards.append({
            "type": "entities",
            "title": room["name"],
            "icon": room_icon,
            "entities": [
                {
                    "entity": sel_eid,
                    "name": "Select for multi-room clean",
                    "icon": "mdi:checkbox-marked-circle-outline",
                },
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

    # "Clean Selected Rooms" card — shown after all room cards
    if rooms:
        _count_sensor = f"sensor.{device_id}_selected_room_count"
        _clean_sel_btn = f"button.{device_id}_clean_selected_rooms"
        room_cards.append({
            "type": "entities",
            "entities": [
                {
                    "entity": _clean_sel_btn,
                    "name": (
                        "{% set n = states('"
                        + _count_sensor
                        + "') | int(0) %}"
                        "{% if n > 0 %}▶  Clean Selected Rooms ({{ n }}){% else %}No rooms selected{% endif %}"
                    ),
                    "icon": "mdi:broom-check",
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

    if _available(hass, e_live_map):
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
        views = [view_control, view_rooms, view_stats, view_map]
    elif _available(hass, e_live_map):
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
    else:
        views = [view_control, view_rooms, view_stats]

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

    async def async_update(
        self,
        hass: HomeAssistant,
        areas: list[dict[str, Any]],
        device_id: str = _DEV,
        active_map_id: str = "",
        friendly_name: str | None = None,
        available_maps: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Create or update the dashboard only when config has changed.

        Returns True if the dashboard is ready (saved or unchanged),
        False if the lovelace store is not yet available or save failed.
        """
        rooms = _extract_rooms(areas)
        title = friendly_name or self._title
        config = _build_config(hass, rooms, device_id, active_map_id=active_map_id, title=title, available_maps=available_maps)
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

        # Step 3 — Unregister the frontend panel so the sidebar entry disappears
        try:
            from homeassistant.components import frontend as _frontend
            _frontend.async_remove_panel(hass, self._url_path)
            _LOGGER.info(
                "RobEye dashboard: frontend panel '%s' unregistered",
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
) -> bool:
    """Create or update the dashboard. Idempotent — safe to call repeatedly.

    Returns True if the dashboard was saved (or was already up-to-date),
    False if the lovelace store was not available or the save failed.
    """
    _mgr = manager or RobEyeDashboardManager(device_id=device_id, friendly_name=friendly_name)
    return await _mgr.async_update(hass, areas, device_id, active_map_id=active_map_id, friendly_name=friendly_name, available_maps=available_maps)
