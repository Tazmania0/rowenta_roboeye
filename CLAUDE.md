# CLAUDE.md — AI Assistant Guide for rowenta_roboeye

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**rowenta_roboeye** is a Home Assistant custom integration that provides local LAN control of Rowenta/Tefal X-Plorer Serie 120 robot vacuums via their native RobEye HTTP API (port 8080). There are no cloud dependencies, no Tuya protocol — pure local polling.

- **Language**: Python 3.9+, async/await throughout
- **Framework**: Home Assistant (≥ 2024.1.0)
- **Install mechanism**: HACS (Home Assistant Community Store)
- **Entity platforms**: Vacuum, Sensor, Binary Sensor, Button, Select, Switch
- **Discovery**: mDNS/Zeroconf (`_robeye._tcp.local.`) with manual IP fallback

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
│   ├── entity.py            # Shared base entity (coordinator binding, device_info)
│   ├── vacuum.py            # StateVacuumEntity — main control entity
│   ├── sensor.py            # Static + dynamic per-room sensor entities + live_map sensor
│   ├── binary_sensor.py     # side_brush_left_stuck, side_brush_right_stuck, dustbin flags
│   ├── button.py            # Clean All, Stop, Return Home, per-room clean buttons
│   ├── select.py            # Global + per-room fan speed selectors
│   ├── switch.py            # Global + per-room deep clean toggles
│   ├── manifest.json        # Integration metadata, HA version floor, dependencies
│   ├── strings.json         # UI label definitions
│   ├── services.yaml        # Service schema for clean_room
│   ├── icons.json           # Entity icon mappings
│   ├── translations/en.json # English UI translations
│   ├── brand/               # HACS brand assets (icon.png, icon@2x.png, logo.png)
│   ├── icon.png / icon_512.png / logo.png / logo_512.png  # Integration icons
│   └── frontend/
│       ├── __init__.py      # Lovelace JS resource registration (JSModuleRegistration)
│       └── rowenta-map-card.js  # Custom SVG live-map Lovelace card
├── tests/
│   ├── conftest.py          # Mock payloads and fixtures used by all tests
│   ├── test_api.py          # API client unit tests
│   ├── test_config_flow.py  # Config/options flow tests
│   ├── test_coordinator.py  # Coordinator data-merge and polling tests
│   ├── test_sensor.py       # Sensor entity tests
│   └── test_vacuum.py       # Vacuum entity tests
├── conftest.py              # Root-level pytest stub for homeassistant package
├── .github/workflows/
│   └── release.yml          # CI/CD: GitHub Release creation on tag
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

3. **Dynamic entity discovery**: Room-based entities (per-room sensor, button, select, switch) are created at runtime. The coordinator compares `_known_area_ids` after each `/get/areas` call and fires `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"` when the room set changes. Platform listeners add new entities without a full integration reload.

4. **Live map pipeline**: During cleaning the coordinator polls `/get/rob_pose`, `/get/seen_polygon`, and `/get/cleaning_grid_map` at 5 s. It accumulates a path list (up to 2000 points, de-duplicated by 5-unit distance threshold) and builds a `DATA_LIVE_MAP` payload for the SVG card. Between sessions, the last completed grid + path is frozen and replayed.

5. **Session lifecycle tracking**: The coordinator detects cleaning start (mode transitions to `cleaning`) and end (mode leaves `cleaning`/`go_home` while not active). On session end it freezes the grid, path, and outline for replay.

6. **Dashboard auto-generation**: On entry setup, `__init__.py` launches `_async_initial_dashboard` in the background. It retries up to 5 times with delays `(0, 2, 5, 15, 30)` s. On failure it fires a persistent notification asking the user to restart. `RobEyeDashboardManager` holds the hash and dashboard reference; `async_create_dashboard` is a no-op when config hasn't changed (SHA-256 dedup). The dashboard is hidden (sidebar disabled) when the entry is disabled, and shown again when it is re-enabled.

