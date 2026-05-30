# Rowenta Map Editor

A standalone, in-browser editor for viewing and editing the saved floor maps on
a Rowenta / Tefal X-Plorer (RobEye / Robart) robot vacuum. It talks directly to
the robot's local HTTP API on port `8080` — **no cloud, no Tuya, no camera
entity**. It is independent of the Home Assistant integration but speaks the
exact same API.

> ⚠️ **Edits are written straight to the robot's saved map and persist
> immediately.** Splitting, merging, deleting areas, adding no-go zones, and
> deleting maps cannot be undone from here. Use with care.

---

## What it can do

- Render the saved map: room outlines, room names, the docking station, and the
  robot's live position/heading.
- **Inspect & edit a room** — rename it and set its fan speed, cleaning
  strategy, floor type (and, on wet models, the per-area pump volume) via
  `/set/modify_area`.
- **Split** a room along a drawn line.
- **Merge** two adjacent rooms into one.
- **Draw a no-go (blocking) zone** the robot will avoid.
- **Draw a spot area** for targeted cleaning.
- **Propose / confirm no-go zones** suggested by the robot.
- **Map management** — save edits, rename a map, delete a map.
- **Explore** a brand-new map (guided multi-phase workflow).
- **Cleaning controls** — clean all, stop, return to dock, clean the selected
  area(s), reset statistics, go-to-point.

---

## Running the editor

Opening `rowenta-map-editor.html` directly from disk **does not work** — the
browser blocks the robot's plain-HTTP requests from a `file://` / `https://`
page (mixed-content policy). Always run it through one of the options below,
which serve the page over `http://` and proxy the API calls.

### Option 1 — Desktop launcher (easiest)

Double-click `launch-rowenta-editor.py`, or run it from a terminal. It opens a
small Tk window where you enter the robot IP, start the proxy, open the editor
in your browser, and stop the server when done.

```bash
python3 map_editor/launch-rowenta-editor.py
```

You can also pass the IP (and an optional port) up front:

```bash
python3 map_editor/launch-rowenta-editor.py 192.168.1.50
python3 map_editor/launch-rowenta-editor.py 192.168.1.50 --port 9000
```

The launcher just shells out to `rowenta-editor-server.py`, so it accepts the
same arguments.

### Option 2 — Proxy server directly

```bash
# Serves at http://localhost:8765 and proxies to the robot
python3 map_editor/rowenta-editor-server.py 192.168.1.50

# Custom port / don't auto-open a browser
python3 map_editor/rowenta-editor-server.py 192.168.1.50 --port 9000 --no-browser
```

Then open `http://localhost:8765`. You can omit the IP and type it into the
**Robot IP** field in the editor header instead — the UI POSTs it to the
server's `/config` endpoint, so it can be changed live without a restart.

Requires only the Python standard library (Python 3.6+) — no `pip install`.

### Option 3 — Home Assistant add-on (ingress)

