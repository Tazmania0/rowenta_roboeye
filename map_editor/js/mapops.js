// ─────────────────────────────────────────────────────────────────────────────
// MAP OPERATIONS — save, rename, go home, reset stats
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { api, pollCmd } from './api.js';
import { showModal, showToast, showSpinner } from './modal.js';
import { reloadMapChips, loadMap } from './load.js';

export function _updateSaveButton() {
  const btn = document.getElementById('btn-save-map-edits');
  if (!btn) return;
  btn.disabled      = !state.mapHasUnsavedEdits || !state.activeMapId;
  btn.style.opacity = state.mapHasUnsavedEdits ? '1' : '0.5';
}

export function _updateMapOpsButtons() {
  const hasMap    = !!state.activeMapId;
  const permCount = state.maps.filter(
    m => String(m.permanent_flag).toLowerCase() === 'true').length;
  const btnRename = document.getElementById('btn-rename-map');
  const btnDelete = document.getElementById('btn-delete-map');
  const btnGoHome = document.getElementById('btn-go-home');
  if (btnRename) btnRename.disabled = !hasMap;
  if (btnDelete) btnDelete.disabled = !hasMap || permCount <= 1;
  if (btnGoHome) btnGoHome.disabled = !hasMap || state.robotMode === 'go_home';
  _updateSaveButton();
}

export async function executeSaveExistingMap() {
  const mapId = state.activeMapId; if (!mapId) return;
  const map  = state.maps.find(m => String(m.map_id) === String(mapId));
  const name = map ? ((map.map_meta_data || '').trim() || `Map ${mapId}`) : `Map ${mapId}`;
  try {
    await showModal({ title: `Save changes to "${name}"?`,
      desc: 'All splits, merges, area names, and no-go zones will be permanently saved.',
      confirmLabel: 'Save Changes' });
  } catch { return; }
  showSpinner(true);
  try {
    const res   = await api(`/set/save_map?map_id=${mapId}`);
    const cmdId = res.cmd_id ?? res.cmdId;
    if (cmdId) { showToast('Saving…', 'info'); await pollCmd(cmdId, 60000); }
    else await new Promise(r => setTimeout(r, 2000));
    state.mapHasUnsavedEdits = false; _updateSaveButton();
    showToast(`"${name}" saved`, 'success');
  } catch (e) { showToast('Save failed: ' + e.message.substring(0, 80), 'error'); }
  finally { showSpinner(false); }
}

export async function executeRenameMap() {
  const mapId = state.activeMapId; if (!mapId) return;
  const map = state.maps.find(m => String(m.map_id) === String(mapId));
  const currentName = map ? ((map.map_meta_data || '').trim() || `Map ${mapId}`) : '';
  let vals;
  try {
    vals = await showModal({ title: 'Rename map', desc: 'Enter a new name.',
      confirmLabel: 'Rename',
      fields: [{ key: 'name', label: 'Map name', value: currentName, placeholder: 'e.g. Ground Floor' }] });
  } catch { return; }
  const newName = vals.name.trim(); if (!newName || newName === currentName) return;
  if (!state.dockingPose) {
    showSpinner(true);
    try {
      const fr = await api(`/get/feature_map?map_id=${mapId}`);
      state.dockingPose = (fr.map ?? fr).docking_pose ?? null;
    } catch {}
  }
  if (!state.dockingPose) { showToast('Cannot rename: failed to read dock position', 'error'); showSpinner(false); return; }
  showSpinner(true);
  try {
    await api(`/set/modify_map?map_id=${mapId}&name=${encodeURIComponent(newName)}`
            + `&docking_pose=${encodeURIComponent(JSON.stringify(state.dockingPose))}`);
    if (map) map.map_meta_data = newName;
    reloadMapChips();
    showToast(`Renamed to "${newName}"`, 'success');
  } catch (e) { showToast('Rename failed: ' + e.message.substring(0, 80), 'error'); }
  finally { showSpinner(false); }
}

export async function executeGoHome() {
  showSpinner(true);
  try { await api('/set/go_home'); showToast('Robot returning to dock', 'success'); }
  catch (e) { showToast('Go home failed: ' + e.message, 'error'); }
  finally { showSpinner(false); }
}

export async function executeResetStats() {
  try {
    await showModal({ title: 'Reset all statistics?',
      desc: 'Clears all cleaning counters, times, and last-cleaned dates. Cannot be undone.',
      confirmLabel: 'Reset Statistics', danger: true });
  } catch { return; }
  showSpinner(true);
  try {
    await api('/set/do_statistics_reset');
    await new Promise(r => setTimeout(r, 1000));
    await loadMap(state.activeMapId);
    showToast('Statistics reset', 'success');
  } catch (e) { showToast('Reset failed: ' + e.message, 'error'); }
  finally { showSpinner(false); }
}
