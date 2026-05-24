// ─────────────────────────────────────────────────────────────────────────────
// MODE MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { clearSplitOverlay } from './overlay.js';
import { hideInstruction } from './modal.js';

const mapSvg    = document.getElementById('map-svg');
const statusMode = document.getElementById('status-mode');

export function setMode(mode) {
  state.mode = mode;
  mapSvg.setAttribute('class', `mode-${mode}`);

  // Update toolbar
  ['select','pan','split','merge','block','spot','goto'].forEach(m => {
    const btn = document.getElementById('tool-' + m);
    if (btn) btn.classList.toggle('active', m === mode);
  });

  const modeNames = { select: 'Select', pan: 'Pan', split: 'Split', merge: 'Merge', block: 'No-Go Draw', spot: 'Spot Draw', goto: 'Go To' };
  statusMode.textContent = `Mode: ${modeNames[mode] || mode}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// FIT TO SCREEN
// ─────────────────────────────────────────────────────────────────────────────
export function fitToScreen() {
  if (!state.bbox) return;
  const pad = 300;
  const { minX, minY, maxX, maxY } = state.bbox;
  mapSvg.setAttribute('viewBox', `${minX-pad} ${minY-pad} ${(maxX-minX)+pad*2} ${(maxY-minY)+pad*2}`);
}
