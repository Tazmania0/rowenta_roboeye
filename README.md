# Rowenta Xplorer 120 — Home Assistant Integration

A native Home Assistant custom integration for the **Rowenta Xplorer 120** robot vacuum using the local **RobEye HTTP API** (port 8080). No cloud, no YAML, no token hunting.

---

## Quick Start

### 1 — Install

**Via HACS (recommended)**
1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/your-username/rowenta-roboeye-ha` as **Integration**
3. Search *Rowenta Xplorer 120*, install, restart HA

**Manual**
Copy `custom_components/rowenta_roboeye/` into `config/custom_components/`, restart HA.

---

### 2 — Add Integration

**Settings → Devices & Services → Add Integration → Rowenta Xplorer 120**

| Field | Value |
|-------|-------|
| Host  | Local IP of your vacuum (e.g. `192.168.1.50`) — assign a DHCP reservation so it never changes |
| Map ID | Number shown next to your map in the RobEye app — usually `3` |

> **Auto-discovery**: if your LAN supports mDNS multicast, HA will find the vacuum automatically and show a notification — no IP entry needed.

---

### 3 — Finding Your Map ID

Open the **RobEye mobile app** → tap the map icon → the number next to your floor plan is the Map ID.

---

### 4 — Use the Dashboard

After setup, the integration writes a ready-made Lovelace dashboard to:

```
config/rowenta_xplorer120_dashboard.yaml
```

To use it:
1. **Settings → Dashboards → Add Dashboard** (or open an existing one)
2. Click the ⋮ menu → **Edit Dashboard** → **Raw configuration editor**
3. Paste the contents of `rowenta_xplorer120_dashboard.yaml`

The dashboard includes:
- Vacuum state card with battery
- **Clean All**, **Stop**, **Return to Base** buttons
- Fan speed selector (Eco / Normal / High / Silent)
- One **Clean \<Room\>** button per discovered room
- Per-room statistics (cleans, area, avg time, last cleaned)
- Lifetime statistics
- Device info (serial, firmware, Wi-Fi)

---

## Entities

### Controls
| Entity | Description |
|--------|-------------|
| `vacuum.rowenta_xplorer120` | Main vacuum — start, stop, return, fan speed |
| `button.rowenta_xplorer120_return_to_base` | Send vacuum to dock |
| `button.rowenta_xplorer120_stop` | Stop immediately |
| `button.rowenta_xplorer120_clean_entire_home` | Start full-home clean |
| `button.rowenta_xplorer120_clean_<room>` | Clean one specific room (one per discovered room) |
| `select.rowenta_xplorer120_cleaning_mode` | Fan speed: Eco / Normal / High / Silent |

### Status Sensors
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer120_battery_level` | Battery % |
| `sensor.rowenta_xplorer120_mode` | Current mode (cleaning / ready / go_home) |
| `sensor.rowenta_xplorer120_charging` | Charging state |
| `sensor.rowenta_xplorer120_current_area_cleaned` | Area cleaned this session |
| `sensor.rowenta_xplorer120_current_cleaning_time` | Time elapsed this session |

### Lifetime Statistics
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer120_total_number_of_cleaning_runs` | Total runs |
| `sensor.rowenta_xplorer120_total_area_cleaned` | Total area (m²) |
| `sensor.rowenta_xplorer120_total_distance_driven` | Total distance (m) |
| `sensor.rowenta_xplorer120_total_cleaning_time` | Total time (h) |

### Per-Room Sensors (auto-discovered, one set per room)
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer120_<room>_cleanings` | Times cleaned |
| `sensor.rowenta_xplorer120_<room>_area` | Room area (m²) |
| `sensor.rowenta_xplorer120_<room>_avg_time` | Average clean time (min) |
| `sensor.rowenta_xplorer120_<room>_last_cleaned` | Date last cleaned |

### Diagnostic Sensors (hidden by default)
| Entity | Description |
|--------|-------------|
| `sensor.rowenta_xplorer120_wifi_rssi` | Wi-Fi signal strength |
| `sensor.rowenta_xplorer120_wifi_ssid` | Wi-Fi network name |
| `sensor.rowenta_xplorer120_protocol_version` | Firmware version |
| `sensor.rowenta_xplorer120_robot_serial` | Serial number |
| `sensor.rowenta_xplorer120_sensor_cliff_status` | Cliff sensor health |
| `sensor.rowenta_xplorer120_sensor_bump_status` | Bump sensor health |

> To reveal hidden sensors: **Settings → Devices → Rowenta Xplorer 120 → Show hidden entities**

---

## Services

### `rowenta_roboeye.clean_room`

Clean specific rooms by area ID — useful for automations.

```yaml
service: rowenta_roboeye.clean_room
target:
  entity_id: vacuum.rowenta_xplorer120
data:
  room_ids: [3, 11]      # list of RobEye area IDs
  fan_speed: high        # optional — eco / normal / high / silent
```

**Finding room IDs**: visible in the RobEye app map view, or from the button entity names after setup (e.g. `button.rowenta_xplorer120_clean_bedroom` → area id 3).

---

## Poll Intervals

| Data | Interval |
|------|----------|
| Status, live parameters | 15 s |
| Room areas, sensor health | 300 s |
| Lifetime statistics | 600 s |
| Serial, Wi-Fi, firmware | 3600 s |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Integration not found after install | Restart HA |
| "Cannot connect" on setup | Check IP, ensure vacuum is on and on same network, assign DHCP reservation |
| Rooms not appearing | Check Map ID in RobEye app matches what was entered during setup |
| Dashboard file not created | Check HA logs for write errors; create the file manually using the entity names above |
| Auto-discovery not working | Use manual IP entry; open an issue with the mDNS service type from your vacuum |

---

## Running Tests

```bash
pip install pytest pytest-asyncio aiohttp homeassistant
pytest tests/ -v
```
