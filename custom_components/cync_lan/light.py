"""Light platform for Cync LAN."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, KeysView

from cync_lan.classify import (
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    LightFeatures,
    cync_to_kelvin,
    kelvin_to_cync,
)

from homeassistant.components.group.light import LightGroup
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    CONF_ENABLE_LIGHT_GROUPS,
    CONF_INDICATOR_LED_AS_LIGHT,
    CONF_HIDE_GROUP_MEMBERS,
    DEFAULT_ENABLE_EXPERIMENTAL,
    DEFAULT_ENABLE_LIGHT_GROUPS,
    DEFAULT_HIDE_GROUP_MEMBERS,
    DEFAULT_INDICATOR_LED_AS_LIGHT,
    DOMAIN,
)
from .bridge import CyncLanBridge
from .entity import CyncLanEntity, CyncLanIndicatorLedEntity
from .groups import apply_group_member_visibility, wait_for_member_entities

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice


# parallel-updates (silver): each light entity issues its own independent
# command to the device over the shared TCP connection - the underlying
# protocol handles command serialization per bridge itself, so entities
# don't need to be limited to N-at-a-time from HA's side.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[LightEntity] = []
    light_dev_ids = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not node.is_light:
            continue
        entities.append(CyncLanLight(bridge, entry.entry_id, node))
        light_dev_ids.append(node.id)

    # The status ring as a light, when the user has chosen that form. It is
    # exclusive with the select/number/switch trio rather than additional to
    # it: all of them write the same single atomic mesh command, so shipping
    # both would put two UIs in a race over one piece of hardware and let
    # them disagree about its state. The other platforms skip their
    # indicator entities when this is on, and stale ones are removed from
    # the registry so flipping the option does not leave debris behind.
    if entry.options.get(CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT):
        for node in runtime_data.ncync_server.node_devices.values():
            if node.metadata is None or not node.metadata.supported:
                continue
            entities.append(CyncLanIndicatorLedLight(bridge, entry.entry_id, node))

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
    # wait_for_member_entities' docstring for why this polls instead of
    # using hass.async_block_till_done() (the original fix, which caused
    # its own real-world regression: platform setup taking 60+ seconds).
    registry = er.async_get(hass)
    await wait_for_member_entities(
        hass, registry, Platform.LIGHT, entry.entry_id, light_dev_ids
    )

    await async_add_light_groups(hass, entry)


async def async_add_light_groups(
    hass: HomeAssistant,
    entry: ConfigEntry,
    hide_members: bool | None = None,
    use_group_command: bool | None = None,
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
    read stale for that caller. hide_members and use_group_command have the
    same staleness problem: pass them explicitly (from the just-submitted
    form data) when calling from there; left as None they fall back to
    entry.options, correct for the async_setup_entry caller where options
    are current.
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
    if use_group_command is None:
        use_group_command = entry.options.get(
            CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
        )
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
            # account no longer has) - nothing to aggregate. A pure-switch
            # group gets its aggregate entity from switch.py instead - see
            # CyncLanSwitchGroup.
            continue
        group_entities.append(
            CyncLanLightGroup(
                unique_id=f"{entry.entry_id}_group_{group_id}",
                name=group.get("name") or f"Group {group_id}",
                entity_ids=member_entity_ids,
                group_id=group_id,
                use_group_command=use_group_command,
            )
        )
        already_created.add(group_id)
    if group_entities:
        add_entities(group_entities)

    if hide_members is None:
        hide_members = entry.options.get(
            CONF_HIDE_GROUP_MEMBERS, DEFAULT_HIDE_GROUP_MEMBERS
        )
    apply_group_member_visibility(
        registry, Platform.LIGHT, entry.entry_id, groups, hide_members
    )


# The device reports this in the temperature field to mean "I am in RGB mode
# right now" - color_mode below reads it for exactly that. It is not a
# temperature and must never be scaled as one.
RGB_MODE_SENTINEL = 254


def _kelvin_features(node: "CyncDevice") -> LightFeatures:
    """The node's kelvin range, in the shape the shared converter wants."""
    characteristics = getattr(node.metadata, "characteristics", None)
    return LightFeatures(
        color_temp=True,
        min_kelvin=getattr(characteristics, "min_kelvin", None) or None,
        max_kelvin=getattr(characteristics, "max_kelvin", None) or None,
    )


