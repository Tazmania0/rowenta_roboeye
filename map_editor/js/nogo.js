// NO-GO / CLEAN-SPOT AREA HELPERS
// Confirmed Rowenta/Robart add_area format: flat x1/y1...x4/y4 query params.
// Do not send area=<json> or points=...; those formats return HTTP 400.
import { state } from './state.js';
import { svgToRobot, eventToSVGPoint } from './coords.js';
import { showModal, showToast, showSpinner, showInstruction, hideInstruction } from './modal.js';
import { clearSplitOverlay, setSplitDot, setSpotRect } from './overlay.js';
import { apiText, pollCmd } from './api.js';
import { setMode } from './mode.js';
import { SPOT_AREA_TYPES } from './config.js';

export function startBlockZone() {
  startBlock();
}

export function startBlock() {
  if (!state.activeMapId || !state.bbox) {
    showToast('Load a map first', 'error');
    return;
  }
  state.splitPoints = [];
  state.rectStart = null;
  clearSplitOverlay();
  setMode('block');
  showInstruction('NO-GO AREA', 'Click and drag to draw a blocked rectangle', 'red');
}

export function startSpot() {
  if (!state.activeMapId || !state.bbox) {
    showToast('Load a map first', 'error');
    return;
  }
  state.splitPoints = [];
  state.rectStart = null;
  clearSplitOverlay();
  setMode('spot');
  showInstruction('CLEAN SPOT', 'Click and drag to draw a clean spot rectangle', 'amber');
}

function rectangleRobotPointsFromSvg(svgA, svgB) {
  const minX = Math.min(svgA.x, svgB.x);
  const maxX = Math.max(svgA.x, svgB.x);
  const minY = Math.min(svgA.y, svgB.y);
  const maxY = Math.max(svgA.y, svgB.y);
  return [
    svgToRobot(minX, minY),
    svgToRobot(minX, maxY),
    svgToRobot(maxX, maxY),
    svgToRobot(maxX, minY),
  ];
}

function areaSize(points) {
  return {
    width: Math.abs(points[3].x - points[0].x),
    height: Math.abs(points[1].y - points[0].y),
  };
}

function addAreaPath(mapId, points, { stateValue, cleaningParameterSet, name, spotAreaType }) {
  const metaData = { name: name || '' };
  if (spotAreaType && spotAreaType !== 'none') {
    metaData.spot_area_type = spotAreaType;
  }

  const params = new URLSearchParams();
  params.set('map_id', String(mapId));
  params.set('area_meta_data', JSON.stringify(metaData));
  params.set('area_type', 'to_be_cleaned');
  params.set('cleaning_parameter_set', String(cleaningParameterSet));
  params.set('area_state', stateValue);
  params.set('floor_type', 'none');
  params.set('room_type', 'none');

  points.forEach((point, index) => {
    params.set(`x${index + 1}`, String(Math.round(point.x)));
    params.set(`y${index + 1}`, String(Math.round(point.y)));
  });
  params.set('strategy_mode', 'normal');

  return `/set/add_area?${params.toString()}`;
}

async function refreshMap() {
  const { loadMap } = await import('./load.js');
  await loadMap(state.activeMapId);
}

async function runAddArea(points, options, successMessage) {
  const path = addAreaPath(state.activeMapId, points, options);
  console.log('[add_area]', path);
  const res = await apiText(path);
  const cmdId = res?.cmd_id ?? res?.cmdId ?? res?.command_id;
  if (cmdId) await pollCmd(cmdId, 30000);
  else await new Promise(resolve => setTimeout(resolve, 1500));
  await refreshMap();
  showToast(successMessage, 'success');
  setMode('select');
}

export async function executeBlockZone(svgA, svgB) {
  return executeAreaRectangle(svgA, svgB, 'block');
}

export async function executeNogo(svgX1, svgY1, svgX2, svgY2) {
  return executeAreaRectangle({ x: svgX1, y: svgY1 }, { x: svgX2, y: svgY2 }, 'block');
}

