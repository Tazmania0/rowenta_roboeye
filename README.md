# Rowenta / Tefal RobEye Robot Vacuum — Home Assistant Integration

A native Home Assistant custom integration for **Rowenta and Tefal X-Plorer Serie 120 / S220 / S240** robot vacuums using the **local RobEye HTTP API** (port 8080). No cloud, no YAML, no token hunting — everything runs on your LAN.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/Tazmania0/rowenta_roboeye)](https://github.com/Tazmania0/rowenta_roboeye/releases)

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Tazmania0&repository=rowenta_roboeye&category=integration)

---

## Compatible Models

### ✅ Confirmed working
| Brand | Model |
|-------|-------|
| Rowenta | X-Plorer Serie 120 (RR7865WH, RR7867WH) |
| Rowenta | X-Plorer Serie 120 AI |
| Tefal | X-Plorer Serie 120 (RG7865WH, RG7867WH) |
| Tefal | X-Plorer Serie 120 AI |

### 🟡 Likely compatible (D-shaped body with local RobEye API)
| Brand | Model |
|-------|-------|
| Rowenta / Tefal | X-Plorer S220 / S220+ |
| Rowenta / Tefal | X-Plorer S240 / S240+ |

Community confirmation welcome — open an issue with your model if it works or doesn't.

### ❌ Not compatible — use [Tuya Local](https://github.com/make-all/tuya-local) instead

Round-body models use the Tuya cloud protocol, not the local RobEye API:
X-Plorer Serie 50, 60, 75, 75S, 80, S85, S90, S135, S140, S275, S280, S375+, S380+, S575, S580 Max, Eclipse 2n1 / 3n1.

> **How to tell:** D-shaped (flat front) = RobEye local API → this integration. Round body = Tuya.

---

## Quick Start

### 1 — Install

**Via HACS (recommended)**

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Tazmania0&repository=rowenta_roboeye&category=integration)

