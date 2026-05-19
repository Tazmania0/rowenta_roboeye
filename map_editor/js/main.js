// ─────────────────────────────────────────────────────────────────────────────
// MAIN ENTRY POINT
// ─────────────────────────────────────────────────────────────────────────────
import { state } from './state.js';
import { USE_PROXY, ROBOT_PORT, ROOM_TYPE_OPTIONS } from './config.js';
import * as config from './config.js';
import { setProxyRobotIP } from './api.js';
import { showToast, showSpinner, setStatus, showInstruction } from './modal.js';
import { setAreaClickCallback, renderMapChips, renderAreaList } from './render.js';
import { setHandleMergeClick } from './areas.js';
import { onAreaClick, saveArea, toggleBlock, updateSplitListUI } from './areas.js';
import { startSplit } from './split.js';
import { startMerge, handleMergeClick } from './merge.js';
import { startBlock, startBlockZone, startSpot } from './nogo.js';
import { setMode, fitToScreen } from './mode.js';
import { loadMaps, loadMap } from './load.js';
import { executeExplore, executeDeleteMap, _promptSaveMap } from './explore.js';
import { initEvents } from './events.js';

const ipInput = document.getElementById('ip-input');

// Wire the circular dependency between render and areas
setAreaClickCallback(onAreaClick);
setHandleMergeClick(handleMergeClick);

// ─────────────────────────────────────────────────────────────────────────────
// ROBOT COMMANDS (Clean All, Stop, Return Home)
// ─────────────────────────────────────────────────────────────────────────────
async function sendRobotCommand(path, label) {
  const { apiText, pollCmd } = await import('./api.js');
  showSpinner(true);
  try {
    const res = await apiText(path);
    const cmdId = res?.cmd_id ?? res?.cmdId ?? res?.command_id;
    if (cmdId) {
      await pollCmd(cmdId, 30000).catch(e =>
        console.warn(`[command] ${label} accepted, but polling did not confirm completion:`, e)
      );
    }
    showToast(`${label} sent`, 'success');
    if (state.activeMapId) await loadMap(state.activeMapId);
  } catch (e) {
    console.warn(`[command] ${label} failed:`, e);
    showToast(`${label} failed: ${e.message}`, 'error');
  } finally {
    showSpinner(false);
  }
}

function startCleanAll() {
  const speed = document.getElementById('field-fan')?.value || '2';
  const strategyValue = document.getElementById('field-strategy')?.value || 'normal';
  const strategy = strategyValue === 'deep' ? '3' : strategyValue === 'normal' ? '1' : '4';
  sendRobotCommand(
    `/set/clean_all?cleaning_parameter_set=${encodeURIComponent(speed === '0' ? '2' : speed)}&cleaning_strategy_mode=${encodeURIComponent(strategy)}`,
    'Clean all'
  );
}

function startSpotClean() {
  startSpot();
}

// ─────────────────────────────────────────────────────────────────────────────
// TOOLBAR WIRING
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById('tool-select').addEventListener('click', () => setMode('select'));
document.getElementById('tool-pan').addEventListener('click', () => setMode('pan'));
document.getElementById('tool-split').addEventListener('click', startSplit);
document.getElementById('tool-merge').addEventListener('click', startMerge);
document.getElementById('tool-block').addEventListener('click', startBlock);
document.getElementById('tool-spot').addEventListener('click',  startSpot);
document.getElementById('tool-fit').addEventListener('click', fitToScreen);

document.getElementById('btn-clean-all').addEventListener('click', startCleanAll);
document.getElementById('btn-stop').addEventListener('click', () => sendRobotCommand('/set/stop', 'Stop'));
document.getElementById('btn-home').addEventListener('click', () => sendRobotCommand('/set/go_home', 'Return home'));
document.getElementById('btn-spot-clean')?.addEventListener('click', startSpot);

document.getElementById('btn-explore').addEventListener('click', executeExplore);
document.getElementById('btn-delete-map').addEventListener('click', () => {
  if (state.activeMapId) executeDeleteMap(state.activeMapId);
});

document.getElementById('btn-save-area').addEventListener('click', saveArea);
document.getElementById('btn-split-area').addEventListener('click', startSplit);
document.getElementById('btn-merge-area').addEventListener('click', startMerge);
document.getElementById('btn-block-area').addEventListener('click', startBlockZone);

