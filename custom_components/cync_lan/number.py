"""Number platform for Cync LAN: indicator-LED brightness.

See select.py's module docstring for the shared assumed-state/RestoreEntity
rationale - this uses RestoreNumber instead, since RestoreEntity's own
restored-state string doesn't carry NumberEntity's native_value shape.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CyncLanIndicatorLedEntity

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    from cync_lan.structs import GlobalObject

    g = GlobalObject()
    bridge = entry.runtime_data.bridge
    entities: list[NumberEntity] = []
    for node in g.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        entities.append(CyncLanIndicatorLedBrightness(bridge, entry.entry_id, node))
    async_add_entities(entities)


class CyncLanIndicatorLedBrightness(CyncLanIndicatorLedEntity, RestoreNumber, NumberEntity):
    _attr_translation_key = "indicator_led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, bridge, entry_id: str, node) -> None:
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
