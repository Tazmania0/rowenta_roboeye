// ─────────────────────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────────────────────
export const state = {
  maps: [],
  activeMapId: null,
  areas: [],
  bbox: null,          // {minX,minY,maxX,maxY}
  selectedAreaId: null,
  selectedAreaIds: new Set(),
  mergeFirstId: null,
  mode: 'select',      // select | pan | split | merge | block | spot
  splitPoints: [],     // 0 or 1 SVG points for split line preview
  rectStart:   null,   // {x,y} SVG coords of first rect corner (drag-to-draw)
  rectMode:    null,   // 'block' | 'spot' | null
  areaDrag:    null,   // active no-go / spot reposition drag
  suppressNextClick: false,
  connected: false,
  exploreMapId:   null,   // map_id of current temporary/explore map
  dockingPose:    null,   // {x,y,heading,valid} read from feature_map
  explorePhase:   null,   // null | 'running' | 'drawing' | 'naming' | 'saving'
  splitFormat:    null,   // last successful split URL format label

  // Robot live state
  robPose:              null,
  robPoseTimer:         null,
  robotMode:            null,
  robotCharging:        null,
  robotBatteryLevel:    null,
  robotErrorCode:       null,   // number | null
  robotCleaningTimeSec: null,   // number | null (seconds)
  robotAreaCleanedCm2:  null,   // number | null
  cleaningGrid:         null,
  statusTimer:          null,
  mapHasUnsavedEdits:   false,
  _lastWalls:         [],
  _lastDock:          null,

  // Pan/zoom
  viewTx: 0, viewTy: 0, viewScale: 1,
  panStart: null,
};
