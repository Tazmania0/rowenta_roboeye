// ─────────────────────────────────────────────────────────────────────────────
// MERGE AREAS
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { USE_PROXY, ROBOT_PORT, ROOM_TYPE_OPTIONS } from './config.js';
import * as config from './config.js';
import { showModal, showToast, showSpinner, hideInstruction, showInstruction } from './modal.js';
import { highlightMergeFirst, highlightArea, renderAreaList, getAreaName, renderMap } from './render.js';
import { saveArea } from './areas.js';
import { pollCmd, api } from './api.js';
import { setMode } from './mode.js';

const mergeHintEl  = document.getElementById('merge-hint');
const areaDetailEl = document.getElementById('area-detail');

export function startMerge() {
  if (state.selectedAreaId === null) { showToast('Select a room first', 'error'); return; }
  state.mergeFirstId = state.selectedAreaId;
  setMode('merge');
  highlightMergeFirst(state.mergeFirstId);
  mergeHintEl.classList.add('visible');
  document.getElementById('merge-first-id').textContent = state.mergeFirstId;
  showInstruction('STEP 2 of 2', `Now click the second room to merge with #${state.mergeFirstId}`, 'amber');
}


export function findMergedAreaAfterMerge(id1, id2, preIds) {
  const oldIds = new Set([...preIds].map(id => String(id)));
  const newArea = state.areas.find(a =>
    a.area_id !== null &&
    !oldIds.has(String(a.area_id)) &&
    a.area_state !== 'blocking'
  );
  if (newArea) return newArea;

  // Some firmware keeps one of the original area IDs for the merged room.
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

  // ── Confirmation modal ──────────────────────────────────────────────────
  let vals;
  try {
    vals = await showModal({
      title: 'Merge rooms?',
      desc:  `"${name1}" and "${name2}" will be combined. This cannot be undone without re-splitting.`,
      fields: [
        { key: 'name',      label: 'Name for merged room', value: name1,
          placeholder: 'e.g. Living room' },
        { key: 'room_type', label: 'Room type',
          type: 'select',
          options: ROOM_TYPE_OPTIONS,
          value: area1?.room_type || 'none',
        },
      ],
      confirmLabel: 'Merge Rooms',
    });
  } catch {
    // Cancelled — restore select mode without losing current selection
    state.mergeFirstId = null;
    setMode('select');
    mergeHintEl.classList.remove('visible');
    hideInstruction();
    highlightArea(state.selectedAreaId);
    return;
  }
  // ───────────────────────────────────────────────────────────────────────

  state.mergeFirstId = null;
  setMode('select');
  mergeHintEl.classList.remove('visible');
  hideInstruction();
  showSpinner(true);

  // Snapshot area IDs before merge so we can find the new merged area after
  const preIds = new Set(state.areas.map(a => a.area_id));

  // Import loadMap lazily to avoid circular dep
  const { loadMap } = await import('./load.js');

  try {
    let res, mergeOk = false, workingFmt = '';
    for (const [p1, p2] of [
      ['area_id_1','area_id_2'],
      ['areaId1','areaId2'],
      ['area_id1','area_id2'],
      ['id1','id2'],
    ]) {
      const url = `/set/merge_areas?map_id=${state.activeMapId}&${p1}=${id1}&${p2}=${id2}`;
      const resp = await fetch(USE_PROXY ? url : `http://${config.robotIP}:${ROBOT_PORT}${url}`);
      console.log(`[merge] ${p1}/${p2} → HTTP ${resp.status}`);
      if (resp.ok) {
        res = await resp.json();
        mergeOk = true;
        workingFmt = `${p1}/${p2}`;
        break;
      }
      if (resp.status !== 400) break;
    }
    if (!mergeOk) throw new Error('All merge param formats returned 400 — check console');

    const cmdId = res.cmd_id ?? res.cmdId ?? res.command_id;
    if (cmdId) {
      showToast(`Merging…`, 'info');
      await pollCmd(cmdId, 30000).catch(async e => {
        // The robot accepted the merge; keep going so the merged area can be named.
        console.warn('[merge] command accepted, but polling did not confirm completion:', e);
        showToast('Merge accepted; refreshing map...', 'info');
        await new Promise(r => setTimeout(r, 3000));
      });
    } else {
      await new Promise(r => setTimeout(r, 3000));
    }
    await new Promise(r => setTimeout(r, 1500));
    await loadMap(state.activeMapId);

    // Apply user-entered name to the merged result
    if (vals.name) {
      const merged = findMergedAreaAfterMerge(id1, id2, preIds);
      if (merged) {
        merged.area_meta_data = JSON.stringify({ name: vals.name });
        merged.area_state     = 'clean';
        if (vals.room_type) merged.room_type = vals.room_type;
        state.selectedAreaId  = merged.area_id;
        document.getElementById('field-name').value       = vals.name;
        document.getElementById('field-room-type').value  = vals.room_type || 'none';
        document.getElementById('field-state').value      = 'clean';
        areaDetailEl.classList.add('visible');
        await saveArea();
      } else {
        showToast('Merged — could not find new area to name', 'info');
      }
    } else {
      showToast('Merged successfully', 'success');
    }
  } catch(e) {
    showToast('Merge failed: ' + e.message, 'error');
    const { loadMap: lm } = await import('./load.js');
    await lm(state.activeMapId);
  } finally {
    showSpinner(false);
  }
}
