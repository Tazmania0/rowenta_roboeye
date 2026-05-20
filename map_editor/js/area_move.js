import { state } from './state.js';
import { robotToSVG, svgToRobot } from './coords.js';
import { apiText, pollCmd } from './api.js';
import { showSpinner, showToast } from './modal.js';
import { highlightArea, isBlockingArea, isSpotArea, renderAreaList, renderMap } from './render.js';
import { _updateSaveButton } from './mapops.js';

const mapSvg = document.getElementById('map-svg');

function isMovableArea(area) {
  return area && (isBlockingArea(area) || isSpotArea(area));
}

function pointsToSvgAttr(points) {
  return points.map(p => {
    const s = robotToSVG(p.x, p.y);
    return `${s.x},${s.y}`;
  }).join(' ');
}

function movedRobotPoints(originalPoints, startSvg, currentSvg) {
  const dx = currentSvg.x - startSvg.x;
  const dy = currentSvg.y - startSvg.y;
  return originalPoints.map(p => {
    const s = robotToSVG(p.x, p.y);
    return svgToRobot(s.x + dx, s.y + dy);
  });
}

function moveAreaPath(mapId, area, points) {
  const params = new URLSearchParams();
  params.set('map_id', String(mapId));
  params.set('area_id', String(area.area_id));
  params.set('area_meta_data', area.area_meta_data || JSON.stringify({ name: '' }));
  params.set('area_type', area.area_type || 'to_be_cleaned');
  params.set('cleaning_parameter_set', String(area.cleaning_parameter_set ?? (isBlockingArea(area) ? 0 : 1)));
  params.set('area_state', area.area_state || (isBlockingArea(area) ? 'blocking' : 'clean'));
  params.set('floor_type', area.floor_type || 'none');
  params.set('room_type', area.room_type || 'none');

  points.forEach((point, index) => {
    params.set(`x${index + 1}`, String(Math.round(point.x)));
    params.set(`y${index + 1}`, String(Math.round(point.y)));
  });
  params.set('strategy_mode', area.strategy_mode || 'normal');

  return `/set/modify_area?${params.toString()}`;
}

export function startAreaDrag(areaId, startSvg) {
  const area = state.areas.find(a => String(a.area_id) === String(areaId));
  if (!isMovableArea(area)) return false;

  state.selectedAreaId = area.area_id;
  state.areaDrag = {
    areaId: area.area_id,
    startSvg,
    currentPoints: area.points.map(p => ({ x: p.x, y: p.y })),
    originalPoints: area.points.map(p => ({ x: p.x, y: p.y })),
    moved: false,
  };

  const poly = mapSvg.querySelector(`polygon[data-area-id="${area.area_id}"]`);
  if (poly) {
    poly.classList.add('area-dragging');
    poly.style.cursor = 'grabbing';
  }
  return true;
}

export function updateAreaDrag(currentSvg) {
  if (!state.areaDrag) return false;

  const drag = state.areaDrag;
  const points = movedRobotPoints(drag.originalPoints, drag.startSvg, currentSvg);
  drag.currentPoints = points;

  const dx = Math.abs(currentSvg.x - drag.startSvg.x);
  const dy = Math.abs(currentSvg.y - drag.startSvg.y);
  if (dx > 3 || dy > 3) drag.moved = true;

  const poly = mapSvg.querySelector(`polygon[data-area-id="${drag.areaId}"]`);
  if (poly) poly.setAttribute('points', pointsToSvgAttr(points));
  return true;
}

export async function finishAreaDrag() {
  const drag = state.areaDrag;
  state.areaDrag = null;
  if (!drag) return;

  const area = state.areas.find(a => String(a.area_id) === String(drag.areaId));
  const poly = mapSvg.querySelector(`polygon[data-area-id="${drag.areaId}"]`);
  if (poly) {
    poly.classList.remove('area-dragging');
    poly.style.cursor = 'grab';
  }

  if (!area || !drag.moved) {
    if (area && poly) poly.setAttribute('points', pointsToSvgAttr(area.points));
    return;
  }

  const originalPoints = area.points.map(p => ({ x: p.x, y: p.y }));
  area.points = drag.currentPoints.map(p => ({ x: p.x, y: p.y }));
  if (area._raw) area._raw.points = area.points.map(p => ({ x: p.x, y: p.y }));
  renderMap(state._lastWalls, state._lastDock);
  highlightArea(area.area_id);
  renderAreaList();

  showSpinner(true);
  try {
    const path = moveAreaPath(state.activeMapId, area, area.points);
    console.log('[modify_area move]', path);
    const res = await apiText(path);
    const cmdId = res?.cmd_id ?? res?.cmdId ?? res?.command_id;
    if (cmdId) await pollCmd(cmdId, 30000);
    state.mapHasUnsavedEdits = true;
    _updateSaveButton();
    showToast('Area moved', 'success');
  } catch (error) {
    console.error('[modify_area move]', error);
    area.points = originalPoints;
    if (area._raw) area._raw.points = originalPoints.map(p => ({ x: p.x, y: p.y }));
    renderMap(state._lastWalls, state._lastDock);
    highlightArea(area.area_id);
    renderAreaList();
    showToast('Move failed: ' + String(error.message || error).substring(0, 100), 'error');
  } finally {
    showSpinner(false);
  }
}
