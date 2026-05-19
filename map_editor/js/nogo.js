// ─────────────────────────────────────────────────────────────────────────────
// NO-GO / SPOT / BLOCK DRAG-TO-DRAW HELPERS
// CONFIRMED: id must be absent; area_type must be "to_be_cleaned" for blocking
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { USE_PROXY, ROBOT_PORT, ROOM_TYPE_OPTIONS } from './config.js';
import * as config from './config.js';
import { svgToRobot, eventToSVGPoint } from './coords.js';
import { showModal, showToast, showSpinner, showInstruction, hideInstruction } from './modal.js';
import { clearSplitOverlay, setSplitDot, setSpotRect } from './overlay.js';
import { getAreaMeta, isSpotArea } from './render.js';
import { api, apiText, pollCmd } from './api.js';
import { setMode } from './mode.js';
import { extractAreas } from './normalize.js';

export function startBlockZone() {
  if (!state.activeMapId || !state.bbox) {
    showToast('Load a map first', 'error');
    return;
  }
  state.splitPoints = [];
  clearSplitOverlay();
  setMode('block');
  showInstruction('NO-GO AREA', 'Click first corner of the blocked rectangle', 'red');
}

export function startBlock() {
  if (!state.activeMapId || !state.bbox) {
    showToast('Load a map first', 'error');
    return;
  }
  state.splitPoints = [];
  state.rectStart   = null;
  clearSplitOverlay();
  setMode('block');
  showInstruction('BLOCK', 'Click and drag to draw a no-go rectangle', 'blue');
}

export function startSpot() {
  if (!state.activeMapId || !state.bbox) {
    showToast('Load a map first', 'error');
    return;
  }
  state.splitPoints = [];
  state.rectStart   = null;
  clearSplitOverlay();
  setMode('spot');
  showInstruction('SPOT', 'Click and drag to draw a spot-clean rectangle', 'amber');
}

function blockAreaObject(points, camel=false, opts={}) {
  const includeId = opts.includeId || false;
  const includeExtra = opts.includeExtra !== false;
  if (camel) {
    return {
      ...(includeId ? { areaId: 0 } : {}),
      areaType: 'to_be_cleaned',
      areaState: 'blocking',
      metaData: '',
      roomType: 'none',
      cleaningParameterSet: 0,
      ...(includeExtra ? { floorType: 'none', strategyMode: 'normal' } : {}),
      points,
    };
  }
  return {
    ...(includeId ? { id: 0 } : {}),
    area_type: 'to_be_cleaned',
    area_state: 'blocking',
    area_meta_data: '',
    room_type: 'none',
    cleaning_parameter_set: 0,
    ...(includeExtra ? { floor_type: 'none', strategy_mode: 'normal' } : {}),
    points,
  };
}

function blockZoneFormats(mapId, points) {
  const camelMinimal = blockAreaObject(points, true, { includeExtra: false });
  const camelWithAreaId = blockAreaObject(points, true, { includeId: true });
  const snakeArea = blockAreaObject(points);
  const snakeWithId = blockAreaObject(points, false, { includeId: true });
  const flatPoints = points.map(p => `${p.x},${p.y}`).join(',');
  return [
    { label: 'add_block+camel-minimal-reference', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(camelMinimal))}` },
    { label: 'add_block+camel-areaId', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(camelWithAreaId))}` },
    { label: 'add_block+snake-area', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(snakeArea))}` },
    { label: 'add_block+snake-id', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(snakeWithId))}` },
    { label: 'add_block+flat-points', url: `/set/add_area?map_id=${mapId}&area_type=to_be_cleaned&area_state=blocking&area_meta_data=&room_type=none&points=${encodeURIComponent(flatPoints)}` },
  ];
}

