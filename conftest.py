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
class _ConfigFlowMeta(type):
    """Metaclass that accepts keyword args like `domain=...`."""
    def __new__(mcs, name, bases, namespace, **kwargs):
        return super().__new__(mcs, name, bases, namespace)
    def __init_subclass__(cls, **kwargs):
        pass

class _ConfigFlowBase(metaclass=_ConfigFlowMeta):
    """Stub ConfigFlow that accepts domain= keyword."""
    pass

ha_ce = _make_module(
    "homeassistant.config_entries",
    ConfigEntry=MagicMock,
    ConfigEntryState=MagicMock(),
    ConfigFlow=_ConfigFlowBase,
    ConfigFlowResult=MagicMock,
    OptionsFlow=MagicMock,
)

# homeassistant.const
ha_const = _make_module(
    "homeassistant.const",
    Platform=MagicMock(),
    CONF_HOST="host",
    EntityCategory=MagicMock(),
    PERCENTAGE="%",
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT="dBm",
    UnitOfArea=MagicMock(),
    UnitOfLength=MagicMock(),
    UnitOfTime=MagicMock(),
)

class _CoordinatorEntityBase:
    """Stub CoordinatorEntity that is subscriptable."""
    def __class_getitem__(cls, item):
        return cls
    def __init__(self, coordinator=None, *a, **kw):
        self.coordinator = coordinator

# homeassistant.helpers.update_coordinator
ha_uc = _make_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=_CoordinatorEntityBase,
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
class _RestoreEntityStub:
    """Stub RestoreEntity that is safe to subclass without breaking property access."""
    async def async_get_last_state(self):
        return None
    async def async_added_to_hass(self):
        pass

ha_helpers.restore_state = _make_module(
    "homeassistant.helpers.restore_state",
    RestoreEntity=_RestoreEntityStub,
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

# Stub base classes that get subclassed — must be real classes, not MagicMock
class _StubEntity:
    """Base for stub entity classes that can be subclassed."""
    _attr_has_entity_name = False
    _attr_unique_id = None
    _attr_name = None
    _attr_icon = None
    _attr_device_info = None
    _attr_extra_state_attributes = None
    _attr_native_value = None
    _attr_native_unit_of_measurement = None
    _attr_entity_category = None
    entity_id = None
    def __init__(self, *a, **kw): pass

from dataclasses import dataclass as _dataclass, field as _field

@_dataclass(frozen=True, kw_only=True)
class _StubSensorEntityDescription:
    """Stub SensorEntityDescription usable as dataclass base."""
    key: str = ""
    translation_key: str = ""
    icon: str = ""
    native_unit_of_measurement: str | None = None
    device_class: object = None
    entity_category: object = None
    entity_registry_enabled_default: bool = True
    state_class: object = None
    suggested_display_precision: int | None = None

for _comp in ("binary_sensor", "sensor", "switch", "select", "vacuum", "button"):
    _m = _make_module(f"homeassistant.components.{_comp}")
    # Set MagicMock instances for enum-like constants
    for _cls in (
        "BinarySensorDeviceClass", "SensorDeviceClass", "SensorStateClass",
        "VacuumEntityFeature", "VacuumActivity",
    ):
        setattr(_m, _cls, MagicMock())
    # Set real stub classes for entity bases that get subclassed
    setattr(_m, "BinarySensorEntity", _StubEntity)
    setattr(_m, "SensorEntity", _StubEntity)
    setattr(_m, "SwitchEntity", _StubEntity)
    setattr(_m, "SelectEntity", _StubEntity)
    setattr(_m, "StateVacuumEntity", _StubEntity)
    setattr(_m, "ButtonEntity", _StubEntity)
    setattr(_m, "SensorEntityDescription", _StubSensorEntityDescription)
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
