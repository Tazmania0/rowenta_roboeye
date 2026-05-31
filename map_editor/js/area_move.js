import { state } from './state.js';
import { robotToSVG, svgToRobot } from './coords.js';
import { apiText, pollCmd } from './api.js';
import { showSpinner, showToast } from './modal.js';
import { highlightArea, isBlockingArea, isSpotArea, renderAreaList, renderMap } from './render.js';
import { _updateSaveButton } from './mapops.js';

const mapSvg = document.getElementById('map-svg');
const SVG_NS = 'http://www.w3.org/2000/svg';

function isMovableArea(area) {
  return area && (isBlockingArea(area) || isSpotArea(area));
}

function pointsToSvgAttr(points) {
  return points.map(p => {
    const s = robotToSVG(p.x, p.y);
    return `${s.x},${s.y}`;
  }).join(' ');
}

function svgPointsFromRobot(points) {
  return points.map(p => robotToSVG(p.x, p.y));
}

function centroid(points) {
  if (!points.length) return { x: 0, y: 0 };
  return {
    x: points.reduce((sum, p) => sum + p.x, 0) / points.length,
    y: points.reduce((sum, p) => sum + p.y, 0) / points.length,
  };
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function angle(a, b) {
  return Math.atan2(a.y - b.y, a.x - b.x);
}

function dot(a, b) {
  return a.x * b.x + a.y * b.y;
}

function normalize(v) {
  const len = Math.hypot(v.x, v.y);
  return len > 0 ? { x: v.x / len, y: v.y / len } : { x: 1, y: 0 };
}

function orientedBasis(points, center) {
  if (points.length < 2) return { ux: { x: 1, y: 0 }, uy: { x: 0, y: 1 } };
  const ux = normalize({ x: points[1].x - points[0].x, y: points[1].y - points[0].y });
  const uyCandidate = { x: -ux.y, y: ux.x };
  const farthest = points.reduce((best, point) => {
    const d = distance(point, center);
    return d > best.d ? { point, d } : best;
  }, { point: points[0], d: -1 }).point;
  const fromCenter = { x: farthest.x - center.x, y: farthest.y - center.y };
  const uy = dot(fromCenter, uyCandidate) >= 0
    ? uyCandidate
    : { x: -uyCandidate.x, y: -uyCandidate.y };
  return { ux, uy };
}

function projectedExtents(points, center, basis) {
  return points.reduce((ext, point) => {
    const v = { x: point.x - center.x, y: point.y - center.y };
    return {
      hx: Math.max(ext.hx, Math.abs(dot(v, basis.ux))),
      hy: Math.max(ext.hy, Math.abs(dot(v, basis.uy))),
    };
  }, { hx: 1, hy: 1 });
}

function rotatePoint(point, center, radians) {
  const dx = point.x - center.x;
  const dy = point.y - center.y;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  return {
    x: center.x + dx * cos - dy * sin,
    y: center.y + dx * sin + dy * cos,
  };
}

function svgPointsToRobot(points) {
  return points.map(p => svgToRobot(p.x, p.y));
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

function updateAreaLocalPoints(area, points) {
  area.points = points.map(p => ({ x: p.x, y: p.y }));
  if (area._raw) area._raw.points = area.points.map(p => ({ x: p.x, y: p.y }));
}

async function commitAreaGeometry(area, originalPoints, successMessage) {
  updateAreaLocalPoints(area, area.points);
  renderMap(state._lastWalls, state._lastDock);
  highlightArea(area.area_id);
  renderAreaTransformHandles(area.area_id);
  renderAreaList();

  showSpinner(true);
  try {
    const path = moveAreaPath(state.activeMapId, area, area.points);
    console.log('[modify_area geometry]', path);
    const res = await apiText(path);
    const cmdId = res?.cmd_id ?? res?.cmdId ?? res?.command_id;
    if (cmdId) await pollCmd(cmdId, 30000);
    state.mapHasUnsavedEdits = true;
    _updateSaveButton();
    showToast(successMessage, 'success');
  } catch (error) {
    console.error('[modify_area geometry]', error);
    updateAreaLocalPoints(area, originalPoints);
    renderMap(state._lastWalls, state._lastDock);
    highlightArea(area.area_id);
    renderAreaTransformHandles(area.area_id);
    renderAreaList();
    showToast('Geometry update failed: ' + String(error.message || error).substring(0, 100), 'error');
  } finally {
    showSpinner(false);
  }
}

function transformOverlay() {
  return mapSvg.querySelector('#split-overlay');
}

export function clearAreaTransformHandles() {
  transformOverlay()?.querySelector('#area-transform-handles')?.remove();
}

export function renderAreaTransformHandles(areaId = state.selectedAreaId) {
  clearAreaTransformHandles();
  const area = state.areas.find(a => String(a.area_id) === String(areaId));
  if (!isMovableArea(area) || !area.points || area.points.length < 3 || state.mode !== 'select') return;

  const overlay = transformOverlay();
  if (!overlay) return;

  const svgPoints = svgPointsFromRobot(area.points);
  const center = centroid(svgPoints);
  const firstPoint = svgPoints[0];
  const rotateVector = {
    x: firstPoint.x - center.x,
    y: firstPoint.y - center.y,
  };
  const rawRotateLen = Math.hypot(rotateVector.x, rotateVector.y);
  const rotateLen = Math.max(80, rawRotateLen);
  const rotateUnit = rawRotateLen > 0
    ? { x: rotateVector.x / rawRotateLen, y: rotateVector.y / rawRotateLen }
    : { x: 0, y: -1 };
  const rotateHandle = {
    x: center.x + rotateUnit.x * (rotateLen + 90),
    y: center.y + rotateUnit.y * (rotateLen + 90),
  };

  const group = document.createElementNS(SVG_NS, 'g');
  group.id = 'area-transform-handles';
  group.dataset.areaId = area.area_id;

  const frame = document.createElementNS(SVG_NS, 'polygon');
  frame.setAttribute('class', 'area-transform-frame');
  frame.setAttribute('points', svgPoints.map(p => `${p.x},${p.y}`).join(' '));
  group.appendChild(frame);

  const link = document.createElementNS(SVG_NS, 'line');
  link.setAttribute('class', 'area-transform-link');
  link.setAttribute('x1', center.x);
  link.setAttribute('y1', center.y);
  link.setAttribute('x2', rotateHandle.x);
  link.setAttribute('y2', rotateHandle.y);
  group.appendChild(link);

  svgPoints.forEach((point, index) => {
    const handle = document.createElementNS(SVG_NS, 'rect');
    handle.setAttribute('class', 'area-transform-handle resize');
    handle.dataset.areaId = area.area_id;
    handle.dataset.transformHandle = 'resize';
    handle.dataset.pointIndex = String(index);
    handle.setAttribute('x', point.x - 22);
    handle.setAttribute('y', point.y - 22);
    handle.setAttribute('width', 44);
    handle.setAttribute('height', 44);
    handle.setAttribute('rx', 8);
    group.appendChild(handle);
  });

  const rotate = document.createElementNS(SVG_NS, 'circle');
  rotate.setAttribute('class', 'area-transform-handle rotate');
  rotate.dataset.areaId = area.area_id;
  rotate.dataset.transformHandle = 'rotate';
  rotate.setAttribute('cx', rotateHandle.x);
  rotate.setAttribute('cy', rotateHandle.y);
  rotate.setAttribute('r', 24);
  group.appendChild(rotate);

  const basis = orientedBasis(svgPoints, center);
  const ext = projectedExtents(svgPoints, center, basis);
  [
    { axis: 'x', sign: -1 },
    { axis: 'x', sign: 1 },
    { axis: 'y', sign: -1 },
    { axis: 'y', sign: 1 },
  ].forEach(({ axis, sign }) => {
    const point = axis === 'x'
      ? { x: center.x + basis.ux.x * ext.hx * sign, y: center.y + basis.ux.y * ext.hx * sign }
      : { x: center.x + basis.uy.x * ext.hy * sign, y: center.y + basis.uy.y * ext.hy * sign };
    const handle = document.createElementNS(SVG_NS, 'circle');
    handle.setAttribute('class', 'area-transform-handle resize-edge');
    handle.dataset.areaId = area.area_id;
    handle.dataset.transformHandle = 'resize-axis';
    handle.dataset.axis = axis;
    handle.dataset.sign = String(sign);
    handle.setAttribute('cx', point.x);
    handle.setAttribute('cy', point.y);
    handle.setAttribute('r', 17);
    group.appendChild(handle);
  });

  overlay.appendChild(group);
}

function updateAreaTransformHandlesPreview(areaId, svgPoints) {
  const group = mapSvg.querySelector(`#area-transform-handles[data-area-id="${areaId}"]`);
  if (!group) return;
  const center = centroid(svgPoints);
  const firstPoint = svgPoints[0];
  const vector = { x: firstPoint.x - center.x, y: firstPoint.y - center.y };
  const rawLen = Math.hypot(vector.x, vector.y);
  const shownLen = Math.max(80, rawLen);
  const unit = rawLen > 0 ? { x: vector.x / rawLen, y: vector.y / rawLen } : { x: 0, y: -1 };
  const rotateHandle = {
    x: center.x + unit.x * (shownLen + 90),
    y: center.y + unit.y * (shownLen + 90),
  };

  group.querySelector('.area-transform-frame')
    ?.setAttribute('points', svgPoints.map(p => `${p.x},${p.y}`).join(' '));
  const link = group.querySelector('.area-transform-link');
  if (link) {
    link.setAttribute('x1', center.x);
    link.setAttribute('y1', center.y);
    link.setAttribute('x2', rotateHandle.x);
    link.setAttribute('y2', rotateHandle.y);
  }
  group.querySelectorAll('.area-transform-handle.resize').forEach(handle => {
    const point = svgPoints[Number(handle.dataset.pointIndex || 0)];
    if (!point) return;
    handle.setAttribute('x', point.x - 22);
    handle.setAttribute('y', point.y - 22);
  });
  const rotate = group.querySelector('.area-transform-handle.rotate');
  if (rotate) {
    rotate.setAttribute('cx', rotateHandle.x);
    rotate.setAttribute('cy', rotateHandle.y);
  }

  const basis = orientedBasis(svgPoints, center);
  const ext = projectedExtents(svgPoints, center, basis);
  group.querySelectorAll('.area-transform-handle.resize-edge').forEach(handle => {
    const axis = handle.dataset.axis;
    const sign = Number(handle.dataset.sign || 1);
    const point = axis === 'x'
      ? { x: center.x + basis.ux.x * ext.hx * sign, y: center.y + basis.ux.y * ext.hx * sign }
      : { x: center.x + basis.uy.x * ext.hy * sign, y: center.y + basis.uy.y * ext.hy * sign };
    handle.setAttribute('cx', point.x);
    handle.setAttribute('cy', point.y);
  });
}

export function startAreaTransform(target, startSvg) {
  const handle = target instanceof Element ? target.closest('[data-transform-handle]') : null;
  if (!handle) return false;

  const area = state.areas.find(a => String(a.area_id) === String(handle.dataset.areaId));
  if (!isMovableArea(area)) return false;

  const originalSvgPoints = svgPointsFromRobot(area.points);
  const center = centroid(originalSvgPoints);
  const basis = orientedBasis(originalSvgPoints, center);
  state.selectedAreaId = area.area_id;
  state.selectedAreaIds = new Set([area.area_id]);
  state.areaTransform = {
    areaId: area.area_id,
    type: handle.dataset.transformHandle,
    pointIndex: Number(handle.dataset.pointIndex ?? -1),
    center,
    startSvg,
    originalSvgPoints,
    originalPoints: area.points.map(p => ({ x: p.x, y: p.y })),
    currentPoints: area.points.map(p => ({ x: p.x, y: p.y })),
    moved: false,
    startDistance: handle.dataset.transformHandle === 'resize'
      ? distance(startSvg, center)
      : 0,
    startAngle: handle.dataset.transformHandle === 'rotate'
      ? angle(startSvg, center)
      : 0,
    axis: handle.dataset.axis || null,
    axisSign: Number(handle.dataset.sign || 1),
    basis,
    extents: projectedExtents(originalSvgPoints, center, basis),
  };
  mapSvg.querySelector(`polygon[data-area-id="${area.area_id}"]`)?.classList.add('area-dragging');
  return true;
}

export function updateAreaTransform(currentSvg) {
  const drag = state.areaTransform;
  if (!drag) return false;

  let svgPoints;
  if (drag.type === 'rotate') {
    const delta = angle(currentSvg, drag.center) - drag.startAngle;
    svgPoints = drag.originalSvgPoints.map(point => rotatePoint(point, drag.center, delta));
  } else if (drag.type === 'resize-axis') {
    const axisVector = drag.axis === 'y' ? drag.basis.uy : drag.basis.ux;
    const baseHalf = drag.axis === 'y' ? drag.extents.hy : drag.extents.hx;
    const projected = dot(
      { x: currentSvg.x - drag.center.x, y: currentSvg.y - drag.center.y },
      axisVector,
    ) * drag.axisSign;
    const scale = Math.max(0.15, projected / Math.max(1, baseHalf));
    svgPoints = drag.originalSvgPoints.map(point => {
      const v = { x: point.x - drag.center.x, y: point.y - drag.center.y };
      const localX = dot(v, drag.basis.ux);
      const localY = dot(v, drag.basis.uy);
      const sx = drag.axis === 'x' ? scale : 1;
      const sy = drag.axis === 'y' ? scale : 1;
      return {
        x: drag.center.x + drag.basis.ux.x * localX * sx + drag.basis.uy.x * localY * sy,
        y: drag.center.y + drag.basis.ux.y * localX * sx + drag.basis.uy.y * localY * sy,
      };
    });
  } else {
    const scale = Math.max(0.15, distance(currentSvg, drag.center) / Math.max(1, drag.startDistance));
    svgPoints = drag.originalSvgPoints.map(point => ({
      x: drag.center.x + (point.x - drag.center.x) * scale,
      y: drag.center.y + (point.y - drag.center.y) * scale,
    }));
  }

  drag.currentPoints = svgPointsToRobot(svgPoints);
  if (distance(currentSvg, drag.startSvg) > 3) drag.moved = true;

  const poly = mapSvg.querySelector(`polygon[data-area-id="${drag.areaId}"]`);
  if (poly) poly.setAttribute('points', pointsToSvgAttr(drag.currentPoints));
  updateAreaTransformHandlesPreview(drag.areaId, svgPoints);
  return true;
}

export async function finishAreaTransform() {
  const drag = state.areaTransform;
  state.areaTransform = null;
  if (!drag) return;

  const area = state.areas.find(a => String(a.area_id) === String(drag.areaId));
  mapSvg.querySelector(`polygon[data-area-id="${drag.areaId}"]`)?.classList.remove('area-dragging');
  if (!area || !drag.moved) {
    if (area) renderAreaTransformHandles(area.area_id);
    return;
  }

  updateAreaLocalPoints(area, drag.currentPoints);
  await commitAreaGeometry(area, drag.originalPoints, drag.type === 'rotate' ? 'Area rotated' : 'Area resized');
}

export function startAreaDrag(areaId, startSvg) {
  const area = state.areas.find(a => String(a.area_id) === String(areaId));
  if (!isMovableArea(area)) return false;

  state.selectedAreaId = area.area_id;
  state.selectedAreaIds = new Set([area.area_id]);
  const btnClean = document.getElementById('btn-clean-area');
  if (btnClean) {
    const busy = state.robotMode === 'cleaning' || state.robotMode === 'go_home';
    btnClean.disabled = area.area_state !== 'clean' || busy;
  }
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
  clearAreaTransformHandles();
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
  updateAreaLocalPoints(area, drag.currentPoints);
  await commitAreaGeometry(area, originalPoints, 'Area moved');
}
