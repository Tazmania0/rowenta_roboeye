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

> **Tip:** Assign a DHCP reservation so the IP never changes.

The floor map is fetched automatically from the device — no Map ID entry is needed.

> **Auto-discovery:** If your LAN supports mDNS multicast, HA detects the vacuum automatically via the `_robeye._tcp.local.` service and shows a confirmation prompt — no IP entry needed at all.

---

### 3 — Dashboard

A Lovelace dashboard named **Rowenta Xplorer 120** is created automatically on first setup. It appears in the HA sidebar with no manual YAML required. Room cards are added automatically as rooms are discovered on the current floor.

The dashboard has four views:

**🤖 Control** — vacuum state and battery tile; Clean All / Stop / Return to Base buttons; fan speed selector (Cleaning mode); cleaning strategy selector; deep clean toggle; live status panel (mode, charging state, session area and time elapsed); schedule summary; current floor panel (active map name and map switch selector).

**🏠 Rooms** — one card per discovered room showing: fan speed selector, strategy selector, deep clean toggle, Start Cleaning button, last cleaned date, times cleaned, area (m²), and average clean duration.

**📊 Statistics** — lifetime totals (cleaning runs, area, distance, time) and device info (serial number, firmware version, Wi-Fi network and signal strength).

**🗺 Map** — live SVG map card showing the saved floor plan, the cleaned-area overlay from the current or most recent session, and a moving robot position dot. This view appears only when the `Live Map` sensor entity is enabled. If no map data is available a guidance card is shown instead.

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
| `switch.<serial>_map<N>_room_<id>_deep_clean` | Per-room deep clean override — forces Deep strategy for that room only |

Both switches survive HA restarts (state is restored via `RestoreEntity`).

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
| `sensor.<serial>_current_area_cleaned` | m² cleaned this session |
| `sensor.<serial>_current_cleaning_time` | Time elapsed this session |
| `sensor.<serial>_schedule` | Next scheduled run — full schedule list in attributes |

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
| Robot position | Every tick (when live map enabled) | `/get/rob_pose` |
| Cleaned-area polygon + occupancy grid | 5 s cleaning / 60 s idle | `/get/seen_polygon`, `/get/cleaning_grid_map` |
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

## Cleaning Schedule

The schedule sensor reads `/get/schedule` every 60 seconds. Both enabled and disabled schedules are returned by the API. The sensor state shows the next upcoming enabled run; all schedules are available in the entity attributes:

```yaml
# Example attribute entry
- task_id: 1
  enabled: true
  days: [Mon, Wed, Fri]
  time: "07:30"
  map_id: "3"
  cleaning_mode: all_rooms
  fan_speed: normal
  rooms: []
```

> Schedules are **read-only** from HA — create and edit them in the RobEye mobile app.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Integration not found after install | Restart Home Assistant |
| "Cannot connect" on setup | Check IP; confirm the vacuum is powered on and reachable on port 8080; assign a DHCP reservation |
| Rooms not appearing | Wait for the first 300-second areas poll, or reload the integration; verify the vacuum has a saved map with named rooms |
| Dashboard not in sidebar | Reload the integration; check HA logs for Lovelace errors |
| Auto-discovery not working | Use manual IP entry; open an issue with the mDNS service name your vacuum advertises |
| Map view missing from dashboard | Enable `sensor.<serial>_live_map` in the entity list, then reload the integration |
| Map shows wrong floor after robot moved | The integration detects floor changes on the 300-second areas poll; wait one cycle or reload |
| Vacuum shows `error` state | Check dustbin is seated and brushes are not stuck — the `error` attribute shows the specific cause |
| Schedule sensor empty | No schedules are configured in the RobEye app, or the vacuum returned an empty schedule list |
| Room "Last cleaned" unavailable | The room has never been cleaned — correct behaviour; the API sentinel (year 2001) is shown as unavailable |

---

## API Reference (confirmed endpoints)

All communication is plain HTTP to port 8080 on the local network — no authentication, no cloud.

| Endpoint | Used for |
|----------|---------|
| `GET /get/status` | Robot mode, battery, error state |
| `GET /get/sensor_values` | Dustbin, brush stuck flags, motor currents |
| `GET /get/rob_pose` | Real-time robot position (x, y, heading) — works in all states |
| `GET /get/areas?map_id=N` | Room list with names, areas, statistics |
| `GET /get/maps` | All saved floor maps with names and statistics |
| `GET /get/map_status` | Currently active floor map ID |
| `GET /get/schedule` | All cleaning schedules (enabled + disabled) |
| `GET /get/statistics` | Session and cumulative statistics |
| `GET /get/permanent_statistics` | Alternative lifetime statistics |
| `GET /get/seen_polygon` | Cleaned-area outline (live session) |
| `GET /get/cleaning_grid_map` | Occupancy grid (live + saved) |
| `GET /get/feature_map?map_id=N` | Saved floor plan walls, room polygons, dock position |
| `GET /get/tile_map?map_id=N` | Tile-based map geometry |
| `GET /get/sensor_status` | Cliff / bump / wheel-drop sensor health |
| `GET /get/robot_flags` | Feature capability flags |
| `GET /get/robot_id` | Serial number and hardware identifiers |
| `GET /get/wifi_status` | SSID and RSSI |
| `GET /get/protocol_version` | Firmware version string |
| `GET /set/clean_all` | Start a full-home clean |
| `GET /set/clean_map` | Start a targeted room clean |
| `GET /set/go_home` | Return to dock |
| `GET /set/stop` | Stop immediately |
| `GET /set/switch_cleaning_parameter_set` | Change fan speed |

**Known dead/empty endpoints on Serie 120 firmware:**
- `/get/rooms` — returns `unknown_request`; room data comes from `/get/areas` only
- `/get/points_of_interest` — responds but always returns `[{}]`; dock position comes from `/get/feature_map` `docking_pose` field
- `/set/use_map` — returns error 101; active map follows physical location automatically

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
- Translations — copy `translations/en.json`, translate, and open a PR
- Reporting undocumented API endpoints or parameters discovered via packet capture or APK analysis

---

## Credits & Prior Art

- **[Romy integration](https://www.home-assistant.io/integrations/romy/)** — official HA integration for a related Robart-SDK vacuum; architecture reference
- **[HA community thread](https://community.home-assistant.io/t/rowenta-vacuum-cleaner-ht-component/244131/)** — original community exploration of the RobEye HTTP API
- **[Dreame Vacuum integration](https://github.com/Tasshack/dreame-vacuum)** — reference for live map card patterns and multi-room entity architecture
- **[Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card)** — inspiration for the SVG map card interaction model

---

## License

MIT — see [LICENSE](LICENSE).
