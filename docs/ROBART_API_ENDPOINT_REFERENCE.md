# Robart SDK Local HTTP API — Complete Endpoint Reference

**Scope:** Rowenta / Tefal RobEye robot vacuums using the Robart SDK local HTTP API (port 8080).
Consolidated from all live-device captures, APK (Rowenta Robots v9.5.1-RC1) DEX/asset extraction,
the RobEye web UI HTML, and the `rowenta_roboeye` integration codebase.

**Confidence legend:**
`LIVE` = confirmed from live device response · `APK` = confirmed in app bytecode/assets ·
`HTML` = confirmed in robot web UI · `INFER` = inferred, needs live test · `DEAD` = exists but unusable on this firmware

**Transport:** All endpoints are HTTP `GET` (even commands). Base: `http://<robot_ip>:8080`.
**Coordinate system (LIVE):** 1 API unit = 2 mm = 0.2 cm. Y increases upward (flip for SVG). Heading: `/get/rob_pose` returns degrees directly; legacy `debug/*` endpoints return raw (`÷ 65536/360 → degrees`).

---

## 1. Platform & Model Taxonomy

Model detection comes from `parseRobotType` (APK bytecode). The robot's `uniqueId` prefix is the
primary discriminator, then sub-type by name.

| Platform | Models | `uniqueId` | Setup | Auth | Mopping | Robart SDK / port 8080 |
|---|---|---|---|---|---|---|
| **LEGACY** | X-Plorer Serie 120, S220, S240 | not `aicu-` | WiFi AP hotspot | None (HTTP always open) | ❌ | ✅ |
| **AICU (modern)** | Serie 130, 140, 375 | starts `aicu-` | BLE | 8-char password | varies by sub-type | ✅ |
| └ HELIOS | name contains `Helios` | `aicu-` | BLE | 8-char | ✅ wet mop | ✅ |
| └ L6 | name == `Agon` or contains `HY100` | `aicu-` | BLE | 8-char | ✅ wet mop | ✅ |
| └ L7 | name == `Agonoa` | `aicu-` | BLE | 8-char | ✅ wet mop | ✅ |
| └ RC100 | name contains `RC100` | `aicu-` | BLE | 8-char | ❌ | ✅ |
| └ C5 | name contains `Chronos20` | `aicu-` | BLE | 8-char | ❌ | ✅ |
| **Tuya** | Serie 50–80, S85+ | n/a | Tuya cloud | Tuya | n/a | ❌ **OUT OF SCOPE** |

Notes:
- `isModern()` is `true` for both LEGACY and AICU (UNKNOWN ≠ LEGACY). Only Tuya models are excluded.
- The HTTP API is **identical** across LEGACY and AICU except for (a) auth and (b) mopping endpoints.
- Cross-reference: the official **ROMY** Home Assistant integration (`homeassistant/components/romy`,
  mDNS `_romy._tcp.local`) and the openHAB ROMY binding both drive the same Robart SDK get/set scheme.

---

## 2. Authentication Model

| Platform | Behaviour |
|---|---|
| **LEGACY** (120/S220/S240) | `LIVE` HTTP permanently open. No password ever set. `lock_http`/`unlock_http` exist in SDK but irrelevant. |
| **AICU** (130/140/375/Helios/L6/L7) | `APK` 8-char password (`ROBOT_PASSWORD_SIZE = 8`) from QR sticker on robot during BLE onboarding. |

AICU auth flow (`APK`):
- `lock_http` is **NOT** called by default → HTTP stays open even on AICU.
- If the native app enabled lock: every request needs `Authorization: Basic base64(robot_unique_id:password)`.
- `GET /set/unlock_http?pass=<8-char>` re-opens HTTP.
- Integration: optional `http_password` config field (blank = no auth). `401` → `ConfigEntryAuthFailed`.

---

## 3. GET Endpoints — Robot State & Info

