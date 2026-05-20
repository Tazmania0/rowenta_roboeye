// SPLIT AREA
import { state } from './state.js';
import { ROOM_TYPE_OPTIONS } from './config.js';
import { computeSplitPoints } from './coords.js';
import { showModal, showToast, showSpinner, showInstruction, hideInstruction } from './modal.js';
import { clearSplitOverlay } from './overlay.js';
import { highlightArea, _panToArea, renderAreaList, getAreaName } from './render.js';
import { saveArea } from './areas.js';
import { api, pollCmd } from './api.js';
import { setMode } from './mode.js';

const areaDetailEl = document.getElementById('area-detail');

export function startSplit() {
  if (state.selectedAreaId === null) {
    state.splitPoints = [];
    clearSplitOverlay();
    setMode('split');
    showInstruction('STEP 1 of 3', 'Click the room you want to split', 'green');
    return;
  }

  state.splitPoints = [];
  clearSplitOverlay();
  setMode('split');
  highlightAreaSplit(state.selectedAreaId);
  showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
}

export function splitAreaPath(mapId, areaId, p0, p1) {
  const params = new URLSearchParams();
  params.set('map_id', mapId);
  params.set('area_id', areaId);
  params.set('x1', Math.round(p0.x));
  params.set('y1', Math.round(p0.y));
  params.set('x2', Math.round(p1.x));
  params.set('y2', Math.round(p1.y));
  return `/set/split_area?${params.toString()}`;
}

export async function executeSplit(lineA, lineB) {
  const area = state.areas.find(a => a.area_id === state.selectedAreaId);
  if (!area) return;

  const pts = computeSplitPoints(area, lineA, lineB);
  if (!pts || pts.length < 2) {
    showToast('Line must cross the room boundary at two points - try again', 'error');
    state.splitPoints = [];
    clearSplitOverlay();
    highlightAreaSplit(state.selectedAreaId);
    showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    return;
  }

  const areaName = getAreaName(area);
  try {
    await showModal({
      title: `Split "${areaName}"?`,
      desc: 'The split line will divide this room into two new segments. You can name them after the split completes.',
      fields: [],
      confirmLabel: 'Split Room',
      danger: false,
    });
  } catch {
    state.splitPoints = [];
    clearSplitOverlay();
    highlightAreaSplit(state.selectedAreaId);
    showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    return;
  }

  hideInstruction();
  showSpinner(true);

  const { loadMap } = await import('./load.js');
  const oldIds = new Set(state.areas.map(a => a.area_id));

  try {
    const res = await api(splitAreaPath(state.activeMapId, state.selectedAreaId, pts[0], pts[1]));
    state.splitFormat = 'area_id+x1y1';
    showToast('Splitting...', 'info');

    const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
    if (cmdId) {
      await pollCmd(cmdId, 30000).catch(async e => {
        console.warn('[split] command accepted, but polling did not confirm completion:', e);
        showToast('Split accepted; refreshing map...', 'info');
        await new Promise(r => setTimeout(r, 3000));
      });
    } else {
      await new Promise(r => setTimeout(r, 3000));
    }

    await new Promise(r => setTimeout(r, 1500));
    setMode('select');
    state.splitPoints = [];
    clearSplitOverlay();

    let splitVisible = false;
    for (let attempt = 0; attempt < 4 && !splitVisible; attempt++) {
      if (attempt > 0) await new Promise(r => setTimeout(r, 2000));
      await loadMap(state.activeMapId);
      splitVisible = state.areas.some(a =>
        a.area_id !== null && !oldIds.has(a.area_id) && a.area_state !== 'blocking'
      );
    }

    if (state.explorePhase === 'drawing') {
      const { _highlightAllInactive, _showPhaseBar } = await import('./explore.js');
      _highlightAllInactive();
      _showPhaseBar('drawing');
      setMode('split');
    }

    showSpinner(false);
    await _promptNameNewAreas(oldIds);
  } catch (e) {
    showSpinner(false);
    showToast('Split failed: ' + e.message.substring(0, 100), 'error');
    state.splitPoints = [];
    clearSplitOverlay();
    setMode('select');
    hideInstruction();
  }
}

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
    state.selectedAreaId = a.area_id;
    _panToArea(a);
    highlightArea(a.area_id);
    renderAreaList();
    areaDetailEl.classList.add('visible');
    document.getElementById('field-name').value = getAreaName(a);
    document.getElementById('field-room-type').value = a.room_type || 'none';
    document.getElementById('field-state').value = a.area_state || 'clean';
    document.getElementById('field-fan').value = String(a.cleaning_parameter_set || 0);
    document.getElementById('field-strategy').value = a.strategy_mode || 'normal';
    const floorTypeEl = document.getElementById('field-floor-type');
    if (floorTypeEl) floorTypeEl.value = a.floor_type || 'none';

    let vals;
    try {
      vals = await showModal({
        title: `Name new area ${i + 1} of ${newAreas.length}`,
        desc: `The split created area #${a.area_id}. Give it a name and type.`,
        fields: [
          { key: 'name', label: 'Room name', placeholder: 'e.g. Kitchen' },
          { key: 'room_type', label: 'Room type', type: 'select', options: ROOM_TYPE_OPTIONS, value: 'none' },
        ],
        confirmLabel: 'Save Name',
      });
    } catch {
      continue;
    }

    if (vals.name) {
      const idx = state.areas.findIndex(x => x.area_id === a.area_id);
      if (idx >= 0) {
        state.areas[idx].area_meta_data = JSON.stringify({ name: vals.name });
        state.areas[idx].area_state = 'clean';
        if (vals.room_type) state.areas[idx].room_type = vals.room_type;
        state.selectedAreaId = a.area_id;
        document.getElementById('field-name').value = vals.name;
        document.getElementById('field-room-type').value = vals.room_type || 'none';
        document.getElementById('field-state').value = 'clean';
        await saveArea();
      }
    }
  }

  showToast('Split complete', 'success');
}

export function hideSplitPreview() {
  clearSplitOverlay();
}
