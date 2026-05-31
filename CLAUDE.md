# CLAUDE.md — AI Assistant Guide for rowenta_roboeye

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**rowenta_roboeye** is a Home Assistant custom integration that provides local LAN control of Rowenta/Tefal X-Plorer Serie 120 robot vacuums via their native RobEye HTTP API (port 8080). There are no cloud dependencies, no Tuya protocol — pure local polling.

- **Language**: Python 3.9+, async/await throughout
- **Framework**: Home Assistant (≥ 2024.1.0)
- **Install mechanism**: HACS (Home Assistant Community Store)
- **Entity platforms**: Vacuum, Sensor, Binary Sensor, Button, Select, Switch
- **Discovery**: mDNS/Zeroconf (`_robeye._tcp.local.`) with manual IP fallback
- **Companion tool**: `map_editor/` — a standalone, browser-based map editor (vanilla-JS frontend + stdlib Python proxy server) for splitting/merging rooms, drawing no-go zones, and exploring new maps. Independent of Home Assistant; see "Map Editor" below.

---

## Repository Structure

```
rowenta_roboeye/
├── custom_components/rowenta_roboeye/   # Integration source
│   ├── __init__.py          # Entry point: setup, teardown, dashboard init, frontend registration
│   ├── api.py               # Async HTTP client for RobEye REST API
│   ├── config_flow.py       # Config + options flows (UI setup wizard)
│   ├── const.py             # Constants, API endpoint paths, timing values, data keys
│   ├── coordinator.py       # DataUpdateCoordinator — multi-endpoint polling hub
│   ├── dashboard.py         # Auto-generates Lovelace dashboard; RobEyeDashboardManager
│   ├── entity.py            # Shared base entity + entity-registry housekeeping helpers
│   ├── vacuum.py            # StateVacuumEntity — main control entity; registers clean_room + remove_queue_entry services
│   ├── sensor.py            # Static + per-room sensors + live_map, schedule, command-queue, selected-room-count sensors
│   ├── binary_sensor.py     # side_brush_left_stuck, side_brush_right_stuck, dustbin present
│   ├── button.py            # Clean All, Stop, Return Home, Clean Selected, per-room clean buttons
│   ├── select.py            # Global + per-room fan speed, strategy, active-map selectors
│   ├── switch.py            # Global/per-room deep clean, per-room selection, per-task schedule switches
│   ├── manifest.json        # Integration metadata, HA version floor, dependencies
│   ├── strings.json         # UI label definitions
│   ├── services.yaml        # Service schema (clean_room; remove_queue_entry is registered in vacuum.py but not listed here)
│   ├── icons.json           # Entity icon mappings
│   ├── translations/en.json # English UI translations
│   ├── brand/               # HACS brand assets (icon.png, icon@2x.png, icon_dark.png,
│   │                        #   icon_dark@2x.png, logo.png, logo@2x.png,
│   │                        #   logo_dark.png, logo_dark@2x.png)
│   └── frontend/
│       ├── __init__.py      # Lovelace JS resource registration (JSModuleRegistration)
│       └── rowenta-map-card.js  # Custom SVG live-map Lovelace card
├── tests/
│   ├── __init__.py          # Package marker
│   ├── conftest.py          # Mock payloads and fixtures used by all tests
│   ├── test_api.py          # API client unit tests
│   ├── test_binary_sensor.py# Binary sensor entity tests
│   ├── test_button.py       # Button entity tests
│   ├── test_config_flow.py  # Config/options flow tests
│   ├── test_coordinator.py  # Coordinator data-merge and polling tests
│   ├── test_dashboard_entity_guard.py  # Dashboard entity-readiness guard tests
│   ├── test_editor_server.py           # Map-editor proxy server IP-validation tests
│   ├── test_init.py                    # async_setup/unload + init wiring helper tests
│   ├── test_map_switch_atomic.py       # Snapshot-model map-switch + areas-cache tests
│   ├── test_notifications_and_events.py# Event-log processing + brush/dustbin notification tests
│   ├── test_select.py       # Select entity tests
│   ├── test_sensor.py       # Sensor entity tests
│   ├── test_sensor_values_and_path.py  # sensor_values GPIO parsing + live-map path accumulation tests
│   ├── test_stale_entity_removal.py    # Stale/orphaned entity removal tests
│   ├── test_switch.py       # Switch entity tests
│   └── test_vacuum.py       # Vacuum entity tests
├── map_editor/              # Standalone browser map editor (see "Map Editor" section)
│   ├── rowenta-editor-server.py     # Stdlib HTTP proxy: serves editor + proxies /get,/set to robot:8080
│   ├── launch-rowenta-editor.py     # Tkinter (or CLI) launcher that spawns the proxy server
│   ├── rowenta-map-editor.html      # Editor single-page app shell
│   ├── rowenta-map-editor.css       # Editor styles
│   ├── map_editor.md                # (currently empty)
│   ├── js/                          # ES-module frontend (api, state, render, split, merge, nogo, explore, …)
│   └── android/                     # Android WebView wrapper (OUT OF SCOPE — not documented here)
├── conftest.py              # Root-level pytest stub for homeassistant package
├── pytest.ini               # asyncio_default_fixture_loop_scope = function
├── requirements-test.txt    # Test dependencies: aiohttp, pytest, pytest-asyncio
├── .github/workflows/
│   ├── release.yml          # CI/CD: GitHub Release creation on tag push or main push
│   └── validate.yml         # CI: runs pytest + hassfest on every push/PR
├── CHANGELOG.md             # Version history
├── brand/                   # Root-level brand assets (icon.png, logo.png)
├── icon.png                 # Root icon (HACS detection)
├── logo.png                 # Root logo (HACS detection)
├── hacs.json                # HACS metadata
├── README.md                # End-user documentation
└── LICENSE                  # MIT
```

