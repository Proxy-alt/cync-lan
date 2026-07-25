"""Select platform for Cync LAN: indicator-LED mode and color.

Both are config entities backed by CyncDevice.set_indicator_led() -
confirmed working on real hardware this session (see docs/mesh_opcodes.md's
"Indicator LED ring" section). There's no way to read the indicator LED's
real state back from the device, so these are assumed-state entities
(HA's documented pattern for "can command it, can't read it back") backed
by the shared per-device cache in bridge.py and restored across HA
restarts via RestoreEntity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import LED_COLOR_TO_INT, LED_MODE_TO_INT, CyncLanBridge
from .entity import CyncLanIndicatorLedEntity

if TYPE_CHECKING:
    from cync_lan.devices import CyncDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime_data = entry.runtime_data
    bridge = runtime_data.bridge
    entities: list[SelectEntity] = []
    for node in runtime_data.ncync_server.node_devices.values():
        if node.metadata is None or not node.metadata.supported:
            continue
        entities.append(CyncLanIndicatorLedModeSelect(bridge, entry.entry_id, node))
        entities.append(CyncLanIndicatorLedColorSelect(bridge, entry.entry_id, node))
    async_add_entities(entities)


class CyncLanIndicatorLedModeSelect(CyncLanIndicatorLedEntity, SelectEntity):
    _attr_translation_key = "indicator_led_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_options = list(LED_MODE_TO_INT)

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_indicator_led_mode")

    @property
    def current_option(self) -> str:
        return self._bridge.get_indicator_led(self._node.id).mode

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_led_field(
            "mode", lambda s: s if s in LED_MODE_TO_INT else None
        )

    async def async_select_option(self, option: str) -> None:
        await self._bridge.set_indicator_led_field(self._node, mode=option)


class CyncLanIndicatorLedColorSelect(CyncLanIndicatorLedEntity, SelectEntity):
    _attr_translation_key = "indicator_led_color"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_options = list(LED_COLOR_TO_INT)

    def __init__(self, bridge: CyncLanBridge, entry_id: str, node: "CyncDevice") -> None:
        super().__init__(bridge, entry_id, node, unique_id_suffix="_indicator_led_color")

    @property
    def current_option(self) -> str:
        return self._bridge.get_indicator_led(self._node.id).color

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_led_field(
            "color", lambda s: s if s in LED_COLOR_TO_INT else None
        )

    async def async_select_option(self, option: str) -> None:
        await self._bridge.set_indicator_led_field(self._node, color=option)
