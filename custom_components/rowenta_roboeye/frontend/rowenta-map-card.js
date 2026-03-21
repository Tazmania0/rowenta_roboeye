// Rowenta Xplorer 120 — Live Map Card  v2.6.0
// Renders rooms, walls, dock, robot, live outline and post-run session
// replay (cleaning grid + robot trail) from _build_live_map_payload() schema.
// v2.6.0: avoidance zones rendered as hatched red overlay.

const VERSION = "2.6.0";

// ── Geometry helpers ────────────────────────────────────────────────────

/** Compute polygon centroid as mean of vertices. */
function centroid(pts) {
  if (!pts || !pts.length) return { x: 0, y: 0 };
  const sx = pts.reduce((a, p) => a + p[0], 0);
  const sy = pts.reduce((a, p) => a + p[1], 0);
  return { x: sx / pts.length, y: sy / pts.length };
}

/**
 * Decode a cleaning grid's RLE `cleaned` array into renderable rects.
 *
 * The `cleaned` array alternates [filled_run, empty_run, filled_run, …]
 * scanning left-to-right, row-by-row from the bottom (lower_left origin).
 *
 * Returns { rects:[{x,y,w,h}], res, lower_left_x, lower_left_y, size_x, size_y }
 * where x/y/w/h are in API units (1 unit = 2 mm = 0.2 cm).
 * Returns null if the grid is missing or empty.
 */
