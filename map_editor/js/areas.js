// ─────────────────────────────────────────────────────────────────────────────
// AREA SELECTION, DETAIL PANEL, SAVE, TOGGLE BLOCK
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { apiText } from './api.js';
import { showToast, showSpinner, showInstruction, showModal } from './modal.js';
import { highlightArea, highlightAreaSplit, renderAreaList, renderMap, getAreaName } from './render.js';
import { loadMap } from './load.js';
import { _updateSaveButton } from './mapops.js';

const areaDetailEl = document.getElementById('area-detail');
const mergeHintEl  = document.getElementById('merge-hint');
const areaListEl   = document.getElementById('area-list');

// handleMergeClick imported lazily to avoid circular dep — injected from main.js
let _handleMergeClickCb = null;
export function setHandleMergeClick(fn) { _handleMergeClickCb = fn; }

function _selectedAreaIds() {
  if (!(state.selectedAreaIds instanceof Set)) {
    state.selectedAreaIds = new Set(state.selectedAreaId !== null ? [state.selectedAreaId] : []);
  }
  return state.selectedAreaIds;
}

function _setAreaSelection(areaId, event = null) {
  const ids = _selectedAreaIds();
  const additive = !!(event?.ctrlKey || event?.metaKey || event?.shiftKey);

  if (additive) {
    if (ids.has(areaId)) ids.delete(areaId);
    else ids.add(areaId);
    state.selectedAreaId = ids.has(areaId) ? areaId : (ids.values().next().value ?? null);
    return;
  }

  ids.clear();
  ids.add(areaId);
  state.selectedAreaId = areaId;
}

function _selectedCleanAreas() {
  const ids = _selectedAreaIds();
  return state.areas.filter(area => ids.has(area.area_id) && area.area_state === 'clean');
}

export function updateCleanSelectionButton() {
  const btnClean = document.getElementById('btn-clean-area');
  if (!btnClean) return;
  const count = _selectedCleanAreas().length;
  const busy = state.robotMode === 'cleaning' || state.robotMode === 'go_home';
  btnClean.disabled = count === 0 || busy;
  btnClean.textContent = count > 1 ? `▶ Clean Selected (${count})` : '▶ Clean Selected';
}

function _showAreaDetail(area) {
  areaDetailEl.classList.add('visible');
  mergeHintEl.classList.remove('visible');

  document.getElementById('field-name').value      = getAreaName(area);
  document.getElementById('field-room-type').value = area.room_type || 'none';
  document.getElementById('field-state').value     = area.area_state || 'clean';
  document.getElementById('field-fan').value       = String(area.cleaning_parameter_set || 0);
  document.getElementById('field-strategy').value  = area.strategy_mode || 'normal';
  const floorTypeEl = document.getElementById('field-floor-type');
  if (floorTypeEl) floorTypeEl.value = area.floor_type || 'none';
  _renderAreaStats(area);
}

export function clearAreaSelection() {
  state.selectedAreaId = null;
  state.selectedAreaIds = new Set();
  highlightArea(null);
  areaDetailEl.classList.remove('visible');
  updateSplitListUI(null);
  updateCleanSelectionButton();
}

export function onAreaClick(areaId, event = null) {
  if (state.mode === 'merge') {
    if (_handleMergeClickCb) _handleMergeClickCb(areaId);
    return;
  }

  if (state.mode === 'split') {
    if (state.selectedAreaId === null) {
      // Step 1: pick the area to split
      state.selectedAreaId = areaId;
      state.selectedAreaIds = new Set([areaId]);
      highlightAreaSplit(areaId);
      updateSplitListUI(areaId);
      updateCleanSelectionButton();
      showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    }
    // If area already selected, clicks place points (handled by SVG click handler)
    return;
  }

  // Normal select mode
  _setAreaSelection(areaId, event);
  highlightArea(state.selectedAreaId);
  updateSplitListUI(state.selectedAreaId);
  updateCleanSelectionButton();

  const area = state.areas.find(a => a.area_id === state.selectedAreaId);
  if (!area) {
    areaDetailEl.classList.remove('visible');
    return;
  }

  if (area.area_state === 'inactive' && state.explorePhase === 'drawing') {
    showToast('Use Split to divide this segment, or Merge to combine with adjacent segment', 'info');
    highlightAreaSplit(area.area_id);
    state.selectedAreaId = area.area_id;
    state.selectedAreaIds = new Set([area.area_id]);
    updateCleanSelectionButton();
    return;
  }

  _showAreaDetail(area);
}

