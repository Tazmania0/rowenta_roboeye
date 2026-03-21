# Rowenta / Tefal RobEye Robot Vacuum — Home Assistant Integration

A native Home Assistant custom integration for **Rowenta and Tefal X-Plorer Serie 120 / S220 / S240** robot vacuums using the local **RobEye HTTP API** (port 8080). No cloud, no YAML, no token hunting.

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

### 🟡 Likely compatible (community verification welcome)
| Brand | Model |
|-------|-------|
| Rowenta / Tefal | X-Plorer S220 / S220+ |
| Rowenta / Tefal | X-Plorer S240 / S240+ |

### ❌ Not compatible — use [Tuya Local](https://github.com/make-all/tuya-local) instead
X-Plorer Serie 50, 60, 75, 75S, 80, S85, S90, S135, S140, S275, S280, S375+, S380+, S575, S580 Max, Eclipse 2n1 / 3n1 — these use the Tuya cloud protocol, not the local RobEye API.

---

## Quick Start

### 1 — Install

**Via HACS (recommended)**

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Tazmania0&repository=rowenta_roboeye&category=integration)

Click the button above, or add manually:
1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Tazmania0/rowenta_roboeye` as **Integration**
3. Search *RobEye*, install, restart HA

**Manual**
Copy `custom_components/rowenta_roboeye/` into `config/custom_components/`, restart HA.

---

### 2 — Add Integration

**Settings → Devices & Services → Add Integration → Rowenta / Tefal RobEye Robot Vacuum**

| Field | Value |
|-------|-------|
| Host | Local IP of your vacuum (e.g. `192.168.1.50`) — assign a DHCP reservation so it never changes |
| Map ID | Number shown next to your map in the RobEye app — usually `3` |

> **Auto-discovery**: if your LAN supports mDNS multicast, HA will find the vacuum automatically and show a notification — no IP entry needed.

---

### 3 — Finding Your Map ID

Open the **RobEye mobile app** → tap the map icon → the number shown next to your floor plan is the Map ID.

---

### 4 — Dashboard

A Lovelace dashboard named **Rowenta Xplorer 120** is **created automatically** when the integration is set up. It appears immediately in the HA sidebar with no manual YAML editing required. The dashboard updates live when new rooms are discovered.

It has four views:

**🤖 Control** — vacuum tile with battery and state, Clean All / Stop / Return to Base action buttons, fan speed selector, deep clean (double pass) toggle, live status (battery, mode, charging, brush health, dustbin), current session progress (area cleaned, time elapsed), and cleaning schedule.

**🗺 Rooms** — one card per discovered room showing: fan speed selector, Start Cleaning button, per-room deep clean toggle, last cleaned date, times cleaned, room area, and average clean duration.

**📊 Statistics** — lifetime totals (runs, area cleaned, distance, time) and device info (serial number, firmware version, Wi-Fi network and signal).

**🗺 Map** *(only shown when the Live Map sensor is enabled)* — live SVG map card (`custom:rowenta-map-card`) showing the floor plan, cleaned area overlay, and moving robot position dot.

---

## Entities

### Main Vacuum
| Entity | Description |
|--------|-------------|
| `vacuum.rowenta_xplorer_120` | Main vacuum — state, battery %, fan speed, start / stop / return to base |

### Buttons
| Entity | Description |
|--------|-------------|
| `button.rowenta_xplorer_120_clean_entire_home` | Start a full-home clean |
| `button.rowenta_xplorer_120_return_to_base` | Send the vacuum to its dock |
| `button.rowenta_xplorer_120_stop` | Stop immediately |
| `button.rowenta_xplorer_120_clean_room_<id>` | Clean one room — one button per discovered room, created automatically |

### Selects
| Entity | Description |
|--------|-------------|
| `select.rowenta_xplorer_120_cleaning_mode` | Global fan speed: Eco / Normal / High / Silent |
| `select.rowenta_xplorer_120_room_<id>_fan_speed` | Per-room fan speed override — one per discovered room |

### Switches
| Entity | Description |
|--------|-------------|
| `switch.rowenta_xplorer_120_deep_clean_mode` | Global deep clean (double pass) toggle — state is restored after HA restart |
| `switch.rowenta_xplorer_120_room_<id>_deep_clean` | Per-room deep clean toggle — one per discovered room |

### Status Sensors
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer_120_battery_level` | Battery % |
| `sensor.rowenta_xplorer_120_mode` | Current mode: `cleaning` / `ready` / `go_home` |
| `sensor.rowenta_xplorer_120_charging_status` | Charging state: `charging` / `connected` / `unconnected` |
| `sensor.rowenta_xplorer_120_current_area_cleaned` | m² cleaned this session |
| `sensor.rowenta_xplorer_120_current_cleaning_time` | Time elapsed this session |
| `sensor.rowenta_xplorer_120_schedule` | Next scheduled run — full schedule list in attributes |