| Endpoint | Params | Confirmed response (key fields) | Poll | Models | Conf |
|---|---|---|---|---|---|
| `/get/status` | — | battery %, `mode`, `charging`, fan speed raw. AICU wet adds `active_pump_volume`. | 2–15 s | all | LIVE |
| `/get/robot_id` | — | `serial_number` etc. → device `unique_id` source | 3600 s | all | LIVE |
| `/get/wifi_status` | — | `ssid`, `rssi` | 3600 s | all | LIVE |
| `/get/protocol_version` | — | `version` firmware string | 3600 s | all | LIVE |
| `/get/robot_flags` | — | feature/state flags. AICU wet adds `WATER_TANK_INSERTED`, `WATER_TANK_EMPTY`, `STUCK_WATER_PUMP`, `MISSING_WATER_PUMP`, `STUCK_WET_PAD_AGITATOR` | 15 s | all | LIVE |
| `/get/sensor_status` | — | cliff / bumper / LiDAR health | diag | all | LIVE |
| `/get/sensor_values` | — | gyro / odometry + GPIO brush-stuck flags | 500 ms / 10 s cached | all | LIVE |
| `/get/sensor_commands` | — | alternate brush-stuck source (higher-level codes) | diag | all | LIVE |
| `/get/product_feature_set` | — | feature flags → detect HELIOS/L6/L7 wet support | setup | all | APK |
| `/get/safety_mcu_firmware_version` | — | secondary firmware version | setup | all | APK |
| `/get/statistics` | — | total distance/time/area/run count | 600 s | all | LIVE |
| `/get/permanent_statistics` | — | lifetime stats (e.g. `total_distance_driven`) | 600 s | all | LIVE |
| `/get/cleaning_parameter_set` | — | active fan/strategy params. AICU wet adds `user_pump_volume` | — | all | LIVE |
| `/get/command_result` | — | `{"commands":[{"cmd_id":154,"status":"executing","error_code":0}]}` — **array** not single object | 3–5 s | all | LIVE |

`/get/status` mode strings: `ready`, `cleaning`, `go_home`. Charging: `charging` etc.
**Recharge-and-continue:** `mode=cleaning + charging=charging` persists for the whole dock+charge cycle (~100 min observed); the same `cmd_id` stays `"executing"` throughout.

---

## 4. GET Endpoints — Maps & Areas

| Endpoint | Params | Confirmed response (key fields) | Poll | Conf |
|---|---|---|---|---|
| `/get/maps` | — | `{"maps":[{"map_id":3,"map_meta_data":"Дружба ","permanent_flag":"true","statistics":{...}}]}`. `permanent_flag` is STRING `"true"`. `map_meta_data` = user name (strip whitespace; empty → "Map N" by 1-based position). `last_cleaned.year==2001` = never-cleaned sentinel. | 600 s | LIVE |
| `/get/map_status` | — | active map metadata / `map_id` confirmation | — | LIVE |
| `/get/areas` | `map_id` | per-area objects. **Top-level** (NOT in `area_meta_data`): `cleaning_parameter_set`, `strategy_mode` (STRING `"normal"`/`"deep"` only). `area_state`: `inactive`(unnamed)/`clean`(named+cleaned). `area_type`: `to_be_cleaned`(spot/avoid). `room_type`: corridor/kitchen/sleeping/living/none. Named: `area_meta_data` = JSON with `name`. AICU wet adds `pump_volume`. | 300 s | LIVE |
| `/get/schedule` | — | see §7 | 300 s | LIVE |
| `/get/n_n_polygons` | `map_id` | named room boundary polygons (static floor plan) | 5 s SLAM bucket | LIVE |
| `/get/seen_polygon` | `map_id` | visited/cleaned area polygon (segments `x1/y1/x2/y2`). Persists after dock. | 5 s | LIVE |
| `/get/cleaning_grid_map` | `map_id` | occupancy grid. `lower_left_x/y` (API units), `resolution`=40 units = 8 cm/cell. `map_id=3` persists last run after dock (514 cells observed); `map_id=41` is the live-session variant. | 5 s | LIVE |
| `/get/feature_map` | `map_id` | walls (`lines x1/y1/x2/y2`) **+ dock position** via `docking_pose` (`heading` raw; handle `valid:'false'`). | 600 s | LIVE |
| `/get/tile_map` | `map_id` | tile outline (`outline x/y`); also lists docking position | 600 s | LIVE |
| `/get/topo_map` | `map_id` | topological map graph | 600 s | LIVE |
| `/get/points_of_interest` | `map_id` | `{"map_id":3,"points_of_interest":[{}]}` — **empty on Serie 120 firmware**, do not use as dock source | — | DEAD |

