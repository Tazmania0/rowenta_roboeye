// ─────────────────────────────────────────────────────────────────────────────
// EXPLORE, SAVE MAP, DELETE MAP, PHASE BAR MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { api, pollCmd } from './api.js';
import { showModal, showToast, showSpinner, setStatus, hideInstruction, showInstruction } from './modal.js';
import { renderMap, setAreaPolyStyle } from './render.js';
import { setMode } from './mode.js';
import { saveArea } from './areas.js';
import { loadMaps, loadMap, reloadMapChips } from './load.js';

const mapSvg = document.getElementById('map-svg');

export function _showSaveMapButton() {
  let btn = document.getElementById('btn-save-map-float');
  if (!btn) {
    btn = document.createElement('button');
    btn.id        = 'btn-save-map-float';
    btn.className = 'btn success';
    btn.style.cssText = 'position:absolute;bottom:44px;left:50%;transform:translateX(-50%);' +
                        'z-index:30;padding:8px 24px;font-size:13px;box-shadow:0 4px 20px rgba(0,0,0,0.4)';
    btn.innerHTML = '💾  Save Map';
    btn.addEventListener('click', _promptSaveMap);
    document.getElementById('canvas-wrap').appendChild(btn);
  }
  btn.style.display = '';
}

export function _hideSaveMapButton() {
  const btn = document.getElementById('btn-save-map-float');
  if (btn) btn.style.display = 'none';
}

export async function _promptSaveMap() {
  let vals;
  try {
    vals = await showModal({
      title:        'Save map permanently?',
      desc:         'Give this floor a name, then save it. After saving the map ' +
                    'is permanent and will survive robot power cycles.',
      confirmLabel: 'Save Map',
      fields: [
        { key: 'mapName', label: 'Map name', placeholder: 'e.g. Ground Floor' },
      ],
    });
  } catch { return; }

  await executeSaveMap(vals.mapName || '');
}

export async function executeSaveMap(mapName) {
  const mapId = state.exploreMapId ?? state.activeMapId;
  if (!mapId) { showToast('No map to save', 'error'); return; }

  showSpinner(true);
  try {
    // Step 1: Read docking_pose — CRITICAL, omitting resets dock to origin
    if (!state.dockingPose) {
      const featRes   = await api(`/get/feature_map?map_id=${mapId}`);
      const mapData   = featRes.map ?? featRes;
      state.dockingPose = mapData.docking_pose ?? null;
    }

    // Step 2: Rename map (with docking_pose)
    if (mapName) {
      const dockJson = encodeURIComponent(JSON.stringify(
        state.dockingPose ?? { x: 0, y: 0, heading: 0, valid: false }
      ));
      const nameEnc = encodeURIComponent(mapName);
      await api(`/set/modify_map?map_id=${mapId}&name=${nameEnc}&docking_pose=${dockJson}`);
    }

    // Step 3: Save as permanent
    showToast('Saving map…', 'info');
    const saveRes = await api(`/set/save_map?map_id=${mapId}`);
    const cmdId   = saveRes.cmd_id ?? saveRes.cmdId;
    if (cmdId) {
      await pollCmd(cmdId, 60000);
    } else {
      await new Promise(r => setTimeout(r, 3000));
    }
    await new Promise(r => setTimeout(r, 1500));

    // Step 4: Reload
    state.exploreMapId = null;
    state.explorePhase = null;
    state.dockingPose  = null;
    hideInstruction();
    _showPhaseBar(null);
    _hideSaveMapButton();
    await loadMaps();
    const savedMap = state.maps.find(m =>
      (m.map_meta_data || '').trim() === mapName.trim()
    ) ?? state.maps[state.maps.length - 1];
    if (savedMap) await loadMap(savedMap.map_id);
    showToast(`Map "${mapName || 'unnamed'}" saved permanently`, 'success');

  } catch (e) {
    console.error('[saveMap]', e);
    showToast('Save failed: ' + e.message.substring(0, 100), 'error');
  } finally {
    showSpinner(false);
  }
}