export async function executeBlockZone(svgA, svgB) {
  const minX = Math.min(svgA.x, svgB.x);
  const maxX = Math.max(svgA.x, svgB.x);
  const minY = Math.min(svgA.y, svgB.y);
  const maxY = Math.max(svgA.y, svgB.y);
  const points = [
    svgToRobot(minX, minY),
    svgToRobot(minX, maxY),
    svgToRobot(maxX, maxY),
    svgToRobot(maxX, minY),
  ];
  const w = Math.abs(points[3].x - points[0].x);
  const h = Math.abs(points[1].y - points[0].y);

  try {
    await showModal({
      title: 'Create no-go area?',
      desc: `Save a permanent blocked rectangle (${(w * 0.002).toFixed(2)}m x ${(h * 0.002).toFixed(2)}m).`,
      fields: [],
      confirmLabel: 'Create No-go',
    });
  } catch {
    clearSplitOverlay();
    state.splitPoints = [];
    showInstruction('NO-GO AREA', 'Click first corner of the blocked rectangle', 'red');
    return;
  }

  hideInstruction();
  showSpinner(true);
  const results = [];
  try {
    for (const fmt of blockZoneFormats(state.activeMapId, points)) {
      try {
        const resp = await fetch(USE_PROXY ? fmt.url : `http://${config.robotIP}:${ROBOT_PORT}${fmt.url}`);
        const body = await resp.text();
        results.push({ label: fmt.label, status: resp.status, body });
        console.log(`[block] ${fmt.label} -> ${resp.status} body: ${body}`);
        if (!resp.ok) {
          if (resp.status === 400 || resp.status === 404) continue;
          throw new Error(`HTTP ${resp.status}`);
        }
        let res = {};
        try { res = JSON.parse(body); } catch {}
        const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
        if (cmdId) {
          await pollCmd(cmdId, 30000).catch(e =>
            console.warn('[block] add_area accepted, but polling did not confirm completion:', e)
          );
        }
        await new Promise(r => setTimeout(r, 1500));
        const { loadMap } = await import('./load.js');
        await loadMap(state.activeMapId);
        showToast('No-go area created', 'success');
        setMode('select');
        state.splitPoints = [];
        clearSplitOverlay();
        return;
      } catch (e) {
        results.push({ label: fmt.label, status: 'ERR', body: String(e) });
        console.warn(`[block] ${fmt.label} failed:`, e);
      }
    }
    const dbgRaw = document.getElementById('debug-raw');
    const dbgBox = document.getElementById('debug-box');
    if (dbgRaw && dbgBox) {
      dbgRaw.textContent =
        `NO-GO AREA PROBE RESULTS (${results.length} formats tried)\n\n` +
        results.map(r => `[${r.status}] ${r.label} ${r.body ? '- ' + r.body.substring(0, 120) : ''}`).join('\n');
      dbgBox.style.display = 'block';
    }
    showToast('No-go area command not accepted by this firmware', 'error');
  } finally {
    showSpinner(false);
    state.splitPoints = [];
    clearSplitOverlay();
    setMode('select');
  }
}

export function buildAreaPayload(p0, p1, kind = 'block') {
  const minX = Math.min(p0.x, p1.x);
  const maxX = Math.max(p0.x, p1.x);
  const minY = Math.min(p0.y, p1.y);
  const maxY = Math.max(p0.y, p1.y);
  return {
    area_type:              'to_be_cleaned',
    area_state:             kind === 'block' ? 'blocking' : 'clean',
    area_meta_data:         '',
    room_type:              'none',
    floor_type:             'none',
    cleaning_parameter_set: 0,
    method:                 'normal',
    pump_volume:            'default',
    strategy_mode:          'normal',
    points: [
      { x: minX, y: maxY },
      { x: maxX, y: maxY },
      { x: maxX, y: minY },
      { x: minX, y: minY },
    ],
  };
}

