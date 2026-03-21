"""Root-level conftest: stub out the homeassistant package so unit tests can
run without a full Home Assistant installation."""
from __future__ import annotations

import sys
import types
from datetime import timedelta
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Minimal DataUpdateCoordinator implementation (enough for coordinator tests)
# ---------------------------------------------------------------------------

class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init_subclass__(cls, **kwargs):
        """Wrap _async_update_data so self.data is updated automatically,
        mimicking real HA DataUpdateCoordinator.async_refresh() behaviour."""
        super().__init_subclass__(**kwargs)
        if "_async_update_data" in cls.__dict__:
            _orig = cls._async_update_data

            async def _wrapped(self, *args, **kw):
                result = await _orig(self, *args, **kw)
                self.data = result
                return result

            cls._async_update_data = _wrapped

    def __init__(self, hass, logger=None, *, name, update_interval=None,
                 config_entry=None, **kwargs):
        self.hass = hass
        self.logger = logger or MagicMock()
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.data: dict = {}
        self._listeners: list = []

    async def _async_update_data(self):  # pragma: no cover
        raise NotImplementedError

    def async_add_listener(self, update_callback, context=None):
        self._listeners.append(update_callback)
        def remove():
            self._listeners.remove(update_callback)
        return remove

    def async_set_updated_data(self, data):
        self.data = data

    @property
    def last_update_success(self):
        return True


# ---------------------------------------------------------------------------
# Build a stub homeassistant package hierarchy
# ---------------------------------------------------------------------------

def _make_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# homeassistant
ha_root = _make_module("homeassistant")

# homeassistant.core
ha_core = _make_module(
    "homeassistant.core",
    HomeAssistant=MagicMock,
    callback=lambda f: f,
    CoreState=MagicMock(),
    EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
)

# homeassistant.config_entries
ha_ce = _make_module(
    "homeassistant.config_entries",
    ConfigEntry=MagicMock,
    ConfigFlow=MagicMock,
    ConfigFlowResult=MagicMock,
    OptionsFlow=MagicMock,
)

# homeassistant.const
ha_const = _make_module(
    "homeassistant.const",
    Platform=MagicMock(),
    CONF_HOST="host",
    EntityCategory=MagicMock(),
)

# homeassistant.helpers.update_coordinator
ha_uc = _make_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=MagicMock,
)

# homeassistant.helpers.dispatcher
ha_dispatcher = _make_module(
    "homeassistant.helpers.dispatcher",
    async_dispatcher_send=MagicMock(),
    async_dispatcher_connect=MagicMock(),
)

# homeassistant.helpers.aiohttp_client
ha_aiohttp = _make_module(
    "homeassistant.helpers.aiohttp_client",
    async_get_clientsession=MagicMock(),
)

# homeassistant.helpers.event
ha_event = _make_module(
    "homeassistant.helpers.event",
    async_call_later=MagicMock(),
)

# homeassistant.helpers.typing
ha_typing = _make_module("homeassistant.helpers.typing", ConfigType=dict)

# homeassistant.helpers.device_registry
ha_device_reg = _make_module(
    "homeassistant.helpers.device_registry",
    DeviceInfo=dict,
)

# homeassistant.helpers.entity_platform
ha_ep = _make_module(
    "homeassistant.helpers.entity_platform",
    AddConfigEntryEntitiesCallback=MagicMock,
)

# homeassistant.helpers (parent)
ha_helpers = _make_module(
    "homeassistant.helpers",
    update_coordinator=ha_uc,
    dispatcher=ha_dispatcher,
    aiohttp_client=ha_aiohttp,
    event=ha_event,
    typing=ha_typing,
    device_registry=ha_device_reg,
    entity_platform=ha_ep,
)
ha_helpers.config_validation = MagicMock()
ha_helpers.entity_registry = MagicMock()
ha_helpers.restore_state = _make_module(
    "homeassistant.helpers.restore_state",
    RestoreEntity=MagicMock,
)
ha_helpers.service_info = _make_module("homeassistant.helpers.service_info")
ha_helpers.service_info.zeroconf = _make_module(
    "homeassistant.helpers.service_info.zeroconf",
    ZeroconfServiceInfo=MagicMock,
)

# homeassistant.components.http
ha_http = _make_module(
    "homeassistant.components.http",
    StaticPathConfig=MagicMock,
    HomeAssistantView=MagicMock,
)

# homeassistant.components.*
ha_components = types.ModuleType("homeassistant.components")
for _comp in ("binary_sensor", "sensor", "switch", "select", "vacuum", "button"):
    _m = _make_module(f"homeassistant.components.{_comp}")
    for _cls in (
        "BinarySensorEntity", "SensorEntity", "SwitchEntity",
        "SelectEntity", "StateVacuumEntity", "ButtonEntity",
        "BinarySensorDeviceClass", "SensorDeviceClass", "SensorStateClass",
        "VacuumEntityFeature",
    ):
        setattr(_m, _cls, MagicMock)
    setattr(ha_components, _comp, _m)
    sys.modules[f"homeassistant.components.{_comp}"] = _m

ha_components.http = ha_http
sys.modules["homeassistant.components.http"] = ha_http

ha_persistent = _make_module(
    "homeassistant.components.persistent_notification",
    async_create=MagicMock(),
    dismiss=MagicMock(),
)
ha_components.persistent_notification = ha_persistent
sys.modules["homeassistant.components.persistent_notification"] = ha_persistent

# Register all modules
_modules = {
    "homeassistant": ha_root,
    "homeassistant.core": ha_core,
    "homeassistant.config_entries": ha_ce,
    "homeassistant.const": ha_const,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.update_coordinator": ha_uc,
    "homeassistant.helpers.dispatcher": ha_dispatcher,
    "homeassistant.helpers.aiohttp_client": ha_aiohttp,
    "homeassistant.helpers.event": ha_event,
    "homeassistant.helpers.typing": ha_typing,
    "homeassistant.helpers.device_registry": ha_device_reg,
    "homeassistant.helpers.entity_platform": ha_ep,
    "homeassistant.helpers.restore_state": ha_helpers.restore_state,
    "homeassistant.helpers.service_info": ha_helpers.service_info,
    "homeassistant.helpers.service_info.zeroconf": ha_helpers.service_info.zeroconf,
    "homeassistant.helpers.config_validation": ha_helpers.config_validation,
    "homeassistant.helpers.entity_registry": ha_helpers.entity_registry,
    "homeassistant.components": ha_components,
    "homeassistant.components.http": ha_http,
    "homeassistant.components.persistent_notification": ha_persistent,
}

for _name, _mod in _modules.items():
    sys.modules.setdefault(_name, _mod)
