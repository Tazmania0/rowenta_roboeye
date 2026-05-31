import { state } from './state.js';

function areaEstimateMs(area) {
  const stats = area?._raw?.statistics || area?.statistics || {};
  const value = Number(
    stats.average_cleaning_time
    || stats.estimated_cleaning_time
    || 0
  );
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function isCleanableRoom(area) {
  return area?.area_state === 'clean' && area?.area_type !== 'to_be_cleaned';
}

function selectedCleanableAreas() {
  const ids = state.selectedAreaIds instanceof Set ? state.selectedAreaIds : new Set();
  if (!ids.size) return [];
  return state.areas.filter(area => ids.has(area.area_id) && isCleanableRoom(area));
}

function currentSessionAreas() {
  const sourceIds = Array.isArray(state.robotAreaIds) && state.robotAreaIds.length
    ? state.robotAreaIds
    : state.editorCleanAreaIds;
  const ids = Array.isArray(sourceIds) ? new Set(sourceIds) : new Set();
  if (!ids.size) return [];
  return state.areas.filter(area => ids.has(Number(area.area_id)) && isCleanableRoom(area));
}

function formatDuration(totalSeconds) {
  const minutes = Math.max(1, Math.round(totalSeconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}

function currentElapsedSeconds() {
  if (state.robotCleaningTimeSec !== null) return state.robotCleaningTimeSec;
  if (state.editorCleanStartedAt) {
    return Math.max(0, Math.floor((Date.now() - state.editorCleanStartedAt) / 1000));
  }
  return null;
}

export function updateEtaChip() {
  const chip = document.getElementById('eta-chip');
  const text = document.getElementById('eta-text');
  if (!chip || !text) return;

  if (!state.connected || !state.activeMapId || !state.areas.length) {
    chip.style.display = 'none';
    return;
  }

  const isCleaning = state.robotMode === 'cleaning';
  const session = isCleaning ? currentSessionAreas() : [];
  const selected = selectedCleanableAreas();
  const areas = isCleaning
    ? (session.length ? session : state.areas.filter(isCleanableRoom))
    : (selected.length ? selected : state.areas.filter(isCleanableRoom));
  let totalMs = areas.reduce((sum, area) => sum + areaEstimateMs(area), 0);

  if (!areas.length || totalMs <= 0) {
    chip.style.display = 'none';
    return;
  }

  const elapsedSeconds = isCleaning ? currentElapsedSeconds() : null;
  if (elapsedSeconds !== null) {
    totalMs = Math.max(0, totalMs - elapsedSeconds * 1000);
  }

  const suffix = isCleaning ? 'left' : (selected.length ? 'selected' : 'map');
  text.textContent = `ETA ${formatDuration(totalMs / 1000)} ${suffix}`;
  chip.title = isCleaning
    ? `Robot average cleaning time for current ${session.length ? 'partial' : 'map'} session`
    : `Robot average cleaning time for ${areas.length} room${areas.length === 1 ? '' : 's'}`;
  chip.style.display = 'inline-flex';
}
