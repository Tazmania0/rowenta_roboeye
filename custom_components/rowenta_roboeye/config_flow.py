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
from .const import CONF_HOSTNAME, CONF_MAP_ID, CONF_NAME, DEFAULT_DEVICE_NAME, DEFAULT_MAP_ID, DOMAIN, LOGGER


class RobEyeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rowenta RobEye."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the config flow."""
        self._host: str = ""
        self._hostname: str = ""
        self._map_id: str = DEFAULT_MAP_ID

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
            map_id: str = user_input[CONF_MAP_ID].strip()
            name: str = user_input.get(CONF_NAME, DEFAULT_DEVICE_NAME).strip() or DEFAULT_DEVICE_NAME

            try:
                await self._test_connection(host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            else:
                # Use IP as unique_id for manual setup
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{name} ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_HOSTNAME: host,
                        CONF_MAP_ID: map_id,
                        CONF_NAME: name,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_MAP_ID, default=DEFAULT_MAP_ID): cv.string,
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
        self._hostname = discovery_info.hostname

        LOGGER.debug("Zeroconf host: %s  hostname: %s", self._host, self._hostname)

        # Use stable mDNS hostname as unique_id — survives DHCP IP changes
        await self.async_set_unique_id(self._hostname)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host}  # silently update IP if hostname matches
        )

        # Verify the REST API is reachable at the discovered IP
        try:
            await self._test_connection(self._host)
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")

        self.context.update(
            {
                "title_placeholders": {"host": self._host},
                "configuration_url": f"http://{self._host}:{8080}",
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
        return self.async_create_entry(
            title=f"{name} ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_HOSTNAME: self._hostname,
                CONF_MAP_ID: self._map_id,
                CONF_NAME: name,
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
        client = RobEyeApiClient(host=host)
        await client.test_connection()


class RobEyeOptionsFlow(OptionsFlow):
    """Allow updating IP address, map ID, and name without removing the integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options update."""
        errors: dict[str, str] = {}

        current_host = self._config_entry.data.get(CONF_HOST, "")
        current_map_id = self._config_entry.data.get(CONF_MAP_ID, DEFAULT_MAP_ID)
        current_name = self._config_entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME)

        if user_input is not None:
            host: str = user_input[CONF_HOST].strip()
            map_id: str = user_input[CONF_MAP_ID].strip()
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
                        CONF_MAP_ID: map_id,
                        CONF_NAME: name,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current_host): cv.string,
                    vol.Required(CONF_MAP_ID, default=current_map_id): cv.string,
                    vol.Optional(CONF_NAME, default=current_name): cv.string,
                }
            ),
            errors=errors,
        )
