# CLAUDE.md — AI Assistant Guide for rowenta_roboeye

This file provides guidance for AI assistants working on this codebase.

## Project Overview

**rowenta_roboeye** is a Home Assistant custom integration that provides local LAN control of Rowenta/Tefal X-Plorer Serie 120, S220, and S240 robot vacuums via their native RobEye HTTP API (port 8080). There are no cloud dependencies, no Tuya protocol — pure local polling.

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
│   ├── __init__.py          # Entry point: setup, teardown, frontend registration
│   ├── api.py               # Async HTTP client for RobEye REST API
│   ├── config_flow.py       # Config + options flows (UI setup wizard)
│   ├── const.py             # Constants, API endpoint paths, timing values
│   ├── coordinator.py       # DataUpdateCoordinator — multi-endpoint polling hub
│   ├── dashboard.py         # Auto-generates Lovelace dashboard on entry setup
│   ├── entity.py            # Shared base entity (coordinator binding)
│   ├── vacuum.py            # StateVacuumEntity — main control entity
│   ├── sensor.py            # Static + dynamic per-room sensor entities
│   ├── binary_sensor.py     # Dustbin full, brush stuck flags (diagnostic)
│   ├── button.py            # Clean All, Stop, Return Home, per-room clean buttons
│   ├── select.py            # Global + per-room fan speed selectors
│   ├── switch.py            # Global + per-room deep clean toggles
│   ├── manifest.json        # Integration metadata, HA version floor, dependencies
│   ├── strings.json         # UI label definitions
│   ├── services.yaml        # Service schema for clean_room
│   ├── icons.json           # Entity icon mappings
│   ├── translations/en.json # English UI translations
│   └── frontend/
│       ├── __init__.py      # Lovelace JS resource registration
│       └── rowenta-map-card.js  # Custom SVG live-map Lovelace card
├── tests/
│   ├── conftest.py          # Root conftest: homeassistant stub + mock payloads
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
        ▼ HTTP polling
   RobEyeApiClient (api.py)
        │
        ▼ merged results
   RobEyeCoordinator (coordinator.py)
        │ DataUpdateCoordinator
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
   Entity platforms                            dashboard.py
   (vacuum, sensor, binary_sensor,            (Lovelace auto-gen)
    button, select, switch)
        │
        ▼
   Home Assistant state machine