Click the button above, or add manually:
1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Tazmania0/rowenta_roboeye` as **Integration**
3. Search *RobEye*, install, restart HA

**Manual install**
Copy `custom_components/rowenta_roboeye/` into your `config/custom_components/` directory and restart HA.

---

### 2 — Add Integration

**Settings → Devices & Services → Add Integration → Rowenta / Tefal RobEye Robot Vacuum**

| Field | Value |
|-------|-------|
| Host | Local IP address of your vacuum (e.g. `192.168.1.50`) |
| Name | A friendly nickname for this vacuum in HA (e.g. *Rosie*, *Daisy*) |

> **Tip:** Assign a DHCP reservation so the IP never changes. If the IP does change later, update it without removing the integration via **Settings → Devices & Services → Rowenta / Tefal RobEye Robot Vacuum → Configure**.

The floor map is fetched automatically from the device — no Map ID entry is needed.

> **Auto-discovery:** If your LAN supports mDNS multicast, HA detects the vacuum automatically via the `_robeye._tcp.local.` service and shows a confirmation prompt — no IP entry needed at all. The mDNS hostname is used as the stable unique ID, so if the IP changes the integration updates silently.

---

### 3 — Dashboard

A Lovelace dashboard named **Rowenta Xplorer 120** is created automatically on first setup. It appears in the HA sidebar with no manual YAML required. Room cards are added automatically as rooms are discovered on the current floor.

The dashboard has four views:

**🤖 Control** — vacuum state, battery, brush stuck and dustbin status tiles; Clean All / Stop / Return to Base buttons; fan speed selector; cleaning strategy selector; deep clean toggle; live status panel (mode, charging state, session area and time elapsed); **Cleaning Queue** panel showing active/paused/pending job labels with estimated completion time; schedule panel listing all configured schedules with per-schedule enable/disable toggles; current floor panel (active map name and map switch selector).

**🏠 Rooms** — one card per discovered room showing: fan speed selector, strategy selector, deep clean toggle, room selection toggle, Start Cleaning button, last cleaned date, times cleaned, area (m²), and average clean duration. Toggle the selection switches across multiple rooms then press **Clean Selected Rooms** to send them as a single multi-room clean command.

**📊 Statistics** — lifetime totals (cleaning runs, area, distance, time) and device info (serial number, firmware version, Wi-Fi network and signal strength).

**🗺 Map** — live SVG map card showing the saved floor plan, the cleaned-area overlay from the current or most recent session, and a moving robot position dot with direction indicator. During cleaning a **robot trail** (up to 2000 path points, 1 cm resolution) traces the robot's route. **Avoidance zones** are rendered as a hatched red overlay. After cleaning ends the card switches to **"LAST SESSION"** replay mode, showing the frozen cleaned-area grid and path from the completed run until the next clean starts; this saved session is also restored from `/get/cleaning_grid_map` on HA startup so the last run is visible immediately. The map card is fully interactive: tap any room polygon to toggle its selection, then press **Clean Selected** in the control bar — or use the control bar buttons to Start/Pause, Stop, or Go Home without leaving the map view. This view appears only when the `Live Map` sensor entity is enabled. If no map data is available a guidance card is shown instead.

---

## Entities

> **Entity ID format:** all entity IDs use the robot's serial number as the device slug (e.g. `sensor.abc1234567_battery_level`), not a fixed name. This allows multiple robots to coexist in the same HA instance without collisions. The entity names shown below use `<serial>` as a placeholder — replace it with your robot's actual serial number slug, which you can find in **Settings → Devices & Services → Rowenta Xplorer 120 → device page**.

### Main Vacuum
| Entity | Description |
|--------|-------------|
| `vacuum.<serial>` | Main control entity — state, battery %, fan speed, start / stop / return to base |

### Buttons
| Entity | Description |
|--------|-------------|
| `button.<serial>_clean_entire_home` | Start a full-home clean |
| `button.<serial>_stop` | Stop immediately |
| `button.<serial>_return_to_base` | Send the vacuum to its dock |
| `button.<serial>_map<N>_clean_room_<id>` | Clean one specific room — one per discovered room, added automatically |
| `button.<serial>_clean_selected_rooms` | Clean all rooms currently toggled ON by their `_selected` switches — resolves fan speed and strategy across the selection (most intensive wins), then resets all selection switches; unavailable when no rooms are selected |

### Selects
| Entity | Description |
|--------|-------------|
| `select.<serial>_cleaning_mode` | Global fan speed: Normal / Eco / High / Silent |
| `select.<serial>_cleaning_strategy` | Global cleaning strategy: Default / Normal / Walls & Corners |
| `select.<serial>_active_map` | Shows the active floor; lets you switch between saved maps |
| `select.<serial>_map<N>_room_<id>_fan_speed` | Per-room fan speed override — one per discovered room |
| `select.<serial>_map<N>_room_<id>_strategy` | Per-room cleaning strategy override — one per discovered room |

### Switches
| Entity | Description |
|--------|-------------|
| `switch.<serial>_deep_clean_mode` | Global deep clean toggle — when ON, all cleans use Deep strategy (mode 3, double/triple pass) |
| `switch.<serial>_map<N>_room_<id>_deep_clean` | Per-room deep clean override — forces Deep strategy for that room; bidirectionally synced with the robot's stored `strategy_mode` (changes made in the native app reflected within 300 s) |
| `switch.<serial>_map<N>_room_<id>_selected` | Per-room selection toggle — HA-only state, no API call; toggled by the user (or by tapping the room on the map card) to build the room set for `button.clean_selected_rooms` |
| `switch.<serial>_schedule_<task_id>` | Per-schedule enable/disable — one switch per schedule entry; writes via `/set/modify_scheduled_task`; optimistic state prevents bounce while the robot confirms |

The global and per-room deep clean switches survive HA restarts (state restored via `RestoreEntity`). The per-room selection switches also restore state, but are map-scoped — they become unavailable when a different map is active.

#### Cleaning Strategy Modes
Confirmed from the RobEye web UI source:

| API value | Label | Description |
|-----------|-------|-------------|
| `4` | Default | Robot chooses automatically |
| `1` | Normal | Standard single-pass |
| `2` | Walls & Corners | Extra passes along edges |
| `3` | Deep | Double/triple pass |

The `cleaning_strategy` select offers **Default / Normal / Walls & Corners**. Deep mode is applied exclusively by the `deep_clean_mode` switch (and per-room counterparts), which takes precedence over the select when ON. Per-room strategy selects offer the same three options; the per-room deep clean switch overrides them for that room.

#### Strategy Precedence (highest wins)
1. Per-room deep clean switch → Deep for that room
2. Per-room strategy select → room-level strategy
3. Global deep clean switch → Deep for all rooms
4. Global cleaning strategy select → Default / Normal / Walls & Corners

### Sensors — Status
| Entity | Description |
|--------|-------------|
| `sensor.<serial>_battery_level` | Battery % |
| `sensor.<serial>_mode` | Current mode: `cleaning` / `ready` / `go_home` / `not_ready` |
| `sensor.<serial>_charging_status` | Charging state: `charging` / `connected` / `unconnected` |
| `sensor.<serial>_fan_speed` | Current fan speed label |
| `sensor.<serial>_active_map` | Display name of the currently active floor map |
| `sensor.<serial>_current_area_cleaned` | m² cleaned this session (disabled by default) |
| `sensor.<serial>_current_cleaning_time` | Time elapsed this session (disabled by default) |
| `sensor.<serial>_schedule` | Count of active schedules; `schedules` attribute holds the full parsed list consumed by the dashboard |
| `sensor.<serial>_last_event` | Human-readable label of the most recent robot event (e.g. "Room clean succeeded", "Recharging mid-clean") — driven by `/get/event_log` polled every 30 s |
| `sensor.<serial>_cleaning_queue` | Count of items in the HA command queue (0 = idle); `queue` attribute is a list of `{status, label, map_name}` dicts; `recent_events` attribute holds the last 10 top-level robot events |
| `sensor.<serial>_queue_eta` | Estimated seconds remaining to complete all queued cleaning commands; `unavailable` during recharge-and-continue |
| `sensor.<serial>_selected_room_count` | Count of rooms currently toggled ON for multi-room cleaning; used by the dashboard "Clean Selected" button label |

### Sensors — Lifetime Statistics
| Entity | Description |
|--------|-------------|
| `sensor.<serial>_total_cleaning_runs` | Total number of cleaning runs |
| `sensor.<serial>_total_cleaned_area` | Total area cleaned (m²) |
| `sensor.<serial>_total_distance_driven` | Total distance driven (m) |
| `sensor.<serial>_total_cleaning_time` | Total cleaning time (h) |

### Sensors — Per-Room (auto-discovered)
One set per room per floor, added automatically. `<N>` is the map ID; `<id>` is the area ID from the RobEye API.

| Entity | Description |
|--------|-------------|
| `sensor.<serial>_map<N>_room_<id>_cleanings` | Times this room has been cleaned |
| `sensor.<serial>_map<N>_room_<id>_area` | Room area (m²) |
| `sensor.<serial>_map<N>_room_<id>_avg_clean_time` | Average clean duration (min) |
| `sensor.<serial>_map<N>_room_<id>_last_cleaned` | Date last cleaned, or unavailable if never |

### Binary Sensors (diagnostic, hidden by default)
| Entity | State | Description |
|--------|-------|-------------|
| `binary_sensor.<serial>_dustbin_present` | Present / Missing | Dustbin seated or removed |
| `binary_sensor.<serial>_left_brush_stuck` | Stuck / OK | Left side brush stuck — fires a persistent HA notification |
| `binary_sensor.<serial>_right_brush_stuck` | Stuck / OK | Right side brush stuck — fires a persistent HA notification |

### Diagnostic Sensors (hidden by default)
| Entity | Description |
|--------|-------------|
| `sensor.<serial>_wi_fi_signal_strength` | Wi-Fi RSSI (dBm) |
| `sensor.<serial>_wi_fi_network` | Wi-Fi SSID |
| `sensor.<serial>_firmware_version` | Protocol / firmware version |
| `sensor.<serial>_serial_number` | Robot serial number |
| `sensor.<serial>_cliff_sensor` | Cliff sensor health |
| `sensor.<serial>_bump_sensor` | Bump sensor health |
| `sensor.<serial>_wheel_drop_sensor` | Wheel drop sensor health |
| `sensor.<serial>_main_brush_current` | Main brush motor current (mA) |
| `sensor.<serial>_left_brush_current` | Left side brush motor current (mA) |
| `sensor.<serial>_right_brush_current` | Right side brush motor current (mA) |

> Reveal hidden entities: **Settings → Devices & Services → Rowenta Xplorer 120 → entities → Show hidden entities**

### Live Map Sensor (opt-in, disabled by default)
| Entity | Description |
|--------|-------------|
| `sensor.<serial>_live_map` | Transport sensor for the SVG map card — attributes carry the floor plan geometry, cleaned-area overlay, robot position/heading, and path trail |

Enable this entity in **Settings → Devices & Services → Rowenta Xplorer 120** to activate the Map dashboard view and robot tracking. When disabled, all live-map polling is suspended.

---

## Services

### `rowenta_roboeye.clean_room`

Start a targeted clean of one or more specific rooms.

```yaml
service: rowenta_roboeye.clean_room
target:
  entity_id: vacuum.<serial>
