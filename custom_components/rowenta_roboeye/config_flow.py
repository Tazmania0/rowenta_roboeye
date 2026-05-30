"""Config flow for the Rowenta Xplorer 120 (RobEye) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import CannotConnect, RobEyeApiClient
from .const import CONF_HOSTNAME, CONF_LAST_ACTIVE_MAP, CONF_MAP_ID, CONF_NAME, CONF_SERIAL, DEFAULT_DEVICE_NAME, DEFAULT_MAP_ID, DEFAULT_PORT, DOMAIN, LOGGER


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

            try:
                await self._test_connection(host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            else:
                serial = await self._fetch_serial(host)
                # Prefer the device serial as unique_id so the same robot can't
                # be added twice (and dedupes against a zeroconf-discovered
                # entry).  Fall back to the IP only when the serial is unknown.
                await self.async_set_unique_id(serial or host)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=f"{name} ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_HOSTNAME: host,
                        CONF_NAME: name,
                        CONF_SERIAL: serial,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Optional(CONF_NAME, default=DEFAULT_DEVICE_NAME): cv.string,
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
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")

        # Prefer the device serial as the final unique_id so a manual entry and a
        # zeroconf discovery for the same robot dedupe against each other.  Keep
        # the hostname-based id when the serial can't be read.
        self._serial = await self._fetch_serial(self._host)
        if self._serial and self._serial != provisional_uid:
            await self.async_set_unique_id(self._serial)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

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
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                description_placeholders={"host": self._host},
                data_schema=vol.Schema(
                    {
                        vol.Optional(CONF_NAME, default=DEFAULT_DEVICE_NAME): cv.string,
                    }
                ),
            )
        name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME
        # Serial was already fetched in async_step_zeroconf; reuse it.
        return self.async_create_entry(
            title=f"{name} ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_HOSTNAME: self._hostname or self._host,
                CONF_NAME: name,
                CONF_SERIAL: self._serial,
            },
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

    async def _test_connection(self, host: str) -> None:
        """Verify connectivity; raises CannotConnect on failure."""
        client = RobEyeApiClient(host=host)
        await client.test_connection()

    async def _fetch_serial(self, host: str) -> str:
        """Return normalised serial number from robot, empty string on any failure."""
        try:
            client = RobEyeApiClient(host=host)
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

        if user_input is not None:
            host: str = user_input[CONF_HOST].strip()
            name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME
            try:
                client = RobEyeApiClient(host=host)
                await client.test_connection()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"{name} ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_HOSTNAME: self._config_entry.data.get(CONF_HOSTNAME, host),
                        CONF_NAME: name,
                        # Preserve stable identifiers so entity unique_ids never change
                        CONF_SERIAL: self._config_entry.data.get(CONF_SERIAL, ""),
                        "_device_id": self._config_entry.data.get("_device_id", ""),
                        # Preserve map selection so the active map survives options saves
                        CONF_MAP_ID: self._config_entry.data.get(CONF_MAP_ID, DEFAULT_MAP_ID),
                        CONF_LAST_ACTIVE_MAP: self._config_entry.data.get(CONF_LAST_ACTIVE_MAP),
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current_host): cv.string,
                    vol.Optional(CONF_NAME, default=current_name): cv.string,
                }
            ),
            errors=errors,
        )