**Dock position source:** use `/get/feature_map` → `docking_pose` (NOT `points_of_interest`, which is empty on Serie 120).

---

## 5. GET Endpoints — Position / Localization

| Endpoint | Params | Notes | Conf |
|---|---|---|---|
| `/get/rob_pose` | — | **Authoritative position.** `{"map_id":3,"x1":-2,"y1":-3,"heading":157,"valid":true,"is_tentative":false,"timestamp":958459}`. `heading` already degrees. `valid:false`=no fix. Works in all states. **Replaces all `debug/*` localization endpoints.** | LIVE |
| `/get/live_parameters` | — | `{x, y, heading}` + `area_cleaned`, `cleaning_time`. **Removed from all polling** in favour of `rob_pose`. | LIVE |
| `/debug/localization` | — | idle: `localization_algo_input[]` → use `"global"` entry `rob_pose[0..2]`. *Superseded by `rob_pose`.* | LIVE |
| `/debug/relocalization` | — | cleaning on saved map: use last `"continuous"` entry. *Superseded.* | LIVE |
| `/debug/exploration` | — | new map: `exploration_points[]` → max `ts`. *Superseded.* | LIVE |
| `/debug/smsc` | — | low-level diagnostic | LIVE |

---

## 6. GET Endpoints — Logs & History

| Endpoint | Params | Confirmed response (key fields) | Conf |
|---|---|---|---|
| `/get/event_log` | `last_id` | `{"robot_events":[{id,type,type_id,timestamp,current_status,map_id,area_id,source_type,source_id,hierarchy,info}]}`. Incremental: `last_id=N` → events with `id > N`. `source_type`: `user`=command, `operation_unit`=hardware sensor. | LIVE |
| `/get/ui_cmd_log` | — | `[{id,cmd,rtc{...},params,source}]`. `cmd` names below. `source=0` = from app/UI. | LIVE |
| `/get/task_history` | — | past cleaning task records | LIVE |
| `/get/critical_logs` | — | critical errors → HA Repairs panel | LIVE |
| `/get/bug_report` | — | diagnostic bundle | LIVE |

**Confirmed `event_log` `type_id` values:**
`2000`=battery_low · `2010`=robot_lifted · `2011`=robot_setback · `2030`=dustbin_missing · `2031`=dustbin_inserted ·
`1011`=clean_area succeeded · `1012`=clean_area interrupted · `1033`=go_home failed ·
`1040`=recharge_and_continue started · `1042`=recharge_and_continue interrupted ·
`1111`=clean_map_areas succeeded · `1170`=redocking started · `1172`=redocking interrupted ·
`1200`=skipped (`info=5`=insufficient battery). `hierarchy=3` = go_home sub-action inside recharge_and_continue.

**`ui_cmd_log` `cmd` names:** `ui_cmd_modify_area`, `ui_cmd_propose_nogo_areas`, `ui_suction_control_mode`,
`ui_cmd_clean_map`, `ui_cmd_clean_start_or_continue`, `ui_cmd_go_home`.
`modify_area` params show `modified={...,scm:N}` where `scm` = `cleaning_parameter_set`.

---

## 7. Schedules

| Endpoint | Params | Notes | Conf |
|---|---|---|---|
| `/get/schedule` | — | Returns ALL (enabled+disabled). Fields per entry below. | LIVE |
| `/set/modify_scheduled_task` | `task_id`, `enabled` (0/1) | Toggle enable/disable → `{"cmd_id":N}`. Only these two params required. **Must bypass the command queue.** | LIVE |
| `/set/add_scheduled_task` | (full task) | Add new schedule. AICU wet adds `pump_volume`. | APK |