```

### Key Design Patterns

1. **Multi-endpoint polling**: The coordinator calls several independent API endpoints (`/get/status`, `/get/stats`, `/get/areas`, `/get/wifi`, `/get/map`) and merges them into one `coordinator.data` dict. Entities read from this merged dict.

2. **Adaptive poll intervals**: 5 s during active cleaning, 15 s when idle (constants in `const.py`).

3. **Dynamic entity discovery**: Room-based entities (per-room sensor, button, select, switch) are created at runtime after the first poll returns area data. The coordinator fires `SIGNAL_AREAS_UPDATED` to trigger platform re-scan without requiring an integration reload.

4. **Dashboard auto-generation**: On entry setup, `dashboard.py` programmatically creates a 4-view Lovelace dashboard (Control, Rooms, Statistics, Map). It uses SHA-256 hashing of the config to avoid redundant writes.

5. **Frontend JS resource**: `rowenta-map-card.js` is served as a static file and registered as a Lovelace resource. Version-based cache-busting ensures clients reload on update.

---

## Key Files to Understand First

When working on a new task, read these files to orient yourself:

| File | Why |
|------|-----|
| `const.py` | All API endpoints, timing constants, platform names |
| `coordinator.py` | The heart of the integration — understand before touching any entity |
| `api.py` | Network layer — all HTTP calls go here |
| `entity.py` | Base class all entities inherit from |
| `tests/conftest.py` | Mock payloads and fixtures used by all tests |

---

## Development Conventions

### Python Style

- All I/O is async (`async def`, `await`). Never introduce blocking calls.
- Follow existing naming: `async_setup_entry`, `async_update_data`, etc.
- Use `_LOGGER = logging.getLogger(__name__)` in every module.
- Type hints are used selectively — match the existing style in each file (don't add blanket annotations).
- No external Python dependencies beyond what Home Assistant bundles (`aiohttp`, `voluptuous`). Do not add to `requirements.txt` or create one.

### Entity Conventions

- All entities inherit `RobeyeEntity` from `entity.py`.
- `unique_id` must be stable and globally unique: `f"{entry.entry_id}_{SUFFIX}"`.
- `device_info` is set on the base class — do not override it per-entity.
- Use `coordinator.data.get(KEY)` defensively; data keys may be absent if an API call failed.
- `available` property must return `False` when coordinator data is stale.

### API Client (`api.py`)

- All new API calls go in `api.py` as methods on `RobEyeApiClient`.
- Use `self._get(endpoint)` helper — it handles timeouts, JSON parsing, and logging.
- Never call `aiohttp` directly in coordinator or entity code.
- Return `None` (not raise) on non-fatal errors so the coordinator can still merge partial data.

### Coordinator (`coordinator.py`)

- Add new API endpoints to `_async_update_data` and merge their results into the returned dict.
- When adding new data keys, document them with a comment so entities know what to expect.
- Room discovery is tracked in `self._known_areas`; fire `SIGNAL_AREAS_UPDATED` when the set changes.

### Adding a New Entity Platform

1. Create `<platform>.py` following the pattern of `sensor.py` or `button.py`.
2. Register it in `manifest.json` under `"platforms"` and in `__init__.py` `PLATFORMS` list.
3. Add translations to `translations/en.json` and `strings.json`.
4. Add icon mappings to `icons.json`.
5. Write tests in `tests/test_<platform>.py`.

### Config Flow

- All user-facing strings must reference keys from `strings.json` — never hardcode UI text.
- Validate connection (`api.get_status()`) before accepting config entry.
- Options flow must allow updating `host` and `map_id` without full re-setup.

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

The root `conftest.py` stubs out the entire `homeassistant` package so tests run without a full HA installation. Do not import real Home Assistant classes in tests — use the stubs.

Mock API payloads are defined in `tests/conftest.py` as fixtures:
- `mock_status_payload` — `/get/status` response
- `mock_stats_payload` — `/get/stats` response
- `mock_areas_payload` — `/get/areas` response

Reuse these fixtures; do not invent new payload structures in individual test files.

### Writing Tests

- Use `pytest-asyncio` with `@pytest.mark.asyncio` for all async tests.
- Mock `RobEyeApiClient` methods using `unittest.mock.AsyncMock`.
- Test error paths: `None` returns from API methods, missing keys in coordinator data, `UpdateFailed` exceptions.
- Dynamic entity tests should simulate `SIGNAL_AREAS_UPDATED` dispatch.

---

## Release Process

Releases are automated via `.github/workflows/release.yml`:

1. Update `"version"` in `custom_components/rowenta_roboeye/manifest.json`.
2. Commit and push to `main`.
3. The workflow creates a git tag (`v<version>`) and a GitHub Release with a zip of `custom_components/`.

Do not manually create tags or releases — let CI handle it.

---

## Common Tasks

### Adding a New Sensor

1. Define a constant for the new data key in `const.py`.
2. Add the API call to `api.py` if needed.
3. Merge the result in `coordinator.py` `_async_update_data`.
4. Add a `SensorEntityDescription` entry in `sensor.py`.
5. Add translation strings to `translations/en.json`.
6. Add a test in `tests/test_sensor.py`.

### Adding a New API Command

1. Add the method to `api.py` using the `_get` or `_post` helper.
2. Call it from the appropriate entity's `async_<action>` method.
3. After issuing a command, call `await self.coordinator.async_request_refresh()` to update state immediately.

### Debugging a Robot Integration

- Enable debug logging: add `custom_components.rowenta_roboeye: debug` to HA logger config.
- Check `coordinator.data` in HA Developer Tools > Template to inspect raw merged state.
- The vacuum's web UI is at `http://<robot-ip>:8080` for direct API inspection.

---

## What to Avoid

- **Do not** introduce cloud API calls or external network dependencies.
- **Do not** use synchronous I/O (`requests`, `time.sleep`) anywhere in integration code.
- **Do not** hardcode entity IDs, device names, or area names — all are dynamic.
- **Do not** modify the Lovelace dashboard structure in `dashboard.py` without verifying hash invalidation still works.
- **Do not** skip `available` property updates — entities must go unavailable when coordinator fails.
- **Do not** add Python packages to `manifest.json` `requirements` without confirming the package is not already bundled with Home Assistant.
- **Do not** push to `main` directly — open a PR or use the feature branch workflow.
