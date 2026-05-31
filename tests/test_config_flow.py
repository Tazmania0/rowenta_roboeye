"""Unit tests for config flow — manual setup, mDNS discovery, options flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.rowenta_roboeye.api import AuthFailed, CannotConnect
from custom_components.rowenta_roboeye.config_flow import RobEyeConfigFlow, RobEyeOptionsFlow
from custom_components.rowenta_roboeye.const import CONF_HTTP_PASSWORD, DEFAULT_MAP_ID


def _make_flow():
    flow = RobEyeConfigFlow.__new__(RobEyeConfigFlow)
    flow.hass = MagicMock()
    flow._host = ""
    flow._hostname = ""
    flow._serial = ""
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    # No pre-existing entries by default — legacy dedup is a no-op.
    flow._async_current_entries = MagicMock(return_value=[])
    flow.async_create_entry = lambda title="", data=None, **kw: {
        "type": "create_entry", "title": title, "data": data or {}
    }
    flow.async_show_form = lambda step_id="", data_schema=None, errors=None, **kw: {
        "type": "form", "step_id": step_id, "errors": errors or {}
    }
    flow.async_abort = lambda reason="", **kw: {"type": "abort", "reason": reason}
    return flow


# ── Manual setup — happy path ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_step_success():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="sn_abc123")):
        result = await flow.async_step_user(
            {"host": "192.168.1.50"}
        )

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.50"
    assert result["data"]["serial"] == "sn_abc123"
    assert "map_id" not in result["data"]
    # unique_id prefers the device serial so the robot can't be added twice.
    flow.async_set_unique_id.assert_awaited_once_with("sn_abc123")


@pytest.mark.asyncio
async def test_user_step_unique_id_falls_back_to_host_without_serial():
    """When the serial can't be read, the IP is used as unique_id."""
    flow = _make_flow()
    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        await flow.async_step_user({"host": "192.168.1.50"})
    flow.async_set_unique_id.assert_awaited_once_with("192.168.1.50")


@pytest.mark.asyncio
async def test_user_step_serial_fetch_failure_still_creates_entry():
    """Serial fetch failure is non-fatal; entry is created with empty serial."""
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        result = await flow.async_step_user({"host": "192.168.1.50"})

    assert result["type"] == "create_entry"
    assert result["data"]["serial"] == ""


@pytest.mark.asyncio
async def test_user_step_dedupes_legacy_ip_entry():
    """Re-adding a robot whose existing entry is keyed by the legacy IP aborts
    and migrates that entry's unique_id to the serial."""
    flow = _make_flow()
    legacy = MagicMock()
    legacy.unique_id = "192.168.1.50"      # pre-serial entry keyed by IP
    legacy.data = {"host": "192.168.1.50"}
    flow._async_current_entries = MagicMock(return_value=[legacy])
    flow.hass.config_entries.async_update_entry = MagicMock()

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="sn_abc123")):
        result = await flow.async_step_user({"host": "192.168.1.50"})

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    # Legacy entry migrated to the serial unique_id.
    kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["unique_id"] == "sn_abc123"
    # No brand-new entry created on top of the existing one.
    flow.async_set_unique_id.assert_not_called()


@pytest.mark.asyncio
async def test_user_step_no_dedupe_when_serial_unknown():
    """With no serial, the legacy migration is skipped (falls back to IP id)."""
    flow = _make_flow()
    legacy = MagicMock()
    legacy.unique_id = "192.168.1.50"
    legacy.data = {"host": "192.168.1.50"}
    flow._async_current_entries = MagicMock(return_value=[legacy])
    flow.hass.config_entries.async_update_entry = MagicMock()

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        await flow.async_step_user({"host": "192.168.1.50"})

    flow.hass.config_entries.async_update_entry.assert_not_called()
    flow.async_set_unique_id.assert_awaited_once_with("192.168.1.50")


@pytest.mark.asyncio
async def test_user_step_cannot_connect():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=CannotConnect)):
        result = await flow.async_step_user(
            {"host": "192.168.1.50"}
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_step_unknown_error():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=Exception("boom"))):
        result = await flow.async_step_user(
            {"host": "192.168.1.50"}
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_user_step_shows_form_when_no_input():
    flow = _make_flow()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


# ── mDNS / Zeroconf discovery ─────────────────────────────────────────

def _mock_zeroconf_info(host="192.168.1.100", hostname="xplorer120.local."):
    info = MagicMock()
    info.host = host
    info.hostname = hostname
    return info


@pytest.mark.asyncio
async def test_zeroconf_happy_path():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="sn_zc")):
        result = await flow.async_step_zeroconf(_mock_zeroconf_info())

    # Should advance to confirmation form
    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"
    # Serial fetched during discovery is stashed for the confirm step.
    assert flow._serial == "sn_zc"