function decodeCleaningGrid(grid) {
  if (!grid || !grid.size_x || !grid.cleaned?.length) return null;
  const { lower_left_x, lower_left_y, size_x, size_y, resolution, cleaned } = grid;
  const rects = [];
  let cellIdx = 0;
  let isFilled = true;  // first run is always "filled" (cleaned)
  for (let ri = 0; ri < cleaned.length; ri++) {
    const runLen = cleaned[ri];
    if (isFilled) {
      let remaining = runLen;
      while (remaining > 0) {
        const col = cellIdx % size_x;
        const row = Math.floor(cellIdx / size_x);
        const runInRow = Math.min(remaining, size_x - col);
        rects.push({
          x: lower_left_x + col * resolution,
          y: lower_left_y + row * resolution,
          w: runInRow * resolution,
          h: resolution,
        });
        cellIdx += runInRow;
        remaining -= runInRow;
      }
    } else {
      cellIdx += runLen;
    }
    isFilled = !isFilled;
  }
  return { rects, res: resolution, lower_left_x, lower_left_y, size_x, size_y };
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
      entity:                "sensor.rowenta_xplorer_120_live_map",
      rotate:                0,
      show_dock:             true,
      show_walls:            true,
      show_room_labels:      true,
      show_room_areas:       true,
      room_opacity:          0.25,
      title:                 "Live Map",
      show_redundant_rooms:  false,
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

    const rooms           = attrs.rooms            || [];
    const avoidanceZones  = attrs.avoidance_zones  || [];
    const outline         = attrs.outline          || [];
    const walls           = attrs.walls            || [];
    const dock            = attrs.dock             || null;
    const robot           = attrs.robot            || null;
    const liveOut         = attrs.live_outline     || [];
    const bounds          = attrs.bounds           || null;
    const isActive        = attrs.is_active        || false;
    const cleaningGrid    = attrs.cleaning_grid    || null;
    const robotPath       = attrs.robot_path       || [];
    const sessionComplete = attrs.session_complete || false;
    // is_tentative=true means rough initial estimate → dim/pulse the icon
    const robotIsTentative = robot?.is_tentative   ?? false;

    const stateColor = mapState === "cleaning"         ? "#4CAF50"
                     : mapState === "exploring"        ? "#9C27B0"
                     : mapState === "returning"        ? "#FF9800"
                     : mapState === "mapping"          ? "#2196F3"
                     : mapState === "session_complete" ? "#4CAF50"
                     : "var(--secondary-text-color)";

    const frozen = this._frozen;

    const svgHtml = this._buildSvg(
      rooms, avoidanceZones, outline, walls, dock, robot, liveOut, bounds, isActive, cfg,
      cleaningGrid, robotPath, sessionComplete, robotIsTentative
    );

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
        .svg-wrap { display: block; width: 100%; background: #0d1a2a; overflow: hidden; }
        .svg-wrap svg {
          display: block; width: 100%; height: auto; max-width: 100%;
          transform: rotate(${cfg.rotate ?? 0}deg);
          transform-origin: center center;
        }
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

  _buildSvg(rooms, avoidanceZones, outline, walls, dock, robot, liveOut, bounds, isActive, cfg,
            cleaningGrid, robotPath, sessionComplete,
            robotIsTentative = false) {
    if (!bounds) {
      return `<div class="empty">
        <div style="font-size:40px;opacity:.25">🏠</div>
        <div>No map data yet.<br>Start a cleaning run to populate the map.</div>
      </div>`;
    }

    const displayRobot = robot;
    const displayPath  = robotPath;

    let effMinX = displayRobot ? Math.min(bounds.min_x, displayRobot.x) : bounds.min_x;
    let effMaxX = displayRobot ? Math.max(bounds.max_x, displayRobot.x) : bounds.max_x;
    let effMinY = displayRobot ? Math.min(bounds.min_y, displayRobot.y) : bounds.min_y;
    let effMaxY = displayRobot ? Math.max(bounds.max_y, displayRobot.y) : bounds.max_y;

    const PAD  = 100;
    const minX = effMinX - PAD;
    const maxX = effMaxX + PAD;
    const minY = effMinY - PAD;
    const maxY = effMaxY + PAD;
    const W    = maxX - minX || 1;
    const H    = maxY - minY || 1;

    // Flip Y: SVG Y increases downward, world Y increases upward
    // svgY = (minY + maxY) - worldY
    const flipY = (y) => minY + maxY - y;

    const pts2str = (arr) =>
      arr.map((p) => `${p[0] - minX},${flipY(p[1]) - minY}`).join(" ");

    // ── Adaptive sizing: scales with map extent so icons and text remain
    //    legible on both mobile (narrow viewport) and desktop (wide viewport).
    //    Map coordinates are in API units (1 unit = 2 mm); minDim drives all
    //    proportional sizing so the card looks correct at any display resolution.
    const minDim        = Math.min(W, H);
    const labelFontSize = Math.max(28,  (minDim * 0.038) | 0);  // room name
    const areaFontSize  = Math.max(20,  (minDim * 0.026) | 0);  // area m²
    const robotBodyR    = Math.max(20,  minDim * 0.030);         // robot body
    const robotGlowR    = robotBodyR * 1.5;                      // glow ring
    const rf            = robotBodyR / 16;                       // scale factor vs original r=16
    const dockGlowR     = Math.max(24,  minDim * 0.036);         // dock glow
    const df            = dockGlowR / 24;                        // scale factor vs original r=24
    // Stroke weight: proportional to map extent so lines are neither invisible
    // on large maps nor overwhelming on small ones, across all display resolutions.
    const sw            = Math.max(1.5, minDim * 0.0018);
    // Session badge: scales with map so the badge is always readable and
    // appropriately sized relative to the total map area.
    const badgeFontSz   = Math.max(14,  (minDim * 0.020) | 0);
    const badgeHalfW    = badgeFontSz * 6.5;
    const badgeHh       = badgeFontSz * 1.05;
    const badgeRx       = badgeFontSz * 0.55;

    // ── Layer 1: floor fill (seen_polygon outline) ──────────────────
    let floorFill = "";
    if (outline.length > 2) {
      floorFill = `<polygon points="${pts2str(outline)}"
        fill="#0d1a2a" stroke="#1a3a5c" stroke-width="${(sw * 1.5).toFixed(1)}"/>`;
    } else {
      // Fallback: draw a background rect covering viewBox
      floorFill = `<rect x="0" y="0" width="${W}" height="${H}" fill="#0d1a2a"/>`;
    }

    // ── Layer 2: room fills ─────────────────────────────────────────
    let roomFills = "";
    // In session_complete mode, rooms are faint background reference only
    const roomOpacity = sessionComplete ? 0.15 : (cfg.room_opacity ?? 0.25);
    for (const room of rooms) {
      const p = room.polygon || [];
      if (p.length < 3) continue;
      if (room.redundant && !cfg.show_redundant_rooms) continue;
      if (room.redundant) {
        // Redundant auto-segment: very faint, dashed border, no fill
        const dashLen = (sw * 4).toFixed(1);
        const gapLen  = (sw * 2.5).toFixed(1);
        roomFills += `<polygon points="${pts2str(p)}"
          fill="none"
          stroke="${room.color}" stroke-width="${(sw * 0.8).toFixed(1)}" stroke-linejoin="round"
          stroke-dasharray="${dashLen},${gapLen}" stroke-opacity="0.35"
          data-room="${this._esc(room.name)}"
          style="cursor:pointer"/>`;
      } else {
        roomFills += `<polygon points="${pts2str(p)}"
          fill="${room.color}" fill-opacity="${roomOpacity}"
          stroke="${room.color}" stroke-width="${sw.toFixed(1)}" stroke-linejoin="round"
          data-room="${this._esc(room.name)}"
          style="cursor:pointer"/>`;
      }
    }

    // ── Layer 2.5: avoidance zones (hatched red) ────────────────────
    const hatchSize = Math.max(20, (minDim * 0.025) | 0);
    const hatchSW   = Math.max(2,  (hatchSize * 0.25) | 0);
    let avoidanceLayer = "";
    if (avoidanceZones.length) {
      for (const zone of avoidanceZones) {
        const p = zone.polygon || [];
        if (p.length < 3) continue;
        avoidanceLayer += `<polygon points="${pts2str(p)}"
          fill="url(#hatch-red)" stroke="#f44336"
          stroke-width="${(sw * 2).toFixed(1)}" stroke-linejoin="round"
          opacity="0.7"/>`;
      }
    }

    // ── Layer 3: walls ──────────────────────────────────────────────
    let wallLines = "";
    if (cfg.show_walls) {
      for (const w of walls) {
        const [x1, y1, x2, y2] = w;
        wallLines += `<line
          x1="${x1 - minX}" y1="${flipY(y1) - minY}"
          x2="${x2 - minX}" y2="${flipY(y2) - minY}"
          stroke="white" stroke-width="${sw.toFixed(1)}" stroke-opacity="0.55"
          stroke-linecap="round"/>`;
      }
    }

    // ── Layer 4a: cleaned-area grid cells ───────────────────────────
    const gridData = decodeCleaningGrid(cleaningGrid);
    let gridLayer = "";
    if (gridData?.rects?.length) {
      const gridFill   = sessionComplete ? "rgba(76,175,80,0.72)" : "rgba(76,175,80,0.45)";
      const gridStroke = sessionComplete ? "rgba(45,122,45,0.55)" : "rgba(45,122,45,0.3)";
      gridLayer = gridData.rects.map(r => {
        const gx1 = r.x - minX;
        const gy1 = flipY(r.y + r.h) - minY;   // top-left in SVG (flip bottom edge up)
        const gw  = r.w;
        const gh  = r.h;
        return `<rect x="${gx1.toFixed(1)}" y="${gy1.toFixed(1)}"
          width="${gw}" height="${gh}"
          fill="${gridFill}" stroke="${gridStroke}" stroke-width="${(sw * 0.22).toFixed(2)}"/>`;
      }).join("");
    }

    // ── Layer 4b: live outline (during cleaning) ─────────────────────
    let liveOutline = "";
    if (liveOut.length > 2) {
      liveOutline = `<polygon points="${pts2str(liveOut)}"
        fill="#00aaff" fill-opacity="0.08"
        stroke="#00aaff" stroke-width="${(sw * 0.8).toFixed(1)}"
        stroke-dasharray="${(sw * 4).toFixed(1)},${(sw * 2).toFixed(1)}"/>`;
    }

    // ── Layer 4c: robot path trail ───────────────────────────────────
    let trailLayer = "";
    if (displayPath?.length >= 2) {
      const dimOp    = sessionComplete ? 0.55 : 0.20;
      const brightOp = sessionComplete ? 0.85 : 0.70;
      const allPts   = displayPath.map(([x, y]) => `${x - minX},${flipY(y) - minY}`).join(" ");
      const recentPts = displayPath.slice(-30)
                          .map(([x, y]) => `${x - minX},${flipY(y) - minY}`).join(" ");
      trailLayer = `
        <polyline points="${allPts}"
          fill="none" stroke="#64B5F6" stroke-width="${(sw * 1.4).toFixed(1)}"
          stroke-opacity="${dimOp}" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="${recentPts}"
          fill="none" stroke="#64B5F6" stroke-width="${(sw * 2.8).toFixed(1)}"
          stroke-opacity="${brightOp}" stroke-linecap="round" stroke-linejoin="round"/>`;
    }

    // ── Layer 5: room labels ────────────────────────────────────────
    // Rendered before dock/robot so the icons draw on top.
    let labels = "";
    if (cfg.show_room_labels) {
      for (const room of rooms) {
        const p = room.polygon || [];
        if (p.length < 3) continue;
        if (room.redundant && !cfg.show_redundant_rooms) continue;
        const c = centroid(p);
        const cx = c.x - minX;
        const cy = flipY(c.y) - minY;
        const labelOpacity = room.redundant ? 0.3 : 1.0;
        let areaText = "";
        if (cfg.show_room_areas) {
          // Use pre-computed shoelace area from backend; fall back to JS calculation
          const areaM2 = room.area_m2 != null
            ? room.area_m2.toFixed(1)
            : (polyArea(p) / 10000).toFixed(1);
          areaText = `<text x="${cx}" y="${cy + labelFontSize * 1.3}"
            text-anchor="middle" fill="${room.color}" font-size="${areaFontSize}"
            opacity="${room.redundant ? 0.25 : 0.75}"
            font-family="sans-serif">${areaM2} m²</text>`;
        }
        labels += `<text x="${cx}" y="${cy}"
          text-anchor="middle" dominant-baseline="middle"
          fill="${room.color}" font-size="${labelFontSize}" font-weight="bold"
          opacity="${labelOpacity}" font-family="sans-serif"
          style="text-shadow:0 1px 3px rgba(0,0,0,.8)">${this._esc(room.name)}</text>
          ${areaText}`;
      }
    }

    

    // ── Layer 6: dock icon (home) — draws on top of labels ──────────
    let dockIcon = "";
    if (cfg.show_dock && dock) {
      const dx = dock.x - minX;
      const dy = flipY(dock.y) - minY;
      // House/home icon: fixed orientation — dock position doesn't rotate.
      // All dimensions scale with df (= dockGlowR / 24) so the icon stays
      // proportional to the map on any screen size.
      dockIcon = `<g transform="translate(${dx},${dy})">
        <!-- Glow background -->
        <circle cx="0" cy="${(2*df).toFixed(1)}" r="${dockGlowR.toFixed(1)}" fill="#FFD700" opacity="0.15"/>
        <!-- Roof triangle -->
        <polygon points="0,${(-22*df).toFixed(1)} ${(20*df).toFixed(1)},${(2*df).toFixed(1)} ${(-20*df).toFixed(1)},${(2*df).toFixed(1)}"
          fill="#FFD700" stroke="white" stroke-width="${(1.5*df).toFixed(1)}" stroke-linejoin="round"/>
        <!-- House walls -->
        <rect x="${(-16*df).toFixed(1)}" y="${(2*df).toFixed(1)}" width="${(32*df).toFixed(1)}" height="${(20*df).toFixed(1)}"
          fill="#FFD700" stroke="white" stroke-width="${(1.5*df).toFixed(1)}" stroke-linejoin="round"/>
        <!-- Door (dark opening) -->
        <rect x="${(-7*df).toFixed(1)}" y="${(9*df).toFixed(1)}" width="${(14*df).toFixed(1)}" height="${(13*df).toFixed(1)}" rx="${(2*df).toFixed(1)}"
          fill="#0d1a2a"/>
      </g>`;
    }

    // ── Layer 7: robot icon — draws on top of everything ───────────
    // All dimensions scale with rf (= robotBodyR / 16) so the robot icon
    // remains proportional to the map on any screen size.
    // robotIsTentative=true → rough estimate → dimmed pulsing icon.
    let robotIcon = "";
    if (displayRobot != null) {
      const rx = displayRobot.x - minX;
      const ry = flipY(displayRobot.y) - minY;
      // heading_deg from /get/rob_pose is already in degrees — use directly.
      // 0 = North, 90 = East, 180 = South, 270 = West (matches SVG rotate()).
      const headingDeg = displayRobot.heading_deg ?? 0;
      const activeClass = (isActive && !robotIsTentative) ? ' class="robot-active"' : "";
      const robotColor   = robotIsTentative ? "#888888" : "#00d4ff";
      const robotOpacity = robotIsTentative ? "0.5" : "1.0";
      const tentativeAnim = robotIsTentative
        ? `<animate attributeName="opacity" values="0.3;0.7;0.3" dur="1.5s" repeatCount="indefinite"/>`
        : "";
      robotIcon = `<g${activeClass} opacity="${robotOpacity}">
        ${tentativeAnim}
        ${(isActive && !robotIsTentative) ? `<circle cx="${rx}" cy="${ry}" r="${robotGlowR.toFixed(1)}" fill="${robotColor}" opacity="0.15"/>` : ""}
        <!-- Robot body -->
        <circle cx="${rx}" cy="${ry}" r="${robotBodyR.toFixed(1)}"
          fill="#1a2a3a" stroke="${robotColor}" stroke-width="${(3*rf).toFixed(1)}"/>
        <!-- Direction indicators (rotated to heading) -->
        <g transform="translate(${rx},${ry}) rotate(${headingDeg})">
          <!-- Front bumper arc -->
          <path d="M ${(-13*rf).toFixed(1)},${(-9*rf).toFixed(1)} A ${robotBodyR.toFixed(1)},${robotBodyR.toFixed(1)} 0 0,1 ${(13*rf).toFixed(1)},${(-9*rf).toFixed(1)}"
            fill="none" stroke="${robotColor}" stroke-width="${(3.5*rf).toFixed(1)}" stroke-linecap="round"/>
          <!-- Arrow head pointing forward -->
          <polygon points="0,${(-26*rf).toFixed(1)} ${(7*rf).toFixed(1)},${(-16*rf).toFixed(1)} ${(-7*rf).toFixed(1)},${(-16*rf).toFixed(1)}" fill="${robotColor}"/>
        </g>
        <!-- Centre sensor dot -->
        <circle cx="${rx}" cy="${ry}" r="${(4*rf).toFixed(1)}" fill="white" opacity="0.8"/>
        ${robotIsTentative ? `<!-- Tentative position indicator -->
        <text x="${rx}" y="${ry + robotBodyR + (labelFontSize * 0.55)}" text-anchor="middle"
          font-size="${(labelFontSize * 0.45).toFixed(0)}" fill="#888" font-family="sans-serif"
          opacity="0.8">estimating…</text>` : ""}
      </g>`;
    }

    // ── Session badge (top-right, session_complete mode only) ────────
    let sessionBadge = "";
    if (sessionComplete) {
      let cleanedM2 = "?";
      if (gridData?.rects?.length) {
        const cellSizeCm = (gridData.res ?? 40) * 0.2;   // API units × 0.2 cm/unit
        const cellM2     = (cellSizeCm / 100) ** 2;       // cm → m
        const cellCount  = gridData.rects.reduce(
          (s, r) => s + (r.w / gridData.res) * (r.h / gridData.res), 0
        );
        cleanedM2 = (cellCount * cellM2).toFixed(2);
      }
      // Place badge anchored to top-right corner of SVG; all dimensions scale
      // with badgeFontSz (derived from minDim) so it looks right at any resolution.
      const bx = W - badgeHalfW * 0.15;
      const by = badgeHh * 1.2;
      sessionBadge = `
        <g transform="translate(${bx.toFixed(1)},${by.toFixed(1)})">
          <rect x="${(-badgeHalfW * 2).toFixed(1)}" y="${(-badgeHh).toFixed(1)}"
            width="${(badgeHalfW * 2).toFixed(1)}" height="${(badgeHh * 2).toFixed(1)}"
            rx="${badgeRx.toFixed(1)}"
            fill="rgba(13,26,10,0.9)" stroke="#4CAF50" stroke-width="${(sw * 0.8).toFixed(1)}"/>
          <text x="${(-badgeHalfW).toFixed(1)}" y="0" text-anchor="middle"
            dominant-baseline="middle"
            fill="#4CAF50" font-size="${badgeFontSz}" font-family="sans-serif" font-weight="600">
            ✓ Last session · ${cleanedM2} m²
          </text>
        </g>`;
    }

    return `<svg viewBox="0 0 ${W} ${H}"
      width="100%"
      preserveAspectRatio="xMidYMid meet"
      xmlns="http://www.w3.org/2000/svg"
      style="display:block;height:auto">
      <defs>
        <pattern id="hatch-red" patternUnits="userSpaceOnUse"
          width="${hatchSize}" height="${hatchSize}" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="${hatchSize}"
            stroke="#f44336" stroke-width="${hatchSW}" stroke-opacity="0.8"/>
        </pattern>
      </defs>
      ${floorFill}
      ${roomFills}
      ${avoidanceLayer}
      ${wallLines}
      ${gridLayer}
      ${liveOutline}
      ${trailLayer}
      ${labels}
      ${dockIcon}
      ${robotIcon}
      ${sessionBadge}
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
      type:                  "custom:rowenta-map-card",
      entity:                "sensor.rowenta_xplorer_120_live_map",
      show_dock:             true,
      show_walls:            true,
      show_room_labels:      true,
      show_room_areas:       true,
      room_opacity:          0.25,
      title:                 "Live Map",
      show_redundant_rooms:  false,
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
