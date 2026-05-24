"""JavaScript module registration for the Rowenta Xplorer 120 integration.

Pattern from: https://gist.github.com/KipK/3cf706ac89573432803aaa2f5ca40492

Key rules:
  - Registration in async_setup (once per integration), NOT async_setup_entry
  - Static path maps the whole frontend/ directory
  - Wait for lovelace.resources.loaded before registering
  - URL includes ?v=VERSION to bust browser cache on updates
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

_URL_BASE = "/rowenta_roboeye"
_FRONTEND_DIR = Path(__file__).parent

_MODULES = [
    {"name": "Rowenta Map Card", "filename": "rowenta-map-card.js"},
]


class JSModuleRegistration:
    """Registers the map card JavaScript module in Home Assistant."""

    def __init__(self, hass: HomeAssistant, version: str) -> None:
        self.hass = hass
        self.version = version
        self.lovelace = self.hass.data.get("lovelace")

    async def async_register(self) -> None:
        """Register static path and Lovelace resources."""
        await self._async_register_path()
        if self.lovelace is None:
            _LOGGER.debug("RobEye frontend: lovelace not in hass.data, skipping resource registration")
            return
        # Only works in storage mode
        if getattr(self.lovelace, "mode", None) != "storage":
            _LOGGER.debug(
                "RobEye frontend: Lovelace is in %s mode — "
                "add resource manually: %s/rowenta-map-card.js",
                getattr(self.lovelace, "mode", "unknown"), _URL_BASE,
            )
            return
        await self._async_wait_for_resources()

    async def _async_register_path(self) -> None:
        """Register /rowenta_roboeye/ → frontend/ directory."""
        try:
            await self.hass.http.async_register_static_paths([
                StaticPathConfig(_URL_BASE, str(_FRONTEND_DIR), False)
            ])
            _LOGGER.debug("RobEye frontend: static path registered: %s → %s", _URL_BASE, _FRONTEND_DIR)
        except RuntimeError:
            _LOGGER.debug("RobEye frontend: static path already registered")

    async def _async_wait_for_resources(self) -> None:
        """Wait until lovelace.resources is loaded, then register modules."""
        async def _try(_now: Any = None) -> None:
            resources = getattr(self.lovelace, "resources", None)
            if resources is None:
                _LOGGER.debug("RobEye frontend: resources not available yet, retry in 5s")
                async_call_later(self.hass, 5, _try)
                return
            if not getattr(resources, "loaded", True):
                _LOGGER.debug("RobEye frontend: resources not loaded yet, retry in 5s")
                async_call_later(self.hass, 5, _try)
                return
            await self._async_register_modules(resources)

        await _try()

    async def _async_register_modules(self, resources: Any) -> None:
        """Register or update each JS module in Lovelace resources."""
        existing = [
            r for r in resources.async_items()
            if r.get("url", "").startswith(_URL_BASE)
        ]

        for module in _MODULES:
            base_url = f"{_URL_BASE}/{module['filename']}"
            versioned_url = f"{base_url}?v={self.version}"

            # Check if already registered
            match = next(
                (r for r in existing if r.get("url", "").split("?")[0] == base_url),
                None
            )

            if match is None:
                # Not registered yet — create
                _LOGGER.info("RobEye frontend: registering %s v%s", module["name"], self.version)
                try:
                    await resources.async_create_item({
                        "res_type": "module",
                        "url": versioned_url,
                    })
                except Exception as err:
                    _LOGGER.warning("RobEye frontend: create_item failed: %s", err)
            else:
                current_ver = match.get("url", "").split("?v=")[-1]
                if current_ver != self.version:
                    # Version changed — update URL to bust browser cache
                    _LOGGER.info(
                        "RobEye frontend: updating %s %s → %s",
                        module["name"], current_ver, self.version,
                    )
                    try:
                        await resources.async_update_item(
                            match["id"],
                            {"res_type": "module", "url": versioned_url},
                        )
                    except Exception as err:
                        _LOGGER.warning("RobEye frontend: update_item failed: %s", err)
                else:
                    _LOGGER.debug("RobEye frontend: %s already up to date", module["name"])

    async def async_unregister(self) -> None:
        """Remove our resources when integration is unloaded."""
        resources = getattr(self.lovelace, "resources", None)
        if resources is None:
            return
        for r in list(resources.async_items()):
            if r.get("url", "").startswith(_URL_BASE):
                try:
                    await resources.async_delete_item(r["id"])
                    _LOGGER.info("RobEye frontend: removed resource %s", r.get("url"))
                except Exception as err:
                    _LOGGER.debug("RobEye frontend: delete_item failed: %s", err)