The same server runs as a Home Assistant add-on in **ingress** mode (launched by
the add-on's `run.sh`). The server strips the HA ingress path prefix from
incoming requests, and the robot IP can be updated live from the UI. A `PROXY`
badge appears in the header when running behind the proxy.

### Option 4 — Android app

`map_editor/android/` contains a minimal Android wrapper (`MainActivity.kt` +
`MapEditorServer.kt`) that embeds the same proxy + WebView so the editor can run
on a phone/tablet on the same Wi-Fi network. Build it with Gradle
(`./gradlew assembleDebug`).

---

## The interface

### Header

- **Robot IP** field + **Connect** — connects and loads the map list.
- **Map chips** — one chip per permanent floor map; click to switch the active
  map. Unnamed maps show as `Map 1`, `Map 2`, …

### Toolbar & keyboard shortcuts

| Tool | Key | What it does |
|------|-----|--------------|
| Select | `S` | Select a room to inspect/edit it. |
| Pan | hold `Space` | Drag to pan the canvas (releases back to Select). |
| Split room | `X` | Click two points to draw a split line across the selected room. |
| Merge rooms | `M` | Click two adjacent rooms to merge them. |
| Draw no-go area | — (toolbar) | Drag a rectangle the robot must avoid. |
| Draw spot area | `V` | Drag a rectangle for a targeted spot clean. |
| Go to point | `G` | Click a point to send the robot there. |
| Fit to screen | `F` | Zoom/center the whole map. |
| Cancel | `Esc` | Abort the current draw/merge/split and return to Select. |

Zoom with the on-canvas **+ / −** buttons or the mouse wheel. The status bar at
the bottom shows connection state, the active mode, and cursor coordinates.

> The no-go tool is started from its toolbar button. In a new-map **explore**
> session, `Esc` returns you to the Split tool (the default for the drawing
> phase).

### Room detail panel

Selecting a room opens a panel to:

- **Rename** the room and set **fan speed**, **strategy**, and **floor type**
  (plus **pump volume** on wet-capable models) — saved via `/set/modify_area`.
- **Split**, **Merge**, **Clean this room now**, **Draw no-go**, or
  **Delete area**.

### Map tools panel

Save map edits, rename the map, return the robot home, **propose no-go zones**
(the robot suggests them, you confirm/decline), reset statistics, **explore** a
new map, or **delete the map**.

---

## How it talks to the robot

The proxy forwards any `/get/*` or `/set/*` request to
`http://<robot-ip>:8080` unchanged and returns the response. All editing maps to
these confirmed endpoints:

| Operation | Request |
|-----------|---------|
| Rename / set room params | `GET /set/modify_area?map_id=N&area_id=M&cleaning_parameter_set=…&strategy_mode=normal\|deep&floor_type=…&area_meta_data={"name":"…"}` |
| Split area | `GET /set/split_area?map_id=N&area_id=M&x1=…&y1=…&x2=…&y2=…` |
| Merge areas | `GET /set/merge_areas?map_id=N&area_id1=A&area_id2=B` |
| Add no-go / spot | `GET /set/add_area?map_id=N&area_type=to_be_cleaned&area_state=blocking\|clean&area_meta_data={"name":""}&cleaning_parameter_set=…&floor_type=none&room_type=none&strategy_mode=normal&x1..x4&y1..y4` |
| Propose no-go | `GET /set/propose_nogo_areas?map_id=N` |
| Confirm no-go | `GET /set/confirm_nogo_areas?map_id=N&confirmed_ids=[…]&declined_ids=[…]` |
| Save map | `GET /set/save_map?map_id=N` (then poll `/get/command_result`, ≤ 60 s) |
| Rename map | `GET /set/modify_map?map_id=N&name=S&docking_pose=JSON` (always include `docking_pose`) |
| Delete map | `GET /set/delete_map?map_id=N` |
| Explore new map | `GET /set/explore` (zero params) |
| Robot pose | `GET /get/rob_pose` → `{x1, y1, heading, valid, timestamp}` (1 unit = 2 mm) |

Wire-format notes that the editor relies on:

- `area_meta_data` is **always a JSON string**, even when empty: `{"name":""}`.
- Polygon points are **flat** params (`x1,y1 … x4,y4`) — never a JSON array.
- In `/get/areas`, the area identifier field is **`id`** (not `area_id`);
  `normalize.js` maps it, so both spellings work.
- `strategy_mode` on the wire is only `"normal"` or `"deep"`.
- `permanent_flag` in `/get/maps` is the **string** `"true"`/`"false"`.
- `/get/command_result` returns a **`commands` array**; match your `cmd_id`.

---

## File layout

```
map_editor/
├── launch-rowenta-editor.py     Tk desktop launcher (wraps the server)
├── rowenta-editor-server.py     stdlib HTTP proxy + static file server
├── rowenta-map-editor.html      editor markup (toolbar, panels, SVG canvas)
├── rowenta-map-editor.css       styles
├── js/                          ES modules (loaded by the HTML)
│   ├── api.js          fetch wrapper + /get/command_result polling
│   ├── config.js       proxy vs direct mode, robot IP/port
│   ├── state.js        shared editor state
│   ├── load.js         connect + load maps/areas
│   ├── normalize.js    tolerant area-field normalization (id/area_id/areaId)
│   ├── coords.js       screen ↔ map coordinate transforms
│   ├── render.js       SVG rendering of map, rooms, robot, dock
│   ├── robot.js        live robot pose polling + drawing
│   ├── events.js       pointer + keyboard handling
│   ├── mode.js         tool/mode switching + fit-to-screen
│   ├── overlay.js      transient draw overlays (split line, rectangles)
│   ├── modal.js        prompt/confirm dialogs
│   ├── areas.js        room detail panel + modify_area
│   ├── split.js        split workflow
│   ├── merge.js        merge workflow
│   ├── nogo.js         no-go + spot rectangle workflow (add_area)
│   ├── area_move.js    drag-to-move helpers
│   ├── mapops.js       save / rename / delete map, stats reset
│   └── explore.js      guided new-map exploration workflow
└── android/                     Android WebView + embedded proxy
```

---

## Troubleshooting

- **"Robot IP not set" / 502** — enter the robot's IP in the header (or pass it
  on the command line) so the proxy knows where to forward requests.
- **Nothing loads / connection refused** — confirm the robot is awake and
  reachable at `http://<robot-ip>:8080/get/status` from the same network. The
  robot only serves one HTTP client at a time, so close the native Rowenta app
  while editing.
- **Blank page from `file://`** — don't open the HTML directly; use the launcher,
  the proxy server, or the HA add-on (mixed-content is blocked otherwise).
- **A save seems to "hang"** — `save_map` is asynchronous; the editor polls
  `/get/command_result` for up to ~60 s. Large maps take a few seconds.
- **Map looks empty after `explore`** — finish the guided phases and **save the
  map** before switching away; an unexplored/unsaved map has no room geometry.