---

## Architecture

### Data Flow

```
Robot Vacuum (LAN:8080)
        │
        ▼ HTTP polling (fresh TCP conn per request; force_close=True)
   RobEyeApiClient (api.py)
        │
        ▼ merged results
   RobEyeCoordinator (coordinator.py)
        │ DataUpdateCoordinator
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
   Entity platforms                            dashboard.py
   (vacuum, sensor, binary_sensor,            (Lovelace auto-gen + retry)
    button, select, switch)
        │
        ▼
   Home Assistant state machine
```

### Key Design Patterns

1. **Multi-bucket polling**: The coordinator calls API endpoints at different frequencies and merges all results into one `coordinator.data` dict. See "Polling Schedule" below.

2. **Adaptive poll interval**: The base coordinator interval is 5 s during active cleaning, 15 s when idle. This is set dynamically inside `_async_update_data` based on the current mode.

3. **Dynamic entity discovery**: Room-based entities (per-room sensor, button, select, switch) are created at setup time for **all** known permanent maps via `async_load_all_map_areas()`. The background refresh (`_async_background_refresh`) later diffs `AreaSnapshot` per map and fires `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"` (with the changed `map_id`) when room structure changes. Platform listeners add new entities without a full integration reload. Map add/delete fires `f"{SIGNAL_MAPS_UPDATED}_{entry_id}"` with `{"added": set, "removed": set}`.

4. **Per-map entity tracking**: All dynamic room entities are stored in a `known_entities_by_map: dict[str, dict]` closure keyed by `map_id → {area_id → entities}`. Entities for inactive maps stay registered in HA but show as unavailable. This avoids the async_remove/async_add race that caused duplicate entity ID errors when switching maps.

5. **Stable device ID**: `coordinator.device_id` resolves from `CONF_SERIAL` (stored at config-flow time) → live `robot_info.serial_number` → `entry_id` fallback. Once resolved it never changes, ensuring `unique_id` stability across restarts. Room entity unique_ids embed both `map_id` and `device_id`.

6. **Live map pipeline**: During cleaning the coordinator polls `/get/rob_pose`, `/get/seen_polygon`, and `/get/cleaning_grid_map` at 5 s. It accumulates a path list (up to 2000 points, de-duplicated by 5-unit distance threshold) and builds a `DATA_LIVE_MAP` payload for the SVG card. Between sessions, the last completed grid + path is frozen and replayed.

7. **Session lifecycle tracking**: The coordinator detects cleaning start (mode transitions to `cleaning`) and end (mode leaves `cleaning`/`go_home` while not active). On session end it freezes the grid, path, and outline for replay.

8. **Dashboard auto-generation**: On entry setup, `__init__.py` launches `_async_initial_dashboard` in the background. It retries up to 5 times with delays `(0, 2, 5, 15, 30)` s. On failure it fires a persistent notification asking the user to restart. `RobEyeDashboardManager` holds the hash and dashboard reference; `async_create_dashboard` is a no-op when config hasn't changed (SHA-256 dedup). The dashboard is hidden (sidebar disabled) when the entry is disabled, and shown again when it is re-enabled. A `_room_entities_registered()` guard defers the save until all 9 per-room entity types (4 sensors, 1 button, 2 selects, 1 deep-clean switch, 1 selection switch) exist in `hass.states` — prevents "unavailable" cards on map switch.

9. **Frontend JS resource**: `rowenta-map-card.js` is served as a static file and registered as a Lovelace resource via `JSModuleRegistration`. Version-based cache-busting ensures clients reload on update.

10. **Persistent brush notifications**: When sensor_values reports a side brush as stuck (`active`), the coordinator fires a `persistent_notification`. It clears the notified flag when the brush returns to normal to avoid duplicate alerts.

11. **Force-close HTTP connections**: `RobEyeApiClient._get()` creates a new `aiohttp.TCPConnector(force_close=True)` for every request. This prevents HA from holding a keep-alive connection that would block the Rowenta mobile app from reaching the robot's embedded HTTP server.

12. **Serial command queue**: All robot commands are dispatched through an `asyncio.PriorityQueue` (`_command_queue`) serialised by a background worker (`_command_queue_worker`). Priority 0 = immediate (stop/go_home/resume); priority 1 = normal. The worker captures `cmd_id` from each `/set/` response and polls `/get/command_result` to confirm completion before dispatching the next item. This prevents command collisions when multiple entities issue requests in quick succession. `IMMEDIATE_COMMAND_NAMES = frozenset({"modify_area", "set_fan_speed"})` bypasses the queue entirely — these write settings to the saved map and must not be delayed by pending clean jobs.

13. **Pause/resume lifecycle**: When `stop()` is called mid-clean the coordinator sets `_is_paused = True`, drains pending queue jobs into `_paused_jobs`, and snapshots the interrupted command as `_paused_clean_command`. On `clean_start_or_continue()` the paused jobs are re-enqueued and the snapshot is cleared. `go_home()` discards the snapshot entirely.

14. **Multi-map support (snapshot model)**: At setup time `async_load_all_map_areas()` pre-fetches `/get/areas` for every permanent map. Areas are stored in `_areas_snapshot: dict[str, AreaSnapshot]` keyed by map ID. `async_set_active_map()` is a pure local operation — it flips `_active_map_id`, calls `async_update_listeners()` to flip entity availability instantly, and fires `SIGNAL_ACTIVE_MAP_CHANGED`. No network calls are made on switch. The device's own `/get/map_status` is intentionally ignored. A uniform background refresh (`SCAN_INTERVAL_BACKGROUND = 600 s`, paused during cleaning) keeps all maps' snapshots current and fires `SIGNAL_AREAS_UPDATED` only on structural changes. A cleaning→idle edge triggers an immediate refresh so per-clean statistics propagate promptly.

