// ─────────────────────────────────────────────────────────────────────────────
// SPLIT / MERGE SVG OVERLAY HELPERS
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';

const mapSvg   = document.getElementById('map-svg');
const mapGroup = document.getElementById('map-group');
const SVG_NS   = 'http://www.w3.org/2000/svg';

export function getSplitGroup() {
  let g = mapSvg.querySelector('#split-overlay');
  if (!g) {
    g = document.createElementNS(SVG_NS, 'g');
    g.id = 'split-overlay';
    mapGroup.appendChild(g);
  }
  return g;
}

function ensurePreviewPatterns() {
  let defs = mapSvg.querySelector('#editor-preview-patterns');
  if (defs) return;
  defs = document.createElementNS(SVG_NS, 'defs');
  defs.id = 'editor-preview-patterns';
  defs.innerHTML = `
    <pattern id="editor-preview-hatch-red" patternUnits="userSpaceOnUse" width="80" height="80">
      <path d="M -20,20 L 20,-20 M 0,80 L 80,0 M 60,100 L 100,60"
        stroke="#ef4444" stroke-width="6" stroke-opacity="0.8"/>
      <path d="M -20,60 L 20,100 M 0,0 L 80,80 M 60,-20 L 100,20"
        stroke="#ef4444" stroke-width="6" stroke-opacity="0.8"/>
    </pattern>
    <pattern id="editor-preview-hatch-amber" patternUnits="userSpaceOnUse" width="80" height="80" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="80"
        stroke="#f59e0b" stroke-width="12" stroke-opacity="0.75"/>
    </pattern>`;
  mapSvg.insertBefore(defs, mapSvg.firstChild);
}

export function clearSplitOverlay() {
  const g = mapSvg.querySelector('#split-overlay');
  if (g) g.innerHTML = '';
}

export function setSplitDot(x, y) {
  const g = getSplitGroup();
  let dot = g.querySelector('.split-dot');
  if (!dot) {
    dot = document.createElementNS(SVG_NS, 'circle');
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
    line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('class', 'split-preview');
    g.appendChild(line);
  }
  line.setAttribute('x1', x1); line.setAttribute('y1', y1);
  line.setAttribute('x2', x2); line.setAttribute('y2', y2);
}

export function setSpotRect(x1, y1, x2, y2, kind='spot') {
  ensurePreviewPatterns();
  const g = getSplitGroup();
  let rect = g.querySelector('.spot-preview');
  if (!rect) {
    rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('class', 'split-preview spot-preview');
    rect.setAttribute('pointer-events', 'none');
    g.appendChild(rect);
  }
  rect.setAttribute('fill', kind === 'block' ? 'url(#editor-preview-hatch-red)' : 'url(#editor-preview-hatch-amber)');
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
