// ─────────────────────────────────────────────────────────────────────────────
// MAP LOADING
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { api, pollCmd } from './api.js';
import { extractAreas } from './normalize.js';
import { renderMap, renderAreaList, renderMapChips } from './render.js';
import { showToast, showSpinner, setStatus, showModal, hideInstruction } from './modal.js';
import { fitToScreen } from './mode.js';
import { updateEtaChip } from './eta.js';

const emptyState = document.getElementById('empty-state');

// Reduce-based min/max — Math.min(...arr) spreads every element as an argument
// and throws RangeError ("Maximum call stack size exceeded") on very large
// coordinate arrays (dense outlines). reduce avoids the spread entirely.
function arrMin(arr) { return arr.reduce((m, v) => (v < m ? v : m), Infinity); }
function arrMax(arr) { return arr.reduce((m, v) => (v > m ? v : m), -Infinity); }

async function _discardExploreMap(exploreMapId) {
  try {
    await showModal({
      title:        'Discard unsaved map?',
      desc:         'The explored map will be discarded. No data is lost — your other maps are safe.',
      confirmLabel: 'Discard',
      danger:       true,
    });
  } catch { return; }
  showSpinner(true);
  try {
    const res   = await api(`/set/revert_map?map_id=${exploreMapId}`);
    const cmdId = res?.cmd_id ?? res?.cmdId;
    if (cmdId) await pollCmd(cmdId, 30000);
    state.exploreMapId = null;
    state.explorePhase = null;
    hideInstruction();
    const { _showPhaseBar, _hideSaveMapButton } = await import('./explore.js');
    _showPhaseBar(null);
    _hideSaveMapButton();
    await loadMaps();
    if (state.maps.length > 0) await loadMap(state.maps[0].map_id);
    showToast('Unsaved map discarded', 'success');
  } catch (e) {
    showToast('Discard failed: ' + e.message, 'error');
  } finally {
    showSpinner(false);
  }
}

export async function loadMaps() {
  setStatus('Loading maps…', 'busy');
  const res = await api('/get/maps');
  console.log('[rowenta-editor] /get/maps response:', JSON.stringify(res, null, 2));
  state.maps = (res.maps || []).filter(m => m.permanent_flag === 'true' || m.permanent_flag === true);
  renderMapChips({ onChipClick: loadMap, onDiscardClick: _discardExploreMap });
  setStatus(`Connected — ${state.maps.length} map(s)`, 'ok');
  state.connected = true;
  _updateDeleteBtn();
  // Await so callers (and the status line) reflect the map actually being
  // loaded rather than resolving while loadMap is still in flight.
  if (state.maps.length > 0) await loadMap(state.maps[0].map_id);
}

export async function reloadMapChips() {
  const res  = await api('/get/maps');
  state.maps = (res.maps || []).filter(m =>
    String(m.permanent_flag).toLowerCase() === 'true' || m.map_id === state.exploreMapId
  );
  renderMapChips({ onChipClick: loadMap, onDiscardClick: _discardExploreMap });
}

export function _updateDeleteBtn() {
  const btn = document.getElementById('btn-delete-map');
  if (!btn) return;
  const canDelete = state.maps.filter(
    m => String(m.permanent_flag).toLowerCase() === 'true'
  ).length > 1;
  btn.disabled = !canDelete || !state.activeMapId;
  btn.title    = canDelete
    ? `Delete map ${state.activeMapId}`
    : 'Cannot delete — only one map exists';
}

export async function loadLastSessionGrid(mapId) {
  state.lastSessionGrid = null;
  state.lastSessionGridMapId = null;

  try {
    const grid = await api(`/get/cleaning_grid_map?map_id=${mapId}`);
    const gridMapId = grid?.map_id ?? grid?.mapId ?? mapId;
    if (grid?.size_x > 0 && String(gridMapId) === String(mapId)) {
      state.lastSessionGrid = grid;
      state.lastSessionGridMapId = String(gridMapId);
    }
  } catch (err) {
    console.debug('[rowenta-editor] last session grid unavailable:', err);
  }
}

