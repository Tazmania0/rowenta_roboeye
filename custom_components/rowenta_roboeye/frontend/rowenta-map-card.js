// Rowenta Xplorer 120 — Live Map Card  v2.0.0
// Renders rooms, walls, dock, robot and live outline from the new
// _build_live_map_payload() schema (map_id, rooms, outline, walls,
// dock, robot, live_outline, bounds, scale).

const VERSION = "2.0.0";

// ── Geometry helpers ────────────────────────────────────────────────────

/** Compute polygon centroid as mean of vertices. */
function centroid(pts) {
  if (!pts || !pts.length) return { x: 0, y: 0 };
  const sx = pts.reduce((a, p) => a + p[0], 0);
  const sy = pts.reduce((a, p) => a + p[1], 0);
  return { x: sx / pts.length, y: sy / pts.length };
}

/** Shoelace formula — polygon area in the same units² as input coords. */
function polyArea(pts) {
  if (!pts || pts.length < 3) return 0;
  let s = 0;
  for (let i = 0, n = pts.length; i < n; i++) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % n];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}


class RowentaMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass    = null;
    this._config  = {};
    this._frozen  = false;
    this._lastAttrs = null;
  }

  setConfig(config) {
    if (!config) throw new Error("No configuration provided");
    this._config = {
      entity:           "sensor.rowenta_xplorer_120_live_map",
      rotate:           0,
      show_dock:        true,
      show_walls:       true,
      show_room_labels: true,
      show_room_areas:  true,
      room_opacity:     0.25,
      title:            "Live Map",
      ...config,
    };
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._frozen) return;
    try { this._render(); }
    catch (err) {
      console.error("rowenta-map-card:", err);
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color)">
        <b>rowenta-map-card error:</b> ${err.message}</div></ha-card>`;
    }
  }

  _render() {
    if (!this._hass || !this._config.entity) return;
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px;color:var(--warning-color)">
        Entity <b>${this._config.entity}</b> not found —
        enable <b>sensor.rowenta_xplorer_120_live_map</b> in the entity registry.
      </div></ha-card>`;
      return;
    }
    const attrs = state.attributes || {};
    this._lastAttrs = attrs;
    this._renderFull(state.state || "idle", attrs);
  }

  _renderFull(mapState, attrs) {
    const cfg = this._config;

    const rooms      = attrs.rooms       || [];
    const outline    = attrs.outline     || [];
    const walls      = attrs.walls       || [];
    const dock       = attrs.dock        || null;
    const robot      = attrs.robot       || null;
    const liveOut    = attrs.live_outline || [];
    const bounds     = attrs.bounds      || null;
    const isActive   = attrs.is_active   || false;

    const stateColor = mapState === "cleaning"  ? "#4CAF50"
                     : mapState === "returning" ? "#FF9800"
                     : mapState === "mapping"   ? "#2196F3"
                     : "var(--secondary-text-color)";

    const svgHtml = this._buildSvg(
      rooms, outline, walls, dock, robot, liveOut, bounds, isActive, cfg
    );

    const frozen = this._frozen;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 0; overflow: hidden; }
        * { box-sizing: border-box; }
        .hdr {
          display: flex; align-items: center; padding: 10px 14px;
          border-bottom: 1px solid var(--divider-color); gap: 8px;
        }
        .title { font-size: 15px; font-weight: 500; flex: 1; }
        .badge {
          padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;
          background: ${stateColor}22; color: ${stateColor};
          border: 1px solid ${stateColor}; white-space: nowrap;
        }
        .fbtn {
          padding: 4px 10px; border-radius: 6px; font-size: 12px; cursor: pointer;
          border: 1px solid var(--divider-color); white-space: nowrap;
          background: ${frozen ? "#ff572222" : "var(--secondary-background-color)"};
          color: ${frozen ? "#ff5722" : "var(--primary-text-color)"};
          font-weight: ${frozen ? "bold" : "normal"};
        }
        .frozen-bar {
          background: #ff572218; border-bottom: 2px solid #ff5722;
          padding: 3px 14px; font-size: 11px; color: #ff5722;
        }
        .svg-wrap { background: #0d1a2a; }
        .svg-wrap svg { display: block; width: 100%; height: auto; }
        .empty {
          display: flex; align-items: center; justify-content: center;
          min-height: 200px; flex-direction: column; gap: 8px; padding: 20px;
          text-align: center; font-size: 13px;
          color: var(--secondary-text-color); background: #0d1a2a;
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.45; }
        }
        .robot-active { animation: pulse 1.5s infinite; }
      </style>
      <ha-card>
        <div class="hdr">
          <span class="title">${cfg.title}</span>
          <span class="badge">${mapState.toUpperCase()}</span>
          <button class="fbtn" id="fbtn">${frozen ? "▶ Live" : "⏸ Freeze"}</button>
        </div>
        ${frozen ? `<div class="frozen-bar">⏸ Frozen — click Live to resume</div>` : ""}
        <div class="svg-wrap">${svgHtml}</div>
      </ha-card>`;

    this.shadowRoot.getElementById("fbtn")?.addEventListener("click", () => {
      if (this._frozen) {
        this._frozen = false;
        this._render();
      } else {
        this._frozen = true;
        this._renderFull(
          this._hass?.states[this._config.entity]?.state || "idle",
          this._lastAttrs || {}
        );
      }
    });
  }

  // ── SVG renderer ──────────────────────────────────────────────────────

  _buildSvg(rooms, outline, walls, dock, robot, liveOut, bounds, isActive, cfg) {
    if (!bounds) {
      return `<div class="empty">
        <div style="font-size:40px;opacity:.25">🏠</div>
        <div>No map data yet.<br>Start a cleaning run to populate the map.</div>
      </div>`;
    }

    const PAD  = 100;
    const minX = bounds.min_x - PAD;
    const maxX = bounds.max_x + PAD;
    const minY = bounds.min_y - PAD;
    const maxY = bounds.max_y + PAD;
    const W    = maxX - minX || 1;
    const H    = maxY - minY || 1;

    // Flip Y: SVG Y increases downward, world Y increases upward
    // svgY = (minY + maxY) - worldY
    const flipY = (y) => minY + maxY - y;

    const pts2str = (arr) =>
      arr.map((p) => `${p[0] - minX},${flipY(p[1]) - minY}`).join(" ");

    // ── Layer 1: floor fill (seen_polygon outline) ──────────────────
    let floorFill = "";
    if (outline.length > 2) {
      floorFill = `<polygon points="${pts2str(outline)}"
        fill="#0d1a2a" stroke="#1a3a5c" stroke-width="3"/>`;
    } else {
      // Fallback: draw a background rect covering viewBox
      floorFill = `<rect x="0" y="0" width="${W}" height="${H}" fill="#0d1a2a"/>`;
    }

    // ── Layer 2: room fills ─────────────────────────────────────────
    let roomFills = "";
    const roomOpacity = cfg.room_opacity ?? 0.25;
    for (const room of rooms) {
      const p = room.polygon || [];
      if (p.length < 3) continue;
      roomFills += `<polygon points="${pts2str(p)}"
        fill="${room.color}" fill-opacity="${roomOpacity}"
        stroke="${room.color}" stroke-width="2" stroke-linejoin="round"
        data-room="${this._esc(room.name)}"
        style="cursor:pointer"/>`;
    }

    // ── Layer 3: walls ──────────────────────────────────────────────
    let wallLines = "";
    if (cfg.show_walls) {
      for (const w of walls) {
        const [x1, y1, x2, y2] = w;
        wallLines += `<line
          x1="${x1 - minX}" y1="${flipY(y1) - minY}"
          x2="${x2 - minX}" y2="${flipY(y2) - minY}"
          stroke="white" stroke-width="2" stroke-opacity="0.55"
          stroke-linecap="round"/>`;
      }
    }

    // ── Layer 4: live outline (during cleaning) ─────────────────────
    let liveOutline = "";
    if (liveOut.length > 2) {
      liveOutline = `<polygon points="${pts2str(liveOut)}"
        fill="#00aaff" fill-opacity="0.08"
        stroke="#00aaff" stroke-width="1.5" stroke-dasharray="6,3"/>`;
    }

    // ── Layer 5: dock icon ──────────────────────────────────────────
    let dockIcon = "";
    if (cfg.show_dock && dock) {
      const dx = dock.x - minX;
      const dy = flipY(dock.y) - minY;
      dockIcon = `<g transform="translate(${dx},${dy})">
        <polygon points="0,-14 9,9 -9,9"
          fill="#FFD700" stroke="white" stroke-width="1.5"
          transform="rotate(${dock.heading_deg ?? 0})"/>
      </g>`;
    }

    // ── Layer 6: robot icon ─────────────────────────────────────────
    let robotIcon = "";
    if (robot != null) {
      const rx = robot.x - minX;
      const ry = flipY(robot.y) - minY;
      const hRad = ((robot.heading_deg ?? 0) * Math.PI) / 180;
      const arrowLen = 22;
      // In SVG (Y-down), the direction arrow:
      //   forward = negative Y in SVG for "up" heading, but since we've flipped,
      //   we use: ax = rx + sin(hRad)*arrowLen, ay = ry - cos(hRad)*arrowLen
      const ax = (rx + Math.sin(hRad) * arrowLen).toFixed(1);
      const ay = (ry - Math.cos(hRad) * arrowLen).toFixed(1);
      const activeClass = isActive ? ' class="robot-active"' : "";
      robotIcon = `<g${activeClass}>
        <circle cx="${rx}" cy="${ry}" r="14"
          fill="#00d4ff" stroke="white" stroke-width="2.5"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}"
          stroke="white" stroke-width="3.5" stroke-linecap="round"/>
      </g>`;
    }

    // ── Layer 7: room labels ────────────────────────────────────────
    let labels = "";
    if (cfg.show_room_labels) {
      for (const room of rooms) {
        const p = room.polygon || [];
        if (p.length < 3) continue;
        const c = centroid(p);
        const cx = c.x - minX;
        const cy = flipY(c.y) - minY;
        let areaText = "";
        if (cfg.show_room_areas) {
          const areaCm2 = polyArea(p);
          const areaM2 = (areaCm2 / 10000).toFixed(1);
          areaText = `<text x="${cx}" y="${cy + 36}"
            text-anchor="middle" fill="${room.color}" font-size="20" opacity="0.75"
            font-family="sans-serif">${areaM2} m²</text>`;
        }
        labels += `<text x="${cx}" y="${cy}"
          text-anchor="middle" dominant-baseline="middle"
          fill="${room.color}" font-size="28" font-weight="bold"
          font-family="sans-serif"
          style="text-shadow:0 1px 3px rgba(0,0,0,.8)">${this._esc(room.name)}</text>
          ${areaText}`;
      }
    }

    return `<svg viewBox="0 0 ${W} ${H}"
      xmlns="http://www.w3.org/2000/svg"
      style="width:100%;height:auto;transform:rotate(${cfg.rotate ?? 0}deg)">
      ${floorFill}
      ${roomFills}
      ${wallLines}
      ${liveOutline}
      ${dockIcon}
      ${robotIcon}
      ${labels}
    </svg>`;
  }

  _esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  getCardSize() { return 6; }

  static getStubConfig() {
    return {
      type:             "custom:rowenta-map-card",
      entity:           "sensor.rowenta_xplorer_120_live_map",
      show_dock:        true,
      show_walls:       true,
      show_room_labels: true,
      show_room_areas:  true,
      room_opacity:     0.25,
      title:            "Live Map",
    };
  }
}

customElements.define("rowenta-map-card", RowentaMapCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type:        "rowenta-map-card",
  name:        "Rowenta Map Card",
  description: "Live SVG map for the Rowenta Xplorer 120",
});
console.info(
  `%c ROWENTA-MAP-CARD %c v${VERSION} `,
  "background:#2196F3;color:white;padding:2px 4px;border-radius:3px 0 0 3px",
  "background:#1e1e1e;color:#ccc;padding:2px 4px;border-radius:0 3px 3px 0"
);