export async function executeAddArea(payload, mapId) {
  const clean = Object.assign({}, payload);
  delete clean.id;
  delete clean.area_id;
  delete clean.statistics;
  delete clean._raw;

  const areaJson = encodeURIComponent(JSON.stringify(clean));
  const path     = `/set/add_area?map_id=${mapId}&area=${areaJson}`;
  const url      = USE_PROXY ? path : `http://${config.robotIP}:${ROBOT_PORT}${path}`;

  const resp = await fetch(url);
  const body = await resp.text();
  console.log(`[add_area] HTTP ${resp.status}  body: ${body}`);

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${body.substring(0, 200)}`);
  }

  let res = {};
  try { res = JSON.parse(body); } catch {}
  return res;
}

export async function executeNogo(svgX1, svgY1, svgX2, svgY2) {
  const p0 = svgToRobot(svgX1, svgY1);
  const p1 = svgToRobot(svgX2, svgY2);

  if (Math.abs(p1.x - p0.x) < 20 || Math.abs(p1.y - p0.y) < 20) {
    showToast('Draw a larger rectangle', 'error');
    clearSplitOverlay();
    state.rectStart = null;
    state.rectMode  = null;
    return;
  }

  try {
    await showModal({
      title:        'Add no-go zone?',
      desc:         'A permanent blocked area will be added to this map. The robot will avoid it during cleaning.',
      confirmLabel: 'Add No-Go Zone',
      danger:       true,
    });
  } catch {
    clearSplitOverlay();
    state.rectStart = null;
    showInstruction('BLOCK', 'Click and drag to draw a no-go rectangle', 'blue');
    return;
  }

  clearSplitOverlay();
  state.rectStart = null;
  state.rectMode  = null;
  showSpinner(true);

  try {
    const payload = buildAreaPayload(p0, p1, 'block');
    const res     = await executeAddArea(payload, state.activeMapId);

    const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
    if (cmdId) {
      showToast('Adding no-go zone…', 'info');
      await pollCmd(cmdId, 15000);
    } else {
      await new Promise(r => setTimeout(r, 2000));
    }
    await new Promise(r => setTimeout(r, 1000));
    const { loadMap } = await import('./load.js');
    await loadMap(state.activeMapId);
    showToast('No-go zone added', 'success');
    setMode('select');
    // Refresh phase bar and re-highlight if in explore draw phase
    if (state.explorePhase === 'drawing') {
      const { _highlightAllInactive, _showPhaseBar } = await import('./explore.js');
      _highlightAllInactive();
      _showPhaseBar('drawing');
      setMode('split');
    }
  } catch (e) {
    console.error('[nogo]', e);
    showToast('Failed: ' + e.message.substring(0, 100), 'error');
    setMode('select');
  } finally {
    showSpinner(false);
    hideInstruction();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SPOT CLEAN HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function spotAreaObject(points, areaId=null, camel=false) {
  const meta = JSON.stringify({ name: '', customState: 'spot' });
  if (camel) {
    return {
      ...(areaId !== null ? { id: areaId } : {}),
      areaType: 'to_be_cleaned',
      areaState: 'clean',
      metaData: meta,
      roomType: 'none',
      cleaningParameterSet: 0,
      floorType: 'none',
      strategyMode: 'normal',
      points,
    };
  }
  return {
    ...(areaId !== null ? { id: areaId } : {}),
    area_type: 'to_be_cleaned',
    area_state: 'clean',
    area_meta_data: meta,
    room_type: 'none',
    cleaning_parameter_set: 0,
    floor_type: 'none',
    strategy_mode: 'normal',
    points,
  };
}

function spotCleanFormats(mapId, points) {
  const snakeArea = spotAreaObject(points);
  const camelArea = spotAreaObject(points, null, true);
  const flatPoints = points.map(p => `${p.x},${p.y}`).join(',');
  return [
    { label: 'add_area+snake-area', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(snakeArea))}` },
    { label: 'add_area+camel-area', url: `/set/add_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(camelArea))}` },
    { label: 'add_area+flat-points', url: `/set/add_area?map_id=${mapId}&area_type=to_be_cleaned&area_state=clean&area_meta_data=${encodeURIComponent(snakeArea.area_meta_data)}&room_type=none&points=${encodeURIComponent(flatPoints)}` },
  ];
}

