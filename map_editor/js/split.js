// ─────────────────────────────────────────────────────────────────────────────
// SPLIT AREA
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { USE_PROXY, ROBOT_PORT, ROOM_TYPE_OPTIONS } from './config.js';
import * as config from './config.js';
import { computeSplitPoints, svgToRobot } from './coords.js';
import { showModal, showToast, showSpinner, showInstruction, hideInstruction } from './modal.js';
import { clearSplitOverlay, setSplitDot, setSplitLine } from './overlay.js';
import { highlightAreaSplit, renderAreaList, getAreaName } from './render.js';
import { saveArea } from './areas.js';
import { pollCmd } from './api.js';
import { setMode } from './mode.js';

export function startSplit() {
  if (state.selectedAreaId === null) {
    // No area pre-selected — enter split mode and ask user to pick
    state.splitPoints = [];
    clearSplitOverlay();
    setMode('split');
    showInstruction('STEP 1 of 3', 'Click the room you want to split', 'green');
    return;
  }
  // Area already selected — go straight to drawing
  state.splitPoints = [];
  clearSplitOverlay();
  setMode('split');
  highlightAreaSplit(state.selectedAreaId);
  showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
}

// Build split_area URL in one of several point-encoding formats.
// The Robart SDK GET param format for List<Point2D> is not documented;
// probe sequentially until one succeeds.
// Force explicit float notation — JS JSON.stringify drops ".0" for whole numbers.
// Robot's Moshi Float parser may require "118.0" not "118".
export function asFloat(n) {
  const f = parseFloat(n);
  return Number.isInteger(f) ? f.toFixed(1) : String(f);
}
export function jsonPoint(p) {
  // e.g. {"x":118.0,"y":1349.0}
  return `{"x":${asFloat(p.x)},"y":${asFloat(p.y)}}`;
}

export function splitFormats(mapId, areaId, p0, p1) {
  // Confirmed: area field in /get/areas response is "id".
  // URL param name for the area may be area_id, areaId, or id.
  // Points format is unknown — try every plausible encoding.
  const pJsonF  = encodeURIComponent(`[${jsonPoint(p0)},${jsonPoint(p1)}]`); // JSON float array
  const pJsonI  = encodeURIComponent(`[{"x":${p0.x},"y":${p0.y}},{"x":${p1.x},"y":${p1.y}}]`); // JSON int array
  const pCsvF   = `${asFloat(p0.x)},${asFloat(p0.y)},${asFloat(p1.x)},${asFloat(p1.y)}`;
  const pCsvI   = `${p0.x},${p0.y},${p1.x},${p1.y}`;
  const p0jf    = encodeURIComponent(jsonPoint(p0));
  const p1jf    = encodeURIComponent(jsonPoint(p1));
  const p0ji    = encodeURIComponent(`{"x":${p0.x},"y":${p0.y}}`);
  const p1ji    = encodeURIComponent(`{"x":${p1.x},"y":${p1.y}}`);

  const rows = [];
  for (const [aParam, aVal] of [
    ['area_id', areaId],   // snake_case — what other set commands use
    ['areaId',  areaId],   // camelCase  — Kotlin method param name
    ['id',      areaId],   // raw        — confirmed field name in /get/areas
  ]) {
    const b = `/set/split_area?map_id=${mapId}&${aParam}=${aVal}`;
    rows.push(
      { label: `${aParam}+json-float`,   url: b + `&points=${pJsonF}` },
      { label: `${aParam}+json-int`,     url: b + `&points=${pJsonI}` },
      { label: `${aParam}+csv-float`,    url: b + `&points=${pCsvF}` },
      { label: `${aParam}+csv-int`,      url: b + `&points=${pCsvI}` },
      { label: `${aParam}+x1y1-float`,   url: b + `&x1=${asFloat(p0.x)}&y1=${asFloat(p0.y)}&x2=${asFloat(p1.x)}&y2=${asFloat(p1.y)}` },
      { label: `${aParam}+x1y1-int`,     url: b + `&x1=${p0.x}&y1=${p0.y}&x2=${p1.x}&y2=${p1.y}` },
      { label: `${aParam}+point×2-float`,url: b + `&point=${p0jf}&point=${p1jf}` },
      { label: `${aParam}+point×2-int`,  url: b + `&point=${p0ji}&point=${p1ji}` },
    );
  }
  return rows;  // 24 total combinations
}