`/get/schedule` entry: `task_id`, `enabled`(int 0/1), `time{days_of_week[1=Mon..7=Sun], hour, min, sec}`,
`task{map_id, cleaning_parameter_set(0=per-room default, 1=Eco,2=Normal,3=High,4=Silent), cleaning_mode(1=all rooms, 2=specific), parameters[area_ids]}`.
`parameter1`/`parameter2` are string duplicates of `parameters[0]`/`[1]` — **ignore them**, use `parameters[]`.

---

## 8. SET Endpoints — Commands

| Endpoint | Params | Notes | Models | Conf |
|---|---|---|---|---|
| `/set/clean_all` | `cleaning_parameter_set`(1–4), `cleaning_strategy_mode`(numeric, e.g. `3`=Deep), `method`(see below) | Clean whole map | all | LIVE |
| `/set/clean_map` | `map_id`, `area_ids`(comma-list), `cleaning_parameter_set`, `cleaning_strategy_mode`, AICU: `pump_volume` | Clean specific rooms. Multi-room = comma-separated ids in one call → one `cmd_id`. Also the **only** way to switch active map (`?map_id=X`). | all | LIVE |
| `/set/clean_start_or_continue` | — | Resume after user-pause → **brand new `cmd_id`** (original → `aborted`). Use this, NOT `clean_continue`. | all | LIVE |
| `/set/go_home` | — | Return to dock | all | LIVE |
| `/set/stop` | — | Stop. Transitions in-progress `cmd_id` → `"aborted"` (not `"done"`). | all | LIVE |
| `/set/switch_cleaning_parameter_set` | `cleaning_parameter_set`(1–4) | Set fan speed. **Must bypass queue** (settings write). | all | LIVE |

**Strategy / fan-speed numbering (cross-endpoint caution — they differ!):**
- `clean_*` `cleaning_strategy_mode` (numeric): `1`=Normal, `2`=Walls/Corners, `3`=Deep → `STRATEGY_DEEP="3"`.
- `/set/modify_area` `strategy_mode` (STRING): only `"normal"` / `"deep"` accepted.
- `/get/areas` `strategy_mode` (STRING): `"normal"` / `"deep"`.
- `modify_area` fan speed: `1`=Silent, `2`=Eco, `3`=High, `4`=Super Silent.
- Suction levels elsewhere: `0`=Default, `1`=Normal, `2`=Silent, `3`=Intensive, `4`=Super Silent.
- Schedule `cleaning_parameter_set`: `0`=per-room default, `1`=Eco, `2`=Normal, `3`=High, `4`=Silent.

**`method` parameter (`HTML`/`INFER`):** RobEye web UI exposes `none`/`dry`/`wet` for `clean_all`/`clean_map`.
String vs numeric form unconfirmed on-device — live-test both `&method=wet` and `&method=2`. Irrelevant on dry-only LEGACY units.

---

## 9. SET Endpoints — Area / Map Editing

All flat query params, no JSON body. No `save_map` call needed after.

| Endpoint | Params | Notes | Conf |
|---|---|---|---|
| `/set/modify_area` | `map_id`, `area_id`, `cleaning_parameter_set`, `strategy_mode`(`"normal"`/`"deep"`) | Per-area settings write. Persists across HA restarts. **Must bypass queue.** | LIVE |
| `/set/add_area` | `x1,y1,x2,y2,x3,y3,x4,y4`, `area_meta_data='{"name":""}'`, `area_state`, `cleaning_parameter_set`, `area_type` | Blocking zone: `area_state=blocking, cps=0, area_type=to_be_cleaned`. Spot: `area_state=clean, cps=1`. | APK |
| `/set/merge_areas` | `area_id1`, `area_id2` | NOTE: `area_id1/2`, not `area_id_1/_2` | APK |
| `/set/split_area` | `area_id`, `x1,y1,x2,y2` | Two boundary intersection points (NOT `points=JSON`) | APK |

---

## 10. AICU-Only Mopping Endpoints

Only for HELIOS / L6 / L7 (`hasWetSupport()==true`). LEGACY and RC100/C5 have no mopping hardware — skip entirely.

