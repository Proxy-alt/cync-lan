"""Custom services for Cync LAN: experimental commands whose outer envelope
byte (cmd_) is PREDICTED (via the length formula in
docs/mesh_opcodes.md's "TCP relay envelope research"), not confirmed
against a real packet capture. Every service ID is prefixed
"experimental_" as the primary user-facing risk signal - visible in
Developer Tools -> Actions and the automation/script action picker.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_INDICATOR_LED = "experimental_set_indicator_led"
SERVICE_SET_MOTION_SENSOR_SETTINGS = "experimental_set_motion_sensor_settings"
SERVICE_EXECUTE_SCENE = "experimental_execute_scene"

ATTR_DEVICE_ID = "device_id"
ATTR_MODE = "mode"
ATTR_COLOR = "color"
ATTR_BRIGHTNESS = "brightness"
ATTR_WIFI_DISCONNECT_BLINK = "wifi_disconnect_blink"
ATTR_SENSOR_TYPE = "sensor_type"
ATTR_ENABLED = "enabled"
ATTR_SENSITIVITY = "sensitivity"
ATTR_DELAY_SECONDS = "delay_seconds"
ATTR_DEACTIVATION_SECONDS = "deactivation_seconds"
ATTR_SCENE_ID = "scene_id"

# Confirmed enums - see docs/mesh_opcodes.md (LEDIndicatorMode.java,
# LEDIndicatorColor.java) and devices.py's _build_motion_sensor_settings_payload
# (MotionSensorSensitivity.java).
_LED_MODE = {"always_on": 0, "always_off": 1, "normal": 2}
_LED_COLOR = {"white": 0, "red": 1, "green": 2, "blue": 3}
_SENSOR_TYPE = {"motion": 1, "ambient_light": 2}
_SENSITIVITY = {"high": 0, "medium": 1, "low": 2}


def _resolve_device(hass: HomeAssistant, device_id: str):
    """Resolve a HA device-registry device_id to its (ConfigEntry,
    CyncDevice) pair. Raises ServiceValidationError if it doesn't resolve
    to a real, currently-loaded Cync LAN device - a device from another
    integration, an unknown id, or the bridge hub device itself (which has
    no CyncDevice, only individual devices identified by
    f"{entry_id}_{dev_id}" do - see entity.py's build_device_info)."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            continue
        prefix = f"{entry_id}_"
        for identifier_domain, identifier_value in device.identifiers:
            if identifier_domain != DOMAIN or not identifier_value.startswith(prefix):
                continue
            try:
                dev_id = int(identifier_value[len(prefix):])
            except ValueError:
                continue
            node = runtime_data.ncync_server.node_devices.get(dev_id)
            if node is not None:
                return entry, node
    raise ServiceValidationError(
        f"Device {device_id} is not a Cync LAN device (or its entry isn't loaded). "
        "Note: experimental_execute_scene targets the 'Cync LAN Bridge' device, not "
        "an individual device."
    )


def _resolve_bridge_entry(hass: HomeAssistant, device_id: str):
    """Resolve a HA device-registry device_id to its config entry, requiring
    it to be the "Cync LAN Bridge" hub device (identifiers=(DOMAIN, entry_id),
    no dev_id suffix - see binary_sensor.py's diagnostic sensors) rather than
    an individual device, since Scenes are home-wide, not per-device."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")
    for identifier_domain, identifier_value in device.identifiers:
        if identifier_domain == DOMAIN:
            entry = hass.config_entries.async_get_entry(identifier_value)
            if entry is not None and entry.domain == DOMAIN:
                return entry
    raise ServiceValidationError(
        f"Device {device_id} is not the Cync LAN Bridge device - "
        "experimental_execute_scene must target the bridge device, not an "
        "individual light/switch/sensor."
    )


async def _handle_set_indicator_led(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    await node.set_indicator_led(
        mode=_LED_MODE[call.data[ATTR_MODE]],
        color=_LED_COLOR[call.data[ATTR_COLOR]],
        brightness=call.data[ATTR_BRIGHTNESS],
        wifi_disconnect_blink=call.data.get(ATTR_WIFI_DISCONNECT_BLINK, False),
    )


async def _handle_set_motion_sensor_settings(hass: HomeAssistant, call: ServiceCall) -> None:
    _, node = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    sensitivity = call.data.get(ATTR_SENSITIVITY)
    await node.set_motion_sensor_settings(
        setting_type=_SENSOR_TYPE[call.data[ATTR_SENSOR_TYPE]],
        enabled=call.data.get(ATTR_ENABLED),
        sensitivity=_SENSITIVITY[sensitivity] if sensitivity else None,
        delay_seconds=call.data.get(ATTR_DELAY_SECONDS, 0),
        deactivation_seconds=call.data.get(ATTR_DEACTIVATION_SECONDS, 0),
    )


async def _handle_execute_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    from cync_lan.devices import execute_scene

    _resolve_bridge_entry(hass, call.data[ATTR_DEVICE_ID])
    await execute_scene(call.data[ATTR_SCENE_ID])


_SERVICE_SCHEMAS = {
    SERVICE_SET_INDICATOR_LED: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_MODE): vol.In(_LED_MODE),
            vol.Required(ATTR_COLOR): vol.In(_LED_COLOR),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            vol.Optional(ATTR_WIFI_DISCONNECT_BLINK, default=False): cv.boolean,
        }
    ),
    SERVICE_SET_MOTION_SENSOR_SETTINGS: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SENSOR_TYPE): vol.In(_SENSOR_TYPE),
            vol.Optional(ATTR_ENABLED): cv.boolean,
            vol.Optional(ATTR_SENSITIVITY): vol.In(_SENSITIVITY),
            vol.Optional(ATTR_DELAY_SECONDS, default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional(ATTR_DEACTIVATION_SECONDS, default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
        }
    ),
    SERVICE_EXECUTE_SCENE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_SCENE_ID): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
}

_HANDLERS = {
    SERVICE_SET_INDICATOR_LED: _handle_set_indicator_led,
    SERVICE_SET_MOTION_SENSOR_SETTINGS: _handle_set_motion_sensor_settings,
    SERVICE_EXECUTE_SCENE: _handle_execute_scene,
}


def async_setup_services(hass: HomeAssistant) -> None:
    """Register all 3 experimental services - idempotent, so calling this
    from every config entry's async_setup_entry (there's only ever one
    entry per the unique-config-entry design, but this is cheap insurance)
    is safe."""
    for service, schema in _SERVICE_SCHEMAS.items():
        if hass.services.has_service(DOMAIN, service):
            continue
        handler = _HANDLERS[service]

        async def _call(call: ServiceCall, _handler=handler) -> None:
            await _handler(hass, call)

        hass.services.async_register(DOMAIN, service, _call, schema=schema)


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove all 3 services, but only once no Cync LAN config entry
    remains loaded - checked as "<=1" rather than "==0" because this runs
    from async_unload_entry *before* HA has finished marking the entry
    currently being unloaded as unloaded, so that entry itself would
    otherwise always still show up in async_loaded_entries()."""
    if len(hass.config_entries.async_loaded_entries(DOMAIN)) > 1:
        return
    for service in _SERVICE_SCHEMAS:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