15. **AreaSnapshot**: `AreaSnapshot` is a frozen dataclass with structural equality — two snapshots are equal iff `area_ids` and room `names` match. Statistics-only updates (e.g. `cleaning_counter`) compare equal and do not trigger dashboard rebuilds. The `blob` field carries the raw API response but is excluded from equality. `coordinator.areas_for(map_id)` returns the area list for any known map; returns `[]` if no snapshot exists.

16. **Entity availability for per-map entities**: Per-map entities override `available` to return `self._map_id == self.coordinator.active_map_id and super().available`. There is no longer a grace-period or `map_available_for()` helper — all maps' entities are pre-created and availability flips instantly on switch. `async_enable_all_room_entities()` in `entity.py` is a one-time migration helper that re-enables any entities disabled by the old disable/enable model.

17. **Entity registry housekeeping** (entity.py): Three cleanup functions keep the registry clean:
    - `async_remove_stale_room_entities()`: Removes entities for area IDs that no longer exist on the active map (e.g. after room redraw). Guards against empty area lists (transient network failures).
    - `async_remove_entities_for_deleted_maps()`: Removes entities for maps that were deleted from the device. Called when `SIGNAL_MAPS_UPDATED` fires.
    - `async_remove_duplicate_room_entities()`: Removes orphaned registry entries with old `device_id`-based unique_ids when `CONF_SERIAL` becomes available after a first-boot fallback to `entry_id`.

18. **Room selection toggles**: Per-room `RobEyeRoomSelectSwitch` entities (in switch.py) allow the user to select/deselect rooms for the "Clean Selected Rooms" command. Each entity ID follows the pattern `switch.{device_id}_map{map_id}_room_{area_id}_selected` (generated by `room_selection_entity_id()` in const.py). `SIGNAL_ROOM_SELECTION_CHANGED` is fired on every toggle so `RobEyeCleanSelectedButton` can update its availability immediately.

19. **Cleaning strategy**: Global `RobEyeStrategySelect` (4 modes: Default/Normal/Walls & Corners/Deep). Per-room `RobEyeRoomStrategySelect` stores desired strategy per room (no Deep option — use per-room deep-clean switch). Global `RobEyeDeepCleanSwitch` is kept for backwards compatibility. `coordinator.cleaning_strategy` and `coordinator.last_non_deep_strategy` track state. **Two distinct firmware parameters exist** — do not conflate them:
    - **Clean commands** (`/set/clean_all`, `/set/clean_map`) take a `cleaning_strategy_mode` query param carrying the **numeric** `STRATEGY_*` value (`"1"`–`"4"`). `clean_all()`/`clean_map()` in `api.py` map their `strategy_mode` argument onto this param.
    - **`/set/modify_area`** (per-room settings write) takes a `strategy_mode` param that accepts **only `"normal"` or `"deep"`**; Default/Normal/Walls & Corners must all be sent as `"normal"`.

20. **Incremental event log**: `/get/event_log` is polled every 30 s with a cursor (`_last_event_log_id`). The first fetch seeds the cursor without surfacing historical entries. Subsequent fetches deliver only new events; `_process_new_events` maps them to HA logbook entries and persistent notifications using the `EVENT_TYPE_*` constants in `const.py`.

21. **Recharge-and-continue**: During firmware-initiated recharge cycles (mode=`cleaning` + charging=`charging`), the command worker waits up to `MODE_RECHARGE_CONTINUE_WAIT_S` (3 hours) polling every `MODE_RECHARGE_CONTINUE_POLL_S` (30 s) before giving up. This prevents the queue from treating a mid-clean charge as a stalled command.

22. **Schedule enable/disable**: `RobEyeScheduleSwitch` entities bypass the serial command queue — `api.set_schedule_enabled()` is called directly (GET /set/modify_scheduled_task), followed by `async_request_refresh()`. Settings writes must not be delayed by pending motion commands.

---

## Polling Schedule

All intervals are managed internally by timestamps in `_async_update_data`. The coordinator's `update_interval` only controls how often `_async_update_data` is called.

| Endpoint(s) | Interval | Notes |
|---|---|---|
| `/get/status` | Every tick (5 s cleaning / 15 s idle) | Always called; drives mode-based interval change |
| `/get/sensor_values` | Every tick | Parses GPIO for brush stuck + dustbin flags |
| `/get/rob_pose` | Every tick when `live_map` entity enabled | Staleness detected via `timestamp` field |
| `/get/seen_polygon`, `/get/cleaning_grid_map` (live) | 5 s when cleaning, 60 s idle | Live map polygon only during active session |
| `/get/maps`, `/get/areas` (for every known permanent map) | Every 600 s (`SCAN_INTERVAL_BACKGROUND`), paused during cleaning; forced once on cleaning→idle edge | Snapshot model: `_async_background_refresh` diffs `AreaSnapshot` per map and fires `SIGNAL_AREAS_UPDATED` only on structural change |
| `/get/sensor_status`, `/get/robot_flags`, `/get/map_status` | Every 600 s | Sensor health and map metadata |
| `/get/statistics`, `/get/permanent_statistics` | Every 600 s | Lifetime totals |
| `/get/feature_map`, `/get/tile_map`, `/get/areas` (saved map), `/get/seen_polygon` (saved map), `/get/cleaning_grid_map` (saved map) | Every 600 s | Map geometry for SVG card; also loaded at startup |
| `/get/schedule` | Every 60 s | Cleaning schedule |
| `/get/event_log` | Every 30 s | Incremental robot event log (cursor-based); seeds `DATA_EVENT_LOG` |
| `/get/robot_id`, `/get/wifi_status`, `/get/protocol_version` | Every 3600 s | Device identity; stored under `DATA_ROBOT_INFO` |

