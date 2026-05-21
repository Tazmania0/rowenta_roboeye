# Rowenta / Tefal RobEye — Home Assistant Integration

[![HACS Custom][hacs-badge]][hacs-url]
[![HA Version][ha-badge]](https://www.home-assistant.io)
[![License: MIT][license-badge]](LICENSE)

A native Home Assistant custom integration for **Rowenta / Tefal RobEye** robot vacuums using the local **RobEye HTTP API** (port 8080, Robart SDK). No cloud, no YAML, no token hunting.

> **Prior art:** Architecture is modelled on the [Romy](https://www.home-assistant.io/integrations/romy/) integration (also Robart-based) and the [Dreame](https://github.com/Tasshack/dreame-vacuum) integration pattern. The map card draws inspiration from [Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card).

---

## Compatible Models

| Model | Shape | Protocol | Status |
|-------|-------|----------|--------|
| Rowenta X-Plorer Serie 120 | D-shape | RobEye / Robart | ✅ Tested |
| Rowenta X-Plorer S220 | D-shape | RobEye / Robart | ✅ Compatible |
| Rowenta X-Plorer S240 | D-shape | RobEye / Robart | ✅ Compatible |
| Tefal X-Plorer Serie 120 | D-shape | RobEye / Robart | ✅ Compatible |

> **Out of scope:** Rowenta Serie 50–80 / S85 and above use the **Tuya** protocol and are not supported.

---

## Quick Start

### 1 — Install

**Via HACS (recommended)**

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Tazmania0/rowenta_roboeye` as **Integration**
3. Search *Rowenta RobEye*, install, restart HA

**Manual**

Copy `custom_components/rowenta_roboeye/` into `config/custom_components/`, restart HA.

---

### 2 — Add Integration

**Settings → Devices & Services → Add Integration → Rowenta / Tefal RobEye Robot Vacuum**

| Field | Notes |
|-------|-------|
| Host | Local IP of your vacuum (e.g. `192.168.1.50`) — assign a DHCP reservation so it never changes |
| Name | Optional friendly nickname for the device (e.g. *Merry*, *Daisy*, *Rosie*) |

Maps and rooms are **auto-discovered** from the robot after a successful connection — no map IDs to look up.

> **Auto-discovery:** If your LAN supports mDNS multicast, HA detects the vacuum via `_robeye._tcp.local.` and shows a notification — no IP entry needed. The mDNS hostname is used as the unique ID, so the integration survives DHCP IP changes automatically.

> **Options flow:** After setup, you can update the IP address or name any time via **Settings → Devices & Services → Rowenta RobEye → Configure** — no need to remove and re-add the integration.

---

### 3 — Map & Room Discovery

Map IDs and room layouts are discovered automatically via `/get/maps` and `/get/areas`. Each map's display name comes from the label you assigned in the RobEye app; unnamed maps appear as "Map 1", "Map 2", etc.

For multi-floor homes, use the **Active Map** selector to switch floors after physically moving the robot. Map switching is manual — the integration does not auto-switch.

---

### 4 — Dashboard

After setup the integration writes a ready-made Lovelace dashboard to:

```
config/rowenta_xplorer120_dashboard.yaml
```

Load it via **Settings → Dashboards → Add Dashboard → Raw configuration editor**.

The dashboard includes:

- Vacuum state card with battery
- **Clean All**, **Pause / Continue**, **Stop**, **Return to Base** buttons
- Fan speed selector and global strategy selector
- Active map selector (for multi-floor homes)
- Per-room clean buttons, fan speed selects, strategy selects, and deep-clean switches
- **Clean Selected Rooms** — tick rooms on the map card then press once
- Schedule toggles (enable / disable each saved schedule)
- Per-room and lifetime statistics
- Device info (serial, firmware, Wi-Fi)

---

## Lovelace Map Card

The integration ships `rowenta-map-card.js`, a custom Lovelace card that renders a **live SVG map** of your home in the browser. No camera entity, no image proxy.

The card JS file is registered as a Lovelace resource **automatically during integration setup** — no manual file copying or resource configuration required.

**Features:**

- Floor plan outline from room boundary polygons
- Robot position dot with smooth heading interpolation (`transition: transform 0.45s linear`)
- Cleaned-area shading, updated in real time
- Docking station icon
- Interactive room selection — tap a room to toggle it (highlighted blue), then press **Clean Selected Rooms**
- Four control buttons: Start/Pause · Stop · Go Home · Clean Selected Rooms
- Purely reactive to WebSocket push — zero `setInterval`

**Card configuration:**

```yaml
type: custom:rowenta-map-card
entity: vacuum.rowenta_xplorer120
```

---

## Map Editor

The repo includes a standalone **in-browser map editor** (`map_editor/`) for drawing and editing room boundaries directly against the robot's live map. It runs as a lightweight Python proxy server — no dependencies beyond the Python standard library.

**Tools:**

| Tool | Shortcut | Description |
|------|----------|-------------|
| Select | S | Select and inspect a room |
| Pan | Space | Pan the map canvas |
| Split room | X | Draw a split line across a room |
| Merge rooms | M | Click two adjacent rooms to merge |
| Draw no-go zone | B | Draw a blocking rectangle (robot avoids) |
| Draw spot area | — | Draw a targeted spot-clean zone |
| Fit to screen | F | Zoom to fit the whole map |
| Cancel | Esc | Cancel current operation |

**Running:**

```bash
# Standalone — serves at http://localhost:8765
python3 map_editor/rowenta-editor-server.py 192.168.1.50

# Custom port
python3 map_editor/rowenta-editor-server.py 192.168.1.50 --port 9000
```

The editor also runs as a **Home Assistant add-on** (ingress mode), where the robot IP can be updated live without restarting the server. In proxy mode a `PROXY` badge appears in the header. Opening the HTML file directly is blocked by mixed-content policy — use the Python server or HA add-on instead.

---

## Entities

### Entity ID Convention

All entity IDs are derived from the robot's serial number (`{device_id}`), read from `/get/robot_id` at first connection. The serial is lower-cased with hyphens and spaces replaced by underscores.

Per-room and per-map entities include the map ID to prevent registry collisions across floors:

```
{platform}.{device_id}_map{map_id}_room_{area_id}_{property}
```

Examples for a robot with serial `ser120_abc123`:

| Entity ID | Description |
|-----------|-------------|
| `vacuum.ser120_abc123` | Main vacuum entity |
| `button.ser120_abc123_map3_clean_room_10` | Clean room 10 on map 3 |
| `switch.ser120_abc123_map3_room_10_deep_clean` | Deep clean switch for room 10 |
| `select.ser120_abc123_map3_room_10_fan_speed` | Fan speed for room 10 |
| `select.ser120_abc123_map3_room_10_strategy` | Strategy for room 10 |
| `switch.ser120_abc123_map3_room_10_selected` | Room selection switch for room 10 |

---

### Main Vacuum Entity

`vacuum.{device_id}`

Supported HA features: **START · STOP · PAUSE · RETURN_HOME · FAN_SPEED · STATE**

Fan speed options (via the vacuum entity or `select.{device_id}_cleaning_mode`):

| Label | API value | Description |
|-------|-----------|-------------|
| normal | 1 | Standard power |
| eco | 2 | Quiet, lower suction |
| high | 3 | Maximum suction |
| silent | 4 | Minimal noise |

---

### Controls

| Entity ID | Description |
|-----------|-------------|
| `vacuum.{device_id}` | Main vacuum — start, pause, stop, return, fan speed |
| `button.{device_id}_clean_entire_home` | Full-home clean |
| `button.{device_id}_clean_selected_rooms` | Clean all currently selected rooms in one pass |
| `button.{device_id}_return_to_base` | Send to dock |
| `button.{device_id}_stop` | Stop immediately |
| `select.{device_id}_cleaning_mode` | Global fan speed: Normal / Eco / High / Silent |
| `select.{device_id}_cleaning_strategy` | Global strategy: Default / Normal / Walls & Corners |
| `select.{device_id}_active_map` | Switch between saved floor maps |
| `switch.{device_id}_deep_clean_mode` | Global deep-clean toggle (forces strategy=deep for this run) |

> **Strategy note:** The strategy selector offers three options — Default / Normal / Walls & Corners. Deep clean is a separate switch (`deep_clean_mode`) that overrides strategy to `"deep"` (double/triple pass). The API only accepts `"normal"` or `"deep"` on the wire; "Default" and "Walls & Corners" both map to `"normal"`.

---

### Per-Room Controls (one set per named room, per map)

| Entity ID | Description |
|-----------|-------------|
| `button.{device_id}_map{map_id}_clean_room_{area_id}` | Clean this room |
| `switch.{device_id}_map{map_id}_room_{area_id}_selected` | Mark room for multi-room clean |
| `select.{device_id}_map{map_id}_room_{area_id}_fan_speed` | Per-room fan speed (persists to robot) |
| `select.{device_id}_map{map_id}_room_{area_id}_strategy` | Per-room strategy: Default / Normal / Walls & Corners (persists to robot) |
| `switch.{device_id}_map{map_id}_room_{area_id}_deep_clean` | Per-room deep clean (persists to robot) |

---

### Schedule Switches (one per saved schedule)

| Entity ID | Description |
|-----------|-------------|
| `switch.{device_id}_schedule_{task_id}` | Enable / disable a saved cleaning schedule |

Toggled via `GET /set/modify_scheduled_task?task_id=N&enabled=0\|1`. Schedule switches **bypass the command queue** — they are settings writes, not cleaning operations.

---

### Status Sensors

| Entity ID | Description |
|-----------|-------------|
| `sensor.{device_id}_battery_level` | Battery % |
| `sensor.{device_id}_mode` | Current mode (cleaning / ready / go_home / charging) |
| `sensor.{device_id}_charging` | Charging state |
| `sensor.{device_id}_fan_speed_label` | Active fan speed label |
| `sensor.{device_id}_active_map` | Name of the currently active map |
| `sensor.{device_id}_queue_eta` | Estimated minutes to finish queued jobs (`None` during recharge) |
| `sensor.{device_id}_last_event` | Most recent hardware event; backed by a rolling 50-event buffer |
| `sensor.{device_id}_current_area_cleaned` | Area cleaned this session (m²) |
| `sensor.{device_id}_current_cleaning_time` | Time elapsed this session |
| `sensor.{device_id}_cleaning_queue` | Command queue status |
| `sensor.{device_id}_selected_room_count` | Number of rooms currently selected for multi-room clean |
| `sensor.{device_id}_live_map` | Live map data (floor plan, cleaned area, robot position) as attributes — consumed by the map card |
| `sensor.{device_id}_schedule` | All saved schedules as attributes |

---

### Lifetime Statistics

| Entity ID | Description |
|-----------|-------------|
| `sensor.{device_id}_total_number_of_cleaning_runs` | Total runs |
| `sensor.{device_id}_total_area_cleaned` | Total area (m²) |
| `sensor.{device_id}_total_distance_driven` | Total distance (m) |
| `sensor.{device_id}_total_cleaning_time` | Total time (h) |

---

### Per-Room Sensors (one set per named room, per map)

| Entity ID | Description |
|-----------|-------------|
| `sensor.{device_id}_{m}room_{area_id}_cleanings` | Times cleaned |
| `sensor.{device_id}_{m}room_{area_id}_area` | Room area (m²) |
| `sensor.{device_id}_{m}room_{area_id}_avg_clean_time` | Average clean time (min) |
| `sensor.{device_id}_{m}room_{area_id}_last_cleaned` | Date last cleaned (`None` = never) |

> `{m}` is empty for the default map or `map{map_id}_` for secondary maps.

---

### Diagnostic Sensors (hidden by default)

| Entity ID | Description |
|-----------|-------------|
| `sensor.{device_id}_wifi_rssi` | Wi-Fi signal strength (dBm) |
| `sensor.{device_id}_wifi_ssid` | Wi-Fi network name |
| `sensor.{device_id}_protocol_version` | Robart SDK / firmware version |
| `sensor.{device_id}_robot_serial` | Serial number |
| `sensor.{device_id}_main_brush_current_ma` | Main brush motor current (mA) |
| `sensor.{device_id}_side_brush_left_current_ma` | Left side brush motor current (mA) |
| `sensor.{device_id}_side_brush_right_current_ma` | Right side brush motor current (mA) |
| `sensor.{device_id}_sensor_cliff_status` | Cliff sensor health |
| `sensor.{device_id}_sensor_bump_status` | Bump sensor health |
| `sensor.{device_id}_sensor_wheel_drop_status` | Wheel drop sensor health |

---

### Binary Sensors

| Entity ID | Description |
|-----------|-------------|
| `binary_sensor.{device_id}_left_brush_stuck` | Left brush stuck — `PROBLEM` device class |
| `binary_sensor.{device_id}_right_brush_stuck` | Right brush stuck — `PROBLEM` device class |
| `binary_sensor.{device_id}_dustbin_present` | Dustbin present / missing (custom states, no device class) |

> Reveal hidden entities: **Settings → Devices → Rowenta RobEye → Show hidden entities**

---

## Services

### `rowenta_roboeye.clean_room`

Clean specific rooms by area ID — useful in automations.

```yaml
service: rowenta_roboeye.clean_room
target:
  entity_id: vacuum.ser120_abc123
data:
  room_ids: [3, 11]      # list of RobEye area IDs
  fan_speed: high        # optional — eco / normal / high / silent
  deep_clean: false      # optional — forces deep strategy for this run only
```

---

## Hardware Alerts & Logbook

The integration raises **persistent HA notifications** for hardware events detected via the event log:

| Event | Notification |
|-------|-------------|
| Left or right brush stuck | "Rowenta — Brush Alert" — auto-dismissed when brush is freed |
| Dustbin removed | "Rowenta — Dustbin Missing" — auto-dismissed when dustbin is reinserted |

Top-level user-initiated events (`hierarchy=1`, `source_type=user`) are also written to the **HA logbook** with human-readable labels. The integration keeps a rolling buffer of the last 50 top-level events, exposed via `sensor.{device_id}_last_event`.

**Tracked event types:**

| type_id | Label |
|---------|-------|
| 1010 | Started cleaning room |
| 1011 | Room clean succeeded |
| 1012 | Room clean interrupted |
| 1030 | Returning to dock |
| 1031 | Docked |
| 1032 | Docking interrupted |
| 1033 | Docking failed |
| 1040 | Recharging mid-clean |
| 1042 | Recharge cycle interrupted |
| 1050 | Localizing |
| 1051 | Localized |
| 1052 | Localization failed |
| 1110 | Room clean started |
| 1111 | Multi-room clean succeeded |
| 1112 | Room clean interrupted |
| 1140 | Undocking |
| 1170 | Re-docking |
| 1172 | Re-docking interrupted |
| 1200 | Command skipped |
| 2000 | Battery low |
| 2010 | Robot lifted |
| 2011 | Robot set back down |
| 2030 | Dustbin removed |
| 2031 | Dustbin inserted |

---

## Command Queue & State Machine

Cleaning commands (`clean_all`, `clean_map`, `go_home`) are managed by an `asyncio.Queue` with a background worker. Each dispatched command receives a `cmd_id` from the robot and is polled every **5 s** via `/get/command_result` (timeout: 30 s per command).

**Settings writes bypass the queue entirely:** `modify_area`, per-room fan speed / strategy, and `modify_scheduled_task` are sent immediately. This bypass is enforced in three places in the code to prevent regressions.

### Recharge-and-Continue

When the robot runs low mid-clean it docks, charges (~100 min), then resumes automatically. During this cycle:

- State stays `mode=cleaning + charging=charging`
- The original `cmd_id` remains `"executing"` throughout
- ETA returns `None`
- The queue worker extends its timeout to **3 hours** (`MODE_RECHARGE_CONTINUE_WAIT_S`) and polls every **30 s** (`MODE_RECHARGE_CONTINUE_POLL_S`)

---

## Poll Intervals

| Data | Interval | Endpoint(s) |
|------|----------|-------------|
| Status + robot position | 5 s (cleaning) / 15 s (idle) | `/get/status`, `/get/rob_pose` |
| Event log | 30 s | `/get/event_log?last_id=N` |
| Map geometry + areas | 600 s | `/get/n_n_polygons`, `/get/seen_polygon`, `/get/areas` |
| Statistics | 600 s | `/get/statistics` |
| Maps list (background refresh) | 600 s | `/get/maps` |
| Device info | 3600 s | `/get/robot_id`, `/get/wifi_status`, `/get/sensor_status` |
| Command result polling | 5 s (per queued command) | `/get/command_result` |

---

## CI / Workflows

| Workflow | Trigger | Jobs |
|----------|---------|------|
| `validate.yml` | push / PR | pytest (Python 3.12) + hassfest |
| `release.yml` | push to `main` or `v*` tag | Reads version from `manifest.json`, creates git tag if absent, builds `rowenta_roboeye.zip`, publishes GitHub Release |

---

## API Endpoints Reference

### Implemented

| Endpoint | Used For |
|----------|----------|
| `GET /get/status` | Mode, charging, battery, current area / time |
| `GET /get/rob_pose` | Robot X/Y position and heading (degrees). 1 unit = 2 mm. `valid=false` = no fix |
| `GET /get/sensor_values` | Brush motor currents, brush stuck flag |
| `GET /get/sensor_status` | Cliff / bump / wheel-drop sensor health |
| `GET /get/maps` | All saved floor maps with names and IDs |
| `GET /get/areas?map_id=N` | Room list — boundaries, `area_state`, `area_type`, `cleaning_parameter_set`, `strategy_mode` |
| `GET /get/seen_polygon` | Cleaned-area polygon (current or last run) |
| `GET /get/n_n_polygons` | Room boundary polygons for map rendering |
| `GET /get/feature_map` | Docking station pose (`docking_pose.valid` may be string `"false"` before relocation) |
| `GET /get/cleaning_grid_map` | Binary occupancy grid (map background) |
| `GET /get/statistics` | Per-room and lifetime stats |
| `GET /get/schedule` | All saved schedules (enabled + disabled) |
| `GET /get/wifi_status` | SSID, RSSI |
| `GET /get/robot_id` | Serial number (`unique_id` / `serial_number` fields) |
| `GET /get/event_log?last_id=N` | Incremental hardware event log — returns events with `id > N` |
| `GET /get/ui_cmd_log` | User command history |
| `GET /get/command_result` | Poll command status by `cmd_id` — returns `{"commands":[...]}` array |
| `GET /debug/exploration` | SLAM exploration state |
| `GET /debug/relocalization` | SLAM relocalization state |
| `SET /set/clean_map` | Clean specific rooms or switch active map |
| `SET /set/clean_all` | Clean all rooms |
| `SET /set/clean_start_or_continue` | Resume after user-initiated pause |
| `SET /set/go_home` | Return to dock |
| `SET /set/stop` | Stop cleaning — transitions active `cmd_id` to `"aborted"` |
| `SET /set/modify_area` | Write per-room fan speed / strategy (persists to robot map) |
| `SET /set/modify_scheduled_task` | Enable / disable a saved schedule (`task_id` + `enabled` only) |

**Known deprecated / broken:**

| Endpoint | Notes |
|----------|-------|
| `SET /set/clean_continue` | Returns error 106 `command_deprecated` — use `/set/clean_start_or_continue` |
| `GET /get/live_parameters` | Removed from all polling — unreliable |
| `GET /debug/localization` | Superseded by `/get/rob_pose` |
| `GET /get/configurable_parameters` | Returns HTTP 400 on this firmware |

---

### Discovered — Not Yet Integrated

Confirmed present on the robot (via ApolloLogs / APK analysis) but not yet used. Candidates for future features:

| Endpoint | Notes |
|----------|-------|
| `GET /get/map_status` | Map build / load state |
| `GET /get/robot_flags` | Low-level capability flags |
| `GET /get/protocol_version` | Robart SDK version string |
| `GET /get/cleaning_parameter_set` | Global cleaning parameter set definitions |
| `GET /get/permanent_statistics` | Non-resettable lifetime counters |
| `GET /get/sensor_commands` | Brush / sensor raw commands; stuck-brush detection |
| `GET /get/topo_map` | Topological map data |
| `GET /get/tile_map` | Tile-based map; also contains docking position |
| `GET /get/task_history` | Per-task cleaning history |
| `GET /get/points_of_interest` | POI list (returns `[{}]` on current firmware) |
| `GET /get/product_feature_set` | Feature capability flags per model — useful for S220/S240 auto-detection |
| `GET /get/safety_mcu_firmware_version` | Safety MCU firmware string |
| `GET /get/bug_report` | Internal diagnostic log |
| `GET /get/critical_logs` | Critical error log |
| `GET /debug/smsc` | Safety MCU status |
| `SET /set/add_area` | Draw new room or blocking zone (confirmed: flat `x1..y4` params, no `save_map` needed) |
| `SET /set/merge_areas` | Merge two rooms (`area_id1=N&area_id2=M`) |
| `SET /set/split_area` | Split a room (`area_id=N&x1=X&y1=Y&x2=X&y2=Y`) |

---

## Known Limitations & Open Items

### Rooms dashboard view

After a map switch or HA restart, the Rooms view may occasionally display entity cards as unavailable or show room names from the previous floor. This is a timing issue between the coordinator loading new room data and the dashboard updating.

**Workaround:** reload the integration via **Settings → Devices & Services → Rowenta RobEye → ⋮ → Reload**. The Rooms view will rebuild correctly within a few seconds.

### Other

| # | Item | Status |
|---|------|--------|
| 1 | Room sensor `unique_id` includes map prefix — multi-floor collision resolved | ✅ Fixed |
| 2 | Root-level `icon.png` / `logo.png` are byte-for-byte duplicates of `brand/` variants — delete | 🟢 HACS hygiene |
| 3 | `zip_release: false` in `hacs.json` conflicts with `release.yml` producing a zip artifact | 🟢 HACS hygiene |
| 4 | Submit brand assets to [home-assistant/brands](https://github.com/home-assistant/brands) for official icon | 🟢 Future |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Integration not found after install | Restart HA |
| "Cannot connect" during setup | Check IP, confirm vacuum is powered on and on the same subnet, assign a DHCP reservation |
| Rooms view shows wrong rooms or unavailable cards | Reload the integration — see Known Limitations |
| Map card shows blank | Hard-refresh the browser; if still blank check HA logs for resource registration errors at startup |
| Auto-discovery not working | Use manual IP entry; open a GitHub issue with the mDNS service type from your vacuum |
| Schedule toggles not visible | At least one schedule must be saved on the robot |
| Robot shows "unavailable" | Three consecutive failed polls trigger unavailable state — check network / firewall |
| Map editor: "Mixed content blocked" | Use the Python proxy server (`rowenta-editor-server.py`) or the HA add-on instead of opening the HTML file directly |

---

## Running Tests

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

---

## Contributing

PRs welcome. Before opening one:

1. Run `ruff check` and `mypy`
2. Add or update tests for any new API endpoints
3. Follow the **empirical-first** principle — confirm live device responses before implementing

---

## Credits

- [Romy integration](https://www.home-assistant.io/integrations/romy/) — Robart HTTP API pattern
- [Dreame vacuum](https://github.com/Tasshack/dreame-vacuum) — coordinator / entity architecture
- [Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) — map card inspiration
- [ApolloLogs](https://community.home-assistant.io/t/rowenta-vacuum-cleaner-ht-component/244131/19) — endpoint discovery

---

## License

MIT — see [LICENSE](LICENSE)

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://github.com/Tazmania0/rowenta_roboeye
[ha-badge]: https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg
[license-badge]: https://img.shields.io/badge/License-MIT-yellow.svg
