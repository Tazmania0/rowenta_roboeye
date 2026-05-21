// ─────────────────────────────────────────────────────────────────────────────
// ROBOT STATUS POLLING, ROBOT DOT, GOTO, AUTO NO-GO ZONES
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { api, pollCmd } from './api.js';
import { showModal, showToast, showSpinner, showInstruction } from './modal.js';
import { setMode } from './mode.js';
import { svgToRobot, robotToSVG } from './coords.js';
import { highlightArea, renderMap } from './render.js';
import { updateCleanSelectionButton } from './areas.js';
import { loadMap } from './load.js';
import { USE_PROXY, ROBOT_PORT } from './config.js';
import * as config from './config.js';

const mapSvg   = document.getElementById('map-svg');
const mapGroup = document.getElementById('map-group');

// ─── Status polling ───────────────────────────────────────────────────────────
export function startStatusPolling() {
  if (state.statusTimer) clearInterval(state.statusTimer);
  const pollStatus = async () => {
    try {
      const res = await api('/get/status');
      state.robotMode    = res.mode;
      state.robotCharging = res.charging;
      state.robotBatteryLevel = _extractBatteryLevel(res);
      await _updateCleaningGrid();
      _updateRobotStatusUI();
    } catch {}
  };
  pollStatus();
  state.statusTimer = setInterval(pollStatus, 5000);
}

function _extractBatteryLevel(status) {
  const raw = status?.battery_level ?? status?.battery ?? status?.batteryLevel ?? status?.charge_level;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : null;
}

async function _updateCleaningGrid() {
  if (!state.activeMapId) return;
  const shouldShowGrid = state.robotMode === 'cleaning';
  if (!shouldShowGrid) {
    if (state.cleaningGrid) {
      state.cleaningGrid = null;
      renderMap(state._lastWalls, state._lastDock);
      if (state.selectedAreaId !== null) highlightArea(state.selectedAreaId);
    }
    return;
  }

  try {
    const grid = await api('/get/cleaning_grid_map');
    state.cleaningGrid = grid?.size_x > 0 ? grid : null;
    renderMap(state._lastWalls, state._lastDock);
    if (state.selectedAreaId !== null) highlightArea(state.selectedAreaId);
  } catch {}
}

export function _updateRobotStatusUI() {
  const busy = state.robotMode === 'cleaning' || state.robotMode === 'go_home';
  ['btn-explore'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = busy;
    btn.title = busy ? `Robot is ${state.robotMode} — wait for it to finish` : '';
  });
  const btnClean = document.getElementById('btn-clean-area');
  if (btnClean) {
    btnClean.title = busy ? `Robot is ${state.robotMode} - wait for it to finish` : 'Start cleaning selected area(s)';
    updateCleanSelectionButton();
  }
  const btnGH = document.getElementById('btn-go-home');
  if (btnGH) btnGH.disabled = state.robotMode === 'go_home';
  _updateBatteryUI();
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  if (dot && text && state.connected) {
    if (state.robotMode === 'cleaning') {
      dot.className = 'status-dot busy'; text.textContent = 'Cleaning…';
    } else if (state.robotMode === 'go_home') {
      dot.className = 'status-dot busy'; text.textContent = 'Returning to dock…';
    } else {
      dot.className = 'status-dot ok'; text.textContent = `Connected — ${state.maps.length} map(s)`;
    }
  }
}

function _updateBatteryUI() {
  const chip = document.getElementById('battery-chip');
  const fill = document.getElementById('battery-fill');
  const text = document.getElementById('battery-text');
  if (!chip || !fill || !text) return;

  const level = state.robotBatteryLevel;
  if (level === null) {
    chip.className = 'battery-chip';
    fill.style.width = '0%';
    text.textContent = '--%';
    return;
  }

  chip.className = 'battery-chip ' + (level <= 20 ? 'low' : level <= 40 ? 'warn' : 'ok');
  fill.style.width = `${level}%`;
  const charging = String(state.robotCharging || '').toLowerCase().includes('charging');
  text.textContent = `${level}%${charging ? ' charging' : ''}`;
}

