// ─────────────────────────────────────────────────────────────────────────────
// API CLIENT
// ─────────────────────────────────────────────────────────────────────────────
import { USE_PROXY, ROBOT_PORT } from './config.js';
import * as config from './config.js';

// When running via proxy server, we can set the robot IP dynamically
export async function setProxyRobotIP(ip) {
  try {
    await fetch('/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ robot_ip: ip }),
    });
  } catch(e) { /* best-effort */ }
}

export function api(path) {
  let url;
  if (USE_PROXY) {
    url = path;                                    // relative → proxy handles it
  } else {
    url = `http://${config.robotIP}:${ROBOT_PORT}${path}`; // direct → robot IP
  }
  return fetch(url)
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
}

export async function apiText(path) {
  const url = USE_PROXY ? path : `http://${config.robotIP}:${ROBOT_PORT}${path}`;
  const resp = await fetch(url);
  const body = await resp.text();
  if (!resp.ok) {
    const err = new Error(`HTTP ${resp.status}${body ? ': ' + body.substring(0, 160) : ''}`);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  try { return JSON.parse(body); } catch { return body; }
}

const CMD_POLL_INTERVAL_MS = 5000;

export async function pollCmd(cmdId, timeoutMs = 15000, onTick = null) {
  const deadline = Date.now() + timeoutMs;
  const startMs  = Date.now();
  let first = true;
  while (Date.now() < deadline) {
    // Poll immediately, then sleep between polls, so a command that finishes
    // quickly is detected without waiting a full interval first.
    if (!first) await new Promise(r => setTimeout(r, CMD_POLL_INTERVAL_MS));
    first = false;
    const res  = await api('/get/command_result');
    const cmds = res.commands || [];
    const cmd  = cmds.find(c => String(c.cmd_id) === String(cmdId));

    if (onTick) onTick(Math.floor((Date.now() - startMs) / 1000));

    if (!cmd) continue;
    const st = cmd.state ?? cmd.status;
    if (['done', 'success', 'completed', 'complete', 'finished'].includes(st)) return true;
    if (cmd.error_code && cmd.error_code !== 0) throw new Error(`Command ${cmdId} error ${cmd.error_code}`);
    if (['aborted', 'failed', 'error', 'rejected'].includes(st)) throw new Error(`Command ${cmdId} ${st}`);
  }
  throw new Error('Command timeout');
}