data:
  room_ids: [3, 11]       # required — list of numeric area IDs
  fan_speed: high          # optional — eco / normal / high / silent
  deep_clean: true         # optional — forces Deep strategy for this call only
```

Room IDs are the numeric area IDs visible in the per-room entity names (the `<id>` suffix after `room_`).

### `rowenta_roboeye.remove_queue_entry`

Remove a pending item from the HA command queue by position index (0-based). Useful when a queued room clean is no longer needed without stopping the currently active job.

```yaml
service: rowenta_roboeye.remove_queue_entry
target:
  entity_id: vacuum.<serial>
data:
  pending_index: 0   # 0 = first pending item (the one that would run next)
```

---

## Command Queue

All `/set/` cleaning commands are serialised through an `asyncio.PriorityQueue`. The worker dispatches one command at a time, polls `/get/command_result` for completion, waits for the robot to physically finish, then dispatches the next.

### Priority

| Priority | Commands |
|----------|---------|
| 0 (immediate) | `stop`, `go_home`, `clean_start_or_continue` — jump to front of queue; worker wakes immediately from any poll sleep |
| 1 (normal) | All cleaning commands (`clean_map`, `clean_all`) |
| bypass (no queue) | `modify_area`, `set_fan_speed` — fire directly and return; never held behind a cleaning job |

### Completion detection

After each dispatched command, the worker:
1. Captures the `cmd_id` from the `/set/` response.
2. Polls `/get/command_result` every 5 s (up to 30 s) for that exact `cmd_id`.
3. Calls `_wait_for_active_operation_end()` — two-phase: waits up to 30 s for the robot to *enter* active mode, then waits (no timeout, 2 consecutive non-active polls) for it to *exit* — preventing the worker from racing ahead during brief inter-room transitions.
4. Waits 8 s (dock settle delay) before dispatching the next job.

### Pause / resume

**Pause** (`vacuum.stop` / Stop button while cleaning):
- Drains all pending cleaning jobs from the queue into `_paused_jobs` (saved for resume).
- Sets `_is_paused = True`, captures current fan speed.
- Vacuum state → `paused`.

**Resume** (`vacuum.start` when paused):
- Re-dispatches the original `clean_map` as a fresh queue item (NOT `clean_start_or_continue` — firmware frequently abandons sessions after `/set/stop`, so a fresh `clean_map` guarantees the room gets cleaned).
- Re-enqueues `_paused_jobs` behind the resumed room; commands added while paused come last.
- Queue order: *paused room* → *saved jobs* → *newly-added-while-paused jobs*.

**Full stop** (`vacuum.return_to_base` / Go Home):
- Discards `_paused_jobs` entirely — no resume possible.
- Clears `_is_paused`.

**Advance to next job** (Stop button when already paused):
- Abandons the current (paused) room.
- Re-enqueues the remaining `_paused_jobs` so the queue continues with the next room.

### Queue display

`sensor.<serial>_cleaning_queue` shows each job's status:

| Status icon | Meaning |
|-------------|---------|
| 🔄 active | Currently being dispatched and tracked |
| ⏸ paused | Was stopped mid-run; will re-run first on resume |
| ⏳ pending | Waiting in the queue |

`sensor.<serial>_queue_eta` sums `average_cleaning_time` from `/get/areas` for every room in every queued job (the same room queued twice counts twice). Returns `unavailable` during recharge-and-continue.

---

## Multi-Floor / Multi-Map Support

The robot does **not** auto-detect which floor it is on. The workflow for multi-floor homes is:

1. Physically move the robot to the dock on the other floor.
2. In HA, use **`select.active_map`** to select the correct floor map.
3. The coordinator reloads rooms, areas, and map geometry for the selected floor.

Room entity `unique_id` values include both the map ID and area ID, so rooms on different floors with identical area IDs never collide in the entity registry.

The `select.active_map` entity lists all permanent saved maps by their user-assigned name (Cyrillic supported) or "Map 1", "Map 2" for unnamed maps. The `sensor.active_map` shows the name of the currently selected floor.

> **Note:** `/set/use_map` returns error 101 on this firmware — the map can only be selected via the integration's `select.active_map` entity, which switches the coordinator's tracking context. The robot's own SLAM engine determines localisation from its physical position.

---

## Poll Intervals

| Data | Interval | Endpoint(s) |
|------|----------|-------------|
| Status | 5 s cleaning / 15 s idle | `/get/status` |
| Sensor values (brushes, dustbin) | Every tick | `/get/sensor_values` |
| Robot position | 5 s cleaning / 60 s idle (live map enabled only) | `/get/rob_pose` |
| Cleaned-area polygon + occupancy grid (live) | Every 5 s — **active cleaning only**, never polled when idle | `/get/seen_polygon`, `/get/cleaning_grid_map` |
| Event log (incremental, `last_id` cursor) | Every 30 s | `/get/event_log` |
| Rooms, areas, sensor health, maps, map status | Every 300 s | `/get/areas`, `/get/sensor_status`, `/get/robot_flags`, `/get/maps`, `/get/map_status` |
| Cleaning schedule | Every 60 s | `/get/schedule` |
| Lifetime statistics | Every 600 s | `/get/statistics`, `/get/permanent_statistics` |
| Saved map geometry (walls, rooms, last-session grid) | Every 600 s | `/get/feature_map`, `/get/tile_map`, `/get/areas` (saved), `/get/seen_polygon` (saved), `/get/cleaning_grid_map` (saved) |
| Serial number, Wi-Fi, firmware | Every 3600 s | `/get/robot_id`, `/get/wifi_status`, `/get/protocol_version` |

> `/get/live_parameters` is **never polled** — it is a configuration endpoint only; polling it during cleaning would interfere with robot state.

---

## Robot Position

Position is read from `/get/rob_pose`:

```json
{
  "map_id": 3,
  "x1": -2,
  "y1": -3,
  "heading": 157,
  "valid": true,
  "is_tentative": false,
  "timestamp": 958459
}
```

- `x1`, `y1` — position in API units. **1 unit = 2 mm** (validated by physical measurement).
- `heading` — already in degrees (0–360), no conversion needed.
- `valid: false` — robot has no SLAM fix; the position dot is hidden on the map card.
- `is_tentative: true` — SLAM is converging; position may jump.
- `timestamp` — monotonic uptime counter; an unchanged value during active cleaning is logged as a potential stale-position warning.

The SVG map card uses `transition: transform 0.45s linear` on the robot element to interpolate smoothly between position fixes, making motion appear continuous.

> `/debug/localization` and related debug endpoints are **not used** — `/get/rob_pose` supersedes them and works in all robot states.

---

## Recharge-and-Continue

When the robot runs low on battery mid-clean it docks automatically to recharge, then resumes the same job. During the entire charge cycle:

- `sensor.<serial>_mode` stays `cleaning`
- `sensor.<serial>_charging_status` shows `charging`
- The same `cmd_id` remains `executing` — it never aborts
- `sensor.<serial>_queue_eta` returns `unavailable`
- `/get/event_log` emits `type_id=2000` (battery low) then `type_id=1040` (`recharge_and_continue`)
- After charging completes, `charging` returns to `unconnected` and the robot leaves the dock to resume

The command worker extends its `_wait_for_cmd` timeout to 3 hours with a 30 s poll interval during this state so the queue is not prematurely cancelled.

---

## Live Map Card Configuration

The `custom:rowenta-map-card` card is added automatically to the Map view dashboard. To customise it in YAML, the available options are:

```yaml
type: custom:rowenta-map-card
entity: sensor.<serial>_live_map      # required
vacuum_entity: vacuum.<serial>        # optional — auto-derived from entity if omitted
title: "Living Room Map"              # default: "Live Map"
rotate: 0                             # degrees to rotate the whole map (0/90/180/270)
show_dock: true                       # show dock position icon
show_walls: true                      # show wall outlines
show_room_labels: true                # show room name labels
show_room_areas: true                 # show room area (m²) under label
room_opacity: 0.25                    # fill opacity for room polygons (0.0–1.0)
show_redundant_rooms: false           # show auto-detected rooms without user-assigned names
```

The card is purely reactive to WebSocket push from the `live_map` sensor — it uses no `setInterval` polling.

---

## Cleaning Schedule

The schedule sensor (`sensor.<serial>_schedule`) reads `/get/schedule` every 60 seconds. Both enabled and disabled schedules are returned by the API. The **sensor state is the count of active (enabled) schedules**. The full parsed list is in the `schedules` attribute, consumed by the dashboard:

```yaml
# Example schedules attribute entry
- task_id: 1
  enabled: true
  days: ["Mon", "Wed", "Fri"]
  days_full: ["Monday", "Wednesday", "Friday"]
  time: "07:30"
  hour: 7
  minute: 30
  mode: "all"           # "all" = whole home, "rooms" = specific rooms
  map_id: "3"
  map_name: "Ground Floor"
  rooms: []             # list of {id, name} when mode is "rooms"
  rooms_str: "All rooms"
  fan_speed: "eco"
  fan_raw: 2            # 0 = per-room default, 1–4 = Normal/Eco/High/Silent
