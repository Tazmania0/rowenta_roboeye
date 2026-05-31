"""Config flow for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import AuthFailed, CannotConnect, RobEyeApiClient
from .const import (
    CONF_HOSTNAME,
    CONF_HTTP_PASSWORD,
    CONF_LAST_ACTIVE_MAP,
    CONF_MAP_ID,
    CONF_NAME,
    CONF_SERIAL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_MAP_ID,
    DEFAULT_PORT,
    DOMAIN,
    LOGGER,
    validate_http_password,
)


class RobEyeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rowenta RobEye."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the config flow."""
        self._host: str = ""
        self._hostname: str = ""
        self._serial: str = ""

    # ------------------------------------------------------------------
    # Step 1a — Manual IP entry (fallback when mDNS unavailable)
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host: str = user_input[CONF_HOST].strip()
            name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME
            http_password: str = user_input.get(CONF_HTTP_PASSWORD, "").strip()

            if not validate_http_password(http_password):
                errors["base"] = "invalid_password"
            else:
                try:
                    await self._test_connection(host, http_password)
                except AuthFailed:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Unexpected error during connection test")
                    errors["base"] = "unknown"
                else:
                    serial = await self._fetch_serial(host, http_password)
                    # Dedupe against an existing entry that predates serial-based
                    # unique_ids (its unique_id is still the IP or hostname).
                    if (legacy := self._legacy_entry_abort(serial, [host], CONF_HOST, host)):
                        return legacy
                    # Serial-less robot: its unique_id will be the IP, which never
                    # matches a zeroconf entry's hostname-based id, so dedupe on the
                    # stored host instead to avoid a duplicate entry. The IP itself
                    # is left to _abort_if_unique_id_configured below.
                    if not serial and (
                        dup := self._abort_if_host_configured(host, skip_uids={host})
                    ):
                        return dup
                    # Prefer the device serial as unique_id so the same robot can't
                    # be added twice (and dedupes against a zeroconf-discovered
                    # entry). Fall back to the IP only when the serial is unknown.
                    await self.async_set_unique_id(serial or host)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: host, CONF_HTTP_PASSWORD: http_password}
                    )
                    return self.async_create_entry(
                        title=f"{name} ({host})",
                        data={
                            CONF_HOST: host,
                            CONF_HOSTNAME: host,
                            CONF_NAME: name,
                            CONF_SERIAL: serial,
                            CONF_HTTP_PASSWORD: http_password,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Optional(CONF_NAME, default=DEFAULT_DEVICE_NAME): cv.string,
                    vol.Optional(CONF_HTTP_PASSWORD, default=""): cv.string,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 1b — mDNS / Zeroconf auto-discovery (preferred path)
    # ------------------------------------------------------------------

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a discovered RobEye device on the local network."""
        LOGGER.debug("Zeroconf discovery_info: %s", discovery_info)

        self._host = discovery_info.host
        self._hostname = discovery_info.hostname or ""

        LOGGER.debug("Zeroconf host: %s  hostname: %s", self._host, self._hostname)

        if not self._hostname and not self._host:
            return self.async_abort(reason="no_hostname")

        # Provisional unique_id from the stable mDNS hostname (survives DHCP IP
        # changes).  Normalize: strip trailing dot and lowercase so announcements
        # differing only in case/trailing dot map to the same device.  This dedupes
        # repeated discovery flows before we incur a network round-trip below.
        provisional_uid = (self._hostname or self._host).rstrip(".").lower()
        await self.async_set_unique_id(provisional_uid)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host}  # silently update IP if hostname matches
        )

        # Verify the REST API is reachable at the discovered IP
        try:
            await self._test_connection(self._host)
        except AuthFailed:
            # Robot has lock_http enabled — proceed to the confirm step where the
            # user can enter the password, rather than aborting discovery.
            LOGGER.debug("Zeroconf: robot requires HTTP password, prompting at confirm")
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")

        # Prefer the device serial as the final unique_id so a manual entry and a
        # zeroconf discovery for the same robot dedupe against each other.  Keep
        # the hostname-based id when the serial can't be read.
        self._serial = await self._fetch_serial(self._host)
        if self._serial and self._serial != provisional_uid:
            # Dedupe against a legacy entry whose unique_id is still the IP /
            # hostname (the provisional hostname check above only catches a
            # legacy zeroconf entry, not a manually-added one keyed by IP).
            if (legacy := self._legacy_entry_abort(
                self._serial, [self._host, provisional_uid], CONF_HOST, self._host
            )):
                return legacy
            await self.async_set_unique_id(self._serial)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        elif not self._serial:
            # Serial-less robot: the hostname-based provisional id (already
            # checked above) won't match a manually-added entry keyed by IP, so
            # dedupe on the stored host too.
            if (dup := self._abort_if_host_configured(
                self._host, self._hostname, skip_uids={provisional_uid}
            )):
                return dup

        self.context.update(
            {
                "title_placeholders": {"host": self._host},
                "configuration_url": f"http://{self._host}:{DEFAULT_PORT}",
            }
        )

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirmation step for the auto-discovered device — lets user set a name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            http_password: str = user_input.get(CONF_HTTP_PASSWORD, "").strip()
            if not validate_http_password(http_password):
                errors["base"] = "invalid_password"
            else:
                # When a password is supplied, verify it against the robot before
                # saving — otherwise a wrong password is accepted here and only
                # surfaces as a re-auth prompt after setup (the manual/options
                # flows validate the same way).
                try:
                    if http_password:
                        await self._test_connection(self._host, http_password)
                except AuthFailed:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Unexpected error during zeroconf confirm test")
                    errors["base"] = "unknown"
                else:
                    name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME
                    serial = await self._fetch_serial(self._host, http_password) or self._serial
                    if serial and serial != self._serial:
                        await self.async_set_unique_id(serial)
                        self._abort_if_unique_id_configured(
                            updates={
                                CONF_HOST: self._host,
                                CONF_HTTP_PASSWORD: http_password,
                            }
                        )
                    return self.async_create_entry(
                        title=f"{name} ({self._host})",
                        data={
                            CONF_HOST: self._host,
                            CONF_HOSTNAME: self._hostname or self._host,
                            CONF_NAME: name,
                            CONF_SERIAL: serial,
                            CONF_HTTP_PASSWORD: http_password,
                        },
                    )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"host": self._host},
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=DEFAULT_DEVICE_NAME): cv.string,
                    vol.Optional(CONF_HTTP_PASSWORD, default=""): cv.string,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Re-auth — triggered when the robot starts returning HTTP 401
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start the re-auth flow (HTTP password missing or wrong)."""
        self._host = entry_data.get(CONF_HOST, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for the 8-character HTTP password and update the entry."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None and entry is not None:
            http_password: str = user_input.get(CONF_HTTP_PASSWORD, "").strip()
            host = entry.data.get(CONF_HOST, self._host)
            if not validate_http_password(http_password) or not http_password:
                errors["base"] = "invalid_password"
            else:
                try:
                    await self._test_connection(host, http_password)
                except AuthFailed:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, CONF_HTTP_PASSWORD: http_password},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_HTTP_PASSWORD, default=""): cv.string}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Options flow — lets user update IP, map_id, and name without re-adding
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return RobEyeOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @callback
    def _legacy_entry_abort(self, serial, legacy_uids, host_key, host):
        """Dedupe against a pre-serial entry keyed by IP/hostname."""
        if not serial:
            return None
        candidates = {str(u).strip().lower() for u in legacy_uids if u}
        for entry in self._async_current_entries():
            existing_uid = (entry.unique_id or "").strip().lower()
            if existing_uid and existing_uid in candidates:
                self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=serial,
                    data={**entry.data, host_key: host},
                )
                return self.async_abort(reason="already_configured")
        return None

    @callback
    def _abort_if_host_configured(self, host, hostname="", skip_uids=None):
        """Abort if an existing entry already targets this host/hostname.

        Fallback dedupe for serial-less robots, whose unique_id is the IP
        (manual) or mDNS hostname (zeroconf): those two ids never match each
        other, so without this a robot discovered both ways would be added
        twice.  Matches on stored CONF_HOST / CONF_HOSTNAME instead of
        unique_id and refreshes the stored IP on the matched entry.

        ``skip_uids`` lists the unique_ids the standard
        ``_abort_if_unique_id_configured`` path already handles (the IP for the
        manual flow, the hostname for zeroconf); entries with those ids are
        skipped here so this only catches the cross-mechanism case.
        """
        candidates = {str(h).strip().lower() for h in (host, hostname) if h}
        if not candidates:
            return None
        skip = {str(u).strip().lower() for u in (skip_uids or set()) if u}
        for entry in self._async_current_entries():
            existing_uid = (entry.unique_id or "").strip().lower()
            if existing_uid and existing_uid in skip:
                continue
            stored = {
                str(entry.data.get(CONF_HOST, "")).strip().lower(),
                str(entry.data.get(CONF_HOSTNAME, "")).strip().lower(),
            } - {""}
            if candidates & stored:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: host}
                )
                return self.async_abort(reason="already_configured")
        return None

    async def _test_connection(self, host: str, http_password: str = "") -> None:
        """Verify connectivity; raises CannotConnect (or AuthFailed) on failure."""
        client = RobEyeApiClient(host=host, http_password=http_password)
        await client.test_connection()

    async def _fetch_serial(self, host: str, http_password: str = "") -> str:
        """Return normalised serial number from robot, empty string on any failure."""
        try:
            client = RobEyeApiClient(host=host, http_password=http_password)
            data = await client.get_robot_id()
            raw = (
                data.get("unique_id")
                or data.get("serial_number")
                or data.get("robot_id")
                or data.get("id")
                or ""
            )
            return str(raw).lower().replace("-", "_").replace(" ", "_") if raw else ""
        except Exception:  # noqa: BLE001
            return ""