---

## Key Files to Understand First

| File | Why |
|------|-----|
| `const.py` | All API endpoints, timing constants, data keys, mode/charging/fan-speed/strategy strings |
| `coordinator.py` | The heart of the integration — understand before touching any entity |
| `api.py` | Network layer — all HTTP calls go here; `CannotConnect` exception |
| `entity.py` | Base class all entities inherit from; entity-registry housekeeping helpers |
| `tests/conftest.py` | Mock payloads and HA stub used by all tests |

---

## Development Conventions

### Python Style

- All I/O is async (`async def`, `await`). Never introduce blocking calls.
- Follow existing naming: `async_setup_entry`, `async_update_data`, etc.
- Use `_LOGGER = logging.getLogger(__name__)` in every module (or `LOGGER` from `const.py` in integration modules).
- Type hints are used selectively — match the existing style in each file (don't add blanket annotations).
- No external Python dependencies beyond what Home Assistant bundles (`aiohttp`, `voluptuous`). Do not add `requirements.txt` or entries to `manifest.json` `requirements` without confirming the package is already bundled with HA.

### Entity Conventions

- All entities inherit `RobEyeEntity` from `entity.py`.
- `unique_id` must be stable and globally unique: `f"{suffix}_{coordinator.device_id}"` for global entities; room entities embed both `map_id` and `device_id` (e.g. `f"room_fan_speed_map{map_id}_{area_id}_{device_id}"`).
- `device_info` is set on the base class (`RobEyeEntity.__init__`) — do not override it per-entity.
- Per-map entities must override `available` to `return self._map_id == self.coordinator.active_map_id and super().available` so they go unavailable when a different map is active. Do **not** call the removed `coordinator.map_available_for()` helper.
- Use `coordinator.data.get(KEY)` defensively; data keys may be absent if an API call failed on a particular cycle.
- `available` property must return `False` when coordinator data is stale (inherits from `CoordinatorEntity`).

### API Client (`api.py`)

- All new API calls go in `api.py` as methods on `RobEyeApiClient`.
- Use `self._get(endpoint, params=...)` — it creates a fresh connection per request, handles timeouts, JSON parsing, and raises `CannotConnect` on errors.
- Never call `aiohttp` directly in coordinator or entity code.
- `CannotConnect` is caught by the coordinator; methods should let it propagate (do not return `None` from `_get`-based methods unless you handle the exception yourself in the method body).
- `/get/rooms` is a dead endpoint on Xplorer 120 firmware — `get_rooms()` raises `NotImplementedError`. Use `/get/areas` exclusively.
- Schedule changes use `set_schedule_enabled(task_id, enabled)` — do NOT route through `coordinator.async_send_command()`; call directly and then call `async_request_refresh()`.

### Coordinator (`coordinator.py`)

- Add new API endpoints to `_async_update_data` with the appropriate timing bucket.
- Wrap optional/diagnostic endpoints in `try/except CannotConnect` so one missing endpoint does not fail the whole update.
- When adding new data keys, add a matching constant to `const.py` in the `DATA_*` section and document what it contains with a comment.
- Room discovery is handled by `_async_background_refresh`; it diffs `AreaSnapshot` per map and fires `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"` with the `map_id` string only on structural changes (area IDs or names changed). Do not manually track `_known_area_ids`.
- Map add/delete fires `f"{SIGNAL_MAPS_UPDATED}_{entry_id}"` with a `{"added": set, "removed": set}` dict.
- User map switch fires `f"{SIGNAL_ACTIVE_MAP_CHANGED}_{entry_id}"` with the new `map_id` string. Listen to this in `__init__.py` to rebuild the dashboard; do not rebuild in platform files.
- Read room areas via `coordinator.areas_for(map_id)` — returns `[]` if no snapshot; never access `coordinator._areas_snapshot` directly from entity code.
- After `MAX_POLL_FAILURES` consecutive failures the coordinator logs a warning; it raises `UpdateFailed` on every failure regardless.
- Convenience properties (`status`, `statistics`, `areas`, `robot_info`, `available_maps`, etc.) on the coordinator are the preferred way for entities to read frequently-accessed sub-keys.
- `coordinator.device_id` is always stable; use it for all `unique_id` construction.
- `coordinator.active_map_id` is the single source of truth for the displayed map. `coordinator.committed_active_map_id` is a deprecated alias — use `active_map_id` for all new code.

### Adding a New Entity Platform

1. Create `<platform>.py` following the pattern of `sensor.py` or `button.py`.
2. Add the `Platform.<NAME>` value to `PLATFORMS` in `const.py` and list it in `manifest.json` (no separate `"platforms"` key — HA discovers them from the `PLATFORMS` constant).
3. Add translations to `translations/en.json` and `strings.json`.
4. Add icon mappings to `icons.json`.
5. Write tests in `tests/test_<platform>.py`.

### Config Flow

- All user-facing strings must reference keys from `strings.json` — never hardcode UI text.
- Validate connection via `api.test_connection()` (calls `get_status()`) before accepting the config entry.
- Options flow allows updating `host` (IP) and `name` without full re-setup; stable IDs (`CONF_SERIAL`, internal `_device_id`, `CONF_MAP_ID`, `CONF_LAST_ACTIVE_MAP`) are preserved across the update.
- Config entry stores `CONF_HOST` (IP string), `CONF_HOSTNAME` (mDNS hostname or same IP as fallback), `CONF_MAP_ID`, `CONF_LAST_ACTIVE_MAP` (last map chosen via the active-map select; persisted silently so the prior map is restored on restart), and `CONF_SERIAL` (fetched from robot at setup time; used to build stable `device_id`). `CONF_NAME` stores the user-provided friendly name. `CONF_HOST` is imported from `homeassistant.const`; the others live in `const.py`.

---

## Coordinator Data Keys (from `const.py`)

| Constant | Key string | Source endpoint | Notes |
|---|---|---|---|
| `DATA_STATUS` | `"status"` | `/get/status` | battery, mode, charging, fan speed |
| `DATA_SENSOR_VALUES` | `"sensor_values"` | `/get/sensor_values` | Raw ADC; parsed into `"sensor_values_parsed"` |
| `DATA_LIVE_PARAMETERS` | `"live_parameters"` | `/get/live_parameters` | Real-time cleaning metrics (area, time) |
| `DATA_STATISTICS` | `"statistics"` | `/get/statistics` | Lifetime totals |
| `DATA_PERMANENT_STATISTICS` | `"permanent_statistics"` | `/get/permanent_statistics` | Alternate lifetime stats |
| `DATA_AREAS` | `"areas"` | `/get/areas?map_id=X` | Room list; triggers dynamic entity discovery |
| `DATA_ROOMS` | `"rooms"` | `/get/rooms` | Dead endpoint on Xplorer 120 — not polled; use `DATA_AREAS` |
| `DATA_ROBOT_INFO` | `"robot_info"` | `/get/robot_id` + `/get/wifi_status` + `/get/protocol_version` | Dict of dicts |
| `DATA_SENSOR_STATUS` | `"sensor_status"` | `/get/sensor_status` | Cliff/bump/wheel-drop health |
| `DATA_ROBOT_FLAGS` | `"robot_flags"` | `/get/robot_flags` | Capability bitmask |
| `DATA_ROB_POSE` | `"rob_pose"` | `/get/rob_pose` | Real-time position; works in all states |
| `DATA_SEEN_POLYGON` | `"seen_polygon"` | `/get/seen_polygon` | Live explored boundary |
| `DATA_CLEANING_GRID` | `"cleaning_grid_map"` | `/get/cleaning_grid_map` | Live occupancy grid |
| `DATA_FEATURE_MAP` | `"feature_map"` | `/get/feature_map?map_id=X` | Wall lines + dock pose |
| `DATA_TILE_MAP` | `"tile_map"` | `/get/tile_map?map_id=X` | Area IDs + outline polygon |
| `DATA_TOPO_MAP` | `"topo_map"` | `/get/topo_map?map_id=X` | Topology map |
| `DATA_AREAS_SAVED_MAP` | `"areas_saved_map"` | `/get/areas?map_id=SAVED_MAP_ID` | Saved-map room geometry |
| `DATA_SEEN_POLY_SAVED_MAP` | `"seen_poly_saved_map"` | `/get/seen_polygon?map_id=SAVED_MAP_ID` | Saved map explored boundary |
| `DATA_LIVE_MAP` | `"live_map"` | Assembled by coordinator | Combined payload for SVG card |
| `DATA_SCHEDULE` | `"schedule"` | `/get/schedule` | Cleaning schedule |
| `DATA_MAP_STATUS` | `"map_status"` | `/get/map_status` | Active map metadata |
| `DATA_MAPS` | `"maps"` | `/get/maps` | Full list of available floor maps |
| `DATA_ACTIVE_MAP_ID` | `"active_map_id"` | Resolved by coordinator | HA-selected map ID (not device-reported) |
| `DATA_EXPLORATION` | `"exploration"` | `/debug/exploration` | Debug exploration points (not for runtime use) |
| `DATA_RELOCALIZATION` | `"relocalization"` | `/debug/relocalization` | Debug relocalization data (not for runtime use) |
| `DATA_EVENT_LOG` | `"event_log"` | `/get/event_log` | Incremental robot event list (last 50 entries) |

---

## Key Constants (from `const.py`)

### Cleaning Strategy

```python
STRATEGY_DEFAULT       = "4"   # robot chooses automatically
STRATEGY_NORMAL        = "1"
STRATEGY_WALLS_CORNERS = "2"
STRATEGY_DEEP          = "3"   # double/triple pass

STRATEGY_LABELS        # dict: API value → display label
STRATEGY_REVERSE_MAP   # dict: display label → API value
STRATEGY_OPTIONS       # list of human labels (excludes Deep — Deep is via the switch)
```

The firmware only accepts `"normal"` or `"deep"` for the `strategy_mode` parameter in `/set/modify_area`. Default and Walls & Corners both map to `"normal"` in API calls.

### Fan Speed

```python
FAN_SPEED_MAP          # {"1": "normal", "2": "eco", "3": "high", "4": "silent"}
FAN_SPEED_REVERSE_MAP  # inverse of FAN_SPEED_MAP
FAN_SPEEDS             # list of human labels
FAN_SPEED_LABELS       # {0: "default", 1: "normal", ...}  — 0 = per-room default
```

### Area / Room State

```python
AREA_STATE_CLEAN    = "clean"
AREA_STATE_INACTIVE = "inactive"
AREA_STATE_BLOCKING = "blocking"   # no-go / avoidance zone — not a cleanable room
AREA_TYPE_ROOM      = "room"
AREA_TYPE_AVOIDANCE = "to_be_cleaned"

# States for which NO HA entities are created (avoidance + inactive segments):
AREA_STATES_SKIP    = frozenset({AREA_STATE_BLOCKING, AREA_STATE_INACTIVE})
```

Skip entity creation for areas whose `area_state` is in `AREA_STATES_SKIP` (i.e. `blocking` **or** `inactive`).

### Event Types

`EVENT_TYPE_*` integer constants map to robot event type_ids (e.g. `EVENT_TYPE_CLEAN_MAP_STARTED = 1110`). `EVENT_TYPE_LABELS` provides human-readable strings for logbook entries.

### Signals

```python
SIGNAL_AREAS_UPDATED           # f"{DOMAIN}_areas_updated"          — fired with map_id when snapshot diff detects structural room change
SIGNAL_MAPS_UPDATED            # f"{DOMAIN}_maps_updated"           — fired with {"added": set, "removed": set} on permanent-map add/delete
SIGNAL_ACTIVE_MAP_CHANGED      # f"{DOMAIN}_active_map_changed"     — fired with new map_id when user switches maps (triggers dashboard rebuild)
SIGNAL_ROOM_SELECTION_CHANGED  # f"{DOMAIN}_room_selection_changed" — fired on selection toggle
```

All signals are scoped per config entry: `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"`.

### Room Selection

```python
def room_selection_entity_id(device_id: str, map_id: str, area_id: str) -> str:
    """Returns: switch.{device_id}_map{map_id}_room_{area_id}_selected"""
```

Used by `RobEyeRoomSelectSwitch`, `RobEyeCleanSelectedButton`, and the dashboard.

### Command Queue Bypass

```python
IMMEDIATE_COMMAND_NAMES = frozenset({"modify_area", "set_fan_speed"})
```

Commands in this set are dispatched immediately without waiting in the serial queue.

### Services

Both services are **entity services** registered on the vacuum entity in `vacuum.py`
via `platform.async_register_entity_service()` — they are not registered in `__init__.py`.

```python
SERVICE_CLEAN_ROOM         = "clean_room"          # handler: RobEyeVacuumEntity._async_clean_room
SERVICE_REMOVE_QUEUE_ENTRY = "remove_queue_entry"  # handler: RobEyeVacuumEntity._async_remove_queue_entry
```

- `clean_room` — clean one or more rooms by area ID. Fields: `room_ids` (required list),
  `fan_speed` (optional), `deep_clean` (optional bool). Documented in `services.yaml`.
- `remove_queue_entry` — drop a pending command from the HA-side command queue.
  Field: `pending_index` (default 0). **Not currently described in `services.yaml`** — add
  a schema entry there if you touch this.

---

## Entity Registry Helpers (entity.py)

These functions keep the HA entity registry clean:

| Function | When to call |
|---|---|
| `async_remove_stale_room_entities(hass, entry, coordinator, platform, current_ids)` | In `_async_on_areas_updated` when `map_id == coordinator.active_map_id`. Guards against empty `current_ids` (transient failures). |
| `async_remove_entities_for_deleted_maps(hass, entry, platform, deleted_map_ids)` | In `_async_on_maps_updated` handler. Returns removed `(map_id, area_id)` tuples. |
| `async_remove_duplicate_room_entities(hass, entry, platform, canonical_uids)` | At platform setup time, after the initial entities are built. Removes orphaned registry entries from prior runs with a different `device_id`. |
| `find_room_registry_records(hass, entry, platform)` | To re-claim inactive-map entities from the registry at setup (builds stub entities for maps not currently active). |
| `pick_room_name_from_records(records, suffixes)` | Recovers room name from registry `original_name` when areas data is unavailable. |

---

## Testing

### Running Tests

```bash
# Install test dependencies
pip install -r requirements-test.txt
# or manually:
pip install pytest pytest-asyncio aiohttp

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_coordinator.py -v

# Run tests matching a pattern
pytest tests/ -k "test_api" -v
```

### Test Architecture

The root `conftest.py` stubs out the entire `homeassistant` package so tests run without a full HA installation. Do not import real Home Assistant classes in tests — use the stubs provided.

`pytest.ini` sets `asyncio_default_fixture_loop_scope = function` — every async test gets a fresh event loop.

Mock API payloads are defined in `tests/conftest.py` as module-level dicts (not pytest fixtures):
- `MOCK_STATUS` — `/get/status` response
- `MOCK_STATISTICS` — `/get/statistics` response
- `MOCK_PERMANENT_STATISTICS` — `/get/permanent_statistics` response
- `MOCK_AREAS` — `/get/areas` response (includes 2 named rooms + 1 with empty metadata)
- `MOCK_WIFI_STATUS`, `MOCK_ROBOT_ID`, `MOCK_PROTOCOL_VERSION` — identity endpoints
- `MOCK_LIVE_PARAMETERS`, `MOCK_SENSOR_STATUS`, `MOCK_ROBOT_FLAGS`, `MOCK_MAP_STATUS` — diagnostics
- `MOCK_MAPS` — `/get/maps` full map list response
- `MOCK_ROB_POSE` — `/get/rob_pose` robot position response
- `MOCK_SENSOR_VALUES` — `/get/sensor_values` ADC data
- `MOCK_CLEANING_GRID` — `/get/cleaning_grid_map` occupancy grid
- `MOCK_LOCALIZATION`, `MOCK_RELOCALIZATION`, `MOCK_EXPLORATION` — debug localization

Reuse these; do not invent new payload structures in individual test files.

### Writing Tests

- Use `pytest-asyncio` with `@pytest.mark.asyncio` for all async tests.
- Mock `RobEyeApiClient` methods using `unittest.mock.AsyncMock`.
- Test error paths: `CannotConnect` raised from API methods, missing keys in coordinator data, `UpdateFailed` exceptions.
- Dynamic entity tests should simulate the `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"` dispatch.
- Entity registry tests should mock `er.async_get` and `er.async_entries_for_config_entry` (see `test_stale_entity_removal.py`).
- Dashboard tests should set `manager._ENTITY_POLL_INTERVAL_S` / `manager._ENTITY_POLL_TIMEOUT_S` to millisecond values so entity-readiness polls don't block real time (see `test_dashboard_entity_guard.py`).

### CI

Two workflows run on every push/PR:
- **validate.yml**: `pytest tests/` (Python 3.12) + `hassfest` integration validation.
- **release.yml**: Creates a git tag and GitHub Release zip when a new version is pushed to `main`.

---

## Release Process

Releases are automated via `.github/workflows/release.yml`:

1. Update `"version"` in `custom_components/rowenta_roboeye/manifest.json` (also update `VERSION` in `const.py` to keep them in sync).
2. Commit and push to `main`.
3. The workflow reads the version, creates a git tag (`v<version>`), and publishes a GitHub Release with a zip of `custom_components/`. If the tag already exists it skips release creation (idempotent).

Do not manually create tags or releases — let CI handle it.

---

## Common Tasks

### Adding a New Sensor

1. Define a `DATA_*` constant for the new data key in `const.py`.
2. Add the API method to `api.py` if needed.
3. Merge the result in `coordinator.py` `_async_update_data` at the appropriate interval bucket.
4. Add a `RobEyeSensorDescription` entry in `sensor.py` with a `value_fn` lambda.
5. Add translation strings to `translations/en.json` and `strings.json`.
6. Add a test in `tests/test_sensor.py`.

### Adding a New API Command

1. Add the method to `api.py` using `self._get(endpoint, params=...)`.
2. For **motion commands** (clean, stop, go_home): call via `coordinator.async_send_command()`. After issuing, the coordinator's queue worker polls `/get/command_result` and calls `async_request_refresh()` when done.
3. For **settings writes** (fan speed, schedule, modify_area): if listed in `IMMEDIATE_COMMAND_NAMES`, also use `async_send_command()` — it bypasses the queue. For truly one-shot writes (schedule enable/disable), call `api.set_schedule_enabled()` directly, then call `await coordinator.async_request_refresh()`.

### Working with Robot Position

- Use `/get/rob_pose` (stored as `DATA_ROB_POSE`). This endpoint works in all robot states (docked, cleaning, returning home).
- Coordinates: `x1` and `y1` in API units where **1 unit = 2 mm**. `heading` is in degrees (0–360).
- `valid: false` means the robot has no position fix. `is_tentative: true` means the position is a rough estimate.
- `timestamp` is a monotonic uptime counter; an unchanged timestamp during cleaning means the position is stale.
- Do NOT use `/debug/localization` or `/debug/relocalization` for live tracking — `/get/rob_pose` supersedes both.

### Working with Cleaning Strategy

- Global strategy: read `coordinator.cleaning_strategy`; write via `RobEyeStrategySelect` or `RobEyeDeepCleanSwitch`.
- Per-room strategy: stored in `RobEyeRoomStrategySelect._selected` (restored via `RestoreEntity`). When a clean is launched for a room, read from the strategy select entity's HA state rather than `coordinator.areas` cache to get the user's latest choice.
- The firmware's `strategy_mode` parameter accepts only `"normal"` or `"deep"`. Map: Default/Normal/Walls & Corners → `"normal"`, Deep → `"deep"`.
- Always include both `cleaning_parameter_set` and `strategy_mode` in `/set/modify_area` calls — omitting one resets the other to firmware default.

### Working with Maps

- `coordinator.active_map_id` — the map HA is using (user-selected). Never read `DATA_MAP_STATUS` to determine active map.
- `coordinator.active_map_id` — the single source of truth for which floor HA is displaying. Never use the removed `areas_map_id` or `map_available_for()`.
- `coordinator.areas_for(map_id)` — areas list for any known map (active or inactive). Returns `[]` if no snapshot for that map yet. Use this instead of `coordinator.data[DATA_AREAS]` in entity code.
- `coordinator.available_maps` — list of permanent maps with display names (position-based `"Map N"` if unnamed).
- To switch maps programmatically: call `await coordinator.async_set_active_map(map_id)`. This is a pure local operation; background refresh keeps areas current on its own 600 s cadence.

### Debugging a Robot Integration

- Enable debug logging: add `custom_components.rowenta_roboeye: debug` to HA logger config.
- Check `coordinator.data` in HA Developer Tools > Template to inspect raw merged state.
- The vacuum's web UI is at `http://<robot-ip>:8080` for direct API inspection.

---

## Map Editor (`map_editor/`)

A **standalone, browser-based floor-map editor** for the same robots. It is fully
independent of the Home Assistant integration — it shares no Python code with
`custom_components/` and can run with HA stopped. It talks to the robot's RobEye
API (port 8080) directly, through its own Python proxy. Use it to split/merge
rooms, draw no-go ("blocking") and spot areas, reposition zones, rename rooms,
explore/build new maps, and save maps back to the device.

> **Scope note:** `map_editor/android/` (an Android WebView wrapper) is intentionally
> **not documented here** and should be treated as out of scope for routine work.

### Components

| Path | Role |
|------|------|
| `rowenta-editor-server.py` | Stdlib-only HTTP server (`ThreadingHTTPServer`). Serves the editor HTML/CSS/JS and **proxies** `/get/*` and `/set/*` to `{robot_ip}:8080`. Hardened: private-LAN-only robot IP, DNS-rebinding + cross-origin guards when bound locally, no-redirect opener (SSRF hardening), `Cache-Control: no-store`. |
| `launch-rowenta-editor.py` | Clickable Tkinter launcher (falls back to CLI when Tk is unavailable). Spawns the server as a subprocess, streams its logs, opens the browser. |
| `rowenta-map-editor.html` / `.css` | Single-page-app shell and styles. |
| `js/*.js` | ES-module frontend (no build step, no npm). See module map below. |
| `map_editor.md` | Documentation stub — currently empty. |

### Server (`rowenta-editor-server.py`)

- **Dependencies**: Python 3.6+ standard library only (`http.server`, `urllib`,
  `json`, `ipaddress`, `threading`, `webbrowser`, `mimetypes`, `pathlib`). No third-party packages.
- **Ports**: editor/proxy on `DEFAULT_PORT = 8765`; robot on `ROBOT_PORT = 8080`.
- **Modes**: `PROXY_MODE` (default — binds loopback, `_enforce_local = True`) and
  `INGRESS_MODE` (Home Assistant add-on — binds `0.0.0.0` behind trusted HA ingress and
  strips the ingress path prefix). The add-on `run.sh` packaging is **not in this repo**.
- **Routes**: `GET /` → editor HTML; `GET|POST /config` → read/update the target robot IP
  (`{robot_ip, proxy_mode}`); `GET /get/*` and `GET /set/*` → proxied to the robot;
  static `*.css`/`*.js` from `STATIC_DIR` (with path-traversal guard); `OPTIONS` → 204.
- **`_validate_robot_ip()`** accepts only private unicast LAN addresses; rejects public,
  loopback, link-local (e.g. `169.254.169.254`), the unspecified address (`0.0.0.0`),
  multicast, and reserved ranges. This is the function covered by `tests/test_editor_server.py`.

### Frontend (`js/`)

Single shared mutable `state` object (`state.js`); no framework. Edits mutate `state.*`
and call explicit `renderMap()` / `renderAreaList()` re-renders.

| Module | Role |
|--------|------|
| `main.js` | Entry point: wires the toolbar, runs connect/init, hosts the `?test=1` self-tests. |
| `api.js` | HTTP helpers (`api()`, `apiText()`), `setProxyRobotIP()` (POST `/config`), `pollCmd()`. |
| `state.js` | Shared runtime state (maps, areas, selection, mode, robot pose, transforms). |
| `config.js` | Runtime mode detection (DIRECT/PROXY/INGRESS) + room/floor/fan/strategy enums. |
| `load.js` | `loadMaps()` / `loadMap()` / `loadLastSessionGrid()` — fetch + populate. |
| `render.js` | SVG rendering of walls, dock, area polygons; sidebar + map-chip rendering. |
| `normalize.js` | Normalizes area field names across firmware variants. |
| `coords.js` | Coordinate transforms (robot↔SVG, Y-flip) and split-line geometry. |
| `areas.js` | Area selection, detail-panel edits, `saveArea()` (POST `/set/modify_area`), block toggle. |
| `split.js` / `merge.js` | Room split / merge flows (+ locating the resulting area). |
| `nogo.js` | Draw no-go (`blocking`) and spot (`clean`) areas via `/set/add_area`. |
| `area_move.js` | Drag-to-reposition no-go/spot areas (`/set/modify_area` with new points). |
| `explore.js` | New-map exploration flow with phase bar (running→drawing→naming→saving). |
| `mapops.js` | Save / rename map, go-home, reset-stats operations. |
| `robot.js` | Live status + rob-pose polling, click-to-navigate ("go to"), proposed no-go. |
| `mode.js` / `events.js` / `modal.js` / `overlay.js` | Mode switching, input handlers/shortcuts, modal/toast UI, SVG preview overlays. |

### Run / test

```bash
# Launcher (GUI or CLI):
python3 map_editor/launch-rowenta-editor.py [robot-ip] [--port 8765] [--no-browser]
# Server directly:
python3 map_editor/rowenta-editor-server.py 192.168.1.50 --port 8765
```

- Python tests: `tests/test_editor_server.py` (IP-validation; loads the hyphenated server
  module by path). Run with the rest of the suite via `pytest tests/`.
- Frontend self-tests: open the editor with `?test=1` to run in-browser assertions.

---

## What to Avoid

- **Do not** introduce cloud API calls or external network dependencies.
- **Do not** use synchronous I/O (`requests`, `time.sleep`) anywhere in integration code.
- **Do not** hardcode entity IDs, device names, or area names — all are dynamic.
- **Do not** modify the Lovelace dashboard structure in `dashboard.py` without verifying SHA-256 hash invalidation still works.
- **Do not** skip `available` property updates — entities must go unavailable when coordinator fails or when their map is not active.
- **Do not** add Python packages to `manifest.json` `requirements` without confirming the package is not already bundled with Home Assistant.
- **Do not** push to `main` directly — open a PR or use the feature branch workflow.
- **Do not** call `/debug/localization`, `/debug/relocalization`, or `/debug/exploration` for runtime position tracking — use `/get/rob_pose` instead.
- **Do not** call `/get/rooms` — it returns `unknown_request` on Xplorer 120 firmware; `get_rooms()` raises `NotImplementedError`.
- **Do not** hold persistent `aiohttp` sessions — `_get()` creates and closes a fresh connection per request intentionally.
- **Do not** route schedule enable/disable through `coordinator.async_send_command()` — call `api.set_schedule_enabled()` directly.
- **Do not** pass only `strategy_mode` (or only `cleaning_parameter_set`) to `/set/modify_area` — always include both to prevent the firmware from resetting the omitted parameter.
- **Do not** call `async_remove_stale_room_entities()` with an empty `current_area_ids` set — this is guarded internally but the guard exists to prevent wiping all entities on transient API failures.