export function updateSplitListUI(areaId) {
  const ids = _selectedAreaIds();
  areaListEl.querySelectorAll('.area-item').forEach(el => {
    const id = parseInt(el.dataset.areaId, 10);
    el.classList.toggle('selected', ids.has(id) || id === areaId);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// SAVE AREA
// ─────────────────────────────────────────────────────────────────────────────
export async function saveArea() {
  const area = state.areas.find(a => a.area_id === state.selectedAreaId);
  if (!area) return;

  const name      = document.getElementById('field-name').value.trim();
  const roomType  = document.getElementById('field-room-type').value;
  const areaState = document.getElementById('field-state').value;
  const fanSpeed  = parseInt(document.getElementById('field-fan').value);
  const strategy  = document.getElementById('field-strategy').value;
  const floorType = document.getElementById('field-floor-type')?.value || 'none';

  const metaData = name ? JSON.stringify({ name }) : '';
  const currentMetaData = area.area_meta_data || '';
  const metadataChanged = metaData !== currentMetaData;
  const modifyAreaUrl = params =>
    `/set/modify_area?${new URLSearchParams({ map_id: state.activeMapId, ...params }).toString()}`;

  // Build payload from the raw original + only patch the changed fields.
  // This guarantees the robot gets exactly the same structure it sent us,
  // with our modifications applied on top.
  const payload = Object.assign({}, area._raw || {}, {
    id:                    area.area_id,      // confirmed field name = "id"
    area_meta_data:        metaData,
    area_state:            areaState,
    area_type:             area.area_type || 'room',
    cleaning_parameter_set: fanSpeed,
    floor_type:            floorType,
    method:                area.method    || 'normal',
    pump_volume:           area.pump_volume || 'default',
    room_type:             roomType,
    strategy_mode:         strategy,
    points:                area.points,
    // Remove statistics from payload — robot doesn't want it back
    statistics:            undefined,
  });
  // Remove undefined keys (e.g. statistics)
  Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k]);

  const modifyPayloads = [
    {
      label: 'params-metadata-settings',
      url: modifyAreaUrl({
        area_id: area.area_id,
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
        cleaning_parameter_set: fanSpeed,
        strategy_mode: strategy,
      }),
      changesMetadata: true,
      localPatch: {
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
        cleaning_parameter_set: fanSpeed,
        strategy_mode: strategy,
      },
    },
    {
      label: 'params-metadata-only',
      url: modifyAreaUrl({
        area_id: area.area_id,
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
      }),
      changesMetadata: true,
      localPatch: {
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
      },
    },
    {
      label: 'params-settings',
      url: modifyAreaUrl({
        area_id: area.area_id,
        cleaning_parameter_set: fanSpeed,
        strategy_mode: strategy,
      }),
      changesMetadata: false,
      localPatch: {
        cleaning_parameter_set: fanSpeed,
        strategy_mode: strategy,
      },
    },
    { label: 'full', payload, changesMetadata: true },
    {
      label: 'core',
      changesMetadata: true,
      payload: {
        id: area.area_id,
        points: area.points,
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
      },
    },
    {
      label: 'metadata-state',
      changesMetadata: true,
      payload: {
        id: area.area_id,
        area_meta_data: metaData,
        area_state: areaState,
        room_type: roomType,
      },
    },
    {
      label: 'metadata-only',
      changesMetadata: true,
      payload: {
        id: area.area_id,
        area_meta_data: metaData,
      },
    },
  ];

  showSpinner(true);
  try {
    let res;
    let saved = false;
    let savedPayload = null;
    let lastErr;
    for (const fmt of modifyPayloads) {
      if (metadataChanged && !fmt.changesMetadata) continue;
      const url = fmt.url || `/set/modify_area?map_id=${state.activeMapId}&area=${encodeURIComponent(JSON.stringify(fmt.payload))}`;
      try {
        res = await apiText(url);
        console.log(`[modify_area] SUCCESS with ${fmt.label}`, res);
        saved = true;
        savedPayload = fmt.localPatch || fmt.payload || {};
        break;
      } catch (e) {
        lastErr = e;
        console.warn(`[modify_area] ${fmt.label} failed:`, e);
        if (e.status !== 400) throw e;
      }
    }
    if (!saved) throw lastErr || new Error('modify_area failed');

    // If the winning format didn't include settings, send a dedicated settings-only call
    const settingsApplied =
      Object.prototype.hasOwnProperty.call(savedPayload, 'cleaning_parameter_set') &&
      Object.prototype.hasOwnProperty.call(savedPayload, 'strategy_mode');
    if (!settingsApplied) {
      const originalFanSpeed = area._raw?.cleaning_parameter_set ?? area.cleaning_parameter_set;
      const originalStrategy = area._raw?.strategy_mode ?? area.strategy_mode;
      const settingsChanged  =
        Number(originalFanSpeed) !== fanSpeed ||
        (originalStrategy || 'normal') !== strategy;
      if (settingsChanged) {
        try {
          await apiText(modifyAreaUrl({
            area_id: area.area_id,
            cleaning_parameter_set: fanSpeed,
            strategy_mode: strategy,
          }));
          console.log('[modify_area] settings-only call succeeded');
        } catch (e) {
          console.warn('[modify_area] settings-only call failed:', e);
          showToast('Settings may not have been saved — try saving again', 'warn');
        }
      }
    }

    // Update local state — always reflect what the user set
    Object.assign(area, {
      area_meta_data: metaData,
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'area_state') ? { area_state: areaState } : {}),
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'room_type')  ? { room_type: roomType }   : {}),
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'floor_type') ? { floor_type: floorType } : {}),
      // Always reflect settings (either sent in main call or the fallback above)
      cleaning_parameter_set: fanSpeed,
      strategy_mode: strategy,
    });
    renderAreaList();
    renderMap();
    state.mapHasUnsavedEdits = true;
    _updateSaveButton();
    showToast('Area saved', 'success');
  } catch(e) {
    showToast('Save failed: ' + e.message, 'error');
  } finally {
    showSpinner(false);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// BLOCK / UNBLOCK AREA
// ─────────────────────────────────────────────────────────────────────────────
export async function toggleBlock() {
  const area = state.areas.find(a => a.area_id === state.selectedAreaId);
  if (!area) return;

  const isBlocked = area.area_state === 'blocking' || area.area_state === 'proposed_blocking';
  document.getElementById('field-state').value = isBlocked ? 'clean' : 'blocking';
  await saveArea();
}

// ─────────────────────────────────────────────────────────────────────────────
// DELETE AREA
// ─────────────────────────────────────────────────────────────────────────────
export async function executeDeleteArea() {
  const areaId = state.selectedAreaId;
  if (!areaId) return;
  const area = state.areas.find(a => a.area_id === areaId);
  if (!area) return;
  const name    = getAreaName(area);
  const isNamed = area.area_state === 'clean';
  try {
    await showModal({
      title:        `Delete "${name}"?`,
      desc:         isNamed
        ? 'Removes the room, its name, and all cleaning statistics.'
        : 'Removes this area segment from the map.',
      confirmLabel: 'Delete Area', danger: true,
    });
  } catch { return; }
  showSpinner(true);
  try {
    const { api, pollCmd } = await import('./api.js');
    const res   = await api(`/set/delete_area?map_id=${state.activeMapId}&area_id=${areaId}`);
    const cmdId = res?.cmd_id ?? res?.cmdId;
    if (cmdId) await pollCmd(cmdId, 15000);
    else await new Promise(r => setTimeout(r, 1500));
    state.areas = state.areas.filter(a => a.area_id !== areaId);
    state.selectedAreaId = null;
    state.selectedAreaIds = new Set();
    state.mapHasUnsavedEdits = true;
    _updateSaveButton();
    renderMap(state._lastWalls, state._lastDock);
    renderAreaList();
    areaDetailEl.classList.remove('visible');
    showToast(`"${name}" deleted`, 'success');
  } catch (e) {
    showToast('Delete failed: ' + e.message.substring(0, 80), 'error');
    await loadMap(state.activeMapId);
  } finally { showSpinner(false); }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLEAN AREA NOW
// ─────────────────────────────────────────────────────────────────────────────
export async function executeCleanArea() {
  const areas = _selectedCleanAreas();
  if (areas.length === 0) {
    showToast('Select at least one cleanable area', 'error');
    updateCleanSelectionButton();
    return;
  }

  const label = areas.length === 1 ? `"${getAreaName(areas[0])}"` : `${areas.length} areas`;
  try {
    await showModal({ title: `Clean ${label}?`,
      desc: areas.length === 1
        ? 'Robot will start cleaning this room immediately.'
        : 'Robot will start cleaning the selected areas immediately.',
      confirmLabel: 'Start Cleaning' });
  } catch { return; }
  showSpinner(true);
  try {
    const { api } = await import('./api.js');
    const areaIds = areas.map(area => area.area_id).join(',');
    const fan = Math.max(...areas.map(area => Number(area.cleaning_parameter_set ?? 0)));
    const path = `/set/clean_map?map_id=${state.activeMapId}&area_ids=${areaIds}`
               + `&cleaning_parameter_set=${fan}&cleaning_strategy_mode=1`;
    await api(path);
    showToast(`Cleaning ${label}...`, 'success');
  } catch (e) { showToast('Clean failed: ' + e.message.substring(0, 80), 'error'); }
  finally { showSpinner(false); }
}

// ─────────────────────────────────────────────────────────────────────────────
// AREA STATISTICS
// ─────────────────────────────────────────────────────────────────────────────
export function _renderAreaStats(area) {
  const el = document.getElementById('area-stats');
  if (!el) return;
  const stats = area._raw?.statistics;
  if (!stats) { el.style.display = 'none'; return; }
  const areaM2      = (stats.area_size / 1_000_000).toFixed(1);
  const avgMins     = stats.average_cleaning_time > 0
    ? (stats.average_cleaning_time / 60000).toFixed(1) + ' min' : '—';
  const lastCleaned = stats.last_cleaned?.year === 2001 ? 'Never'
    : `${stats.last_cleaned.year}-${String(stats.last_cleaned.month).padStart(2, '0')}-`
    + String(stats.last_cleaned.day).padStart(2, '0');
  el.style.display = '';
  el.innerHTML = `<div class="panel-title">Statistics</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;font-size:11px">
      <span style="color:var(--muted)">Area</span><span>${areaM2} m2</span>
      <span style="color:var(--muted)">Cleanings</span><span>${stats.cleaning_counter}</span>
      <span style="color:var(--muted)">Avg time</span><span>${avgMins}</span>
      <span style="color:var(--muted)">Last cleaned</span><span>${lastCleaned}</span>
    </div>`;
}
