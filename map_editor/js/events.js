// ─────────────────────────────────────────────────────────────────────────────
// SVG AND WINDOW EVENT LISTENERS
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { eventToSVGPoint, svgToRobot } from './coords.js';
import { showInstruction, hideInstruction } from './modal.js';
import { clearSplitOverlay, setSplitDot, setSplitLine, setSpotRect } from './overlay.js';
import { highlightArea, renderAreaList } from './render.js';
import { setMode, fitToScreen } from './mode.js';
import { executeSplit } from './split.js';
import { startSplit } from './split.js';
import { startMerge } from './merge.js';
import { clearAreaSelection } from './areas.js';
import { executeNogo, executeSpot, handleSpotClick, handleBlockClick } from './nogo.js';
import { startGoTo, executeGoTo } from './robot.js';
import {
  clearAreaTransformHandles,
  finishAreaDrag,
  finishAreaTransform,
  renderAreaTransformHandles,
  startAreaDrag,
  startAreaTransform,
  updateAreaDrag,
  updateAreaTransform,
} from './area_move.js';

const mapSvg       = document.getElementById('map-svg');
const areaDetailEl = document.getElementById('area-detail');
const areaListEl   = document.getElementById('area-list');
const mergeHintEl  = document.getElementById('merge-hint');
const statusCoords = document.getElementById('status-coords');

function _isTypingTarget(target) {
  const el = target instanceof Element ? target : null;
  if (!el) return false;
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable;
}

function _zoom(factor, cx, cy) {
  const vb = mapSvg.viewBox.baseVal;
  const nw = vb.width * factor;
  const nh = vb.height * factor;
  const nx = cx - (cx - vb.x) * factor;
  const ny = cy - (cy - vb.y) * factor;
  mapSvg.setAttribute('viewBox', `${nx} ${ny} ${nw} ${nh}`);
}