export function _enterDrawingPhase() {
  const inactive = state.areas.filter(a => a.area_state === 'inactive');
  const count    = inactive.length;

  showInstruction('PHASE 1 / 3 — DRAW ROOMS',
    `${count} auto-segments detected. Split large areas, merge small fragments.`,
    'green');

  _highlightAllInactive();
  _showPhaseBar('drawing');
  setMode('split');

  showToast(`${count} segments detected — draw room boundaries, then click "Done Drawing"`,
    'info');
}

export function _highlightAllInactive() {
  state.areas.forEach(area => {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${area.area_id}"]`);
    if (!poly) return;
    if (area.area_state === 'inactive') {
      poly.setAttribute('fill',            'rgba(245,158,11,0.12)');
      poly.setAttribute('stroke',          '#f59e0b');
      poly.setAttribute('stroke-width',    '6');
      poly.setAttribute('stroke-dasharray','14 6');
      poly.style.pointerEvents = 'auto';
    }
  });
}

export function _showPhaseBar(kind) {
  let bar = document.getElementById('phase-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'phase-bar';
    bar.style.cssText = [
      'display:flex', 'align-items:center', 'gap:10px',
      'padding:7px 14px',
      'background:var(--surface)',
      'border-bottom:1px solid var(--border)',
      'font-size:12px', 'flex-shrink:0',
      'z-index:5',
    ].join(';');
    const main = document.getElementById('main');
    if (main) main.parentNode.insertBefore(bar, main);
  }

  if (!kind) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';

  if (kind === 'drawing') {
    const named    = state.areas.filter(a => a.area_state === 'clean').length;
    const inactive = state.areas.filter(a => a.area_state === 'inactive').length;

    bar.innerHTML = `
      <span style="color:var(--accent2);font-weight:600;font-family:'JetBrains Mono',monospace;
                   font-size:10px;text-transform:uppercase;letter-spacing:.06em">
        ✦ Draw Rooms
      </span>
      <span style="color:var(--muted);font-size:11px">
        ${inactive} unnamed segment${inactive !== 1 ? 's' : ''}
        ${named ? ` · ${named} named` : ''}
        — Split to divide, Merge to combine
      </span>
      <div style="flex:1"></div>
      <button class="btn" id="phase-bar-undo" title="Undo last split/merge"
              style="font-size:11px;padding:3px 10px">
        ↩ Undo Last
      </button>
      <button class="btn primary" id="phase-bar-done" style="font-size:11px;padding:4px 14px">
        Done Drawing →
      </button>
    `;

    document.getElementById('phase-bar-done').addEventListener('click', _enterNamingPhase);
    document.getElementById('phase-bar-undo').addEventListener('click', _undoLastOperation);

  } else if (kind === 'naming') {
    const total  = state.areas.filter(a =>
      a.area_state !== 'blocking' && a.area_type !== 'to_be_cleaned').length;
    const named  = state.areas.filter(a => a.area_state === 'clean').length;

    bar.innerHTML = `
      <span style="color:var(--accent);font-weight:600;font-family:'JetBrains Mono',monospace;
                   font-size:10px;text-transform:uppercase;letter-spacing:.06em">
        ✦ Name Rooms
      </span>
      <span style="color:var(--muted);font-size:11px">
        ${named} / ${total} rooms named
      </span>
      <div style="flex:1"></div>
      <button class="btn" id="phase-bar-back" style="font-size:11px;padding:3px 10px">
        ← Back to Drawing
      </button>
      <button class="btn primary" id="phase-bar-save"
              style="font-size:11px;padding:4px 14px"
              ${named === 0 ? 'disabled title="Name at least one room first"' : ''}>
        Save Map →
      </button>
    `;

    document.getElementById('phase-bar-back').addEventListener('click', () => {
      state.explorePhase = 'drawing';
      _enterDrawingPhase();
    });
    document.getElementById('phase-bar-save').addEventListener('click', _promptSaveMap);
  }
}

export async function _undoLastOperation() {
  try {
    await showModal({
      title:        'Reload areas from robot?',
      desc:         'This reloads the current area state from the robot. ' +
                    'It acts as an undo only if no further changes were made. ' +
                    'Your split/merge history on the robot is permanent.',
      confirmLabel: 'Reload',
    });
  } catch { return; }
  showSpinner(true);
  try {
    await loadMap(state.activeMapId);
    if (state.explorePhase === 'drawing') {
      _highlightAllInactive();
      _showPhaseBar('drawing');
      setMode('split');
    }
    showToast('Areas reloaded', 'success');
  } catch (e) {
    showToast('Reload failed: ' + e.message, 'error');
  } finally {
    showSpinner(false);
  }
}