class CyncLanLight(CyncLanEntity, LightEntity):
    _attr_name = None  # has-entity-name: device name is the entity name

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(bridge, entry_id, node)
        modes: set[ColorMode] = set()
        if node.supports_temperature:
            modes.add(ColorMode.COLOR_TEMP)
        if node.supports_rgb:
            modes.add(ColorMode.RGB)
            self._attr_effect_list = list(_light_run_mode_effects())
            self._attr_supported_features = LightEntityFeature.EFFECT
        if not modes:
            modes.add(ColorMode.BRIGHTNESS)
        self._supported_color_modes: set[ColorMode] = modes
        self._attr_supported_color_modes = modes
        self._attr_color_mode = next(iter(modes))
        # Advertised range and conversion range come from the same place, so
        # they cannot disagree. They did: a device declaring no max_kelvin
        # kept Home Assistant's own default of 6535 here while the converter
        # scaled against 7000, so the top of the user's slider mapped to 90
        # rather than 100 and a device reporting 100 read back above the
        # maximum this entity claims to support.
        kelvin = _kelvin_features(node)
        self._attr_min_color_temp_kelvin = kelvin.min_kelvin or DEFAULT_MIN_KELVIN
        self._attr_max_color_temp_kelvin = kelvin.max_kelvin or DEFAULT_MAX_KELVIN

    @property
    def is_on(self) -> bool | None:
        state = self._entity_state()
        return bool(state.power) if state else None

    @property
    def color_mode(self) -> ColorMode | None:
        """Live, not the static value __init__ computed once via
        next(iter(modes)) - for a dual-capable device (RGB+COLOR_TEMP both
        supported) that static value was fixed at construction time by
        Python's set iteration order (hash-randomization-dependent for a
        StrEnum), permanently rendering the wrong control widget for an
        unpredictable subset of devices regardless of what the bulb is
        actually doing. Mirrors src/cync_lan/mqtt_client.py's own
        already-shipping live color_mode convention exactly: the device's
        own status packets report temperature=254 as a sentinel meaning
        "currently in RGB mode" (see MQTTClient.update_rgb/update_temperature
        and its pub_entity_state color_mode branch), with any 0-100 value
        meaning "currently in CCT mode" instead.
        """
        modes = self._supported_color_modes
        if ColorMode.RGB in modes and ColorMode.COLOR_TEMP in modes:
            state = self._entity_state()
            if state is not None:
                if state.temperature == RGB_MODE_SENTINEL:
                    return ColorMode.RGB
                if 0 <= state.temperature <= 100:
                    return ColorMode.COLOR_TEMP
        return self._attr_color_mode  # single-mode device, or no state yet

    @property
    def brightness(self) -> int | None:
        state = self._entity_state()
        if not state:
            return None
        return round(state.brightness * 255 / 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Cync reports 0-100 on the wire whatever the bulb's real range is,
        so this has to convert - it used to hand the raw 0-100 back as if it
        were kelvin, which put every colour-temperature reading far below the
        min_color_temp_kelvin this same class advertises."""
        state = self._entity_state()
        if not state or not self._node.supports_temperature:
            return None
        if state.temperature is None:
            return None
        # 254 is the device's "I am in RGB mode" sentinel, not a temperature;
        # color_mode reads it for exactly that, and it must not be scaled.
        if state.temperature == RGB_MODE_SENTINEL:
            return None
        return cync_to_kelvin(state.temperature, _kelvin_features(self._node))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        state = self._entity_state()
        if not state or not self._node.supports_rgb:
            return None
        return (state.red, state.green, state.blue)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose 5% hardware minimum dimming floor for dimmer switches and plugs."""
        if self._node.is_dimmer_switch:
            return {"min_brightness_pct": 5}
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await self._node.set_rgb(r, g, b)
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            # set_temperature validates 0-100 and refuses anything larger, so
            # passing HA's kelvin straight through logged "Invalid
            # temperature! must be 0-100" and sent nothing at all - colour
            # temperature simply did not work. The MQTT add-on has always
            # converted (kelvin2cync); this never did.
            await self._node.set_temperature(
                kelvin_to_cync(
                    kwargs[ATTR_COLOR_TEMP_KELVIN], _kelvin_features(self._node)
                )
            )
        if ATTR_EFFECT in kwargs:
            await self._node.set_light_effect(kwargs[ATTR_EFFECT])
        bri_pct = (
            round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            if ATTR_BRIGHTNESS in kwargs
            else None
        )
        min_floor = 5 if self._node.is_dimmer_switch else 1
        if ATTR_TRANSITION in kwargs:
            # EXPERIMENTAL (see set_fine_brightness's docstring, predicted
            # cmd_code): the fine-brightness wire command always carries a
            # mandatory target-brightness field, so a transition always
            # needs one - falls back to current brightness, then 100, when
            # only `transition=` was given with no explicit brightness=.
            if bri_pct is not None:
                target_bri = max(min_floor, bri_pct)
            elif self.brightness:
                target_bri = round(self.brightness * 100 / 255)
            else:
                target_bri = 100
            fade_ms = round(kwargs[ATTR_TRANSITION] * 1000)
            await self._node.set_fine_brightness(target_bri, fade_ms)
        elif bri_pct is not None:
            await self._node.set_brightness(max(min_floor, bri_pct))
        if not kwargs:
            await self._node.set_power(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._node.set_power(0)


class CyncLanLightGroup(LightGroup):
    """A Cync device group ("Living Room", etc.) exposed as an aggregate
    light entity, built entirely on Home Assistant's own built-in group-
    light implementation rather than reimplementing it:

    - async_turn_on/async_turn_off forward to every member via the
      standard light.turn_on/light.turn_off services (turning the group on
      turns every member on, and vice versa) - unless the experimental
      option is on, see below.
    - is_on is OR-based across members (LightGroup's `mode` parameter,
      left at its default/falsy - `any`, not `all`) - the group reads as
      "on" if any single member is on.
    - Brightness/color/etc. are averaged across currently-on members by
      LightGroup itself; nothing group-specific needed here for that.

    No _attr_device_info: like HA's own native "Light Group" helper, this
    is a virtual aggregate, not tied to a physical device.

    **Experimental direct-group-command override.** By default, turning
    this on/off issues one command per member device via the fanout above -
    the confirmed-working path. When the experimental option is on and the
    call carries no other attributes (a plain on/off, not
    "set brightness to 50%"), a single command addresses the group's own
    MeshAddress directly instead (cync_lan.devices.set_group_power) -
    unconfirmed against real firmware, which is exactly why it's opt-in.
    This replaces the old standalone group-power switch entity, which did
    the same thing but as a separate control disconnected from the room's
    actual light entity; folding it in here means there's one group control
    per room, not two disagreeing ones. Attribute-carrying calls always use
    the fanout regardless of the option, since the group command carries no
    such data. See switch.py's CyncLanSwitchGroup for the equivalent on
    groups with no light-domain members.

    icon-translations (gold): translation_key (not a static _attr_icon)
    drives icons.json's entity.light.cync_light_group block, which gives
    "off" a proper mdi:lightbulb-group-off instead of showing the "on"
    icon at all times regardless of state. Also declares an icon for
    "unavailable" (same off glyph) - included defensively since it's
    harmless if the frontend doesn't honor it, but unlike the "off" case
    (which mirrors this codebase's already-proven binary_sensor pattern),
    HA's icon-translation state lookup for "unavailable" specifically
    isn't confirmed/documented anywhere; the frontend may just dim
    whatever icon is already showing instead. Safe to set translation_key
    here despite _attr_name also being set below - Entity._name_internal
    checks _attr_name first and never falls through to translation_key
    for naming, so this only affects icon resolution, not the group's
    display name (e.g. "Living Room").
    """

    _attr_translation_key = "cync_light_group"

    def __init__(
        self,
        unique_id: str,
        name: str,
        entity_ids: list[str],
        group_id: int,
        use_group_command: bool,
    ) -> None:
        super().__init__(unique_id, name, entity_ids, mode=False)
        self._group_id = group_id
        self._use_group_command = use_group_command

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._use_group_command and not kwargs:
            from cync_lan.devices import set_group_power

            await set_group_power(self._group_id, 1)
            return
        await super().async_turn_on(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._use_group_command and not kwargs:
            from cync_lan.devices import set_group_power

            await set_group_power(self._group_id, 0)
            return
        await super().async_turn_off(**kwargs)


def _light_run_mode_effects() -> KeysView[str]:
    """Deferred import: cync_lan.const reads its env-var-backed constants at
    import time, so it must not be imported before configure_environment()
    has run (see util.configure_environment's docstring)."""
    from cync_lan.const import LIGHT_RUN_MODE_EFFECTS

    return LIGHT_RUN_MODE_EFFECTS.keys()


# The four colours the ring can actually be, and the RGB each one is meant to
# look like. Confirmed values, not a palette choice: the hardware takes an enum
# (DimmingLedsIndicatorColor), so anything a colour wheel produces has to be
# mapped onto one of these four before it can be sent.
_LED_REFERENCE_RGB: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
}


def nearest_led_color(rgb: tuple[int, int, int]) -> str:
    """The ring colour closest to an arbitrary RGB value.

    Straight Euclidean distance in RGB space. Not perceptually uniform - CIEDE
    2000 would be the "correct" answer - but with only four widely separated
    reference points, every input is unambiguously nearest one of them and the
    extra machinery would not change a single result.

    Ties go to the earlier entry in `_LED_REFERENCE_RGB`, which only happens on
    exact midpoints such as (255, 255, 0) between red and green.
    """
    red, green, blue = rgb
    return min(
        _LED_REFERENCE_RGB,
        key=lambda name: (
            (red - _LED_REFERENCE_RGB[name][0]) ** 2
            + (green - _LED_REFERENCE_RGB[name][1]) ** 2
            + (blue - _LED_REFERENCE_RGB[name][2]) ** 2
        ),
    )


class CyncLanIndicatorLedLight(CyncLanIndicatorLedEntity, LightEntity):
    """A switch's status ring, presented as a light.

    The point is reach rather than capability: the select/number/switch trio
    already sets everything this does, but none of them can be dropped on a
    light card, and none are exposed to HomeKit or Alexa as a light. This is,
    so "set the porch switch ring to red" works from anywhere that speaks
    lights.

    Two lossy edges, both unavoidable and both deliberate:

    - **Colour is snapped.** The hardware takes an enum of four colours, so an
      arbitrary RGB is mapped to the nearest of them (`nearest_led_color`). The
      entity then reports back the *reference* RGB rather than what was asked
      for, because reporting the requested value would claim a precision the
      device does not have.
    - **On/off maps onto mode**, which has three values, not two. Off is
      `always_off`. On is `always_on` - except when the mode is already
      `normal`, which is already "on" in every sense that matters, and
      overwriting it would silently destroy a setting the user chose from the
      select entity. So turning on a ring that is already in `normal` leaves it
      there.

    `assumed_state` for the same reason as its siblings: the device never
    reports this back over the mesh.
    """

    _attr_translation_key = "indicator_led_light"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_indicator_led_light"
        )
        # Instance rather than class attribute, matching CyncLanLight above -
        # a mutable class-level default is shared across every instance.
        self._attr_supported_color_modes = {ColorMode.RGB}

    @property
    def _led(self) -> Any:
        return self._bridge.get_indicator_led(self._node.id)

    @property
    def is_on(self) -> bool:
        return bool(self._led.mode != "always_off")

    @property
    def brightness(self) -> int:
        # The ring is 0-100; HA lights are 0-255.
        return int(round(max(0, min(100, self._led.brightness)) * 255 / 100))

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return _LED_REFERENCE_RGB.get(self._led.color, (255, 255, 255))

    async def async_turn_on(self, **kwargs: Any) -> None:
        fields: dict[str, Any] = {}
        if (rgb := kwargs.get(ATTR_RGB_COLOR)) is not None:
            fields["color"] = nearest_led_color(tuple(rgb))
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            fields["brightness"] = round(brightness * 100 / 255)
        # Only force the mode when the ring is actually off. See the class
        # docstring: clobbering `normal` on every turn_on would quietly undo a
        # deliberate choice made through the mode select.
        if self._led.mode == "always_off":
            fields["mode"] = "always_on"
        await self._bridge.set_indicator_led_field(self._node, **fields)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.set_indicator_led_field(self._node, mode="always_off")
