"""Number platform for Cync LAN: indicator-LED brightness.

See select.py's module docstring for the shared assumed-state/RestoreEntity
rationale - this uses RestoreNumber instead, since RestoreEntity's own
restored-state string doesn't carry NumberEntity's native_value shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import CyncLanBridge
from .const import CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL
from .entity import CyncLanEntity, CyncLanIndicatorLedEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


# Indicator-LED presentation: one form or the other, never both. All of these
# entities write the same single atomic mesh command, so offering both a light
# and the select/number/switch trio would put two UIs in a race over one piece
# of hardware and let them disagree about its state.
def _indicator_led_is_a_light(entry: ConfigEntry) -> bool:
    from .const import CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT

    return bool(
        entry.options.get(CONF_INDICATOR_LED_AS_LIGHT, DEFAULT_INDICATOR_LED_AS_LIGHT)
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[NumberEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        if not _indicator_led_is_a_light(entry):
            entities.append(CyncLanIndicatorLedBrightness(bridge, entry.entry_id, node))

    # Experimental-only. Gated like the experimental_* services - the
    # cmd_code for this command is predicted, not confirmed.
    if entry.options.get(CONF_ENABLE_EXPERIMENTAL, DEFAULT_ENABLE_EXPERIMENTAL):
        for node in runtime_data.ncync_server.node_devices.values():
            if node.metadata is None or not node.metadata.supported:
                continue
            if node.supports_rgb:
                entities.append(
                    CyncLanMultiColorSegmentCount(bridge, entry.entry_id, node)
                )
            # The level bar exists on dimmer switches, not binary ones.
            #
            # This asked `is_dimmable and not is_light`, which no device
            # type could satisfy: is_dimmable was gated to LIGHT-classified
            # types, and a dimmable switch has is_light True by the
            # carve-out that keeps it on the light platform. So this entity
            # was never created, for anyone. is_dimmer_switch is the
            # question that was meant - see cync_lan.classify.
            if node.is_dimmer_switch:
                entities.append(
                    CyncLanDimmerLedBrightness(bridge, entry.entry_id, node)
                )

    async_add_entities(entities)


class CyncLanIndicatorLedBrightness(
    CyncLanIndicatorLedEntity, RestoreNumber, NumberEntity
):
    _attr_translation_key = "indicator_led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_indicator_led_brightness"
        )

    @property
    def native_value(self) -> int:
        return self._bridge.get_indicator_led(self._node.id).brightness

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._bridge.seed_indicator_led_field(
                self._node, brightness=int(last.native_value)
            )

    async def async_set_native_value(self, value: float) -> None:
        await self._bridge.set_indicator_led_field(self._node, brightness=int(value))


class CyncLanMultiColorSegmentCount(CyncLanEntity, RestoreNumber, NumberEntity):
    """Logical segment count for a custom MultiColor scheme, replacing
    experimental_set_multicolor_segment_count.

    Assumed state, restored across restarts - the device never reports this
    back. One of three primitives a full custom scheme needs (see
    CyncDevice.set_multicolor_segment_count's docstring); setting it alone
    may not produce a visible change.
    """

    _attr_translation_key = "multicolor_segment_count"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_multicolor_segment_count"
        )
        self._attr_native_value = 0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = int(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        await self._node.set_multicolor_segment_count(int(value))
        self._attr_native_value = int(value)
        self.async_write_ha_state()


class CyncLanDimmerLedBrightness(CyncLanEntity, RestoreNumber, NumberEntity):
    """Brightness of a dimmer's row of level LEDs.

    Assumed state, restored across restarts - the device never reports this
    back. Each change sends two packets (Preview then Save); see
    CyncDevice.set_dimmer_led_brightness for why a single one would commit
    whatever the device happened to be previewing.
    """

    _attr_translation_key = "dimmer_led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice"
    ) -> None:
        super().__init__(
            bridge, entry_id, node, unique_id_suffix="_dimmer_led_brightness"
        )
        self._attr_native_value = 100

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = int(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        await self._node.set_dimmer_led_brightness(int(value))
        self._attr_native_value = int(value)
        self.async_write_ha_state()
