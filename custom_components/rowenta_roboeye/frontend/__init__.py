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
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

_URL_BASE = "/rowenta_roboeye"
_FRONTEND_DIR = Path(__file__).parent

# Matches the card's own `const VERSION = "x.y.z";` declaration so the cache-bust
# query string tracks the card file, not the integration version.  A card-only
# edit that bumps this constant therefore reaches browsers without needing a
# manifest/const.py version bump.
_CARD_VERSION_RE = re.compile(r'const\s+VERSION\s*=\s*["\']([^"\']+)["\']')


def _read_module_version(filename: str, fallback: str) -> str:
    """Extract the `const VERSION` from a frontend module file.

    Runs in an executor (blocking file read).  Returns ``fallback`` when the
    file is missing or has no recognisable version declaration.
    """
    path = _FRONTEND_DIR / filename
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return fallback
    match = _CARD_VERSION_RE.search(text)
    return match.group(1) if match else fallback


def _version_from_url(url: str) -> str | None:
    """Return the ``v`` query parameter of a resource URL, or None if absent.

    Robust against URLs registered without a ``?v=`` (the old naive
    ``split('?v=')[-1]`` returned the whole URL in that case, forcing a
    perpetual "update" on every setup).
    """
    return parse_qs(urlsplit(url).query).get("v", [None])[0]

_MODULES = [
    {"name": "Rowenta Map Card", "filename": "rowenta-map-card.js"},
]


class JSModuleRegistration:
    """Registers the map card JavaScript module in Home Assistant."""

    # Retry cadence for "lovelace/resources not ready yet" — bounded so a
    # permanently-missing Lovelace store does not reschedule forever.
    _RETRY_INTERVAL_S = 5
    _MAX_RETRIES = 60  # ~5 minutes

    def __init__(self, hass: HomeAssistant, version: str) -> None:
        self.hass = hass
        self.version = version
        self.lovelace: Any = None
        self._retry_count = 0
        # Unsub handle for the pending async_call_later retry, so it can be
        # cancelled on unregister instead of firing into a torn-down integration.
        self._cancel_retry: Any = None

    def _schedule_retry(self, action) -> None:
        """Schedule one bounded retry of ``action`` via async_call_later.

        ``action`` is an async callable taking an optional ``_now`` arg.
        Stops (logging a warning) once _MAX_RETRIES is exceeded.
        """
        if self._retry_count >= self._MAX_RETRIES:
            _LOGGER.warning(
                "RobEye frontend: Lovelace not ready after %d retries — giving up; "
                "reload the integration once Lovelace is available",
                self._MAX_RETRIES,
            )
            return
        self._retry_count += 1

        def _fire(_now: Any) -> None:
            self._cancel_retry = None
            self.hass.async_create_task(action())

        self._cancel_retry = async_call_later(
            self.hass, self._RETRY_INTERVAL_S, _fire
        )

    async def async_register(self) -> None:
        """Register static path and Lovelace resources."""
        await self._async_register_path()
        await self._async_retry_register_resources()

    async def _async_retry_register_resources(self) -> None:
        """Attempt Lovelace resource registration, retrying if lovelace is not ready."""
        self.lovelace = self.hass.data.get("lovelace")
        if self.lovelace is None:
            _LOGGER.debug("RobEye frontend: lovelace not in hass.data, retry in 5s")
            self._schedule_retry(self._async_retry_register_resources)
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
                self._schedule_retry(_try)
                return
            if not getattr(resources, "loaded", True):
                _LOGGER.debug("RobEye frontend: resources not loaded yet, retry in 5s")
                self._schedule_retry(_try)
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
            # Cache-bust on the card file's own version so card-only edits reach
            # browsers; fall back to the integration version if it can't be read.
            module_version = await self.hass.async_add_executor_job(
                _read_module_version, module["filename"], self.version
            )
            versioned_url = f"{base_url}?v={module_version}"

            # Check if already registered
            match = next(
                (r for r in existing if r.get("url", "").split("?")[0] == base_url),
                None
            )

            if match is None:
                # Not registered yet — create
                _LOGGER.info("RobEye frontend: registering %s v%s", module["name"], module_version)
                try:
                    await resources.async_create_item({
                        "res_type": "module",
                        "url": versioned_url,
                    })
                except Exception as err:
                    _LOGGER.warning("RobEye frontend: create_item failed: %s", err)
            else:
                current_ver = _version_from_url(match.get("url", ""))
                if current_ver != module_version:
                    # Version changed — update URL to bust browser cache
                    _LOGGER.info(
                        "RobEye frontend: updating %s %s → %s",
                        module["name"], current_ver, module_version,
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
        # Cancel any pending "lovelace not ready" retry so it does not fire
        # into a torn-down integration.
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None
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