function spotModifyFormats(mapId, spotAreaId, points) {
  const snakeArea = spotAreaObject(points, spotAreaId);
  const camelArea = spotAreaObject(points, spotAreaId, true);
  const flatPoints = points.map(p => `${p.x},${p.y}`).join(',');
  return [
    { label: 'modify_area+snake-area', url: `/set/modify_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(snakeArea))}` },
    { label: 'modify_area+camel-area', url: `/set/modify_area?map_id=${mapId}&area=${encodeURIComponent(JSON.stringify(camelArea))}` },
    { label: 'modify_area+flat-points', url: `/set/modify_area?map_id=${mapId}&area_id=${spotAreaId}&area_type=to_be_cleaned&area_state=clean&area_meta_data=${encodeURIComponent(snakeArea.area_meta_data)}&cleaning_parameter_set=0&strategy_mode=normal&room_type=none&points=${encodeURIComponent(flatPoints)}` },
  ];
}

function findReusableSpotArea(areas) {
  const spots = areas.filter(a => {
    const meta = getAreaMeta(a);
    return isSpotArea(a) || meta.customState === 'spot';
  });
  return spots.find(a => getAreaMeta(a).customState === 'spot') || spots[0] || null;
}

function areaPointsMatch(area, points) {
  if (!area || !Array.isArray(area.points)) return false;
  const want = new Set(points.map(p => `${p.x},${p.y}`));
  const got = new Set(area.points.map(p => `${p.x},${p.y}`));
  return got.size === want.size && [...want].every(k => got.has(k));
}

async function fetchAreasOnly(mapId) {
  const res = await api(`/get/areas?map_id=${mapId}`);
  return extractAreas(res);
}

function extractAreaIdFromResponse(res) {
  if (!res || typeof res !== 'object') return null;
  return res.id ?? res.area_id ?? res.areaId ?? res.created_area_id ?? res.createdAreaId ?? null;
}

function findCreatedSpotArea(beforeIds, afterAreas, points) {
  const newAreas = afterAreas.filter(a => a.area_id !== null && !beforeIds.has(String(a.area_id)));
  const spotNew = newAreas.find(a => a.area_type === 'to_be_cleaned');
  if (spotNew) return spotNew;
  if (newAreas.length === 1) return newAreas[0];

  const want = new Set(points.map(p => `${p.x},${p.y}`));
  return afterAreas.find(a => {
    if (a.area_type !== 'to_be_cleaned' || !Array.isArray(a.points)) return false;
    const got = new Set(a.points.map(p => `${p.x},${p.y}`));
    if (got.size !== want.size) return false;
    return [...want].every(k => got.has(k));
  }) || null;
}

async function tryUpdateSpotArea(mapId, spotAreaId, points, results) {
  for (const fmt of spotModifyFormats(mapId, spotAreaId, points)) {
    try {
      const resp = await fetch(USE_PROXY ? fmt.url : `http://${config.robotIP}:${ROBOT_PORT}${fmt.url}`);
      const body = await resp.text();
      results.push({ label: fmt.label, status: resp.status, body });
      console.log(`[spot] ${fmt.label} -> ${resp.status} body: ${body}`);
      if (!resp.ok) {
        if (resp.status === 400 || resp.status === 404) continue;
        throw new Error(`HTTP ${resp.status}`);
      }
      let res = {};
      try { res = JSON.parse(body); } catch {}
      const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
      if (cmdId) {
        await pollCmd(cmdId, 30000).catch(e =>
          console.warn('[spot] modify accepted, but polling did not confirm completion:', e)
        );
      }
      await new Promise(r => setTimeout(r, 1500));
      const afterAreas = await fetchAreasOnly(mapId);
      const updated = afterAreas.find(a => String(a.area_id) === String(spotAreaId));
      if (areaPointsMatch(updated, points)) return updated;
    } catch (e) {
      results.push({ label: fmt.label, status: 'ERR', body: String(e) });
      console.warn(`[spot] ${fmt.label} failed:`, e);
    }
  }
  return null;
}

function cleaningStrategyValue(mode) {
  if (mode === 'deep') return '3';
  if (mode === 'normal') return '1';
  return '4';
}