export function initEvents(onAreaClick) {
  // ── SVG click: capture phase (spot/block)
  mapSvg.addEventListener('click', e => {
    if (state.suppressNextClick) {
      state.suppressNextClick = false;
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (state.mode === 'spot') handleSpotClick(e);
    if (state.mode === 'block') handleBlockClick(e);
  }, true);

  // ── SVG click: bubble phase (split, select)
  mapSvg.addEventListener('click', e => {
    if (state.mode === 'spot' || state.mode === 'block') {
      return;
    }

    if (state.mode === 'goto') {
      const pt = eventToSVGPoint(e);
      executeGoTo(pt.x, pt.y);
      return;
    }

    if (state.mode === 'split') {
      // Only place points if an area is already selected
      if (state.selectedAreaId === null) return;
      const pt = eventToSVGPoint(e);
      state.splitPoints.push(pt);

      if (state.splitPoints.length === 1) {
        // Show dot at first point + update instruction
        setSplitDot(pt.x, pt.y);
        setSplitLine(pt.x, pt.y, pt.x, pt.y);
        showInstruction('STEP 3 of 3', 'Click second point to complete the split line', 'green');
      } else if (state.splitPoints.length === 2) {
        executeSplit(state.splitPoints[0], state.splitPoints[1]);
      }
      return;
    }

    // Deselect on empty-space click in select mode
    if (state.mode === 'select') {
      const tag = e.target.tagName;
      if (tag === 'svg' || tag === 'rect' || tag === 'g') {
        clearAreaSelection();
      }
    }
  });

  // ── SVG mousemove
  mapSvg.addEventListener('mousemove', e => {
    const pt = eventToSVGPoint(e);
    const robotPt = state.bbox ? svgToRobot(pt.x, pt.y) : null;
    if (robotPt) {
      statusCoords.textContent = `x:${robotPt.x}  y:${robotPt.y}  (${(robotPt.x*0.002).toFixed(2)}m, ${(robotPt.y*0.002).toFixed(2)}m)`;
    }

    if (state.areaTransform) {
      updateAreaTransform(pt);
      return;
    }

    if (state.areaDrag) {
      updateAreaDrag(pt);
      return;
    }

    // Split line: track cursor from first point
    if (state.mode === 'split' && state.splitPoints.length === 1) {
      const p0 = state.splitPoints[0];
      setSplitLine(p0.x, p0.y, pt.x, pt.y);
    }

    if ((state.mode === 'block' || state.mode === 'spot') && state.rectStart) {
      setSpotRect(state.rectStart.x, state.rectStart.y, pt.x, pt.y, state.mode);
    } else if (state.mode === 'spot' && state.splitPoints.length === 1) {
      const p0 = state.splitPoints[0];
      setSpotRect(p0.x, p0.y, pt.x, pt.y);
    } else if (state.mode === 'block' && state.splitPoints.length === 1) {
      const p0 = state.splitPoints[0];
      setSpotRect(p0.x, p0.y, pt.x, pt.y, 'block');
    }

    // Pan
    if (state.panStart) {
      const dx = e.clientX - state.panStart.cx;
      const dy = e.clientY - state.panStart.cy;
      const vb = mapSvg.viewBox.baseVal;
      const rect = mapSvg.getBoundingClientRect();
      const scaleX = vb.width  / rect.width;
      const scaleY = vb.height / rect.height;
      mapSvg.setAttribute('viewBox',
        `${state.panStart.vbx - dx*scaleX} ${state.panStart.vby - dy*scaleY} ${vb.width} ${vb.height}`);
    }
  });

  // ── SVG mousedown
  mapSvg.addEventListener('mousedown', e => {
    if (state.mode === 'select' && e.button === 0) {
      if (startAreaTransform(e.target, eventToSVGPoint(e))) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }

      const poly = e.target instanceof Element ? e.target.closest('polygon[data-area-id]') : null;
      if (poly && startAreaDrag(poly.dataset.areaId, eventToSVGPoint(e))) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
    }

    if (e.button === 1 || state.mode === 'pan') {
      state.panStart = {
        cx: e.clientX, cy: e.clientY,
        vbx: mapSvg.viewBox.baseVal.x,
        vby: mapSvg.viewBox.baseVal.y,
      };
      mapSvg.classList.add('panning');
      e.preventDefault();
      return;
    }

    if (state.mode === 'block' || state.mode === 'spot') {
      const pt = eventToSVGPoint(e);
      state.rectStart = pt;
      state.rectMode  = state.mode;
      e.preventDefault();
      return;
    }
  });

  // ── Window mouseup
  window.addEventListener('mouseup', e => {
    if (state.areaTransform) {
      const moved = state.areaTransform.moved;
      if (moved) state.suppressNextClick = true;
      finishAreaTransform();
      e.preventDefault();
      return;
    }

    if (state.areaDrag) {
      const moved = state.areaDrag.moved;
      if (moved) state.suppressNextClick = true;
      finishAreaDrag();
      e.preventDefault();
      return;
    }

    state.panStart = null;
    mapSvg.classList.remove('panning');

    if (state.rectStart && state.rectMode) {
      const pt = eventToSVGPoint(e);
      const rs = state.rectStart;
      state.rectStart = null;
      // (suppression of the trailing click is set in the execute branches below)

      const dx = Math.abs(pt.x - rs.x);
      const dy = Math.abs(pt.y - rs.y);
      if (dx < 10 && dy < 10) {
        clearSplitOverlay();
        state.rectMode = null;
        return;
      }

      // A real drag just completed; suppress the trailing click so it does not
      // also fire handleBlockClick/handleSpotClick and push a stray point into
      // state.splitPoints (which would corrupt the next draw).
      state.suppressNextClick = true;
      if (state.rectMode === 'block') {
        state.rectMode = null;
        executeNogo(rs.x, rs.y, pt.x, pt.y);
      } else if (state.rectMode === 'spot') {
        state.rectMode = null;
        executeSpot(rs.x, rs.y, pt.x, pt.y);
      }
    }
  });

  // ── Abort an in-progress area drag if the pointer is released outside the
  // window or focus is lost.  Without this, state.areaDrag stays set and every
  // subsequent mousemove keeps dragging the polygon with no button held.
  window.addEventListener('blur', () => {
    if (state.areaTransform) finishAreaTransform();
    if (state.areaDrag) finishAreaDrag();
  });
  document.addEventListener('pointercancel', () => {
    if (state.areaTransform) finishAreaTransform();
    if (state.areaDrag) finishAreaDrag();
  });

  // ── Scroll zoom
  mapSvg.addEventListener('wheel', e => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.12 : 1/1.12;
    const pt = eventToSVGPoint(e);
    const vb = mapSvg.viewBox.baseVal;
    const nw = vb.width * factor;
    const nh = vb.height * factor;
    const nx = pt.x - (pt.x - vb.x) * factor;
    const ny = pt.y - (pt.y - vb.y) * factor;
    mapSvg.setAttribute('viewBox', `${nx} ${ny} ${nw} ${nh}`);
  }, { passive: false });

  // ── Zoom buttons
  document.getElementById('zoom-in').addEventListener('click', () => {
    const vb = mapSvg.viewBox.baseVal;
    const cx = vb.x + vb.width/2, cy = vb.y + vb.height/2;
    const nw = vb.width/1.3, nh = vb.height/1.3;
    mapSvg.setAttribute('viewBox', `${cx-nw/2} ${cy-nh/2} ${nw} ${nh}`);
  });
  document.getElementById('zoom-out').addEventListener('click', () => {
    const vb = mapSvg.viewBox.baseVal;
    const cx = vb.x + vb.width/2, cy = vb.y + vb.height/2;
    const nw = vb.width*1.3, nh = vb.height*1.3;
    mapSvg.setAttribute('viewBox', `${cx-nw/2} ${cy-nh/2} ${nw} ${nh}`);
  });

  // ── Space = pan mode (hold)
  window.addEventListener('keydown', e => {
    if (_isTypingTarget(e.target)) return;

    if (e.code === 'Space' && state.mode !== 'pan') { e.preventDefault(); setMode('pan'); }
    if (e.code === 'Escape') {
      state.splitPoints  = [];
      state.mergeFirstId = null;
      state.rectStart    = null;
      state.rectMode     = null;
      state.areaTransform = null;
      clearSplitOverlay();
      clearAreaTransformHandles();
      hideInstruction();
      mergeHintEl.classList.remove('visible');
      if (state.mode === 'goto') setMode('select');
      setMode('select');
      if (state.selectedAreaId !== null) {
        highlightArea(state.selectedAreaId);
        renderAreaTransformHandles(state.selectedAreaId);
      } else {
        highlightArea(null);
      }
      // In draw phase: return to split mode (it is the default for that phase)
      if (state.explorePhase === 'drawing') {
        setMode('split');
        showInstruction('PHASE 1 / 3 — DRAW ROOMS',
          'Split large areas, merge small fragments, then click "Done Drawing"', 'green');
      }
    }
    if (e.code === 'KeyS') setMode('select');
    if (e.code === 'KeyX') startSplit();
    if (e.code === 'KeyM') startMerge();
    if (e.code === 'KeyF') fitToScreen();
    if (e.code === 'KeyV') { import('./nogo.js').then(m => m.startSpot()); }
    if (e.code === 'KeyG') startGoTo();
  });
  window.addEventListener('keyup', e => {
    if (_isTypingTarget(e.target)) return;

    if (e.code === 'Space') setMode('select');
  });
}
