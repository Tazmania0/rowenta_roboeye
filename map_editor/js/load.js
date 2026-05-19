// ─────────────────────────────────────────────────────────────────────────────
// MAP LOADING
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { api, pollCmd } from './api.js';
import { extractAreas } from './normalize.js';
import { renderMap, renderAreaList, renderMapChips } from './render.js';
import { showToast, showSpinner, setStatus, showModal, hideInstruction } from './modal.js';
import { fitToScreen } from './mode.js';

const emptyState = document.getElementById('empty-state');

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
  if (state.maps.length > 0) loadMap(state.maps[0].map_id);
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

export async function loadMap(mapId) {
  state.activeMapId = mapId;
  state.selectedAreaId = null;
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
        minX: Math.min(...xs), maxX: Math.max(...xs),
        minY: Math.min(...ys), maxY: Math.max(...ys),
      };
    } else if (state.areas.length > 0) {
      // Fallback: compute from area points
      const allPts = state.areas.flatMap(a => a.points || []);
      state.bbox = {
        minX: Math.min(...allPts.map(p=>p.x)),
        maxX: Math.max(...allPts.map(p=>p.x)),
        minY: Math.min(...allPts.map(p=>p.y)),
        maxY: Math.max(...allPts.map(p=>p.y)),
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
