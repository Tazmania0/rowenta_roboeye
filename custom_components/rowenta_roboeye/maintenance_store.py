"""Persistent maintenance counter storage for the Rowenta RobEye integration.

The Robart firmware exposes no consumable counters (no brush hours, no filter
lifetime, no reset endpoint).  Maintenance tracking is therefore implemented
entirely in HA using delta tracking against the monotonically-increasing
``/get/statistics`` lifetime totals::

    runtime_since_reset = stats.total_cleaning_time - baseline_at_reset
    area_since_reset    = stats.total_area_cleaned  - baseline_at_reset

(``/get/permanent_statistics`` was the original source but is partial — it omits
``total_area_cleaned`` — and does not reliably increment on Xplorer 120 firmware,
so ``/get/statistics`` is used instead, matching the lifetime sensors.)

Baselines are stored in HA persistent storage keyed on the robot's stable
``device_id`` so they survive an integration remove + re-add (as long as the
``device_id`` does not change).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    LOGGER,
    MAINT_AREA_UNITS_PER_M2,
    MAINT_TIME_UNITS_PER_HOUR,
)

STORAGE_VERSION = 1

# All time values stored in seconds (raw total_cleaning_time), area in the raw
# total_area_cleaned unit (mm²) — converted to h / m² only on read.
DEFAULT_DATA: dict[str, object] = {
    # Replacement baselines (runtime since last replacement)
    "main_brush_replace_baseline_s": 0,
    "side_brush_replace_baseline_s": 0,
    "mop_pad_replace_baseline_s": 0,

    # Cleaning baselines (area since last clean)
    "main_brush_clean_baseline_mm2": 0,
    "main_brush_clean_baseline_s": 0,
    "side_brush_clean_baseline_mm2": 0,
    "side_brush_clean_baseline_s": 0,
    "dustbin_clean_baseline_mm2": 0,
    "dustbin_clean_baseline_s": 0,
    "filter_clean_baseline_mm2": 0,
    "filter_clean_baseline_s": 0,
    "drop_sensor_clean_baseline_mm2": 0,

    # Reset timestamps (component -> ISO string)
    "last_reset": {},
}


class MaintenanceStore:
    """Persistent store for maintenance counters."""

    def __init__(self, hass: HomeAssistant, robot_unique_id: str) -> None:
        # Key includes the stable device_id — survives config entry recreation.
        key = f"rowenta_roboeye.maintenance.{str(robot_unique_id).replace('-', '_')}"
        self._store = Store(hass, STORAGE_VERSION, key)
        self._data: dict = {}
        # True when async_load() found no persisted data — i.e. a brand-new
        # install with no prior baselines.  Lets the coordinator seed baselines
        # from current lifetime totals so an already-used robot doesn't report
        # its entire history as "consumed since last reset".
        self.is_new: bool = False

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        self.is_new = not loaded
        # Deep copy so nested mutable defaults (e.g. ``last_reset``) are never
        # shared with the module-level DEFAULT_DATA — async_reset() mutates them
        # in place, which would otherwise leak across robots/stores.
        self._data = loaded if loaded else copy.deepcopy(DEFAULT_DATA)
        # Ensure all default keys exist (migration safety for older stores).
        for k, v in DEFAULT_DATA.items():
            self._data.setdefault(k, copy.deepcopy(v))

    async def async_seed_baselines(
        self, current_total_s: int, current_total_mm2: int
    ) -> None:
        """Set every baseline to the current lifetime totals.

        Called once for a brand-new store so a robot that already has runtime /
        area on the clock starts every maintenance counter at zero "since reset"
        instead of immediately reporting (and alerting) as overdue.
        """
        for key in DEFAULT_DATA:
            if key.endswith("_baseline_s"):
                self._data[key] = current_total_s
            elif key.endswith("_baseline_mm2"):
                self._data[key] = current_total_mm2
        await self.async_save()
        LOGGER.info(
            "Maintenance baselines seeded from current totals (s=%s, mm2=%s)",
            current_total_s, current_total_mm2,
        )

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    def get(self, key: str) -> int | float:
        return self._data.get(key, 0)

    async def async_reset(
        self,
        component: str,
        current_total_s: int,
        current_total_mm2: int,
    ) -> None:
        """Record a maintenance action for ``component``.

        ``component`` is one of:
            main_brush_replace, side_brush_replace, mop_pad_replace,
            main_brush_clean, side_brush_clean,
            dustbin_clean, filter_clean, drop_sensor_clean
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        if component.endswith("_replace"):
            base = component[: -len("_replace")]
            self._data[f"{base}_replace_baseline_s"] = current_total_s

        elif component.endswith("_clean"):
            base = component[: -len("_clean")]
            self._data[f"{base}_clean_baseline_mm2"] = current_total_mm2
            self._data[f"{base}_clean_baseline_s"] = current_total_s

        self._data.setdefault("last_reset", {})[component] = now_iso
        await self.async_save()
        LOGGER.info("Maintenance reset: %s at %s", component, now_iso)

    def runtime_since_replace_h(self, component: str, current_total_s: int) -> float:
        """Hours of runtime since the last replacement of ``component``."""
        baseline = self._data.get(f"{component}_replace_baseline_s", 0)
        return max(0.0, (current_total_s - baseline) / MAINT_TIME_UNITS_PER_HOUR)

    def area_since_clean_m2(self, component: str, current_total_mm2: int) -> float:
        """m² cleaned since the last cleaning action on ``component``."""
        baseline = self._data.get(f"{component}_clean_baseline_mm2", 0)
        return max(0.0, (current_total_mm2 - baseline) / MAINT_AREA_UNITS_PER_M2)

    def runtime_since_clean_h(self, component: str, current_total_s: int) -> float:
        """Hours of runtime since the last cleaning action for ``component``."""
        baseline = self._data.get(f"{component}_clean_baseline_s", 0)
        return max(0.0, (current_total_s - baseline) / MAINT_TIME_UNITS_PER_HOUR)

    def last_reset_iso(self, component: str) -> str | None:
        return self._data.get("last_reset", {}).get(component)
