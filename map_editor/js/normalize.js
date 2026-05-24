// ─────────────────────────────────────────────────────────────────────────────
// AREA NORMALIZATION
// Handles both snake_case (area_id) and camelCase (areaId) field names,
// and both top-level response keys (areas vs areaResponseList).
// Logs the raw first area so exact field names can be confirmed in console.
// ─────────────────────────────────────────────────────────────────────────────
export function normalizeArea(raw) {
  // CONFIRMED from live device: area ID field is "id" (not area_id or areaId)
  let area_id = raw.id ?? raw.area_id ?? raw.areaId ?? null;

  // Last-resort: scan all integer fields (skip known non-ID integers)
  if (area_id === null || area_id === undefined) {
    const skip = new Set(['cleaning_parameter_set','cleaningParameterSet',
                          'floor_type','floorType','area_size','cleaning_counter',
                          'estimated_cleaning_time','average_cleaning_time']);
    for (const [k, v] of Object.entries(raw)) {
      if (!skip.has(k) && typeof v === 'number' && Number.isInteger(v) && v > 0 && v < 100000) {
        console.warn(`[rowenta-editor] area_id fallback: using field "${k}" = ${v}`);
        area_id = v;
        break;
      }
    }
  }

  return {
    area_id,
    area_meta_data:         raw.area_meta_data  ?? raw.areaMetaData  ?? raw.metaData ?? '',
    area_state:             raw.area_state  ?? raw.areaState  ?? 'inactive',
    area_type:              raw.area_type   ?? raw.areaType   ?? 'room',
    cleaning_parameter_set: raw.cleaning_parameter_set ?? raw.cleaningParameterSet ?? 0,
    floor_type:             raw.floor_type  ?? raw.floorType  ?? 'default',
    method:                 raw.method      ?? 'normal',
    pump_volume:            raw.pump_volume ?? raw.pumpVolume ?? 'default',
    room_type:              raw.room_type   ?? raw.roomType   ?? 'none',
    strategy_mode:          raw.strategy_mode ?? raw.strategyMode ?? 'normal',
    points: (raw.points || []).map(p => ({
      x: p.x ?? p.x1 ?? 0,
      y: p.y ?? p.y1 ?? 0,
    })),
    _raw: raw,
  };
}

export function extractAreas(res) {
  const raw = res.areas ?? res.areaResponseList ?? res.area_list ?? [];
  if (raw.length > 0) {
    const keys = Object.keys(raw[0]);
    console.log('[rowenta-editor] area field names from device:', keys);
    console.log('[rowenta-editor] area[0] raw:', JSON.stringify(raw[0], null, 2));
    // Show in the UI so it's visible without opening DevTools
    const dbg = document.getElementById('debug-raw');
    if (dbg) {
      dbg.textContent = 'RAW AREA KEYS: ' + keys.join(', ') + '\n\narea[0]: ' +
        JSON.stringify(raw[0], null, 2);
      const box = document.getElementById('debug-box');
      if (box) box.style.display = 'block';
    }
  }
  return raw.map(normalizeArea);
}
