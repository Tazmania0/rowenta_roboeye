// MERGE AREAS
import { state } from './state.js';
import { ROOM_TYPE_OPTIONS } from './config.js';
import { showModal, showToast, showSpinner, hideInstruction, showInstruction } from './modal.js';
import { highlightMergeFirst, highlightArea, _panToArea, renderAreaList, getAreaName } from './render.js';
import { saveArea } from './areas.js';
import { pollCmd, api } from './api.js';
import { setMode } from './mode.js';

const mergeHintEl = document.getElementById('merge-hint');
const areaDetailEl = document.getElementById('area-detail');

function _showAreaFocus(area) {
  if (!area) return;
  state.selectedAreaId = area.area_id;
  _panToArea(area);
  highlightArea(area.area_id);
  renderAreaList();
  areaDetailEl.classList.add('visible');
  document.getElementById('field-name').value = getAreaName(area);
  document.getElementById('field-room-type').value = area.room_type || 'none';
  document.getElementById('field-state').value = area.area_state || 'clean';
  document.getElementById('field-fan').value = String(area.cleaning_parameter_set || 0);
  document.getElementById('field-strategy').value = area.strategy_mode || 'normal';
  const floorTypeEl = document.getElementById('field-floor-type');
  if (floorTypeEl) floorTypeEl.value = area.floor_type || 'none';
}

export function mergeAreasPath(mapId, areaId1, areaId2) {
  const params = new URLSearchParams();
  params.set('map_id', mapId);
  params.set('area_id1', areaId1);
  params.set('area_id2', areaId2);
  return `/set/merge_areas?${params.toString()}`;
}

export function startMerge() {
  if (state.selectedAreaId === null) {
    showToast('Select a room first', 'error');
    return;
  }

  state.mergeFirstId = state.selectedAreaId;
  setMode('merge');
  _showAreaFocus(state.areas.find(a => a.area_id === state.mergeFirstId));
  highlightMergeFirst(state.mergeFirstId);
  mergeHintEl.classList.add('visible');
  document.getElementById('merge-first-id').textContent = state.mergeFirstId;
  showInstruction('STEP 2 of 2', `Now click the second room to merge with #${state.mergeFirstId}`, 'amber');
}

export function findMergedAreaAfterMerge(id1, id2, preIds) {
  const oldIds = new Set([...preIds].map(id => String(id)));
  const newArea = state.areas.find(a =>
    a.area_id !== null && !oldIds.has(String(a.area_id)) && a.area_state !== 'blocking'
  );
  if (newArea) return newArea;

  const survivingOriginals = state.areas.filter(a =>
    (String(a.area_id) === String(id1) || String(a.area_id) === String(id2)) &&
    a.area_state !== 'blocking'
  );
  return survivingOriginals.length === 1 ? survivingOriginals[0] : null;
}

export async function handleMergeClick(areaId) {
  if (areaId === state.mergeFirstId) {
    showToast('Select a different room', 'error');
    return;
  }

  const id1 = state.mergeFirstId;
  const id2 = areaId;
  const area1 = state.areas.find(a => a.area_id === id1);
  const area2 = state.areas.find(a => a.area_id === id2);
  const name1 = getAreaName(area1 || {});
  const name2 = getAreaName(area2 || {});
  _showAreaFocus(area2);
  highlightMergeFirst(id1);

  let vals;
  try {
    vals = await showModal({
      title: 'Merge rooms?',
      desc: `"${name1}" and "${name2}" will be combined. This cannot be undone without re-splitting.`,
      fields: [
        { key: 'name', label: 'Name for merged room', value: name1, placeholder: 'e.g. Living room' },
        {
          key: 'room_type',
          label: 'Room type',
          type: 'select',
          options: ROOM_TYPE_OPTIONS,
          value: area1?.room_type || 'none',
        },
      ],
      confirmLabel: 'Merge Rooms',
    });
  } catch {
    state.mergeFirstId = null;
    setMode('select');
    mergeHintEl.classList.remove('visible');
    hideInstruction();
    highlightArea(state.selectedAreaId);
    return;
  }

  state.mergeFirstId = null;
  setMode('select');
  mergeHintEl.classList.remove('visible');
  hideInstruction();
  showSpinner(true);

  const preIds = new Set(state.areas.map(a => a.area_id));
  const { loadMap } = await import('./load.js');

  try {
    const res = await api(mergeAreasPath(state.activeMapId, id1, id2));
    const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
    if (cmdId) {
      showToast('Merging...', 'info');
      await pollCmd(cmdId, 30000).catch(async e => {
        console.warn('[merge] command accepted, but polling did not confirm completion:', e);
        showToast('Merge accepted; refreshing map...', 'info');
        await new Promise(r => setTimeout(r, 3000));
      });
    } else {
      await new Promise(r => setTimeout(r, 3000));
    }

    await new Promise(r => setTimeout(r, 1500));
    await loadMap(state.activeMapId);

    if (vals.name) {
      const merged = findMergedAreaAfterMerge(id1, id2, preIds);
      if (merged) {
        merged.area_meta_data = JSON.stringify({ name: vals.name });
        merged.area_state = 'clean';
        if (vals.room_type) merged.room_type = vals.room_type;
        _showAreaFocus(merged);
        document.getElementById('field-name').value = vals.name;
        document.getElementById('field-room-type').value = vals.room_type || 'none';
        document.getElementById('field-state').value = 'clean';
        await saveArea();
      } else {
        showToast('Merged - could not find new area to name', 'info');
      }
    } else {
      showToast('Merged successfully', 'success');
    }
  } catch (e) {
    showToast('Merge failed: ' + e.message, 'error');
    await loadMap(state.activeMapId);
  } finally {
    showSpinner(false);
  }
}