export async function loadMap(mapId) {
  if (state.explorePhase === 'running'
      && state.activeMapId
      && String(mapId) !== String(state.activeMapId)) {
    console.log('[rowenta-editor] Keeping current map visible during exploration; ignored map_id:', mapId);
    return;
  }

  state.activeMapId = mapId;
  state.selectedAreaId = null;
  state.selectedAreaIds = new Set();
  state.cleaningGrid = null;
  state.lastSessionGrid = null;
  state.lastSessionGridMapId = null;
  state.splitPoints = [];
  renderMapChips({ onChipClick: loadMap, onDiscardClick: _discardExploreMap });
  showSpinner(true);
  emptyState.style.display = 'none';
  // Clear stale debug data from previous map load
  const debugBox = document.getElementById('debug-box');
  if (debugBox) debugBox.style.display = 'none';

  try {
    const [areasRes, tileRes, featureRes] = await Promise.all([
      api(`/get/areas?map_id=${mapId}`),
      api(`/get/tile_map?map_id=${mapId}`),
      api(`/get/feature_map?map_id=${mapId}`),
    ]);
    await loadLastSessionGrid(mapId);

    console.log('[rowenta-editor] /get/areas keys:', Object.keys(areasRes));
    console.log('[rowenta-editor] /get/tile_map keys:', Object.keys(tileRes));
    console.log('[rowenta-editor] /get/feature_map keys:', Object.keys(featureRes));

    state.areas = extractAreas(areasRes);

    // Compute bounding box — outline may be at different nesting levels
    const outline = tileRes.outline
                 ?? tileRes.map?.outline
                 ?? tileRes.tileMap?.outline
                 ?? [];
    console.log('[rowenta-editor] outline length:', outline.length,
                outline.length ? outline[0] : '(empty)');
    if (outline.length > 0) {
      const xs = outline.map(p => p.x);
      const ys = outline.map(p => p.y);
      state.bbox = {
        minX: arrMin(xs), maxX: arrMax(xs),
        minY: arrMin(ys), maxY: arrMax(ys),
      };
    } else if (state.areas.length > 0) {
      // Fallback: compute from area points
      const allPts = state.areas.flatMap(a => a.points || []);
      state.bbox = {
        minX: arrMin(allPts.map(p=>p.x)),
        maxX: arrMax(allPts.map(p=>p.x)),
        minY: arrMin(allPts.map(p=>p.y)),
        maxY: arrMax(allPts.map(p=>p.y)),
      };
    } else {
      state.bbox = { minX:0, maxX:1000, minY:0, maxY:1000 };
    }

    const featureMap = featureRes.map ?? featureRes.featureMap ?? featureRes;
    const walls = featureMap.lines ?? featureMap.segments ?? [];
    const dock  = featureMap.docking_pose ?? featureMap.dockingPose ?? featureMap.dockingPoseResponse ?? null;
    state.dockingPose = dock;   // cache for executeSaveMap
    console.log('[rowenta-editor] walls:', walls.length, '  dock:', dock);

    state._lastWalls = walls;
    state._lastDock  = dock;
    state.mapHasUnsavedEdits = false;
    renderMap(walls, dock);
    renderAreaList();
    updateEtaChip();
    const btnCleanArea = document.getElementById('btn-clean-area');
    if (btnCleanArea) {
      btnCleanArea.disabled = true;
      btnCleanArea.textContent = '▶ Clean Selected';
    }
    // Refresh phase bar if we are in the post-explore flow
    if (state.explorePhase === 'drawing') {
      const { _highlightAllInactive, _showPhaseBar } = await import('./explore.js');
      _highlightAllInactive();
      _showPhaseBar('drawing');
    } else if (state.explorePhase === 'naming') {
      const { _showPhaseBar } = await import('./explore.js');
      _showPhaseBar('naming');
    }
    fitToScreen();
    showToast(`Loaded map ${mapId} — ${state.areas.length} areas`, 'success');
    // Update map operations buttons
    const { _updateMapOpsButtons } = await import('./mapops.js');
    _updateMapOpsButtons();
    // Check capability for auto no-go zones
    api('/get/product_feature_set').then(f => {
      const has = f?.features?.includes('auto_no_go_zones') || f?.auto_no_go_zones === true;
      const btn = document.getElementById('btn-propose-nogo');
      if (btn) btn.style.display = has ? '' : 'none';
    }).catch(() => {});
    // Start live polling
    const { startRobPosePolling, startStatusPolling } = await import('./robot.js');
    startRobPosePolling();
    startStatusPolling();
  } catch(e) {
    showToast('Failed to load map: ' + e.message, 'error');
    emptyState.style.display = 'flex';
  } finally {
    showSpinner(false);
    _updateDeleteBtn();
  }
}