```

> Schedules are **read and toggle** from HA — enable or disable individual schedules using the `switch.<serial>_schedule_<task_id>` entities. Each schedule switch exposes `task_id`, `days`, `time`, `cleaning_mode`, `area_ids`, `fan_speed`, and `map_id` as extra state attributes. Creating or editing schedule times/days must be done in the RobEye mobile app.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Integration not found after install | Restart Home Assistant |
| "Cannot connect" on setup | Check IP; confirm the vacuum is powered on and reachable on port 8080; assign a DHCP reservation |
| Robot got a new IP address | Update without removing the integration: **Settings → Devices & Services → Rowenta / Tefal RobEye → Configure** |
| Rooms not appearing | Wait for the first 300-second areas poll, or reload the integration; verify the vacuum has a saved map with named rooms |
| Dashboard not in sidebar | Reload the integration; check HA logs for Lovelace errors |
| Auto-discovery not working | Use manual IP entry; open an issue with the mDNS service name your vacuum advertises |
| Map view missing from dashboard | Enable `sensor.<serial>_live_map` in the entity list, then reload the integration |
| Map shows wrong floor after robot moved | The integration detects floor changes on the 300-second areas poll; wait one cycle or reload |
| Vacuum shows `error` state | Check dustbin is seated and brushes are not stuck — the `error` attribute shows the specific cause |
| Mode sensor shows `cleaning` while docked | Robot is in **recharge-and-continue** — battery was low mid-clean; it will resume automatically when charged |
| `queue_eta` sensor shows `unavailable` | Normal during recharge-and-continue; ETA resumes once the robot leaves the dock |
| Schedule sensor empty | No schedules are configured in the RobEye app, or the vacuum returned an empty schedule list |
| Room "Last cleaned" unavailable | The room has never been cleaned — correct behaviour; the API sentinel (year 2001) is shown as unavailable |

---

## API Reference (confirmed endpoints)

All communication is plain HTTP to port 8080 on the local network — no authentication, no cloud.

### Status & Position

| Endpoint | Used for |
|----------|---------|
| `GET /get/status` | Robot mode, battery, error state |
| `GET /get/sensor_values` | Dustbin level, brush stuck flags, motor currents |
| `GET /get/sensor_commands` | Brush status and stuck-brush error codes |
| `GET /get/sensor_status` | Cliff / bump / wheel-drop sensor health |
| `GET /get/robot_flags` | Feature capability flags |
| `GET /get/robot_id` | Serial number and hardware identifiers |
| `GET /get/wifi_status` | SSID and RSSI |
| `GET /get/protocol_version` | Firmware version string |
| `GET /get/rob_pose` | Real-time robot position (x, y, heading) — works in all states |

### Maps & Rooms

| Endpoint | Used for |
|----------|---------|
| `GET /get/maps` | All saved floor maps with names and statistics |
| `GET /get/map_status` | Currently active floor map ID |
| `GET /get/areas?map_id=N` | Room list with names, areas, statistics, fan speed, strategy |
| `GET /get/feature_map?map_id=N` | Saved floor plan: walls, room polygons, dock position (`docking_pose`) |
| `GET /get/tile_map?map_id=N` | Tile-based map geometry; also contains docking position |
| `GET /get/topo_map` | Topological map graph (node/edge connectivity between areas) |
| `GET /get/seen_polygon` | Cleaned-area outline (live session) |
| `GET /get/cleaning_grid_map` | Occupancy grid (live + saved session) |
| `GET /get/n_n_polygons` | Nearest-neighbour polygon boundaries |

### Schedules & Statistics

| Endpoint | Used for |
|----------|---------|
| `GET /get/schedule` | All cleaning schedules (enabled + disabled) |
| `GET /get/cleaning_parameter_set` | Current fan speed / cleaning parameter set |
| `GET /get/statistics` | Session and cumulative statistics |
| `GET /get/permanent_statistics` | Alternative lifetime statistics |
| `GET /get/task_history` | Historical cleaning task records |

### Commands & Events

| Endpoint | Used for |
|----------|---------|
| `GET /get/command_result` | Poll result of an issued command by `cmd_id` |
| `GET /get/event_log?last_id=N` | Incremental event log — battery low, recharge-and-continue, area cleaned, dock errors |
| `GET /get/ui_cmd_log` | Audit log of all commands issued from app or UI |
| `GET /set/clean_all` | Start a full-home clean |
| `GET /set/clean_map?map_id=N&area_ids=X,Y` | Start a targeted room clean (one or more rooms) |
| `GET /set/clean_start_or_continue` | Resume a paused / interrupted clean |
| `GET /set/go_home` | Return to dock |
| `GET /set/stop` | Stop immediately |
| `GET /set/switch_cleaning_parameter_set` | Change global fan speed |
| `GET /set/modify_area` | Write per-room fan speed and strategy (bypasses command queue) |
| `GET /set/modify_scheduled_task?task_id=X&enabled=1` | Enable or disable a schedule entry |

### Debug (read-only, not polled in production)

| Endpoint | Notes |
|----------|-------|
| `GET /debug/exploration` | SLAM exploration state dump |
| `GET /debug/relocalization` | Relocalization debug data |
| `GET /debug/smsc` | SMSC state machine debug |
| `GET /get/critical_logs` | On-device critical error log |
| `GET /get/bug_report` | Full on-device diagnostic bundle |

**Known dead / limited endpoints on Serie 120 firmware:**
- `/get/rooms` — returns `unknown_request`; room data comes from `/get/areas` only
- `/get/points_of_interest` — responds but always returns `[{}]`; dock position comes from `/get/feature_map` `docking_pose` instead
- `/get/configurable_parameters` — returns HTTP 400 Bad Request on this firmware
- `/set/use_map` — returns error 101; active map is switched via `/set/clean_map?map_id=X` only
- `/set/clean_continue` — returns error 106 `command_deprecated`; use `/set/clean_start_or_continue` instead

---

## Planned / Not Yet Implemented

| Feature | Notes |
|---------|-------|
| **Schedule create / edit from HA** | Schedules are read + toggle-only from HA. Create and edit schedule entries in the RobEye mobile app. |
| **No-go zone / spot clean entities** | `area_type: "to_be_cleaned"` areas are skipped during entity creation; no HA entities are built for avoidance or spot zones yet. |
| **Event log as HA events** | `/get/event_log` is consumed internally (recharge-and-continue detection, `type_id` 2000/1040) but events are not yet surfaced as HA events or sensors. |
| **HACS default list** | Integration targets the HACS default list once `v1.0.0` is released. |

---

## Running Tests

```bash
pip install pytest pytest-asyncio pytest-homeassistant-custom-component
pytest tests/ -v
```

---

## Contributing

Issues and pull requests welcome at [github.com/Tazmania0/rowenta_roboeye](https://github.com/Tazmania0/rowenta_roboeye/issues).

Helpful contributions:
- Testing on S220 / S240 models and reporting compatibility
- **Translations** — copy `translations/en.json`, translate, and open a PR. The following entities currently fall back to their hardcoded `_attr_name` values and need translation keys added to `en.json`: `sensor.schedule`, `sensor.cleaning_queue`, `sensor.selected_room_count`, `sensor.queue_eta`, `sensor.last_event`, `button.clean_selected`, `switch.deep_clean` (per-room), `switch.selected` (per-room), `switch.schedule_<N>`
- Reporting undocumented API endpoints or parameters discovered via packet capture or APK analysis

---

## Credits & Prior Art

- **[Romy integration](https://www.home-assistant.io/integrations/romy/)** — official HA integration for a related Robart-SDK vacuum; architecture reference
- **[HA community thread](https://community.home-assistant.io/t/rowenta-vacuum-cleaner-ht-component/244131/)** — original community exploration of the RobEye HTTP API; first published endpoint map
- **[openHAB Romy integration](https://community.openhab.org/t/romy-robot-integration-via-http-binding-austrian-vacuum-robot-highly-recommended/143307)** — independent HTTP binding implementation; useful endpoint cross-reference
- **[Dreame Vacuum integration](https://github.com/Tasshack/dreame-vacuum)** — reference for live map card patterns and multi-room entity architecture
- **[Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card)** — inspiration for the SVG map card interaction model

---

## License

MIT — see [LICENSE](LICENSE).