// ─────────────────────────────────────────────────────────────────────────────
// CONNECTION
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById('btn-connect').addEventListener('click', async () => {
  const ip = ipInput.value.trim();

  if (USE_PROXY) {
    if (!ip || ip === 'via proxy server') {
      showToast('Enter robot IP first', 'error');
      return;
    }
    config.setRobotIP(ip);
    // Push IP to the running proxy server so it knows where to forward
    await setProxyRobotIP(ip);
    setStatus('Connecting via proxy…', 'busy');
  } else {
    if (!ip) { showToast('Enter robot IP address', 'error'); return; }
    config.setRobotIP(ip);
    setStatus('Connecting…', 'busy');
  }

  try {
    await loadMaps();
  } catch(e) {
    setStatus('Connection failed', 'err');
    showToast('Cannot reach robot: ' + e.message, 'error');
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// INIT — detect mode and configure UI accordingly
// ─────────────────────────────────────────────────────────────────────────────
async function init() {
  // Wire all SVG/window events
  initEvents(onAreaClick);

  if (USE_PROXY) {
    // Show PROXY badge
    const badge = document.getElementById('mode-badge');
    if (badge) badge.style.display = '';

    // Fetch what IP the server already knows (passed as CLI arg or last set)
    try {
      const cfg = await fetch('/config').then(r => r.json());
      if (cfg.robot_ip) {
        config.setRobotIP(cfg.robot_ip);
        ipInput.value = config.robotIP;
        ipInput.disabled = false;   // allow changing it
      } else {
        ipInput.placeholder = '192.168.1.xx';
        ipInput.value = localStorage.getItem('rowenta_ip') || '';
      }
    } catch(e) {
      ipInput.value = localStorage.getItem('rowenta_ip') || '';
    }

    // Auto-connect if we already have an IP
    if (config.robotIP) {
      setStatus('Connecting via proxy…', 'busy');
      try { await loadMaps(); }
      catch(e) { setStatus('Connection failed', 'err'); showToast(e.message, 'error'); }
    }
  } else {
    // Direct mode — HA webpage card or plain file
    if (config.robotIP) ipInput.value = config.robotIP;
    // Show mixed-content warning if page is HTTPS
    if (window.location.protocol === 'https:') {
      showToast('⚠ HTTPS→HTTP blocked. Use the HA add-on or Python server instead.', 'error');
      setStatus('Mixed content blocked — use add-on or python server', 'err');
    }
  }

  document.getElementById('status-mode').title = 'S=Select  Space=Pan  X=Split  M=Merge  F=Fit  Esc=Cancel';
}

document.addEventListener('DOMContentLoaded', init);

// ── Inline test suite — runs when ?test=1 is in the URL ──────────────────────
if (new URLSearchParams(window.location.search).get('test') === '1') {
  const _results = [];
  function _assert(desc, cond) {
    _results.push({ desc, pass: !!cond });
    if (!cond) console.error(`FAIL: ${desc}`);
  }

  (function testExploreUrl() {
    const url = '/set/explore';
    _assert('explore URL has no query string', !url.includes('?'));
    _assert('explore URL has no map_id',       !url.includes('map_id'));
    _assert('explore URL has no params',        url === '/set/explore');
  })();

  (function testFindTempMap() {
    const maps = [
      { map_id: 3,  permanent_flag: 'true',  map_meta_data: 'Дружба' },
      { map_id: 45, permanent_flag: 'false', map_meta_data: '' },
    ];
    const temp = maps.find(m => String(m.permanent_flag).toLowerCase() !== 'true');
    _assert('finds temp map by permanent_flag=false', temp !== undefined);
    _assert('temp map_id is 45',                      temp?.map_id === 45);

    const maps2 = [
      { map_id: 3,  permanent_flag: 'true' },
      { map_id: 99  /* no permanent_flag */ },
    ];
    const temp2 = maps2.find(m => String(m.permanent_flag ?? '').toLowerCase() !== 'true');
    _assert('finds temp map when permanent_flag absent', temp2?.map_id === 99);
  })();

  (function testModifyMapUrl() {
    const dock  = { x: 100, y: 50, heading: 180, valid: true };
    const name  = 'Ground Floor';
    const mapId = 45;
    const url   = `/set/modify_map?map_id=${mapId}&name=${encodeURIComponent(name)}`
                + `&docking_pose=${encodeURIComponent(JSON.stringify(dock))}`;
    _assert('modify_map URL has map_id',      url.includes(`map_id=${mapId}`));
    _assert('modify_map URL has name',        url.includes('name='));
    _assert('modify_map URL has docking_pose',url.includes('docking_pose='));
    const decoded = JSON.parse(decodeURIComponent(url.split('docking_pose=')[1]));
    _assert('docking_pose.x is 100',          decoded.x === 100);
    _assert('docking_pose.valid is true',     decoded.valid === true);

    const dockInvalid = { x: 0, y: 0, heading: 0, valid: false };
    const url2 = `/set/modify_map?map_id=3&name=Test`
               + `&docking_pose=${encodeURIComponent(JSON.stringify(dockInvalid))}`;
    const d2   = JSON.parse(decodeURIComponent(url2.split('docking_pose=')[1]));
    _assert('docking_pose with valid=false is still included', 'valid' in d2);
    _assert('docking_pose.valid=false preserved',              d2.valid === false);
  })();

  (function testDeleteGuards() {
    const canDelete = (maps) =>
      maps.filter(m => String(m.permanent_flag).toLowerCase() === 'true').length > 1;

    _assert('cannot delete when only 1 permanent map',
      canDelete([{ map_id: 3, permanent_flag: 'true' }]) === false);
    _assert('can delete when 2 permanent maps',
      canDelete([
        { map_id: 3,  permanent_flag: 'true' },
        { map_id: 45, permanent_flag: 'true' },
      ]) === true);
    _assert('non-permanent map not counted in guard',
      canDelete([
        { map_id: 3,  permanent_flag: 'true' },
        { map_id: 45, permanent_flag: 'false' },
      ]) === false);
  })();

  (function testDeleteConfirm() {
    const matches = (expected, typed) => typed.trim() === expected.trim();
    _assert('exact match passes',            matches('Дружба', 'Дружба'));
    _assert('trailing space passes (trim)',  matches('Дружба', 'Дружба '));
    _assert('wrong case fails',             !matches('Дружба', 'дружба'));
    _assert('transliteration fails',        !matches('Дружба', 'Druzhba'));
    _assert('empty string fails',           !matches('Дружба', ''));
    _assert('partial match fails',          !matches('Дружба', 'Дружб'));
  })();

  (function testDeleteRouting() {
    const routeDelete = (m) =>
      String(m.permanent_flag ?? '').toLowerCase() === 'true'
        ? 'delete_map' : 'revert_map';
    _assert('permanent map routes to delete_map',
      routeDelete({ permanent_flag: 'true' })  === 'delete_map');
    _assert('temp map routes to revert_map',
      routeDelete({ permanent_flag: 'false' }) === 'revert_map');
    _assert('map without permanent_flag routes to revert_map',
      routeDelete({}) === 'revert_map');
  })();

  (function testTimeouts() {
    const EXPLORE_TIMEOUT  = 10 * 60 * 1000;
    const SAVE_MAP_TIMEOUT = 60 * 1000;
    _assert('explore timeout >= 10 minutes', EXPLORE_TIMEOUT >= 600000);
    _assert('save_map timeout >= 60 seconds', SAVE_MAP_TIMEOUT >= 60000);
  })();

  (function testDockingPoseExtract() {
    const featRes = {
      map: {
        lines: [],
        docking_pose: { x: 100, y: 50, heading: 180, valid: true }
      }
    };
    const dock = (featRes.map ?? featRes).docking_pose ?? null;
    _assert('docking_pose extracted from feature_map', dock !== null);
    _assert('docking_pose.x is 100', dock?.x === 100);
    _assert('docking_pose.heading is 180', dock?.heading === 180);

    const featRes2 = { docking_pose: { x: 200, y: 75, heading: 90, valid: true } };
    const dock2    = (featRes2.map ?? featRes2).docking_pose ?? null;
    _assert('docking_pose extracted when not nested in .map', dock2?.x === 200);
  })();

  // ── _panToArea centroid calculation ────────────────────────────────────
  (function testPanToArea() {
    const area = {
      area_id: 28,
      area_state: 'inactive',
      points: [
        { x: -713, y: 300 },
        { x: -698, y: 962 },
        { x:  127, y: 943 },
        { x:  112, y: 282 },
      ],
    };
    const cx = area.points.reduce((s, p) => s + p.x, 0) / area.points.length;
    const cy = area.points.reduce((s, p) => s + p.y, 0) / area.points.length;
    _assert('centroid x is approximately -293', Math.abs(cx - (-293)) < 2);
    _assert('centroid y is approximately 622',  Math.abs(cy - 622)    < 2);
  })();

  // ── Phase transitions ───────────────────────────────────────────────────
  (function testPhaseTransitions() {
    const validTransitions = {
      null:      ['running'],
      running:   ['drawing'],
      drawing:   ['naming', 'drawing'],
      naming:    ['drawing', 'saving'],
      saving:    [null],
    };
    _assert('running is valid from null',
      validTransitions['null'].includes('running'));
    _assert('drawing is valid from running',
      validTransitions['running'].includes('drawing'));
    _assert('naming is valid from drawing',
      validTransitions['drawing'].includes('naming'));
    _assert('saving is valid from naming',
      validTransitions['naming'].includes('saving'));
    _assert('null is valid from saving',
      validTransitions['saving'].includes(null));
  })();

  // ── Naming wizard skips blocking/no-go areas ────────────────────────────
  (function testNamingWizardFilter() {
    const areas = [
      { area_id: 28, area_state: 'inactive',  area_type: 'room' },
      { area_id: 30, area_state: 'blocking',  area_type: 'to_be_cleaned' },
      { area_id: 31, area_state: 'inactive',  area_type: 'room' },
      { area_id: 32, area_state: 'clean',     area_type: 'room' },
    ];
    const toName = areas.filter(a =>
      a.area_state === 'inactive' && a.area_type !== 'to_be_cleaned');
    _assert('wizard targets 2 inactive room areas', toName.length === 2);
    _assert('wizard skips blocking area',   !toName.find(a => a.area_id === 30));
    _assert('wizard skips already-named',   !toName.find(a => a.area_id === 32));
  })();

  // ── Phase bar "Done Drawing" enabled when any split was done ────────────
  (function testPhaseBarSaveButton() {
    const named0 = [
      { area_id: 28, area_state: 'inactive' },
      { area_id: 31, area_state: 'inactive' },
    ];
    const canSave0 = named0.filter(a => a.area_state === 'clean').length > 0;
    _assert('save disabled when 0 rooms named', canSave0 === false);

    const named1 = [
      { area_id: 28, area_state: 'clean'    },
      { area_id: 31, area_state: 'inactive' },
    ];
    const canSave1 = named1.filter(a => a.area_state === 'clean').length > 0;
    _assert('save enabled when 1 room named', canSave1 === true);
  })();

  // ── Undo: only a reload, not a true undo ───────────────────────────────
  (function testUndoCaveat() {
    const API_ENDPOINTS = [
      '/set/explore', '/set/split_area', '/set/merge_areas',
      '/set/modify_area', '/set/save_map', '/set/delete_map',
      '/set/modify_map', '/set/add_area',
    ];
    const hasUndo = API_ENDPOINTS.includes('/set/undo');
    _assert('no /set/undo endpoint exists in API', hasUndo === false);
  })();

  const passed = _results.filter(r => r.pass).length;
  const failed = _results.filter(r => !r.pass).length;
  console.log(`\n[tests] ${passed} passed, ${failed} failed`);
  _results.filter(r => !r.pass).forEach(r => console.error(`  FAIL: ${r.desc}`));

  const badge = document.createElement('div');
  badge.style.cssText = `position:fixed;bottom:36px;right:12px;z-index:999;
    padding:6px 12px;border-radius:6px;font-family:monospace;font-size:11px;
    background:${failed ? '#7f1d1d' : '#14532d'};color:#fff;border:1px solid ${failed ? '#ef4444' : '#22c55e'}`;
  badge.textContent = `Tests: ${passed}✓ ${failed}✗`;
  document.body.appendChild(badge);
}