@pytest.mark.asyncio
async def test_zeroconf_prefers_serial_unique_id():
    """Discovery upgrades the unique_id from hostname to the device serial."""
    flow = _make_flow()
    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="sn_zc")):
        await flow.async_step_zeroconf(_mock_zeroconf_info())
    # Provisional hostname id first, then upgraded to the serial.
    uids = [c.args[0] for c in flow.async_set_unique_id.call_args_list]
    assert uids == ["xplorer120.local", "sn_zc"]


@pytest.mark.asyncio
async def test_zeroconf_aborts_without_hostname_or_host():
    flow = _make_flow()
    result = await flow.async_step_zeroconf(_mock_zeroconf_info(host="", hostname=""))
    assert result["type"] == "abort"
    assert result["reason"] == "no_hostname"


@pytest.mark.asyncio
async def test_zeroconf_cannot_connect_aborts():
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=CannotConnect)), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        result = await flow.async_step_zeroconf(_mock_zeroconf_info())

    assert result["type"] == "abort"
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_zeroconf_auth_failed_proceeds_to_confirm():
    """A locked AICU robot (401) must reach the confirm step to enter a password,
    not abort discovery."""
    flow = _make_flow()

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=AuthFailed)):
        result = await flow.async_step_zeroconf(_mock_zeroconf_info())

    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"


@pytest.mark.asyncio
async def test_zeroconf_confirm_creates_entry():
    flow = _make_flow()
    flow._host = "192.168.1.100"
    flow._hostname = "xplorer120.local."
    flow._serial = "sn_xyz"  # already fetched during async_step_zeroconf

    result = await flow.async_step_zeroconf_confirm(user_input={})

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.100"
    assert result["data"]["hostname"] == "xplorer120.local."
    assert result["data"]["serial"] == "sn_xyz"
    assert "map_id" not in result["data"]


@pytest.mark.asyncio
async def test_zeroconf_confirm_bad_password_shows_invalid_auth():
    """A wrong password entered at the confirm step is validated against the
    robot and rejected, rather than saved and failing later at setup."""
    flow = _make_flow()
    flow._host = "192.168.1.100"
    flow._hostname = "xplorer120.local."

    with patch.object(flow, "_test_connection", new=AsyncMock(side_effect=AuthFailed)):
        result = await flow.async_step_zeroconf_confirm(
            user_input={"http_password": "wrongpwd"}
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_zeroconf_confirm_good_password_creates_entry():
    flow = _make_flow()
    flow._host = "192.168.1.100"
    flow._hostname = "xplorer120.local."

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="sn_xyz")):
        result = await flow.async_step_zeroconf_confirm(
            user_input={"http_password": "abcd1234"}
        )

    assert result["type"] == "create_entry"
    assert result["data"]["http_password"] == "abcd1234"
    assert result["data"]["serial"] == "sn_xyz"