7. **Frontend JS resource**: `rowenta-map-card.js` is served as a static file and registered as a Lovelace resource via `JSModuleRegistration`. Version-based cache-busting ensures clients reload on update.

8. **Persistent brush notifications**: When sensor_values reports a side brush as stuck (`active`), the coordinator fires a `persistent_notification`. It clears the notified flag when the brush returns to normal to avoid duplicate alerts.

9. **Force-close HTTP connections**: `RobEyeApiClient._get()` creates a new `aiohttp.TCPConnector(force_close=True)` for every request. This prevents HA from holding a keep-alive connection that would block the Rowenta mobile app from reaching the robot's embedded HTTP server.

---

## Polling Schedule

All intervals are managed internally by timestamps in `_async_update_data`. The coordinator's `update_interval` only controls how often `_async_update_data` is called.

| Endpoint(s) | Interval | Notes |
|---|---|---|
| `/get/status` | Every tick (5 s cleaning / 15 s idle) | Always called; drives mode-based interval change |
| `/get/sensor_values` | Every tick | Parses GPIO for brush stuck + dustbin flags |
| `/get/rob_pose` | Every tick when `live_map` entity enabled | Staleness detected via `timestamp` field |
| `/get/seen_polygon`, `/get/cleaning_grid_map` (live) | 5 s when cleaning, 60 s idle | Live map polygon only during active session |
| `/get/areas`, `/get/sensor_status`, `/get/robot_flags` | Every 300 s | Area discovery; `_check_for_new_areas` called here |
| `/get/statistics`, `/get/permanent_statistics` | Every 600 s | Lifetime totals |
| `/get/feature_map`, `/get/tile_map`, `/get/areas` (saved map), `/get/seen_polygon` (saved map), `/get/cleaning_grid_map` (saved map) | Every 600 s | Map geometry for SVG card; also loaded at startup |
| `/get/schedule` | Every 60 s | Cleaning schedule |
| `/get/robot_id`, `/get/wifi_status`, `/get/protocol_version` | Every 3600 s | Device identity; stored under `DATA_ROBOT_INFO` |

---

## Key Files to Understand First

| File | Why |
|------|-----|
| `const.py` | All API endpoints, timing constants, data keys, mode/charging/fan-speed strings |
| `coordinator.py` | The heart of the integration — understand before touching any entity |
| `api.py` | Network layer — all HTTP calls go here; `CannotConnect` exception |
| `entity.py` | Base class all entities inherit from; device_info is set here |
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
- `unique_id` must be stable and globally unique: `f"{entry.entry_id}_{SUFFIX}"`.
- `device_info` is set on the base class (`RobEyeEntity.__init__`) — do not override it per-entity.
- Use `coordinator.data.get(KEY)` defensively; data keys may be absent if an API call failed on a particular cycle.
- `available` property must return `False` when coordinator data is stale (inherits from `CoordinatorEntity`).

### API Client (`api.py`)

- All new API calls go in `api.py` as methods on `RobEyeApiClient`.
- Use `self._get(endpoint, params=...)` — it creates a fresh connection per request, handles timeouts, JSON parsing, and raises `CannotConnect` on errors.
- Never call `aiohttp` directly in coordinator or entity code.
- `CannotConnect` is caught by the coordinator; methods should let it propagate (do not return `None` from `_get`-based methods unless you handle the exception yourself in the method body).

### Coordinator (`coordinator.py`)

- Add new API endpoints to `_async_update_data` with the appropriate timing bucket.
- Wrap optional/diagnostic endpoints in `try/except CannotConnect` so one missing endpoint does not fail the whole update.
- When adding new data keys, add a matching constant to `const.py` in the `DATA_*` section and document what it contains with a comment.
- Room discovery is tracked in `self._known_area_ids`; fire `f"{SIGNAL_AREAS_UPDATED}_{self.config_entry.entry_id}"` when the set changes.
- After `MAX_POLL_FAILURES` consecutive failures the coordinator logs a warning; it raises `UpdateFailed` on every failure regardless.
- Convenience properties (`status`, `statistics`, `areas`, `robot_info`, etc.) on the coordinator are the preferred way for entities to read frequently-accessed sub-keys.

