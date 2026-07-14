"""Light platform for Cync LAN."""

from __future__ import annotations

import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import CyncLanEntity

_LOGGER = logging.getLogger(__name__)

# parallel-updates (silver): each light entity issues its own independent
# command to the device over the shared TCP connection - the underlying
# protocol handles command serialization per bridge itself, so entities
# don't need to be limited to N-at-a-time from HA's side.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    entities = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.is_light:
            continue
        entities.append(CyncLanLight(bridge, entry.entry_id, node))
    async_add_entities(entities)


class CyncLanLight(CyncLanEntity, LightEntity):
    _attr_name = None  # has-entity-name: device name is the entity name

    def __init__(self, bridge, entry_id: str, node) -> None:
        super().__init__(bridge, entry_id, node)
        modes: set[ColorMode] = set()
        if node.supports_temperature:
            modes.add(ColorMode.COLOR_TEMP)
        if node.supports_rgb:
            modes.add(ColorMode.RGB)
            self._attr_effect_list = list(_factory_effects())
            self._attr_supported_features = _light_effect_feature()
        if not modes:
            modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = modes
        self._attr_color_mode = next(iter(modes))
        if node.metadata and node.metadata.characteristics:
            if node.metadata.characteristics.min_kelvin:
                self._attr_min_color_temp_kelvin = node.metadata.characteristics.min_kelvin
            if node.metadata.characteristics.max_kelvin:
                self._attr_max_color_temp_kelvin = node.metadata.characteristics.max_kelvin

    @property
    def is_on(self) -> bool | None:
        state = self._entity_state()
        return bool(state.power) if state else None

    @property
    def brightness(self) -> int | None:
        state = self._entity_state()
        if not state:
            return None
        return round(state.brightness * 255 / 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        state = self._entity_state()
        if not state or not self._node.supports_temperature:
            return None
        return state.temperature or None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        state = self._entity_state()
        if not state or not self._node.supports_rgb:
            return None
        return (state.red, state.green, state.blue)

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await self._node.set_rgb(r, g, b)
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self._node.set_temperature(kwargs[ATTR_COLOR_TEMP_KELVIN])
        if ATTR_EFFECT in kwargs:
            await self._node.set_lightshow(kwargs[ATTR_EFFECT])
        if ATTR_BRIGHTNESS in kwargs:
            bri_pct = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            await self._node.set_brightness(max(1, bri_pct))
        if not kwargs:
            await self._node.set_power(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._node.set_power(0)


def _factory_effects():
    from cync_lan.devices import FACTORY_EFFECTS_BYTES

    return FACTORY_EFFECTS_BYTES.keys()


def _light_effect_feature():
    from homeassistant.components.light import LightEntityFeature

    return LightEntityFeature.EFFECT