@pytest.mark.asyncio
async def test_zeroconf_confirm_shows_form_without_input():
    flow = _make_flow()
    flow._host = "192.168.1.100"

    result = await flow.async_step_zeroconf_confirm(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"


@pytest.mark.asyncio
async def test_zeroconf_unique_id_strips_trailing_dot():
    """Trailing dot is stripped from hostname before setting the provisional unique_id."""
    flow = _make_flow()
    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        await flow.async_step_zeroconf(_mock_zeroconf_info(hostname="xplorer120.local."))
    # No serial → only the provisional hostname id is set.
    flow.async_set_unique_id.assert_called_once_with("xplorer120.local")


@pytest.mark.asyncio
async def test_zeroconf_unique_id_lowercased():
    """Hostname is lowercased so case variants produce the same unique_id."""
    flow = _make_flow()
    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        await flow.async_step_zeroconf(_mock_zeroconf_info(hostname="Xplorer120.Local."))
    flow.async_set_unique_id.assert_called_once_with("xplorer120.local")


@pytest.mark.asyncio
async def test_zeroconf_unique_id_no_trailing_dot_passthrough():
    """Hostname without trailing dot is still normalised to lowercase."""
    flow = _make_flow()
    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        await flow.async_step_zeroconf(_mock_zeroconf_info(hostname="xplorer120.local"))
    flow.async_set_unique_id.assert_called_once_with("xplorer120.local")


@pytest.mark.asyncio
async def test_zeroconf_updates_ip_for_existing_hostname():
    """If mDNS re-announces same hostname with new IP, entry IP is updated silently."""
    flow = _make_flow()
    flow._abort_if_unique_id_configured = MagicMock(side_effect=Exception("already_configured"))

    with patch.object(flow, "_test_connection", new=AsyncMock()), \
         patch.object(flow, "_fetch_serial", new=AsyncMock(return_value="")):
        try:
            await flow.async_step_zeroconf(_mock_zeroconf_info(host="192.168.1.200"))
        except Exception:
            pass

    # _abort_if_unique_id_configured was called with updates dict containing new IP
    call_kwargs = flow._abort_if_unique_id_configured.call_args[1]
    assert call_kwargs["updates"]["host"] == "192.168.1.200"


# ── Options flow ──────────────────────────────────────────────────────

def _make_options_flow(host="192.168.1.100", serial="sn_persisted"):
    entry = MagicMock()
    entry.data = {"host": host, "hostname": "xplorer120.local.", "serial": serial}
    flow = object.__new__(RobEyeOptionsFlow)
    object.__setattr__(flow, "_config_entry", entry)
    object.__setattr__(flow, "hass", MagicMock())
    object.__setattr__(flow, "async_create_entry", lambda title="", data=None, **kw: {
        "type": "create_entry", "title": title, "data": data or {}
    })
    object.__setattr__(flow, "async_show_form", lambda step_id="", data_schema=None, errors=None, **kw: {
        "type": "form", "step_id": step_id, "errors": errors or {}
    })
    return flow


@pytest.mark.asyncio
async def test_options_flow_happy_path():
    flow = _make_options_flow(serial="sn_persisted")

    with patch(
        "custom_components.rowenta_roboeye.config_flow.RobEyeApiClient"
    ) as MockClient:
        MockClient.return_value.test_connection = AsyncMock(return_value=True)
        result = await flow.async_step_init({"host": "192.168.1.101"})

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.1.101"
    # Serial must be preserved so entity unique_ids don't change after IP update
    assert result["data"]["serial"] == "sn_persisted"
    # map_id and last_active_map are now preserved through options saves
    # so the active map survives host/name changes without reverting to default
    assert result["data"].get("map_id") == "3"       # DEFAULT_MAP_ID fallback when not set
    assert result["data"].get("last_active_map") is None  # not yet set in this test entry


@pytest.mark.asyncio
async def test_options_flow_writes_password_to_entry_data():
    """The HTTP password set in options must land in entry.data (where setup
    reads it), not only in entry.options."""
    flow = _make_options_flow(serial="sn_persisted")

    with patch(
        "custom_components.rowenta_roboeye.config_flow.RobEyeApiClient"
    ) as MockClient:
        MockClient.return_value.test_connection = AsyncMock(return_value=True)
        result = await flow.async_step_init(
            {"host": "192.168.1.100", "http_password": "abcd1234"}
        )

    # entry.data was updated (the source async_setup_entry reads).
    flow.hass.config_entries.async_update_entry.assert_called_once()
    _, kwargs = flow.hass.config_entries.async_update_entry.call_args
    assert kwargs["data"][CONF_HTTP_PASSWORD] == "abcd1234"
    # And the returned result still carries the new data.
    assert result["data"][CONF_HTTP_PASSWORD] == "abcd1234"


@pytest.mark.asyncio
async def test_options_flow_rejects_bad_password_length():
    flow = _make_options_flow()
    result = await flow.async_step_init(
        {"host": "192.168.1.100", "http_password": "short"}
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_password"


@pytest.mark.asyncio
async def test_options_flow_cannot_connect():
    flow = _make_options_flow()

    with patch(
        "custom_components.rowenta_roboeye.config_flow.RobEyeApiClient"
    ) as MockClient:
        MockClient.return_value.test_connection = AsyncMock(
            side_effect=CannotConnect
        )
        result = await flow.async_step_init({"host": "10.0.0.1"})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_options_flow_shows_form_without_input():
    flow = _make_options_flow()
    result = await flow.async_step_init(None)
    assert result["type"] == "form"