class RobEyeOptionsFlow(OptionsFlow):
    """Allow updating IP address and name without removing the integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options update."""
        errors: dict[str, str] = {}

        current_host = self._config_entry.data.get(CONF_HOST, "")
        current_name = self._config_entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME)
        current_password = self._config_entry.data.get(CONF_HTTP_PASSWORD, "")

        if user_input is not None:
            host: str = user_input[CONF_HOST].strip()
            name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME
            http_password: str = user_input.get(CONF_HTTP_PASSWORD, "").strip()
            if not validate_http_password(http_password):
                errors["base"] = "invalid_password"
            else:
                try:
                    client = RobEyeApiClient(host=host, http_password=http_password)
                    await client.test_connection()
                except AuthFailed:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                else:
                    new_data = {
                        CONF_HOST: host,
                        CONF_HOSTNAME: self._config_entry.data.get(CONF_HOSTNAME, host),
                        CONF_NAME: name,
                        CONF_HTTP_PASSWORD: http_password,
                        # Preserve stable identifiers so entity unique_ids never change
                        CONF_SERIAL: self._config_entry.data.get(CONF_SERIAL, ""),
                        "_device_id": self._config_entry.data.get("_device_id", ""),
                        # Preserve map selection so the active map survives options saves
                        CONF_MAP_ID: self._config_entry.data.get(CONF_MAP_ID, DEFAULT_MAP_ID),
                        CONF_LAST_ACTIVE_MAP: self._config_entry.data.get(CONF_LAST_ACTIVE_MAP),
                    }
                    # Write to entry.data (where async_setup_entry reads host +
                    # http_password); async_create_entry alone would only update
                    # entry.options, which setup never reads. The update fires
                    # _async_update_listener, which reloads on host/password change.
                    self.hass.config_entries.async_update_entry(
                        self._config_entry, data=new_data
                    )
                    return self.async_create_entry(
                        title=f"{name} ({host})", data=new_data
                    )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current_host): cv.string,
                    vol.Optional(CONF_NAME, default=current_name): cv.string,
                    vol.Optional(CONF_HTTP_PASSWORD, default=current_password): cv.string,
                }
            ),
            errors=errors,
        )