| Endpoint | Params | Notes | Conf |
|---|---|---|---|
| `/get/pump_volume_settings` | — | `{"mode":"low\|medium\|high\|auto\|none"}` | APK |
| `/set/pump_volume_settings` | `mode`(low/medium/high/auto/none) | Set water flow | APK |
| `/set/live_parameters` | `do_wet_clean`(true/false) | On/off wet-clean toggle during a run | APK |

Wet fields appended to existing payloads: `pump_volume` (`clean_map`, `add_scheduled_task`, `/get/areas`),
`user_pump_volume` (`/get/cleaning_parameter_set`), `active_pump_volume` (`/get/status`).
Gate registration of mopping entities on `hasWetSupport()` at setup.

---

## 11. Auth / Lock Endpoints

| Endpoint | Params | Notes | Conf |
|---|---|---|---|
| `/set/lock_http` | — | Require Basic auth on all requests. Not called by default. | APK |
| `/set/unlock_http` | `pass`(8-char) | Re-open HTTP | APK |

---

## 12. Dead / Unsupported Endpoints (Serie 120 firmware)

| Endpoint | Result | Action |
|---|---|---|
| `/get/rooms` | `unknown_request` | Don't call — use `/get/areas` |
| `/get/configurable_parameters` | `400 Bad Request` | Don't call |
| `/get/points_of_interest` | `{"points_of_interest":[{}]}` empty | Don't use for dock |
| `/set/use_map` | error `101` | Switch maps via `/set/clean_map?map_id=X` instead |
| `/set/clean_continue` | error `106 command_deprecated` (does nothing) | Use `/set/clean_start_or_continue` |

---

## 13. APK-Confirmed Endpoint List (v9.5.1-RC1)

GET: `rob_pose`, `rooms`, `product_feature_set`, `safety_mcu_firmware_version`, `event_log?last_id=0`.
SET: `clean_all`, `clean_map`, `go_home`, `stop`, `switch_cleaning_parameter_set`, `modify_scheduled_task`.

---

## 14. Architectural Rules (cross-cutting)

- **Queue bypass (settings writes):** `modify_area`, `switch_cleaning_parameter_set`, `modify_scheduled_task`
  must **always** bypass the `asyncio.Queue` — they are settings writes, not cleaning operations.
- **`cmd_id`** from `/set/` responses is the authoritative tracking handle; `_wait_for_cmd` treats both
  `done` and `aborted` as terminal; detects recharge-and-continue and extends timeout to 3 h (poll every 30 s).
- **Polling tiers:** 500 ms base (sensor_values), 2 s status, 5 s SLAM (polygons/grid/rob_pose),
  10 s brush-stuck (cached), 15 s idle, 300 s areas/schedule, 600 s map geometry/statistics, 3600 s robot info.
- **Map switching** is manual (`select.active_map`); no `use_map` endpoint exists.

---

## 15. Deep-Clean Feature & Entity Naming (open item)

Per-room deep clean as a `SwitchEntity`:
- Unique-id scheme: `[device]_map{N}_room_{id}_[property]`,
  e.g. `rowenta_xplorer_120_map3_room_10_deep_clean` (map prefix required for multi-floor).
- Write path: `/set/modify_area?...&strategy_mode=deep` (string form) — bypasses queue.
- `clean_map`/`clean_all` use the numeric `cleaning_strategy_mode=3` for a one-shot deep run.
- HA is source of truth for per-room strategy after setup.

UI references: `Tasshack/dreame-vacuum` (HA UI patterns) and `PiotrMachowski/lovelace-xiaomi-vacuum-map-card`
(interactive room-selection map card).

---

*Sources: live device captures (X-Plorer Serie 120, fw `SER120-1.1.0-release:3.11.2872`, maps 3/45),
Rowenta Robots v9.5.1-RC1 APK DEX/asset extraction, RobEye web UI HTML, `rowenta_roboeye` codebase,
and ApolloLogs endpoint enumeration. Cross-reference: home-assistant `romy` component, openHAB ROMY binding.*
