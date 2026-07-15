"""Light platform for Cync LAN."""

from __future__ import annotations

import logging

from homeassistant.components.group.light import LightGroup
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENABLE_LIGHT_GROUPS, DEFAULT_ENABLE_LIGHT_GROUPS, DOMAIN
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

    if not entry.options.get(CONF_ENABLE_LIGHT_GROUPS, DEFAULT_ENABLE_LIGHT_GROUPS):
        return
    groups = entry.runtime_data.groups or {}
    if not groups:
        return

    # Member entity_ids are looked up via the entity registry, which
    # requires the individual lights above to actually be registered first
    # - and async_add_entities() is a fire-and-forget callback (its type
    # signature returns None, not a coroutine): it only *schedules* the
    # real registration work as a background task
    # (EntityPlatform._async_schedule_add_entities), it does not complete
    # it before returning. Calling straight through to the registry lookups
    # below without waiting found nothing, every time, for every group -
    # confirmed via a real user report ("groups don't work, it doesn't
    # group the lights") after this looked correct in tests that only used
    # a fake, synchronous async_add_entities stand-in and never exercised
    # this timing gap. async_block_till_done() waits for hass-tracked
    # background tasks (which the scheduled registration task is one of)
    # to actually finish before this proceeds.
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    group_entities = []
    for group_id, group in groups.items():
        member_entity_ids = []
        for dev_id in group.get("device_ids", []):
            unique_id = f"{entry.entry_id}_{dev_id}"
            entity_id = registry.async_get_entity_id(Platform.LIGHT, DOMAIN, unique_id)
            if entity_id is not None:
                member_entity_ids.append(entity_id)
        if not member_entity_ids:
            # Group has no members that ended up as light entities here
            # (e.g. a group of plugs/binary switches, or devices this
            # account no longer has) - nothing to aggregate.
            continue
        group_entities.append(
            CyncLanLightGroup(
                unique_id=f"{entry.entry_id}_group_{group_id}",
                name=group.get("name") or f"Group {group_id}",
                entity_ids=member_entity_ids,
            )
        )
    if group_entities:
        async_add_entities(group_entities)


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


class CyncLanLightGroup(LightGroup):
    """A Cync device group ("Living Room", etc.) exposed as an aggregate
    light entity, built entirely on Home Assistant's own built-in group-
    light implementation rather than reimplementing it:

    - async_turn_on/async_turn_off forward to every member via the
      standard light.turn_on/light.turn_off services (turning the group on
      turns every member on, and vice versa).
    - is_on is OR-based across members (LightGroup's `mode` parameter,
      left at its default/falsy - `any`, not `all`) - the group reads as
      "on" if any single member is on.
    - Brightness/color/etc. are averaged across currently-on members by
      LightGroup itself; nothing group-specific needed here for that.

    No _attr_device_info: like HA's own native "Light Group" helper, this
    is a virtual aggregate, not tied to a physical device.
    """

    _attr_icon = "mdi:lightbulb-group"

    def __init__(self, unique_id: str, name: str, entity_ids: list[str]) -> None:
        super().__init__(unique_id, name, entity_ids, mode=False)


def _factory_effects():
    from cync_lan.devices import FACTORY_EFFECTS_BYTES

    return FACTORY_EFFECTS_BYTES.keys()


def _light_effect_feature():
    from homeassistant.components.light import LightEntityFeature

    return LightEntityFeature.EFFECT
