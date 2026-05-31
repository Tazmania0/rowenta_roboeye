// ─────────────────────────────────────────────────────────────────────────────
// COORDINATE TRANSFORMS
// ─────────────────────────────────────────────────────────────────────────────
// Robot Y is math-convention (up). SVG Y is screen-convention (down).
// Flip: svgY = (maxY + minY) - robotY
import { state } from './state.js';

const mapSvg = document.getElementById('map-svg');

export function robotToSVG(rx, ry) {
  const { minY, maxY } = state.bbox;
  return { x: rx, y: (maxY + minY) - ry };
}

export function svgToRobot(sx, sy) {
  const { minY, maxY } = state.bbox;
  return { x: Math.round(sx), y: Math.round((maxY + minY) - sy) };
}

export function areaToSVGPoints(area) {
  return area.points.map(p => {
    const s = robotToSVG(p.x, p.y);
    return `${s.x},${s.y}`;
  }).join(' ');
}

// Get SVG point from mouse event (accounting for pan/zoom transform)
export function eventToSVGPoint(evt) {
  // Use the SVG native coordinate transform — handles viewBox, preserveAspectRatio,
  // CSS transforms and all other offsets correctly.
  const pt = mapSvg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  // getScreenCTM() returns null when the SVG isn't laid out / is hidden
  // (e.g. empty-state shown) — guard so the mousemove/wheel handlers don't throw.
  const ctm = mapSvg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  return pt.matrixTransform(ctm.inverse());
}

// ─────────────────────────────────────────────────────────────────────────────
// LINE-SEGMENT INTERSECTION (for split)
// ─────────────────────────────────────────────────────────────────────────────
export function lineIntersect(p1, p2, p3, p4) {
  const d1x = p2.x - p1.x, d1y = p2.y - p1.y;
  const d2x = p4.x - p3.x, d2y = p4.y - p3.y;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return null;
  const t = ((p3.x - p1.x) * d2y - (p3.y - p1.y) * d2x) / denom;
  const u = ((p3.x - p1.x) * d1y - (p3.y - p1.y) * d1x) / denom;
  if (t >= 0 && t <= 1 && u >= 0 && u <= 1) {
    return { x: p1.x + t * d1x, y: p1.y + t * d1y };
  }
  return null;
}

export function computeSplitPoints(area, lineA, lineB) {
  // Work in SVG coords. Polygon points are in SVG coords after robotToSVG.
  const pts = area.points.map(p => robotToSVG(p.x, p.y));
  const hits = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const p1 = pts[i], p2 = pts[(i+1)%n];
    const pt = lineIntersect(lineA, lineB, p1, p2);
    if (pt) hits.push(pt);
  }
  if (hits.length < 2) return null;
  // A split line through a concave (e.g. L-shaped) room can cross more than two
  // edges; the first two in vertex order are not necessarily the intended
  // entry/exit pair. Pick the two intersections farthest apart so the split
  // spans the whole room.
  let a = hits[0], b = hits[1], best = -1;
  for (let i = 0; i < hits.length; i++) {
    for (let j = i + 1; j < hits.length; j++) {
      const dx = hits[i].x - hits[j].x, dy = hits[i].y - hits[j].y;
      const d = dx * dx + dy * dy;
      if (d > best) { best = d; a = hits[i]; b = hits[j]; }
    }
  }
  // Convert back to robot integer coords
  return [a, b].map(p => svgToRobot(p.x, p.y));
}
