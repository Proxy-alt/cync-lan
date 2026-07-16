"""Light platform for Cync LAN."""

from __future__ import annotations

import asyncio
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

from .const import (
    CONF_ENABLE_LIGHT_GROUPS,
    CONF_HIDE_GROUP_MEMBERS,
    DEFAULT_ENABLE_LIGHT_GROUPS,
    DEFAULT_HIDE_GROUP_MEMBERS,
    DOMAIN,
)
from .entity import CyncLanEntity

_LOGGER = logging.getLogger(__name__)

# parallel-updates (silver): each light entity issues its own independent
# command to the device over the shared TCP connection - the underlying
# protocol handles command serialization per bridge itself, so entities
# don't need to be limited to N-at-a-time from HA's side.
PARALLEL_UPDATES = 0

_ENTITY_REGISTRATION_POLL_INTERVAL = 0.1
_ENTITY_REGISTRATION_TIMEOUT = 5.0


async def _wait_for_light_entities(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    entry_id: str,
    dev_ids: list[int],
) -> None:
    """Poll the entity registry until every light entity just scheduled by
    async_add_entities() above has actually been registered, or a short
    timeout elapses.

    async_add_entities() only *schedules* registration as a background
    task (EntityPlatform._async_schedule_add_entities_for_entry) - it does
    not complete before returning. A previous version of this function
    waited on hass.async_block_till_done() instead, which waits for every
    hass-tracked background task process-wide, not just this platform's
    own scheduled work - on a real HA install with many integrations
    still settling during startup, that took over 60 seconds and tripped
    HA's own "Setup of platform cync_lan is taking longer than 60
    seconds" warning. Polling just for these specific entities resolves
    as soon as they're actually ready without waiting on anything
    unrelated, and the timeout keeps this from hanging indefinitely if
    one somehow never registers - the caller's own registry lookups
    already tolerate a missing entity by skipping that group member.
    """
    if not dev_ids:
        return
    deadline = hass.loop.time() + _ENTITY_REGISTRATION_TIMEOUT
    while hass.loop.time() < deadline:
        if all(
            registry.async_get_entity_id(Platform.LIGHT, DOMAIN, f"{entry_id}_{dev_id}")
            is not None
            for dev_id in dev_ids
        ):
            return
        await asyncio.sleep(_ENTITY_REGISTRATION_POLL_INTERVAL)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    entities = []
    light_dev_ids = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.is_light:
            continue
        entities.append(CyncLanLight(bridge, entry.entry_id, node))
        light_dev_ids.append(node.id)
    async_add_entities(entities)

    # Stashed so groups can be (re)applied later - e.g. from the options
    # flow when the user enables/refreshes them - without a full entry
    # reload. See async_add_light_groups() below.
    entry.runtime_data.light_add_entities = async_add_entities
    entry.runtime_data.created_light_group_ids = set()

    if not entry.options.get(CONF_ENABLE_LIGHT_GROUPS, DEFAULT_ENABLE_LIGHT_GROUPS):
        return
    if not entry.runtime_data.groups:
        return

    # Member entity_ids are looked up via the entity registry, which
    # requires the individual lights above to actually be registered first
    # - and async_add_entities() only *schedules* that registration as a
    # background task, it does not complete it before returning. See
    # _wait_for_light_entities' docstring for why this polls instead of
    # using hass.async_block_till_done() (the original fix, which caused
    # its own real-world regression: platform setup taking 60+ seconds).
    registry = er.async_get(hass)
    await _wait_for_light_entities(hass, registry, entry.entry_id, light_dev_ids)

    await async_add_light_groups(hass, entry)


async def async_add_light_groups(
    hass: HomeAssistant, entry: ConfigEntry, hide_members: bool | None = None
) -> None:
    """Create and add any light-group entities that don't exist yet, and
    apply each group's member-visibility state.

    Callable both from this platform's own async_setup_entry (initial
    setup/reload) and directly from the options flow when the user
    enables or refreshes light groups - the latter needs this to apply
    without forcing a full entry reload, which would drop every device's
    TCP connection just to add a handful of group entities.

    Callers are responsible for checking CONF_ENABLE_LIGHT_GROUPS
    themselves before calling this - the options flow calls it before its
    own entry.options update actually lands, so entry.options here could
    read stale for that caller. hide_members has the same staleness
    problem: pass it explicitly (from the just-submitted form data) when
    calling from there; left as None it falls back to entry.options,
    correct for the async_setup_entry caller where options are current.
    """
    runtime_data = entry.runtime_data
    add_entities = runtime_data.light_add_entities
    if add_entities is None:
        # This platform hasn't finished its own initial setup yet -
        # nothing running to add group entities to.
        return
    groups = runtime_data.groups or {}
    if not groups:
        return

    registry = er.async_get(hass)
    already_created = runtime_data.created_light_group_ids
    group_entities = []
    for group_id, group in groups.items():
        if group_id in already_created:
            continue
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
        already_created.add(group_id)
    if group_entities:
        add_entities(group_entities)

    if hide_members is None:
        hide_members = entry.options.get(
            CONF_HIDE_GROUP_MEMBERS, DEFAULT_HIDE_GROUP_MEMBERS
        )
    _apply_group_member_visibility(registry, entry.entry_id, groups, hide_members)


def _apply_group_member_visibility(
    registry: er.EntityRegistry, entry_id: str, groups: dict, hide: bool
) -> None:
    """Hide or reveal each light group's member entities, without
    touching entities the user hid themselves.

    The entity registry tracks *why* an entity is hidden via hidden_by
    (None, RegistryEntryHider.USER, or RegistryEntryHider.INTEGRATION) -
    only ever touches entities this integration hid itself
    (hidden_by == INTEGRATION), so a user who explicitly hid a member
    light for their own reasons keeps that choice regardless of this
    option, in either direction.
    """
    for group in groups.values():
        for dev_id in group.get("device_ids", []):
            unique_id = f"{entry_id}_{dev_id}"
            entity_id = registry.async_get_entity_id(Platform.LIGHT, DOMAIN, unique_id)
            if entity_id is None:
                continue
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None:
                continue
            if hide:
                if reg_entry.hidden_by is None:
                    registry.async_update_entity(
                        entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                    )
            elif reg_entry.hidden_by is er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(entity_id, hidden_by=None)


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
