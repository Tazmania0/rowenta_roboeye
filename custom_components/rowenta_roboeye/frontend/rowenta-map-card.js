// Rowenta Xplorer 120 — Live Map Card  v1.3.2
// cleaning_grid_map decoder: RLE occupancy grid → SVG rect cells

const VERSION = "1.3.2";

// ── RLE decoder ────────────────────────────────────────────────────────
// API format: { lower_left_x, lower_left_y, size_x, size_y, resolution, cleaned:[...] }
// cleaned[] = pairs of (skip_count, fill_count), last element may be lone skip
// Coordinate system: mm, Y-up (flip for SVG)
function decodeCleaningGrid(grid) {
  if (!grid || !grid.cleaned || !grid.size_x) return null;
  const { lower_left_x: llx, lower_left_y: lly,
          size_x: cols, size_y: rows, resolution: res, cleaned: rle } = grid;

  // Decode RLE into flat cell array
  const cells = new Uint8Array(cols * rows);
  let pos = 0;
  for (let i = 0; i < rle.length - 1; i += 2) {
    pos += rle[i];           // skip empty cells
    const fill = rle[i + 1];
    for (let j = 0; j < fill && pos < cells.length; j++) cells[pos++] = 1;
  }

  // Build SVG rects for all filled cells
  // World coords: x = llx + col*res,  y = lly + row*res  (Y-up)
  // Merge adjacent cells in same row into wider rects for performance
  const rects = [];
  for (let row = 0; row < rows; row++) {
    let runStart = -1;
    for (let col = 0; col <= cols; col++) {
      const filled = col < cols && cells[row * cols + col] === 1;
      if (filled && runStart === -1) {
        runStart = col;
      } else if (!filled && runStart !== -1) {
        rects.push({
          x:    llx + runStart * res,
          y:    lly + row * res,       // Y-up, flip in SVG transform
          w:    (col - runStart) * res,
          h:    res,
        });
        runStart = -1;
      }
    }
  }

  // Tight bounding box of filled cells only (null when no cells exist yet)
  const wx = rects.map(r => r.x);
  const wy = rects.map(r => r.y);
  const bounds = rects.length ? {
    min_x: Math.min(...wx),
    max_x: Math.max(...wx.map((x,i) => x + rects[i].w)),
    min_y: Math.min(...wy),
    max_y: Math.max(...wy.map((y,i) => y + rects[i].h)),
  } : null;

  // Full grid extent — used as last-resort fallback when no cells are filled yet
  const extent = {
    min_x: llx,
    max_x: llx + cols * res,
    min_y: lly,
    max_y: lly + rows * res,
  };

  return { rects, bounds, extent, cols, rows, res, llx, lly };
}


class RowentaMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass       = null;
    this._config     = {};
    this._frozen     = false;
    this._lastData   = null;
    this._openPanels = new Set();
  }

  setConfig(config) {
    if (!config) throw new Error("No configuration provided");
    this._config = {
      entity:     "sensor.rowenta_xplorer_120_live_map",
      show_debug: true,
      title:      "Live Map",
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
        <b>Error:</b> ${err.message}</div></ha-card>`;
    }
  }

  _render() {
    if (!this._hass || !this._config.entity) return;
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color)">
        Entity <b>${this._config.entity}</b> not found —
        enable sensor.rowenta_xplorer_120_live_map in entity settings.
      </div></ha-card>`;
      return;
    }
    const attrs = state.attributes || {};
    this._lastData = {
      mapState:        state.state || "idle",
      floorPlan:       attrs.floor_plan           || [],
      cleaningGrid:    attrs.cleaning_grid        || null,
      cleanedArea:     attrs.cleaned_area         || [],
      robotPos:        attrs.robot_position       || null,
      bounds:          attrs.coordinate_bounds    || null,
      rawLiveParams:   attrs.raw_live_parameters  || null,
      rawLocalization: attrs.raw_localization     || null,
      rawSeenPolygon:  attrs.raw_seen_polygon     || null,
      rawNnPolygons:   attrs.raw_n_n_polygons    || null,
      attrs,
    };
    this._renderFull(this._lastData);
  }

  _renderFull(d) {
    if (!d) return;
    const { mapState, floorPlan, cleaningGrid, cleanedArea,
            robotPos, bounds, rawLiveParams, rawLocalization, rawSeenPolygon, rawNnPolygons, attrs } = d;
    const frozen = this._frozen;

    const stateColor = mapState === "cleaning" ? "#4CAF50"
                     : mapState === "returning" ? "#FF9800"
                     : "var(--secondary-text-color)";

    // Decode cleaning grid
    const grid = decodeCleaningGrid(cleaningGrid);

    // Bounds priority:
    //  1. floor_plan coordinate_bounds (from n_n_polygons) — most semantically correct
    //  2. tight bounds of filled grid cells — correct when cleaning but no floor plan
    //  3. full grid extent — last resort so the viewport is non-null from the first poll
    const mapBounds = bounds || grid?.bounds || grid?.extent;

    const svgContent = this._buildSvg(floorPlan, cleanedArea, robotPos, mapBounds, grid);
    const debugHtml  = this._config.show_debug
      ? this._buildDebug(floorPlan, cleaningGrid, grid, cleanedArea, robotPos,
          mapBounds, rawLiveParams, rawLocalization, rawSeenPolygon, rawNnPolygons, attrs)
      : "";

    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block}
        ha-card{padding:0;overflow:hidden}
        *{box-sizing:border-box}
        .hdr{display:flex;align-items:center;padding:10px 14px;
          border-bottom:1px solid var(--divider-color);gap:8px}
        .title{font-size:15px;font-weight:500;flex:1}
        .badge{padding:2px 8px;border-radius:12px;font-size:11px;font-weight:bold;
          background:${stateColor}22;color:${stateColor};border:1px solid ${stateColor};white-space:nowrap}
        .fbtn{padding:4px 10px;border-radius:6px;font-size:12px;cursor:pointer;
          border:1px solid var(--divider-color);white-space:nowrap;
          background:${frozen?"#ff572222":"var(--secondary-background-color)"};
          color:${frozen?"#ff5722":"var(--primary-text-color)"};
          font-weight:${frozen?"bold":"normal"}}
        .frozen-bar{background:#ff572218;border-bottom:2px solid #ff5722;
          padding:3px 14px;font-size:11px;color:#ff5722}
        .counters{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
          background:var(--divider-color);border-bottom:1px solid var(--divider-color)}
        .cnt{background:var(--card-background-color);padding:7px;text-align:center}
        .cnt-v{font-size:18px;font-weight:bold;color:var(--primary-color)}
        .cnt-l{font-size:10px;color:var(--secondary-text-color)}
        .svg-wrap{background:var(--primary-background-color);min-height:180px}
        .svg-wrap svg{display:block;width:100%}
        .empty{display:flex;align-items:center;justify-content:center;
          min-height:180px;flex-direction:column;gap:8px;padding:16px;
          text-align:center;font-size:13px;color:var(--secondary-text-color)}
        .dbg{border-top:1px solid var(--divider-color)}
        .dbg-hdr{padding:4px 14px;font-size:11px;color:var(--secondary-text-color);
          background:var(--secondary-background-color);border-bottom:1px solid var(--divider-color)}
        .pnl{border-bottom:1px solid var(--divider-color,#eee)}
        .pnl-h{display:flex;justify-content:space-between;align-items:center;
          padding:7px 12px;cursor:pointer;user-select:none}
        .pnl-h:hover{background:var(--secondary-background-color)}
        .pnl-t{font-size:13px;font-weight:500}
        .pnl-m{font-size:11px;color:var(--secondary-text-color);
          display:flex;align-items:center;gap:6px}
        .cpybtn{padding:1px 6px;font-size:10px;border-radius:4px;cursor:pointer;
          border:1px solid var(--divider-color);background:var(--secondary-background-color)}
        .pnl-b{display:none;padding:8px 12px 10px;font-size:11px;font-family:monospace;
          background:var(--secondary-background-color);overflow-x:auto;
          white-space:pre-wrap;word-break:break-all;max-height:280px;overflow-y:auto;
          user-select:text;-webkit-user-select:text;cursor:text}
        .pnl-b.open{display:block}
      </style>
      <ha-card>
        <div class="hdr">
          <span class="title">🗺 ${this._config.title}</span>
          <span class="badge">${mapState.toUpperCase()}</span>
          <button class="fbtn" id="fbtn">${frozen?"▶ Live":"⏸ Freeze"}</button>
        </div>
        ${frozen ? `<div class="frozen-bar">⏸ Frozen — click Live to resume</div>` : ""}
        <div class="counters">
          <div class="cnt">
            <div class="cnt-v">${floorPlan.length}</div>
            <div class="cnt-l">Rooms</div>
          </div>
          <div class="cnt">
            <div class="cnt-v" style="color:${grid?"#4CAF50":"inherit"}">${grid ? grid.rects.length : "—"}</div>
            <div class="cnt-l">Grid rects</div>
          </div>
          <div class="cnt">
            <div class="cnt-v" style="color:${robotPos?"#FF9800":"inherit"}">${robotPos?"✓":"—"}</div>
            <div class="cnt-l">Robot pos</div>
          </div>
          <div class="cnt">
            <div class="cnt-v" style="color:${mapBounds?"#9C27B0":"inherit"}">${mapBounds?"✓":"—"}</div>
            <div class="cnt-l">Bounds</div>
          </div>
        </div>
        <div class="svg-wrap">${svgContent}</div>
        ${debugHtml}
      </ha-card>`;

    // Freeze button
    this.shadowRoot.getElementById("fbtn")?.addEventListener("click", () => {
      if (this._frozen) { this._frozen = false; this._render(); }
      else { this._frozen = true; this._renderFull(this._lastData); }
    });

    // Restore open panels + attach listeners
    this.shadowRoot.querySelectorAll(".pnl").forEach(pnl => {
      const key   = pnl.dataset.key;
      const head  = pnl.querySelector(".pnl-h");
      const body  = pnl.querySelector(".pnl-b");
      const arrow = pnl.querySelector(".arrow");
      const cpy   = pnl.querySelector(".cpybtn");
      if (key && this._openPanels.has(key)) {
        body?.classList.add("open");
        if (arrow) arrow.textContent = "▲";
      }
      head?.addEventListener("click", () => {
        body?.classList.toggle("open");
        const open = body?.classList.contains("open");
        if (arrow) arrow.textContent = open ? "▲" : "▼";
        if (key) open ? this._openPanels.add(key) : this._openPanels.delete(key);
      });
      cpy?.addEventListener("click", e => {
        e.stopPropagation();
        // Read from data-content attribute (raw unescaped JSON)
        const raw = pnl.dataset.content || body?.textContent || "";
        navigator.clipboard?.writeText(raw).then(() => {
          cpy.textContent = "✓"; setTimeout(() => cpy.textContent = "Copy", 1500);
        }).catch(() => {
          // Fallback: select text manually
          const range = document.createRange();
          range.selectNodeContents(body);
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(range);
        });
      });
    });
  }

  // ── SVG renderer ────────────────────────────────────────────────────

  _buildSvg(floorPlan, cleanedArea, robotPos, bounds, grid) {
    if (!bounds) {
      return `<div class="empty"><div style="font-size:36px;opacity:.3">🏠</div>
        No map bounds yet — start a cleaning run then freeze the card to inspect debug data.</div>`;
    }

    const { min_x, min_y, max_x, max_y } = bounds;
    const W = max_x - min_x || 1, H = max_y - min_y || 1;
    const pad = Math.max(W, H) * 0.05;
    const vw = W + 2*pad, vh = H + 2*pad;

    // Map world coords → SVG coords (flip Y: SVG Y increases down, world Y increases up)
    const sx = x => ((x - min_x + pad) / vw * 500).toFixed(2);
    const sy = y => ((max_y + pad - y) / vh * 400).toFixed(2);  // Y-flip

    const COLORS = ["#4fc3f7","#81c784","#ffb74d","#ba68c8",
                    "#4db6ac","#f06292","#aed581","#4dd0e1"];

    // Room polygons / outlines
    let rooms = "";
    floorPlan.forEach((room, i) => {
      const c = COLORS[i % COLORS.length];

      // Light bounding-box fill (always available)
      const bboxPts = (room.polygon||[]).map(p=>`${sx(p[0])},${sy(p[1])}`).join(" ");
      if (bboxPts)
        rooms += `<polygon points="${bboxPts}" fill="${c}22" stroke="none"/>`;

      if (room.segments && room.segments.length) {
        // Accurate room outline from raw segments
        const d = room.segments.map(s =>
          `M${sx(s.x1)},${sy(s.y1)} L${sx(s.x2)},${sy(s.y2)}`
        ).join(" ");
        rooms += `<path d="${d}" fill="none" stroke="${c}"
          stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`;
      } else if (bboxPts) {
        // Fallback: bounding-box outline
        rooms += `<polygon points="${bboxPts}" fill="none" stroke="${c}"
          stroke-width="1.5" stroke-linejoin="round"/>`;
      }

      if (room.center)
        rooms += `<text x="${sx(room.center.x)}" y="${sy(room.center.y)}"
          font-size="13" fill="${c}" text-anchor="middle" dominant-baseline="middle"
          font-family="sans-serif" font-weight="600"
          style="text-shadow:0 1px 2px rgba(0,0,0,.6)">${room.name||room.id}</text>`;
    });

    // Cleaned area from grid (primary source)
    let cleaned = "";
    if (grid && grid.rects.length > 0) {
      // Render as SVG rects — already merged per-row for performance
      cleaned = grid.rects.map(r =>
        `<rect x="${sx(r.x)}" y="${sy(r.y + r.h)}"
          width="${(r.w / vw * 500).toFixed(2)}"
          height="${(r.h / vh * 400).toFixed(2)}"
          fill="rgba(76,175,80,0.35)"/>`
      ).join("");
    }

    // Seen-polygon outline — always drawn as a stroke layer on top of the grid
    // so the explored boundary is visible regardless of whether grid cells exist.
    let outline = "";
    if (Array.isArray(cleanedArea) && cleanedArea.length > 2) {
      const pts = cleanedArea.map(p =>
        Array.isArray(p) ? `${sx(p[0])},${sy(p[1])}` : `${sx(p.x)},${sy(p.y)}`
      ).join(" ");
      if (!grid || grid.rects.length === 0) {
        // No grid: fill the polygon so the cleaned area is still visible
        outline = `<polygon points="${pts}" fill="rgba(76,175,80,0.25)" stroke="#4CAF50" stroke-width="2" stroke-linejoin="round"/>`;
      } else {
        // Grid present: draw outline only (no fill) to avoid obscuring the cells
        outline = `<polygon points="${pts}" fill="none" stroke="#4CAF50" stroke-width="2" stroke-linejoin="round" stroke-dasharray="6 3"/>`;
      }
    }

    // Robot dot + heading arrow
    let robot = "";
    if (robotPos?.x != null) {
      const rx = parseFloat(sx(robotPos.x)), ry = parseFloat(sy(robotPos.y));
      const angle = ((robotPos.heading_deg || 0) * Math.PI / 180);
      const arrowLen = 16;
      // Heading in world coords (Y-up): flip Y component for SVG
      const ax = (rx + Math.cos(angle)  * arrowLen).toFixed(2);
      const ay = (ry - Math.sin(angle)  * arrowLen).toFixed(2);  // negate sin for Y-flip
      robot = `
        <circle cx="${rx}" cy="${ry}" r="10" fill="#2196F3" stroke="white" stroke-width="2.5"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}"
          stroke="white" stroke-width="3" stroke-linecap="round"/>
        <circle cx="${rx}" cy="${ry}" r="10" fill="none" stroke="#2196F3" stroke-width="2" opacity="0.5">
          <animate attributeName="r" values="10;22;10" dur="2s" repeatCount="indefinite"/>
          <animate attributeName="opacity" values="0.5;0;0.5" dur="2s" repeatCount="indefinite"/>
        </circle>`;
    }

    return `<svg viewBox="0 0 500 400" xmlns="http://www.w3.org/2000/svg" style="width:100%">
      <defs><pattern id="g" width="20" height="20" patternUnits="userSpaceOnUse">
        <path d="M20 0L0 0 0 20" fill="none" stroke="var(--divider-color)" stroke-width="0.3"/>
      </pattern></defs>
      <rect width="500" height="400" fill="url(#g)"/>
      ${rooms}${cleaned}${outline}${robot}
    </svg>`;
  }

  // ── Debug panels ────────────────────────────────────────────────────

  _buildDebug(floorPlan, cleaningGrid, grid, cleanedArea, robotPos, bounds, rawLiveParams, rawLocalization, rawSeenPolygon, rawNnPolygons, attrs) {
    const fmt  = o => { try { return JSON.stringify(o, null, 2); } catch { return String(o); } };

    const pnl = (key, icon, title, color, content, meta="") => `
      <div class="pnl" data-key="${key}" data-content="${this._attrEsc(content)}">
        <div class="pnl-h">
          <span class="pnl-t" style="color:${color}">${icon} ${title}</span>
          <span class="pnl-m">
            <span>${meta}</span>
            <button class="cpybtn">Copy</button>
            <span class="arrow">▼</span>
          </span>
        </div>
        <div class="pnl-b">${this._esc(content)}</div>
      </div>`;

    const gridStats = grid
      ? `${grid.rects.length} rects, ${grid.cols}×${grid.rows} cells @ ${grid.res}mm`
      : "—";

    return `<div class="dbg">
      <div class="dbg-hdr">▼ Debug · tap to expand · ⏸ Freeze then expand · Copy to clipboard</div>
      ${pnl("robot","🤖","robot_position","#FF9800",
        robotPos ? fmt(robotPos) : "(null — check raw_localization below for field names)",
        robotPos ? `x:${robotPos.x} y:${robotPos.y}` : "—")}
      ${pnl("rawloc","🧭","raw_localization (position source)","#FF5722",
        rawLocalization ? fmt(rawLocalization) : "(null — fetched from /debug/localization during cleaning)",
        rawLocalization ? Object.keys(rawLocalization).join(", ") : "—")}
      ${pnl("rawseen","🗺️","raw_seen_polygon (cleaned outline)","#4CAF50",
        rawSeenPolygon ? fmt(rawSeenPolygon) : "(null — fetched from /get/seen_polygon during cleaning)",
        rawSeenPolygon && rawSeenPolygon.polygons ? rawSeenPolygon.polygons.length + " polygon(s)" : "—")}
      ${pnl("rawlive","⚙️","raw_live_parameters (behaviour config)","#9E9E9E",
        rawLiveParams ? fmt(rawLiveParams) : "(null)",
        rawLiveParams ? Object.keys(rawLiveParams).join(", ") : "—")}
      ${pnl("grid","🟩","cleaning_grid (decoded)","#4CAF50",
        grid ? `${gridStats}\nbounds: ${fmt(grid.bounds)}` : "(null — start a cleaning run)",
        gridStats)}
      ${pnl("rawgrid","📦","cleaning_grid (raw API)","#8BC34A",
        cleaningGrid ? fmt(cleaningGrid) : "(null)",
        cleaningGrid ? `${cleaningGrid.size_x}×${cleaningGrid.size_y} @ ${cleaningGrid.resolution}mm` : "—")}
      ${pnl("bounds","📐","coordinate_bounds","#9C27B0",
        bounds ? fmt(bounds) : "(null)",
        bounds ? `${(bounds.max_x-bounds.min_x).toFixed(0)}×${(bounds.max_y-bounds.min_y).toFixed(0)}mm` : "—")}
      ${pnl("rawnn","🔷","raw_n_n_polygons (room outlines)","#2196F3",
        rawNnPolygons ? fmt(rawNnPolygons) : "(null — available any time, check HA logs for parsing errors)",
        rawNnPolygons && typeof rawNnPolygons==="object" ? Object.keys(rawNnPolygons).join(", ") : "—")}
      ${pnl("floor","🏠","floor_plan (parsed rooms)","#4fc3f7",
        floorPlan.length ? fmt(floorPlan) : "(empty — check /get/n_n_polygons endpoint)",
        `${floorPlan.length} rooms`)}
      ${pnl("all","📋","all attributes","#607D8B", fmt(attrs), "")}
    </div>`;
  }

  _esc(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  _attrEsc(s) {
    // Escape for use in HTML attribute value (double-quoted)
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  getCardSize() { return this._config.show_debug ? 9 : 5; }

  static getStubConfig() {
    return {
      type:       "custom:rowenta-map-card",
      entity:     "sensor.rowenta_xplorer_120_live_map",
      show_debug: true,
      title:      "Live Map",
    };
  }
}

customElements.define("rowenta-map-card", RowentaMapCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type:        "rowenta-map-card",
  name:        "Rowenta Map Card",
  description: "Live map for the Rowenta Xplorer 120",
});
console.info(
  `%c ROWENTA-MAP-CARD %c v${VERSION} `,
  "background:#2196F3;color:white;padding:2px 4px;border-radius:3px 0 0 3px",
  "background:#1e1e1e;color:#ccc;padding:2px 4px;border-radius:0 3px 3px 0"
);
