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

export const ROOM_TYPES = [
  { value: 'none',         label: '— None —'           },
  { value: 'living',       label: 'Living Room'         },
  { value: 'sleeping',     label: 'Bedroom'             },
  { value: 'bed',          label: 'Bedroom (alt)'       },
  { value: 'kids',         label: 'Kids Room'           },
  { value: 'play_room',    label: 'Play Room'           },
  { value: 'study',        label: 'Study'               },
  { value: 'office',       label: 'Office'              },
  { value: 'kitchen',      label: 'Kitchen'             },
  { value: 'dining',       label: 'Dining Room'         },
  { value: 'dining_table', label: 'Dining Table'        },
  { value: 'bath',         label: 'Bathroom'            },
  { value: 'lavatory',     label: 'Lavatory / WC'       },
  { value: 'laundry_room', label: 'Laundry Room'        },
  { value: 'storage',      label: 'Storage'             },
  { value: 'garage',       label: 'Garage'              },
  { value: 'basement',     label: 'Basement'            },
  { value: 'corridor',     label: 'Corridor'            },
  { value: 'hallway',      label: 'Hallway'             },
  { value: 'couch',        label: 'Couch / Sofa'        },
  { value: 'armchair',     label: 'Armchair'            },
  { value: 'chair',        label: 'Chair'               },
  { value: 'desk',         label: 'Desk'                },
  { value: 'coffee_table', label: 'Coffee Table'        },
  { value: 'stool',        label: 'Stool'               },
  { value: 'garderobe',    label: 'Wardrobe'            },
  { value: 'pet_area',     label: 'Pet Area'            },
  { value: 'flower',       label: 'Plant / Flower'      },
  { value: 'lamp',         label: 'Lamp Area'           },
  { value: 'cables',       label: 'Cables Area'         },
  { value: 'toys',         label: 'Toys Area'           },
];

// Keep ROOM_TYPE_OPTIONS as alias for backward compat (used in explore.js _startNamingWizard)
export const ROOM_TYPE_OPTIONS = ROOM_TYPES;

export const FLOOR_TYPES = [
  { value: 'none',            label: 'Default'           },
  { value: 'hard_wood',       label: 'Hard Floor'        },
  { value: 'low_pile_carpet', label: 'Short Carpet'      },
  { value: 'carpet',          label: 'Long Carpet'       },
  { value: 'tiles',           label: 'Tiles'             },
];

export const STRATEGY_MODES = [
  { value: 'normal', label: 'Normal'     },
  { value: 'deep',   label: 'Deep Clean' },
];

export const FAN_SPEEDS = [
  { value: '0', label: 'Default (per-room)' },
  { value: '2', label: 'Eco'                },
  { value: '3', label: 'Normal'             },
  { value: '1', label: 'Silent'             },
  { value: '4', label: 'Super Silent'       },
];

export const EXPLORE_TIMEOUT    = 10 * 60 * 1000;
export const SAVE_MAP_TIMEOUT   = 60 * 1000;
export const SPLIT_TIMEOUT      = 30 * 1000;
export const MERGE_TIMEOUT      = 30 * 1000;
export const CMD_POLL_INTERVAL  = 5000;
