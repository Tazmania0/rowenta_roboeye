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




def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _available(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.state not in ("unavailable", "unknown", "")


# ── Config builder ────────────────────────────────────────────────────

def _build_config(hass: HomeAssistant, rooms: list[dict[str, Any]], device_id: str = _DEV) -> dict[str, Any]:
    _d = device_id
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
    e_deep_clean_switch = f"switch.{_d}_deep_clean_mode"
    e_btn_clean_all     = f"button.{_d}_clean_entire_home"
    e_btn_stop          = f"button.{_d}_stop"
    e_btn_go_home       = f"button.{_d}_return_to_base"

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
                    *live_entities,
                    *live_map_entities,
                ],
            },
        ],
    }

    room_cards: list[dict[str, Any]] = []
    for room in rooms:
        rid = room["id"]
        room_cards.append({
            "type": "entities",
            "title": room["name"],
            "icon": "mdi:door",
            "entities": [
                {
                    "entity": f"select.{device_id}_room_{rid}_fan_speed",
                    "name": "Fan Speed",
                    "icon": "mdi:speedometer",
                },
                {
                    "entity": f"button.{device_id}_clean_room_{rid}",
                    "name": "▶  Start Cleaning",
                    "icon": "mdi:broom",
                },
                {
                    "entity": f"switch.{device_id}_room_{rid}_deep_clean",
                    "name": "Deep clean this room",
                    "icon": "mdi:robot-vacuum-variant",
                },
                {"type": "divider"},
                {
                    "entity": f"sensor.{device_id}_room_{rid}_last_cleaned",
                    "name": "Last Cleaned",
                    "icon": "mdi:calendar-clock",
                },
                {
                    "entity": f"sensor.{device_id}_room_{rid}_cleanings",
                    "name": "Times Cleaned",
                    "icon": "mdi:counter",
                },
                {
                    "entity": f"sensor.{device_id}_room_{rid}_area",
                    "name": "Room Area",
                    "icon": "mdi:texture-box",
                },
                {
                    "entity": f"sensor.{device_id}_room_{rid}_avg_clean_time",
                    "name": "Avg Duration",
                    "icon": "mdi:timer-outline",
                },
            ],
        })

    view_rooms: dict[str, Any] = {
        "title": "Rooms",
        "icon": "mdi:floor-plan",
        "cards": room_cards or [
            {
                "type": "markdown",
                "content": (
                    "## No rooms discovered\n\n"
                    "Check the **Map ID** matches the active map in the RobEye app, "
                    "then reload the integration."
                ),
            }
        ],
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

    # ── View 4: Live Map ──────────────────────────────────────────────
    view_map: dict[str, Any] = {
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

    views = [view_control, view_rooms, view_stats]
    # Only add map view if the live map sensor is enabled
    if _available(hass, e_live_map):
        views.append(view_map)

    return {
        "title": DASHBOARD_TITLE,
        "views": views,
    }


# ── Dashboard manager ─────────────────────────────────────────────────

class RobEyeDashboardManager:
    """Manages the Rowenta dashboard lifecycle.

    One instance lives in __init__.py for the lifetime of the config entry.
    Tracks last-saved config hash to avoid spurious saves/notifications.
    """

    def __init__(self) -> None:
        self._last_hash: str | None = None

    def invalidate(self) -> None:
        """Force a save on the next async_update() call (e.g. after room change)."""
        self._last_hash = None

    async def async_update(
        self,
        hass: HomeAssistant,
        areas: list[dict[str, Any]],
        device_id: str = _DEV,
    ) -> None:
        """Create or update the dashboard only when config has changed."""
        rooms = _extract_rooms(areas)
        config = _build_config(hass, rooms, device_id)
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
            return

        # Step 2: skip save if config is identical AND dashboard exists
        dashboard_exists = lovelace_store is not None
        if new_hash == self._last_hash and dashboard_exists:
            _LOGGER.debug("RobEye dashboard: config unchanged, skipping save")
            return

        # Step 3: save config
        try:
            await lovelace_store.async_save(config)
            self._last_hash = new_hash
            _LOGGER.info(
                "RobEye dashboard: saved — %d rooms, hash=%s",
                len(rooms), new_hash[:8],
            )
        except Exception as err:
            self._last_hash = None   # retry next cycle
            _LOGGER.warning("RobEye dashboard: async_save() failed: %s", err)

    async def async_delete(self, hass: HomeAssistant) -> None:
        """Remove the dashboard from the Lovelace registry."""
        try:
            from homeassistant.components.lovelace.dashboard import DashboardsCollection
        except ImportError as err:
            _LOGGER.warning("RobEye dashboard: cannot import lovelace internals: %s", err)
            return

        dashboards_collection = DashboardsCollection(hass)
        try:
            await dashboards_collection.async_load()
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: async_load() failed during delete: %s", err)
            return

        item = next(
            (i for i in dashboards_collection.async_items()
             if i.get(_CONF_URL_PATH) == DASHBOARD_URL_PATH),
            None,
        )
        if item is None:
            _LOGGER.debug("RobEye dashboard: '%s' not found in registry — nothing to delete", DASHBOARD_URL_PATH)
            return

        try:
            await dashboards_collection.async_delete_item(item["id"])
            _LOGGER.info("RobEye dashboard: deleted '%s'", DASHBOARD_URL_PATH)
        except Exception as err:
            _LOGGER.warning("RobEye dashboard: async_delete_item() failed: %s", err)

    async def _async_get_lovelace_store(self, hass: HomeAssistant) -> Any | None:
        """Return the LovelaceStorage object for our dashboard.

        Mirrors _create_map_dashboard() from homeassistant/components/lovelace/__init__.py:

          1. Get hass.data[LOVELACE_DATA] (key = "lovelace")
          2. If our url_path already in .dashboards → return it directly
          3. Otherwise: get DashboardsCollection, call async_create_item(), then
             return hass.data[LOVELACE_DATA].dashboards[DASHBOARD_URL_PATH]
        """
        # ── Import lovelace internals ─────────────────────────────────
        try:
            from homeassistant.components.lovelace.dashboard import (
                DashboardsCollection,
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
        _LOGGER.debug(
            "RobEye dashboard: lovelace_data type=%s, dashboards keys=%s",
            type(lovelace_data).__name__,
            list(lovelace_dashboards.keys()),
        )

        # ── Fast path: our dashboard already registered ───────────────
        if DASHBOARD_URL_PATH in lovelace_dashboards:
            _LOGGER.debug(
                "RobEye dashboard: '%s' already in dashboards dict",
                DASHBOARD_URL_PATH,
            )
            return lovelace_dashboards[DASHBOARD_URL_PATH]

        # ── Need to create — use DashboardsCollection ─────────────────
        _LOGGER.info(
            "RobEye dashboard: '%s' not found in registry — creating",
            DASHBOARD_URL_PATH,
        )

        dashboards_collection = DashboardsCollection(hass)
        try:
            await dashboards_collection.async_load()
        except Exception as err:
            _LOGGER.warning(
                "RobEye dashboard: DashboardsCollection.async_load() failed: %s", err
            )
            return None

        _LOGGER.debug(
            "RobEye dashboard: DashboardsCollection loaded, items=%s",
            [item.get(_CONF_URL_PATH) for item in dashboards_collection.async_items()],
        )

        # Check if it's already in the collection (could have been loaded from storage)
        already = any(
            item.get(_CONF_URL_PATH) == DASHBOARD_URL_PATH
            for item in dashboards_collection.async_items()
        )


        if not already:
            try:
                await dashboards_collection.async_create_item({
                    _CONF_URL_PATH:        DASHBOARD_URL_PATH,
                    _CONF_TITLE:           DASHBOARD_TITLE,
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

        # Yield to event loop so HA's storage_dashboard_changed listener fires.
        # That listener (lovelace/__init__.py) handles CHANGE_ADDED and adds the
        # LovelaceStorage object to hass.data[LOVELACE_DATA].dashboards[url_path].
        # Without this yield we check hass.data before the listener has run.
        await asyncio.sleep(0)

        # Re-fetch from hass.data
        lovelace_dashboards = getattr(lovelace_data, "dashboards", {})
        store = lovelace_dashboards.get(DASHBOARD_URL_PATH)

        if store is None:
            _LOGGER.warning(
                "RobEye dashboard: '%s' still not in hass.data after yield. "
                "dashboards keys: %s — will retry next cycle",
                DASHBOARD_URL_PATH,
                list(lovelace_dashboards.keys()),
            )
        else:
            _LOGGER.info(
                "RobEye dashboard: store obtained — type=%s", type(store).__name__
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
        rooms.append({"id": area["id"], "name": name})
    return rooms


# ── Public entry point ────────────────────────────────────────────────

async def async_create_dashboard(
    hass: HomeAssistant,
    areas: list[dict[str, Any]],
    robot_info: dict[str, Any] | None = None,
    manager: "RobEyeDashboardManager | None" = None,
    device_id: str = _DEV,
) -> None:
    """Create or update the dashboard. Idempotent — safe to call repeatedly."""
    _mgr = manager or RobEyeDashboardManager()
    await _mgr.async_update(hass, areas, device_id)