async function startCleanMapArea(mapId, areaId, fanSpeed, strategyMode) {
  const speed = fanSpeed && fanSpeed !== '0' ? fanSpeed : '2';
  const strategy = cleaningStrategyValue(strategyMode);
  const base =
    `/set/clean_map?map_id=${mapId}` +
    `&cleaning_parameter_set=${encodeURIComponent(speed)}` +
    `&cleaning_strategy_mode=${encodeURIComponent(strategy)}`;
  const formats = [
    { label: 'clean_map+area_ids', url: `${base}&area_ids=${encodeURIComponent(String(areaId))}` },
    { label: 'clean_map+area_id',  url: `${base}&area_id=${encodeURIComponent(String(areaId))}` },
  ];
  let lastErr = null;
  for (const fmt of formats) {
    try {
      const res = await apiText(fmt.url);
      console.log(`[spot] ${fmt.label} accepted`, res);
      const cmdId = res?.cmd_id ?? res?.cmdId ?? res?.command_id;
      if (cmdId) {
        await pollCmd(cmdId, 30000).catch(e =>
          console.warn('[spot] clean_map accepted, but polling did not confirm completion:', e)
        );
      }
      return res;
    } catch (e) {
      lastErr = e;
      console.warn(`[spot] ${fmt.label} failed:`, e);
      if (e.status !== 400 && e.status !== 404) throw e;
    }
  }
  throw lastErr || new Error('clean_map failed');
}