### Adding a New Entity Platform

1. Create `<platform>.py` following the pattern of `sensor.py` or `button.py`.
2. Add the `Platform.<NAME>` value to `PLATFORMS` in `const.py` and list it in `manifest.json` (no separate `"platforms"` key — HA discovers them from the `PLATFORMS` constant).
3. Add translations to `translations/en.json` and `strings.json`.
4. Add icon mappings to `icons.json`.
5. Write tests in `tests/test_<platform>.py`.

### Config Flow

- All user-facing strings must reference keys from `strings.json` — never hardcode UI text.
- Validate connection via `api.test_connection()` (calls `get_status()`) before accepting the config entry.
- Options flow must allow updating `host` and `map_id` without full re-setup.
- Config entry stores `CONF_HOST` (IP string) and `CONF_HOSTNAME` (mDNS hostname or same IP as fallback) plus `CONF_MAP_ID`.

---

## Coordinator Data Keys (from `const.py`)

| Constant | Key string | Source endpoint | Notes |
|---|---|---|---|
| `DATA_STATUS` | `"status"` | `/get/status` | battery, mode, charging, fan speed |
| `DATA_SENSOR_VALUES` | `"sensor_values"` | `/get/sensor_values` | Raw ADC; parsed into `"sensor_values_parsed"` |
| `DATA_STATISTICS` | `"statistics"` | `/get/statistics` | Lifetime totals |
| `DATA_PERMANENT_STATISTICS` | `"permanent_statistics"` | `/get/permanent_statistics` | Alternate lifetime stats |
| `DATA_AREAS` | `"areas"` | `/get/areas?map_id=X` | Room list; triggers dynamic entity discovery |
| `DATA_ROBOT_INFO` | `"robot_info"` | `/get/robot_id` + `/get/wifi_status` + `/get/protocol_version` | Dict of dicts |
| `DATA_SENSOR_STATUS` | `"sensor_status"` | `/get/sensor_status` | Cliff/bump/wheel-drop health |
| `DATA_ROBOT_FLAGS` | `"robot_flags"` | `/get/robot_flags` | Capability bitmask |
| `DATA_ROB_POSE` | `"rob_pose"` | `/get/rob_pose` | Real-time position; works in all states |
| `DATA_SEEN_POLYGON` | `"seen_polygon"` | `/get/seen_polygon` | Live explored boundary |
| `DATA_CLEANING_GRID` | `"cleaning_grid_map"` | `/get/cleaning_grid_map` | Live occupancy grid |
| `DATA_FEATURE_MAP` | `"feature_map"` | `/get/feature_map?map_id=X` | Wall lines + dock pose |
| `DATA_TILE_MAP` | `"tile_map"` | `/get/tile_map?map_id=X` | Area IDs + outline polygon |
| `DATA_AREAS_SAVED_MAP` | `"areas_saved_map"` | `/get/areas?map_id=SAVED_MAP_ID` | Saved-map room geometry |
| `DATA_SEEN_POLY_SAVED_MAP` | `"seen_poly_saved_map"` | `/get/seen_polygon?map_id=SAVED_MAP_ID` | Saved map explored boundary |
| `DATA_LIVE_MAP` | `"live_map"` | Assembled by coordinator | Combined payload for SVG card |
| `DATA_SCHEDULE` | `"schedule"` | `/get/schedule` | Cleaning schedule |
| `DATA_MAP_STATUS` | `"map_status"` | `/get/map_status` | Active map metadata |

---

## Testing

### Running Tests

```bash
# Install test dependencies (one-time)
pip install pytest pytest-asyncio pytest-homeassistant-custom-component

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_coordinator.py -v

# Run tests matching a pattern
pytest tests/ -k "test_api" -v
```