// ─── Robot position dot ───────────────────────────────────────────────────────
export function updateRobotDot(pose) {
  if (!mapGroup || !state.bbox) return;
  let dot = document.getElementById('robot-dot');
  if (!dot) {
    dot = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    dot.id = 'robot-dot';
    mapGroup.appendChild(dot);
  }
  dot.innerHTML = '';
  if (!pose || !pose.valid || String(pose.map_id) !== String(state.activeMapId)) {
    dot.style.display = 'none'; return;
  }
  dot.style.display = '';
  const sc = robotToSVG(pose.x1, pose.y1);
  const vb = mapSvg.viewBox.baseVal;
  const r  = Math.max(30, (vb.width || 2000) * 0.018);
  const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  ring.setAttribute('cx', sc.x); ring.setAttribute('cy', sc.y);
  ring.setAttribute('r', r * 1.5); ring.setAttribute('fill', 'rgba(59,130,246,0.15)');
  ring.setAttribute('stroke', '#3b82f6'); ring.setAttribute('stroke-width', '4');
  const center = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  center.setAttribute('cx', sc.x); center.setAttribute('cy', sc.y);
  center.setAttribute('r', r); center.setAttribute('fill', '#3b82f6');
  const rad = ((pose.heading - 90) * Math.PI) / 180;
  const al  = r * 2.2;
  const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  arrow.setAttribute('x1', sc.x); arrow.setAttribute('y1', sc.y);
  arrow.setAttribute('x2', sc.x + Math.cos(rad) * al);
  arrow.setAttribute('y2', sc.y + Math.sin(rad) * al);
  arrow.setAttribute('stroke', '#93c5fd'); arrow.setAttribute('stroke-width', '5');
  arrow.setAttribute('stroke-linecap', 'round');
  dot.appendChild(ring); dot.appendChild(center); dot.appendChild(arrow);
}

export function startRobPosePolling() {
  if (state.robPoseTimer) clearInterval(state.robPoseTimer);
  state.robPoseTimer = setInterval(async () => {
    if (document.visibilityState !== 'visible') return;
    try {
      const res = await api('/get/rob_pose');
      state.robPose = res;
      updateRobotDot(res);
    } catch {}
  }, 2000);
}

// ─── GoTo point ───────────────────────────────────────────────────────────────
export function startGoTo() {
  setMode('goto');
  showInstruction('GOTO', 'Click on the floor to send robot there', 'amber');
}

export async function executeGoTo(svgX, svgY) {
  const p = svgToRobot(svgX, svgY);
  const candidates = [
    `/set/target_point?map_id=${state.activeMapId}&x=${p.x}&y=${p.y}`,
    `/set/target_point?map_id=${state.activeMapId}&point=${encodeURIComponent(JSON.stringify({ x: parseFloat(p.x.toFixed(1)), y: parseFloat(p.y.toFixed(1)) }))}`,
    `/set/target_point?map_id=${state.activeMapId}&x=${p.x.toFixed(1)}&y=${p.y.toFixed(1)}`,
  ];
  for (const path of candidates) {
    const url  = USE_PROXY ? path : `http://${config.robotIP}:${ROBOT_PORT}${path}`;
    const resp = await fetch(url); const body = await resp.text();
    console.log(`[goto] ${path.split('?')[1].substring(0, 40)} → ${resp.status} ${body}`);
    if (resp.ok) { showToast(`Going to (${p.x}, ${p.y})`, 'success'); setMode('select'); return; }
  }
  showToast('GoTo: all formats failed — check console', 'error'); setMode('select');
}

// ─── Auto no-go zones ─────────────────────────────────────────────────────────
export async function executeProposedNoGo() {
  const mapId = state.activeMapId; if (!mapId) return;
  try {
    await showModal({ title: 'Auto No-Go Zones',
      desc: 'Robot will analyse the map and suggest zones to avoid. You review each.',
      confirmLabel: 'Propose Zones' });
  } catch { return; }
  showSpinner(true);
  try {
    const res   = await api(`/set/propose_nogo_areas?map_id=${mapId}`);
    const cmdId = res.cmd_id ?? res.cmdId;
    if (cmdId) { showToast('Analysing map…', 'info'); await pollCmd(cmdId, 30000); }
    await loadMap(mapId);
    const proposed = state.areas.filter(a => a.area_state === 'proposed_blocking');
    if (proposed.length === 0) { showToast('No no-go zones suggested', 'info'); showSpinner(false); return; }
    const confirmed = [], declined = [];
    for (const area of proposed) {
      highlightArea(area.area_id);
      try {
        await showModal({ title: 'No-go zone suggestion',
          desc: `Accept area #${area.area_id} as permanent no-go zone?`,
          confirmLabel: 'Accept' });
        confirmed.push(area.area_id);
      } catch { declined.push(area.area_id); }
    }
    highlightArea(null);
    if (confirmed.length > 0 || declined.length > 0) {
      await api(`/set/confirm_nogo_areas?map_id=${mapId}`
              + `&confirmed_ids=${encodeURIComponent(JSON.stringify(confirmed))}`
              + `&declined_ids=${encodeURIComponent(JSON.stringify(declined))}`);
    }
    await loadMap(mapId);
    showToast(`${confirmed.length} accepted, ${declined.length} declined`, 'success');
  } catch (e) { showToast('Auto no-go failed: ' + e.message.substring(0, 80), 'error'); }
  finally { showSpinner(false); }
}