### Lifetime Statistics
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer_120_total_cleaning_runs` | Total cleaning runs |
| `sensor.rowenta_xplorer_120_total_cleaned_area` | Total area cleaned (m²) |
| `sensor.rowenta_xplorer_120_total_distance_driven` | Total distance driven (m) |
| `sensor.rowenta_xplorer_120_total_cleaning_time` | Total cleaning time (h) |

### Per-Room Sensors (auto-discovered)
One set per room, created automatically. The `<id>` is the numeric area ID from the RobEye API.

| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer_120_room_<id>_cleanings` | Times this room has been cleaned |
| `sensor.rowenta_xplorer_120_room_<id>_area` | Room area (m²) |
| `sensor.rowenta_xplorer_120_room_<id>_avg_clean_time` | Average clean duration |
| `sensor.rowenta_xplorer_120_room_<id>_last_cleaned` | Date last cleaned |

### Binary Sensors (diagnostic, hidden by default)
| Entity | State | Description |
|--------|-------|-------------|
| `binary_sensor.rowenta_xplorer_120_dustbin_present` | Present / Missing | Dustbin seated or removed |
| `binary_sensor.rowenta_xplorer_120_left_brush_stuck` | Stuck / OK | Left side brush stuck |
| `binary_sensor.rowenta_xplorer_120_right_brush_stuck` | Stuck / OK | Right side brush stuck |

### Diagnostic Sensors (hidden by default)
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer_120_wi_fi_signal_strength` | Wi-Fi RSSI (dBm) |
| `sensor.rowenta_xplorer_120_wi_fi_network` | Wi-Fi SSID |
| `sensor.rowenta_xplorer_120_firmware_version` | Firmware / protocol version |
| `sensor.rowenta_xplorer_120_serial_number` | Robot serial number |
| `sensor.rowenta_xplorer_120_cliff_sensor` | Cliff sensor health |
| `sensor.rowenta_xplorer_120_bump_sensor` | Bump sensor health |
| `sensor.rowenta_xplorer_120_wheel_drop_sensor` | Wheel drop sensor health |
| `sensor.rowenta_xplorer_120_fan_speed` | Raw fan speed label (diagnostic) |

> To reveal hidden entities: **Settings → Devices & Services → Rowenta Xplorer 120 → Show hidden entities**

### Live Map (opt-in, disabled by default)
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer_120_live_map` | Transport sensor for the SVG map card — state mirrors vacuum activity; attributes carry floor plan polygons, cleaned area overlay, robot position, and path trail |

Enable this entity in **Settings → Devices & Services → Rowenta Xplorer 120** to activate the Map view in the dashboard and the moving robot dot.

---

## Services

### `rowenta_roboeye.clean_room`

Start a targeted clean of one or more specific rooms.

```yaml
service: rowenta_roboeye.clean_room
target:
  entity_id: vacuum.rowenta_xplorer_120
data:
  room_ids: ["3", "11"]   # list of area IDs
  fan_speed: high          # optional — eco / normal / high / silent
```

`deep_clean: true` can be added to use double-pass mode for this call regardless of the switch state.

Room IDs are the numeric area IDs from the RobEye API — visible in the `button.rowenta_xplorer_120_clean_room_<id>` entity names after setup.

---

## Deep Clean Mode

The **Deep clean mode** switch (`switch.rowenta_xplorer_120_deep_clean_mode`) enables double-pass cleaning globally. When ON, every clean — via the vacuum entity, the Clean All button, or the `clean_room` service — runs with `cleaning_strategy_mode=2`.

Each room also has its own **per-room deep clean** switch (`switch.rowenta_xplorer_120_room_<id>_deep_clean`) that overrides the global toggle for that room only.

Both switches survive HA restarts (state is restored via `RestoreEntity`).

---

## Poll Intervals

| Data | Cleaning | Idle |
|------|----------|------|
| Robot position (`/get/rob_pose`) + status | 5 s | 15 s |
| Cleaned area polygon + occupancy grid | 5 s | — |
| Room areas + schedule + sensor health | 300 s | 300 s |
| Floor plan geometry + saved map grid | 600 s | 600 s |
| Lifetime statistics | 600 s | 600 s |
| Serial, Wi-Fi, firmware | 3600 s | 3600 s |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Integration not found after install | Restart HA |
| "Cannot connect" on setup | Check IP; ensure the vacuum is on and on the same network; assign a DHCP reservation |
| Rooms not appearing | Check the Map ID in the RobEye app matches the value entered during setup, then reload the integration |
| Dashboard not appearing in sidebar | Reload the integration; check HA logs for Lovelace errors |
| Auto-discovery not working | Use manual IP entry; open an issue with the mDNS service type advertised by your vacuum |
| Map view missing from dashboard | Enable `sensor.rowenta_xplorer_120_live_map` in the device page, then reload the integration |
| Vacuum shows ERROR state | Check the dustbin is seated and brushes are not stuck — the error attribute shows the specific cause |

---

## Running Tests

```bash
pip install pytest pytest-asyncio pytest-homeassistant-custom-component
pytest tests/ -v
```

---

## Contributing

Issues and pull requests welcome at [github.com/Tazmania0/rowenta_roboeye](https://github.com/Tazmania0/rowenta_roboeye/issues).

If you have an S220 or S240 and can confirm compatibility, please open an issue with your findings.
