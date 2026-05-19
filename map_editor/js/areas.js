// ─────────────────────────────────────────────────────────────────────────────
// AREA SELECTION, DETAIL PANEL, SAVE, TOGGLE BLOCK
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { apiText } from './api.js';
import { showToast, showSpinner, showInstruction } from './modal.js';
import { highlightArea, highlightAreaSplit, renderAreaList, renderMap, getAreaName } from './render.js';

const areaDetailEl = document.getElementById('area-detail');
const mergeHintEl  = document.getElementById('merge-hint');
const areaListEl   = document.getElementById('area-list');

// handleMergeClick imported lazily to avoid circular dep — injected from main.js
let _handleMergeClickCb = null;
export function setHandleMergeClick(fn) { _handleMergeClickCb = fn; }

export function onAreaClick(areaId) {
  if (state.mode === 'merge') {
    if (_handleMergeClickCb) _handleMergeClickCb(areaId);
    return;
  }

  if (state.mode === 'split') {
    if (state.selectedAreaId === null) {
      // Step 1: pick the area to split
      state.selectedAreaId = areaId;
      highlightAreaSplit(areaId);
      updateSplitListUI(areaId);
      showInstruction('STEP 2 of 3', 'Click first point of the split line', 'green');
    }
    // If area already selected, clicks place points (handled by SVG click handler)
    return;
  }

  // Normal select mode
  state.selectedAreaId = areaId;
  highlightArea(areaId);
  updateSplitListUI(areaId);

  const area = state.areas.find(a => a.area_id === areaId);
  if (!area) return;

  if (area.area_state === 'inactive' && state.explorePhase === 'drawing') {
    showToast('Use Split to divide this segment, or Merge to combine with adjacent segment', 'info');
    highlightAreaSplit(areaId);
    state.selectedAreaId = areaId;
    return;
  }

  areaDetailEl.classList.add('visible');
  mergeHintEl.classList.remove('visible');

  document.getElementById('field-name').value      = getAreaName(area);
  document.getElementById('field-room-type').value = area.room_type || 'none';
  document.getElementById('field-state').value     = area.area_state || 'clean';
  document.getElementById('field-fan').value       = String(area.cleaning_parameter_set || 0);
  document.getElementById('field-strategy').value  = area.strategy_mode || 'normal';
}

export function updateSplitListUI(areaId) {
  areaListEl.querySelectorAll('.area-item').forEach(el => {
    el.classList.toggle('selected', parseInt(el.dataset.areaId) === areaId);
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
    floor_type:            area.floor_type || 'default',
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

    // Update local state
    Object.assign(area, {
      area_meta_data: metaData,
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'area_state') ? { area_state: areaState } : {}),
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'room_type') ? { room_type: roomType } : {}),
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'cleaning_parameter_set') ? { cleaning_parameter_set: fanSpeed } : {}),
      ...(Object.prototype.hasOwnProperty.call(savedPayload, 'strategy_mode') ? { strategy_mode: strategy } : {}),
    });
    renderAreaList();
    renderMap();
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