export async function executeSpotClean(svgA, svgB) {
  const beforeIds = new Set(state.areas.map(a => String(a.area_id)));
  const minX = Math.min(svgA.x, svgB.x);
  const maxX = Math.max(svgA.x, svgB.x);
  const minY = Math.min(svgA.y, svgB.y);
  const maxY = Math.max(svgA.y, svgB.y);
  const points = [
    svgToRobot(minX, minY),
    svgToRobot(minX, maxY),
    svgToRobot(maxX, maxY),
    svgToRobot(maxX, minY),
  ];
  const w = Math.abs(points[3].x - points[0].x);
  const h = Math.abs(points[1].y - points[0].y);

  let opts;
  try {
    opts = await showModal({
      title: 'Spot clean rectangle?',
      desc: `Create and clean a rectangle (${(w * 0.002).toFixed(2)}m x ${(h * 0.002).toFixed(2)}m).`,
      fields: [
        {
          key: 'fan',
          label: 'Fan speed',
          type: 'select',
          value: '2',
          options: [
            { value: '2', label: 'Eco' },
            { value: '1', label: 'Normal' },
            { value: '3', label: 'High' },
            { value: '4', label: 'Silent' },
          ],
        },
        {
          key: 'strategy',
          label: 'Strategy',
          type: 'select',
          value: 'normal',
          options: [
            { value: 'normal', label: 'Normal' },
            { value: 'deep', label: 'Deep clean' },
          ],
        },
      ],
      confirmLabel: 'Clean Spot',
    });
  } catch {
    clearSplitOverlay();
    state.splitPoints = [];
    showInstruction('SPOT CLEAN', 'Click first corner of the rectangle', 'amber');
    return;
  }

  hideInstruction();
  showSpinner(true);
  const results = [];
  const { loadMap } = await import('./load.js');
  try {
    const reusableSpot = findReusableSpotArea(state.areas);
    if (reusableSpot?.area_id !== null && reusableSpot?.area_id !== undefined) {
      const updatedSpot = await tryUpdateSpotArea(state.activeMapId, reusableSpot.area_id, points, results);
      if (updatedSpot) {
        try {
          await startCleanMapArea(state.activeMapId, updatedSpot.area_id, opts.fan, opts.strategy);
        } catch (cleanErr) {
          console.warn('[spot] clean_map failed after spot area update:', cleanErr);
          await loadMap(state.activeMapId);
          showToast(`Spot area #${updatedSpot.area_id} updated, but clean command failed`, 'error');
          setMode('select');
          state.splitPoints = [];
          clearSplitOverlay();
          return;
        }
        await loadMap(state.activeMapId);
        showToast(`Spot clean started for area #${updatedSpot.area_id}`, 'success');
        setMode('select');
        state.splitPoints = [];
        clearSplitOverlay();
        return;
      }
      console.warn(`[spot] could not update existing spot area #${reusableSpot.area_id}; falling back to add_area`);
    }

    for (const fmt of spotCleanFormats(state.activeMapId, points)) {
      try {
        const resp = await fetch(USE_PROXY ? fmt.url : `http://${config.robotIP}:${ROBOT_PORT}${fmt.url}`);
        const body = await resp.text();
        results.push({ label: fmt.label, status: resp.status, body });
        console.log(`[spot] ${fmt.label} -> ${resp.status} body: ${body}`);
        if (!resp.ok) {
          if (resp.status === 400 || resp.status === 404) continue;
          throw new Error(`HTTP ${resp.status}`);
        }
        let res = {};
        try { res = JSON.parse(body); } catch {}
        let createdAreaId = extractAreaIdFromResponse(res);
        const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
        if (cmdId) {
          await pollCmd(cmdId, 30000).catch(e =>
            console.warn('[spot] command accepted, but polling did not confirm completion:', e)
          );
        }
        await new Promise(r => setTimeout(r, 1500));
        const afterAreas = await fetchAreasOnly(state.activeMapId);
        if (!createdAreaId) {
          createdAreaId = findCreatedSpotArea(beforeIds, afterAreas, points)?.area_id ?? null;
        }
        if (!createdAreaId) {
          state.areas = afterAreas;
          await loadMap(state.activeMapId);
          showToast('Spot area created, but new area id was not found', 'error');
          setMode('select');
          state.splitPoints = [];
          clearSplitOverlay();
          return;
        }
        try {
          await startCleanMapArea(state.activeMapId, createdAreaId, opts.fan, opts.strategy);
        } catch (cleanErr) {
          console.warn('[spot] clean_map failed after spot area creation:', cleanErr);
          await loadMap(state.activeMapId);
          showToast(`Spot area #${createdAreaId} created, but clean command failed`, 'error');
          setMode('select');
          state.splitPoints = [];
          clearSplitOverlay();
          return;
        }
        await loadMap(state.activeMapId);
        showToast(`Spot clean started for area #${createdAreaId}`, 'success');
        setMode('select');
        state.splitPoints = [];
        clearSplitOverlay();
        return;
      } catch (e) {
        results.push({ label: fmt.label, status: 'ERR', body: String(e) });
        console.warn(`[spot] ${fmt.label} failed:`, e);
      }
    }
    const dbgRaw = document.getElementById('debug-raw');
    const dbgBox = document.getElementById('debug-box');
    if (dbgRaw && dbgBox) {
      dbgRaw.textContent =
        `SPOT CLEAN PROBE RESULTS (${results.length} formats tried)\n\n` +
        results.map(r => `[${r.status}] ${r.label} ${r.body ? '- ' + r.body.substring(0, 120) : ''}`).join('\n');
      dbgBox.style.display = 'block';
    }
    showToast('Spot area command not accepted by this firmware', 'error');
  } finally {
    showSpinner(false);
    state.splitPoints = [];
    clearSplitOverlay();
    setMode('select');
  }
}