### Test Architecture

The root `conftest.py` stubs out the entire `homeassistant` package so tests run without a full HA installation. Do not import real Home Assistant classes in tests — use the stubs provided.

Mock API payloads are defined in `tests/conftest.py` as module-level dicts (not pytest fixtures):
- `MOCK_STATUS` — `/get/status` response
- `MOCK_STATISTICS` — `/get/statistics` response
- `MOCK_PERMANENT_STATISTICS` — `/get/permanent_statistics` response
- `MOCK_AREAS` — `/get/areas` response (includes 2 named rooms + 1 with empty metadata)
- `MOCK_WIFI_STATUS`, `MOCK_ROBOT_ID`, `MOCK_PROTOCOL_VERSION` — identity endpoints
- `MOCK_LIVE_PARAMETERS`, `MOCK_SENSOR_STATUS`, `MOCK_ROBOT_FLAGS`, `MOCK_MAP_STATUS` — diagnostics
- `MOCK_LOCALIZATION`, `MOCK_RELOCALIZATION`, `MOCK_EXPLORATION` — debug localization

Reuse these; do not invent new payload structures in individual test files.

### Writing Tests

- Use `pytest-asyncio` with `@pytest.mark.asyncio` for all async tests.
- Mock `RobEyeApiClient` methods using `unittest.mock.AsyncMock`.
- Test error paths: `CannotConnect` raised from API methods, missing keys in coordinator data, `UpdateFailed` exceptions.
- Dynamic entity tests should simulate the `f"{SIGNAL_AREAS_UPDATED}_{entry_id}"` dispatch.

---

## Release Process

Releases are automated via `.github/workflows/release.yml`:

1. Update `"version"` in `custom_components/rowenta_roboeye/manifest.json` (also update `VERSION` in `const.py` to keep them in sync).
2. Commit and push to `main`.
3. The workflow creates a git tag (`v<version>`) and a GitHub Release with a zip of `custom_components/`.

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
2. Call it from the appropriate entity's `async_<action>` method.
3. After issuing a command, call `await self.coordinator.async_request_refresh()` to update state immediately.

### Working with Robot Position

- Use `/get/rob_pose` (stored as `DATA_ROB_POSE`). This endpoint works in all robot states (docked, cleaning, returning home).
- Coordinates: `x1` and `y1` in API units where **1 unit = 2 mm**. `heading` is in degrees (0–360).
- `valid: false` means the robot has no position fix. `is_tentative: true` means the position is a rough estimate.
- `timestamp` is a monotonic uptime counter; an unchanged timestamp during cleaning means the position is stale.
- Do NOT use `/debug/localization` or `/debug/relocalization` for live tracking — `/get/rob_pose` supersedes both.

### Debugging a Robot Integration

- Enable debug logging: add `custom_components.rowenta_roboeye: debug` to HA logger config.
- Check `coordinator.data` in HA Developer Tools > Template to inspect raw merged state.
- The vacuum's web UI is at `http://<robot-ip>:8080` for direct API inspection.

---

## What to Avoid

- **Do not** introduce cloud API calls or external network dependencies.
- **Do not** use synchronous I/O (`requests`, `time.sleep`) anywhere in integration code.
- **Do not** hardcode entity IDs, device names, or area names — all are dynamic.
- **Do not** modify the Lovelace dashboard structure in `dashboard.py` without verifying SHA-256 hash invalidation still works.
- **Do not** skip `available` property updates — entities must go unavailable when coordinator fails.
- **Do not** add Python packages to `manifest.json` `requirements` without confirming the package is not already bundled with Home Assistant.
- **Do not** push to `main` directly — open a PR or use the feature branch workflow.
- **Do not** call `/debug/localization`, `/debug/relocalization`, or `/debug/exploration` for runtime position tracking — use `/get/rob_pose` instead.
- **Do not** hold persistent `aiohttp` sessions — `_get()` creates and closes a fresh connection per request intentionally.
