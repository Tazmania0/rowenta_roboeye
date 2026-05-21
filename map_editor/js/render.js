// ─────────────────────────────────────────────────────────────────────────────
// RENDER — SVG map rendering and area list
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { areaToSVGPoints, robotToSVG } from './coords.js';
import { showToast } from './modal.js';

const mapSvg    = document.getElementById('map-svg');
const mapGroup  = document.getElementById('map-group');
const mapBarEl  = document.getElementById('map-bar');
const areaListEl = document.getElementById('area-list');
const emptyState = document.getElementById('empty-state');
const SVG_NS = 'http://www.w3.org/2000/svg';

// Callback injected by main.js to avoid circular dependency (render → areas → render)
let _onAreaClickCb = null;
export function setAreaClickCallback(fn) { _onAreaClickCb = fn; }

// ─────────────────────────────────────────────────────────────────────────────
// MAP CHIPS
// ─────────────────────────────────────────────────────────────────────────────
export function renderMapChips({ onChipClick, onDiscardClick, pollCmd, showModal, showSpinner, hideInstruction, _showPhaseBar, _hideSaveMapButton, loadMaps, loadMap } = {}) {
  const placeholder = document.getElementById('map-chips-placeholder');
  if (placeholder) placeholder.remove();

  // Clear old chips and discard buttons
  mapBarEl.querySelectorAll('.map-chip, .map-discard-btn').forEach(e => e.remove());

  state.maps.forEach((m, i) => {
    const name = (m.map_meta_data || '').trim() || `Map ${i+1}`;
    const chip = document.createElement('button');
    chip.className = 'map-chip' + (m.map_id === state.activeMapId ? ' active' : '');
    chip.dataset.mapId = m.map_id;
    if (state.explorePhase === 'running') {
      chip.disabled = true;
      chip.title = 'Exploration is running';
    }

    // Temporary/unsaved explore map — render differently
    if (m.map_id === state.exploreMapId) {
      chip.style.borderColor  = '#f59e0b';
      chip.style.color        = '#f59e0b';
      chip.textContent        = `${name} (unsaved)`;

      const discard = document.createElement('button');
      discard.className   = 'btn map-discard-btn';
      discard.textContent = '✕ Discard';
      discard.style.cssText = 'font-size:10px;padding:2px 8px;border-color:#ef4444;color:#ef4444';
      if (state.explorePhase === 'running') {
        discard.disabled = true;
        discard.title = 'Exploration is running';
      }
      discard.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (onDiscardClick) await onDiscardClick(m.map_id);
      });
      chip.addEventListener('click', () => onChipClick && onChipClick(m.map_id));
      mapBarEl.appendChild(chip);
      mapBarEl.appendChild(discard);
      return;
    }

    chip.textContent = name;
    chip.addEventListener('click', () => onChipClick && onChipClick(m.map_id));
    mapBarEl.appendChild(chip);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// RENDER SVG
// ─────────────────────────────────────────────────────────────────────────────
export function renderMap(walls=[], dock=null) {
  const pad = 200;
  const { minX, minY, maxX, maxY } = state.bbox;
  const vbX = minX - pad, vbY = minY - pad;
  const vbW = (maxX - minX) + pad*2;
  const vbH = (maxY - minY) + pad*2;
  mapSvg.setAttribute('viewBox', `${vbX} ${vbY} ${vbW} ${vbH}`);

  // Clear map group
  mapGroup.innerHTML = '';

  const defs = document.createElementNS(SVG_NS, 'defs');
  defs.innerHTML = `
    <pattern id="editor-hatch-red" patternUnits="userSpaceOnUse" width="80" height="80">
      <path d="M -20,20 L 20,-20 M 0,80 L 80,0 M 60,100 L 100,60"
        stroke="#ef4444" stroke-width="6" stroke-opacity="0.8"/>
      <path d="M -20,60 L 20,100 M 0,0 L 80,80 M 60,-20 L 100,20"
        stroke="#ef4444" stroke-width="6" stroke-opacity="0.8"/>
    </pattern>
    <pattern id="editor-hatch-amber" patternUnits="userSpaceOnUse" width="80" height="80" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="80"
        stroke="#f59e0b" stroke-width="12" stroke-opacity="0.75"/>
    </pattern>`;
  mapGroup.appendChild(defs);

  // ── Layer 1: Floor background
  const floor = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  floor.setAttribute('x', minX); floor.setAttribute('y', minY);
  floor.setAttribute('width', maxX-minX); floor.setAttribute('height', maxY-minY);
  floor.setAttribute('fill', 'rgba(255,255,255,0.04)');
  floor.setAttribute('rx', '4');
  mapGroup.appendChild(floor);

  // ── Layer 2: Area polygons
  const areaGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  areaGroup.id = 'area-polygons';

  // Render in Z-order: blocking → inactive → named (clean) on top so named
  // rooms capture mouse events over any overlapping inactive segments.
  const zOrder = ['inactive', 'declined_blocking', 'clean', 'blocking', 'proposed_blocking'];
  const sorted = [...state.areas].sort((a, b) => {
    const ai = isSpotArea(a) ? 98 : (zOrder.indexOf(a.area_state) === -1 ? 99 : zOrder.indexOf(a.area_state));
    const bi = isSpotArea(b) ? 98 : (zOrder.indexOf(b.area_state) === -1 ? 99 : zOrder.indexOf(b.area_state));
    return ai - bi;
  });

  sorted.forEach(area => {
    if (!area.points || area.points.length < 3) return;

    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', areaToSVGPoints(area));
    poly.dataset.areaId = area.area_id;

    // Style by state
    const st = area.area_state;
    if (isSpotArea(area)) {
      poly.setAttribute('fill', 'url(#editor-hatch-amber)');
      poly.setAttribute('stroke', '#f59e0b');
      poly.setAttribute('stroke-width', '7');
      poly.setAttribute('stroke-dasharray', '18 10');
    } else if (st === 'clean') {
      poly.setAttribute('fill', 'rgba(59,130,246,0.15)');
      poly.setAttribute('stroke', '#3b82f6');
      poly.setAttribute('stroke-width', '8');
    } else if (st === 'blocking' || st === 'proposed_blocking') {
      poly.setAttribute('fill', 'url(#editor-hatch-red)');
      poly.setAttribute('stroke', '#ef4444');
      poly.setAttribute('stroke-width', '6');
      poly.setAttribute('stroke-dasharray', '10 8');
      poly.style.pointerEvents = 'auto';
    } else {
      // inactive / unknown — render beneath named rooms, don't intercept clicks
      poly.setAttribute('fill', 'rgba(100,116,139,0.08)');
      poly.setAttribute('stroke', '#334155');
      poly.setAttribute('stroke-width', '4');
      poly.setAttribute('stroke-dasharray', '12 6');
      poly.style.pointerEvents = 'none';  // inactive — cannot steal clicks
    }

    poly.setAttribute('stroke-linejoin', 'round');
    if (st !== 'inactive' && st !== 'declined_blocking') {
      poly.style.pointerEvents = 'auto';
      poly.style.cursor = isBlockingArea(area) || isSpotArea(area) ? 'grab' : 'pointer';
      poly.style.transition = 'fill 0.15s';
    }
    if (st === 'clean') {
      poly.style.cursor = isSpotArea(area) ? 'grab' : 'pointer';
      poly.style.transition = 'fill 0.15s';
    }

    // Named rooms, no-go areas, and clean spots are interactable so they can be selected/deleted
    poly.addEventListener('mouseenter', () => {
      if (area.area_id !== state.selectedAreaId)
        poly.setAttribute('fill', 'rgba(255,255,255,0.08)');
    });
    poly.addEventListener('mouseleave', () => {
      if (area.area_id !== state.selectedAreaId)
        setAreaPolyStyle(poly, area);
    });
    poly.addEventListener('click', e => {
      if (state.suppressNextClick) {
        state.suppressNextClick = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (state.mode === 'split' && state.selectedAreaId !== null) {
        // In split mode with area selected: treat click as a point placement
        // (don't stop propagation — let SVG handler place the point)
        return;
      }
      if (state.mode === 'spot') {
        e.preventDefault();
        return;
      }
      if (state.mode === 'block') {
        e.preventDefault();
        return;
      }
      e.stopPropagation();
      if (_onAreaClickCb) _onAreaClickCb(area.area_id);
    });

    areaGroup.appendChild(poly);

    // Room label
    if (area.area_state === 'clean' && area.points.length >= 3) {
      const cx = area.points.reduce((s,p) => s+p.x, 0) / area.points.length;
      const cy = area.points.reduce((s,p) => s+p.y, 0) / area.points.length;
      const sc = robotToSVG(cx, cy);

      const name = getAreaName(area);
      const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      txt.setAttribute('x', sc.x);
      txt.setAttribute('y', sc.y);
      txt.setAttribute('text-anchor', 'middle');
      txt.setAttribute('dominant-baseline', 'central');
      txt.setAttribute('fill', '#93c5fd');
      txt.setAttribute('font-size', '60');
      txt.setAttribute('font-family', 'Sora, sans-serif');
      txt.setAttribute('font-weight', '500');
      txt.setAttribute('pointer-events', 'none');
      txt.textContent = name;
      areaGroup.appendChild(txt);

      const statsLines = svgAreaStatsLines(area);
      statsLines.forEach((line, index) => {
        const idTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        idTxt.setAttribute('x', sc.x);
        idTxt.setAttribute('y', sc.y + 75 + index * 48);
        idTxt.setAttribute('text-anchor', 'middle');
        idTxt.setAttribute('fill', '#475569');
        idTxt.setAttribute('font-size', '40');
        idTxt.setAttribute('font-family', 'JetBrains Mono, monospace');
        idTxt.setAttribute('pointer-events', 'none');
        idTxt.textContent = line;
        areaGroup.appendChild(idTxt);
      });
    }
  });
  mapGroup.appendChild(areaGroup);

  // ── Layer 3: Walls
  if (walls.length > 0) {
    const wallGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    walls.forEach(w => {
      const a = robotToSVG(w.x1, w.y1);
      const b = robotToSVG(w.x2, w.y2);
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
      line.setAttribute('stroke', '#94a3b8');
      line.setAttribute('stroke-width', '6');
      line.setAttribute('stroke-linecap', 'round');
      wallGroup.appendChild(line);
    });
    mapGroup.appendChild(wallGroup);
  }

  // ── Layer 4: Dock icon
  if (dock && dock.valid) {
    const ds = robotToSVG(dock.x, dock.y);
    const dockG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    dockG.setAttribute('transform', `translate(${ds.x},${ds.y})`);

    // Dock symbol: filled D shape
    const dockBg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    dockBg.setAttribute('x', -40); dockBg.setAttribute('y', -40);
    dockBg.setAttribute('width', 80); dockBg.setAttribute('height', 80);
    dockBg.setAttribute('rx', '10');
    dockBg.setAttribute('fill', '#f59e0b');
    dockBg.setAttribute('fill-opacity', '0.9');
    dockBg.setAttribute('stroke', '#fbbf24');
    dockBg.setAttribute('stroke-width', '4');

    const dockTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    dockTxt.setAttribute('text-anchor', 'middle');
    dockTxt.setAttribute('dominant-baseline', 'central');
    dockTxt.setAttribute('fill', '#1c1917');
    dockTxt.setAttribute('font-size', '48');
    dockTxt.setAttribute('font-weight', '700');
    dockTxt.textContent = '⌂';

    dockG.appendChild(dockBg);
    dockG.appendChild(dockTxt);
    mapGroup.appendChild(dockG);
  }

  // Overlay group for split line/dot — appended last so it's always on top
  const overlayG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  overlayG.id = 'split-overlay';
  mapGroup.appendChild(overlayG);
}

export function setAreaPolyStyle(poly, area) {
  const st = area.area_state;
  poly.removeAttribute('stroke-dasharray');
  if (st === 'clean') {
    if (isSpotArea(area)) {
      poly.setAttribute('fill', 'url(#editor-hatch-amber)');
      poly.setAttribute('stroke', '#f59e0b');
      poly.setAttribute('stroke-width', '7');
      poly.setAttribute('stroke-dasharray', '18 10');
      return;
    }
    poly.setAttribute('fill', 'rgba(59,130,246,0.15)');
    poly.setAttribute('stroke', '#3b82f6');
    poly.setAttribute('stroke-width', '8');
  } else if (st === 'blocking' || st === 'proposed_blocking') {
    poly.setAttribute('fill', 'url(#editor-hatch-red)');
    poly.setAttribute('stroke', '#ef4444');
    poly.setAttribute('stroke-width', '6');
    poly.setAttribute('stroke-dasharray', '10 8');
  } else {
    poly.setAttribute('fill', 'rgba(100,116,139,0.08)');
    poly.setAttribute('stroke', '#334155');
    poly.setAttribute('stroke-width', '4');
    poly.setAttribute('stroke-dasharray', '12 6');
  }
}

export function highlightArea(areaId) {
  state.areas.forEach(a => {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${a.area_id}"]`);
    if (poly) {
      poly.classList.remove('merge-first-pulse', 'merge-candidate');
      setAreaPolyStyle(poly, a);
      poly.setAttribute('stroke-width', '8');
    }
  });
  if (areaId !== null) {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${areaId}"]`);
    if (poly) {
      poly.setAttribute('fill', 'rgba(16,185,129,0.28)');
      poly.setAttribute('stroke', '#10b981');
      poly.setAttribute('stroke-width', '12');
    }
  }
}

export function highlightAreaSplit(areaId) {
  // Bright cyan highlight for the area being split
  state.areas.forEach(a => {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${a.area_id}"]`);
    if (poly) setAreaPolyStyle(poly, a);
  });
  const poly = mapSvg.querySelector(`polygon[data-area-id="${areaId}"]`);
  if (poly) {
    poly.setAttribute('fill', 'rgba(6,182,212,0.3)');
    poly.setAttribute('stroke', '#06b6d4');
    poly.setAttribute('stroke-width', '14');
    poly.setAttribute('stroke-dasharray', '20 8');
  }
}

export function highlightMergeFirst(areaId) {
  // Orange pulsing for first merge selection, tint others
  state.areas.forEach(a => {
    const poly = mapSvg.querySelector(`polygon[data-area-id="${a.area_id}"]`);
    if (!poly) return;
    poly.classList.remove('merge-first-pulse', 'merge-candidate');
    setAreaPolyStyle(poly, a);
    if (a.area_id === areaId) {
      poly.setAttribute('fill', 'rgba(245,158,11,0.35)');
      poly.setAttribute('stroke', '#f59e0b');
      poly.setAttribute('stroke-width', '14');
      poly.classList.add('merge-first-pulse');
    } else if (a.area_state !== 'blocking') {
      poly.classList.add('merge-candidate');
    }
  });
}

export function getAreaName(area) {
  if (area.area_meta_data) {
    try {
      const m = JSON.parse(area.area_meta_data);
      if (m.name) return m.name;
    } catch {}
  }
  return area.area_state === 'clean' ? `Room ${area.area_id}` : `Segment ${area.area_id}`;
}

export function getAreaMeta(area) {
  if (!area?.area_meta_data) return {};
  try { return JSON.parse(area.area_meta_data); } catch { return {}; }
}

export function isSpotArea(area) {
  return area?.area_type === 'to_be_cleaned' && area?.area_state !== 'blocking';
}

export function isBlockingArea(area) {
  return area?.area_state === 'blocking' || area?.area_state === 'proposed_blocking';
}

function formatAreaStatsSummary(area) {
  const stats = area?._raw?.statistics;
  if (!stats) return '';

  const parts = [];
  if (typeof stats.area_size === 'number' && stats.area_size > 0) {
    parts.push(`${(stats.area_size / 1_000_000).toFixed(1)} m2`);
  }
  if (typeof stats.average_cleaning_time === 'number' && stats.average_cleaning_time > 0) {
    parts.push(`avg ${(stats.average_cleaning_time / 60000).toFixed(1)} min`);
  }
  return parts.join(' · ');
}

function svgAreaStatsLines(area) {
  const stats = area?._raw?.statistics;
  const lines = [`ID #${area.area_id}`];
  if (!stats) return lines;
  if (typeof stats.area_size === 'number' && stats.area_size > 0) {
    lines.push(`${(stats.area_size / 1_000_000).toFixed(1)} m2`);
  }
  if (typeof stats.average_cleaning_time === 'number' && stats.average_cleaning_time > 0) {
    lines.push(`avg ${(stats.average_cleaning_time / 60000).toFixed(1)} min`);
  }
  return lines;
}

export function renderAreaList(onAreaClick) {
  areaListEl.innerHTML = '';
  if (state.areas.length === 0) {
    areaListEl.innerHTML = '<span style="font-size:11px;color:var(--muted)">No areas found</span>';
    return;
  }
  state.areas.forEach(area => {
    const item = document.createElement('div');
    item.className = 'area-item' + (area.area_id === state.selectedAreaId ? ' selected' : '');
    item.dataset.areaId = area.area_id;

    const dot = document.createElement('div');
    dot.className = 'area-dot';
    const st = area.area_state;
    dot.style.background = st==='clean' ? '#3b82f6' : st==='blocking'||st==='proposed_blocking' ? '#ef4444' : '#475569';

    const textWrap = document.createElement('div');
    textWrap.className = 'area-text';

    const name = document.createElement('span');
    name.className = 'area-name';
    name.textContent = getAreaName(area);
    textWrap.appendChild(name);

    const statsText = formatAreaStatsSummary(area);
    if (statsText) {
      const statsEl = document.createElement('span');
      statsEl.className = 'area-stats-summary';
      statsEl.textContent = statsText;
      textWrap.appendChild(statsEl);
    }

    const idEl = document.createElement('span');
    idEl.className = 'area-id';
    idEl.textContent = `#${area.area_id}`;

    item.appendChild(dot); item.appendChild(textWrap); item.appendChild(idEl);
    item.addEventListener('click', () => {
      if (_onAreaClickCb) _onAreaClickCb(area.area_id);
      else if (onAreaClick) onAreaClick(area.area_id);
    });
    areaListEl.appendChild(item);
  });
}

export function _panToArea(area) {
  if (!area.points || area.points.length === 0) return;

  const cx = area.points.reduce((s, p) => s + p.x, 0) / area.points.length;
  const cy = area.points.reduce((s, p) => s + p.y, 0) / area.points.length;

  const sc  = robotToSVG(cx, cy);
  const vb  = mapSvg.viewBox.baseVal;

  mapSvg.setAttribute('viewBox',
    `${sc.x - vb.width / 2} ${sc.y - vb.height / 2} ${vb.width} ${vb.height}`);
}