export async function executeSpot(svgX1, svgY1, svgX2, svgY2) {
  const p0 = svgToRobot(svgX1, svgY1);
  const p1 = svgToRobot(svgX2, svgY2);

  if (Math.abs(p1.x - p0.x) < 20 || Math.abs(p1.y - p0.y) < 20) {
    showToast('Draw a larger rectangle', 'error');
    clearSplitOverlay();
    state.rectStart = null;
    state.rectMode  = null;
    return;
  }

  const cx = Math.round((p0.x + p1.x) / 2);
  const cy = Math.round((p0.y + p1.y) / 2);
  const rw = Math.abs(p1.x - p0.x);
  const rh = Math.abs(p1.y - p0.y);
  const r  = Math.round(Math.max(rw, rh) / 2);

  try {
    await showModal({
      title:        'Start spot clean?',
      desc:         'Robot will immediately clean the selected rectangle area.',
      confirmLabel: 'Start Spot Clean',
    });
  } catch {
    clearSplitOverlay();
    state.rectStart = null;
    showInstruction('SPOT', 'Click and drag to draw a spot-clean rectangle', 'amber');
    return;
  }

  clearSplitOverlay();
  state.rectStart = null;
  state.rectMode  = null;

  const minX = Math.min(p0.x, p1.x);
  const maxX = Math.max(p0.x, p1.x);
  const minY = Math.min(p0.y, p1.y);
  const maxY = Math.max(p0.y, p1.y);

  // Probe parameter formats — first 200 response logs the working format
  const candidates = [
    `/set/clean_spot?map_id=${state.activeMapId}&x=${cx}&y=${cy}&r=${r}`,
    `/set/clean_spot?map_id=${state.activeMapId}&x1=${minX}&y1=${minY}&x2=${maxX}&y2=${maxY}`,
    `/set/clean_spot?map_id=${state.activeMapId}&point=${encodeURIComponent(
      JSON.stringify({x: parseFloat(cx.toFixed(1)), y: parseFloat(cy.toFixed(1))}))}&radius=${r}`,
    `/set/clean_spot?map_id=${state.activeMapId}&area=${encodeURIComponent(
      JSON.stringify(buildAreaPayload(p0, p1, 'spot')))}`,
    `/set/clean_spot?map_id=${state.activeMapId}&x=${cx}&y=${cy}&r=${r}&cleaning_parameter_set=0`,
  ];

  showSpinner(true);
  let success = false;

  for (const path of candidates) {
    const url  = USE_PROXY ? path : `http://${config.robotIP}:${ROBOT_PORT}${path}`;
    try {
      const resp = await fetch(url);
      const body = await resp.text();
      console.log(`[clean_spot] ${path.split('?')[1].substring(0, 60)} → ${resp.status} ${body}`);

      if (resp.ok) {
        let res = {};
        try { res = JSON.parse(body); } catch {}
        console.log(`[clean_spot] SUCCESS: ${path}`);
        const cmdId = res.cmd_id ?? res.cmdId;
        if (cmdId) await pollCmd(cmdId, 30000);
        showToast('Spot clean started', 'success');
        success = true;
        break;
      }
    } catch (e) {
      console.warn(`[clean_spot] threw: ${e}`);
    }
  }

  showSpinner(false);
  hideInstruction();
  setMode('select');

  if (!success) {
    showToast('clean_spot: all formats failed — check console', 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SVG CLICK HANDLERS FOR SPOT/BLOCK
// ─────────────────────────────────────────────────────────────────────────────
export function handleSpotClick(e) {
  e.preventDefault();
  e.stopPropagation();

  const pt = eventToSVGPoint(e);
  state.splitPoints.push(pt);
  if (state.splitPoints.length === 1) {
    setSplitDot(pt.x, pt.y);
    setSpotRect(pt.x, pt.y, pt.x, pt.y);
    showInstruction('SPOT CLEAN', 'Click opposite corner of the rectangle', 'amber');
  } else if (state.splitPoints.length === 2) {
    executeSpotClean(state.splitPoints[0], state.splitPoints[1]);
  }
}

export function handleBlockClick(e) {
  e.preventDefault();
  e.stopPropagation();

  const pt = eventToSVGPoint(e);
  state.splitPoints.push(pt);
  if (state.splitPoints.length === 1) {
    setSplitDot(pt.x, pt.y);
    setSpotRect(pt.x, pt.y, pt.x, pt.y, 'block');
    showInstruction('NO-GO AREA', 'Click opposite corner of the blocked rectangle', 'red');
  } else if (state.splitPoints.length === 2) {
    executeBlockZone(state.splitPoints[0], state.splitPoints[1]);
  }
}
