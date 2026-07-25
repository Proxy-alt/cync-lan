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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[NumberEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
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

    async_add_entities(entities)


class CyncLanIndicatorLedBrightness(CyncLanIndicatorLedEntity, RestoreNumber, NumberEntity):
    _attr_translation_key = "indicator_led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_indicator_led_brightness")

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

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
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