export function _enterNamingPhase() {
  const inactive = state.areas.filter(a => a.area_state === 'inactive');

  if (inactive.length === 0) {
    _promptSaveMap();
    return;
  }

  state.explorePhase = 'naming';
  setMode('select');

  state.areas.forEach(area => {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${area.area_id}"]`);
    if (poly) setAreaPolyStyle(poly, area);
  });

  state.areas.forEach(area => {
    if (area.area_state === 'inactive') {
      const poly = mapSvg.querySelector(`polygon[data-area-id="${area.area_id}"]`);
      if (poly) poly.style.pointerEvents = 'auto';
    }
  });

  showInstruction('PHASE 2 / 3 — NAME ROOMS',
    `Click each room and give it a name`, 'blue');

  _showPhaseBar('naming');

  _startNamingWizard(inactive);
}

export async function _startNamingWizard(inactiveAreas) {
  const { ROOM_TYPE_OPTIONS } = await import('./config.js');
  const areaDetailEl = document.getElementById('area-detail');

  try {
    await showModal({
      title:        `Name ${inactiveAreas.length} rooms`,
      desc:         'Click through each room to name it. You can also dismiss ' +
                    'this and click rooms on the map manually.',
      confirmLabel: 'Start Naming',
      fields: [],
    });
  } catch {
    showToast('Click a room on the map to name it', 'info');
    return;
  }

  const { _panToArea } = await import('./render.js');
  const { highlightArea } = await import('./render.js');

  for (let i = 0; i < inactiveAreas.length; i++) {
    const area = inactiveAreas[i];

    _panToArea(area);
    highlightArea(area.area_id);
    state.selectedAreaId = area.area_id;
    _showPhaseBar('naming');

    let vals;
    try {
      vals = await showModal({
        title:        `Name room ${i + 1} of ${inactiveAreas.length}`,
        desc:         `Area #${area.area_id}. Leave name blank to skip.`,
        confirmLabel: i < inactiveAreas.length - 1 ? 'Save & Next' : 'Save & Finish',
        fields: [
          { key: 'name',
            label:       'Room name',
            placeholder: 'e.g. Kitchen  (blank to skip)' },
          { key: 'room_type',
            label:   'Room type',
            type:    'select',
            value:   'none',
            options: ROOM_TYPE_OPTIONS },
        ],
      });
    } catch {
      break;
    }

    if (vals.name.trim()) {
      area.area_meta_data = JSON.stringify({ name: vals.name.trim() });
      area.area_state     = 'clean';
      area.room_type      = vals.room_type || 'none';
      document.getElementById('field-name').value      = vals.name.trim();
      document.getElementById('field-room-type').value = vals.room_type || 'none';
      document.getElementById('field-state').value     = 'clean';
      areaDetailEl.classList.add('visible');
      await saveArea();
      _showPhaseBar('naming');
    }
  }

  highlightArea(null);
  state.selectedAreaId = null;

  const named = state.areas.filter(a => a.area_state === 'clean').length;
  if (named > 0) {
    _showPhaseBar('naming');
  }
}