export async function executeSplit(lineA, lineB) {
  const area = state.areas.find(a => a.area_id === state.selectedAreaId);
  if (!area) return;

  const pts = computeSplitPoints(area, lineA, lineB);
  if (!pts || pts.length < 2) {
    showToast('Line must cross the room boundary at two points — try again', 'error');
    state.splitPoints = [];
    clearSplitOverlay();
    highlightAreaSplit(state.selectedAreaId);
    showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    return;
  }

  console.log('[split] computed boundary points:', pts);

  // ── Confirmation modal ────────────────────────────────────────────────────
  const areaName = getAreaName(area);
  let modalValues;
  try {
    modalValues = await showModal({
      title: `Split "${areaName}"?`,
      desc: `The split line will divide this room into two new segments. ` +
            `You can name them after the split completes.`,
      fields: [],
      confirmLabel: 'Split Room',
      danger: false,
    });
  } catch {
    // User cancelled — stay in split mode so they can redraw
    state.splitPoints = [];
    clearSplitOverlay();
    highlightAreaSplit(state.selectedAreaId);
    showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    return;
  }
  // ─────────────────────────────────────────────────────────────────────────

  hideInstruction();
  showSpinner(true);

  const formats = splitFormats(state.activeMapId, state.selectedAreaId, pts[0], pts[1]);
  const results = [];   // collect {label, status, body} for diagnostics

  // Import loadMap lazily to avoid circular dep
  const { loadMap } = await import('./load.js');

  for (const fmt of formats) {
    let status = 0, body = '';
    try {
      const resp = await fetch(USE_PROXY ? fmt.url : `http://${config.robotIP}:${ROBOT_PORT}${fmt.url}`);
      status = resp.status;
      body   = await resp.text();   // always read body — it has the error message
      console.log(`[split] ${fmt.label} → ${status}  body: ${body}`);
      results.push({ label: fmt.label, status, body });

      if (resp.ok) {
        let res = {};
        try { res = JSON.parse(body); } catch {}
        state.splitFormat = fmt.label;
        console.log(`[split] SUCCESS with "${fmt.label}"`, res);
        showToast(`Splitting… (${fmt.label})`, 'info');
        const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
        try {
          if (cmdId) {
            await pollCmd(cmdId, 30000);
          } else {
            await new Promise(r => setTimeout(r, 3000));
          }
        } catch (e) {
          // The robot accepted the split; command polling is best-effort because
          // some firmwares omit or quickly rotate command_result entries.
          console.warn('[split] command accepted, but polling did not confirm completion:', e);
          showToast('Split accepted; refreshing map...', 'info');
          await new Promise(r => setTimeout(r, 3000));
        }
        await new Promise(r => setTimeout(r, 1500));
        setMode('select');
        state.splitPoints = [];
        clearSplitOverlay();
        const oldIds = new Set(state.areas.map(a => a.area_id));
        try {
          await loadMap(state.activeMapId);
          // Refresh phase bar and re-highlight if in explore draw phase
          if (state.explorePhase === 'drawing') {
            const { _highlightAllInactive, _showPhaseBar } = await import('./explore.js');
            _highlightAllInactive();
            _showPhaseBar('drawing');
            setMode('split');
          }
          showSpinner(false);
          _promptNameNewAreas(oldIds);
        } catch (e) {
          showSpinner(false);
          console.warn('[split] split accepted, but map refresh failed:', e);
          showToast('Split accepted, but map refresh failed. Reload map to see changes.', 'error');
        }
        return;
      }
      // ← no break — always try all formats
    } catch(e) {
      console.warn(`[split] "${fmt.label}" threw:`, e);
      results.push({ label: fmt.label, status: 'ERR', body: String(e) });
    }
  }

  // All formats failed — show diagnostic table in debug box
  showSpinner(false);
  const dbgRaw = document.getElementById('debug-raw');
  const dbgBox = document.getElementById('debug-box');
  if (dbgRaw && dbgBox) {
    // Find unique robot error messages
    const msgs = [...new Set(results.filter(r => r.body).map(r => r.body.substring(0, 120)))];
    dbgRaw.textContent =
      `SPLIT PROBE RESULTS (${results.length} formats tried)\n\n` +
      results.map(r => `[${r.status}] ${r.label}`).join('\n') +
      `\n\nROBOT ERROR MESSAGES:\n` + msgs.join('\n');
    dbgBox.style.display = 'block';
  }

  // First non-empty error body = most useful message
  const firstErr = results.find(r => r.body)?.body || 'unknown';
  showToast(`Split: all ${formats.length} formats → 400. Robot says: ${firstErr.substring(0, 80)}`, 'error');
  state.splitPoints = [];
  clearSplitOverlay();
  setMode('select');
  hideInstruction();
}

// After a split, find the new areas and offer to name them one by one
export async function _promptNameNewAreas(oldIds) {
  const newAreas = state.areas.filter(a =>
    a.area_id !== null && !oldIds.has(a.area_id) && a.area_state !== 'blocking'
  );
  if (newAreas.length === 0) {
    showToast('Split complete', 'success');
    return;
  }
  for (let i = 0; i < newAreas.length; i++) {
    const a = newAreas[i];
    let vals;
    try {
      vals = await showModal({
        title: `Name new area ${i+1} of ${newAreas.length}`,
        desc: `The split created area #${a.area_id}. Give it a name and type.`,
        fields: [
          { key: 'name',      label: 'Room name',  placeholder: 'e.g. Kitchen' },
          { key: 'room_type', label: 'Room type',  type: 'select', options: ROOM_TYPE_OPTIONS, value: 'none' },
        ],
        confirmLabel: 'Save Name',
      });
    } catch {
      // User skipped naming this area — that's fine
      continue;
    }
    if (vals.name) {
      // Apply name by patching the area and calling modify_area
      const idx = state.areas.findIndex(x => x.area_id === a.area_id);
      if (idx >= 0) {
        state.areas[idx].area_meta_data = JSON.stringify({ name: vals.name });
        state.areas[idx].area_state     = 'clean';
        if (vals.room_type) state.areas[idx].room_type = vals.room_type;
        state.selectedAreaId = a.area_id;
        document.getElementById('field-name').value      = vals.name;
        document.getElementById('field-room-type').value = vals.room_type || 'none';
        document.getElementById('field-state').value     = 'clean';
        await saveArea();
      }
    }
  }
  showToast('Split complete', 'success');
}

// Legacy alias — now handled by clearSplitOverlay()
export function hideSplitPreview() { clearSplitOverlay(); }
