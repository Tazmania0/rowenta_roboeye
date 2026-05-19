// ─────────────────────────────────────────────────────────────────────────────
// SPLIT / MERGE SVG OVERLAY HELPERS
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';

const mapSvg   = document.getElementById('map-svg');
const mapGroup = document.getElementById('map-group');

export function getSplitGroup() {
  let g = mapSvg.querySelector('#split-overlay');
  if (!g) {
    g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.id = 'split-overlay';
    mapGroup.appendChild(g);
  }
  return g;
}

export function clearSplitOverlay() {
  const g = mapSvg.querySelector('#split-overlay');
  if (g) g.innerHTML = '';
}

export function setSplitDot(x, y) {
  const g = getSplitGroup();
  let dot = g.querySelector('.split-dot');
  if (!dot) {
    dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('class', 'split-dot');
    g.appendChild(dot);
  }
  const vb  = mapSvg.viewBox.baseVal;
  const r   = Math.max(15, (vb.width || 2000) * 0.009);
  dot.setAttribute('r', r);
  dot.setAttribute('cx', x); dot.setAttribute('cy', y);
}

export function setSplitLine(x1, y1, x2, y2) {
  const g = getSplitGroup();
  let line = g.querySelector('.split-preview');
  if (!line) {
    line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('class', 'split-preview');
    g.appendChild(line);
  }
  line.setAttribute('x1', x1); line.setAttribute('y1', y1);
  line.setAttribute('x2', x2); line.setAttribute('y2', y2);
}

export function setSpotRect(x1, y1, x2, y2, kind='spot') {
  const g = getSplitGroup();
  let rect = g.querySelector('.spot-preview');
  if (!rect) {
    rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('class', 'split-preview spot-preview');
    rect.setAttribute('pointer-events', 'none');
    g.appendChild(rect);
  }
  rect.setAttribute('fill', kind === 'block' ? 'rgba(239,68,68,0.16)' : 'rgba(245,158,11,0.14)');
  rect.setAttribute('stroke', kind === 'block' ? '#ef4444' : '#f59e0b');
  rect.setAttribute('stroke-width', '8');
  rect.setAttribute('stroke-dasharray', kind === 'block' ? '18 10' : 'none');
  rect.setAttribute('x', Math.min(x1, x2));
  rect.setAttribute('y', Math.min(y1, y2));
  rect.setAttribute('width', Math.abs(x2 - x1));
  rect.setAttribute('height', Math.abs(y2 - y1));
}

// Legacy alias — now handled by clearSplitOverlay()
export function hideSplitPreview() { clearSplitOverlay(); }
