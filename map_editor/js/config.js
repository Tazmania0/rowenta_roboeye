// ─────────────────────────────────────────────────────────────────────────────
// CONFIGURATION — three modes:
//  1. DIRECT  — HA Webpage card (direct HTTP to robot — blocked by mixed content
//               when HA is HTTPS; only works if HA runs on plain HTTP)
//  2. PROXY   — Python server on localhost:8765 (standalone browser use)
//  3. INGRESS — HA add-on behind HA ingress (HTTPS → proxy → robot)
// ─────────────────────────────────────────────────────────────────────────────
export const ROBOT_PORT = 8080;

// Detect mode from hostname
const _host = window.location.hostname;
export const PROXY_MODE   = (_host === 'localhost' || _host === '127.0.0.1');
export const INGRESS_MODE = (!PROXY_MODE && window.location.pathname.includes('ingress'));
export const USE_PROXY    = PROXY_MODE || INGRESS_MODE;   // use relative URLs

export let robotIP = localStorage.getItem('rowenta_ip') || '';

export function setRobotIP(ip) {
  robotIP = ip;
  localStorage.setItem('rowenta_ip', ip);
}

export const ROOM_TYPE_OPTIONS = [
  { value: 'none', label: '— none —' },
  { value: 'living', label: 'Living room' },
  { value: 'kitchen', label: 'Kitchen' },
  { value: 'sleeping', label: 'Bedroom' },
  { value: 'corridor', label: 'Corridor / Hallway' },
];

export const EXPLORE_TIMEOUT    = 10 * 60 * 1000;
export const SAVE_MAP_TIMEOUT   = 60 * 1000;
export const SPLIT_TIMEOUT      = 30 * 1000;
export const MERGE_TIMEOUT      = 30 * 1000;
export const CMD_POLL_INTERVAL  = 5000;