export async function executeSpotClean(svgA, svgB) {
  return executeAreaRectangle(svgA, svgB, 'spot');
}

export async function executeSpot(svgX1, svgY1, svgX2, svgY2) {
  return executeAreaRectangle({ x: svgX1, y: svgY1 }, { x: svgX2, y: svgY2 }, 'spot');
}

async function executeAreaRectangle(svgA, svgB, kind) {
  const points = rectangleRobotPointsFromSvg(svgA, svgB);
  const { width, height } = areaSize(points);

  if (width < 20 || height < 20) {
    showToast('Draw a larger rectangle', 'error');
    clearSplitOverlay();
    state.rectStart = null;
    state.rectMode = null;
    return;
  }

  const isSpot = kind === 'spot';
  let opts;
  try {
    opts = await showModal({
      title: isSpot ? 'Add clean spot area?' : 'Add no-go area?',
      desc: isSpot
        ? `Create a clean spot rectangle (${(width * 0.002).toFixed(2)}m x ${(height * 0.002).toFixed(2)}m).`
        : `Create a permanent blocked rectangle (${(width * 0.002).toFixed(2)}m x ${(height * 0.002).toFixed(2)}m).`,
      fields: isSpot ? [
        { key: 'name', label: 'Spot name', type: 'text', value: 'Spot' },
        { key: 'spot_area_type', label: 'Area type', type: 'select', options: SPOT_AREA_TYPES, value: 'none' },
      ] : [],
      confirmLabel: isSpot ? 'Add Clean Spot' : 'Add No-Go',
      danger: !isSpot,
    });
  } catch {
    clearSplitOverlay();
    state.rectStart = null;
    showInstruction(
      isSpot ? 'CLEAN SPOT' : 'NO-GO AREA',
      isSpot ? 'Click and drag to draw a clean spot rectangle' : 'Click and drag to draw a blocked rectangle',
      isSpot ? 'amber' : 'red',
    );
    return;
  }

  clearSplitOverlay();
  state.rectStart = null;
  state.rectMode = null;
  hideInstruction();
  showSpinner(true);

  try {
    await runAddArea(
      points,
      isSpot
        ? {
            stateValue: 'clean',
            cleaningParameterSet: 1,
            name: opts?.name || 'Spot',
            spotAreaType: opts?.spot_area_type || 'none',
          }
        : { stateValue: 'blocking', cleaningParameterSet: 0, name: '' },
      isSpot ? 'Clean spot area added' : 'No-go area added',
    );
    if (state.explorePhase === 'drawing') {
      const { _highlightAllInactive, _showPhaseBar } = await import('./explore.js');
      _highlightAllInactive();
      _showPhaseBar('drawing');
      setMode('split');
    }
  } catch (error) {
    console.error('[add_area]', error);
    showToast('Add area failed: ' + String(error.message || error).substring(0, 120), 'error');
    setMode('select');
  } finally {
    showSpinner(false);
  }
}

export function handleSpotClick(e) {
  e.preventDefault();
  e.stopPropagation();

  const pt = eventToSVGPoint(e);
  state.splitPoints.push(pt);
  if (state.splitPoints.length === 1) {
    setSplitDot(pt.x, pt.y);
    setSpotRect(pt.x, pt.y, pt.x, pt.y);
    showInstruction('CLEAN SPOT', 'Click opposite corner of the rectangle', 'amber');
  } else if (state.splitPoints.length === 2) {
    executeSpotClean(state.splitPoints[0], state.splitPoints[1]);
  }
}

export function handleBlockClick(e) {
  e.preventDefault();
  e.stopPropagation();

  const pt = eventToSVGPoint(e);
  state.splitPoints.push(pt);
  if (state.splitPoints.length === 1) {
    setSplitDot(pt.x, pt.y);
    setSpotRect(pt.x, pt.y, pt.x, pt.y, 'block');
    showInstruction('NO-GO AREA', 'Click opposite corner of the blocked rectangle', 'red');
  } else if (state.splitPoints.length === 2) {
    executeBlockZone(state.splitPoints[0], state.splitPoints[1]);
  }
}