export async function executeExplore() {
  try {
    await showModal({
      title: 'Start floor exploration?',
      desc:  'The robot will map the floor from scratch. This creates a new ' +
             'temporary map — your existing maps are not affected until you ' +
             'choose to save. Make sure the robot is at its dock and all ' +
             'doors are open. Exploration takes 5–20 minutes.',
      confirmLabel: 'Start Exploring',
    });
  } catch { return; }

  showSpinner(true);
  setStatus('Exploring…', 'busy');
  state.explorePhase = 'running';

  const progressEl = document.getElementById('explore-progress');
  const phaseText  = document.getElementById('explore-phase-text');
  const elapsedEl  = document.getElementById('explore-elapsed');
  if (progressEl) progressEl.style.display = 'block';

  try {
    // CONFIRMED: zero parameters
    const startRes = await api('/set/explore');
    const cmdId    = startRes.cmd_id ?? startRes.cmdId;
    if (!cmdId) throw new Error('/set/explore returned no cmd_id');

    showToast('Exploration started — robot is mapping the floor', 'info');

    const TIMEOUT_MS = 10 * 60 * 1000;

    await pollCmd(cmdId, TIMEOUT_MS, (elapsed) => {
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      if (elapsedEl) elapsedEl.textContent = `${m}:${String(s).padStart(2,'0')} elapsed`;
      if (phaseText) phaseText.textContent  = 'Mapping in progress…';
    });

    if (phaseText) phaseText.textContent = 'Exploration done — loading map…';

    const mapsRes  = await api('/get/maps');
    const allMaps  = mapsRes.maps || [];

    let tempMap = allMaps.find(m => String(m.permanent_flag ?? '').toLowerCase() !== 'true');
    if (!tempMap) {
      const msRes = await api('/get/map_status');
      const aId   = msRes.active_map_id;
      tempMap = allMaps.find(m => String(m.map_id) === String(aId));
    }
    if (!tempMap) throw new Error('Cannot find new temporary map after explore');

    const newMapId = tempMap.map_id;
    state.exploreMapId = newMapId;
    console.log('[explore] new temporary map_id:', newMapId);

    await loadMap(newMapId);
    await reloadMapChips();

    // Transition to drawing phase
    state.explorePhase = 'drawing';
    _enterDrawingPhase();

  } catch (e) {
    console.error('[explore]', e);
    showToast('Exploration failed: ' + e.message.substring(0, 100), 'error');
    state.explorePhase = null;
  } finally {
    showSpinner(false);
    setStatus(state.connected ? `Connected — ${state.maps.length} map(s)` : 'Disconnected',
              state.connected ? 'ok' : '');
    if (progressEl) progressEl.style.display = 'none';
  }
}

export async function executeDeleteMap(mapId) {
  if (!mapId) { showToast('No map selected', 'error'); return; }

  const permanentMaps = state.maps.filter(m =>
    String(m.permanent_flag).toLowerCase() === 'true'
  );
  if (permanentMaps.length <= 1) {
    showToast('Cannot delete the last map', 'error');
    return;
  }

  const target = state.maps.find(m => String(m.map_id) === String(mapId));
  const name   = target
    ? ((target.map_meta_data || '').trim() || `Map ${mapId}`)
    : `Map ${mapId}`;

  let vals;
  try {
    vals = await showModal({
      title:        `⚠ Delete "${name}"?`,
      desc:         `This permanently deletes the floor plan, all named rooms, ` +
                    `and all associated statistics. THIS CANNOT BE UNDONE.\n\n` +
                    `Type the map name to confirm deletion.`,
      confirmLabel: 'Delete Forever',
      danger:       true,
      fields: [
        { key: 'confirm', label: `Type "${name}" to confirm`, placeholder: name },
      ],
    });
  } catch { return; }

  if ((vals.confirm || '').trim() !== name.trim()) {
    showToast('Name did not match — deletion cancelled', 'error');
    return;
  }

  showSpinner(true);
  const mapGroup  = document.getElementById('map-group');
  const emptyState = document.getElementById('empty-state');
  try {
    const res   = await api(`/set/delete_map?map_id=${mapId}`);
    const cmdId = res?.cmd_id ?? res?.cmdId;
    if (cmdId) {
      await pollCmd(cmdId, 15000);
    } else {
      await new Promise(r => setTimeout(r, 1500));
    }

    const wasActive = String(state.activeMapId) === String(mapId);

    await loadMaps();

    if (wasActive && state.maps.length > 0) {
      await loadMap(state.maps[0].map_id);
    } else if (state.maps.length === 0) {
      state.activeMapId = null;
      mapGroup.innerHTML = '';
      emptyState.style.display = 'flex';
    }

    showToast(`Map "${name}" deleted`, 'success');
  } catch (e) {
    console.error('[deleteMap]', e);
    showToast('Delete failed: ' + e.message.substring(0, 100), 'error');
  } finally {
    showSpinner(false);
  }
}
